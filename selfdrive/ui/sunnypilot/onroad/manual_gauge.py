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
import math

import pyray as rl

from openpilot.common.filter_simple import FirstOrderFilter
from openpilot.selfdrive.ui.ui_state import ui_state, UIStatus
from openpilot.system.ui.lib.application import gui_app, FontWeight
from openpilot.system.ui.lib.text_measure import measure_text_cached

# Civic Si (FE1): the red arc on the cluster begins just past 6500.
REDLINE_RPM = 6500.0
# Two stages of urgency. Amber starts pulsing gently to say "getting there", then red flashes
# hard and takes the whole screen with it - this panel is small and easy to miss at a glance.
# Colour is continuous, behaviour is stepped. The bar eases off green from BLEND_START_RPM so
# the build reads without thinking about it; the pulse and then the hard flash arrive as events.
BLEND_START_RPM = 3600.0        # starts easing green -> amber here, still steady
AMBER_RPM = 4800.0              # fully amber, and the pulse starts: this is the sudden one
RED_RPM = 5900.0                # fully red, hard flash, edges strobe

# Idle measured 1003 rpm in the logs, so a 900 line sits below idle and could only ever fire on
# the verge of a stall. 1200 in 3rd is ~15 mph, which is genuinely a gear too tall. Light
# throttle counts: lugging usually happens while feeding it in, not at 15% down.
LUG_RPM = 1200.0
LUG_PEDAL = 0.08
LUG_MIN_SPEED = 2.2             # m/s, ~5 mph
LUG_MIN_GEAR = 3                # below 3rd, low revs are just pulling away

# GEAR_MT is derived from engine speed against wheel speed, so with the car stopped there is
# nothing to derive it from and it holds whatever it last saw. Don't believe it below this.
MIN_TRUST_SPEED = 1.3           # m/s, ~3 mph

# Coasting in neutral, GEAR_MT keeps naming whatever gear the road speed would suit, so the
# numeral climbs through gears on its own. The tell is the engine: with the driveline
# connected, rpm tracks road speed through a fixed ratio. Running SLOWER than the named gear
# demands means it is not driving the wheels. Ratios are medians measured from logged driving,
# all within 1% of the published ones. Only the low side is tested - rev-matching runs high.
GEAR_RATIOS = {1: 215.0, 2: 122.7, 3: 80.1, 4: 60.2, 5: 48.8, 6: 40.3}   # rpm per mph
RATIO_LOW_TOL = 0.04
RATIO_MIN_MPH = 12.0

# GEAR_MT also reads 0 for the whole time the clutch is down, so every shift would flash N
# between gears. Hold the gear we were in for a moment: a shift is quicker than this, sitting
# in neutral is not, so a real neutral still falls through to N.
CLUTCH_HOLD = 1.2               # seconds

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

AMBER_PULSE_HZ = 3.0
AMBER_PULSE_LOW = 0.5           # a swell, not a blink - it runs for a long stretch of revs
RED_FLASH_HZ = 6.0
RED_FLASH_LOW = 0.15            # dim end of the blink, not fully off
SCRIM_ALPHA = 0.65              # black over the camera feed, see SCRIM note in render

EDGE_GLOW = 52                  # how far the glow reaches in from each edge
EDGE_RED_ALPHA = 0.65
EDGE_AMBER_LOW = 0.15           # the amber glow breathes between these
EDGE_AMBER_HIGH = 0.45
HINT_BLINK_HZ = 2.0             # the downshift arrow, which has to carry itself
HINT_BLINK_LOW = 0.2


TEMP_SIZE = 48
TEMP_LABEL_SIZE = 18
TEMP_GAP = 2

