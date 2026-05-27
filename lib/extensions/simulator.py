# Authors: see git history
#
# Copyright (c) 2010 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

import time

import wx

from ..gui.simulator import SimulatorWindow
from ..stitch_plan import stitch_groups_to_stitch_plan
from ..svg import convert_length
from ..utils.svg_data import get_pagecolor
from .base import InkstitchExtension

PROGRESSIVE_UPDATE_INTERVAL = 0.5  # seconds between partial simulator updates


class Simulator(InkstitchExtension):
    def __init__(self):
        InkstitchExtension.__init__(self)

    def effect(self):
        if not self.get_elements():
            return

        metadata = self.get_inkstitch_metadata()
        collapse_len = metadata['collapse_len_mm']
        min_stitch_len = metadata['min_stitch_len_mm']
        background_color = get_pagecolor(self.svg.namedview)
        elements = self.elements

        # Extract all SVG-derived data before the event loop starts
        page_specs_base = self._extract_page_specs_base()

        app = wx.App()
        current_screen = wx.Display.GetFromPoint(wx.GetMousePosition())
        display = wx.Display(current_screen)
        screen_rect = display.GetClientArea()
        width = int(screen_rect.width * 0.8)
        height = int(screen_rect.height * 0.8)
        simulator = SimulatorWindow(size=(width, height), background_color=background_color)
        wx.CallLater(100, simulator.Centre)
        app.SetTopWindow(simulator)
        simulator.Show()

        state = {
            'index': 0,
            'accumulated': [],
            'last_stitch_group': None,
            'last_update': 0.0,
        }

        def do_update():
            try:
                stitch_plan = stitch_groups_to_stitch_plan(
                    list(state['accumulated']),
                    collapse_len=collapse_len,
                    min_stitch_len=min_stitch_len,
                )
                page_specs = dict(page_specs_base)
                page_specs['x'] = stitch_plan.bounding_box[0]
                page_specs['y'] = stitch_plan.bounding_box[1]
                simulator.load(stitch_plan)
                simulator.set_page_specs(page_specs)
                simulator.go()
            except RuntimeError:
                pass

        def process_next():
            i = state['index']
            if i >= len(elements):
                if state['accumulated']:
                    do_update()
                return

            element = elements[i]
            next_element = elements[i + 1] if i + 1 < len(elements) else None
            new_groups = element.embroider(state['last_stitch_group'], next_element)
            state['accumulated'].extend(new_groups)
            if new_groups:
                state['last_stitch_group'] = new_groups[-1]
            state['index'] = i + 1

            now = time.monotonic()
            if state['accumulated'] and now - state['last_update'] >= PROGRESSIVE_UPDATE_INTERVAL:
                do_update()
                state['last_update'] = now

            wx.CallLater(1, process_next)

        wx.CallLater(100, process_next)
        app.MainLoop()

    def _extract_page_specs_base(self):
        svg = self.document.getroot()
        width = svg.get('width', 0)
        height = svg.get('height', 0)
        page_color = 'white'
        desk_color = 'white'
        border_color = 'black'
        show_page_shadow = True

        named_view = svg.namedview
        if named_view is not None:
            page_color = named_view.get('pagecolor', page_color)
            desk_color = named_view.get('inkscape:deskcolor', desk_color)
            border_color = named_view.get('bordercolor', border_color)
            show_page_shadow = named_view.get('inkscape:showpageshadow', 'true') in ['true', 'yes', 'y', '1', '2']

        return {
            'width': convert_length(width),
            'height': convert_length(height),
            'page_color': page_color,
            'desk_color': desk_color,
            'border_color': border_color,
            'show_page_shadow': show_page_shadow,
        }
