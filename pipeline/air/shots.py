"""Shot characteristics taken from gimbal pitch.

Bands match ``droneAngleOf`` in src/logic.ts. Pitch is degrees, and DJI's
straight-down value of -90 is treated the same as 90 degrees down.
"""

from __future__ import annotations

NADIR_MAX_OFF_DEG = 5.0
HIGH_OBLIQUE_MIN_DOWN_DEG = 60.0
MEDIUM_OBLIQUE_MIN_DOWN_DEG = 30.0
LOW_OBLIQUE_MIN_DOWN_DEG = 10.0
ALT_LOW_MAX_M = 30.0
ALT_HIGH_MIN_M = 90.0

SHOT_TAGS = ("nadir", "high_oblique", "medium_oblique", "low_oblique", "horizon")


def drone_angle(pitch: float | None) -> str | None:
    if pitch is None:
        return None
    try:
        value = float(pitch)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    down = abs(value)
    if down >= 90.0 - NADIR_MAX_OFF_DEG:
        return "nadir"
    if down >= HIGH_OBLIQUE_MIN_DOWN_DEG:
        return "high_oblique"
    if down >= MEDIUM_OBLIQUE_MIN_DOWN_DEG:
        return "medium_oblique"
    if down >= LOW_OBLIQUE_MIN_DOWN_DEG:
        return "low_oblique"
    return "horizon"


def altitude_band(meters: float | None) -> str | None:
    if meters is None:
        return None
    try:
        value = float(meters)
    except (TypeError, ValueError):
        return None
    if value != value:
        return None
    if value <= ALT_LOW_MAX_M:
        return "low"
    if value >= ALT_HIGH_MIN_M:
        return "high"
    return "medium"