# Front wheelspin. The Si is FWD, so the undriven rear pair is a true ground-speed reference.
# Cornering has to be taken out first: corr(steeringAngleDeg, FL-FR) measured -0.976 in logs,
# i.e. almost all of the raw left-right difference is steering geometry, not slip. Positive
# steeringAngleDeg is a LEFT turn, so the right wheels run outside and faster.
TRACK_M = 1.54                  # FE1 front track; CarParams carries no track width
SLIP_QUIET_KPH = 0.3            # measured straight-line noise floor: nothing to report below it
SLIP_AMBER_KPH = 1.0            # fully amber
SLIP_RED_KPH = 2.5              # fully red, and the wheel starts blinking
SLIP_BLINK_HZ = 2.0             # once a wheel is fully red it is spinning, which is urgent
SLIP_BLINK_LOW = 0.2
SLIP_MIN_SPEED = 3.0            # m/s, below this the measurement means nothing
BIAS_RC = 20.0                  # s, learns out per-wheel tyre wear / pressure offsets
BIAS_MIN_SPEED = 8.0            # m/s
BIAS_MAX_STEER = 5.0            # deg
BIAS_MAX_PEDAL = 0.05

# The two front wheels sit tight together in the top right, right aligned to the margin. The rears are the reference, so they have no slip of their own to
# show - RL vs RR imbalance would be measurable, but it is not worth two more blocks here.
WHEEL_W = 34
WHEEL_H = 46
WHEEL_GAP = 12
WHEEL_Y = 56

COL_OK = rl.Color(74, 222, 128, 255)
COL_AMBER = rl.Color(251, 191, 36, 255)
COL_RED = rl.Color(239, 68, 68, 255)
COL_WHITE = rl.Color(255, 255, 255, 255)
COL_BLACK = rl.Color(0, 0, 0, 255)

FG_ALPHA = 0.95
TRACK_ALPHA = 0.15



def _ratio_ok(gear: int, rpm: float, v_ego: float) -> bool:
  """Does engine speed agree that this gear is driving the wheels? Only the low side is
  tested: coasting in neutral drops the engine to idle while the road speed stays up, whereas
  rev-matching runs high and is still a real gear."""
  mph = v_ego * 2.23694
  if mph <= RATIO_MIN_MPH:
    return True
  return rpm >= GEAR_RATIOS[gear] * mph * (1.0 - RATIO_LOW_TOL)


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


def _zone_color(rpm: float) -> rl.Color:
  """One colour for the whole bar, eased across the rev range rather than snapped between
  three. It lands exactly on amber where the pulse starts, and on red where the flash does."""
  if rpm <= BLEND_START_RPM:
    return COL_OK
  if rpm < AMBER_RPM:
    return _lerp_color(COL_OK, COL_AMBER, (rpm - BLEND_START_RPM) / (AMBER_RPM - BLEND_START_RPM))
  if rpm < RED_RPM:
    return _lerp_color(COL_AMBER, COL_RED, (rpm - AMBER_RPM) / (RED_RPM - AMBER_RPM))
  return COL_RED


def _slip_color(mag: float) -> rl.Color:
  """Green through the noise floor, then eased to amber and on to red - same treatment as the
  rev bar, so the panel reads one way."""
  if mag <= SLIP_QUIET_KPH:
    return COL_OK
  if mag < SLIP_AMBER_KPH:
    return _lerp_color(COL_OK, COL_AMBER,
                       (mag - SLIP_QUIET_KPH) / (SLIP_AMBER_KPH - SLIP_QUIET_KPH))
  if mag < SLIP_RED_KPH:
    return _lerp_color(COL_AMBER, COL_RED,
                       (mag - SLIP_AMBER_KPH) / (SLIP_RED_KPH - SLIP_AMBER_KPH))
  return COL_RED



def _gear_color(rpm: float, in_gear: bool) -> rl.Color:
  """White until the pulse starts, then it takes the bar's colour. The bar carries the smooth
  build; the numeral marks the moment it starts asking for a shift."""
  if not in_gear or rpm < AMBER_RPM:
    return COL_WHITE
  return _zone_color(rpm)


def _with_alpha(colour: rl.Color, alpha: float) -> rl.Color:
  return rl.Color(colour.r, colour.g, colour.b, int(255 * min(1.0, max(0.0, alpha))))


