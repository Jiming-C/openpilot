#!/usr/bin/env python3
"""Regenerate the alert sounds.

Pure sine with an exponential decay, matching the envelope of the stock comma
beeps this replaces. Two hard constraints:

  * soundd asserts 48 kHz / mono / 16-bit -- it will crash otherwise.
  * Nothing below ~500 Hz. The device speaker cannot reproduce it (radiated SPL
    scales ~f^2, and a 330 Hz wavelength is ~104 cm vs a ~10 cm device, so the
    front and back waves largely cancel). Every stock sound sits above 500 Hz
    for this reason; the lowest is 660 Hz.

Pitch rises with urgency: everyday cues sit low and warm, driver-monitoring
alerts sit high where hearing is most sensitive.
"""
import wave
import numpy as np

SR = 48000
E5, FS5, G5, A5, C6, E6, E7 = 659.26, 739.99, 783.99, 880.00, 1046.50, 1318.51, 2637.02


def tone(freq, duration, decay, amp=1.0, attack=0.005, harm=0.0):
  t = np.arange(int(SR * duration)) / SR
  s = np.sin(2 * np.pi * freq * t)
  if harm:  # harmonics add bite for the alerts that must cut through road noise
    s += harm * np.sin(2 * np.pi * freq * 2 * t) + (harm * 0.5) * np.sin(2 * np.pi * freq * 3 * t)
  s *= np.exp(-decay * t)
  a = int(SR * attack)
  s[:a] *= np.linspace(0, 1, a)  # soft attack, avoids a click
  return amp * s


def build(notes, total, decay=6.85, harm=0.0, attack=0.005, duration=0.5):
  out = np.zeros(int(SR * total))
  for onset, freq, amp in notes:
    s = tone(freq, duration, decay, amp, attack, harm)
    o = int(SR * onset)
    e = min(len(out), o + len(s))
    out[o:e] += s[:e - o]
  return out


def save(name, y, peak=0.82):
  y = y / (np.max(np.abs(y)) + 1e-9) * peak
  f = int(SR * 0.005)
  y[-f:] *= np.linspace(1, 0, f)
  with wave.open(name, 'w') as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(SR)
    w.writeframes((y * 32767).astype(np.int16).tobytes())


if __name__ == "__main__":
  # engage/disengage are a mirrored pair: up = on, down = off
  save("engage.wav",             build([(0, E5, 1.0), (0.24, FS5, 1.0)], 0.80, duration=0.58))
  save("disengage.wav",          build([(0, FS5, 1.0), (0.24, E5, 1.0)], 0.80, duration=0.58))
  save("green_light.wav",        build([(0, A5, 1.0)], 0.60, duration=0.55), 0.62)
  save("refuse.wav",             build([(0, G5, 0.9), (0.15, G5, 1.0)], 0.65, duration=0.48))
  save("prompt.wav",             build([(0, A5, 0.85), (0.22, C6, 1.0)], 0.85, duration=0.55), 0.72)
  save("prompt_single_low.wav",  build([(0, E5, 1.0)], 0.45, duration=0.4), 0.70)
  save("prompt_single_high.wav", build([(0, A5, 1.0)], 0.42, duration=0.38), 0.70)

  # These loop until the driver responds. Kept high and harsh on purpose.
  save("prompt_distracted.wav",  build([(0, C6, 0.95), (0.16, C6, 1.0)], 0.80, decay=9, harm=0.12, attack=0.003))
  save("warning_soft.wav",       build([(0, E6, 0.9), (0.18, E6, 0.95), (0.36, E6, 1.0)], 1.15, decay=9, harm=0.15, attack=0.003))
  save("warning_immediate.wav",  build([(0, E7, 1.0), (0.12, E7, 1.0), (0.24, E7, 1.0)], 0.66, decay=16, harm=0.30, attack=0.001))
