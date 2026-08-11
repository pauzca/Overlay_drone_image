"""

Read metadat of image setting custom fields
DOES NOT REQUIRE QGIS library

It uses the systems exiftool

"""

from __future__ import annotations

import subprocess
import csv
import json
import re
from pathlib import Path

from PIL import ExifTags, Image
from dataclasses import dataclass

import xml.etree.ElementTree as ET


from drone_raw_utils.PhotoMeta import PhotoMeta

# DJI normally stores the XMP packet near the beginning of the JPEG.
_XMP_SCAN_BYTES = 262144

# Extract attributes such as:
#
# drone-dji:GpsLatitude="+9.12345"
# drone-dji:GimbalPitchDegree="-89.9"
#
_ATTR_RE = re.compile(
    rb'drone-dji:(\w+)\s*=\s*"([^"]*)"'
)



class CustomMetadataReader:
    """Read Trinity/Sony metadata using ExifTool."""

    def read(self, path: Path | str) -> PhotoMeta | None:
        """Read projection-relevant metadata from a Trinity/Sony image."""

        path = Path(path)

        # ----------------------------------------------------------
        # Read metadata using ExifTool
        # ----------------------------------------------------------
        meta = self._read_exiftool(path)

        lat = self._to_float(meta.get("GPSLatitude"))
        lon = self._to_float(meta.get("GPSLongitude"))
        abs_alt = self._to_float(meta.get("GPSAltitude"))

        yaw = self._to_float(meta.get("Yaw"))
        pitch = self._to_float(meta.get("Pitch"))
        roll = self._to_float(meta.get("Roll"))

        # ----------------------------------------------------------
        # Validate required GPS and orientation metadata
        # ----------------------------------------------------------
        required_metadata = {
            "GPSLatitude": lat,
            "GPSLongitude": lon,
            "GPSAltitude": abs_alt,
            "Yaw": yaw,
            "Pitch": pitch,
            "Roll": roll,
        }

        missing = [
            name
            for name, value in required_metadata.items()
            if value is None
        ]

        if missing:
            raise ValueError(
                f"Cannot read required metadata from image:\n"
                f"  File: {path}\n"
                f"  Missing: {', '.join(missing)}\n"
                f"  Required fields: {', '.join(required_metadata)}"
            )


        # ----------------------------------------------------------
        # Trinity pitch convention
        # ----------------------------------------------------------
        # ONLY keep this if you have verified that Trinity uses:
        #
        #     0 degrees = nadir
        #
        # while your projection code expects:
        #
        #    -90 degrees = nadir
        #
        pitch = pitch - 90.0

        # ----------------------------------------------------------
        # Camera information
        # ----------------------------------------------------------
        make = meta.get("Make")
        model = meta.get("Model")

        if make is None or model is None:
            raise ValueError(
                f"Missing camera make/model in {path}"
            )

        camera = "wide"

        # ----------------------------------------------------------
        # Image dimensions
        # ----------------------------------------------------------
        # ExifTool already provides these, so Pillow is not strictly
        # necessary for width/height.
        width_px = meta.get("ImageWidth")
        height_px = meta.get("ImageHeight")

        if width_px is None or height_px is None:
            # Fallback to Pillow if ExifTool did not return dimensions.
            with Image.open(path) as im:
                width_px, height_px = im.size

        # ----------------------------------------------------------
        # Focal length
        # ----------------------------------------------------------
        # Trinity/Sony metadata contains FocalLengthIn35mmFormat.
        fl35_mm = self._to_float(
            meta.get("FocalLengthIn35mmFormat")
        )

        # If it isn't available, try the normal focal length.
        if fl35_mm is None:
            fl35_mm = self._to_float(
                meta.get("FocalLength")
            )

        # Sony DSC-RX1RM2 sensor dimensions
        SENSOR_WIDTH_MM = 35.9

        focal_length_mm = self._to_float(
            meta.get("FocalLength")
        )

        if focal_length_mm is None:
            raise ValueError(
                f"Missing focal length in {path}"
            )

        calib_focal = (
            focal_length_mm
            * float(width_px)
            / SENSOR_WIDTH_MM
        )

        # ----------------------------------------------------------
        # Timestamp
        # ----------------------------------------------------------
        timestamp = meta.get("DateTimeOriginal")

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

    @staticmethod
    def _read_exiftool(path: Path) -> dict:
        """Read the required metadata from an image using ExifTool."""

        cmd = [
            "exiftool",
            "-json",
            "-n",

            "-GPSLatitude",
            "-GPSLongitude",
            "-GPSAltitude",

            "-Pitch",
            "-Roll",
            "-Yaw",

            "-Make",
            "-Model",

            "-ImageWidth",
            "-ImageHeight",

            "-FocalLength",
            "-FocalLengthIn35mmFormat",

            "-DateTimeOriginal",

            str(path),
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=True,
        )

        metadata = json.loads(result.stdout)

        if not metadata:
            raise ValueError(
                f"ExifTool returned no metadata for {path}"
            )

        return metadata[0]

    @staticmethod
    def _to_float(value) -> float | None:
        """Convert an ExifTool numeric value to float."""

        if value is None or value == "":
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    
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


    def extract_gps_to_csv(self, image_folder: Path | str, output_csv: Path | str) -> str:
        image_folder = Path(image_folder)

        cmd = [
            r"exiftool",
            "-fast2",
            "-json",
            "-n",
            "-GPSLatitude",
            "-GPSLongitude",
            "-FileName",
            str(image_folder)
        ]

        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True
        )

        metadata = json.loads(result.stdout)

        rows = []

        for img in metadata:

            if (
                "GPSLatitude" not in img
                or "GPSLongitude" not in img
            ):
                continue

            lat = (
                img["GPSLatitude"]
            )

            lon = (
                img["GPSLongitude"]
            )

            rows.append(
                {
                    "filename": img["FileName"],
                    "latitude": lat,
                    "longitude": lon
                }
            )


        with open(output_csv, "w", newline="") as f:

            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "filename",
                    "latitude",
                    "longitude"
                ]
            )

            writer.writeheader()
            writer.writerows(rows)


        print(
            f"Saved {len(rows)} images to {output_csv}"
        )


