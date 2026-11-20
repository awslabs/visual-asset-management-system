"""Regenerates the small GeoTIFF fixtures the cogRaster tests read (run from this directory).

    uv venv -p 3.13 venv && uv pip install -p venv rasterio numpy && venv/bin/python generate_fixtures.py

Every file is at most 256 x 256 pixels. rasterio's bundled GDAL writes them; nothing here is needed at
build or run time, only to regenerate the committed .tif files.
"""
import numpy as np
import rasterio
from rasterio.shutil import copy as rcopy
from rasterio.transform import from_origin


def grid(h, w, count=1, dtype="uint8", scale=255):
    y, x = np.mgrid[0:h, 0:w]
    base = ((x / max(w - 1, 1)) * 0.6 + (y / max(h - 1, 1)) * 0.4)
    return np.stack([(np.clip(base + i * 0.1, 0, 1) * scale).astype(dtype) for i in range(count)])


def write(path, data, crs, transform, nodata=None, **profile):
    count, h, w = data.shape
    with rasterio.open(path, "w", driver="GTiff", width=w, height=h, count=count, dtype=str(data.dtype),
                       crs=crs, transform=transform, nodata=nodata, **profile) as dst:
        dst.write(data)


# Plain striped RGB in WGS84, about 7 km across.
write("wgs84-striped.tif", grid(48, 64, 3), "EPSG:4326", from_origin(10.0, 50.0, 0.001, 0.001), compress="DEFLATE")

# Cloud-optimized, EPSG:3857, single band (GDAL COG driver reprojects to the Google tile scheme).
write("src-3857.tmp.tif", grid(256, 256, 1), "EPSG:4326", from_origin(10.0, 50.0, 0.0005, 0.0005))
rcopy("src-3857.tmp.tif", "cog-3857.tif", driver="COG", TILING_SCHEME="GoogleMapsCompatible", COMPRESS="DEFLATE")

# A 3857 file that is NOT a COG (striped).
with rasterio.open("cog-3857.tif") as s:
    write("striped-3857.tif", s.read(), s.crs, s.transform, compress="DEFLATE")

# Cloud-optimized but WGS84 (what GDAL writes without a tiling scheme).
rcopy("src-3857.tmp.tif", "cog-4326.tif", driver="COG", COMPRESS="DEFLATE")

# UTM zone 33N, float32 with a nodata hole.
f = grid(64, 64, 1, "float32", 1.0) * 120.0 - 10.0
f[0, :8, :8] = -9999.0
write("utm33-float-nodata.tif", f, "EPSG:32633", from_origin(500000.0, 5000000.0, 30.0, 30.0), nodata=-9999.0,
      compress="DEFLATE")

# UTM zone 17S, 16-bit single band.
write("utm17s-uint16.tif", grid(64, 64, 1, "uint16", 4000), "EPSG:32717",
      from_origin(300000.0, 9000000.0, 30.0, 30.0), compress="LZW")

# Swiss LV95 (not bundled, so the viewer must report an unsupported CRS).
write("unsupported-2056.tif", grid(32, 32, 1), "EPSG:2056", from_origin(2600000.0, 1200000.0, 10.0, 10.0))

# WGS84 but 2 degrees across, over the placed-image extent cap.
write("wgs84-wide.tif", grid(32, 32, 1), "EPSG:4326", from_origin(10.0, 50.0, 0.0625, 0.0625))

# No georeference at all.
write("no-georeference.tif", grid(32, 32, 1), None, rasterio.Affine.identity())

# JPEG in YCbCr, NAD83 / UTM 13N (the reader must convert it to RGB). Pure red, so a YCbCr read shows up.
red = np.zeros((3, 64, 64), dtype="uint8")
red[0] = 220
write("utm13-jpeg-ycbcr.tif", red, "EPSG:26913", from_origin(500000.0, 4300000.0, 10.0, 10.0),
      compress="JPEG", photometric="YCBCR", tiled=True, blockxsize=64, blockysize=64)

import os

# Pixel bomb: 11000 x 10000 (110 Mpx, over the placed path's decode cap), striped, no overviews, a tiny
# WGS84 extent. Deflated zeros keep it small on disk; decoding it would take the full 110 MB.
with rasterio.open("pixel-bomb-4326.tif", "w", driver="GTiff", width=11000, height=10000, count=1,
                   dtype="uint8", crs="EPSG:4326", transform=from_origin(10.0, 50.0, 1e-6, 1e-6),
                   compress="DEFLATE", blockysize=64) as dst:
    for row in range(0, 10000, 1000):
        dst.write(np.zeros((1, 1000, 11000), dtype="uint8"), window=((row, row + 1000), (0, 11000)))
os.remove("src-3857.tmp.tif")
