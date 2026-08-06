"""Footprint and georeferencing geometry for a simple nadir planar projection.

Copied from closeup_ortho.geometry and adapted for standalone use inside the
phaseone_image QGIS plugin package.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from affine import Affine
from shapely.affinity import rotate, translate
from shapely.geometry import Polygon, box

# Diagonal of a 36 x 24 mm "full frame", used to interpret FocalLengthIn35mmFilm.
_DIAG35_MM = math.hypot(36.0, 24.0)  # 43.26661...


@dataclass
class Footprint:
    """Result of sizing a photo's ground footprint at a given distance."""

    gsd_m: float        # ground sample distance, meters / pixel
    width_m: float      # ground footprint width  (along image columns)
    height_m: float     # ground footprint height (along image rows)
    focal_source: str   # "calib" or "fl35"


def focal_length_px(
    width_px: int,
    height_px: int,
    fl35_mm: float | None,
    calib_focal_px: float | None,
    camera: str,
) -> tuple[float, str]:
    """Return the focal length in pixels and which source was used."""
    if camera == "wide" and calib_focal_px:
        return calib_focal_px, "calib"
    if fl35_mm:
        diag_px = math.hypot(width_px, height_px)
        return diag_px * fl35_mm / _DIAG35_MM, "fl35"
    raise ValueError("No focal length available (neither CalibratedFocalLength nor FL35).")


def compute_footprint(
    dist_m: float,
    width_px: int,
    height_px: int,
    fl35_mm: float | None,
    calib_focal_px: float | None,
    camera: str,
) -> Footprint:
    """Size the ground footprint of a nadir photo at camera-to-ground distance ``dist_m``."""
    f_px, source = focal_length_px(width_px, height_px, fl35_mm, calib_focal_px, camera)
    gsd = dist_m / f_px
    return Footprint(
        gsd_m=gsd,
        width_m=gsd * width_px,
        height_m=gsd * height_px,
        focal_source=source,
    )


def footprint_polygon(x0: float, y0: float, width_m: float, height_m: float, yaw_deg: float) -> Polygon:
    """Build the ground footprint rectangle centered at ``(x0, y0)`` in the DSM CRS."""
    rect = box(-width_m / 2.0, -height_m / 2.0, width_m / 2.0, height_m / 2.0)
    rect = rotate(rect, -yaw_deg, origin=(0.0, 0.0))
    return translate(rect, xoff=x0, yoff=y0)


def geotiff_affine(
    x0: float,
    y0: float,
    gsd_m: float,
    width_px: int,
    height_px: int,
    yaw_deg: float,
) -> Affine:
    """Affine transform mapping image (col, row) -> projected (easting, northing)."""
    g = gsd_m
    cx = width_px / 2.0
    cy = height_px / 2.0
    p = math.radians(yaw_deg)
    cs = math.cos(p)
    sn = math.sin(p)

    a = g * cs
    b = -g * sn
    c = x0 - cx * g * cs + cy * g * sn
    d = -g * sn
    e = -g * cs
    f = y0 + cx * g * sn + cy * g * cs
    return Affine(a, b, c, d, e, f)


def camera_boresight_enu(yaw_deg: float, pitch_deg: float, roll_deg: float) -> np.ndarray:
    """Unit vector (ENU: east, north, up) that the camera is pointing along."""
    y, p, r = (math.radians(a) for a in (yaw_deg, pitch_deg, roll_deg))
    Rz = np.array([[math.cos(y), -math.sin(y), 0],
                   [math.sin(y),  math.cos(y), 0],
                   [0, 0, 1]])
    Ry = np.array([[math.cos(p), 0, math.sin(p)],
                   [0, 1, 0],
                   [-math.sin(p), 0, math.cos(p)]])
    Rx = np.array([[1, 0, 0],
                   [0, math.cos(r), -math.sin(r)],
                   [0, math.sin(r), math.cos(r)]])
    R_body_to_ned = Rz @ Ry @ Rx
    ned_to_enu = np.array([[0, 1, 0],
                            [1, 0, 0],
                            [0, 0, -1]], dtype=float)
    M = ned_to_enu @ R_body_to_ned
    return M[:, 0]  # forward/boresight axis, expressed in ENU


def intersect_boresight_with_dsm(x0, y0, z0, direction, dsm, max_iter=10, tol_m=0.01):
    """Ray-march the camera boresight down to where it hits the DSM surface.

    Returns (x, y, z) in the DSM CRS, or None if it can't resolve.
    """
    if direction[2] >= 0:
        return None  # camera pointing at/above horizon, not the ground
    x, y = x0, y0
    z = dsm.max_elevation(x, y, 0.0)
    if z is None:
        return None
    for _ in range(max_iter):
        t = (z0 - z) / (-direction[2])
        x_new = x0 + direction[0] * t
        y_new = y0 + direction[1] * t
        z_new = dsm.max_elevation(x_new, y_new, 0.0)
        if z_new is None:
            return None
        if abs(x_new - x) < tol_m and abs(y_new - y) < tol_m:
            return x_new, y_new, z_new
        x, y, z = x_new, y_new, z_new
    return x, y, z
