# Authors: see git history
#
# Copyright (c) 2010 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Headless (no wx GUI, no writing to disk) inspection of Ink/Stitch designs.

This is the core used by the MCP connector (see mcp_server/) to read stitch
parameters and run diagnostics against a saved .svg file, without going
through Inkscape's extension menu.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Optional

from inkex.base import SvgInputMixin

from .elements.validation import ObjectTypeWarning, ValidationWarning
from .extensions.base import InkstitchExtension
from .stitch_plan import stitch_groups_to_stitch_plan
from .svg import PIXELS_PER_MM
from .svg.tags import INKSTITCH_ATTRIBS
from .update import update_inkstitch_document


class DesignLoadError(Exception):
    """The given file could not be opened or parsed as an Ink/Stitch design."""


class ElementNotFoundError(Exception):
    """The given element id does not exist in the design."""


class _HeadlessLoader(InkstitchExtension):
    """Loads an SVG exactly like any Ink/Stitch extension would, but never
    opens a wx dialog and never writes anything back to disk."""

    def effect(self):
        # required by the abstract base class; intentionally never called
        pass

    def load(self, stream):
        # Bypass InkstitchExtension.load(), which calls
        # update_inkstitch_document() with warn_unversioned=True by default.
        # For a legacy unversioned document that would open a wx.Frame
        # (RequestUpdate) asking the user to confirm a migration - which
        # would hang a headless process. Migrate silently instead.
        document = SvgInputMixin.load(self, stream)
        update_inkstitch_document(document, warn_unversioned=False)
        return document


@dataclass
class LoadedDesign:
    path: str
    extension: _HeadlessLoader
    elements: list = field(default_factory=list)

    @property
    def svg(self):
        return self.extension.svg

    @property
    def metadata(self):
        return self.extension.get_inkstitch_metadata()


def load_design(svg_path: str) -> LoadedDesign:
    # inkex's own argparse "type=filename_arg" validator calls sys.exit(2) via
    # argparse.error() on a missing file, which would kill a long-running
    # server process if left uncaught - so check up front instead.
    if not os.path.isfile(svg_path):
        raise DesignLoadError(f"No such file: '{svg_path}'")

    ext = _HeadlessLoader()

    try:
        ext.parse_arguments([svg_path])
        ext.load_raw()
    except SystemExit as exc:
        raise DesignLoadError(f"Could not open '{svg_path}'") from exc
    except OSError as exc:
        raise DesignLoadError(f"Could not open '{svg_path}': {exc}") from exc
    except Exception as exc:
        raise DesignLoadError(f"'{svg_path}' is not a valid SVG document: {exc}") from exc
    finally:
        # load_raw() opens the file itself but never closes it.
        file_io = getattr(ext, 'file_io', None)
        if file_io is not None:
            file_io.close()

    # troubleshoot=True: process every node in the document, selection or not,
    # and never raise/print an error just because there are zero elements.
    ext.get_elements(troubleshoot=True)

    return LoadedDesign(path=svg_path, extension=ext, elements=list(ext.elements))


def _find_element(design: LoadedDesign, element_id: str):
    for element in design.elements:
        if element.id == element_id:
            return element

    valid_ids = [element.id for element in design.elements]
    raise ElementNotFoundError(
        f"No element with id '{element_id}' in this design. Valid ids: {valid_ids}"
    )


def summarize_design(design: LoadedDesign) -> dict:
    """Cheap overview of a design: does not run embroider(), so dimensions
    come from the page size and colors from element styling rather than
    the actual stitch plan (see stitch_plan_stats for the stitched numbers,
    which can differ slightly due to pull/push compensation)."""

    elements_by_type: dict[str, int] = {}
    colors = set()
    for element in design.elements:
        type_name = type(element).__name__
        elements_by_type[type_name] = elements_by_type.get(type_name, 0) + 1

        color = getattr(element, 'color', None)
        if color is not None:
            colors.add(str(color))

    # viewport_* is the page size in px; viewbox_* is in user units, which
    # are only px when the document's scale is 1 (often mm instead).
    svg = design.svg
    width_mm = svg.viewport_width / PIXELS_PER_MM
    height_mm = svg.viewport_height / PIXELS_PER_MM

    return dict(
        path=design.path,
        width_mm=round(width_mm, 2),
        height_mm=round(height_mm, 2),
        element_count=len(design.elements),
        elements_by_type=elements_by_type,
        color_count=len(colors),
    )


