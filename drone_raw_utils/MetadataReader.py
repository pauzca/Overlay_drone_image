from __future__ import annotations

import abc
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from PIL import ExifTags, Image
import csv
import shutil

from qgis.core import QgsExifTools, QgsPointXY
_XMP_SCAN_BYTES = 262144
_ATTR_RE = re.compile(rb'drone-dji:(\w+)\s*=\s*"([^"]*)"')


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


class BaseMetadataReader(abc.ABC):
    """Abstract base class for drone metadata readers."""

    @abc.abstractmethod
    def read(self, path: Path | str) -> PhotoMeta | None:
        """Parse metadata from file and return a standardized PhotoMeta instance."""
        pass
    
    def extract_gps_to_csv(self, image_folder: Path | str, output_csv: Path | str) -> str:
            """Batch extract GPS coordinates using native QGIS QgsExifTools."""
            image_folder = Path(image_folder)
            output_csv = Path(output_csv)
            output_csv.parent.mkdir(parents=True, exist_ok=True)

            image_files = sorted(
                [p for p in image_folder.iterdir() if p.suffix.lower() in (".jpg", ".jpeg")]
            )

            rows = []
            for img_path in image_files:
                # QgsExifTools.getGeoTag returns a QgsPointXY (X=Longitude, Y=Latitude)
                point, check = QgsExifTools.getGeoTag(str(img_path))
                # Check if point is valid and non-empty
                if not point.isEmpty():
                    rows.append({
                        "filename": img_path.name,
                        "latitude": point.y(),   # Y is Latitude
                        "longitude": point.x(),  # X is Longitude
                    })

            with open(output_csv, "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=["filename", "latitude", "longitude"])
                writer.writeheader()
                writer.writerows(rows)

            print("Extracted %d coordinates to %s using QgsExifTools", len(rows), output_csv)
            return str(output_csv)

## CUSTOM DRONE METADATA READERS (e.g., Phase One, DJI) IMPLEMENTED BELOW

# ── Phase One Reader ──────────────────────────────────────────────────────────

class PhaseOneMetadataReader(BaseMetadataReader):
    """Reads EXIF + XMP metadata for Phase One cameras."""

    def read(self, path: Path | str) -> PhotoMeta | None:
        path = Path(path)
        xmp = self._read_xmp(path)

        lat = self._parse_gps(xmp.get("GPSLatitude"))
        lon = self._parse_gps(xmp.get("GPSLongitude"))
        abs_alt = self._to_float(xmp.get("GPSAltitude"))

        yaw = self._to_float(xmp.get("Yaw"))
        pitch = self._to_float(xmp.get("Pitch"))
        roll = self._to_float(xmp.get("Roll"))

        if pitch is None or None in (lat, lon, abs_alt, yaw, roll):
            return None

        # phase one pitch correction
        pitch = pitch - 90  # Convert to nadir convention

        calib_focal = self._to_float(xmp.get("DIST_F"))
        if calib_focal is None:
            return None

        with Image.open(path) as im:
            width_px, height_px = im.size
            exif = im.getexif()
            exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)

        fl35 = exif_ifd.get(0xA405)
        fl35_mm = float(fl35) if fl35 is not None else None
        timestamp = exif_ifd.get(0x9003)

        return PhotoMeta(
            path=path,
            camera="wide",
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

    def _read_xmp(self, path: Path) -> dict:
        with open(path, "rb") as f:
            data = f.read()
        start = data.find(b"<x:xmpmeta")
        end = data.find(b"</x:xmpmeta")
        if start == -1 or end == -1:
            return {}
        end += len(b"</x:xmpmeta>")
        root = ET.fromstring(data[start:end])
        attrs = {}
        for elem in root.iter():
            for k, v in elem.attrib.items():
                attrs[k.split("}")[-1]] = v
            if elem.text and elem.text.strip():
                attrs[elem.tag.split("}")[-1]] = elem.text.strip()
        return attrs

    def _to_float(self, value: str | None) -> float | None:
        if not value:
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

    def _parse_gps(self, value: str | None) -> float | None:
        if not value:
            return None
        try:
            val = str(value).strip()
            hemisphere = val[-1]
            degrees, minutes = val[:-1].split(",")
            decimal = float(degrees) + float(minutes) / 60
            if hemisphere in ["S", "W"]:
                decimal *= -1
            return decimal
        except Exception:
            return None


# ── DJI Reader ────────────────────────────────────────────────────────────────

class DJIMetadataReader(BaseMetadataReader):
    """Reads EXIF + XMP metadata for DJI Enterprise drones (Mavic 3E, P4 RTK, etc.)."""

    def read(self, path: Path | str) -> PhotoMeta | None:
        path = Path(path)
        xmp = self._read_xmp(path)

        lat = self._to_float(xmp.get("GpsLatitude"))
        lon = self._to_float(xmp.get("GpsLongitude"))
        abs_alt = self._to_float(xmp.get("AbsoluteAltitude"))

        yaw = self._to_float(xmp.get("GimbalYawDegree"))
        pitch = self._to_float(xmp.get("GimbalPitchDegree"))
        roll = self._to_float(xmp.get("GimbalRollDegree"))

        if None in (lat, lon, abs_alt, yaw, pitch, roll):
            return None

        with Image.open(path) as im:
            width_px, height_px = im.size
            exif = im.getexif()
            exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)

        timestamp = exif_ifd.get(0x9003)
        fl35 = exif_ifd.get(0xA405)

        return PhotoMeta(
            path=path,
            camera="wide",
            latitude=lat,
            longitude=lon,
            abs_alt_m=abs_alt,
            yaw=yaw,
            pitch=pitch,
            roll=roll,
            width_px=width_px,
            height_px=height_px,
            fl35_mm=float(fl35) if fl35 else None,
            timestamp=timestamp,
        )

    def _read_xmp(self, path: Path) -> dict[str, str]:
        with open(path, "rb") as fh:
            head = fh.read(_XMP_SCAN_BYTES)
        return {m.group(1).decode(): m.group(2).decode() for m in _ATTR_RE.finditer(head)}

    def _to_float(self, value: str | None) -> float | None:
        if value is None or value == "":
            return None
        try:
            return float(value)
        except ValueError:
            return None