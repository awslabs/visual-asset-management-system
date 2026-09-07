# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Compression is the output FORMAT, not a separate writer setting, so `get_writer` takes only a format.

Run from this directory:  python -m pytest tests/test_writer_output_format.py -q

`get_writer` is a function of the format alone, and a `compress` argument alongside it would be a
second control over the same property. laspy's `LasData.write` documents that for a path destination
the encoding follows the extension -- ".laz -> compressed, .las -> uncompressed. And the do_compress
option will be ignored" -- and `LasWriter.write` derives that extension from the format. So
`LasWriter(compress=False)` writing to something named `.laz` is not reachable: laspy would refuse to
honour it. On top of that, `pipeline._write_outputs` records the output as `f"{stem}.{fmt.value}"`
from the FORMAT while a flag-driven writer would re-suffix from the flag, so `compress=False` with
`outputFormats: ["laz"]` would write `stem.las` and report `stem.laz` -- a path that does not exist.

Both compression states are reachable through the format: `outputFormats: ["las"]` is the
uncompressed request and `["laz"]` the compressed one.

`compressLaz` remains an operator-facing key on the pipeline's configuration, and because it and
`outputFormats` govern one property they must AGREE. `coord_xform.pipeline.run_pipeline` refuses
`compress_laz=False` alongside `OutputFormat.LAZ` rather than running with one of the two discarded;
the last class below pins that, and the asymmetry (only false + laz is refused, because the field
defaults to true).

The check lives in `run_pipeline` rather than on `OutputConfig` for two reasons: it is the single
entry both routes into the container pass through -- the VAMS pipeline via
`coord_transform_pipeline.core` and `coord-xform transform --config` via `PipelineConfig.from_yaml` --
and a pydantic validator would have to be written for one major version, which the split below rules
out.