def list_element_params(design: LoadedDesign, element_id: Optional[str] = None,
                        only_set: bool = True, param_names: Optional[list[str]] = None) -> list[dict]:
    """only_set: skip params left at their default (not written on the node),
    which is most of them - a full listing can run to hundreds of KB.
    param_names: only report these params (set or not)."""

    if element_id is not None:
        elements = [_find_element(design, element_id)]
    else:
        elements = design.elements

    result = []
    for element in elements:
        param_by_name = {param.name: param for param in element.get_params()}
        values = element.get_params_and_values()

        params = {}
        for name, value in values.items():
            if param_names is not None:
                if name not in param_names:
                    continue
            elif only_set and not element.node.get(INKSTITCH_ATTRIBS.get(name, ''), '').strip():
                continue

            param = param_by_name.get(name)
            params[name] = dict(
                value=value,
                unit=getattr(param, 'unit', None),
                description=getattr(param, 'description', None),
            )

        result.append(dict(
            id=element.id,
            label=element.node.label,
            type=type(element).__name__,
            params=params,
        ))

    return result


def _serialize_problem(problem, severity: str) -> dict:
    return dict(
        element_label=problem.label,
        severity=severity,
        name=problem.name,
        description=problem.description,
        steps_to_solve=list(problem.steps_to_solve),
        # element geometry is in px; report mm like every other tool
        position_mm=dict(
            x=round(problem.position.x / PIXELS_PER_MM, 2),
            y=round(problem.position.y / PIXELS_PER_MM, 2),
        ),
    )


def diagnose_design(design: LoadedDesign, include_warnings: bool = True) -> dict:
    errors = []
    warnings = []
    type_warnings = []

    for element in design.elements:
        for problem in element.validation_errors():
            errors.append(_serialize_problem(problem, 'error'))

        if not include_warnings:
            continue

        for problem in element.validation_warnings():
            if isinstance(problem, ObjectTypeWarning):
                type_warnings.append(_serialize_problem(problem, 'type_warning'))
            elif isinstance(problem, ValidationWarning):
                warnings.append(_serialize_problem(problem, 'warning'))

    return dict(
        errors=errors,
        warnings=warnings,
        type_warnings=type_warnings,
        is_valid=not errors,
    )


def stitch_plan_stats(design: LoadedDesign) -> dict:
    """Runs the same embroider() + stitch_groups_to_stitch_plan() pipeline as
    Extensions > Ink/Stitch > Element Info. Can be slow on large designs."""

    metadata = design.metadata
    stitch_groups = design.extension.elements_to_stitch_groups(design.elements)
    stitch_plan = stitch_groups_to_stitch_plan(
        stitch_groups,
        collapse_len=metadata['collapse_len_mm'],
        min_stitch_len=metadata['min_stitch_len_mm'],
    )

    width_mm, height_mm = stitch_plan.dimensions_mm

    return dict(
        num_colors=stitch_plan.num_colors,
        num_color_blocks=stitch_plan.num_color_blocks,
        num_stops=stitch_plan.num_stops,
        num_trims=stitch_plan.num_trims,
        num_stitches=stitch_plan.num_stitches,
        num_jumps=stitch_plan.num_jumps,
        dimensions_mm=[round(width_mm, 2), round(height_mm, 2)],
        estimated_thread_m=round(stitch_plan.estimated_thread, 2),
        color_sequence=[
            dict(hex=block.color.hex_digits, name=block.color.name)
            for block in stitch_plan
        ],
    )


def _mm(value_px):
    return round(value_px / PIXELS_PER_MM, 2)


def _bbox_mm(geometry):
    if geometry is None or geometry.is_empty:
        return None
    min_x, min_y, max_x, max_y = geometry.bounds
    return dict(x=_mm(min_x), y=_mm(min_y), width=_mm(max_x - min_x), height=_mm(max_y - min_y))


