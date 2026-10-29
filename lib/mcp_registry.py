# Authors: see git history
#
# Copyright (c) 2010 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""A lightweight, best-effort record of which .svg files are currently open
in Inkscape, so the MCP connector can answer "what design am I working on?"
without the user having to paste a file path.

Ink/Stitch itself has no lifecycle hook for "document opened/saved" - the
closest approximation is that InkstitchExtension.load() runs every time any
Ink/Stitch extension is used (Params, Troubleshoot, Simulator, embroider,
...), which in practice is often enough while actively designing.
"""

import json
import os
import time

from .utils.paths import get_user_dir

REGISTRY_FILE_NAME = 'mcp_open_designs.json'

# Entries not touched in this long are treated as no longer open.
MAX_AGE_SECONDS = 60 * 60 * 24


def _registry_path():
    return get_user_dir(REGISTRY_FILE_NAME)


def _read_registry():
    try:
        with open(_registry_path()) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _write_registry(registry):
    with open(_registry_path(), 'w') as f:
        json.dump(registry, f)


def register_open_design(svg_path):
    """Record that svg_path was just loaded by an Ink/Stitch extension.

    Best-effort: never raises, since this must not break normal extension
    use just because the registry file couldn't be written.
    """

    try:
        svg_path = os.path.abspath(svg_path)
        registry = _read_registry()
        registry[svg_path] = dict(
            path=svg_path,
            docname=os.path.basename(svg_path),
            last_seen=time.time(),
        )
        _write_registry(registry)
    except OSError:
        pass


def list_registered_designs(max_age=MAX_AGE_SECONDS):
    """Designs seen recently, most-recent first. Prunes entries whose file
    no longer exists or that are older than max_age."""

    now = time.time()
    registry = _read_registry()

    entries = []
    changed = False
    for path, entry in list(registry.items()):
        if not os.path.isfile(path) or (now - entry['last_seen']) > max_age:
            del registry[path]
            changed = True
            continue
        entries.append(entry)

    if changed:
        _write_registry(registry)

    entries.sort(key=lambda entry: entry['last_seen'], reverse=True)
    return entries
