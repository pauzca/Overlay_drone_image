from dataclasses import dataclass
from pathlib import Path

@dataclass
class PhotoMeta:
    """Metadata for one close-up photo, standardized across all drone models."""
    path: Path
    camera: str          # "tele", "wide", or "generic"
    latitude: float      # WGS84 decimal degrees
    longitude: float     # WGS84 decimal degrees
    abs_alt_m: float     # Absolute Altitude in meters
    yaw: float           # Gimbal Yaw in degrees (clockwise from North)
    pitch: float         # Gimbal Pitch in degrees (~ -90 for nadir)
    roll: float          # Gimbal Roll in degrees (~ 0)
    width_px: int
    height_px: int
    fl35_mm: float | None = None          # FocalLengthIn35mmFilm
    calib_focal_px: float | None = None   # Calibrated Focal Length in pixels
    timestamp: str | None = None          # EXIF DateTimeOriginal
