# Plan: Renderizado GPU para el Simulador de Puntadas

## Objetivo

Reemplazar el backend de dibujo `wx.GraphicsContext` (CPU, software) del simulador
por un canvas OpenGL (`wx.glcanvas.GLCanvas`) que cargue todas las puntadas en la
GPU una sola vez y renderice cada frame con un único draw call por color.

El resto del simulador (controles, animación, zoom/pan, lógica de `SimulatorPanel`)
no cambia.

---

## Diagnóstico del cuello de botella actual

Archivo: `lib/gui/simulator/drawing_panel.py`

### ¿Qué pasa en cada frame?

```
OnPaint()                           ← wx EVT_PAINT, ~30 FPS
  └─ draw_stitches(canvas)
       └─ for cada bloque de color:
            ├─ canvas.StrokeLines(stitches[:current_stitch])   ← copia Python list → GDI cada frame
            └─ draw_needle_penetration_points(...)              ← segunda pasada sobre los mismos datos
```

Con un diseño de 80 000 puntadas en 20 bloques de color:
- **160 llamadas** a `StrokeLines` / `StrokeLineSegments` por frame
- **Cada llamada** serializa una lista Python hacia el driver gráfico del SO
- El slice `stitches[:current_stitch]` **crea una nueva lista Python** cada frame

### ¿Qué tiene de bueno la estructura actual para migrar?

`parse_stitch_plan()` (línea 276) ya pre-convierte cada `Stitch` a una tupla
`(x_scaled, y_scaled)` y las agrupa por bloque de color en `self.stitch_blocks`.
Eso es exactamente lo que necesita un VBO: un array plano de floats por color.

---

## Arquitectura propuesta

```
DrawingPanel (wx.Panel)              →   GLDrawingPanel (wx.glcanvas.GLCanvas)
  wx.GraphicsContext                 →   OpenGL 3.3 Core Profile
  StrokeLines() por frame            →   glDrawArrays(0, current_stitch) por frame
  Transformación Python cada frame   →   Uniform mat4 (proyección + pan + zoom)
  numpy.split() para saltos          →   VBO separado para jump segments
```

### Componentes nuevos

| Archivo | Descripción |
|---|---|
| `lib/gui/simulator/gl_drawing_panel.py` | Reemplazo de `drawing_panel.py` con GLCanvas |
| `lib/gui/simulator/shaders/stitch.vert` | Vertex shader: aplica proyección, pasa color |
| `lib/gui/simulator/shaders/stitch.frag` | Fragment shader: color sólido por línea |

`drawing_panel.py` original se mantiene intacto. `simulator_panel.py` elige cuál
usar según disponibilidad de OpenGL (fallback automático).

---

## Plan de implementación paso a paso

### Paso 1 — Dependencias y detección de OpenGL

**Archivo:** `lib/gui/simulator/gl_drawing_panel.py` (nuevo)

- Importar `wx.glcanvas` y `OpenGL.GL` (PyOpenGL, ya disponible en entornos con wx)
- Detectar soporte en tiempo de carga:
  ```python
  try:
      import wx.glcanvas
      from OpenGL import GL
      OPENGL_AVAILABLE = True
  except ImportError:
      OPENGL_AVAILABLE = False
  ```
- Exponer `OPENGL_AVAILABLE` para que `simulator_panel.py` elija el panel correcto

**Criterio de éxito:** el módulo importa sin error en entornos con y sin OpenGL.

---

### Paso 2 — GLDrawingPanel: inicialización del canvas

**Clase:** `GLDrawingPanel(wx.glcanvas.GLCanvas)`

```python
attribs = [
    wx.glcanvas.WX_GL_CORE_PROFILE,
    wx.glcanvas.WX_GL_MAJOR_VERSION, 3,
    wx.glcanvas.WX_GL_MINOR_VERSION, 3,
    wx.glcanvas.WX_GL_DOUBLEBUFFER,
    0  # terminator
]
self.gl_context = wx.glcanvas.GLContext(self, attribList=attribs)
```

