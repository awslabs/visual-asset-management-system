# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`E57Writer` records the target CRS on the E57Root, where ASTM E2807 puts it.

Run from this directory:  python -m pytest tests/test_e57_crs_write.py -q

The write half of `test_e57_crs_detection.py`. `E57Writer.write` took `crs_wkt` and never wrote it, so
every E57 the pipeline produced recorded no coordinate reference system: the coordinates were reprojected
and nothing said into what. It also made the pipeline's own output unusable as its own input, because
`enforce_source_crs` rejects a file with no detectable CRS.

The value cannot simply be set afterwards, and that is what shapes the implementation. Measured against
pye57 0.4.19 in a repository-external virtual environment (never the shared interpreter, which runs the
backend's pydantic 1.10.13 against this container's `pydantic>=2.0`):

* `pye57.E57.write_default_header` creates `coordinateMetadata` as an EMPTY StringNode, so the element
  already exists by the time `E57.__init__` returns.
* a later `root.set("coordinateMetadata", ...)` raises
  `E57Exception: attempted to set an existing child element to a new value (ErrorSetTwice)`,
  reported from `e57::StructureNodeImpl::set`. libe57 has no remove or replace: `StructureNode` exposes
  `checkInvariant, childCount, destImageFile, elementName, get, isDefined, isAttached, isRoot, parent,
  pathName, set` and nothing else.
* supplying the value WHILE that header is written round-trips exactly: a 305-character WKT and a
  1,561-character one both read back byte-identical through the same path `E57Reader._read_crs` uses,
  with the scan count and point count unchanged and the `ASTM-E57` signature intact.

`pye57` is stubbed here rather than installed, for the pydantic reason above. The stub is faithful about
the two things under test: its root refuses to set an existing child, with libe57's own message, and its
`write_default_header` creates the same nine root elements in the same order as pye57's.
"""

import os
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from coord_xform.models import PointChunk  # noqa: E402
from coord_xform.spill import ChunkSpill  # noqa: E402

WKT = 'PROJCRS["NAD83 / UTM zone 13N",ID["EPSG",26913]]'

# pye57.E57.write_default_header's elements, in the order it sets them.
DEFAULT_ROOT_ELEMENTS = [
    "formatName",
    "guid",
    "versionMajor",
    "versionMinor",
    "e57LibraryVersion",
    "coordinateMetadata",
    "creationDateTime",
    "data3D",
    "images2D",
]


class _SetTwice(Exception):
    """libe57's ErrorSetTwice, raised with the message the real library reports."""


class _Node:
    """A libe57 leaf node. `StringNode` carries the text; the other kinds only need identity."""

    def __init__(self, image_file, value=None):
        self.image_file = image_file
        self._value = value

    def value(self):
        return self._value


class _Root:
    """The E57Root StructureNode, including libe57's refusal to replace an existing child."""

    def __init__(self):
        self.set_calls = []
        self._children = {}

    def set(self, name, node):
        self.set_calls.append(name)
        if name in self._children:
            raise _SetTwice(
                "attempted to set an existing child element to a new value (ErrorSetTwice)"
            )
        self._children[name] = node

    def isDefined(self, name):  # noqa: N802 - libe57's own spelling
        return name in self._children

    def __getitem__(self, name):
        return self._children[name]


class _ImageFile:
    def __init__(self, path, mode):
        self.path = path
        self.mode = mode
        self.closed = False
        self._root = _Root()

    def root(self):
        return self._root

    def extensionsAdd(self, prefix, uri):  # noqa: N802 - libe57's own spelling
        pass

    def close(self):
        self.closed = True


class _E57:
    """pye57.E57: `root` is a property, and mode "w" writes the default header from __init__.

    Every handle opened is recorded, so a test can inspect the file a writer produced without reaching
    into the writer's internals — which is what lets the same assertions run against any implementation.
    """

    instances = []

    def __init__(self, path, mode="r"):
        _E57.instances.append(self)
        self.path = path
        self.scans = []
        self.image_file = _ImageFile(path, mode)
        if mode == "w":
            self.write_default_header()

    @property
    def root(self):
        return self.image_file.root()

    def write_default_header(self):
        imf = self.image_file
        imf.extensionsAdd("", "http://www.astm.org/COMMIT/E57/2010-e57-v1.0")
        for name in DEFAULT_ROOT_ELEMENTS:
            value = "" if name == "coordinateMetadata" else name
            self.root.set(name, _Node(imf, value))

    def write_scan_raw(self, data):
        self.scans.append(data)

    def close(self):
        self.image_file.close()


