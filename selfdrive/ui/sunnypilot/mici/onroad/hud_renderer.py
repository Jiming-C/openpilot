"""
Copyright (c) 2021-, Haibin Wen, sunnypilot, and a number of other contributors.

This file is part of sunnypilot and is licensed under the MIT License.
See the LICENSE.md file in the root directory for more details.
"""
import pyray as rl

from openpilot.selfdrive.ui.mici.onroad.hud_renderer import HudRenderer
from openpilot.selfdrive.ui.sunnypilot.onroad.blind_spot_indicators import BlindSpotIndicators
from openpilot.selfdrive.ui.sunnypilot.onroad.manual_gauge import ManualGauge


class HudRendererSP(HudRenderer):
  def __init__(self):
    super().__init__()
    self.blind_spot_indicators = BlindSpotIndicators()
    self.manual_gauge = ManualGauge()

  def _update_state(self) -> None:
    super()._update_state()
    self.blind_spot_indicators.update()
    self.manual_gauge.update()

  def _render(self, rect: rl.Rectangle) -> None:
    super()._render(rect)
    # the gauge lays a scrim over the camera feed, and the blind-spot indicators have no
    # engagement gate, so they go on top of it rather than under it
    self.manual_gauge.render(rect)
    self.blind_spot_indicators.render(rect)

  def _has_blind_spot_detected(self) -> bool:

    return self.blind_spot_indicators.detected