Binds necesarios (mismos que `DrawingPanel` actual):
- `EVT_PAINT` → `on_paint()`
- `EVT_SIZE` → `on_resize()`
- `EVT_LEFT_DOWN`, `EVT_MOUSEWHEEL` → drag y zoom (lógica idéntica al original)

La primera llamada a `on_paint()` llama a `_init_gl()` si aún no se inicializó.

**Criterio de éxito:** ventana GL abre sin errores, fondo negro o blanco sólido.

---

### Paso 3 — Shaders

**`shaders/stitch.vert`**

```glsl
#version 330 core

layout(location = 0) in vec2 position;

uniform mat4 u_projection;   // ortho(0, width, height, 0)
uniform mat4 u_transform;    // pan + zoom

void main() {
    gl_Position = u_projection * u_transform * vec4(position, 0.0, 1.0);
}
```

**`shaders/stitch.frag`**

```glsl
#version 330 core

uniform vec4 u_color;
out vec4 frag_color;

void main() {
    frag_color = u_color;
}
```

Compilación en `_init_gl()`:
```python
self.shader = _compile_program('stitch.vert', 'stitch.frag')
self.u_projection = GL.glGetUniformLocation(self.shader, 'u_projection')
self.u_transform   = GL.glGetUniformLocation(self.shader, 'u_transform')
self.u_color       = GL.glGetUniformLocation(self.shader, 'u_color')
```

**Criterio de éxito:** shaders compilan, se puede hacer `glUseProgram` sin errores.

---

### Paso 4 — Carga de puntadas en VBOs (`load()`)

Reemplaza `parse_stitch_plan()`. En lugar de listas Python de tuplas, produce
arrays numpy `float32` y los sube a la GPU.

```python
def load(self, stitch_plan):
    self.vbos = []        # lista de (vao, vbo, color_rgba, num_verts, jump_indices)
    self.current_stitch = 0
    # ... reutiliza la misma lógica de parse_stitch_plan para extraer bloques ...

    for color_block in stitch_plan:
        verts = []
        jumps = []
        for i, stitch in enumerate(color_block):
            verts.append([PIXEL_DENSITY * (stitch.x - minx),
                          PIXEL_DENSITY * (stitch.y - miny)])
            if stitch.jump:
                jumps.append(i)

        arr = np.array(verts, dtype=np.float32)  # shape (N, 2)

        vao = GL.glGenVertexArrays(1)
        vbo = GL.glGenBuffers(1)
        GL.glBindVertexArray(vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, arr.nbytes, arr, GL.GL_STATIC_DRAW)
        GL.glEnableVertexAttribArray(0)
        GL.glVertexAttribPointer(0, 2, GL.GL_FLOAT, False, 0, None)
        GL.glBindVertexArray(0)

        rgba = color_to_rgba(color_block.color, ...)
        self.vbos.append((vao, vbo, rgba, len(arr), jumps))

    self.loaded = True
    self.choose_zoom_and_pan()
```

**Puntos clave:**
- `GL_STATIC_DRAW`: los datos no cambian después de la carga
- Los saltos (`jumps`) se guardan como índices Python; se usan en el paso 5
  para calcular rangos de `glDrawArrays` sin tocar los datos GPU

**Criterio de éxito:** la carga no produce errores GL; `len(self.vbos)` == número
de bloques de color.

---

### Paso 5 — Render loop (`on_paint()`)

```python
def on_paint(self, event):
    self.SetCurrent(self.gl_context)
    if not self._gl_initialized:
        self._init_gl()

    GL.glClearColor(*self.background_rgba)
    GL.glClear(GL.GL_COLOR_BUFFER_BIT)

    if not self.loaded:
        self.SwapBuffers()
        return

    GL.glUseProgram(self.shader)
    GL.glUniformMatrix4fv(self.u_projection, 1, False, self._ortho_matrix())
    GL.glUniformMatrix4fv(self.u_transform,  1, False, self._transform_matrix())

    stitch_offset = 0
    for vao, vbo, color, num_verts, jumps in self.vbos:
        block_end = stitch_offset + num_verts

        if block_end <= self.current_stitch:
            visible = num_verts          # bloque completo
        elif stitch_offset < self.current_stitch:
            visible = self.current_stitch - stitch_offset   # bloque parcial
        else:
            break                        # bloque futuro, no dibujar

        GL.glUniform4f(self.u_color, *color)
        GL.glBindVertexArray(vao)

        # Dibujar segmentos excluyendo saltos
        self._draw_segments(visible, jumps)

        stitch_offset = block_end

    GL.glBindVertexArray(0)
    self.SwapBuffers()
```

