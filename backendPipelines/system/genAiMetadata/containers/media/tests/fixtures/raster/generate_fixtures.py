# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Generate the raster fixture set and its `.expected.json` sidecars with GDAL.

Run it (from this directory) in the pinned GDAL image; nothing else is needed on the host:

    docker run --rm -v "$PWD":/work -w /work \
      ghcr.io/osgeo/gdal:ubuntu-small-latest@sha256:580b608eccdfb8dfdfc9a819e642855d4c155b3ad7c49809e959aa93c62ccd34 \
      python3 generate_fixtures.py

Every file is tiny (zero-filled or a low-entropy gradient, so a 1024 x 1024 file is a few kilobytes). The
sidecar states what a correct extractor reports for the file: layout facts, `isCog` (the header-only definition in
`media_extractors/raster.py`), the CRS, and the WGS84 bounds GDAL itself computes (`wgs84Extent`) as an independent
oracle for the reprojection helper. The expected values that GDAL does not decide (isCog, the antimeridian split, a
polar extent) are stated here by hand. The same fixture set and sidecars are copied, unchanged, into the viewer and
pipeline cards' test trees (the shared contract), so a change here is a change to all three.
"""

import json
import os

from osgeo import gdal, osr

gdal.UseExceptions()
OUT = os.path.dirname(os.path.abspath(__file__))

USER_DEFINED_LAEA = "+proj=laea +lat_0=40 +lon_0=-100 +x_0=0 +y_0=0 +ellps=GRS80 +units=m +no_defs"
DTYPES = {gdal.GDT_Byte: "uint8", gdal.GDT_UInt16: "uint16", gdal.GDT_Float32: "float32"}


def srs_for(spec):
    if spec is None:
        return None
    srs = osr.SpatialReference()
    srs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    if isinstance(spec, int):
        srs.ImportFromEPSG(spec)
    else:
        srs.ImportFromProj4(spec)
    return srs


def make_source(path, width, height, bands, dtype, srs, gt, nodata=None, palette=False, point=False, options=()):
    driver = gdal.GetDriverByName("GTiff")
    ds = driver.Create(path, width, height, bands, dtype, options=list(options))
    if srs is not None:
        ds.SetProjection(srs.ExportToWkt())
    if gt is not None:
        ds.SetGeoTransform(gt)
    row = bytes((x * 255 // max(1, width - 1)) for x in range(width))
    for band in range(1, bands + 1):
        b = ds.GetRasterBand(band)
        if nodata is not None:
            b.SetNoDataValue(nodata)
        if palette:
            # Set before any pixels are written: GDAL cannot change the photometric interpretation of a
            # GTiff that already holds data.
            table = gdal.ColorTable()
            for i in range(256):
                table.SetColorEntry(i, (i, 255 - i, 128, 255))
            b.SetRasterColorTable(table)
            b.SetRasterColorInterpretation(gdal.GCI_PaletteIndex)
        if dtype == gdal.GDT_Byte:
            b.WriteRaster(0, 0, width, height, row * height)
    if point:
        ds.SetMetadataItem("AREA_OR_POINT", "POINT")
    ds.FlushCache()
    return ds


# name: (width, height, bands, dtype, srs, geotransform, builder options, expected hand-stated facts)
# builder: "cog" (COG driver), "gtiff" (GTiff driver, translated), "direct" (GTiff driver, written in place)
CASES = [
    # --- layouts ---
    ("cog_4326", dict(w=1024, h=1024, srs=4326, gt=(-122.5, 0.0005, 0, 38.0, 0, -0.0005), kind="cog",
                      options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]), dict(isCog=True)),
    ("cog_small_4326", dict(w=256, h=256, srs=4326, gt=(10.0, 0.001, 0, 50.0, 0, -0.001), kind="cog",
                            options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]), dict(isCog=True)),
    ("cog_rgb_4326", dict(w=256, h=256, bands=3, srs=4326, gt=(-70.0, 0.001, 0, 44.0, 0, -0.001), kind="cog",
                          options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]), dict(isCog=True)),
    ("cog_bigtiff_4326", dict(w=256, h=256, srs=4326, gt=(20.0, 0.001, 0, 30.0, 0, -0.001), kind="cog",
                              options=["COMPRESS=DEFLATE", "BLOCKSIZE=128", "BIGTIFF=YES"]), dict(isCog=True)),
    ("striped_4326", dict(w=256, h=256, srs=4326, gt=(5.0, 0.001, 0, 52.0, 0, -0.001), kind="gtiff",
                          options=["COMPRESS=NONE"]), dict(isCog=False)),
    # Tiled, 256 x 256 (no overview needed) and written directory-first: a COG by the header-only definition.
    ("tiled_no_overviews_4326", dict(w=256, h=256, srs=4326, gt=(6.0, 0.001, 0, 53.0, 0, -0.001), kind="direct",
                                     options=["TILED=YES", "BLOCKXSIZE=128", "BLOCKYSIZE=128", "COMPRESS=DEFLATE"]),
     dict(isCog=True)),
    ("tiled_big_no_overviews_4326", dict(w=1024, h=1024, srs=4326, gt=(7.0, 0.0005, 0, 54.0, 0, -0.0005),
                                         kind="gtiff", options=["TILED=YES", "BLOCKXSIZE=256", "BLOCKYSIZE=256",
                                                                "COMPRESS=DEFLATE"]), dict(isCog=False)),
    # --- coordinate reference systems ---
    ("epsg3857", dict(w=256, h=256, srs=3857, gt=(-13630000.0, 100.0, 0, 4570000.0, 0, -100.0), kind="cog",
                      options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]), dict(isCog=True)),
    ("utm33", dict(w=256, h=256, srs=32633, gt=(500000.0, 30.0, 0, 5000000.0, 0, -30.0), kind="cog",
                   options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]), dict(isCog=True)),
    ("polar_3031_offpole", dict(w=256, h=256, srs=3031, gt=(1000000.0, 1000.0, 0, -1000000.0, 0, -1000.0),
                                kind="cog", options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]), dict(isCog=True)),
    ("polar_3031_pole", dict(w=256, h=256, srs=3031, gt=(-128000.0, 1000.0, 0, 128000.0, 0, -1000.0), kind="cog",
                             options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]),
     dict(isCog=True, footprint=None, footprintNote="contains-pole")),
    ("antimeridian_3832", dict(w=256, h=256, srs=3832, gt=(3200000.0, 1000.0, 0, 1000000.0, 0, -1000.0),
                               kind="cog", options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]),
     dict(isCog=True, footprint="MultiPolygon")),
    ("rotated_utm33", dict(w=256, h=256, srs=32633, gt=(500000.0, 25.0, 12.0, 5000000.0, 12.0, -25.0),
                           kind="direct", options=["TILED=YES", "BLOCKXSIZE=128", "BLOCKYSIZE=128"]),
     dict(isCog=True, rotated=True)),
    ("user_defined_laea", dict(w=256, h=256, srs=USER_DEFINED_LAEA, gt=(-100000.0, 100.0, 0, 100000.0, 0, -100.0),
                               kind="cog", options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]),
     dict(isCog=True, crs="user-defined", footprint=None, footprintNote="user-defined-crs")),
    ("no_georef", dict(w=256, h=256, srs=None, gt=None, kind="cog", options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]),
     dict(isCog=True, crs="none", georeferenced=False, footprint=None, footprintNote="not-georeferenced")),
    # Written in place, so the directory follows the pixel data: tiled but not COG-ordered.
    ("pixel_is_point_4326", dict(w=256, h=256, srs=4326, gt=(30.0, 0.01, 0, 20.0, 0, -0.01), kind="direct",
                                 point=True, options=["TILED=YES", "BLOCKXSIZE=128", "BLOCKYSIZE=128"]),
     dict(isCog=False, pixelIsPoint=True)),
    # --- sample types ---
    ("nodata_float32_4326", dict(w=256, h=256, dtype=gdal.GDT_Float32, nodata=-9999.0, srs=4326,
                                 gt=(40.0, 0.001, 0, 10.0, 0, -0.001), kind="cog",
                                 options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]), dict(isCog=True)),
    ("palette_4326", dict(w=256, h=256, palette=True, srs=4326, gt=(41.0, 0.001, 0, 11.0, 0, -0.001), kind="cog",
                          options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]), dict(isCog=True)),
    ("uint16_4326", dict(w=256, h=256, dtype=gdal.GDT_UInt16, srs=4326, gt=(42.0, 0.001, 0, 12.0, 0, -0.001),
                         kind="cog", options=["COMPRESS=DEFLATE", "BLOCKSIZE=128"]), dict(isCog=True)),
    # --- compressions ---
    ("compress_lzw", dict(w=256, h=256, srs=4326, gt=(50.0, 0.001, 0, 13.0, 0, -0.001), kind="gtiff",
                          options=["COMPRESS=LZW"]), dict(isCog=False)),
    ("compress_deflate", dict(w=256, h=256, srs=4326, gt=(51.0, 0.001, 0, 13.0, 0, -0.001), kind="gtiff",
                              options=["COMPRESS=DEFLATE"]), dict(isCog=False)),
    ("compress_zstd", dict(w=256, h=256, srs=4326, gt=(52.0, 0.001, 0, 13.0, 0, -0.001), kind="gtiff",
                           options=["COMPRESS=ZSTD"]), dict(isCog=False)),
    ("compress_jpeg", dict(w=256, h=256, srs=4326, gt=(53.0, 0.001, 0, 13.0, 0, -0.001), kind="gtiff",
                           options=["COMPRESS=JPEG"]), dict(isCog=False)),
]


def build(name, spec, hand):
    width, height = spec["w"], spec["h"]
    bands = spec.get("bands", 1)
    dtype = spec.get("dtype", gdal.GDT_Byte)
    srs = srs_for(spec["srs"])
    path = os.path.join(OUT, name + ".tif")
    scratch = os.path.join(OUT, name + ".src.tif")
    if spec["kind"] == "direct":
        # Written straight to its final name: GDAL appends the directory after the pixel data, so the file is a
        # tiled GeoTIFF that is not laid out as a COG.
        ds = make_source(path, width, height, bands, dtype, srs, spec["gt"], spec.get("nodata"),
                         spec.get("palette", False), spec.get("point", False), spec["options"])
        ds = None
    else:
        src = make_source(scratch, width, height, bands, dtype, srs, spec["gt"], spec.get("nodata"),
                          spec.get("palette", False), spec.get("point", False))
        if spec["kind"] == "cog":
            gdal.Translate(path, src, format="COG", creationOptions=spec["options"])
        else:
            gdal.Translate(path, src, format="GTiff", creationOptions=spec["options"])
        src = None
        os.remove(scratch)
    for leftover in (path + ".aux.xml",):
        if os.path.exists(leftover):
            os.remove(leftover)
    info = gdal.Info(path, format="json")
    ds = gdal.Open(path)
    band = ds.GetRasterBand(1)
    block = band.GetBlockSize()
    layout = "tiled" if spec["kind"] == "cog" or "TILED=YES" in spec["options"] else "striped"
    compression = (ds.GetMetadata("IMAGE_STRUCTURE").get("COMPRESSION") or "NONE").lower()
    crs = hand.get("crs")
    if crs is None:
        code = info.get("stac", {}).get("proj:epsg") if spec["srs"] is not None else None
        crs = "EPSG:%d" % code if code else ("none" if spec["srs"] is None else "user-defined")
    extent = (info.get("wgs84Extent") or {}).get("coordinates")
    wgs84 = None
    if extent and "footprint" not in hand:
        ring = extent[0]
        lons = [p[0] for p in ring]
        lats = [p[1] for p in ring]
        wgs84 = [min(lons), min(lats), max(lons), max(lats)]
    gt = spec["gt"]
    expected = {
        "file": name + ".tif",
        "width": width,
        "height": height,
        "bandCount": bands,
        "dataType": DTYPES[dtype],
        "nodata": spec.get("nodata"),
        "compression": compression,
        "layout": layout,
        "blockWidth": block[0],
        "blockHeight": block[1],
        "overviewCount": band.GetOverviewCount(),
        "isCog": hand["isCog"],
        "bigTiff": "BIGTIFF=YES" in spec["options"],
        "georeferenced": hand.get("georeferenced", gt is not None and spec["srs"] is not None),
        "crs": crs,
        "pixelSizeX": gt[1] if gt else None,
        "pixelSizeY": abs(gt[5]) if gt else None,
        "rotated": bool(hand.get("rotated")),
        "pixelIsPoint": bool(hand.get("pixelIsPoint")),
        "nativeBounds": None if not (gt and spec["srs"] is not None) or hand.get("rotated") else [
            gt[0], gt[3] + gt[5] * height, gt[0] + gt[1] * width, gt[3]],
        # GDAL's own WGS84 extent bounds (an independent oracle); the tests compare within a tolerance.
        "wgs84Bounds": wgs84,
        "footprint": hand.get("footprint", "Polygon" if wgs84 else None),
        "footprintNote": hand.get("footprintNote"),
    }
    # GDAL reports a PixelIsPoint raster's extent shifted by half a pixel; the helper must agree.
    with open(os.path.join(OUT, name + ".expected.json"), "w") as handle:
        json.dump(expected, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return path


if __name__ == "__main__":
    for case_name, case_spec, case_hand in CASES:
        build(case_name, case_spec, case_hand)
        print("wrote", case_name)
