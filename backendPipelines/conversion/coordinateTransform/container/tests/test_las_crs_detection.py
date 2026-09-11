# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""S33-CDK-016: a LAS file that records its CRS as GeoTIFF GeoKeys must be detected, not reported as absent.

Run from this directory:  python -m pytest tests/test_las_crs_detection.py -q

Found by running the fixed pipeline, and it is the defect UNDERNEATH S33-CDK-015. A LAS file can record
its coordinate reference system two ways, both in normal use:

    VLR 2112   OGC WKT                    - the LAS 1.4 form
    VLR 34735  GeoTIFF GeoKeyDirectoryTag - the LAS 1.0-1.3 form, and the common case for LAS 1.2

`LasReader.read_metadata` read only 2112. So a real 4,004,326-point LAS 1.2 cloud carrying
`GeoKey 3072 = 26913` (NAD83 / UTM 13N, linear unit 9001/metre) came back with `crs=None`, and the
validator reported "No CRS detected in file metadata".

That single gap produced both halves of the original corruption:

  * With `on_mismatch: warn` (the container's old default) an undetectable CRS was waved through, the
    file's metre coordinates were reprojected as though they were EPSG:4326 degrees, and the result
    landed outside double-precision range - the DBL_MAX..+inf bounding box that started this.
  * With `on_mismatch: error` (the fix) the same file is rejected outright. Safe, but wrong: the CRS is
    right there in the file.

So the fix is to detect it. `laspy.LasHeader.parse_crs()` reads both VLR forms, and these tests pin the
three behaviours that matter without requiring laspy to be installed - it is a container-only dependency
(`laspy[lazrs]>=2.5`), and this container also pins `pydantic>=2.0` while the repository's backend runs
pydantic 1.10.13 in the same interpreter, so installing its requirements here would break the backend
suite. `laspy` is therefore stubbed and the reader's own branching is what gets exercised.
"""

import os
import sys
import types

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))


class _Vlr:
    def __init__(self, record_id, string=""):
        self.record_id = record_id
        self.string = string


class _Crs:
    """Stands in for the pyproj.CRS that parse_crs returns."""

    def __init__(self, wkt):
        self._wkt = wkt

    def to_wkt(self):
        return self._wkt


class _Header:
    def __init__(self, vlrs, parse_result=None, parse_raises=False):
        self.vlrs = vlrs
        self.point_count = 4_004_326
        self._parse_result = parse_result
        self._parse_raises = parse_raises

    def parse_crs(self):
        if self._parse_raises:
            raise ValueError("malformed GeoKey directory")
        return self._parse_result


class _Opened:
    def __init__(self, header):
        self.header = header

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


@pytest.fixture
def las_reader(monkeypatch):
    """The real LasReader, with laspy stubbed so `read_metadata` can be driven header-by-header."""
    laspy = types.ModuleType("laspy")
    holder = {}

    def _open(_path):
        return _Opened(holder["header"])

    laspy.open = _open
    monkeypatch.setitem(sys.modules, "laspy", laspy)

    from coord_xform.readers import LasReader

    return LasReader(), holder


GEOKEY_WKT = 'PROJCRS["NAD83 / UTM zone 13N",ID["EPSG",26913]]'
OGC_WKT = 'PROJCRS["WGS 84 / Pseudo-Mercator",ID["EPSG",3857]]'


def test_a_geokey_only_file_yields_its_crs(las_reader, tmp_path):
    """The regression proper: VLR 34735 present, VLR 2112 absent — exactly red-rocks.laz.

    Before the fix this returned crs=None and the run was rejected with "No CRS detected".
    """
    reader, holder = las_reader
    holder["header"] = _Header(
        vlrs=[_Vlr(34735), _Vlr(34736), _Vlr(34737), _Vlr(22204)],
        parse_result=_Crs(GEOKEY_WKT),
    )

    metadata = reader.read_metadata(tmp_path / "red-rocks.laz")

    assert metadata.crs is not None, "a GeoKey-tagged file must not report an absent CRS"
    assert "26913" in metadata.crs


def test_a_wkt_only_file_still_yields_its_crs(las_reader, tmp_path):
    """The pre-existing path must keep working: parse_crs handles 2112 as well."""
    reader, holder = las_reader
    holder["header"] = _Header(
        vlrs=[_Vlr(2112, OGC_WKT)],
        parse_result=_Crs(OGC_WKT),
    )

    metadata = reader.read_metadata(tmp_path / "mercator.las")

    assert metadata.crs is not None
    assert "3857" in metadata.crs


def test_a_malformed_geokey_directory_falls_back_to_the_wkt_vlr(las_reader, tmp_path):
    """parse_crs raises on a partial GeoKey directory; a file carrying both records is still usable.

    Without the fallback, one bad record would make a file with a perfectly good WKT VLR unreadable —
    turning a cosmetic defect into a rejected input.
    """
    reader, holder = las_reader
    holder["header"] = _Header(
        vlrs=[_Vlr(34735), _Vlr(2112, OGC_WKT)],
        parse_raises=True,
    )

    metadata = reader.read_metadata(tmp_path / "both.las")

    assert metadata.crs == OGC_WKT


def test_a_file_with_no_crs_records_still_reports_none(las_reader, tmp_path):
    """The negative control.

    Some LAS files genuinely carry no CRS, and that must stay distinguishable from a detection failure —
    it is what `enforce_source_crs` acts on. A fix that invented a CRS here would be worse than the bug.
    """
    reader, holder = las_reader
    holder["header"] = _Header(vlrs=[_Vlr(22204)], parse_result=None)

    metadata = reader.read_metadata(tmp_path / "bare.las")

    assert metadata.crs is None


def test_the_point_count_is_read_regardless(las_reader, tmp_path):
    """Guards against a CRS-branch refactor swallowing the rest of the metadata read."""
    reader, holder = las_reader
    holder["header"] = _Header(vlrs=[_Vlr(34735)], parse_result=_Crs(GEOKEY_WKT))

    metadata = reader.read_metadata(tmp_path / "red-rocks.laz")

    assert metadata.point_count == 4_004_326


def test_the_detected_crs_is_parseable_by_the_validator(las_reader, tmp_path):
    """The two halves have to agree: detection returns a string the validator can parse.

    `_parse_crs` calls `pyproj.CRS.from_user_input`, so returning a laspy CRS OBJECT rather than its WKT
    would type-error inside validation instead of at the read — a failure that would surface only on a
    live run against a real file.
    """
    reader, holder = las_reader
    holder["header"] = _Header(vlrs=[_Vlr(34735)], parse_result=_Crs(GEOKEY_WKT))

    metadata = reader.read_metadata(tmp_path / "red-rocks.laz")

    assert isinstance(metadata.crs, str), f"expected WKT text, got {type(metadata.crs).__name__}"

    import re

    assert re.search(r'ID\["EPSG"\s*,\s*(\d+)\]', metadata.crs), (
        "the validator's EPSG fallback regex must be able to read this string when "
        "pyproj.CRS.from_user_input cannot"
    )
