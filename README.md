# PhaseOne Image Align — QGIS Plugin

Orthorectify and align a Phase One aerial image to a reference orthomosaic and DSM directly inside QGIS.

---

## Folder Structure

```
overlay_raw_drone_image/       ← Plugin root (install this whole folder)
├─ __init__.py                 ← QGIS plugin entry point (classFactory)
├─ metadata.txt                ← Plugin metadata
├─ plugin.py                   ← Plugin class (toolbar / menu)
├─ dialog.py                   ← UI dialog
├─ icons/
│   └─ plugin.png              ← Toolbar icon
└─ phaseone_image/             ← Copied processing helpers
    ├─ __init__.py
    ├─ dsm.py                  ← DSM raster accessor
    ├─ geometry.py             ← Footprint & geotiff affine helpers
    ├─ metadata.py             ← Phase One XMP / EXIF reader
    ├─ orthorectify.py         ← Single-image orthorectification
    ├─ find_phaseone.py        ← Nearest-nadir image finder
    └─ align_aux.py            ← SIFT alignment to orthomosaic
```

---

## Installation

1. **Copy the folder** `overlay_raw_drone_image` into your QGIS plugins directory:
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`
2. **Install Python dependencies** (in the OSGeo4W shell or QGIS Python console):
   ```
   pip install geopandas pillow numpy scipy pyproj scikit-image opencv-python-headless affine shapely
   ```
3. In QGIS, go to **Plugins → Manage and Install Plugins**, find **PhaseOne Image Align**, and enable it.

---

## Usage

1. Load your reference **Orthomosaic** and **DSM** rasters into QGIS.
2. Click the **PhaseOne Image Align** toolbar button (or use the Plugins menu).
3. In the dialog:
   - Select the **Reference Orthomosaic** and **Reference DSM** layers.
   - Browse to the **Phase One GPS coordinates CSV** (`phaseone_coordinates.csv`).
     - Required columns: `filename`, `longitude`, `latitude`.
   - Browse to the **Phase One image folder** containing the raw JPEGs.
   - Click **"Pick point on map"** and click the target location in the canvas to set `target_x` / `target_y`, or type them manually.
   - Set the **output GeoTIFF path** for the aligned image.
4. Click **Run**.
5. The aligned raster is automatically added to the QGIS Layers panel.

> **Note**: Intermediate files (copied JPEG, orthorectified GeoTIFF) are written to a temporary folder and are not saved permanently.

---

## Pipeline Steps

| Step | Description |
|------|-------------|
| **1 – Find image** | `find_phaseone.py` queries the GPS CSV for the closest Phase One image to the target point (within 20 m radius) and selects the most-nadir frame. |
| **2 – Orthorectify** | `orthorectify.py` uses Phase One XMP metadata (pitch, roll, yaw, GPS altitude) and the DSM to project the image onto the ground, writing a GeoTIFF. |
| **3 – Align** | `align_aux.py` uses SIFT feature matching + RANSAC to compute a sub-pixel affine correction between the orthorectified image and the reference orthomosaic, then writes the final aligned GeoTIFF. |
| **4 – Load** | The aligned GeoTIFF is loaded as a raster layer in QGIS. |

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `rasterio` | Raster I/O |
| `geopandas` | GeoDataFrame for footprints |
| `numpy` | Array operations |
| `Pillow` | JPEG reading / EXIF |
| `pyproj` | CRS transformations |
| `scipy` | KDTree spatial index |
| `scikit-image` | Histogram matching |
| `opencv-python-headless` | SIFT, RANSAC |
| `affine` | Affine transform arithmetic |
| `shapely` | Footprint polygons |

---

## Troubleshooting

| Problem | Solution |
|---------|---------|
| "No Phase One images found near target coordinate" | Increase the search radius in `find_phaseone.py` (`radius` param), or check that the target point CRS matches the DSM CRS. |
| "Missing Phase One metadata" | Ensure images contain valid XMP (`Yaw`, `Pitch`, `Roll`, `GPSLatitude`, `GPSLongitude`, `GPSAltitude`, `DIST_F`). |
| Affine scale error (>10%) | SIFT found poor matches; try adjusting `crop_size` in the dialog or add more texture to the target area. |
| Layer not loading | Open the output GeoTIFF manually via **Layer → Add Raster Layer**. |