class DJImetadata(CustomMetadataReader):
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


class TrinityMetadata():
    """Read Trinity/Sony metadata without QGIS or ExifTool."""

    SENSOR_WIDTH_MM = 35.9

    def read(self, path: Path | str) -> PhotoMeta | None:
        path = Path(path)

        # EXIF metadata
        exif_meta = self._read_exif(path)
        lat, lon, abs_alt = exif_meta["latitude"], exif_meta["longitude"], exif_meta["altitude"]
        width_px, height_px = exif_meta["width_px"], exif_meta["height_px"]
        focal_length_mm, fl35_mm, timestamp = exif_meta["focal_length_mm"], exif_meta["fl35_mm"], exif_meta["timestamp"]

        # Trinity / QBase XMP orientation
        xmp = self._read_xmp(path)
        yaw, pitch, roll = self._to_float(xmp.get("Yaw")), self._to_float(xmp.get("Pitch")), self._to_float(xmp.get("Roll"))

        # Validate required metadata
        required_metadata = {
            "GPSLatitude": lat, "GPSLongitude": lon, "GPSAltitude": abs_alt,
            "Yaw": yaw, "Pitch": pitch, "Roll": roll, "FocalLength": focal_length_mm,
        }
        missing = [name for name, value in required_metadata.items() if value is None]
        if missing:
            raise ValueError(f"Cannot read required metadata from image:\n  File: {path}\n  Missing: {', '.join(missing)}")

        # Convert Trinity pitch convention (-90° = nadir, 0° = horizontal)
        pitch = pitch - 90.0

        # Approximate focal length in pixels (fx = focal_length_mm * width_px / sensor_width_mm)
        calib_focal = focal_length_mm * float(width_px) / self.SENSOR_WIDTH_MM

        return PhotoMeta(
            path=path, camera="wide", latitude=lat, longitude=lon, abs_alt_m=abs_alt,
            yaw=yaw, pitch=pitch, roll=roll, width_px=int(width_px), height_px=int(height_px),
            calib_focal_px=calib_focal, fl35_mm=fl35_mm, timestamp=timestamp,
        )

    def _read_exif(self, path: Path) -> dict:
        """Read standard EXIF/GPS metadata using Pillow."""
        with Image.open(path) as im:
            width_px, height_px = im.size
            exif = im.getexif()

            try:
                exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            except (KeyError, AttributeError):
                exif_ifd = {}

            try:
                gps_ifd = exif.get_ifd(ExifTags.IFD.GPSInfo)
            except (KeyError, AttributeError):
                gps_ifd = {}

        # GPS coordinates
        latitude = self._gps_to_decimal(gps_ifd.get(2), gps_ifd.get(1))
        longitude = self._gps_to_decimal(gps_ifd.get(4), gps_ifd.get(3))
        altitude = gps_ifd.get(6)

        if altitude is not None:
            altitude = float(altitude)
            if gps_ifd.get(5) == 1:  # 1 = below sea level
                altitude = -altitude

        # Focal length (0x920A = FocalLength, 0xA405 = FocalLengthIn35mmFilm)
        focal_length = exif_ifd.get(0x920A)
        focal_length_mm = float(focal_length) if focal_length is not None else None
        fl35 = exif_ifd.get(0xA405)
        fl35_mm = float(fl35) if fl35 is not None else None

        # Timestamp (0x9003 = DateTimeOriginal)
        timestamp = exif_ifd.get(0x9003)

        return {
            "latitude": latitude, "longitude": longitude, "altitude": altitude,
            "width_px": width_px, "height_px": height_px,
            "focal_length_mm": focal_length_mm, "fl35_mm": fl35_mm, "timestamp": timestamp,
        }

    @staticmethod
    def _gps_to_decimal(value, ref) -> float | None:
        """Convert EXIF GPS coordinates to signed decimal degrees."""
        if value is None or ref is None:
            return None

        try:
            degrees, minutes, seconds = float(value[0]), float(value[1]), float(value[2])
            decimal = degrees + minutes / 60.0 + seconds / 3600.0

            if isinstance(ref, bytes):
                ref = ref.decode("ascii")
            if str(ref).upper() in ("S", "W"):
                decimal = -decimal

            return decimal
        except (TypeError, ValueError, IndexError, ZeroDivisionError):
            return None

    def _read_xmp(self, path: Path) -> dict[str, str]:
        """Read Trinity/QBase XMP metadata directly from the JPEG binary."""
        with open(path, "rb") as f:
            data = f.read()

        start, end = data.find(b"<x:xmpmeta"), data.find(b"</x:xmpmeta")
        if start == -1 or end == -1:
            return {}

        end += len(b"</x:xmpmeta>")
        try:
            root = ET.fromstring(data[start:end])
        except ET.ParseError:
            return {}

        metadata = {}
        for elem in root.iter():
            # Clean local tag name (strips XML namespace like '{http://...}')
            tag = elem.tag.split("}")[-1] if "}" in elem.tag else elem.tag

            # Check element attributes (e.g., Pitch="24070/4297")
            for attr_key, attr_val in elem.attrib.items():
                clean_key = attr_key.split("}")[-1] if "}" in attr_key else attr_key
                metadata[clean_key] = attr_val

            # Check element text content
            if elem.text and elem.text.strip():
                metadata[tag] = elem.text.strip()

        return metadata

    @staticmethod
    def _to_float(value: str | float | None) -> float | None:
        if value is None or value == "":
            return None

        try:
            # Handle fractional string values like "24070/4297"
            if isinstance(value, str) and "/" in value:
                num, denom = value.split("/", 1)
                return float(num) / float(denom)

            return float(value)
        except (TypeError, ValueError, ZeroDivisionError):
            return None