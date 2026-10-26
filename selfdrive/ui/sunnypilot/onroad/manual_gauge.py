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

# Lugging is rpm too low for the gear you are in, so the line has to move with the gear: 900 rpm
# is 7 mph in 2nd, which is just pulling away, but 22 mph in 6th, which is already well into it.
LUG_FLOOR = {2: 900.0, 3: 1000.0, 4: 1100.0, 5: 1150.0, 6: 1200.0}   # rpm, by gear
# Light throttle is the whole point. Measured across 12.8 min of logged driving, the most
# throttle present at ANY low-rpm-in-gear moment was 0.024 - feeding it in gently at low revs is
# what lugging IS, so the old 0.08 gate could not fire once in that entire drive. This only has
# to clear pure coasting, where pedalGas sits at 0.000.
LUG_PEDAL = 0.02
LUG_MIN_SPEED = 2.2             # m/s, ~5 mph

# The bar already shouts from AMBER_RPM up, so the arrow only adds something when you are
# actually asking for power. Without this it fired for 3.1 s while coasting down from 66 mph
# off the throttle, which is the opposite of advice.
UPSHIFT_MIN_PEDAL = 0.15

# GEAR_MT is derived from engine speed against wheel speed, so with the car stopped there is
# nothing to derive it from and it holds whatever it last saw. Don't believe it below this.
MIN_TRUST_SPEED = 1.3           # m/s, ~3 mph

# Coasting in neutral, GEAR_MT keeps naming whatever gear the road speed would suit, so the
# numeral climbs through gears on its own. The tell is the engine: with the driveline
# connected, rpm tracks road speed through a fixed ratio. Running SLOWER than the named gear
# demands means it is not driving the wheels. Ratios are medians measured from logged driving,
# all within 1% of the published ones. Only the low side is tested - rev-matching runs high.
GEAR_RATIOS = {1: 215.0, 2: 122.7, 3: 80.1, 4: 60.2, 5: 48.8, 6: 40.3}   # rpm per mph
# 3% rather than 4%: validated on 77k samples of logged driving, it rejects 32% of
# clearly-disconnected frames instead of 27% for the same 0.16% false-neutral rate.
# Tighter than this (2%) nearly doubles false neutrals for little extra catch. The rest
# are genuinely ambiguous - 6th at 23 mph and idle are the same engine speed.
RATIO_LOW_TOL = 0.03
RATIO_MIN_MPH = 12.0

# The ratio check cannot catch the worst case, because GEAR_MT picks whichever gear fits: at
# 21 mph idle is almost exactly what 6th demands, so it sits on "6" for as long as you coast
# there - 23s of one 11 min drive. And with the clutch in it has nothing to derive from at all,
# so as the engine decays to idle it sweeps 4th, 5th, 6th in turn. That sweep is the numeral
# climbing on its own.
#
# Requiring a gear to arrive out of a clutch press does NOT fix this. Measured over 10.9 min it
# removed 0.0s of the 33.6s where a gear was shown with the engine plainly at idle, because the
# phantom gears arrive through the clutch too. An rpm floor removes 82% of it.
#
# On the throttle you must be in gear whatever the revs, so the floor only applies off-throttle,
# and only once moving fast enough that idle cannot be driving anything.
IDLE_FLOOR = 1000.0             # rpm
IDLE_FLOOR_MIN_MPH = 12.0
IDLE_FLOOR_MAX_PEDAL = 0.02

# GEAR_MT also reads 0 for the whole time the clutch is down, so every shift would flash N
# between gears. Hold the gear we were in for a moment: a shift is quicker than this, sitting
# in neutral is not, so a real neutral still falls through to N.
CLUTCH_HOLD = 2.0               # seconds. Measured over 93 clutch events: the clutch is down
                                # 1.29s on an upshift and 1.44s on a downshift, so the old 1.2
                                # expired mid-shift on more than half of them and flashed N.

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
SCRIM_ALPHA = 1.0               # black over the camera feed, see SCRIM note in render