def _satin_measurements(element, samples=20):
    rails = element.line_string_rails
    if len(rails) != 2:
        return dict(error="satin column needs exactly two rails")
    rail_a, rail_b = rails

    # distance from evenly spaced points on one rail to the other rail -
    # the column's width as stitched, before pull compensation
    widths = sorted(
        rail_a.interpolate(i / samples, normalized=True).distance(rail_b)
        for i in range(samples + 1)
    )

    return dict(
        width_mm=dict(min=_mm(widths[0]), median=_mm(widths[len(widths) // 2]), max=_mm(widths[-1])),
        length_mm=_mm((rail_a.length + rail_b.length) / 2),
        # rungs drawn by the user; with none, Ink/Stitch pairs up the rail
        # nodes itself (implicit_rungs), which only works when both rails
        # have the same number of points
        rungs=max(len(element.filtered_subpaths) - 2, 0),
        implicit_rungs=len(element.rungs) if len(element.filtered_subpaths) == 2 else 0,
        bbox_mm=_bbox_mm(rail_a.union(rail_b)),
    )


def measure_elements(design: LoadedDesign, element_ids: Optional[list[str]] = None) -> list[dict]:
    """Real-world geometry per element, in embroidery order: satin width
    (min/median/max) and length, fill area, running stitch length, bounding
    boxes - all in mm. Measured from the SVG, not the stitch plan, so
    compensation (pull/push, expand) isn't included."""

    if element_ids is not None:
        elements = [_find_element(design, element_id) for element_id in element_ids]
    else:
        elements = design.elements

    result = []
    for element in elements:
        type_name = type(element).__name__
        entry = dict(
            id=element.id,
            label=element.node.label,
            type=type_name,
            color=str(element.color) if getattr(element, 'color', None) is not None else None,
            order=design.elements.index(element),
        )

        try:
            if type_name == 'SatinColumn':
                entry.update(_satin_measurements(element))
            elif type_name == 'FillStitch':
                shape = element.shape
                entry.update(area_mm2=round(shape.area / PIXELS_PER_MM ** 2, 2), bbox_mm=_bbox_mm(shape))
            elif type_name == 'Stroke':
                lines = element.as_multi_line_string()
                entry.update(length_mm=_mm(lines.length), bbox_mm=_bbox_mm(lines))
        except Exception as exc:  # broken geometry shouldn't hide the rest of the design
            entry['error'] = f"{type(exc).__name__}: {exc}"

        result.append(entry)

    return result


def suggest_trims(design: LoadedDesign, min_gap_mm: float = 1.0) -> dict:
    """Walks the elements in embroidery order, stitching each one the way the
    stitch plan does, and finds same-color neighbours with no trim between
    them. Two cases are reported, both of which leave thread on top of the
    fabric:

    - "connector": the gap is shorter than the jump threshold (collapse_len,
      or the element's own min_jump_stitch_length_mm), so the machine sews a
      single straight stitch across it - visible between letters, say.
      Gaps under min_gap_mm are left alone: those are pieces of one shape
      that touch or overlap.
    - "jump": a jump stitch with no trim, which stays as a loose thread
      unless the machine auto-trims jumps or it is cut by hand.

    Color changes are not reported (machines trim there anyway).
    """

    metadata = design.metadata
    collapse_len = float(metadata['collapse_len_mm']) * PIXELS_PER_MM
    min_gap = min_gap_mm * PIXELS_PER_MM

    elements = design.elements
    previous = None  # (element, its last stitch group)
    last_stitch_group = None
    suggestions = []
    already_trimmed = 0

    for index, element in enumerate(elements):
        next_element = elements[index + 1] if index + 1 < len(elements) else None
        # same previous-group/next-element chaining as elements_to_stitch_groups()
        all_groups = element.embroider(last_stitch_group, next_element)
        if all_groups:
            last_stitch_group = all_groups[-1]
        stitch_groups = [group for group in all_groups if group.stitches]
        if not stitch_groups:
            continue

        if previous is not None:
            previous_element, previous_group = previous
            first_group = stitch_groups[0]
            if previous_group.color == first_group.color:
                if previous_group.trim_after or previous_group.stop_after:
                    already_trimmed += 1
                else:
                    gap = (first_group.stitches[0] - previous_group.stitches[-1]).length()
                    jump_threshold = previous_group.min_jump_stitch_length or collapse_len
                    kind = 'jump' if gap > jump_threshold else 'connector'
                    if kind == 'jump' or gap >= min_gap:
                        suggestions.append(dict(
                            after_id=previous_element.id,
                            after_label=previous_element.node.label,
                            next_id=element.id,
                            next_label=element.node.label,
                            gap_mm=_mm(gap),
                            kind=kind,
                        ))

        previous = (element, stitch_groups[-1])

    return dict(
        collapse_len_mm=float(metadata['collapse_len_mm']),
        min_gap_mm=min_gap_mm,
        already_trimmed=already_trimmed,
        suggestions=suggestions,
        # ready to pass to the set_params tool
        set_params_changes=[dict(ids=[s['after_id'] for s in suggestions], params=dict(trim_after='True'))]
        if suggestions else [],
    )