**`_draw_segments(visible, jumps)`** calcula rangos entre saltos y emite
`glDrawArrays(GL_LINE_STRIP, start, count)` por cada segmento continuo.
Esto reemplaza el `numpy.split()` + bucle actual, pero ahora sin copiar datos.

**`_ortho_matrix()`** construye una proyección ortográfica con `numpy` según el
tamaño actual del panel (se recalcula solo en resize).

**`_transform_matrix()`** aplica pan y zoom como una matriz 4×4, exactamente
como lo hace `wx.AffineMatrix2D` hoy.

**Criterio de éxito:** las puntadas aparecen en pantalla con los colores correctos;
avanzar `current_stitch` muestra más puntadas.

---

### Paso 6 — Puntos de penetración de aguja (NPP)

Los NPP son los mismos vértices, dibujados como `GL_POINTS` con `gl_PointSize`
grande en lugar de `GL_LINE_STRIP`.

```python
if global_settings['npp_button_status']:
    GL.glEnable(GL.GL_PROGRAM_POINT_SIZE)   # permite controlar tamaño desde shader
    # Agregar línea al vertex shader: gl_PointSize = u_npp_size;
    GL.glBindVertexArray(vao)
    GL.glDrawArrays(GL.GL_POINTS, 0, visible)
```

Alternativa sin modificar el shader: `GL.glPointSize(npp_size)` antes del draw.

**Criterio de éxito:** los NPP aparecen/desaparecen correctamente con la opción
del panel de vista.

---

### Paso 7 — Integración en SimulatorPanel con fallback

**Archivo:** `lib/gui/simulator/simulator_panel.py` (modificación mínima)

```python
from . import ControlPanel, ViewPanel
try:
    from .gl_drawing_panel import GLDrawingPanel, OPENGL_AVAILABLE
except ImportError:
    OPENGL_AVAILABLE = False

# En __init__:
if OPENGL_AVAILABLE:
    self.dp = GLDrawingPanel(self)
else:
    self.dp = DrawingPanel(self)   # fallback original
```

`GLDrawingPanel` expone la misma interfaz pública que `DrawingPanel`:
- `load(stitch_plan)`
- `set_current_stitch(n)`
- `clear()`
- `set_page_specs(specs)`
- `set_background_color(color)`
- `choose_zoom_and_pan()`

**Criterio de éxito:** `SimulatorPanel` funciona igual con ambos backends;
el fallback activa automáticamente si PyOpenGL no está instalado.

---

### Paso 8 — Página de fondo y escala

La **página** (rectángulo con borde de `draw_page()`) se puede dibujar en GL
como dos `glDrawArrays(GL_LINE_LOOP, ...)` o simplemente mantener el dibujo
con `wx.GraphicsContext` en un overlay — dado que es un elemento estático que
no impacta el rendimiento.

La **barra de escala** (`draw_scale()`) también es estática. Se puede dibujar
con un segundo `wx.PaintDC` overlay encima del GLCanvas, o replicar en GL.

Decisión recomendada: **overlay wx** para página y escala en primera iteración,
migrar a GL si se quiere mayor pureza.

---

### Paso 9 — Crosshair

El crosshair sigue la última puntada visible. En GL es un `glDrawArrays(GL_LINES)`
con dos segmentos, calculados en CPU a partir de las coordenadas de la última
puntada. Misma lógica que `draw_crosshair()` actual.

---

## Orden de trabajo y criterios de aceptación

