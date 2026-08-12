# Align Raw Drone Images — QGIS Plugin

Orthoproject and align raw drone images to a reference orthomosaic and DSM directly inside QGIS.

---

## Folder Structure

```
overlay_raw_drone_image/       ← Plugin root (install this whole folder in the QGIS plugins folder)
├─ __init__.py                 ← QGIS plugin entry point 
├─ metadata.txt                ← Plugin metadata
├─ plugin.py                   ← Plugin class (toolbar / menu)
├─ dialog.py                   ← UI dialog
├─ icons/
│   └─ plugin.png              ← Toolbar icon
└─ drone_raw_utils/            ← Helper functions to process the drone imagery
    ├─ __init__.py
    ├─ dsm.py                  ← DSM raster accessor
    ├─ geometry.py             ← Footprint & geotiff affine helpers
    ├─ metadata.py             ← Raw Drone Image XMP / EXIF reader
    ├─ orthorectify.py         ← Single-image orthorectification
    ├─ find_phaseone.py        ← Nearest-nadir image finder
    └─ align_aux.py            ← SIFT alignment to orthomosaic
```

---

## Installation

1. **Copy the folder** `overlay_raw_drone_image` into your QGIS plugins directory:
   - Windows: `%APPDATA%\QGIS\QGIS3\profiles\default\python\plugins\`

2. **Install Python dependencies** (in the OSGeo4W shell or QGIS Python console):
    ( i modified this so that the dependencies will be installe automatically but in case it doesn't work they can be installed manually in the QGIS python console)
   ```
   pip install geopandas pillow numpy scipy pyproj scikit-image opencv-python-headless affine shapely
   ```

4. In QGIS, go to **Plugins → Manage and Install Plugins**, find **Overlay Raw Drone Image**, and enable it.

---

## Usage

1. Load your reference **Orthomosaic** and **DSM** rasters into QGIS.
2. Click the **Overlay Drone Image** toolbar button (or use the Plugins menu).
3. In the window docked to the left:
   - Select the **Reference Orthomosaic** and **Reference DSM** layers.
   - Browse to the **Image folder** containing the raw JPEGs.
   - Select the drone being used: DJI Mavic 3, Trinity + sony camera, Trinity + phase one camera
   - Click **"Pick point on map"** and click the target location in the canvas to set `target_x` / `target_y`, or type them manually.
   - Set the **Output path** for the projected and aligned images, the projected can be deleted later.
   - You can modify the other parameters or leave default
     
4. Click **Run**.
5. The aligned raster is automatically added to the QGIS Layers panel.

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
| "No images found near target coordinate" | Increase the search radius in `find_phaseone.py` (`radius` param), or check that the target point CRS matches the DSM CRS. |
| "Missing metadata" | Ensure you picked the right drone model, or modify the plugin to read the images contain valid XMP (`Yaw`, `Pitch`, `Roll`, `GPSLatitude`, `GPSLongitude`, `GPSAltitude`, `DIST_F`). |
| Could not align image: the orthoprojected image will get loaded, but the problem is likely due to a big difference between the orthomosaic and the raw images, try to find an orthomosaic with the closes date possible to the raw images |