class ManualGauge:
  def __init__(self):
    self._font_bold: rl.Font = gui_app.font(FontWeight.BOLD)
    self._font_semi: rl.Font = gui_app.font(FontWeight.SEMI_BOLD)

    dt = 1 / gui_app.target_fps
    self._bias_fl = FirstOrderFilter(0.0, BIAS_RC, dt)
    self._bias_fr = FirstOrderFilter(0.0, BIAS_RC, dt)
    # 0.12 put a visible ~0.12s lag on the bar; 0.05 still smooths sensor jitter
    self._rpm_filter = FirstOrderFilter(0.0, 0.05, 1 / gui_app.target_fps)
    self._fade_filter = FirstOrderFilter(0.0, 0.18, 1 / gui_app.target_fps)

    self._gear = GEAR_CLUTCH
    self._held_gear = 0         # last gear we actually believed, for the clutch hold
    self._clutch_since = 0.0
    self._slip_fl: float | None = None
    self._slip_fr: float | None = None
    self._lugging = False
    self._upshift = False

  # ---------------------------------------------------------------- state

  @property
  def _is_manual(self) -> bool:
    cp = ui_state.sm['carParams']
    return str(cp.transmissionType) == 'manual'

  def update(self) -> None:
    cs_sp = ui_state.sm['carStateSP']
    cs = ui_state.sm['carState']
    v_ego = cs.vEgo

    raw = int(cs_sp.gearMt)
    rpm_now = max(0.0, float(cs_sp.engineRpm))
    now = rl.get_time()
    if 1 <= raw <= 6 and v_ego >= MIN_TRUST_SPEED and _ratio_ok(raw, rpm_now, v_ego):
      # a gear we can believe: moving, the box reports one, and the engine agrees
      self._held_gear, self._clutch_since = raw, 0.0
      gear = raw
    elif raw == GEAR_REVERSE:
      self._held_gear, self._clutch_since = 0, 0.0
      gear = GEAR_REVERSE
    else:
      # clutch down, mid-shift, truly neutral, or stopped - where GEAR_MT holds a stale gear
      if self._clutch_since == 0.0:
        self._clutch_since = now
      holding = (self._held_gear != 0 and v_ego >= MIN_TRUST_SPEED
                 and now - self._clutch_since < CLUTCH_HOLD)
      if holding:
        gear = self._held_gear
      else:
        self._held_gear = 0
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

    self._update_slip(cs, cs_sp)

    self._lugging = (LUG_MIN_GEAR <= gear <= 6 and
                     rpm < LUG_RPM and
                     cs_sp.pedalGas > LUG_PEDAL and
                     v_ego > LUG_MIN_SPEED)
    # from the amber stage on; nothing to go up to out of 6th
    self._upshift = 1 <= gear < 6 and rpm >= AMBER_RPM

  def _update_slip(self, cs, cs_sp) -> None:
    """Front wheelspin against the undriven rears, with the cornering geometry removed."""
    ws = cs.wheelSpeeds
    ground = (ws.rl + ws.rr) / 2.0
    if ground < SLIP_MIN_SPEED:
      self._slip_fl = self._slip_fr = None
      return

    cp = ui_state.CP
    wheelbase = cp.wheelbase if cp and cp.wheelbase > 0.1 else 2.7
    steer_ratio = cp.steerRatio if cp and cp.steerRatio > 1.0 else 15.38

    delta = math.radians(cs.steeringAngleDeg / steer_ratio)
    yaw = ground * math.tan(delta) / wheelbase          # rad/s
    front = ground / max(0.5, math.cos(delta))          # front axle centre runs wider
    exp_fl = front - yaw * TRACK_M / 2.0
    exp_fr = front + yaw * TRACK_M / 2.0

    raw_fl = (ws.fl - exp_fl) * 3.6
    raw_fr = (ws.fr - exp_fr) * 3.6

    # learn each wheel's standing offset while straight, steady and off the throttle
    if (ground > BIAS_MIN_SPEED and abs(cs.steeringAngleDeg) < BIAS_MAX_STEER
            and cs_sp.pedalGas < BIAS_MAX_PEDAL):
      self._bias_fl.update(raw_fl)
      self._bias_fr.update(raw_fr)

    self._slip_fl = raw_fl - self._bias_fl.x
    self._slip_fr = raw_fr - self._bias_fr.x

  # ---------------------------------------------------------------- draw

  def render(self, rect: rl.Rectangle) -> None:
    fade = self._fade_filter.x
    if fade < 0.01:
      return

    # SCRIM: this only draws while openpilot is disengaged, and then you are driving the car
    # yourself - the camera view is background, not something you are reading. Knocking it back
    # buys a lot of contrast on a small, bright screen. Everything below lands on top of it.
    # NOTE: the blind-spot indicators have no engagement gate, so HudRendererSP renders them
    # AFTER this widget to keep them out from under the scrim.
    rl.draw_rectangle_rec(rect, _with_alpha(COL_BLACK, SCRIM_ALPHA * fade))

    rpm = max(0.0, self._rpm_filter.x)
    in_gear = 1 <= self._gear <= 6

    # escalate: steady, then an amber swell, then a hard red blink - and from amber on, the
    # screen edges glow in step. A border catches the corner of your eye while leaving the road
    # visible, which a full wash does not.
    flash, edge, edge_a = 1.0, None, 0.0
    if in_gear and rpm >= RED_RPM:
      lit = int(rl.get_time() * RED_FLASH_HZ * 2) % 2 == 0
      flash = 1.0 if lit else RED_FLASH_LOW
      edge, edge_a = _zone_color(rpm), (EDGE_RED_ALPHA if lit else 0.0)
    elif in_gear and rpm >= AMBER_RPM:
      swell = 0.5 + 0.5 * math.sin(rl.get_time() * 2 * math.pi * AMBER_PULSE_HZ)
      flash = AMBER_PULSE_LOW + (1.0 - AMBER_PULSE_LOW) * swell
      edge = _zone_color(rpm)
      edge_a = EDGE_AMBER_LOW + (EDGE_AMBER_HIGH - EDGE_AMBER_LOW) * swell
    alpha = FG_ALPHA * flash * fade

    # behind everything, so the gauge still reads against it
    if edge is not None and edge_a > 0.0:
      self._draw_edge_glow(rect, edge, edge_a * fade)

    bar_w = rect.width - 2 * MARGIN_X
    bar_x = rect.x + MARGIN_X
    bar_y = rect.y + rect.height - BAR_BOTTOM - BAR_H
    self._draw_bar(bar_x, bar_y, bar_w, min(1.0, rpm / REDLINE_RPM),
                   _zone_color(rpm), alpha, fade)

    # gear numeral, left, big, taking the bar's colour as the revs climb
    label = _gear_label(self._gear)
    txt = measure_text_cached(self._font_bold, label, GEAR_SIZE)
    pos = rl.Vector2(rect.x + GEAR_X, bar_y - GEAR_GAP - txt.y)
    rl.draw_text_ex(self._font_bold, label, pos, GEAR_SIZE, 0,
                    _with_alpha(_gear_color(rpm, in_gear), alpha))

    self._draw_traction(rect, fade)

    hx, hy = pos.x + txt.x + HINT_GAP, pos.y + txt.y / 2
    if self._upshift:
      # takes the bar's colour and its swell/blink, since it says the same thing
      self._draw_arrow(hx, hy, True, _zone_color(rpm), alpha)
    elif self._lugging:
      # down here the bar is green and steady, so the arrow is the only thing that can move
      lit = int(rl.get_time() * HINT_BLINK_HZ * 2) % 2 == 0
      self._draw_arrow(hx, hy, False, COL_AMBER,
                       FG_ALPHA * (1.0 if lit else HINT_BLINK_LOW) * fade)

  def _draw_bar(self, x: float, y: float, w: float, frac: float,
                colour: rl.Color, alpha: float, fade: float) -> None:
    """A faint full-length track with one solid pill over it, the whole fill in one colour."""
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, w, BAR_H), 1.0, 12,
                              _with_alpha(COL_WHITE, TRACK_ALPHA * fade))
    if frac <= 0.0:
      return
    # never shorter than its own end caps, or the pill degenerates
    fill_w = max(BAR_H, w * frac)
    rl.draw_rectangle_rounded(rl.Rectangle(x, y, fill_w, BAR_H), 1.0, 12,
                              _with_alpha(colour, alpha))

  def _draw_traction(self, rect: rl.Rectangle, fade: float) -> None:
    """The two front wheels, top right against the margin.
    Green while they track the undriven rears, amber or red as they break away, grey when we
    are too slow for the measurement to mean anything."""
    right = rect.x + rect.width - MARGIN_X
    x0 = right - (2 * WHEEL_W + WHEEL_GAP)
    for i, slip in enumerate((self._slip_fl, self._slip_fr)):
      if slip is None:
        colour, a = COL_WHITE, TRACK_ALPHA * fade
      else:
        mag = abs(slip)
        colour, a = _slip_color(mag), FG_ALPHA * fade
        if mag >= SLIP_RED_KPH:
          # blink the wheel that is actually spinning, not the pair
          lit = int(rl.get_time() * SLIP_BLINK_HZ * 2) % 2 == 0
          a *= 1.0 if lit else SLIP_BLINK_LOW
      box = rl.Rectangle(x0 + i * (WHEEL_W + WHEEL_GAP), rect.y + WHEEL_Y, WHEEL_W, WHEEL_H)
      rl.draw_rectangle_rounded(box, 0.35, 6, _with_alpha(colour, a))

    label = "TRACTION"
    tl = measure_text_cached(self._font_semi, label, TEMP_LABEL_SIZE)
    rl.draw_text_ex(self._font_semi, label,
                    rl.Vector2(right - tl.x, rect.y + WHEEL_Y + WHEEL_H + TEMP_GAP),
                    TEMP_LABEL_SIZE, 0, _with_alpha(COL_WHITE, FG_ALPHA * 0.7 * fade))

  def _draw_edge_glow(self, rect: rl.Rectangle, colour: rl.Color, alpha: float) -> None:
    """Glow in from all four edges rather than washing the whole view. Corners get both passes,
    which reads as a vignette."""
    c = _with_alpha(colour, alpha)
    x, y = int(rect.x), int(rect.y)
    w, h = int(rect.width), int(rect.height)
    d = EDGE_GLOW
    rl.draw_rectangle_gradient_v(x, y, w, d, c, rl.BLANK)
    rl.draw_rectangle_gradient_v(x, y + h - d, w, d, rl.BLANK, c)
    rl.draw_rectangle_gradient_h(x, y, d, h, c, rl.BLANK)
    rl.draw_rectangle_gradient_h(x + w - d, y, d, h, rl.BLANK, c)

  def _draw_arrow(self, cx: float, cy: float, up: bool,
                  colour: rl.Color, alpha: float) -> None:
    """Shift arrow, drawn rather than typed: the bundled font has no arrow glyph.

    raylib backface-culls draw_triangle, so these have to wind the same way as the triangles
    known to render (star_icon.py): positive signed area in screen coords, which are y-down.
    Wound the other way nothing is drawn at all, silently - which is what happened here.
    """
    h = HINT_SIZE
    c = _with_alpha(colour, alpha)
    if up:
      rl.draw_triangle(rl.Vector2(cx, cy - h / 2),
                       rl.Vector2(cx + h / 2, cy + h / 3),
                       rl.Vector2(cx - h / 2, cy + h / 3), c)
    else:
      rl.draw_triangle(rl.Vector2(cx - h / 2, cy - h / 3),
                       rl.Vector2(cx + h / 2, cy - h / 3),
                       rl.Vector2(cx, cy + h / 2), c)
