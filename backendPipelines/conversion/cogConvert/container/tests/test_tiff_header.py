# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The header check against the shared fixtures: isCog, georeferenced and CRS must equal the sidecars."""

import glob
import os
import struct

import pytest

from cog_convert_pipeline.tiff_header import HeaderRejected, needs_conversion, read_header
from conftest import FIXTURES, expected, fixture_path

NAMES = sorted(os.path.basename(p) for p in glob.glob(os.path.join(FIXTURES, "*.tif")))


@pytest.mark.unit
@pytest.mark.parametrize("name", NAMES)
def test_header_matches_the_shared_sidecar(name):
    header, want = read_header(fixture_path(name)), expected(name)
    assert header.is_cog == want["isCog"]
    assert header.georeferenced == want["georeferenced"]
    assert header.crs == want["crs"]
    assert (header.width, header.height) == (want["width"], want["height"])


@pytest.mark.unit
def test_there_are_fixtures():
    assert len(NAMES) >= 20


@pytest.mark.unit
@pytest.mark.parametrize("name,convert", [
    ("epsg3857.tif", False),            # a COG in EPSG:3857: the one case skipped as already done
    ("no_georef.tif", False),           # nothing to draw on a map
    ("cog_4326.tif", True),             # a COG in another CRS is still converted
    ("utm33.tif", True),
    ("striped_4326.tif", True),
    ("rotated_utm33.tif", True),
    ("user_defined_laea.tif", True),
])
def test_needs_conversion(name, convert):
    assert needs_conversion(read_header(fixture_path(name)))[0] is convert


@pytest.mark.unit
def test_web_mercator_is_recognized():
    assert read_header(fixture_path("epsg3857.tif")).is_web_mercator


@pytest.mark.unit
def test_corrupt_headers_are_refused(tmp_path):
    for body in (b"", b"not a tiff at all", b"II*\x00\xff\xff\xff\x7f", b"II+\x00\x08\x00\x00\x00"):
        path = tmp_path / "bad.tif"
        path.write_bytes(body)
        with pytest.raises(HeaderRejected):
            read_header(str(path))


@pytest.mark.unit
def test_a_directory_loop_is_refused(tmp_path):
    # One directory whose next-directory offset points back at itself.
    ifd = struct.pack("<H", 1) + struct.pack("<HHII", 256, 3, 1, 4) + struct.pack("<I", 8)
    path = tmp_path / "loop.tif"
    path.write_bytes(b"II*\x00" + struct.pack("<I", 8) + ifd)
    with pytest.raises(HeaderRejected):
        read_header(str(path))
