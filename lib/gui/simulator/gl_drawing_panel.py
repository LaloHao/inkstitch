# Authors: see git history
#
# Copyright (c) 2024 Authors
# Licensed under the GNU GPL version 3.0 or later.  See the file LICENSE for details.

try:
    import wx.glcanvas
    from OpenGL import GL
    OPENGL_AVAILABLE = True
except ImportError:
    OPENGL_AVAILABLE = False

if OPENGL_AVAILABLE:
    import math
    import os

    import numpy as np
    import wx

    from typing import List, Optional, Tuple

    from ...stitch_plan import StitchPlan
    from ...svg import PIXELS_PER_MM
    from ...threads.color import ThreadColor
    from ...utils.settings import global_settings


    def _load_shader_source(filename: str) -> str:
        shader_dir = os.path.join(os.path.dirname(__file__), 'shaders')
        with open(os.path.join(shader_dir, filename), 'r') as f:
            return f.read()


    def _compile_shader(source: str, shader_type: int) -> int:
        shader = GL.glCreateShader(shader_type)
        GL.glShaderSource(shader, source)
        GL.glCompileShader(shader)
        if not GL.glGetShaderiv(shader, GL.GL_COMPILE_STATUS):
            log = GL.glGetShaderInfoLog(shader).decode()
            raise RuntimeError(f"Shader compile error:\n{log}")
        return shader


    def _compile_program(vert_file: str, frag_file: str) -> int:
        vert = _compile_shader(_load_shader_source(vert_file), GL.GL_VERTEX_SHADER)
        frag = _compile_shader(_load_shader_source(frag_file), GL.GL_FRAGMENT_SHADER)
        program = GL.glCreateProgram()
        GL.glAttachShader(program, vert)
        GL.glAttachShader(program, frag)
        GL.glLinkProgram(program)
        GL.glDeleteShader(vert)
        GL.glDeleteShader(frag)
        if not GL.glGetProgramiv(program, GL.GL_LINK_STATUS):
            log = GL.glGetProgramInfoLog(program).decode()
            raise RuntimeError(f"Shader link error:\n{log}")
        return program


    def _rect_strip(x: float, y: float, w: float, h: float) -> List[Tuple[float, float]]:
        return [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]


    def _rect_loop(x: float, y: float, w: float, h: float) -> List[Tuple[float, float]]:
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]


    class GLDrawingPanel(wx.glcanvas.GLCanvas):
        PIXEL_DENSITY = 10

        pan: Tuple[float, float]
        zoom: float

        def __init__(self, parent, *args, **kwargs) -> None:
            self.stitch_plan: Optional[StitchPlan] = kwargs.pop('stitch_plan', None)

            attribs = [wx.glcanvas.WX_GL_DOUBLEBUFFER, 0]
            kwargs['attribList'] = attribs
            kwargs['style'] = kwargs.get('style', 0) | wx.BORDER_SUNKEN

            wx.glcanvas.GLCanvas.__init__(self, parent, *args, **kwargs)

            self.gl_context = wx.glcanvas.GLContext(self)
            self._gl_initialized = False

            self.SetMinSize((300, 300))

            self.width = 0
            self.height = 0
            self.loaded = False
            self.current_stitch = 0
            self.page_specs = {}
            self.background_color = None
            self.show_page = True

            self.pan = (0.0, 0.0)
            self.zoom = 1.0
            self._vbos: List[Tuple] = []
            self._positions: List[np.ndarray] = []  # CPU copy of positions for crosshair

            self.Bind(wx.EVT_PAINT, self.on_paint)
            self.Bind(wx.EVT_SIZE, self.on_resize)
            self.Bind(wx.EVT_LEFT_DOWN, self.on_left_mouse_button_down)
            self.Bind(wx.EVT_MOUSEWHEEL, self.on_mouse_wheel)

        # ------------------------------------------------------------------
        # GL lifecycle
        # ------------------------------------------------------------------

        def _init_gl(self) -> None:
            self.shader = _compile_program('stitch.vert', 'stitch.frag')
            self.u_projection = GL.glGetUniformLocation(self.shader, 'u_projection')
            self.u_transform = GL.glGetUniformLocation(self.shader, 'u_transform')
            self.u_color = GL.glGetUniformLocation(self.shader, 'u_color')

            # Single VAO+VBO reused for page, crosshair, scale bar (few vertices, updated each frame)
            self._overlay_vao = GL.glGenVertexArrays(1)
            self._overlay_vbo = GL.glGenBuffers(1)
            GL.glBindVertexArray(self._overlay_vao)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._overlay_vbo)
            GL.glEnableVertexAttribArray(0)
            GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, False, 0, None)
            GL.glBindVertexArray(0)

            GL.glEnable(GL.GL_BLEND)
            GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)

            self._gl_initialized = True

        # ------------------------------------------------------------------
        # Paint
        # ------------------------------------------------------------------

        def on_paint(self, event: wx.PaintEvent) -> None:
            self.SetCurrent(self.gl_context)
            if not self._gl_initialized:
                self._init_gl()

            size = self.GetClientSize()
            GL.glViewport(0, 0, max(size.Width, 1), max(size.Height, 1))
            GL.glClearColor(*self._background_rgba())
            GL.glClear(GL.GL_COLOR_BUFFER_BIT)

            if not self.loaded:
                self.SwapBuffers()
                return

            GL.glUseProgram(self.shader)
            world_proj = self._ortho_matrix()
            world_xform = self._transform_matrix()
            identity = np.eye(4, dtype=np.float32)

            # 1. Page background (world space, drawn before stitches)
            GL.glUniformMatrix4fv(self.u_projection, 1, GL.GL_TRUE, world_proj)
            GL.glUniformMatrix4fv(self.u_transform, 1, GL.GL_TRUE, world_xform)
            self._draw_page()

            # 2. Stitches + NPP (world space)
            last_pos = None
            stitch_offset = 0
            for (vao, vbo, color_rgba, num_verts, jumps), positions in zip(self._vbos, self._positions):
                block_end = stitch_offset + num_verts

                if block_end <= self.current_stitch:
                    visible = num_verts
                elif stitch_offset < self.current_stitch:
                    visible = self.current_stitch - stitch_offset
                else:
                    break

                GL.glUniform4f(self.u_color, *color_rgba)
                GL.glBindVertexArray(vao)
                self._draw_segments(visible, jumps)

                if global_settings['npp_button_status'] and visible > 0:
                    self._draw_npp(visible, color_rgba)

                if visible > 0:
                    last_pos = positions[visible - 1]

                stitch_offset = block_end

            GL.glBindVertexArray(0)

            # 3. Screen-space overlays: crosshair and scale bar
            # u_projection stays as world_proj (= screen ortho); only transform changes to identity
            GL.glUniformMatrix4fv(self.u_transform, 1, GL.GL_TRUE, identity)

            if last_pos is not None and global_settings['display_crosshair']:
                self._draw_crosshair(last_pos)

            scale_label = self._draw_scale_bar()

            GL.glUseProgram(0)
            self.SwapBuffers()

            # 4. Scale bar text drawn via wx.ClientDC on top of the GL surface
            if scale_label:
                tx, ty, text = scale_label
                dc = wx.ClientDC(self)
                dc.SetFont(wx.Font(12, wx.DEFAULT, wx.NORMAL, wx.NORMAL))
                dc.SetTextForeground(wx.BLACK)
                dc.DrawText(text, int(tx), int(ty))

        # ------------------------------------------------------------------
        # Resize / zoom / pan
        # ------------------------------------------------------------------

        def on_resize(self, event: wx.SizeEvent) -> None:
            if self._gl_initialized:
                self.SetCurrent(self.gl_context)
                size = self.GetClientSize()
                GL.glViewport(0, 0, max(size.Width, 1), max(size.Height, 1))
            self.choose_zoom_and_pan()
            self.Refresh()

        def choose_zoom_and_pan(self, event: Optional[wx.SizeEvent] = None) -> None:
            if not self.width and not self.height and event is not None:
                return

            size: wx.Rect = self.GetClientSize()
            panel_width = size.Width
            panel_height = size.Height

            width_ratio = panel_width / float(self.width + 10)
            height_ratio = panel_height / float(self.height + 10)
            self.zoom = max(min(width_ratio, height_ratio), 0.01)

            self.pan = (
                (panel_width - self.zoom * self.width) / 2.0,
                (panel_height - self.zoom * self.height) / 2.0,
            )

        def on_left_mouse_button_down(self, event: wx.MouseEvent) -> None:
            if self.loaded:
                self.CaptureMouse()
                self.drag_start = event.GetPosition()
                self.drag_original_pan = self.pan
                self.Bind(wx.EVT_MOTION, self.on_drag)
                self.Bind(wx.EVT_MOUSE_CAPTURE_LOST, self.on_drag_end)
                self.Bind(wx.EVT_LEFT_UP, self.on_drag_end)

        def on_drag(self, event: wx.MouseEvent) -> None:
            if self.HasCapture() and event.Dragging():
                delta = event.GetPosition()
                offset = (delta.x - self.drag_start[0], delta.y - self.drag_start[1])
                self.pan = (self.drag_original_pan[0] + offset[0], self.drag_original_pan[1] + offset[1])
                self.Refresh()

        def on_drag_end(self, event: wx.MouseEvent) -> None:
            if self.HasCapture():
                self.ReleaseMouse()
            self.Unbind(wx.EVT_MOTION)
            self.Unbind(wx.EVT_MOUSE_CAPTURE_LOST)
            self.Unbind(wx.EVT_LEFT_UP)

        def on_mouse_wheel(self, event: wx.MouseEvent) -> None:
            zoom_delta = 1.03 if event.GetWheelRotation() > 0 else 0.97

            matrix = wx.AffineMatrix2D()
            matrix.Translate(*self.pan)
            matrix.Scale(self.zoom, self.zoom)

            pos = event.GetPosition()
            inverse_matrix = wx.AffineMatrix2D()
            inverse_matrix.Set(*matrix.Get())
            inverse_matrix.Invert()
            pos = inverse_matrix.TransformPoint(*pos)

            x_old, y_old = matrix.TransformPoint(*pos)
            matrix.Scale(zoom_delta, zoom_delta)
            x_new, y_new = matrix.TransformPoint(*pos)

            self.pan = (self.pan[0] - (x_new - x_old), self.pan[1] - (y_new - y_old))
            self.zoom *= zoom_delta
            self.Refresh()

        # ------------------------------------------------------------------
        # Public interface (same as DrawingPanel)
        # ------------------------------------------------------------------

        def load(self, stitch_plan: StitchPlan) -> None:
            self.minx, self.miny, self.maxx, self.maxy = stitch_plan.bounding_box
            self.width = self.maxx - self.minx
            self.height = self.maxy - self.miny
            self.current_stitch = 0

            blocks = self._parse_stitch_plan(stitch_plan)

            self.SetCurrent(self.gl_context)
            if not self._gl_initialized:
                self._init_gl()

            self._cleanup_vbos()
            for arr, jumps, color_rgba in blocks:
                vao = GL.glGenVertexArrays(1)
                vbo = GL.glGenBuffers(1)
                GL.glBindVertexArray(vao)
                GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
                GL.glBufferData(GL.GL_ARRAY_BUFFER, arr.nbytes, arr, GL.GL_STATIC_DRAW)
                GL.glEnableVertexAttribArray(0)
                GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, False, 0, None)
                GL.glBindVertexArray(0)
                self._vbos.append((vao, vbo, color_rgba, len(arr), jumps))
                self._positions.append(arr)

            self.choose_zoom_and_pan()
            self.loaded = True
            self.Refresh()

        def clear(self) -> None:
            if self._gl_initialized:
                self.SetCurrent(self.gl_context)
                self._cleanup_vbos()
            self.loaded = False
            self.Refresh()

        def set_current_stitch(self, stitch: int) -> None:
            self.current_stitch = stitch
            self.Refresh()

        def set_page_specs(self, page_specs) -> None:
            self.page_specs = page_specs

        def set_background_color(self, color) -> None:
            self.background_color = color
            self.Refresh()

        def set_show_page(self, show_page: bool) -> None:
            self.show_page = show_page
            self.Refresh()

        def update_pen_size(self) -> None:
            self.Refresh()

        # ------------------------------------------------------------------
        # Draw helpers
        # ------------------------------------------------------------------

        def _draw_overlay(self, verts: List[Tuple[float, float]], primitive: int,
                          color: Tuple[float, float, float, float]) -> None:
            """Upload verts to the shared overlay VBO and draw."""
            arr = np.array(verts, dtype=np.float32)
            GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._overlay_vbo)
            GL.glBufferData(GL.GL_ARRAY_BUFFER, arr.nbytes, arr, GL.GL_STREAM_DRAW)
            GL.glBindVertexArray(self._overlay_vao)
            GL.glUniform4f(self.u_color, *color)
            GL.glDrawArrays(primitive, 0, len(arr))
            GL.glBindVertexArray(0)

        def _draw_page(self) -> None:
            if not self.page_specs or not self.show_page:
                return

            # Mirror DrawingPanel.draw_page() coordinate formula exactly
            pd = self.PIXEL_DENSITY
            px = -self.page_specs['x'] * pd
            py = -self.page_specs['y'] * pd
            pw = self.page_specs['width'] * pd
            ph = self.page_specs['height'] * pd

            border_c = wx.Colour(self.page_specs['border_color'])
            br, bg, bb = border_c.Red() / 255.0, border_c.Green() / 255.0, border_c.Blue() / 255.0

            if self.page_specs.get('show_page_shadow'):
                shadow = (br, bg, bb, 65 / 255.0)
                self._draw_overlay(_rect_strip(px + 4 * pd, py + 4 * pd, pw, ph),
                                   GL.GL_TRIANGLE_STRIP, shadow)

            page_c = wx.Colour(self.background_color or self.page_specs['page_color'])
            fill = (page_c.Red() / 255.0, page_c.Green() / 255.0, page_c.Blue() / 255.0, 1.0)
            self._draw_overlay(_rect_strip(px, py, pw, ph), GL.GL_TRIANGLE_STRIP, fill)

            border = (br, bg, bb, 1.0)
            self._draw_overlay(_rect_loop(px, py, pw, ph), GL.GL_LINE_LOOP, border)

        def _draw_npp(self, visible: int, color_rgba: Tuple[float, float, float, float]) -> None:
            npp_px = global_settings['simulator_npp_size'] * PIXELS_PER_MM * self.zoom
            GL.glPointSize(max(1.0, npp_px))
            GL.glUniform4f(self.u_color, *color_rgba)
            GL.glDrawArrays(GL.GL_POINTS, 0, visible)
            GL.glPointSize(1.0)

        def _world_to_screen(self, wx_coord: float, wy_coord: float) -> Tuple[float, float]:
            s = self.zoom / self.PIXEL_DENSITY
            return s * wx_coord + self.pan[0], s * wy_coord + self.pan[1]

        def _draw_crosshair(self, last_pos: np.ndarray) -> None:
            sx, sy = self._world_to_screen(float(last_pos[0]), float(last_pos[1]))
            r = float(global_settings['simulator_crosshair_radius'])
            c = wx.Colour(global_settings['simulator_crosshair_colour'])
            color = (c.Red() / 255.0, c.Green() / 255.0, c.Blue() / 255.0, 1.0)
            thickness = float(global_settings['simulator_crosshair_thickness'])

            GL.glLineWidth(thickness)
            self._draw_overlay([(sx - r, sy), (sx + r, sy)], GL.GL_LINES, color)
            self._draw_overlay([(sx, sy - r), (sx, sy + r)], GL.GL_LINES, color)
            GL.glLineWidth(1.0)

        def _draw_scale_bar(self) -> Optional[Tuple[float, float, str]]:
            """Draw scale bar lines. Returns (text_x, text_y, label) for the wx text overlay."""
            size = self.GetClientSize()
            W, H = size.Width, size.Height

            one_mm = PIXELS_PER_MM * self.zoom
            scale_width = one_mm
            scale_width_mm = 1
            max_width = min(W * 0.5, 300)
            min_width = 50

            if scale_width > max_width:
                exponent = math.floor(math.log2(max_width / one_mm))
                if exponent < -6:
                    return None
                scale_width = one_mm * 2 ** exponent
                scale_width_mm = round(2 ** exponent, 2)

            if scale_width < min_width:
                scale_width_mm = int(min_width / one_mm)
                scale_width = one_mm * scale_width_mm

            x, y, sw = 20.0, float(H - 30), scale_width
            verts = [
                (x,          y - 6),
                (x,          y),
                (x + sw / 2, y),
                (x + sw / 2, y - 3),
                (x + sw / 2, y),
                (x + sw,     y),
                (x + sw,     y - 6),
            ]
            self._draw_overlay(verts, GL.GL_LINE_STRIP, (0.0, 0.0, 0.0, 1.0))

            return (x, y + 5, f"{scale_width_mm} mm")

        # ------------------------------------------------------------------
        # Helpers
        # ------------------------------------------------------------------

        def _background_color_str(self) -> str:
            if self.page_specs:
                if self.show_page:
                    return self.page_specs.get('desk_color', '#FFFFFF')
                return self.background_color or self.page_specs.get('page_color', '#FFFFFF')
            return self.background_color or '#FFFFFF'

        def _background_rgba(self) -> Tuple[float, float, float, float]:
            c = wx.Colour(self._background_color_str())
            return c.Red() / 255.0, c.Green() / 255.0, c.Blue() / 255.0, 1.0

        def _color_to_rgba(self, color: ThreadColor) -> Tuple[float, float, float, float]:
            rgb = color.visible_on_background(self._background_color_str()).rgb
            return rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, 1.0

        def _parse_stitch_plan(self, stitch_plan: StitchPlan) -> List[Tuple]:
            blocks = []
            for color_block in stitch_plan:
                color_rgba = self._color_to_rgba(color_block.color)
                verts: List[List[float]] = []
                jumps: List[int] = []
                stitch_index = 0

                for stitch in color_block:
                    verts.append([
                        self.PIXEL_DENSITY * (stitch.x - self.minx),
                        self.PIXEL_DENSITY * (stitch.y - self.miny),
                    ])
                    if stitch.jump:
                        jumps.append(stitch_index)

                    if stitch.trim or stitch.stop or stitch.color_change:
                        if verts:
                            blocks.append((np.array(verts, dtype=np.float32), jumps, color_rgba))
                        verts = []
                        jumps = []
                        stitch_index = 0
                    else:
                        stitch_index += 1

                if verts:
                    blocks.append((np.array(verts, dtype=np.float32), jumps, color_rgba))

            return blocks

        def _cleanup_vbos(self) -> None:
            for vao, vbo, *_ in self._vbos:
                GL.glDeleteVertexArrays(1, [vao])
                GL.glDeleteBuffers(1, [vbo])
            self._vbos = []
            self._positions = []

        def _ortho_matrix(self) -> np.ndarray:
            size = self.GetClientSize()
            W = max(size.Width, 1)
            H = max(size.Height, 1)
            return np.array([
                [2 / W,     0,    0,  -1],
                [0,    -2 / H,    0,   1],
                [0,         0,   -1,   0],
                [0,         0,    0,   1],
            ], dtype=np.float32)

        def _transform_matrix(self) -> np.ndarray:
            s = self.zoom / self.PIXEL_DENSITY
            tx, ty = self.pan
            return np.array([
                [s,  0,  0,  tx],
                [0,  s,  0,  ty],
                [0,  0,  1,   0],
                [0,  0,  0,   1],
            ], dtype=np.float32)

        def _draw_segments(self, visible: int, jumps: List[int]) -> None:
            if global_settings['jump_button_status'] or not jumps:
                if visible > 1:
                    GL.glDrawArrays(GL.GL_LINE_STRIP, 0, visible)
                return

            visible_jumps = [j for j in jumps if 0 < j < visible]
            starts = [0] + visible_jumps
            ends = visible_jumps + [visible]
            for start, end in zip(starts, ends):
                if end - start > 1:
                    GL.glDrawArrays(GL.GL_LINE_STRIP, start, end - start)