EDGE_GLOW = 52                  # how far the glow reaches in from each edge
EDGE_RED_ALPHA = 0.65
EDGE_AMBER_LOW = 0.15           # the amber glow breathes between these
EDGE_AMBER_HIGH = 0.45
HINT_BLINK_HZ = 2.0             # the downshift arrow, which has to carry itself
HINT_BLINK_LOW = 0.2

# One-shot cluster sweep the first time the gauge comes up: the bar runs to redline and back,
# each half carrying one of the arrows. It reads like an ignition self-test because it is one -
# it runs the same bar, numeral and arrow code at the same coordinates as the live gauge, so a
# glance at startup confirms all of it is really being drawn. Once per boot, not per disengage.
SWEEP_UP = 0.6                  # seconds, bar 0 -> redline
SWEEP_DOWN = 0.6                # and back down
SWEEP_TOTAL = SWEEP_UP + SWEEP_DOWN


# The standing state of sunnypilot's green-light / lead-departure watch, as one dot under the
# wheels. It does NOT read the traffic light: nothing on the car or in the driving model reports
# a lamp colour. It mirrors what sunnypilot/selfdrive/controls/lib/e2e_alerts_helper.py is doing,
# which is watching the road ahead - red while stopped and still blocked, amber the moment the
# way opens, green once the chime has fired. At a light those line up with the light, but what
# is being measured is the path, not the lamp. Thresholds below are that file's, kept in step.
SIGNAL_OFF, SIGNAL_WAIT, SIGNAL_SOON, SIGNAL_GO = 0, 1, 2, 3
GREEN_LIGHT_X = 30.0            # metres of clear path the model has to predict
E2E_SETTLE = 2.0                # seconds stopped before the watch arms
LEAD_NEAR_M = 8.0               # a lead this close is the thing being waited on
LEAD_DEPART_M = 1.0             # and this is how far it has to pull away
SIGNAL_HOLD = 3.0               # green stays up this long after the chime, as the alert does

LIGHT_R = 21
LIGHT_Y = 160                   # centre, down from the top of the rect
LIGHT_DIM = 0.18                # grey, when there is nothing to watch

LABEL_SIZE = 18                 # the TRACTION caption
LABEL_GAP = 12                  # clears a wheel turned to full lock, which reaches ~7px lower

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

# Just the two front wheels, top right against the margin. The rears are the reference these are
# measured against, so they have nothing of their own to report and are not drawn at all.
# Sized for the worst case: at full lock a wheel's corners swing out to about 36px either side
# of its centre, which still lands inside the 476 wide rect and above the label.
WHEEL_W = 44
WHEEL_H = 64
WHEEL_GAP = 22
WHEEL_Y = 40
WHEEL_R = 10                    # corner radius
WHEEL_SEGS = 3                  # segments per corner arc; 3 is already smooth at this size

# The watch dot is only ever lit standing still, so while moving it has nothing to say and the
# wheels take its space. One smoothed value drives the whole swap - size, gap, position and the
# dot's own fade - so it cannot pop part way through, the same trick the screen fade uses.
WHEEL_W_BIG = 58
WHEEL_H_BIG = 94
WHEEL_GAP_BIG = 28
WHEEL_Y_BIG = 44
GROW_RC = 0.22                  # seconds
GROW_INSET = 10                 # pull the pair in as it grows, so the big size still fits

# The arrows ease in rather than snapping on, and drift the way they are pointing.
ARROW_RC = 0.10
ARROW_DRIFT = 3.5               # px of travel
ARROW_DRIFT_HZ = 1.4

# A change of gear kicks the numeral and settles, so a shift registers even in peripheral vision.
GEAR_POP = 0.10                 # fraction of GEAR_SIZE
GEAR_POP_RC = 0.07


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


