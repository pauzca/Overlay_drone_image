"""Alignment helpers for Phase One images vs. an orthomosaic reference.

Copied from closeup_ortho.align_aux and adapted for standalone use inside the
phaseone_image QGIS plugin package.
"""

import numpy as np
from osgeo import gdal, osr
from skimage.exposure import match_histograms
from affine import Affine
import cv2


# ── Low-level utilities ─────────────────────────────────────────────────────────
from osgeo import gdal
import numpy as np
from osgeo import gdal
import numpy as np

def crop_square_fast(input_raster, center_x, center_y, half, debug_save_path=None):
    ds = gdal.Open(input_raster)
    if ds is None:
        raise FileNotFoundError(f"Could not open {input_raster}")
        
    try:
        gt = ds.GetGeoTransform()
        raster_width = ds.RasterXSize
        raster_height = ds.RasterYSize
        
        # 1. Invert the geotransform to convert Map (X, Y) -> Pixel (Col, Row)
        inv_gt = gdal.InvGeoTransform(gt)
        if not inv_gt:
            raise RuntimeError("Could not invert the raster's geotransform matrix.")
            
        # 2. Define the true square bounding box in MAP UNITS (meters)
        # 20m above, below, left, right from the center coordinate
        map_min_x = center_x - half
        map_max_x = center_x + half
        map_min_y = center_y - half
        map_max_y = center_y + half
        
        # 3. Convert all 4 corners of the map bounding box into pixel coordinates
        def map_to_pixel(mx, my):
            c = int(inv_gt[0] + mx * inv_gt[1] + my * inv_gt[2])
            r = int(inv_gt[3] + mx * inv_gt[4] + my * inv_gt[5])
            return c, r

        c1, r1 = map_to_pixel(map_min_x, map_min_y)
        c2, r2 = map_to_pixel(map_min_x, map_max_y)
        c3, r3 = map_to_pixel(map_max_x, map_min_y)
        c4, r4 = map_to_pixel(map_max_x, map_max_y)
        
        # Find the min/max pixel extent needed to cover this map region
        cols = [c1, c2, c3, c4]
        rows = [r1, r2, r3, r4]
        
        min_col, max_col = min(cols), max(cols)
        min_row, max_row = min(rows), max(rows)
        
        # 4. Define pixel offsets and sizes based on bounds
        x_offset = min_col
        y_offset = min_row
        x_size = max_col - min_col
        y_size = max_row - min_row
        
        # 5. Clip boundaries to stay safely inside the image dimensions
        if x_offset < 0:
            x_size = max(0, x_size + x_offset)
            x_offset = 0
        if y_offset < 0:
            y_size = max(0, y_size + y_offset)
            y_offset = 0
            
        if x_offset + x_size > raster_width:
            x_size = max(0, raster_width - x_offset)
        if y_offset + y_size > raster_height:
            y_size = max(0, raster_height - y_offset)
            
        if x_size <= 0 or y_size <= 0:
            raise ValueError("The requested crop window falls entirely outside the image bounds.")

        # Read the pixel data matching the calculated bounding box
        data = ds.ReadAsArray(x_offset, y_offset, x_size, y_size)
        
        # 6. Recompute the correct top-left origin based on the pixel offset shift
        new_top_left_x = gt[0] + (x_offset * gt[1]) + (y_offset * gt[2])
        new_top_left_y = gt[3] + (x_offset * gt[4]) + (y_offset * gt[5])
        new_transform = (new_top_left_x, gt[1], gt[2], new_top_left_y, gt[4], gt[5])
        
        src_band = ds.GetRasterBand(1)
        gdal_dtype = src_band.DataType

        profile = {
            'driver': ds.GetDriver().ShortName,
            'height': y_size,
            'width': x_size,
            'count': ds.RasterCount,
            'dtype': gdal.GetDataTypeName(gdal_dtype),
            'crs': ds.GetProjection(),
            'transform': new_transform
        }

        # DEBUG SAVE STEP
        if debug_save_path:
            tiff_driver = gdal.GetDriverByName("GTiff")
            out_ds = tiff_driver.Create(str(debug_save_path), x_size, y_size, ds.RasterCount, gdal_dtype)
            if out_ds is None:
                raise RuntimeError(f"Failed to create debug file at {debug_save_path}")
            
            out_ds.SetGeoTransform(new_transform)
            out_ds.SetProjection(ds.GetProjection())
            
            if ds.RasterCount == 1:
                out_ds.GetRasterBand(1).WriteArray(data)
            else:
                for band_idx in range(ds.RasterCount):
                    out_ds.GetRasterBand(band_idx + 1).WriteArray(data[band_idx])
            out_ds = None
            print(f"[DEBUG] Successfully saved center-cropped preview to: {debug_save_path}")
        
    finally:
        ds = None
        
    return data, new_transform, profile


