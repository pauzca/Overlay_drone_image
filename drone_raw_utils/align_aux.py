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

def crop_square_fast(input_raster, center_x, center_y, half):
    """
    Crop a square region around center point.
    half = size in METERS
    """
    ds = gdal.Open(input_raster)
    if ds is None:
        raise FileNotFoundError(f"Could not open {input_raster}")
    
    try:
        gt = ds.GetGeoTransform()
        raster_width = ds.RasterXSize
        raster_height = ds.RasterYSize
        
        # Get pixel size
        pixel_size_x = abs(gt[1])
        pixel_size_y = abs(gt[5])
        pixel_size = max(pixel_size_x, pixel_size_y)  # Use the larger, or average
        
        # Convert half (meters) to pixels
        half_pixels = int(half / pixel_size)
        
        # Get center pixel location
        inv_gt = gdal.InvGeoTransform(gt)
        if not inv_gt:
            raise RuntimeError("Could not invert the raster's geotransform matrix.")
        
        # Convert center coordinates to pixel (row, col)
        col = int(inv_gt[0] + center_x * inv_gt[1] + center_y * inv_gt[2])
        row = int(inv_gt[3] + center_x * inv_gt[4] + center_y * inv_gt[5])
        
        # Define window (same as rasterio version)
        min_col = max(0, col - half_pixels)
        max_col = min(raster_width, col + half_pixels)
        min_row = max(0, row - half_pixels)
        max_row = min(raster_height, row + half_pixels)
        
        x_offset = min_col
        y_offset = min_row
        x_size = max_col - min_col
        y_size = max_row - min_row
        
        if x_size <= 0 or y_size <= 0:
            raise ValueError("The requested crop window falls entirely outside the image bounds.")
        
        # Read the pixel data
        data = ds.ReadAsArray(x_offset, y_offset, x_size, y_size)
        
        # Recompute the top-left origin based on the pixel offset shift
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
def align_to_ortho(
    orthoprojected_path,
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

    center_x, center_y = get_raster_center(orthoprojected_path)
    print("Raw image center: %.2f, %.2f", center_x, center_y)

    # 1. Crop matching regions
    projected_crop, projected_transform, projected_profile = crop_square_fast(orthoprojected_path, center_x, center_y, crop_size)
    ortho_crop, ortho_transform, ortho_profile = crop_square_fast(ortho_path, center_x, center_y, crop_size)

    # 2. Resample Phase One to ortho grid
    projected_ds = gdal.Open(str(orthoprojected_path))
    projected_crs = projected_ds.GetProjection()
    projected_ds = None

    projected_resampled = resample_to_reference(projected_crop,projected_transform,projected_crs,ortho_profile)

    # 3. Grayscale
    projected_gray = raster_to_grayscale(projected_resampled)
    ortho_gray = raster_to_grayscale(ortho_crop)

    projected = projected_gray

    if do_histogram_matching:
        projected = match_histograms(projected, ortho_gray)

    # 4. Blur
    projected = cv2.GaussianBlur(projected, (21, 21), 0)
    ortho = cv2.GaussianBlur(ortho_gray, (21, 21), 0)

    # 5. SIFT + BF matching
    sift = cv2.SIFT_create()

    kp1, des1 = sift.detectAndCompute(to_uint8(projected), None)
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
    src = gdal.Open(str(orthoprojected_path))

    orig_transform = src.GetGeoTransform()

    
    T_orig = Affine(
        orig_transform[1],orig_transform[2],orig_transform[0],
        orig_transform[4],orig_transform[5],orig_transform[3],
    )

    print(T_orig)

    G = Affine(ortho_transform[1],ortho_transform[2],ortho_transform[0],
        ortho_transform[4],ortho_transform[5],ortho_transform[3],
    )
    print(G)
    print(M_affine)

    T_correction = G * M_affine * (~G)
    T_new = T_correction * T_orig


    os.makedirs(os.path.dirname(os.path.abspath(output_path)),exist_ok=True)

    out_transform = (T_new.c,T_new.a,T_new.b,T_new.f,T_new.d,T_new.e,)

    #create_cog_directly_from_memory(src=src, output_path=output_path, transform=out_transform, projection=src.GetProjection())

    
    save_aligned_normal(
    src=src,
    output_path=output_path,
    transform=out_transform,
    projection=src.GetProjection(),
    )

    src = None

    log.info("Aligned raster written to %s", output_path)

    return {
        "inliers": inliers,
        "total": total,
        "rmse_px": rmse,
        "mean_error": mean_error,
        "median_error": median_error,
        "scale": scale,
    }


def save_aligned_normal(src, output_path, transform, projection):
    """
    Save a GDAL dataset as a tiled, JPEG-compressed GeoTIFF
    with an updated geotransform, optimized for fast visualization.
    """

    driver = gdal.GetDriverByName("GTiff")

    options = [
        "COMPRESS=JPEG",
        "JPEG_QUALITY=90",
        "PHOTOMETRIC=YCBCR",
        "TILED=YES",
        "BLOCKXSIZE=512",
        "BLOCKYSIZE=512",
        "BIGTIFF=IF_SAFER",
        "NUM_THREADS=ALL_CPUS",
    ]

    out_ds = driver.CreateCopy(
        str(output_path),
        src,
        options=options,
    )

    if out_ds is None:
        raise RuntimeError(
            f"GDAL failed to create {output_path}"
        )

    try:
        # Update georeferencing
        out_ds.SetGeoTransform(transform)
        out_ds.SetProjection(projection)

        out_ds.FlushCache()

        # Build internal pyramids for fast visualization
        out_ds.BuildOverviews(
            "AVERAGE",
            [2, 4, 8, 16, 32, 64],
        )

        out_ds.FlushCache()

    finally:
        out_ds = None

import os


def create_cog_directly_from_memory(src, output_path, transform, projection):
    """Create COG directly from memory dataset without intermediate file."""
    
    # Ensure output directory exists
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    
    # Create an in-memory dataset with the correct transform
    mem_driver = gdal.GetDriverByName("MEM")
    mem_ds = mem_driver.Create(
        "",
        src.RasterXSize,
        src.RasterYSize,
        src.RasterCount,
        src.GetRasterBand(1).DataType
    )
    
    # Set transform and projection
    mem_ds.SetGeoTransform(transform)
    mem_ds.SetProjection(projection)
    
    # Copy data from source to memory dataset
    for i in range(1, src.RasterCount + 1):
        data = src.GetRasterBand(i).ReadAsArray()
        mem_ds.GetRasterBand(i).WriteArray(data)
    
    # Create COG directly from memory dataset
    cog_driver = gdal.GetDriverByName("COG")
    cog_options = [
        "COMPRESS=JPEG",
        "QUALITY=90",
        "BLOCKSIZE=512",
        "OVERVIEWS=AUTO",
        "NUM_THREADS=ALL_CPUS"
    ]
    
    # Create COG - this writes directly to disk, no intermediate file needed
    out = cog_driver.CreateCopy(
        str(output_path),
        mem_ds,
        options=cog_options
    )
    
    # Clean up
    out = None
    mem_ds = None
    src = None
    
    print("COG raster written directly to %s", output_path)
    
    return output_path