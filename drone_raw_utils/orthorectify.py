"""Orthorectify a single Phase One image onto a DSM.

Copied from closeup_ortho.orthorectify and trimmed to only the functions needed
for the PhaseOne Image Align QGIS plugin (single-image path, Phase One branch).
All imports are relative within the phaseone_image package.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from osgeo import gdal, osr
from PIL import Image

from .PhotoMeta import PhotoMeta


from .dsm import DSM
from .geometry import (
    camera_boresight_enu,
    compute_footprint,
    footprint_polygon,
    geotiff_affine,
    intersect_boresight_with_dsm,
)

log = logging.getLogger(__name__)

# FOR NOW THIS CODE WORKS FOR PHASE ONE IMAGES

# Nadir tolerance (degrees of gimbal pitch/roll deviation from straight-down).
_NADIR_WARN_DEG = 0.5
_NADIR_SKIP_DEG = 10.0

# Auto buffer target diameter (meters).
_BUFFER_TARGET_DIAM_M = 0.625
_BUFFER_MAX_DIAM_M = 1.0


def _auto_buffer_radius(footprint_w_m: float, footprint_h_m: float) -> float:
    """Buffer radius (m): ~50–75 cm diameter, never larger than the footprint, <= 1 m."""
    diam = min(_BUFFER_TARGET_DIAM_M, footprint_w_m, footprint_h_m, _BUFFER_MAX_DIAM_M)
    return diam / 2.0


def _project_one(meta: PhotoMeta, dsm: DSM, buffer_radius_m: float | None) -> dict | None:

    # expecting picth = -90 and roll = 0 for nadir images, but allow some tolerance
    """Run the planar projection for one Raw drone photo. Returns a record dict, or None to skip."""
    name = meta.path.name

    pitch_dev = abs(meta.pitch + 90.0)
    roll_dev = abs(meta.roll)
    dev = max(pitch_dev, roll_dev)
    if dev > _NADIR_SKIP_DEG:
        log.warning("SKIP %s: gimbal not nadir (pitch=%.2f, roll=%.2f)", name, meta.pitch, meta.roll)
        return None
    nadir_ok = dev <= _NADIR_WARN_DEG
    if not nadir_ok:
        log.warning("%s: gimbal off-nadir by %.2f deg (proceeding)", name, dev)

    x0, y0 = dsm.lonlat_to_xy(meta.longitude, meta.latitude)
    if not dsm.contains(x0, y0):
        log.warning("SKIP %s: camera position is outside the DSM extent", name)
        return None

    # Ground point under the boresight (not the GPS nadir subpoint).
    direction = camera_boresight_enu(meta.yaw, meta.pitch, meta.roll)
    hit = intersect_boresight_with_dsm(x0, y0, meta.abs_alt_m, direction, dsm)
    if hit is None:
        log.warning("SKIP %s: boresight ray doesn't resolve against the DSM", name)
        return None
    gx, gy, seed_elev = hit
    if not dsm.contains(gx, gy):
        log.warning("SKIP %s: boresight ground point outside the DSM extent", name)
        return None

    seed_dist = meta.abs_alt_m - seed_elev
    if seed_dist <= 0:
        log.warning("SKIP %s: non-positive distance (abs_alt=%.2f, dsm=%.2f)", name, meta.abs_alt_m, seed_elev)
        return None
    seed_fp = compute_footprint(
        seed_dist, meta.width_px, meta.height_px, meta.fl35_mm, meta.calib_focal_px, meta.camera
    )

    radius = buffer_radius_m if buffer_radius_m is not None else _auto_buffer_radius(seed_fp.width_m, seed_fp.height_m)
    max_elev = dsm.max_elevation(gx, gy, radius)
    if max_elev is None:
        log.warning("SKIP %s: only nodata in DSM buffer", name)
        return None
    dist = meta.abs_alt_m - max_elev
    if dist <= 0:
        log.warning("SKIP %s: non-positive distance after buffer", name)
        return None

    fp = compute_footprint(
        dist, meta.width_px, meta.height_px, meta.fl35_mm, meta.calib_focal_px, meta.camera
    )
    poly = footprint_polygon(gx, gy, fp.width_m, fp.height_m, meta.yaw)

    return {
        "record": {
            "path": str(meta.path), "filename": name, "directory": str(meta.path.parent),
            "camera": meta.camera, "longitude": meta.longitude, "latitude": meta.latitude,
            "abs_alt_m": meta.abs_alt_m, "dist_m": dist, "gsd_m": fp.gsd_m,
            "footprint_w_m": fp.width_m, "footprint_h_m": fp.height_m,
            "yaw": meta.yaw, "pitch": meta.pitch, "roll": meta.roll, "nadir_ok": nadir_ok,
            "focal_source": fp.focal_source, "timestamp": meta.timestamp, "geometry": poly,
        },
        "x0": gx, "y0": gy,
        "gsd_m": fp.gsd_m, "meta": meta,
    }


def _write_geotiff(meta: PhotoMeta, x0: float, y0: float, gsd_m: float, crs, out_path: Path) -> None:
    """Write the photo as a georeferenced (rotated) GeoTIFF in the DSM CRS using GDAL."""
    
    # 1. Load image data using PIL
    with Image.open(meta.path) as im:
        rgb = np.asarray(im.convert("RGB"))  # (H, W, 3)
    bands = np.transpose(rgb, (2, 0, 1))  # (3, H, W)
    
    # Calculate your existing affine transform matrix
    transform = geotiff_affine(x0, y0, gsd_m, meta.width_px, meta.height_px, meta.yaw)
    
    # Convert your transform to GDAL's 6-element tuple format 
    # (rasterio transforms use a dynamic object or an affine.Affine object)
    gdal_transform = (
        transform.c, transform.a, transform.b,  # X origin, West-East pixel size, Row rotation
        transform.f, transform.d, transform.e   # Y origin, Column rotation, North-South pixel size
    )
    
    # Ensure parent directories exist
    out_path.parent.mkdir(parents=True, exist_ok=True)
    
    # 2. Setup the target coordinate system (CRS)
    srs = osr.SpatialReference()
    # Handle if crs is passed as an EPSG integer, WKT string, or rasterio CRS string
    if isinstance(crs, int):
        srs.ImportFromEPSG(crs)
    elif isinstance(crs, str):
        srs.ImportFromWkt(crs)
    else:
        # Fallback to string representation if it's a rasterio/pyproj CRS object
        srs.ImportFromUserString(str(crs))
    
    # 3. Create a temporary in-memory dataset to hold the initial raw raster
    mem_driver = gdal.GetDriverByName("MEM")
    mem_ds = mem_driver.Create(
        "",  # Memory datasets don't need a filename
        meta.width_px,
        meta.height_px,
        3,   # Number of bands
        gdal.GDT_Byte
    )
    
    try:
        mem_ds.SetGeoTransform(gdal_transform)
        mem_ds.SetProjection(srs.ExportToWkt())
        
        # Write array data band by band
        for i in range(3):
            band = mem_ds.GetRasterBand(i + 1)
            band.WriteArray(bands[i])
            
        # 4. Use the COG driver to create the final optimized file
        cog_driver = gdal.GetDriverByName("COG")
        
        # Translate creation options into GDAL's array-of-strings format
        cog_options = [
            "COMPRESS=DEFLATE",
            "BIGTIFF=IF_SAFER",
            "BLOCKSIZE=512",          # Standard web-optimized block width
            "PREDICTOR=2",             # Optimizes compression ratio for continuous/RGB values
            "NUM_THREADS=ALL_CPUS"     # Spreads compression load across all available cores
        ]
        # CreateCopy generates the official COG layout automatically
        out_ds = cog_driver.CreateCopy(str(out_path), mem_ds, options=cog_options)
        
        if out_ds is None:
            raise RuntimeError(f"GDAL failed to write COG to {out_path}")
            
        # Close the output dataset to flush to disk
        out_ds = None
        
    finally:
        # Clean up the memory file handle safely
        mem_ds = None

def orthorectify_image(
    image_path,
    dsm_path,
    geotiff_path,
    metadata_reader,
    buffer_radius_m: float | None = None,
) -> Path:
    """Orthorectify a single Raw drone image onto a DSM and write a GeoTIFF.

    Parameters
    ----------
    image_path : str or Path
        Path to the Raw drone image.
    dsm_path : str or Path
        Path to the DSM raster.
    geotiff_path : str or Path
        Output GeoTIFF path for the projected image.
    buffer_radius_m : float or None
        DSM sampling buffer override.

    Returns
    -------
    Path to the written GeoTIFF.

    Raises
    ------
    RuntimeError
        If metadata is missing or projection fails.
    """
    image_path = Path(image_path)
    geotiff_path = Path(geotiff_path)

    with DSM(dsm_path) as dsm:
        log.info("DSM CRS: %s | resolution: %.4f m", dsm.crs, dsm.res_x)

        meta = metadata_reader.read(image_path)
        if meta is None:
            raise RuntimeError(f"Missing metadata: {image_path.name}")

        result = _project_one(meta, dsm, buffer_radius_m)
        if result is None:
            raise RuntimeError(f"Projection failed: {image_path.name}")

        _write_geotiff(
            meta,
            result["x0"],
            result["y0"],
            result["gsd_m"],
            dsm.crs,
            geotiff_path,
        )

    log.info("Orthorectified %s -> %s", image_path.name, geotiff_path)
    return geotiff_path