def raster_to_grayscale(img):
    """Convert a (bands, H, W) float array to a normalised grayscale (H, W) array."""
    img = img.astype(np.float32)
    if img.shape[0] < 3:
        raise ValueError("Image needs at least 3 bands")
    gray = 0.299 * img[0] + 0.587 * img[1] + 0.114 * img[2]
    p1, p99 = np.percentile(gray, [1, 99])
    gray = np.clip(gray, p1, p99)
    gray = (gray - p1) / (p99 - p1)
    return gray


def resample_to_reference(source_data, source_transform, source_crs, reference_profile):

    bands, height, width = source_data.shape
    driver = gdal.GetDriverByName("MEM")
    src_ds = driver.Create("", width, height, bands, gdal.GDT_Float32)
    src_ds.SetGeoTransform(source_transform)
    src_ds.SetProjection(source_crs)

    for i in range(bands):
        src_ds.GetRasterBand(i+1).WriteArray(source_data[i])

    dst_ds = driver.Create(
        "",
        reference_profile["width"],
        reference_profile["height"],
        bands,
        gdal.GDT_Float32
    )

    dst_ds.SetGeoTransform(reference_profile["transform"])
    dst_ds.SetProjection(reference_profile["crs"])


    gdal.ReprojectImage(
        src_ds,
        dst_ds,
        source_crs,
        reference_profile["crs"],
        gdal.GRA_Average
    )


    output = np.empty((bands,reference_profile["height"],reference_profile["width"]), dtype=np.float32)

    for i in range(bands):
        output[i] = dst_ds.GetRasterBand(i+1).ReadAsArray()

    return output

def get_raster_center(raster_path):
    ds = gdal.Open(str(raster_path))
    if ds is None:
        raise FileNotFoundError(f"Could not open {raster_path}")
        
    try:
        transform = ds.GetGeoTransform()
        width = ds.RasterXSize
        height = ds.RasterYSize

        # Find the pixel coordinates for the exact middle
        center_col = width / 2.0
        center_row = height / 2.0

        # Calculate true map coordinates using all 6 coefficients 
        # to account for rotation/yaw
        center_x = transform[0] + (center_col * transform[1]) + (center_row * transform[2])
        center_y = transform[3] + (center_col * transform[4]) + (center_row * transform[5])
        
    finally:
        ds = None

    return center_x, center_y

def to_uint8(arr):
    """Stretch a float array to uint8 using 1–99 percentile clipping."""
    arr = arr.astype(np.float32)
    lo, hi = np.nanpercentile(arr, 1), np.nanpercentile(arr, 99)
    arr = np.clip((arr - lo) / (hi - lo + 1e-6), 0, 1) * 255
    return arr.astype(np.uint8)


