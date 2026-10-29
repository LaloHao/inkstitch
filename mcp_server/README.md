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
- `set_element_param(svg_path, element_id, param, value)` - **Linux only**.
  Sets an `inkstitch:*` attribute live on the document currently open in
  Inkscape (visible on canvas immediately) via Inkscape's own D-Bus/GAction
  interface (`select-by-id` + `object-set-attribute`) - no custom Inkscape
  extension required. Does **not** save to disk; press Ctrl+S in Inkscape
  to persist. Only works if `svg_path` is among `list_open_designs`.

## How loading works

Designs are loaded the same way any Ink/Stitch extension would (`inkex` +
`InkstitchExtension`), but without ever opening a wx dialog or writing
anything back to disk - see `lib/design_query.py`.

## Limitations

- The "open designs" registry only updates when some Ink/Stitch extension
  runs against a file - not on every keystroke, and not for plain Inkscape
  use with no Ink/Stitch extension involved.
- Live editing (`set_element_param`) needs `wmctrl` and `gdbus` on PATH and
  a reachable X11/D-Bus session. Some MCP clients spawn this server with a
  stripped-down environment (no `DISPLAY`/`DBUS_SESSION_BUS_ADDRESS`);
  `lib/dbus_bridge.py` falls back to `DISPLAY=:0` and the standard
  systemd-user bus path if they're missing, which covers the common single
  X-display desktop case but can be wrong on unusual setups - set the env
  vars explicitly for the MCP server process if so.
- Live editing selects by targeting whichever Inkscape window title matches
  the file's basename; if two open designs share a filename, it may focus
  the wrong one.
