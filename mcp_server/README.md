# Ink/Stitch MCP connector

Lets an MCP client (Claude Code, Claude Desktop) read Ink/Stitch design
parameters, run Troubleshoot-style diagnostics, get stitch plan stats, and
(Linux only) tweak a parameter live on a design open in Inkscape.

## Install

Same venv you already use to run Ink/Stitch (needs `inkex` + `wxPython`),
plus the `mcp` package:

```
pip install -r requirements.txt -r requirements-mcp.txt
```

## Try it without an MCP client

```
python -m mcp_server.cli summarize path/to/design.svg
python -m mcp_server.cli list-elements path/to/design.svg
python -m mcp_server.cli diagnose path/to/design.svg
python -m mcp_server.cli stitch-plan-stats path/to/design.svg
python -m mcp_server.cli open-designs
python -m mcp_server.cli measure-elements path/to/design.svg
python -m mcp_server.cli suggest-trims path/to/design.svg
python -m mcp_server.cli set-params path/to/open.svg '[{"ids": ["path1"], "params": {"zigzag_spacing_mm": "0.4"}}]'
```

## Register with Claude Code

From the repo root:

```
claude mcp add inkstitch -- /path/to/venv/bin/python -m mcp_server.server
```

## Tools

- `list_open_designs` - designs Ink/Stitch has recently seen open in
  Inkscape (populated automatically whenever you run any Ink/Stitch
  extension - Params, Troubleshoot, Simulator, etc. - against a file).
- `summarize_design(svg_path)` - element counts, colors, dimensions.
- `list_elements(svg_path, element_id=None, only_set=True, param_names=None)` -
  stitch parameters per element; by default only the ones explicitly set.
- `diagnose_design(svg_path)` - same checks as Troubleshoot, as structured data.
- `stitch_plan_stats(svg_path)` - runs the stitch engine: stitch count,
  colors, trims, stops, jumps, dimensions, estimated thread. Can be slow.
- `measure_elements(svg_path, element_ids=None, live=False)` - geometry in
  mm, in embroidery order: satin width (min/median/max), length, rungs
  (drawn vs. implicit), stitch skew (how far stitches lean from straight
  across), fill area, running stitch length, bounding boxes.
  From the SVG, so pull compensation isn't included.
- `suggest_trims(svg_path, min_gap_mm=1.0, live=False)` - runs the stitch
  engine and lists same-color neighbours with no trim between them: gaps
  short enough to be sewn as one visible "connector" stitch, and jumps that
  aren't trimmed. Returns `set_params_changes` to add the trims with
  `set_params`.

  With `live=True` both read the document open in Inkscape (unsaved edits
  included) instead of the saved file - e.g. to re-check right after
  `set_params`. **Linux only**, same requirements as `set_params`.
- `set_params(svg_path, changes)` - **Linux only**. Sets `inkstitch:*`
  params on many elements at once, live on the document open in Inkscape
  (visible on canvas immediately), via Inkscape's own D-Bus/GAction
  interface - no custom Inkscape extension required. `changes` is a list of
  `{"ids": [...], "params": {"name": "value"}}`; an empty value resets a
  param to its default. Each call:
  1. exports the live document to a temp file (unsaved edits included; the
     user's file is never touched) and rejects the whole batch, writing
     nothing, if an id doesn't exist or a param doesn't belong to that
     element's type;
  2. applies the batch (`select-clear` + `select-by-id` + `object-set-attribute`);
  3. exports again and reports `verified`, any `mismatches` (value didn't
     land) and `unexpected_changes` (an inkstitch attribute changed on
     something that wasn't asked for), plus `previous_values` to undo.

  Does **not** save to disk; press Ctrl+S in Inkscape to persist. Only works
  if `svg_path` is among `list_open_designs`.
- `set_element_param(svg_path, element_id, param, value)` - one-param
  shortcut for `set_params`, same checks.

## How loading works

Designs are loaded the same way any Ink/Stitch extension would (`inkex` +
`InkstitchExtension`), but without ever opening a wx dialog or writing
anything back to disk - see `lib/design_query.py`.

## Limitations

- The "open designs" registry only updates when some Ink/Stitch extension
  runs against a file - not on every keystroke, and not for plain Inkscape
  use with no Ink/Stitch extension involved.
- Live editing (`set_params`) needs `wmctrl` and `gdbus` on PATH and
  a reachable X11/D-Bus session. Some MCP clients spawn this server with a
  stripped-down environment (no `DISPLAY`/`DBUS_SESSION_BUS_ADDRESS`);
  `lib/dbus_bridge.py` falls back to `DISPLAY=:0` and the standard
  systemd-user bus path if they're missing, which covers the common single
  X-display desktop case but can be wrong on unusual setups - set the env
  vars explicitly for the MCP server process if so.
- Live editing selects by targeting whichever Inkscape window title matches
  the file's basename; if two open designs share a filename, it may focus
  the wrong one.
