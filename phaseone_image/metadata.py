"""Read the EXIF + XMP metadata needed for Phase One planar projection.

Copied from closeup_ortho.metadata and adapted for standalone use inside the
phaseone_image QGIS plugin package.
Only the Phase One-specific functions are kept; DJI functions are retained for
reference but Phase One path (read_photo_meta_phaseone) is the primary entrypoint.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import re
from dataclasses import dataclass
from pathlib import Path

from PIL import ExifTags, Image

# How far into the file to scan for the XMP packet.
_XMP_SCAN_BYTES = 262144

_ATTR_RE = re.compile(rb'drone-dji:(\w+)\s*=\s*"([^"]*)"')


@dataclass
class PhotoMeta:
    """Metadata for one close-up photo, in the units used downstream."""

    path: Path
    camera: str          # "tele" or "wide"
    latitude: float      # WGS84 decimal degrees
    longitude: float     # WGS84 decimal degrees
    abs_alt_m: float     # AbsoluteAltitude, WGS84 ellipsoidal, meters
    yaw: float           # GimbalYawDegree, clockwise from North
    pitch: float         # GimbalPitchDegree (~ -90 for nadir)
    roll: float          # GimbalRollDegree (~ 0)
    width_px: int
    height_px: int
    fl35_mm: float | None          # FocalLengthIn35mmFilm
    calib_focal_px: float | None   # CalibratedFocalLength (wide / Phase One)
    timestamp: str | None          # EXIF DateTimeOriginal


# ── DJI helpers (kept for completeness) ────────────────────────────────────────

def _read_xmp_attrs(path: Path) -> dict[str, str]:
    """Return the ``drone-dji`` XMP attributes of a JPEG as a flat string dict."""
    with open(path, "rb") as fh:
        head = fh.read(_XMP_SCAN_BYTES)
    return {m.group(1).decode(): m.group(2).decode() for m in _ATTR_RE.finditer(head)}


def _to_float(value: str | None) -> float | None:
    """Parse a DJI numeric string (often prefixed with ``+``) to float."""
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def camera_of(path: Path, image_source: str | None) -> str | None:
    """Classify a photo as ``"tele"`` or ``"wide"``."""
    if image_source == "ZoomCamera":
        return "tele"
    if image_source == "WideCamera":
        return "wide"
    stem = path.stem.lower()
    if stem.endswith("tele"):
        return "tele"
    if stem.endswith("wide"):
        return "wide"
    return None


# ── Phase One helpers ───────────────────────────────────────────────────────────

def read_xmp_phaseone(path: Path) -> dict:
    """Read all XMP properties from a JPEG."""
    with open(path, "rb") as f:
        data = f.read()

    start = data.find(b"<x:xmpmeta")
    end = data.find(b"</x:xmpmeta")

    if start == -1 or end == -1:
        return {}

    end += len(b"</x:xmpmeta>")
    xmp_xml = data[start:end]
    root = ET.fromstring(xmp_xml)

    attrs = {}
    for elem in root.iter():
        for k, v in elem.attrib.items():
            attrs[k.split("}")[-1]] = v
        if elem.text and elem.text.strip():
            attrs[elem.tag.split("}")[-1]] = elem.text.strip()

    return attrs


def _to_float_phaseone(value: str | None) -> float | None:
    """Parse Phase One XMP numeric values into float."""
    if value is None or value == "":
        return None
    value = str(value).strip()
    try:
        return float(value)
    except ValueError:
        pass
    if "/" in value:
        try:
            num, den = value.split("/")
            return float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            pass
    return None


def _parse_phaseone_gps(value: str | None) -> float | None:
    """Convert Phase One GPS format (e.g. '9,9.0224349N') to decimal degrees."""
    if value is None or value == "":
        return None
    value = str(value).strip()
    try:
        hemisphere = value[-1]
        value = value[:-1]
        degrees, minutes = value.split(",")
        decimal = float(degrees) + float(minutes) / 60
        if hemisphere in ["S", "W"]:
            decimal *= -1
        return decimal
    except Exception:
        return None


def read_photo_meta_phaseone(path: Path) -> PhotoMeta | None:
    """Read all projection-relevant metadata from one Phase One photo.

    Returns None if the image lacks the required position/orientation fields.
    """
    path = Path(path)
    xmp = read_xmp_phaseone(path)

    # Position
    lat = _parse_phaseone_gps(xmp.get("GPSLatitude"))
    lon = _parse_phaseone_gps(xmp.get("GPSLongitude"))
    abs_alt = _to_float_phaseone(xmp.get("GPSAltitude"))

    # Camera orientation
    yaw = _to_float_phaseone(xmp.get("Yaw"))
    pitch = _to_float_phaseone(xmp.get("Pitch"))
    roll = _to_float_phaseone(xmp.get("Roll"))

    if None in (pitch,):
        return None
    pitch = pitch - 90

    if None in (lat, lon, abs_alt, yaw, pitch, roll):
        return None

    # Camera identification
    make = xmp.get("Make")
    model = xmp.get("Model")
    if make is None or model is None:
        return None

    camera = "wide"

    # Calibrated focal length in pixels
    calib_focal = _to_float_phaseone(xmp.get("DIST_F"))
    if calib_focal is None:
        return None

    # Image metadata
    with Image.open(path) as im:
        width_px, height_px = im.size
        exif = im.getexif()
        exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)

    fl35 = exif_ifd.get(0xA405)
    fl35_mm = float(fl35) if fl35 is not None else None
    timestamp = exif_ifd.get(0x9003)

    return PhotoMeta(
        path=path,
        camera=camera,
        latitude=lat,
        longitude=lon,
        abs_alt_m=abs_alt,
        yaw=yaw,
        pitch=pitch,
        roll=roll,
        width_px=int(width_px),
        height_px=int(height_px),
        fl35_mm=fl35_mm,
        calib_focal_px=calib_focal,
        timestamp=timestamp,
    )
