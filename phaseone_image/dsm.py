"""DSM access: open once, transform camera coordinates, sample max elevation in a buffer.

Adapted for standalone use inside the phaseone_image QGIS plugin package.
"""

from __future__ import annotations

import math

import numpy as np
from pyproj import Transformer
from osgeo import gdal, osr


class DSM:
    """Wraps an open DSM raster and the WGS84 -> DSM-CRS transformer."""

    def __init__(self, path):
        self._ds = gdal.Open(str(path), gdal.GA_ReadOnly)

        if self._ds is None:
            raise RuntimeError(f"Could not open DSM: {path}")

        self.width = self._ds.RasterXSize
        self.height = self._ds.RasterYSize

        self._band = self._ds.GetRasterBand(1)
        self.nodata = self._band.GetNoDataValue()

        self.transform = self._ds.GetGeoTransform()

        self.res_x = abs(self.transform[1])
        self.res_y = abs(self.transform[5])

        self.crs = self._get_crs()

        self.bounds = self._get_bounds()

        self._tf = Transformer.from_crs(
            "EPSG:4326",
            self.crs,
            always_xy=True
        )


    def _get_crs(self):
        wkt = self._ds.GetProjection()

        srs = osr.SpatialReference()
        srs.ImportFromWkt(wkt)

        return srs.ExportToWkt()


    def _get_bounds(self):
        gt = self.transform

        left = gt[0]
        top = gt[3]

        right = left + self.width * gt[1]
        bottom = top + self.height * gt[5]

        return type(
            "Bounds",
            (),
            {
                "left": min(left, right),
                "right": max(left, right),
                "bottom": min(bottom, top),
                "top": max(bottom, top),
            },
        )()


    def close(self):
        self._ds = None


    def __enter__(self):
        return self


    def __exit__(self, *exc):
        self.close()


    def lonlat_to_xy(self, lon: float, lat: float):
        """Project WGS84 lon/lat to DSM CRS."""
        return self._tf.transform(lon, lat)


    def contains(self, x: float, y: float):
        b = self.bounds
        return b.left <= x <= b.right and b.bottom <= y <= b.top


    def xy_to_pixel(self, x, y):
        """Convert map coordinates to raster row/column."""
        gt = self.transform

        col = int((x - gt[0]) / gt[1])
        row = int((y - gt[3]) / gt[5])

        return row, col


    def max_elevation(self, x: float, y: float, radius_m: float):
        """Max elevation within radius ignoring nodata."""

        if not self.contains(x, y):
            return None

        row, col = self.xy_to_pixel(x, y)

        r_px = max(0, math.ceil(radius_m / self.res_x))

        xoff = max(0, col - r_px)
        yoff = max(0, row - r_px)

        xsize = min(
            2 * r_px + 1,
            self.width - xoff
        )

        ysize = min(
            2 * r_px + 1,
            self.height - yoff
        )

        if xsize <= 0 or ysize <= 0:
            return None


        arr = self._band.ReadAsArray(
            xoff,
            yoff,
            xsize,
            ysize
        ).astype("float64")


        if self.nodata is not None:
            arr[arr == self.nodata] = np.nan


        if np.all(np.isnan(arr)):
            return None


        return float(np.nanmax(arr))