@pytest.fixture
def writers(monkeypatch):
    """The real coord_xform.writers, with pye57 and its libe57 submodule stubbed."""
    _E57.instances = []

    libe57 = types.ModuleType("pye57.libe57")
    libe57.StringNode = _Node
    libe57.E57Exception = _SetTwice

    pye57 = types.ModuleType("pye57")
    pye57.E57 = _E57
    pye57.libe57 = libe57

    monkeypatch.setitem(sys.modules, "pye57", pye57)
    monkeypatch.setitem(sys.modules, "pye57.libe57", libe57)

    import coord_xform.writers as module

    return module


def _spill(scan_index=0):
    """A closed one-scan spill, which is what a writer receives.

    Two chunks rather than one, so a writer that read only the first would fail the point-count
    assertions below rather than silently truncate the scan.

    Deliberately NOT under the test's `tmp_path`: the atomicity test asserts that a failed write leaves
    that directory empty, and a spill file sitting in it would satisfy that assertion for the wrong
    reason -- or break it. The container puts the spill outside its output directory for the same
    reason, so nothing spilled is mistaken for an output.
    """
    spill = ChunkSpill(Path(tempfile.mkdtemp()), scan_index)
    spill.append(
        PointChunk(xyz=np.array([[1.0, 2.0, 3.0]], dtype=np.float64))
    )
    spill.append(
        PointChunk(xyz=np.array([[4.0, 5.0, 6.0]], dtype=np.float64))
    )
    spill.close()
    return spill


def _written_root(writers, crs_wkt, tmp_path):
    """Write an E57 through `E57Writer.write` and return the handle and root it produced.

    Reached through the public write path rather than any helper of the implementation, so the
    assertions describe the file the pipeline publishes rather than how the writer is arranged.
    """
    writers.E57Writer().write(tmp_path / "cloud.e57", _spill(), crs_wkt)

    assert len(_E57.instances) == 1, (
        f"one E57 handle expected for one output file, got {len(_E57.instances)}"
    )
    handle = _E57.instances[0]
    return handle, handle.root


def test_the_stub_reproduces_libe57s_refusal_to_replace_a_child():
    """The control on the stub itself.

    Every rejection test below rests on this: if the stub let a second `set` through, the naive
    one-line fix would pass here while raising ErrorSetTwice inside the container. The message is the
    one the real library reports.
    """
    handle = _E57("cloud.e57", mode="w")

    with pytest.raises(_SetTwice) as excinfo:
        handle.root.set("coordinateMetadata", _Node(handle.image_file, WKT))

    assert "ErrorSetTwice" in str(excinfo.value)


def test_the_crs_is_recorded_on_the_root(writers, tmp_path):
    """The regression proper: before the fix `coordinateMetadata` stayed the empty placeholder."""
    handle, root = _written_root(writers, WKT, tmp_path)

    assert root.isDefined("coordinateMetadata")
    assert root["coordinateMetadata"].value() == WKT, (
        f"the E57Root must record the target CRS, got "
        f"{root['coordinateMetadata'].value()!r}"
    )
    assert handle.image_file is root["coordinateMetadata"].image_file, (
        "the node must belong to the file being written"
    )


def test_coordinate_metadata_is_set_exactly_once(writers, tmp_path):
    """What keeps the fix from being the ErrorSetTwice crash.

    The value has to arrive as the header is written, not afterwards, so the element must be set once
    and only once for the whole file.
    """
    _handle, root = _written_root(writers, WKT, tmp_path)

    assert root.set_calls.count("coordinateMetadata") == 1, (
        f"coordinateMetadata was set {root.set_calls.count('coordinateMetadata')} times: "
        f"{root.set_calls}"
    )