def _engine_driving(rpm: float, v_ego: float, pedal: float) -> bool:
  """Is the engine connected to the wheels at all? Sitting at idle while the car is moving at
  speed it is not, whatever gear GEAR_MT happens to name."""
  if pedal > IDLE_FLOOR_MAX_PEDAL:
    return True
  if v_ego * 2.23694 <= IDLE_FLOOR_MIN_MPH:
    return True
  return rpm >= IDLE_FLOOR


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


def _fill_poly(points, colour: rl.Color, alpha: float) -> None:
  """A filled convex polygon, as a triangle fan. Same winding rule as the arrows: raylib culls
  the wrong way round silently, and front-facing is negative signed area in y-down screen
  coords - which is what the mici lead chevron, drawn the same way, measures. Rather than
  trust the caller to build the loop the right way round, measure it and flip if needed."""
  p = list(points)
  n = len(p)
  area = sum(p[i][0] * p[(i + 1) % n][1] - p[(i + 1) % n][0] * p[i][1] for i in range(n))
  if area > 0:
    p.reverse()
  rl.draw_triangle_fan(p, len(p), _with_alpha(colour, alpha))


def _wheel_poly(cx: float, cy: float, w: float, h: float) -> list[tuple[float, float]]:
  """A rounded rectangle the size of a wheel. Built as a loop of points rather than drawn as a
  rounded rect so it can be any size the animation asks for and still fill as one polygon."""
  hw, hh = w / 2.0, h / 2.0
  r = min(WHEEL_R, hw, hh)
  pts = []
  # the four corner centres, each sweeping a quarter turn: top-right, then round
  for sx, sy, a0 in ((1, -1, -math.pi / 2), (1, 1, 0.0), (-1, 1, math.pi / 2), (-1, -1, math.pi)):
    ox, oy = sx * (hw - r), sy * (hh - r)
    for k in range(WHEEL_SEGS + 1):
      a = a0 + (math.pi / 2) * k / WHEEL_SEGS
      pts.append((cx + ox + r * math.cos(a), cy + oy + r * math.sin(a)))
  return pts


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
    self._grow_filter = FirstOrderFilter(1.0, GROW_RC, dt)
    self._arrow_up = FirstOrderFilter(0.0, ARROW_RC, dt)
    self._arrow_dn = FirstOrderFilter(0.0, ARROW_RC, dt)
    self._pop = FirstOrderFilter(0.0, GEAR_POP_RC, dt)
    self._pop_gear = None

    self._gear = GEAR_CLUTCH
    self._held_gear = 0         # last gear we actually believed, for the clutch hold
    self._clutch_since = 0.0
    self._slip_fl: float | None = None
    self._slip_fr: float | None = None
    self._lugging = False
    self._upshift = False
    self._signal = SIGNAL_OFF
    self._last_moving = 0.0
    self._fired_at = -SIGNAL_HOLD
    self._lead_min = -1.0
    self._sweep_start: float | None = None
    self._sweep_done = False

  # ---------------------------------------------------------------- state

  @property
  def covers_road(self) -> bool:
    """True when the scrim is fully opaque across the whole rect, so nothing drawn underneath
    could be seen anyway. AugmentedRoadView uses this to skip the camera and the model render:
    the pixels come out identical, the work does not happen. Deliberately tied to SCRIM_ALPHA
    so that lowering the scrim turns the optimisation off by itself rather than leaving a
    black screen where the road used to be."""
    return SCRIM_ALPHA >= 1.0 and self._fade_filter.x > 0.99

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
    driving = _engine_driving(rpm_now, v_ego, cs_sp.pedalGas)
    if (1 <= raw <= 6 and v_ego >= MIN_TRUST_SPEED and driving
            and _ratio_ok(raw, rpm_now, v_ego)):
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
      # hold through a shift, but let go the moment the engine drops to idle at speed: during
      # a real shift the revs stay well up (1300-1800 at 27 mph), while coasting in neutral
      # they settle at idle and stay there. That is what tells the two apart.
      holding = (self._held_gear != 0 and v_ego >= MIN_TRUST_SPEED and driving
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
    if visible and not self._sweep_done and self._sweep_start is None:
      self._sweep_start = now
    if not visible:
      return

    rpm = self._rpm_filter.update(max(0.0, float(cs_sp.engineRpm)))

    self._update_slip(cs, cs_sp)

    # rpm under this gear's floor, with some throttle in it, moving, and not braking - slowing
    # to a stop in gear drops below the floor every time and is not lugging.
    floor = LUG_FLOOR.get(gear)
    self._lugging = (floor is not None and
                     rpm < floor and
                     cs_sp.pedalGas > LUG_PEDAL and
                     v_ego > LUG_MIN_SPEED and
                     not cs.brakePressed)
    # from the amber stage on and only under power; nothing to go up to out of 6th
    self._upshift = (1 <= gear < 6 and rpm >= AMBER_RPM
                     and cs_sp.pedalGas > UPSHIFT_MIN_PEDAL)

    self._update_signal(cs, now)

    # the wheels own the dot's space whenever the dot has nothing to show
    self._grow_filter.update(0.0 if self._signal != SIGNAL_OFF else 1.0)
    self._arrow_up.update(1.0 if self._upshift else 0.0)
    self._arrow_dn.update(1.0 if self._lugging else 0.0)
    if gear != self._pop_gear:
      self._pop_gear, self._pop.x = gear, 1.0
    self._pop.update(0.0)

  def _update_signal(self, cs, now: float) -> None:
    """Which colour the watch dot is showing. See the SIGNAL_ notes above: this follows the
    road ahead, not a traffic light, and the fired state comes from the planner rather than
    being recomputed here, so the dot and the chime can never disagree."""
    sm = ui_state.sm
    alerts = sm['longitudinalPlanSP'].e2eAlerts
    if alerts.greenLightAlert or alerts.leadDepartAlert:
      self._fired_at = now

    moving = not cs.standstill and cs.vEgo > 0.1
    if moving:
      self._last_moving = now

    # this widget only draws while disengaged, so the helper's `not CC.enabled` already holds
    armed = not moving and not cs.gasPressed and now - self._last_moving > E2E_SETTLE
    if now - self._fired_at < SIGNAL_HOLD:
      self._signal = SIGNAL_GO
      return
    if not armed:
      self._lead_min = -1.0
      self._signal = SIGNAL_OFF
      return

    lead = sm['radarState'].leadOne
    if lead.status and lead.dRel < LEAD_NEAR_M:
      # waiting on the car in front: remember how close it got, then watch it pull away
      self._lead_min = lead.dRel if self._lead_min < 0 else min(self._lead_min, lead.dRel)
      opening = lead.dRel - self._lead_min > LEAD_DEPART_M
    else:
      # nothing in front: the model's own horizon is what says the way is clear
      self._lead_min = -1.0
      x = sm['modelV2'].position.x
      opening = len(x) > 0 and x[len(x) - 1] > GREEN_LIGHT_X

    self._signal = SIGNAL_SOON if opening else SIGNAL_WAIT

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
    # yourself by eye, so the camera view is not something you are reading: it goes fully black
    # and the gauge gets the whole screen's contrast. Everything below lands on top of it.
    # This saves no work. camerad and modeld run regardless, and the road view underneath is
    # still drawn, so an opaque scrim is one fullscreen fill - the same cost as a translucent
    # one, not a saving. Not drawing the road at all would be a change in augmented_road_view.
    # NOTE: the blind-spot indicators have no engagement gate, so HudRendererSP renders them
    # AFTER this widget to keep them out from under the scrim.
    rl.draw_rectangle_rec(rect, _with_alpha(COL_BLACK, SCRIM_ALPHA * fade))

    sweep = self._sweep_phase()
    if sweep is not None:
      self._render_sweep(rect, sweep, fade)
      return

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
    size = GEAR_SIZE * (1.0 + GEAR_POP * self._pop.x)
    txt = measure_text_cached(self._font_bold, label, size)
    pos = rl.Vector2(rect.x + GEAR_X, bar_y - GEAR_GAP - txt.y)
    rl.draw_text_ex(self._font_bold, label, pos, size, 0,
                    _with_alpha(_gear_color(rpm, in_gear), alpha))

    self._draw_traction(rect, fade)
    self._draw_signal(rect, fade)

    hx, hy = pos.x + txt.x + HINT_GAP, pos.y + txt.y / 2
    drift = ARROW_DRIFT * (0.5 + 0.5 * math.sin(rl.get_time() * 2 * math.pi * ARROW_DRIFT_HZ))
    if self._arrow_up.x > 0.004:
      # takes the bar's colour and its swell/blink, since it says the same thing
      self._draw_arrow(hx, hy, True, _zone_color(rpm), alpha * self._arrow_up.x,
                       self._arrow_up.x, drift)
    elif self._arrow_dn.x > 0.004:
      # down here the bar is green and steady, so the arrow is the only thing that can move
      lit = int(rl.get_time() * HINT_BLINK_HZ * 2) % 2 == 0
      self._draw_arrow(hx, hy, False, COL_AMBER,
                       FG_ALPHA * (1.0 if lit else HINT_BLINK_LOW) * fade * self._arrow_dn.x,
                       self._arrow_dn.x, drift)

  def _sweep_phase(self) -> float | None:
    """Seconds into the one-shot startup sweep, or None once it has run."""
    if self._sweep_done or self._sweep_start is None:
      return None
    t = rl.get_time() - self._sweep_start
    if t >= SWEEP_TOTAL:
      self._sweep_done = True
      return None
    return t

  def _render_sweep(self, rect: rl.Rectangle, t: float, fade: float) -> None:
    """The startup sweep: bar up to redline carrying the up arrow, back down carrying the down
    arrow. Nothing here reads the car - it runs the live draw calls at the live coordinates so
    that one glance at startup proves the bar, the numeral and both triangles are drawn."""
    if t < SWEEP_UP:
      frac, up = t / SWEEP_UP, True
    else:
      frac, up = 1.0 - (t - SWEEP_UP) / SWEEP_DOWN, False
    frac = min(1.0, max(0.0, frac))
    colour = _zone_color(frac * REDLINE_RPM)
    alpha = FG_ALPHA * fade

    # steady, not blinking: this is a sweep, not an alarm
    if frac * REDLINE_RPM >= AMBER_RPM:
      self._draw_edge_glow(rect, colour, EDGE_AMBER_HIGH * fade)

    bar_y = rect.y + rect.height - BAR_BOTTOM - BAR_H
    self._draw_bar(rect.x + MARGIN_X, bar_y, rect.width - 2 * MARGIN_X, frac,
                   colour, alpha, fade)

    label = _gear_label(self._gear)
    txt = measure_text_cached(self._font_bold, label, GEAR_SIZE)
    pos = rl.Vector2(rect.x + GEAR_X, bar_y - GEAR_GAP - txt.y)
    rl.draw_text_ex(self._font_bold, label, pos, GEAR_SIZE, 0, _with_alpha(colour, alpha))
    self._draw_arrow(pos.x + txt.x + HINT_GAP, pos.y + txt.y / 2, up, colour, alpha)

    # The wheels, the caption and the watch dot take the sweep's colour too, so one pass
    # proves every element on this screen is really being drawn AND that each can reach every
    # colour it will ever need - green through amber to red and back. That is the whole point
    # of having a self-test: a condition firing in a log never proved a pixel appeared.
    right = rect.x + rect.width - MARGIN_X
    x0 = right - (2 * WHEEL_W + WHEEL_GAP)
    wcy = rect.y + WHEEL_Y + WHEEL_H / 2.0
    for i in range(2):
      _fill_poly(_wheel_poly(x0 + i * (WHEEL_W + WHEEL_GAP) + WHEEL_W / 2.0, wcy,
                             WHEEL_W, WHEEL_H), colour, alpha)
    cap = "TRACTION"
    cv = measure_text_cached(self._font_semi, cap, LABEL_SIZE)
    rl.draw_text_ex(self._font_semi, cap,
                    rl.Vector2(right - cv.x, rect.y + WHEEL_Y + WHEEL_H + LABEL_GAP),
                    LABEL_SIZE, 0, _with_alpha(COL_WHITE, FG_ALPHA * 0.7 * fade))
    rl.draw_circle(int(right - LIGHT_R), int(rect.y + LIGHT_Y), LIGHT_R,
                   _with_alpha(colour, alpha))

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
    """The two front wheels, top right against the margin. Green while they track the undriven
    rears, amber or red as they break away, grey when we are too slow for the measurement to
    mean anything."""
    grow = self._grow_filter.x
    w = WHEEL_W + (WHEEL_W_BIG - WHEEL_W) * grow
    h = WHEEL_H + (WHEEL_H_BIG - WHEEL_H) * grow
    gap = WHEEL_GAP + (WHEEL_GAP_BIG - WHEEL_GAP) * grow
    wy = WHEEL_Y + (WHEEL_Y_BIG - WHEEL_Y) * grow
    right = rect.x + rect.width - MARGIN_X - GROW_INSET * grow
    x0 = right - (2 * w + gap)
    cy = rect.y + wy + h / 2.0

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
      cx = x0 + i * (w + gap) + w / 2.0
      _fill_poly(_wheel_poly(cx, cy, w, h), colour, a)

    label = "TRACTION"
    tl = measure_text_cached(self._font_semi, label, LABEL_SIZE)
    rl.draw_text_ex(self._font_semi, label,
                    rl.Vector2(right - tl.x, rect.y + wy + h + LABEL_GAP),
                    LABEL_SIZE, 0, _with_alpha(COL_WHITE, FG_ALPHA * 0.7 * fade))

  def _draw_signal(self, rect: rl.Rectangle, fade: float) -> None:
    """One dot under the wheels, shrinking away as they grow into its space. Grey when there is
    nothing to watch, so it is never claiming to know something it does not."""
    colour, a = {
      SIGNAL_WAIT: (COL_RED, FG_ALPHA),
      SIGNAL_SOON: (COL_AMBER, FG_ALPHA),
      SIGNAL_GO: (COL_OK, FG_ALPHA),
    }.get(self._signal, (COL_WHITE, LIGHT_DIM))
    shrink = 1.0 - self._grow_filter.x
    a *= shrink
    if a * fade < 0.004:
      return
    r = LIGHT_R * (0.4 + 0.6 * shrink)
    cx = rect.x + rect.width - MARGIN_X - LIGHT_R
    rl.draw_circle(int(cx), int(rect.y + LIGHT_Y), r, _with_alpha(colour, a * fade))

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

  def _draw_arrow(self, cx: float, cy: float, up: bool, colour: rl.Color, alpha: float,
                  scale: float = 1.0, drift: float = 0.0) -> None:
    """Shift arrow, drawn rather than typed: the bundled font has no arrow glyph.

    raylib backface-culls draw_triangle, and wound the wrong way nothing is drawn at all,
    silently. Screen coords are y-down, so front-facing comes out NEGATIVE under the shoelace
    formula - which is what both triangles in this tree that provably render measure:
    star_icon.py's fill is -0.147 and the mici lead chevron is -1.250. Match that sign, and
    check it against those two rather than against an assumption. Getting this backwards is
    exactly why these never appeared on the car.
    """
    h = HINT_SIZE * scale
    if h < 1.0:
      return
    cy += -drift if up else drift
    c = _with_alpha(colour, alpha)
    if up:
      rl.draw_triangle(rl.Vector2(cx + h / 2, cy + h / 3),
                       rl.Vector2(cx, cy - h / 2),
                       rl.Vector2(cx - h / 2, cy + h / 3), c)
    else:
      rl.draw_triangle(rl.Vector2(cx + h / 2, cy - h / 3),
                       rl.Vector2(cx - h / 2, cy - h / 3),
                       rl.Vector2(cx, cy + h / 2), c)
