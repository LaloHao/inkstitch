# Authors: see git history
#
# Copyright (c) 2010 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

"""Live editing of a design that is currently open in Inkscape, via the
D-Bus/GAction interface Inkscape 1.x already exposes (org.inkscape.Inkscape)
- no custom Inkscape extension required.

Confirmed by hand against Inkscape 1.2.2: selecting a node by id and setting
an XML attribute on it via these actions modifies the live, in-memory
document (the same one shown on screen), exactly like using the XML editor
panel manually would.

Linux/X11 only (uses wmctrl and gdbus).  Deliberately does not save the
document - the change is applied live so it's visible immediately, but
writing to disk is left as an explicit action for the user (Ctrl+S in
Inkscape), since silently overwriting a file the user has open is not this
tool's call to make.
"""

import os
import shutil
import subprocess
import time

INKSCAPE_BUS_NAME = 'org.inkscape.Inkscape'
INKSCAPE_APP_PATH = '/org/inkscape/Inkscape'


class DBusBridgeError(Exception):
    """Could not reach a live Inkscape instance, or the requested design
    isn't open in it right now."""


def _require_tool(name):
    if shutil.which(name) is None:
        raise DBusBridgeError(
            f"'{name}' is required for live editing and wasn't found on PATH. "
            "Live editing only works on Linux with wmctrl and gdbus installed."
        )


def _session_env():
    # MCP clients commonly spawn this server with a sanitized environment
    # (e.g. the reference Python SDK only forwards HOME/LOGNAME/PATH/SHELL/
    # TERM/USER), which drops DISPLAY and DBUS_SESSION_BUS_ADDRESS - without
    # them, wmctrl/gdbus can't find the user's X11/D-Bus session at all.
    # Fall back to the standard single-display, systemd-user-bus defaults;
    # an explicitly-set env var always wins.
    env = dict(os.environ)
    env.setdefault('DISPLAY', ':0')
    env.setdefault('DBUS_SESSION_BUS_ADDRESS', f'unix:path=/run/user/{os.getuid()}/bus')
    return env


def _quote_variant_string(value):
    escaped = str(value).replace('\\', '\\\\').replace("'", "\\'")
    return f"'{escaped}'"


def _activate_action(action_name, param=None):
    _require_tool('gdbus')

    args = [
        'gdbus', 'call', '--session',
        '--dest', INKSCAPE_BUS_NAME,
        '--object-path', INKSCAPE_APP_PATH,
        '--method', 'org.gtk.Actions.Activate',
        action_name,
        f"[<{_quote_variant_string(param)}>]" if param is not None else '@av []',
        '{}',
    ]

    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=10, env=_session_env())
    except FileNotFoundError as exc:
        raise DBusBridgeError("gdbus is not available") from exc

    if result.returncode != 0:
        raise DBusBridgeError(
            f"Could not reach a running Inkscape instance via D-Bus while running "
            f"'{action_name}': {result.stderr.strip()}"
        )


def focus_window(svg_path: str):
    """Raise the Inkscape window showing svg_path, so that the app-level
    actions below (which operate on 'the active window') apply to the right
    document when more than one is open."""

    _require_tool('wmctrl')
    docname = svg_path.rsplit('/', 1)[-1]

    try:
        result = subprocess.run(['wmctrl', '-a', docname], capture_output=True, text=True, timeout=10, env=_session_env())
    except FileNotFoundError as exc:
        raise DBusBridgeError("wmctrl is not available") from exc

    if result.returncode != 0:
        raise DBusBridgeError(
            f"No Inkscape window found with '{docname}' in its title. "
            "Make sure this design is currently open in Inkscape."
        )


def select_element(element_id: str):
    # select-by-id *adds* to the current selection rather than replacing it,
    # so without clearing first, object-set-attribute would also land on every
    # element selected earlier (by a previous call or by the user).
    _activate_action('select-clear')
    time.sleep(_ACTION_SETTLE_SECONDS)
    _activate_action('select-by-id', element_id)


def set_attribute(name: str, value: str):
    _activate_action('object-set-attribute', f"{name},{value}")


# gdbus call only confirms the D-Bus message was delivered, not that Inkscape
# has finished handling it on the GTK main loop - org.gtk.Actions.Activate is
# fire-and-forget. Without a pause here, object-set-attribute can be
# dispatched before select-by-id has actually updated the selection, so the
# attribute silently lands on whatever was selected before (or nothing).
_ACTION_SETTLE_SECONDS = 0.2


def set_element_param(svg_path: str, element_id: str, param: str, value) -> None:
    """Sets inkstitch:<param>="<value>" on element_id, live, in whichever
    Inkscape window currently has svg_path open. Does not save to disk."""

    focus_window(svg_path)
    time.sleep(_ACTION_SETTLE_SECONDS)
    select_element(element_id)
    time.sleep(_ACTION_SETTLE_SECONDS)
    set_attribute(f"inkstitch:{param}", value)
