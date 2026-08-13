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
from .metadata import Basemetadata, DJImetadata, PhaseOnemetadata, TrinityMetadata


## add the function to create the coordinates file

# Registry matching the QComboBox UI selections
READER_REGISTRY: dict[str, Basemetadata] = {
    "Phase One": PhaseOnemetadata(),
    "DJI Mavic 3": DJImetadata(),
    "Trinity": TrinityMetadata(),
}


def get_metadata_reader(drone_model_name: str) -> Basemetadata:
    """Returns the matching metadata reader, defaulting to Phase One if unmapped."""
    return READER_REGISTRY.get(drone_model_name, PhaseOnemetadata())



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
        print(photo_meta)
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
    target_x,
    target_y,
    epsg="EPSG:32617",
    n_images=5,
    radius=20,
    metadata_reader=Basemetadata,
) -> tuple[list, Path]: 
    """Find candidate drone images and select the most nadir image.

    The selection process:
        1. Find closest images by GPS position.
        2. Check camera pitch/roll.
        3. Select image closest to nadir.
        4. Return the path to the original image.

    Parameters
    ----------
    csv_file : str or Path
        CSV with columns ``filename``, ``longitude``, ``latitude``.
    image_folder : str or Path
        Folder containing the Phase One JPEG images.
    target_x, target_y : float
        Target coordinate in the CRS defined by ``epsg``.
    epsg : str
        EPSG code of the projected CRS for spatial search
        (default: EPSG:32617).
    n_images : int
        Maximum number of candidate images to evaluate.
    radius : float
        Search radius in metres.

    Returns
    -------
    Path
        Path to the selected JPEG in ``image_folder``.
    """
    image_folder = Path(image_folder)

    tree, df = _build_image_tree(csv_file, epsg)
    candidates = _find_closest_images(
        tree, df, target_x, target_y, n_images, radius
    )
    print(candidates)
    if not candidates:
        raise RuntimeError("No images found near target coordinate")

    best = _select_most_nadir(
        candidates, image_folder, metadata_reader
    )

    print(best)
    if best is None:
        raise RuntimeError("Could not determine nadir image")

    # the best one is the closes for now
    best = candidates[0]

    return candidates, best

