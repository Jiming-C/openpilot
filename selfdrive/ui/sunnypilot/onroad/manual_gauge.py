"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Manual-transmission gauge: current gear plus a segmented RPM strip that doubles as a shift
light. Only drawn while openpilot is fully disengaged, since it is there for the driver who is
working the clutch themselves.
"""
import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

# Civic Si (FE1): red arc on the cluster begins just past 6500.
REDLINE_RPM = 6500
AMBER_RPM = REDLINE_RPM - 900   # 5600 - start warning well before the marked red zone
RED_RPM = REDLINE_RPM - 300     # 6200 - shift now

# Lugging is low revs UNDER LOAD. The 1.5T holds 192 lb-ft from ~2100, so it pulls cleanly from
# low down; coasting at 1100 is ordinary driving and must not nag. Tune from logged RPM later.
LUG_RPM = 1400
LUG_PEDAL = 0.15                # fraction of travel that counts as "asking for power"
LUG_MIN_SPEED = 2.2             # m/s, ~5 mph

GEAR_CLUTCH = 0                 # GEAR_MT: clutch in or neutral
GEAR_REVERSE = 14

SEG_COUNT = 40

# geometry, in px against the 2160x1080 onroad rect
PAD_X = 40
BOTTOM_GAP = 80                 # clears the 50px steering wheel icon in the bottom-left corner
POD_H = 170
GEAR_W = 150
HINT_W = 70
SEG_GAP = 6
SEG_H = 56

COL_FG = rl.Color(255, 255, 255, 235)
COL_DIM = rl.Color(255, 255, 255, 115)
COL_OFF = rl.Color(255, 255, 255, 26)
COL_OK = rl.Color(74, 222, 128, 255)
COL_AMBER = rl.Color(251, 191, 36, 255)
COL_RED = rl.Color(239, 68, 68, 255)
COL_POD = rl.Color(10, 13, 17, 158)
COL_POD_FLASH = rl.Color(224, 32, 42, 92)
COL_EDGE = rl.Color(255, 255, 255, 26)


def _gear_label(gear: int) -> str:
  if gear == GEAR_CLUTCH:
    return "N"
  if gear == GEAR_REVERSE:
    return "R"
  if 1 <= gear <= 6:
    return str(gear)
  return "-"


class ManualGauge:
  def __init__(self):
    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_medium: rl.Font = gui_app.font(FontWeight.MEDIUM)

    self._rpm_filter = FirstOrderFilter(0.0, 0.12, 1 / gui_app.target_fps)
    self._alpha_filter = FirstOrderFilter(0.0, 0.18, 1 / gui_app.target_fps)

    self._gear = GEAR_CLUTCH
    self._lugging = False

  # ---------------------------------------------------------------- state

  @property
  def _is_manual(self) -> bool:
    cp = ui_state.sm['carParams']
    return str(cp.transmissionType) == 'manual'

  def update(self) -> None:
    visible = ui_state.status == UIStatus.DISENGAGED and self._is_manual
    self._alpha_filter.update(1.0 if visible else 0.0)
    if not visible:
      return

    cs_sp = ui_state.sm['carStateSP']
    cs = ui_state.sm['carState']

    self._gear = int(cs_sp.gearMt)
    self._rpm_filter.update(float(cs_sp.engineRpm))

    in_gear = 1 <= self._gear <= 6
    self._lugging = (in_gear and self._gear >= 3 and
                     self._rpm_filter.x < LUG_RPM and
                     cs_sp.pedalGas > LUG_PEDAL and
                     cs.vEgo > LUG_MIN_SPEED)

  # ---------------------------------------------------------------- draw

  def render(self, rect: rl.Rectangle) -> None:
    alpha = self._alpha_filter.x
    if alpha < 0.01:
      return

    rpm = max(0.0, self._rpm_filter.x)
    in_gear = 1 <= self._gear <= 6
    at_redline = in_gear and rpm >= REDLINE_RPM

    pod = rl.Rectangle(
      rect.x + PAD_X,
      rect.y + rect.height - BOTTOM_GAP - POD_H,
      rect.width - 2 * PAD_X,
      POD_H,
    )

    # backdrop - flashes at redline so it reads without looking straight at it
    flash = at_redline and (int(rl.get_time() * 6) % 2 == 0)
    base = COL_POD_FLASH if flash else COL_POD
    rl.draw_rectangle_rounded(pod, 0.18, 10, self._fade(base, alpha))
    rl.draw_rectangle_rounded_lines_ex(pod, 0.18, 10, 2, self._fade(COL_EDGE, alpha))

    self._draw_gear(pod, rpm, in_gear, alpha)
    self._draw_segments(pod, rpm, alpha)
    self._draw_hint(pod, alpha)

  def _draw_gear(self, pod: rl.Rectangle, rpm: float, in_gear: bool, alpha: float) -> None:
    colour = COL_FG
    if in_gear and rpm >= RED_RPM:
      colour = COL_RED
    elif in_gear and rpm >= AMBER_RPM:
      colour = COL_AMBER

    label = _gear_label(self._gear)
    size = 128
    txt = measure_text_cached(self._font_bold, label, size)
    pos = rl.Vector2(
      pod.x + (GEAR_W - txt.x) / 2,
      pod.y + (pod.height - txt.y) / 2 - 10,
    )
    rl.draw_text_ex(self._font_bold, label, pos, size, 0, self._fade(colour, alpha))

    cap = "GEAR"
    cap_size = 20
    cap_txt = measure_text_cached(self._font_medium, cap, cap_size)
    cap_pos = rl.Vector2(pod.x + (GEAR_W - cap_txt.x) / 2, pod.y + pod.height - 36)
    rl.draw_text_ex(self._font_medium, cap, cap_pos, cap_size, 2, self._fade(COL_DIM, alpha))

  def _draw_segments(self, pod: rl.Rectangle, rpm: float, alpha: float) -> None:
    x0 = pod.x + GEAR_W + 24
    x1 = pod.x + pod.width - HINT_W - 16
    span = x1 - x0
    if span <= 0:
      return

    seg_w = (span - SEG_GAP * (SEG_COUNT - 1)) / SEG_COUNT
    y = pod.y + (pod.height - SEG_H) / 2
    frac = min(1.0, rpm / REDLINE_RPM)
    lit = int(round(frac * SEG_COUNT))

    for i in range(SEG_COUNT):
      seg_rpm = ((i + 1) / SEG_COUNT) * REDLINE_RPM
      if i < lit:
        colour = COL_RED if seg_rpm >= RED_RPM else (COL_AMBER if seg_rpm >= AMBER_RPM else COL_OK)
      else:
        colour = COL_OFF
      bar = rl.Rectangle(x0 + i * (seg_w + SEG_GAP), y, seg_w, SEG_H)
      rl.draw_rectangle_rounded(bar, 0.3, 4, self._fade(colour, alpha))

  def _draw_hint(self, pod: rl.Rectangle, alpha: float) -> None:
    """Down-triangle meaning 'drop a gear'. Drawn rather than typed: the bundled bitmap font
    has no arrow glyph."""
    if not self._lugging:
      return
    cx = pod.x + pod.width - HINT_W / 2 - 12
    cy = pod.y + pod.height / 2
    w, h = 46.0, 38.0
    colour = self._fade(COL_AMBER, alpha)
    rl.draw_triangle(
      rl.Vector2(cx - w / 2, cy - h / 2),
      rl.Vector2(cx, cy + h / 2),
      rl.Vector2(cx + w / 2, cy - h / 2),
      colour,
    )

  @staticmethod
  def _fade(colour: rl.Color, alpha: float) -> rl.Color:
    return rl.Color(colour.r, colour.g, colour.b, int(colour.a * max(0.0, min(1.0, alpha))))
