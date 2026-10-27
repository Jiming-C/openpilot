"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""

from cereal import messaging, custom

from openpilot.sunnypilot.selfdrive.selfdrived.events import EventsSP

# Three marks through the amber band, then the redline tone. The first and last are deliberately
# the same numbers the rev bar changes colour on (AMBER_RPM and RED_RPM in manual_gauge.py), so
# the sound and the colour move together instead of drifting apart by a few hundred rpm.
BEEP_RPM = (4800.0, 5150.0, 5500.0)
HOLD_RPM = 5900.0

# Each mark re-arms only once the revs fall back a clear margin below it. Without the hysteresis
# a steady throttle sitting right on a threshold would chatter the beep on and off.
REARM_MARGIN = 250.0

# Only while driving the car yourself: engaged, the computer picks the moment to change speed
# and a shift cue is noise. Below this throttle you are not accelerating, so nothing is coming.
MIN_PEDAL = 0.15
MIN_GEAR, MAX_GEAR = 1, 5       # nothing to go up to out of 6th


class ShiftAlertHelper:
  # No param toggle, deliberately. This branch is PREBUILT: the list of valid param keys is
  # compiled into common/params_pyx.so, so a key added to params_keys.h does not exist on the
  # device. Reading one that is not there raises UnknownKeyName, and doing that here took
  # plannerd down at startup - which also took the green-light chime and MADS with it.
  def __init__(self):
    # which marks have already fired on this climb
    self._armed = [True] * len(BEEP_RPM)
    self._holding = False
    self._was_active = False

  def update(self, sm: messaging.SubMaster, events_sp: EventsSP) -> None:
    cs_sp = sm['carStateSP']
    rpm = max(0.0, float(cs_sp.engineRpm))
    gear = int(cs_sp.gearMt)

    # the cue only makes sense while you are the one choosing the gear, on the power, with a
    # gear above you to go to
    active = (not sm['carControl'].enabled
              and MIN_GEAR <= gear <= MAX_GEAR and cs_sp.pedalGas > MIN_PEDAL)

    if not active:
      self._armed = [True] * len(BEEP_RPM)
      self._holding = False
      self._was_active = False
      return

    if not self._was_active:
      # arriving already above a mark is not a crossing. Squeezing the throttle at 5100 rpm
      # must not beep for a line passed long before, so those marks are spent silently.
      self._armed = [rpm < mark for mark in BEEP_RPM]
      self._holding = rpm >= HOLD_RPM
    self._was_active = True

    # re-arm on the way down, so one climb gives one beep per mark
    for i, mark in enumerate(BEEP_RPM):
      if rpm < mark - REARM_MARGIN:
        self._armed[i] = True

    if rpm >= HOLD_RPM:
      # past the red line the tone sustains: there is nothing left to count up to
      self._holding = True
      events_sp.add(custom.OnroadEventSP.EventName.shiftHold)
      return

    if self._holding and rpm < HOLD_RPM - REARM_MARGIN:
      self._holding = False
    if self._holding:
      # still over the line as far as the hysteresis is concerned
      events_sp.add(custom.OnroadEventSP.EventName.shiftHold)
      return

    # highest mark first: crossing two in one frame should beep once, not twice
    for i in range(len(BEEP_RPM) - 1, -1, -1):
      if rpm >= BEEP_RPM[i] and self._armed[i]:
        # everything below is now spent too, or dropping back through them would beep again
        for j in range(i + 1):
          self._armed[j] = False
        events_sp.add(custom.OnroadEventSP.EventName.shiftBeep)
        return
