# Authors: see git history
#
# Copyright (c) 2010 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Verified bulk editing of Ink/Stitch params on a design open in Inkscape.

Live edits go through Inkscape's fire-and-forget D-Bus actions (see
dbus_bridge), so a write can silently land on the wrong element or not at
all. Every batch is therefore bracketed by two exports of the live document:
the first validates the request (ids exist, params belong to each element's
type) before anything is written, the second checks that every requested
value landed and that no other inkstitch attribute changed anywhere.
"""

import os
import tempfile

import inkex
from lxml import etree

from . import dbus_bridge, design_query

INKSTITCH_NS = inkex.NSS['inkstitch']


class ParamChangeError(Exception):
    """The requested changes are invalid; nothing was written."""


def _normalize_changes(changes):
    normalized = []
    for index, change in enumerate(changes):
        ids = change.get('ids')
        params = change.get('params')
        if not ids or not isinstance(ids, (list, tuple)):
            raise ParamChangeError(f"Change #{index}: 'ids' must be a non-empty list of element ids.")
        if not params or not isinstance(params, dict):
            raise ParamChangeError(f"Change #{index}: 'params' must be a non-empty {{name: value}} mapping.")
        normalized.append((list(ids), {name: '' if value is None else str(value).strip() for name, value in params.items()}))
    return normalized


def _expected_values(changes):
    # later changes win, like the writes themselves
    expected = {}
    for ids, params in changes:
        for element_id in ids:
            for name, value in params.items():
                expected[(element_id, name)] = value
    return expected


def _validate(snapshot_path, changes):
    design = design_query.load_design(snapshot_path)
    elements = {element.id: element for element in design.elements}

    problems = []
    for ids, params in changes:
        for element_id in ids:
            element = elements.get(element_id)
            if element is None:
                problems.append(f"'{element_id}' is not an embroidery element in the open document.")
                continue
            valid_names = {param.name for param in element.get_params()}
            for name in params:
                if name not in valid_names:
                    problems.append(f"'{name}' is not a parameter of {type(element).__name__} '{element_id}'.")

    if problems:
        raise ParamChangeError("Nothing was changed:\n" + "\n".join(problems))


def _inkstitch_attributes(snapshot_path):
    tree = etree.parse(snapshot_path)
    prefix = '{%s}' % INKSTITCH_NS
    attributes = {}
    for node in tree.iter():
        node_id = node.get('id')
        if node_id is None:
            continue
        for key, value in node.attrib.items():
            if key.startswith(prefix):
                attributes[(node_id, key[len(prefix):])] = value.strip()
    return attributes


def apply_param_changes(svg_path: str, changes: list) -> dict:
    """changes: [{'ids': [element ids], 'params': {param_name: value}}, ...]
    An empty value resets the param to its default.

    Applies every change live in the Inkscape window showing svg_path and
    verifies the result. Does not save. Raises ParamChangeError (before
    writing anything) if an id or param name is invalid."""

    changes = _normalize_changes(changes)
    expected = _expected_values(changes)

    with tempfile.TemporaryDirectory(prefix='inkstitch-live-') as tmp:
        before_path = os.path.join(tmp, 'before.svg')
        after_path = os.path.join(tmp, 'after.svg')

        dbus_bridge.export_live_svg(svg_path, before_path)
        _validate(before_path, changes)
        before = _inkstitch_attributes(before_path)

        dbus_bridge.focus_window(svg_path)
        dbus_bridge.settle()
        for ids, params in changes:
            dbus_bridge.select_elements(ids)
            dbus_bridge.settle()
            for name, value in params.items():
                dbus_bridge.set_attribute(f"inkstitch:{name}", value)
                dbus_bridge.settle()
        dbus_bridge.clear_selection()

        dbus_bridge.export_live_svg(svg_path, after_path)
        after = _inkstitch_attributes(after_path)

    # An empty value is how a param is reset to its default (get_param()
    # treats "" like a missing attribute), so compare them as equal.
    def value(attributes, key):
        return attributes.get(key) or ''

    mismatches = [
        dict(id=element_id, param=name, expected=expected_value, actual=after.get((element_id, name)))
        for (element_id, name), expected_value in expected.items()
        if value(after, (element_id, name)) != expected_value
    ]
    unexpected_changes = [
        dict(id=element_id, param=name, before=before.get((element_id, name)), after=after.get((element_id, name)))
        for (element_id, name) in sorted(set(before) | set(after))
        if (element_id, name) not in expected and value(before, (element_id, name)) != value(after, (element_id, name))
    ]
    previous_values = [
        dict(id=element_id, param=name, value=before.get((element_id, name)))
        for (element_id, name) in expected
    ]

    return dict(
        verified=not mismatches and not unexpected_changes,
        params_written=len(expected),
        mismatches=mismatches,
        unexpected_changes=unexpected_changes,
        # None means the param wasn't set (the element used the default);
        # pass these back, with None as "", to undo the batch.
        previous_values=previous_values,
        saved=False,
    )
