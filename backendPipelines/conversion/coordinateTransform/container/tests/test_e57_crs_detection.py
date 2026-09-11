# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An E57 file's CRS lives on the E57Root element, not on a scan header.

Run from this directory:  python -m pytest tests/test_e57_crs_detection.py -q

The companion to `test_las_crs_detection.py`, for the other format that can carry a CRS. ASTM E2807
records a dataset's coordinate reference system in the E57Root element's optional `coordinateMetadata`
string. A **scan** header carries no CRS at all: `pye57.ScanHeader` exposes 41 properties (point_count,
guid, pose, the bounds groups, the acquisition timestamps ...) and none of them is named
`coordinate_metadata`, so `hasattr(header, "coordinate_metadata")` was `False` for every E57 file ever
opened and `read_metadata` returned `crs=None` unconditionally. The root node is where pye57 itself puts
the value -- `E57.write_default_header` does
`self.root.set("coordinateMetadata", libe57.StringNode(imf, ""))`.

That default matters: pye57 seeds the node with an EMPTY string, so `isDefined("coordinateMetadata")`
being true does not mean a CRS was recorded. Returning `""` would push the empty string into
`validation._parse_crs`, which would report "Failed to parse CRS: " instead of the "No CRS detected"
that `enforce_source_crs` is written to act on. A blank value therefore has to read as absent.