laspy and pyproj are stubbed rather than installed: they are container-only dependencies, and this
container pins `pydantic>=2.0` while the repository's backend runs pydantic 1.10.13 in the same
interpreter, so installing the container's requirements here would break the backend suite.
"""

import inspect
import os
import sys
import tempfile
import types
from pathlib import Path

import numpy as np
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(_HERE))

from coord_xform.config import OutputFormat  # noqa: E402
from coord_xform.models import PointChunk  # noqa: E402
from coord_xform.spill import ChunkSpill  # noqa: E402
from coord_xform.writers import (  # noqa: E402
    E57Writer,
    LasWriter,
    PlyWriter,
    get_writer,
)

METRE_WKT = 'PROJCRS["OSGB36 / British National Grid",ID["EPSG",27700]]'


class _Axis:
    def __init__(self, unit_name):
        self.unit_name = unit_name


class _FakeCrs:
    """Stands in for the pyproj.CRS that _compute_scales reads axis units from."""

    def __init__(self, wkt):
        self._wkt = wkt
        self.axis_info = [_Axis("metre")]

    def to_wkt(self):
        return self._wkt


def test_get_writer_takes_only_a_format():
    """The positive control for the signature: `compress` was a parameter nothing read."""
    parameters = list(inspect.signature(get_writer).parameters)

    assert parameters == ["fmt"], (
        f"get_writer must be a function of the output format alone, got {parameters}"
    )


def test_get_writer_rejects_a_compress_argument():
    """A caller passing a compression flag must fail loudly rather than be silently ignored.

    A writer that accepted the flag and dropped it is what makes a discarded setting invisible: the
    run succeeds and reports success while the setting had no effect.
    """
    with pytest.raises(TypeError):
        get_writer(OutputFormat.LAZ, compress=False)


@pytest.mark.parametrize(
    ("fmt", "expected_type"),
    [
        (OutputFormat.E57, E57Writer),
        (OutputFormat.LAS, LasWriter),
        (OutputFormat.LAZ, LasWriter),
        (OutputFormat.PLY, PlyWriter),
    ],
)
def test_every_output_format_still_resolves_to_a_writer(fmt, expected_type):
    """Removing the parameter must not change which writer a format selects."""
    assert isinstance(get_writer(fmt), expected_type)


def test_both_compression_states_remain_reachable_through_the_format():
    """What makes the removal safe rather than a loss of function.

    If LAS did not still map to uncompressed and LAZ to compressed, dropping `compressLaz` would have
    taken away an operator's only way to ask for one of the two.
    """
    assert get_writer(OutputFormat.LAS)._compress is False
    assert get_writer(OutputFormat.LAZ)._compress is True


LAS_COORD_MAX = 2**31 - 1


def _scales(writer, extent_xyz, unit_name="metre"):
    """`LasWriter._compute_scales` over a two-point cloud spanning `extent_xyz`, pyproj stubbed.

    The extent is passed as the per-axis min/max the writer now takes, derived here from the same
    two-point array the assertions below are written against. It has to be the extent rather than the
    points: laspy writes the header before the first point is appended, so the scale must be known
    before any point has been read back from the spill.
    """
    xyz = np.array([[0.0, 0.0, 0.0], list(extent_xyz)], dtype=np.float64)
    crs = _FakeCrs(METRE_WKT)
    crs.axis_info = [_Axis(unit_name)]
    module = types.ModuleType("pyproj")
    module.CRS = types.SimpleNamespace(from_wkt=lambda _wkt: crs)
    saved = sys.modules.get("pyproj")
    sys.modules["pyproj"] = module
    try:
        return writer._compute_scales(
            xyz.min(axis=0), xyz.max(axis=0), METRE_WKT
        )
    finally:
        if saved is None:
            del sys.modules["pyproj"]
        else:
            sys.modules["pyproj"] = saved


def test_the_extent_the_spill_accumulates_equals_the_whole_array_min_max():
    """The claim byte-transparency rests on, asserted rather than argued.

    `_compute_scales` and `header.offsets` used to read `np.min`/`np.max` over the concatenated cloud.
    They now read the min/max a `ChunkSpill` folded together one chunk at a time, and the two must be
    elementwise EQUAL -- not merely close -- or every coordinate in every existing output moves.
    """
    rng = np.random.default_rng(20260903)
    whole = rng.normal(scale=1e5, size=(997, 3))

    with ChunkSpill(Path(tempfile.mkdtemp()), 0) as spill:
        for start in range(0, len(whole), 100):
            spill.append(PointChunk(xyz=whole[start : start + 100]))
        spill.close()

        assert spill.point_count == len(whole)
        assert np.array_equal(spill.min_xyz, whole.min(axis=0)), (
            f"accumulated min {spill.min_xyz} != whole-array min {whole.min(axis=0)}"
        )
        assert np.array_equal(spill.max_xyz, whole.max(axis=0)), (
            f"accumulated max {spill.max_xyz} != whole-array max {whole.max(axis=0)}"
        )


def test_a_spilled_chunk_reads_back_bit_for_bit():
    """The other half: the points a writer receives are the points the transform produced.

    A spill is a binary round trip through a file, so a dtype or shape mistake would surface as
    silently wrong coordinates in every output rather than as an error.
    """
    rng = np.random.default_rng(7)
    xyz = rng.normal(scale=1e6, size=(250, 3))
    intensity = rng.random(250).astype(np.float32)
    rgb = rng.integers(0, 256, size=(250, 3)).astype(np.uint8)

    with ChunkSpill(Path(tempfile.mkdtemp()), 4) as spill:
        spill.append(
            PointChunk(xyz=xyz[:100], intensity=intensity[:100], rgb=rgb[:100])
        )
        spill.append(
            PointChunk(xyz=xyz[100:], intensity=intensity[100:], rgb=rgb[100:])
        )
        spill.close()

        read = list(spill.chunks())
        assert [c.count for c in read] == [100, 150]
        assert np.array_equal(np.vstack([c.xyz for c in read]), xyz)
        assert np.array_equal(
            np.concatenate([c.intensity for c in read]), intensity
        )
        assert np.array_equal(np.vstack([c.rgb for c in read]), rgb)
        assert all(c.scan_index == 4 for c in read)


def test_a_wide_extent_coarsens_the_scale():
    """The positive control.

    A LAS coordinate is an int32 offset from header.offsets, which write() sets to the per-axis minimum,
    so an axis spans at most (2**31-1)*scale -- about 215 km at the constant 1e-4. Against the unfixed
    code scales[0] is exactly 1e-4 for any extent, and laspy then raises
    `OverflowError: Values given do not fit after applying offset and scale`, failing the whole run.
    """
    scales = _scales(LasWriter(), (400_000.0, 1.0, 1.0))

    assert scales[0] > 1e-4, (
        f"a 400 km X extent needs a coarser scale than 1e-4, got {scales[0]!r}"
    )
    assert 400_000.0 <= LAS_COORD_MAX * scales[0], (
        "the chosen scale must actually represent the far end of the data"
    )


def test_a_wide_axis_does_not_cost_the_other_axes_precision():
    """Coarsening is per axis: a cloud wide in X must keep its Y and Z precision."""
    scales = _scales(LasWriter(), (400_000.0, 1_000.0, 50.0))

    assert scales[0] > 1e-4
    assert scales[1] == 1e-4
    assert scales[2] == 1e-4


@pytest.mark.parametrize(
    ("unit_name", "expected"),
    [("metre", [1e-4, 1e-4, 1e-4]), ("degree", [1e-9, 1e-9, 1e-4])],
)
def test_a_narrow_extent_keeps_the_preferred_scale(unit_name, expected):
    """The byte-transparency control -- what proves no existing output moves.

    Below the cap the preferred scale wins by orders of magnitude, so every dataset that writes today
    writes identical bytes. Covers the geographic pair as well as the projected one, since they have
    different preferred scales and therefore different caps (~2.15 deg vs ~215 km).
    """
    extent = (0.001, 0.001, 0.001) if unit_name == "degree" else (1_000.0, 1_000.0, 100.0)

    scales = _scales(LasWriter(), extent, unit_name=unit_name)

    assert list(scales) == expected, (
        f"a narrow {unit_name} extent must keep the preferred scale, got {list(scales)}"
    )


def test_a_single_point_keeps_the_preferred_scale():
    """A zero extent must not produce a zero scale, which would make every coordinate undefined."""
    scales = _scales(LasWriter(), (0.0, 0.0, 0.0))

    assert list(scales) == [1e-4, 1e-4, 1e-4]


def test_the_coarsened_scale_is_exactly_tight_at_the_boundary():
    """extent/_LAS_COORD_MAX needs no safety margin, and that is a float claim worth pinning.

    Replicates laspy's own two expressions: its bounds check compares the data max against
    `_apply_scale(iinfo.max)` == LAS_COORD_MAX*scale + offset, and its write rounds
    (value-offset)/scale into the int32. Both must sit exactly ON the limit, not one ULP past it --
    measured across 40k extents including these, with a too-fine scale confirmed to trip both arms.
    """
    for extent in (400_000.0, 1_300_000.0, 40_075_000.0):
        scale = _scales(LasWriter(), (extent, 1.0, 1.0))[0]

        assert extent <= LAS_COORD_MAX * scale, f"laspy's bounds check would reject {extent}"
        assert round(extent / scale) <= LAS_COORD_MAX, f"the rounded int overflows for {extent}"


class _FakeHeader:
    def __init__(self, point_format=None):
        self.point_format = point_format
        self.offsets = None
        self.scales = None
        self.crs = None

    def add_crs(self, crs):
        self.crs = crs


class _FakeRecord:
    """Stands in for laspy.ScaleAwarePointRecord: dimensions are plain attributes."""

    def __init__(self, count, header):
        self.count = count
        self.header = header


class _FakeLasStreamWriter:
    """Stands in for the writer `laspy.open(..., mode="w")` returns.

    A context manager whose `write_points` collects the records it was given, so a test can assert that
    the output was produced INCREMENTALLY -- one call per spilled chunk -- rather than from one array.
    """

    def __init__(self, destination, header, kwargs, sink):
        self.destination = destination
        self.header = header
        self.records = []
        sink.append(self)
        self.kwargs = kwargs
        # Create the file, because laspy would: the published-file assertion reads the directory.
        with open(destination, "wb"):
            pass

    def write_points(self, record):
        self.records.append(record)

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return None


@pytest.fixture
def las_write_destinations(monkeypatch):
    """Capture the destination and records `LasWriter.write` hands to laspy, with laspy/pyproj stubbed."""
    sink = []

    laspy = types.ModuleType("laspy")
    laspy.PointFormat = lambda point_format_id: point_format_id
    laspy.LasHeader = lambda point_format=None: _FakeHeader(point_format)
    laspy.ScaleAwarePointRecord = types.SimpleNamespace(
        zeros=lambda count, *, header: _FakeRecord(count, header)
    )
    laspy.open = lambda destination, mode=None, header=None, **kwargs: (
        _FakeLasStreamWriter(destination, header, kwargs, sink)
    )
    monkeypatch.setitem(sys.modules, "laspy", laspy)

    pyproj = types.ModuleType("pyproj")
    pyproj.CRS = types.SimpleNamespace(from_wkt=_FakeCrs)
    monkeypatch.setitem(sys.modules, "pyproj", pyproj)

    return sink


def _two_chunk_spill(tmp_path):
    """A closed two-chunk spill, which is what a writer now receives instead of a chunk list."""
    spill = ChunkSpill(Path(tmp_path) / "spill", 0)
    spill.append(PointChunk(xyz=np.array([[0.0, 0.0, 0.0]], dtype=np.float64)))
    spill.append(PointChunk(xyz=np.array([[1.0, 2.0, 3.0]], dtype=np.float64)))
    spill.close()
    return spill


@pytest.mark.parametrize(
    ("fmt", "expected_suffix"),
    [(OutputFormat.LAS, ".las"), (OutputFormat.LAZ, ".laz")],
)
def test_the_extension_handed_to_laspy_is_what_selects_compression(
    las_write_destinations, tmp_path, fmt, expected_suffix
):
    """The behavioural half: laspy is asked to write a path whose extension IS the encoding.

    laspy derives compression from the extension of a path destination and ignores an explicit
    `do_compress` there -- `open_las` does exactly what `LasData.write` did, `do_compress = splitext(
    source)[1].lower() == ".laz"` -- so this assertion is the whole mechanism and it survives the move to
    the streaming writer. It also pins the temp-file suffix: `LasWriter.write` writes through
    `tempfile.mkstemp(suffix=final_path.suffix)` before renaming, so a temp name that kept the default
    `.tmp` suffix would silently write an UNCOMPRESSED file and then rename it to `.laz`.
    """
    writer = get_writer(fmt)

    writer.write(tmp_path / "cloud", _two_chunk_spill(tmp_path), METRE_WKT)

    assert len(las_write_destinations) == 1
    stream = las_write_destinations[0]

    assert isinstance(stream.destination, (str, os.PathLike)), (
        "laspy only infers compression from the extension for a path destination; handing it an open "
        "stream makes it default to UNCOMPRESSED regardless of the file name"
    )
    assert str(stream.destination).endswith(expected_suffix), (
        f"{fmt.value} must be written through a {expected_suffix} destination, "
        f"got {stream.destination}"
    )
    assert "do_compress" not in stream.kwargs, (
        "laspy ignores do_compress for a path destination, so passing it would read as a control that "
        "does nothing -- the same defect being removed here"
    )


def test_the_las_output_is_appended_one_spilled_chunk_at_a_time(
    las_write_destinations, tmp_path
):
    """The streaming guarantee at the writer, which is where the memory bound is actually spent.

    Against the accumulating shape the writer received a chunk LIST and built one array from it, so peak
    memory was the whole cloud however small `chunkSize` was. One `write_points` call per spilled chunk,
    with the counts in order, is what makes the bound one chunk.
    """
    get_writer(OutputFormat.LAZ).write(
        tmp_path / "cloud", _two_chunk_spill(tmp_path), METRE_WKT
    )

    stream = las_write_destinations[0]
    assert [r.count for r in stream.records] == [1, 1], (
        f"expected one write per spilled chunk, got {[r.count for r in stream.records]}"
    )


def test_the_header_is_fixed_before_the_first_point_is_written(
    las_write_destinations, tmp_path
):
    """Offsets and scales come from the spill's accumulated extent, not from the points.

    laspy writes the header when the file is opened, so a writer that derived either from the points it
    had read so far would produce a header describing only the first chunk -- and every coordinate is
    encoded relative to that header.
    """
    get_writer(OutputFormat.LAS).write(
        tmp_path / "cloud", _two_chunk_spill(tmp_path), METRE_WKT
    )

    header = las_write_destinations[0].header
    assert header is not None, "laspy.open must be given the header, not handed points first"
    assert list(header.offsets) == [0.0, 0.0, 0.0], (
        f"offsets must be the whole spill's per-axis minimum, got {header.offsets}"
    )
    assert header.crs is not None, "the CRS must be on the header before the file is opened"


@pytest.mark.parametrize(
    ("fmt", "expected_suffix"),
    [(OutputFormat.LAS, ".las"), (OutputFormat.LAZ, ".laz")],
)
def test_the_published_file_matches_the_requested_format(
    las_write_destinations, tmp_path, fmt, expected_suffix
):
    """The output that lands on disk carries the extension the operator asked for.

    `pipeline._write_scan_outputs` names its report entry `f"{stem}.{fmt.value}"` and uploads whatever is
    in the output directory, so a writer that re-suffixed against the format would produce a report
    naming a file that does not exist.
    """
    get_writer(fmt).write(tmp_path / "cloud", _two_chunk_spill(tmp_path), METRE_WKT)

    written = sorted(p.name for p in tmp_path.iterdir() if p.is_file())

    assert written == [f"cloud{expected_suffix}"], (
        f"expected a single cloud{expected_suffix} in the output directory, found {written}"
    )
