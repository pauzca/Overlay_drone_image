"""Find the best Phase One image for a given target coordinate.

Copied from closeup_ortho.find_phaseone and adapted for standalone use inside the
phaseone_image QGIS plugin package.
"""

from pathlib import Path
import shutil

import pandas as pd
from pyproj import Transformer
from scipy.spatial import KDTree
import xml.etree.ElementTree as ET
from .MetadataReader import BaseMetadataReader


## add the function to create the coordinates file


def _build_image_tree(csv_file, epsg="EPSG:32617"):
    """Read image GPS CSV and build spatial index."""
    df = pd.read_csv(csv_file)

    print(csv_file)

    transformer = Transformer.from_crs("EPSG:4326", epsg, always_xy=True)
    xs, ys = transformer.transform(df["longitude"].values, df["latitude"].values)

    df["x"] = xs
    df["y"] = ys

    print(df.head())
    tree = KDTree(list(zip(xs, ys)))
    return tree, df


def _find_closest_images(tree, df, x, y, n_images=30, radius=100):
    """Find closest image positions within radius."""
    indices = tree.query_ball_point([x, y], r=radius)

    results = []
    for index in indices:
        row = df.iloc[index]
        distance = ((row["x"] - x) ** 2 + (row["y"] - y) ** 2) ** 0.5
        results.append({"filename": row["filename"], "distance_m": distance})

    results.sort(key=lambda r: r["distance_m"])
    return results[:n_images]



def _select_most_nadir(images, image_folder, metadata_reader):
    """Select image with smallest deviation from nadir."""
    best = None
    best_dev = float("inf")

    for img in images:

        photo_meta = metadata_reader.read(Path(image_folder) / img["filename"])

        pitch = photo_meta.pitch
        roll = photo_meta.roll

        if pitch is None or roll is None:
            continue

        pitch_dev = abs(pitch + 90)
        roll_dev = abs(roll)
        dev = max(pitch_dev, roll_dev)

        img["nadir_dev"] = dev

        if dev < best_dev:
            best_dev = dev
            best = img

    return best


def find_best_raw_drone_image(
    csv_file,
    image_folder,
    output_folder,
    target_x,
    target_y,
    epsg="EPSG:32617",
    n_images=5,
    radius=20,
    metadata_reader=BaseMetadataReader,
) -> Path:
    """Find and copy the best Raw Drone Image for a target coordinate.

    The selection process:
        1. Find closest images by GPS position.
        2. Check camera pitch/roll.
        3. Select image closest to nadir.
        4. Copy image to output folder.

    Parameters
    ----------
    csv_file : str or Path
        CSV with columns ``filename``, ``longitude``, ``latitude``.
    image_folder : str or Path
        Folder containing the Phase One JPEG images.
    output_folder : str or Path
        Destination folder for the copied image.
    target_x, target_y : float
        Target coordinate in the CRS defined by ``epsg``.
    epsg : str
        EPSG code of the projected CRS for spatial search (default: EPSG:32617).
    n_images : int
        Maximum number of candidate images to evaluate.
    radius : float
        Search radius in metres.

    Returns
    -------
    Path
        Path to the copied JPEG in ``output_folder``.
    """
    image_folder = Path(image_folder)
    output_folder = Path(output_folder)

    tree, df = _build_image_tree(csv_file, epsg)
    candidates = _find_closest_images(tree, df, target_x, target_y, n_images, radius)

    if not candidates:
        raise RuntimeError("No images found near target coordinate")

    best = _select_most_nadir(candidates, image_folder, metadata_reader)
    if best is None:
        raise RuntimeError("Could not determine nadir image")

    output_folder.mkdir(parents=True, exist_ok=True)

    src = image_folder / best["filename"]
    dst = output_folder / best["filename"]
    shutil.copy2(src, dst)

    return dst
