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

from lib import dbus_bridge, design_query, mcp_registry  # noqa: E402

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
def set_element_param(svg_path: str, element_id: str, param: str, value: str) -> str:
    """Set an Ink/Stitch parameter (e.g. row_spacing_mm, pull_compensation_mm)
    on an element, applied live to the design currently open in Inkscape - the
    change is visible on canvas immediately. Linux only, and svg_path must
    already be open in Inkscape (check list_open_designs first). This does NOT
    save the file - the user still needs to save from Inkscape (Ctrl+S) to
    persist the change to disk."""

    open_paths = {entry['path'] for entry in mcp_registry.list_registered_designs()}
    if os.path.abspath(svg_path) not in open_paths:
        raise dbus_bridge.DBusBridgeError(
            f"'{svg_path}' isn't among the designs Ink/Stitch has recently seen open in Inkscape. "
            "Open it in Inkscape first (or run any Ink/Stitch extension on it once), then retry."
        )

    dbus_bridge.set_element_param(svg_path, element_id, param, value)
    return (
        f"Set inkstitch:{param}={value!r} on '{element_id}' in the live document. "
        "Not saved yet - press Ctrl+S in Inkscape to persist it."
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")