# ── Main alignment function ─────────────────────────────────────────────────────
def align_phaseone_to_ortho(
    phaseone_path,
    ortho_path,
    output_path,
    crop_size=25,
    do_histogram_matching=False,
):
    """Align a Phase One orthorectified GeoTIFF to a reference orthomosaic."""

    import logging
    import os
    from osgeo import gdal

    log = logging.getLogger(__name__)

    center_x, center_y = get_raster_center(phaseone_path)
    print("Phase One centre: %.2f, %.2f", center_x, center_y)

    # 1. Crop matching regions
    phase_crop, phase_transform, phase_profile = crop_square_fast(phaseone_path, center_x, center_y, crop_size)
    ortho_crop, ortho_transform, ortho_profile = crop_square_fast(ortho_path, center_x, center_y, crop_size)

    # 2. Resample Phase One to ortho grid
    phase_ds = gdal.Open(str(phaseone_path))
    phase_crs = phase_ds.GetProjection()
    phase_ds = None

    phase_resampled = resample_to_reference(phase_crop,phase_transform,phase_crs,ortho_profile)

    # 3. Grayscale
    phase_gray = raster_to_grayscale(phase_resampled)
    ortho_gray = raster_to_grayscale(ortho_crop)

    phase = phase_gray

    if do_histogram_matching:
        phase = match_histograms(phase_gray, ortho_gray)

    # 4. Blur
    phase = cv2.GaussianBlur(phase, (21, 21), 0)
    ortho = cv2.GaussianBlur(ortho_gray, (21, 21), 0)

    # 5. SIFT + BF matching
    sift = cv2.SIFT_create()

    kp1, des1 = sift.detectAndCompute(to_uint8(phase), None)
    kp2, des2 = sift.detectAndCompute(to_uint8(ortho), None)

    if des1 is None or des2 is None:
        raise ValueError("No SIFT descriptors found")

    matcher = cv2.BFMatcher()
    matches = matcher.knnMatch(des1, des2, k=2)

    good = [m for m, n in matches if m.distance < 0.7 * n.distance]

    log.info("Good matches: %d", len(good))

    if len(good) < 5:
        raise ValueError("Not enough good matches")

    src_pts = np.float32([kp1[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)

    dst_pts = np.float32([kp2[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)

    M, mask = cv2.estimateAffinePartial2D(
        src_pts,
        dst_pts,
        method=cv2.RANSAC,
        ransacReprojThreshold=5
    )

    if M is None:
        raise ValueError("Could not estimate affine transformation")

    inliers = int(mask.sum())
    total = len(mask)

    # 6. Reprojection error
    src_inliers = src_pts[mask.ravel() == 1]
    dst_inliers = dst_pts[mask.ravel() == 1]

    predicted = cv2.transform(src_inliers, M)

    errors = np.linalg.norm(
        predicted - dst_inliers,
        axis=2
    )

    rmse = float(np.sqrt(np.mean(errors ** 2)))
    mean_error = float(np.mean(errors))
    median_error = float(np.median(errors))

    log.info(
        "Inliers: %d/%d | RMSE: %.3f px | Mean: %.3f px",
        inliers,
        total,
        rmse,
        mean_error
    )

    M_affine = Affine(
        M[0, 0],M[0, 1],M[0, 2],
        M[1, 0],M[1, 1],M[1, 2],
    )

    scale = float(
        np.sqrt(
            M_affine.a ** 2 +
            M_affine.d ** 2
        )
    )

    log.info(
        "Scale: %.4f | Translation: %.2f %.2f px",
        scale,
        M_affine.c,
        M_affine.f
    )

    if not (0.9 < scale < 1.1):
        raise ValueError(
            "Affine transform implies >10% scale change"
        )

    # 7. Apply correction
    src = gdal.Open(str(phaseone_path))

    orig_transform = src.GetGeoTransform()

    T_orig = Affine(
        orig_transform[1],orig_transform[2],orig_transform[0],
        orig_transform[4],orig_transform[5],orig_transform[3],
    )

    G = Affine(ortho_transform[1],ortho_transform[2],ortho_transform[0],
        ortho_transform[4],ortho_transform[5],ortho_transform[3],
    )

    T_correction = G * M_affine * (~G)
    T_new = T_correction * T_orig


    os.makedirs(os.path.dirname(os.path.abspath(output_path)),exist_ok=True)

    driver = gdal.GetDriverByName("GTiff")

    cog_options = [
    "COMPRESS=DEFLATE",
    "BIGTIFF=IF_SAFER",
    "TILED=YES",               # Crucial for COG layout optimization
    "BLOCKXSIZE=512",          # Standard web-optimized block width
    "BLOCKYSIZE=512",          # Standard web-optimized block height
    "PREDICTOR=2",             # Optimizes compression ratio for continuous/RGB values
    "NUM_THREADS=ALL_CPUS"     # Spreads compression load across all available cores
    ]
    
    out = driver.Create(
        str(output_path),
        src.RasterXSize,
        src.RasterYSize,
        src.RasterCount,
        src.GetRasterBand(1).DataType,
        options=cog_options
    )

    out_transform = (T_new.c,T_new.a,T_new.b,T_new.f,T_new.d,T_new.e,)

    out.SetGeoTransform(out_transform)
    out.SetProjection(src.GetProjection())

    for i in range(1, src.RasterCount + 1):
        out.GetRasterBand(i).WriteArray(src.GetRasterBand(i).ReadAsArray())

    out.FlushCache()

    src = None
    out = None

    log.info("Aligned raster written to %s", output_path)

    return {
        "inliers": inliers,
        "total": total,
        "rmse_px": rmse,
        "mean_error": mean_error,
        "median_error": median_error,
        "scale": scale,
    }