`pye57` is stubbed rather than installed: it is a container-only dependency, and this container pins
`pydantic>=2.0` while the repository's backend runs pydantic 1.10.13 in the same interpreter, so
installing the container's requirements here would break the backend suite. The stub is faithful about
the one thing under test -- its scan header raises `AttributeError` for `coordinate_metadata`, exactly
as the real `ScanHeader` does.
"""

import os
import sys
import types

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

E57_WKT = 'PROJCRS["NAD83 / UTM zone 13N",ID["EPSG",26913]]'


class _StringNode:
    """A libe57 leaf node: the value is reached through .value()."""

    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _Root:
    """The E57Root StructureNode: isDefined() plus __getitem__, as libe57 exposes it."""

    def __init__(self, children=None, raises=False):
        self._children = children or {}
        self._raises = raises

    def isDefined(self, name):  # noqa: N802 - libe57's own spelling
        if self._raises:
            raise RuntimeError("E57_ERROR_BAD_PATH_NAME")
        return name in self._children

    def __getitem__(self, name):
        return _StringNode(self._children[name])


class _ScanHeader:
    """A pye57 ScanHeader. It has no CRS member, and asking for one raises AttributeError."""

    def __init__(self, point_count):
        self.point_count = point_count


class _E57:
    """A pye57.E57 handle: `root` is a property, `get_header(i)` wraps data3D[i]."""

    def __init__(self, root, scan_point_counts):
        self._root = root
        self._scan_point_counts = scan_point_counts

    @property
    def root(self):
        return self._root

    @property
    def scan_count(self):
        return len(self._scan_point_counts)

    def get_header(self, index):
        return _ScanHeader(self._scan_point_counts[index])


@pytest.fixture
def e57_reader(monkeypatch):
    """The real E57Reader, with pye57 stubbed so `read_metadata` can be driven file-by-file."""
    pye57 = types.ModuleType("pye57")
    holder = {}

    pye57.E57 = lambda _path: holder["file"]
    monkeypatch.setitem(sys.modules, "pye57", pye57)

    from coord_xform.readers import E57Reader

    return E57Reader(), holder


def test_a_root_coordinate_metadata_string_is_detected(e57_reader, tmp_path):
    """The regression proper.

    Before the fix this returned crs=None for every E57 file, because the CRS was looked for as an
    attribute of the scan header. With `enforceSourceCrs` defaulting to True and the built-in template
    setting `onMismatch: "error"`, that None rejected the run with "No CRS detected in file metadata".
    """
    reader, holder = e57_reader
    holder["file"] = _E57(
        root=_Root({"coordinateMetadata": E57_WKT}),
        scan_point_counts=[1_000],
    )

    metadata = reader.read_metadata(tmp_path / "site.e57")

    assert metadata.crs is not None, "an E57 recording a CRS on its root must not report an absent CRS"
    assert "26913" in metadata.crs


def test_the_scan_header_is_never_consulted_for_the_crs(e57_reader, tmp_path):
    """The positive control, stated as the mechanism rather than the symptom.

    `_ScanHeader` raises AttributeError for `coordinate_metadata`, as the real pye57 ScanHeader does.
    Any implementation that reads the CRS off a header therefore cannot pass this, and the pre-fix
    `hasattr(header, "coordinate_metadata")` guard is exactly what made that silent instead of loud.
    """
    reader, holder = e57_reader
    holder["file"] = _E57(
        root=_Root({"coordinateMetadata": E57_WKT}),
        scan_point_counts=[7],
    )

    header = holder["file"].get_header(0)
    with pytest.raises(AttributeError):
        getattr(header, "coordinate_metadata")

    assert reader.read_metadata(tmp_path / "site.e57").crs == E57_WKT


@pytest.mark.parametrize("blank", ["", "   ", "\n", "\t "])
def test_an_empty_coordinate_metadata_reads_as_absent(e57_reader, tmp_path, blank):
    """pye57's own writer seeds the node with "", so a defined node is not evidence of a CRS.

    Guards the half of the fix that a naive "read the root node" change would miss: returning "" here
    sends the empty string into validation._parse_crs, which reports "Failed to parse CRS" -- a
    different and more confusing failure than the "No CRS detected" that enforce_source_crs handles.
    """
    reader, holder = e57_reader
    holder["file"] = _E57(
        root=_Root({"coordinateMetadata": blank}),
        scan_point_counts=[42],
    )

    metadata = reader.read_metadata(tmp_path / "written-by-pye57.e57")

    assert metadata.crs is None, f"a blank coordinateMetadata ({blank!r}) must read as no CRS"


def test_a_file_with_no_coordinate_metadata_node_reports_none(e57_reader, tmp_path):
    """The negative control.

    Most E57 files in the wild record no CRS, and that must stay distinguishable from a detection
    failure -- it is what `enforce_source_crs` acts on. A fix that invented a CRS here would be worse
    than the bug.
    """
    reader, holder = e57_reader
    holder["file"] = _E57(root=_Root({"guid": "{...}"}), scan_point_counts=[9])

    metadata = reader.read_metadata(tmp_path / "bare.e57")

    assert metadata.crs is None


def test_an_unreadable_root_does_not_fail_the_metadata_read(e57_reader, tmp_path):
    """A raising root element degrades to "no CRS", not to an unreadable file.

    validation._validate_single treats any exception out of read_metadata as "Failed to read file",
    which is a hard failure for a file whose points are perfectly readable.
    """
    reader, holder = e57_reader
    holder["file"] = _E57(root=_Root(raises=True), scan_point_counts=[5, 6])

    metadata = reader.read_metadata(tmp_path / "odd.e57")

    assert metadata.crs is None
    assert metadata.point_count == 11


def test_the_point_and_scan_counts_are_read_regardless(e57_reader, tmp_path):
    """Guards the CRS refactor against swallowing the rest of the metadata read.

    The unused `header = e57.get_header(0)` binding went away with the header-attribute lookup; the
    per-scan point_count sum must still cover every scan.
    """
    reader, holder = e57_reader
    holder["file"] = _E57(
        root=_Root({"coordinateMetadata": E57_WKT}),
        scan_point_counts=[100, 250, 3],
    )

    metadata = reader.read_metadata(tmp_path / "multi.e57")

    assert metadata.point_count == 353
    assert metadata.scan_count == 3


def test_the_detected_crs_is_a_string_the_validator_can_parse(e57_reader, tmp_path):
    """The two halves have to agree: detection returns text, not a node.

    `validation._parse_crs` calls `pyproj.CRS.from_user_input` and falls back to an EPSG regex over the
    WKT, so handing it a libe57 node object would type-error inside validation instead of at the read --
    a failure that would surface only on a live run against a real file.
    """
    reader, holder = e57_reader
    holder["file"] = _E57(
        root=_Root({"coordinateMetadata": E57_WKT}),
        scan_point_counts=[1],
    )

    metadata = reader.read_metadata(tmp_path / "site.e57")

    assert isinstance(metadata.crs, str), f"expected WKT text, got {type(metadata.crs).__name__}"

    import re

    assert re.search(r'ID\["EPSG"\s*,\s*(\d+)\]', metadata.crs), (
        "the validator's EPSG fallback regex must be able to read this string when "
        "pyproj.CRS.from_user_input cannot"
    )
