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
    come from the SVG viewbox and colors from element styling rather than
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

    svg = design.svg
    width_mm = svg.viewbox_width / PIXELS_PER_MM
    height_mm = svg.viewbox_height / PIXELS_PER_MM

    return dict(
        path=design.path,
        width_mm=round(width_mm, 2),
        height_mm=round(height_mm, 2),
        element_count=len(design.elements),
        elements_by_type=elements_by_type,
        color_count=len(colors),
    )


def list_element_params(design: LoadedDesign, element_id: Optional[str] = None) -> list[dict]:
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
        position=dict(x=problem.position.x, y=problem.position.y),
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