| # | Tarea | Archivo(s) | Aceptación |
|---|---|---|---|
| 1 | Detección OpenGL + estructura base | `gl_drawing_panel.py` | Importa sin crash |
| 2 | Canvas GL + init shaders | `gl_drawing_panel.py`, `shaders/` | Ventana abre, fondo limpio |
| 3 | Carga VBOs | `gl_drawing_panel.py` | Sin errores GL en carga |
| 4 | Render loop básico | `gl_drawing_panel.py` | Puntadas visibles con color |
| 5 | Pan, zoom, resize | `gl_drawing_panel.py` | Interacción idéntica al original |
| 6 | NPP | `gl_drawing_panel.py` | Toggle NPP funciona |
| 7 | Integración + fallback | `simulator_panel.py` | Ambos backends cargan igual |
| 8 | Página y escala (overlay) | `gl_drawing_panel.py` | Página y regla visibles |
| 9 | Crosshair | `gl_drawing_panel.py` | Crosshair sigue puntada actual |

---

## Dependencias externas

| Paquete | Uso | Ya en el proyecto |
|---|---|---|
| `PyOpenGL` | Bindings OpenGL | No — agregar a `pyproject.toml` / `requirements.txt` |
| `numpy` | Arrays float32 para VBOs | Sí (`from numpy import split` en drawing_panel.py) |
| `wx.glcanvas` | Canvas OpenGL en wxPython | Viene con wxPython |

Comando de instalación:
```bash
pip install PyOpenGL PyOpenGL_accelerate
```

---

## Selección de GPU en sistemas con múltiples GPUs

El plan actual **no implementa selección de GPU** — `wx.glcanvas.GLCanvas` delega
al SO/driver, que en laptops con GPU integrada + discreta elige la **integrada**
por defecto (Intel o AMD APU). Para ink/stitch esto es aceptable: el rendering 2D
de líneas es tan simple que incluso la GPU integrada más débil es órdenes de
magnitud más rápida que `wx.GraphicsContext` en CPU.

Si aun así se quiere usar la GPU discreta, se hace a nivel de entorno — el
simulador no necesita cambios.

### Linux — AMD discreta (DRI PRIME)

```bash
DRI_PRIME=1 inkscape
```

Para verificar qué GPU está activa:
```bash
DRI_PRIME=1 glxinfo | grep "OpenGL renderer"
```

### Linux — NVIDIA discreta (PRIME Render Offload)

```bash
__NV_PRIME_RENDER_OFFLOAD=1 __GLX_VENDOR_LIBRARY_NAME=nvidia inkscape
```

O con el wrapper de NVIDIA:
```bash
prime-run inkscape
```

### Linux — forzar GPU por índice (sistemas con >2 GPUs)

```bash
DRI_PRIME=pci-0000_01_00_0 inkscape   # usar PCI ID exacta
```

Listar GPUs disponibles:
```bash
ls /dev/dri/by-path/
```

### Windows y macOS

En estos sistemas el driver elige automáticamente según el perfil de energía.
Para forzar la discreta:
- **Windows:** Panel de control NVIDIA/AMD → "Agregar aplicación" → seleccionar
  `inkscape.exe` → "Alto rendimiento"
- **macOS:** No aplica desde macOS 14 (Apple Silicon usa GPU unificada)

### Documentar en preferencias del simulador

Aunque no se implementará selección programática en esta iteración, se puede
agregar una nota informativa en `simulator_preferences.py` o en la ayuda del
simulador apuntando a estas variables de entorno. Se deja como issue futuro.

---

## Lo que NO cambia

- `SimulatorPanel` y su lógica de animación (`animate()`, `set_current_stitch()`)
- `ControlPanel`, `ViewPanel`, `SimulatorWindow`
- La estructura de datos `StitchPlan` / `ColorBlock` / `Stitch`
- Todos los controles de UI (velocidad, play/pause, slider, NPP toggle, etc.)
- El fallback a `DrawingPanel` original si OpenGL no está disponible

---

## Estimación de esfuerzo

| Tarea | Estimado |
|---|---|
| Pasos 1–4 (VBOs + render básico) | 4–6 horas |
| Paso 5 (pan/zoom/resize) | 2 horas |
| Pasos 6–9 (NPP, página, escala, crosshair) | 3–4 horas |
| Integración + pruebas | 2 horas |
| **Total** | **~12 horas** |

