"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.

Manual-transmission gauge: a big gear numeral beside a rev bar that doubles as a shift light.

Drawn only while openpilot is fully disengaged and no alert is up. The torque bar and the
steering-wheel icon both fade out on DISENGAGED, so the bottom band is free; alerts render
before the HUD does, so anything drawn here would otherwise land on top of them.

SIZING: mici is NOT a big_ui device - gui_app.big_ui() is true only for tici and tizi - so the
screen is 536x240 and the onroad content rect is 476x240 after the 60px side panel. Every
number below is against that, not the 2160x1080 the tici UI uses.
"""
import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

# Civic Si (FE1): the red arc on the cluster begins just past 6500.
REDLINE_RPM = 6500.0
# Split by how the car is actually used: the torque plateau runs 1800-5000 and peak power lands
# near 5700, so green is the whole plateau, amber is getting on it, red is the top of the useful
# band and the flash is the last call before the 6500 limiter.
AMBER_RPM = 5000.0
RED_RPM = 5900.0
FLASH_RPM = 6300.0              # bar and numeral blink: shift now

# Measured on the car: it starts shaking around 600rpm under load, so warn from 900.
LUG_RPM = 900.0
LUG_PEDAL = 0.15
LUG_MIN_SPEED = 2.2             # m/s, ~5 mph
LUG_MIN_GEAR = 2

# GEAR_MT is derived from engine speed against wheel speed, so with the car stopped there is
# nothing to derive it from and it holds whatever it last saw. Don't believe it below this.
MIN_TRUST_SPEED = 1.3           # m/s, ~3 mph

GEAR_CLUTCH = 0
GEAR_REVERSE = 14

# Layout against the 476x240 content rect.
MARGIN_X = 16
BAR_H = 22
BAR_BOTTOM = 16                 # bar's lower edge, up from the bottom of the rect

GEAR_SIZE = 170
GEAR_X = 20                     # the numeral sits left, the bar runs full width beneath it
GEAR_GAP = 6                    # between the numeral and the bar

# The onroad view masks its corners with a rounded black border, drawn as
# draw_rectangle_rounded_lines_ex(rect, 0.2 * 1.02, ...) - a radius of about 0.204 * 240/2.
# The numeral has to start below that or its top gets eaten.
SCREEN_CORNER_R = 25
HINT_SIZE = 34
HINT_GAP = 16

FLASH_HZ = 6.0
FLASH_LOW = 0.15                # dim end of the blink, not fully off

COL_OK = rl.Color(74, 222, 128, 255)
COL_AMBER = rl.Color(251, 191, 36, 255)
COL_RED = rl.Color(239, 68, 68, 255)
COL_WHITE = rl.Color(255, 255, 255, 255)

FG_ALPHA = 0.95
TRACK_ALPHA = 0.15

# Colour against position along the bar, so a given rev always lands on the same colour however
# long the bar is. Green holds through ordinary revs, then runs smoothly to amber and to red.
BAR_STOPS = (
  (0.0, COL_OK),
  (AMBER_RPM / REDLINE_RPM, COL_OK),
  (RED_RPM / REDLINE_RPM, COL_AMBER),
  (1.0, COL_RED),
)


def _gear_label(gear: int) -> str:
  if gear == GEAR_CLUTCH:
    return "N"
  if gear == GEAR_REVERSE:
    return "R"
  if 1 <= gear <= 6:
    return str(gear)
  return "-"


def _lerp_color(a: rl.Color, b: rl.Color, t: float) -> rl.Color:
  t = min(1.0, max(0.0, t))
  # round, not truncate: truncating biases every interpolated channel a step dark
  return rl.Color(round(a.r + (b.r - a.r) * t),
                  round(a.g + (b.g - a.g) * t),
                  round(a.b + (b.b - a.b) * t), 255)


def _gear_color(rpm: float, in_gear: bool) -> rl.Color:
  """The numeral escalates with the bar: white, then amber, then red."""
  if not in_gear or rpm < AMBER_RPM:
    return COL_WHITE
  if rpm < RED_RPM:
    return COL_AMBER
  return COL_RED


def _bar_color(f: float) -> rl.Color:
  """Colour of the bar at fraction f of redline."""
  f = min(1.0, max(0.0, f))
  for (f0, c0), (f1, c1) in zip(BAR_STOPS, BAR_STOPS[1:], strict=False):
    if f <= f1:
      span = f1 - f0
      return _lerp_color(c0, c1, (f - f0) / span if span > 1e-6 else 0.0)
  return BAR_STOPS[-1][1]


def _with_alpha(colour: rl.Color, alpha: float) -> rl.Color:
  return rl.Color(colour.r, colour.g, colour.b, int(255 * min(1.0, max(0.0, alpha))))


class ManualGauge:
  def __init__(self):
    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)

    self._rpm_filter = FirstOrderFilter(0.0, 0.12, 1 / gui_app.target_fps)
    self._fade_filter = FirstOrderFilter(0.0, 0.18, 1 / gui_app.target_fps)

    self._gear = GEAR_CLUTCH
    self._lugging = False

  # ---------------------------------------------------------------- state

  @property
  def _is_manual(self) -> bool:
    cp = ui_state.sm['carParams']
    return str(cp.transmissionType) == 'manual'

  def update(self) -> None:
    cs_sp = ui_state.sm['carStateSP']
    cs = ui_state.sm['carState']
    v_ego = cs.vEgo

    gear = int(cs_sp.gearMt)
    if 1 <= gear <= 6 and v_ego < MIN_TRUST_SPEED:
      # stopped: the gear estimate is stale, so say neutral rather than guess
      gear = GEAR_CLUTCH
    self._gear = gear

    # an alert owns the screen - it draws first and we would paint over it
    alert_showing = ui_state.sm['selfdriveState'].alertSize != 0
    visible = (ui_state.status == UIStatus.DISENGAGED and self._is_manual
               and not alert_showing and gear != GEAR_REVERSE)
    self._fade_filter.update(1.0 if visible else 0.0)
    if not visible:
      return

    rpm = self._rpm_filter.update(max(0.0, float(cs_sp.engineRpm)))

    self._lugging = (LUG_MIN_GEAR <= gear <= 6 and
                     rpm < LUG_RPM and
                     cs_sp.pedalGas > LUG_PEDAL and
                     v_ego > LUG_MIN_SPEED)

  # ---------------------------------------------------------------- draw

  def render(self, rect: rl.Rectangle) -> None:
    fade = self._fade_filter.x
    if fade < 0.01:
      return

    rpm = max(0.0, self._rpm_filter.x)
    in_gear = 1 <= self._gear <= 6

    # blink the whole gauge once we are past the shift point
    flash = 1.0
    if in_gear and rpm >= FLASH_RPM:
      flash = 1.0 if int(rl.get_time() * FLASH_HZ * 2) % 2 == 0 else FLASH_LOW
    alpha = FG_ALPHA * flash * fade

    bar_w = rect.width - 2 * MARGIN_X
    bar_x = rect.x + MARGIN_X
    bar_y = rect.y + rect.height - BAR_BOTTOM - BAR_H
    self._draw_bar(bar_x, bar_y, bar_w, min(1.0, rpm / REDLINE_RPM), alpha, fade)

    # gear numeral, left, big, taking the bar's colour as the revs climb
    label = _gear_label(self._gear)
    txt = measure_text_cached(self._font_bold, label, GEAR_SIZE)
    pos = rl.Vector2(rect.x + GEAR_X, bar_y - GEAR_GAP - txt.y)
    rl.draw_text_ex(self._font_bold, label, pos, GEAR_SIZE, 0,
                    _with_alpha(_gear_color(rpm, in_gear), alpha))

    if self._lugging:
      self._draw_hint(pos.x + txt.x + HINT_GAP, pos.y + txt.y / 2, fade)

  def _draw_bar(self, x: float, y: float, w: float, frac: float,
                alpha: float, fade: float) -> None:
    """One smooth pill: a faint full-length track, then a gradient fill with round caps."""
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, w, BAR_H), 1.0, 12,
                              _with_alpha(COL_WHITE, TRACK_ALPHA * fade))

    r = BAR_H / 2
    x0 = x + r                  # the caps' centres, so the pill ends land on x and x + w
    span = w - 2 * r
    tip = x0 + span * frac

    # body, one band per colour stop; positions are against the whole bar, never the fill
    for (f0, c0), (f1, c1) in zip(BAR_STOPS, BAR_STOPS[1:], strict=False):
      xa = x0 + span * f0
      if xa >= tip:
        break
      xb = x0 + span * f1
      end = c1
      if xb > tip:
        end = _lerp_color(c0, c1, (tip - xa) / (xb - xa))
        xb = tip
      rl.draw_rectangle_gradient_h(int(xa), int(y), int(xb - xa) + 1, int(BAR_H),
                                   _with_alpha(c0, alpha), _with_alpha(end, alpha))

    rl.draw_circle(int(x0), int(y + r), r, _with_alpha(COL_OK, alpha))
    rl.draw_circle(int(tip), int(y + r), r, _with_alpha(_bar_color(frac), alpha))

  def _draw_hint(self, cx: float, cy: float, fade: float) -> None:
    """Down-triangle meaning 'drop a gear'. Drawn rather than typed: the bundled font has no
    arrow glyph. Wound clockwise, which is what raylib fills."""
    h = HINT_SIZE
    colour = _with_alpha(COL_AMBER, FG_ALPHA * fade)
    rl.draw_triangle(
      rl.Vector2(cx - h / 2, cy - h / 3),
      rl.Vector2(cx, cy + h / 2),
      rl.Vector2(cx + h / 2, cy - h / 3),
      colour,
    )