def test_the_rest_of_the_default_header_is_untouched(writers, tmp_path):
    """The header keeps every element pye57 writes, in pye57's order.

    The substitution forwards everything else rather than rebuilding the header, so a future pye57 that
    adds a root element still gets it. Replicating the header instead would silently drop it.
    """
    _handle, root = _written_root(writers, WKT, tmp_path)

    assert root.set_calls == DEFAULT_ROOT_ELEMENTS, (
        f"the root elements changed: {root.set_calls}"
    )
    for name in DEFAULT_ROOT_ELEMENTS:
        if name != "coordinateMetadata":
            assert root[name].value() == name, f"{name} was replaced"


@pytest.mark.parametrize("crs_wkt", ["", "   ", "\n\t "])
def test_a_blank_crs_keeps_the_empty_placeholder(writers, crs_wkt, tmp_path):
    """A blank value must stay blank, because blank is how absence is read.

    `E57Reader._read_crs` strips the value and returns None for an empty result, and
    `validation._parse_crs` reports "Failed to parse CRS" for a whitespace string. Writing whitespace
    would turn "not recorded" into a parse failure.
    """
    _handle, root = _written_root(writers, crs_wkt, tmp_path)

    assert root["coordinateMetadata"].value() == ""


def test_a_blank_crs_uses_the_plain_pye57_handle(writers):
    """No substitution machinery is involved when there is nothing to substitute."""
    assert type(writers._open_e57_for_writing("cloud.e57", "")).__name__ == "_E57"
    assert type(writers._open_e57_for_writing("cloud.e57", WKT)).__name__ == "_CrsE57"


def test_the_written_file_carries_the_crs_and_the_points(writers, tmp_path):
    """The whole `E57Writer.write` path, so the CRS is not recorded at the cost of the scan data.

    The temp-file rename is part of it: the writer builds into a sibling temp file and replaces the
    target, so the assertions are about the file the pipeline publishes.
    """
    handle, root = _written_root(writers, WKT, tmp_path)

    assert (tmp_path / "cloud.e57").exists()
    assert [p.name for p in tmp_path.iterdir()] == ["cloud.e57"], (
        "the temp file must not survive the write"
    )

    assert root["coordinateMetadata"].value() == WKT
    assert handle.image_file.closed, "the file must be closed before it is renamed"
    assert len(handle.scans) == 1
    assert list(handle.scans[0]) == ["cartesianX", "cartesianY", "cartesianZ"]
    assert len(handle.scans[0]["cartesianX"]) == 2


def test_a_failed_write_leaves_no_temp_file_behind(writers, monkeypatch, tmp_path):
    """The atomicity the writer already had must survive the change."""

    class _Explode:
        def __init__(self, path, mode="r"):
            raise RuntimeError("libe57 said no")

    monkeypatch.setattr(sys.modules["pye57"], "E57", _Explode)

    with pytest.raises(RuntimeError):
        writers.E57Writer().write(tmp_path / "cloud.e57", _spill(), WKT)

    assert list(tmp_path.iterdir()) == []


def test_one_spill_becomes_exactly_one_scan_carrying_all_its_points(writers, tmp_path):
    """A spill holds one scan, so a file holds one scan -- and all of its points.

    The writer used to group a chunk list by `scan_index` and emit one E57 scan per group. That path is
    unreachable and has been removed: `pipeline._write_scan_outputs` is called once per scan and each
    scan gets its own file under the `_scanNNN` name, which
    `test_pipeline_streaming_and_naming.py::test_multi_scan_output_names_keep_their_ascending_scan_suffix`
    pins. What matters here instead is that reading the scan back from the spill loses nothing: the
    writer sees two separate chunks and must produce one scan of two points.
    """
    writers.E57Writer().write(tmp_path / "cloud.e57", _spill(scan_index=2), WKT)

    handle = _E57.instances[0]
    assert len(handle.scans) == 1, (
        f"one spilled scan must become one E57 scan, got {len(handle.scans)}"
    )
    scan = handle.scans[0]
    assert list(scan["cartesianX"]) == [1.0, 4.0], (
        f"both spilled chunks must reach the file in order, got {list(scan['cartesianX'])}"
    )
    assert list(scan["cartesianY"]) == [2.0, 5.0]
    assert list(scan["cartesianZ"]) == [3.0, 6.0]
    assert handle.root["coordinateMetadata"].value() == WKT