Ganancia esperada en diseños grandes (>50 000 puntadas): de ~5–10 FPS efectivos
a 30 FPS sostenidos, con uso de CPU cercano a cero durante la animación.

---

## Issues pendientes (fuera del scope GPU)

### Issue A — Apertura lenta de la ventana del simulador ✅ RESUELTO

**Síntoma original:** El simulador tardaba 30–60 segundos en mostrar la ventana.

**Causa raíz:** `simulator.py` generaba el stitch plan completo de forma síncrona
(llamando `element.embroider()` en un bucle) antes de crear la ventana. Para diseños
grandes esto bloqueaba el proceso entero durante minutos.

**Solución implementada** (`lib/extensions/simulator.py`):
- La ventana se muestra de inmediato al abrir la extensión.
- Los elementos se procesan uno a uno con `wx.CallLater(1, process_next)`, cediendo
  1 ms al event loop entre cada elemento (sin threads — lxml no es thread-safe).
- El simulador actualiza su preview cada `PROGRESSIVE_UPDATE_INTERVAL = 0.5 s` con
  el plan parcial acumulado, y hace una actualización final al terminar.
- Los datos SVG (dimensiones de página, colores) se extraen en el hilo principal
  antes del event loop para evitar accesos concurrentes a lxml.

**Resultado:** apertura en 1–3 segundos en lugar de 30–60 segundos. La animación
aparece progresivamente elemento a elemento.

---

### Issue C — Apertura lenta del panel Params (~30 s)

**Síntoma:** Abrir Extensions → Ink/Stitch → Params muestra la ventana con ~30 s de retraso.
La animación del simulador integrado corre suave una vez que abre (GL backend funciona).

**Diferencia con Issue A (ya resuelto):** El simulador standalone fue arreglado con
carga progresiva via `wx.CallLater`. Params tiene su propio flujo donde el stitch plan
ya corre en un hilo de fondo (`PreviewRenderer`), pero la construcción de la UI es lenta.

**Causa raíz:** `SplitSimulatorWindow.__init__` construye toda la UI sincrónicamente
antes de que `app.MainLoop()` arranque. El cuello de botella es `create_tabs()` en
`params.py`, que instancia un widget de parámetros completo por cada elemento SVG
seleccionado. Con diseños de 50+ objetos esto suma 10–30 segundos.

```
effect()  [params.py]
  └─ app = wx.App()
     frame = SplitSimulatorWindow(...)        # bloquea hasta terminar
       └─ SettingsPanel.__init__()
            └─ create_tabs(notebook)          # ← LENTO: 1 widget por elemento
               update_preview()              # lanza PreviewRenderer (background, OK)
     frame.Show()                            # ventana aparece aquí, 30 s después
     app.MainLoop()
```

**Plan de solución:**

1. Mostrar `SplitSimulatorWindow` vacío de inmediato (solo el splitter y el simulador).
2. Construir los tabs de parámetros en diferido con `wx.CallLater(0, create_tabs)` desde
   el hilo principal, cediendo al event loop entre grupos de tabs.
3. Mientras los tabs cargan, mostrar un spinner o mensaje "Cargando parámetros…" en el
   panel de configuración.

**Archivos a modificar:**
- `lib/gui/simulator/split_simulator_window.py` — diferir creación del `SettingsPanel`
- `lib/extensions/params.py` — adaptar `SettingsPanel.__init__` para construcción lazy

**Complejidad:** Media. Requiere refactorizar el orden de inicialización de `SettingsPanel`
sin romper la comunicación entre tabs, presets y el simulador.

---

### Issue B — Guardado lento en formato Janome JEF (y otros formatos)

**Síntoma:** "Save as → Janome JEF file" tarda varios segundos en guardar.

**Causa probable:** La conversión de formato (pyembroidery) recorre toda la colección
de puntadas en Python puro, serializa coordenadas y escribe el archivo byte a byte.
Para diseños grandes esto es inherentemente lento en la CPU.

**Scope:** Este issue está fuera del trabajo de renderizado GPU. Se anota aquí para
no perderlo. Una futura optimización podría cachear el archivo de salida o mover la
conversión a un hilo de fondo con barra de progreso.
