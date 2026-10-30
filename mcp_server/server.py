#!/usr/bin/env python3
# Authors: see git history
#
# Copyright (c) 2010 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""MCP server exposing Ink/Stitch design inspection, diagnosis, and (Linux
only) live parameter editing as tools for an MCP client such as Claude Code.

Run with: python -m mcp_server.server
Or register with Claude Code: claude mcp add inkstitch -- <venv>/bin/python -m mcp_server.server
"""

import os
import sys
from typing import Optional

# Make sure `import lib...` resolves regardless of the MCP client's cwd.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from lib import dbus_bridge, design_query, live_edit, mcp_registry  # noqa: E402

mcp = FastMCP(
    name="inkstitch",
    instructions=(
        "Tools for inspecting and diagnosing Ink/Stitch embroidery designs (.svg files). "
        "If the user doesn't give a file path, call list_open_designs first. If more than "
        "one design was recently open, ask the user which one instead of guessing."
    ),
)


@mcp.tool()
def list_open_designs() -> list[dict]:
    """List .svg designs recently opened in Inkscape via any Ink/Stitch extension
    (Params, Troubleshoot, Simulator, etc.), most recent first. Use this when the
    user refers to "my design" or "the file I have open" without giving a path."""

    return mcp_registry.list_registered_designs()


@mcp.tool()
def summarize_design(svg_path: str) -> dict:
    """Cheap overview of a design: element counts by type, color count, and
    page dimensions in mm. Does not run the stitch engine - for
    the actual stitched dimensions and stitch count, use stitch_plan_stats."""

    design = design_query.load_design(svg_path)
    return design_query.summarize_design(design)


@mcp.tool()
def list_elements(svg_path: str, element_id: Optional[str] = None, only_set: bool = True,
                  param_names: Optional[list[str]] = None) -> list[dict]:
    """List embroidery elements in a design and their Ink/Stitch parameters
    (density, underlay, pull compensation, stitch length, etc.), with each
    parameter's current value, unit, and description. Pass element_id to
    limit to a single element. By default only params explicitly set on an
    element are listed (only_set=False lists defaults too, which is large);
    param_names limits the output to those params, set or not."""

    design = design_query.load_design(svg_path)
    return design_query.list_element_params(design, element_id, only_set, param_names)


@mcp.tool()
def diagnose_design(svg_path: str, include_warnings: bool = True) -> dict:
    """Run Ink/Stitch's validation checks (same as Extensions > Ink/Stitch >
    Troubleshoot) and return errors/warnings as structured data: which
    element, what's wrong, and suggested steps to fix it."""

    design = design_query.load_design(svg_path)
    return design_query.diagnose_design(design, include_warnings)


@mcp.tool()
def stitch_plan_stats(svg_path: str) -> dict:
    """Run the actual stitch engine and return stitch plan statistics:
    stitch count, color sequence, trims, stops, jumps, stitched dimensions,
    and estimated thread usage. Can be slow on large or complex designs."""

    design = design_query.load_design(svg_path)
    return design_query.stitch_plan_stats(design)


@mcp.tool()
def measure_elements(svg_path: str, element_ids: Optional[list[str]] = None, live: bool = False) -> list[dict]:
    """Geometry of each element in mm, in embroidery order: satin column width
    (min/median/max), length, rungs and how much the stitches lean
    (stitch_skew_deg; 0 = straight across); fill area; running
    stitch length; bounding boxes. Useful to spot satins too narrow for the
    fabric or fills that are larger than they look. Measured from the SVG, so
    pull compensation isn't included. live=True reads the document open in
    Inkscape (unsaved edits included) instead of the saved file."""

    return _with_design(svg_path, live, lambda design: design_query.measure_elements(design, element_ids))


@mcp.tool()
def suggest_trims(svg_path: str, min_gap_mm: float = 1.0, live: bool = False) -> dict:
    """Find places where the machine would leave thread on top between two
    same-color elements because there's no trim: short gaps sewn as one
    straight "connector" stitch (e.g. between letters), and jumps with no
    trim. Gaps under min_gap_mm (touching pieces of one shape) are ignored.
    Returns "set_params_changes", ready to pass to set_params to add
    trim_after where suggested. live=True reads the document open in
    Inkscape instead of the saved file. Runs the stitch engine; can be slow."""

    return _with_design(svg_path, live, lambda design: design_query.suggest_trims(design, min_gap_mm))


def _with_design(svg_path, live, query):
    if not live:
        return query(design_query.load_design(svg_path))

    _require_open_design(svg_path)
    with live_edit.live_snapshot(svg_path) as snapshot_path:
        return query(design_query.load_design(snapshot_path))


def _require_open_design(svg_path):
    open_paths = {entry['path'] for entry in mcp_registry.list_registered_designs()}
    if os.path.abspath(svg_path) not in open_paths:
        raise dbus_bridge.DBusBridgeError(
            f"'{svg_path}' isn't among the designs Ink/Stitch has recently seen open in Inkscape. "
            "Open it in Inkscape first (or run any Ink/Stitch extension on it once), then retry."
        )


@mcp.tool()
def set_params(svg_path: str, changes: list[dict]) -> dict:
    """Set Ink/Stitch parameters on many elements at once, live in the design
    open in Inkscape, and verify the result. Linux only; svg_path must be in
    list_open_designs. Does NOT save - the user presses Ctrl+S in Inkscape.

    changes: [{"ids": ["path1", "path2"], "params": {"zigzag_spacing_mm": "0.42",
    "pull_compensation_mm": "0.2"}}, ...]. Group elements that get the same
    values into one change. An empty value resets a param to its default.

    Every id and param name is checked against the open document before
    anything is written; an invalid one fails the whole call with nothing
    changed. Afterwards the live document is read back: "verified" is true
    only if every value landed and no other inkstitch attribute changed.
    "previous_values" lets you undo the batch with another set_params call."""

    _require_open_design(svg_path)
    return live_edit.apply_param_changes(svg_path, changes)


@mcp.tool()
def set_element_param(svg_path: str, element_id: str, param: str, value: str) -> dict:
    """Set one Ink/Stitch parameter (e.g. row_spacing_mm) on one element, live
    in the design open in Inkscape, and verify it. Same rules as set_params,
    which is preferred for more than one change."""

    _require_open_design(svg_path)
    return live_edit.apply_param_changes(svg_path, [dict(ids=[element_id], params={param: value})])


if __name__ == "__main__":
    mcp.run(transport="stdio")
