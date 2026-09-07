# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`run_pipeline` must spill transformed points to disk, keep output names inside the output directory,
and reject a CRS mismatch with an exception the container can catch.

Run from the container directory:  python -m pytest tests/test_pipeline_streaming_and_naming.py -q

These drive the REAL `coord_xform/pipeline.py`, not the `sys.modules` stub `conftest.py` installs for
`core.py`'s late import. The module is loaded from its file path under a private name, so that stub stays
in place for the other suites, and the geospatial dependencies the module reaches through its siblings
(`structlog`, `pyproj`) are stood in for only when they are genuinely absent from the interpreter.

Every assertion below is made through `run_pipeline` rather than through a helper, so the same test file
runs against the accumulate-then-write shape as against the spilling one. Four of them fail against the
accumulating shape:

* `test_the_first_scan_is_written_before_the_last_chunk_is_read` -- the whole point cloud was collected
  into one list before any write, so `chunk_size` bounded nothing ACROSS scans.
* `test_a_single_scan_file_is_not_held_in_memory_before_it_is_written` -- the arm above is satisfied by a
  pipeline that writes at each scan boundary while holding a whole scan, which is what a single-scan
  LAS/LAZ file is. This one has no boundary to write at, so it is the arm the earlier one could not
  reach and the one the spill closes.
* `test_a_traversing_target_crs_cannot_escape_the_output_directory` and
  `test_a_wkt_target_crs_becomes_a_single_safe_name` -- only `:` was replaced, so a caller-supplied CRS
  reached the filesystem verbatim.
* `test_a_crs_mismatch_raises_a_catchable_exception` -- the rejection was a `SystemExit`, which a plain
  `except Exception` cannot catch.
"""

import importlib
import importlib.util
import json
import os
import sys
import types

import numpy as np
import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTAINER = os.path.dirname(_HERE)
if _CONTAINER not in sys.path:
    sys.path.insert(0, _CONTAINER)


class _AnyAttributeModule(types.ModuleType):
    """A stand-in module that answers any attribute with a throwaway type.

    `coord_xform.transform` and `coord_xform.validation` annotate return types as `pyproj.CRS`, which is
    evaluated at import time, so a bare `ModuleType` is not enough to let them import.
    """

    def __getattr__(self, name):
        return type(name, (), {})


def _structlog_stand_in():
    module = types.ModuleType("structlog")

    class _Logger:
        def bind(self, **_kwargs):
            return self

        def info(self, *_args, **_kwargs):
            pass

        def warning(self, *_args, **_kwargs):
            pass

        def error(self, *_args, **_kwargs):
            pass

        def exception(self, *_args, **_kwargs):
            pass

    module.get_logger = lambda *_args, **_kwargs: _Logger()
    return module


def _ensure_importable(name, factory):
    """Install a stand-in for a module only when the real one is not installed."""
    try:
        importlib.import_module(name)
    except ImportError:
        sys.modules[name] = factory()


_ensure_importable("structlog", _structlog_stand_in)
_ensure_importable("pyproj", lambda: _AnyAttributeModule("pyproj"))


def _load_pipeline_module():
    """Load `coord_xform/pipeline.py` under a private name, leaving conftest's stub registered."""
    path = os.path.join(_CONTAINER, "coord_xform", "pipeline.py")
    spec = importlib.util.spec_from_file_location(
        "coord_xform_pipeline_under_test", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


pipeline = _load_pipeline_module()

from coord_xform.config import (  # noqa: E402
    OnMismatch,
    OutputConfig,
    OutputFormat,
    PipelineConfig,
    SourceConfig,
    TargetConfig,
    TransformConfig,
    ValidationConfig,
)
from coord_xform.models import (  # noqa: E402
    CrsConfidence,
    InputFormat,
    PointChunk,
    ScanDataset,
    ScanMetadata,
    TransformResult,
    ValidationResult,
)


def _config(tmp_path, target_crs="EPSG:27700", on_mismatch=OnMismatch.WARN):
    return PipelineConfig(
        name="vams-coordinate-transform",
        version="1.0",
        source=SourceConfig(crs="EPSG:4326"),
        target=TargetConfig(crs=target_crs),
        transform=TransformConfig(chunk_size=100),
        validation=ValidationConfig(on_mismatch=on_mismatch),
        output=OutputConfig(
            formats=[OutputFormat.LAZ], directory=tmp_path / "output"
        ),
    )


class _Harness:
    """Replaces everything `run_pipeline` reaches out to, and records what it did in order.

    `events` is the single ordered log of reads and writes. The interleaving between the two is what
    distinguishes streaming from accumulate-then-write; a per-write assertion alone cannot see it.
    """

    def __init__(self, scan_layout):
        self.scan_layout = scan_layout
        self.events = []
        self.writes = []
        # Which chunk of each scan carries ScanMetadata. None means no chunk does, which is the
        # LAS/LAZ case; an index makes only that chunk carry it, for the sidecar test.
        self.metadata_on_chunk = None

    def install(self, monkeypatch, validation_results=()):
        harness = self

        monkeypatch.setattr(
            pipeline, "validate_inputs", lambda config, inputs: list(validation_results)
        )
        monkeypatch.setattr(
            pipeline,
            "discover_scan_dataset",
            lambda path: ScanDataset(point_cloud_path=path),
        )
        monkeypatch.setattr(pipeline, "detect_format", lambda path: InputFormat.LAZ)
        monkeypatch.setattr(pipeline, "get_reader", lambda fmt: harness._reader())
        monkeypatch.setattr(
            pipeline, "CoordinateTransformer", lambda config: harness._transformer()
        )
        # Deliberately takes only the format: `get_writer` has no second parameter, so a reintroduced
        # `compress=` argument fails here rather than at write time inside the container.
        monkeypatch.setattr(pipeline, "get_writer", lambda fmt: harness._writer(fmt))

    def _reader(self):
        harness = self

        class _Reader:
            def read_chunks(self, path, chunk_size):
                for scan_index, chunk_count in enumerate(harness.scan_layout):
                    for chunk_index in range(chunk_count):
                        harness.events.append(("read", scan_index, chunk_index))
                        meta = None
                        if harness.metadata_on_chunk == chunk_index:
                            meta = ScanMetadata(
                                scan_index=scan_index, name=f"scan-{scan_index}"
                            )
                        yield PointChunk(
                            xyz=np.zeros((10, 3), dtype=np.float64),
                            scan_index=scan_index,
                            chunk_index=chunk_index,
                            scan_metadata=meta,
                        )

        return _Reader()

    def _transformer(self):
        class _Transformer:
            scale_factor = 1.0

            @property
            def target_crs(self):
                return types.SimpleNamespace(to_wkt=lambda: "WKT")

            def transform_chunk(self, chunk):
                return TransformResult(
                    xyz=chunk.xyz,
                    residual_error_mm=0.0,
                    scale_correction_applied=1.0,
                )

        return _Transformer()

    def _writer(self, fmt):
        harness = self

        class _Writer:
            def write(self, path, spill, crs_wkt):
                # Deliberately creates nothing: against the unsanitised name a real write would land
                # outside pytest's temp directory, which is the defect, not something to reproduce.
                #
                # The spill is read back here rather than trusted, because the chunk COUNT is what
                # distinguishes a bounded write from an accumulated one and it is only observable from
                # the file. `_write_scan_outputs` closes the spill before calling a writer, so reading
                # is defined.
                harness.events.append(("write", path.stem))
                read = list(spill.chunks())
                harness.writes.append(
                    (
                        path,
                        [spill.scan_index],
                        len(read),
                        spill.point_count,
                    )
                )

        return _Writer()


def test_the_first_scan_is_written_before_the_last_chunk_is_read(monkeypatch, tmp_path):
    """The streaming guarantee, stated as an ordering between the reader and the writer.

    Accumulating every transformed chunk before writing anything puts every `read` event ahead of every
    `write` event, which is exactly the shape that makes `chunk_size` bound nothing.
    """
    harness = _Harness(scan_layout=[3, 3, 3])
    harness.install(monkeypatch)

    pipeline.run_pipeline(_config(tmp_path), [tmp_path / "cloud.laz"])

    kinds = [event[0] for event in harness.events]
    first_write = kinds.index("write")
    last_read = len(kinds) - 1 - kinds[::-1].index("read")

    assert first_write < last_read, (
        "the first scan must be written while the file is still being read; every write happening "
        "after the reader is exhausted means the whole point cloud was held in memory first"
    )


def test_each_write_receives_exactly_one_scan(monkeypatch, tmp_path):
    """Bounding the held chunks is only useful if a write still carries a whole single scan."""
    harness = _Harness(scan_layout=[3, 3, 3])
    harness.install(monkeypatch)

    pipeline.run_pipeline(_config(tmp_path), [tmp_path / "cloud.laz"])

    assert [scans for _path, scans, _count, _points in harness.writes] == [[0], [1], [2]]
    assert [count for _path, _scans, count, _points in harness.writes] == [3, 3, 3]
    # Every point still reaches the writer. Bounding memory must not bound the OUTPUT: the reader
    # yields 10 points per chunk, so three chunks per scan is 30 points per file.
    assert [points for _path, _scans, _count, points in harness.writes] == [30, 30, 30]


def test_a_single_scan_file_is_not_held_in_memory_before_it_is_written(
    monkeypatch, tmp_path
):
    """The residual the spill closes, and the one arm the earlier scan-boundary test could not see.

    `test_the_first_scan_is_written_before_the_last_chunk_is_read` uses `scan_layout=[3, 3, 3]`, so it is
    satisfied by a pipeline that merely writes at each SCAN boundary while holding a whole scan in
    memory -- which is what the accumulating shape did, and it is the shape every single-scan LAS/LAZ
    file takes. The one-scan layout has no boundary to write at, so the only way the writer can see
    fewer than the whole cloud at once is for the points to be somewhere other than memory.

    Asserted through the chunk count the writer reads back from the spill: a writer handed one array
    cannot report six.
    """
    harness = _Harness(scan_layout=[6])
    harness.install(monkeypatch)

    pipeline.run_pipeline(_config(tmp_path), [tmp_path / "cloud.laz"])

    assert len(harness.writes) == 1
    _path, _scans, chunk_count, point_count = harness.writes[0]
    assert chunk_count == 6, (
        f"the writer must receive the scan as {6} separately-readable chunks, so its peak is one "
        f"chunk rather than one cloud; got {chunk_count}"
    )
    assert point_count == 60, (
        f"and all 60 points must still be there -- bounding memory must not drop points; "
        f"got {point_count}"
    )


def test_the_scan_metadata_sidecar_still_names_the_scan_it_came_from(monkeypatch, tmp_path):
    """The sidecar's source moved from the chunk list to the spill, so its selection rule needs pinning.

    `_write_scan_metadata` used to take the chunk list and pick the FIRST chunk carrying metadata; it now
    takes the value the spill recorded, which is the first non-None one appended. The two must agree, and
    a per-scan sidecar naming the wrong scan is the kind of drift a run reports as success.
    """
    harness = _Harness(scan_layout=[2, 2])
    harness.install(monkeypatch)
    # Only the SECOND chunk of each scan carries metadata, so a rule that read the first chunk
    # unconditionally would write no sidecar at all rather than the wrong one.
    harness.metadata_on_chunk = 1

    pipeline.run_pipeline(_config(tmp_path), [tmp_path / "cloud.laz"])

    output = tmp_path / "output"
    sidecars = sorted(p.name for p in output.glob("*_scan_metadata.json"))
    assert sidecars == [
        "cloud_EPSG_27700_scan000_scan_metadata.json",
        "cloud_EPSG_27700_scan001_scan_metadata.json",
    ], f"got {sidecars}"
    for index, name in enumerate(sidecars):
        body = json.loads((output / name).read_text())
        assert body["scan_index"] == index, (
            f"{name} records scan_index={body['scan_index']}, so the sidecar names a different scan "
            f"than the file it sits beside"
        )


def test_multi_scan_output_names_keep_their_ascending_scan_suffix(monkeypatch, tmp_path):
    """The naming contract a streaming rewrite is most likely to break.

    `_scanNNN` is applied only when a file carries more than one scan, and the files must come out in
    ascending scan order, because that ordering is what `report.output_files` and the S3 upload publish.
    """
    harness = _Harness(scan_layout=[2, 2, 2])
    harness.install(monkeypatch)

    report = pipeline.run_pipeline(_config(tmp_path), [tmp_path / "cloud.laz"])

    assert [path.name for path, _scans, _count, _points in harness.writes] == [
        "cloud_EPSG_27700_scan000.laz",
        "cloud_EPSG_27700_scan001.laz",
        "cloud_EPSG_27700_scan002.laz",
    ]
    assert [path.name for path in report.output_files] == [
        "cloud_EPSG_27700_scan000.laz",
        "cloud_EPSG_27700_scan001.laz",
        "cloud_EPSG_27700_scan002.laz",
    ]


def test_a_single_scan_file_gets_no_scan_suffix(monkeypatch, tmp_path):
    """The other half of the naming contract: one scan keeps the bare stem.

    Deferring the first scan's write until a second scan appears is what preserves this; writing scan 0
    as soon as it completes would suffix every file.
    """
    harness = _Harness(scan_layout=[4])
    harness.install(monkeypatch)

    pipeline.run_pipeline(_config(tmp_path), [tmp_path / "cloud.laz"])

    assert [path.name for path, _scans, _count, _points in harness.writes] == [
        "cloud_EPSG_27700.laz"
    ]


def test_a_traversing_target_crs_cannot_escape_the_output_directory(
    monkeypatch, tmp_path
):
    """A caller-supplied CRS must not reach the filesystem as a path.

    `targetCrs` is settable as asset metadata, so it is untrusted. Replacing only `:` left `..` and the
    separators intact, and the resulting write landed outside the directory the container later uploads
    from -- a run that reported success having published nothing.
    """
    harness = _Harness(scan_layout=[1])
    harness.install(monkeypatch)
    output_dir = (tmp_path / "output").resolve()

    try:
        pipeline.run_pipeline(
            _config(tmp_path, target_crs="../../../../tmp/evil"),
            [tmp_path / "cloud.laz"],
        )
    except Exception:
        pass  # rejecting the name outright is an acceptable outcome; escaping with it is not

    for path, _scans, _count, _points in harness.writes:
        assert path.resolve().parent == output_dir, (
            f"{path} is outside {output_dir}, so the container's upload step would find no output "
            "while the run still reported success"
        )
        for separator in ("/", "\\", ".."):
            assert separator not in path.stem, (
                f"{separator!r} survived into the output name {path.stem!r}"
            )


def test_a_wkt_target_crs_becomes_a_single_safe_name(monkeypatch, tmp_path):
    """A legitimate CRS can also be a WKT or proj4 string full of quotes, spaces and slashes."""
    harness = _Harness(scan_layout=[1])
    harness.install(monkeypatch)
    wkt = 'PROJCS["OSGB 1936 / British National Grid",AUTHORITY["EPSG","27700"]]'

    pipeline.run_pipeline(_config(tmp_path, target_crs=wkt), [tmp_path / "cloud.laz"])

    assert len(harness.writes) == 1
    stem = harness.writes[0][0].stem
    for forbidden in ('"', "'", " ", "/", "\\", "[", "]", ",", ":"):
        assert forbidden not in stem, f"{forbidden!r} survived into the output name {stem!r}"
    assert len(stem) < 120, f"output name {stem!r} is unreasonably long for an S3 key"


def test_an_epsg_target_crs_keeps_its_established_name(monkeypatch, tmp_path):
    """Regression guard on the name the built-in template already produces.

    Sanitising must not rename the ordinary case: `EPSG:27700` has always become `EPSG_27700`, and an
    operator's stored links and any downstream trigger filters depend on that.
    """
    harness = _Harness(scan_layout=[1])
    harness.install(monkeypatch)

    pipeline.run_pipeline(_config(tmp_path), [tmp_path / "cloud.laz"])

    assert harness.writes[0][0].name == "cloud_EPSG_27700.laz"


def _mismatch():
    return ValidationResult(
        file_path="cloud.laz",
        passed=False,
        message="detected EPSG:32613, configured EPSG:4326",
        detected_crs="EPSG:32613",
        expected_crs="EPSG:4326",
        confidence=CrsConfidence.HIGH,
    )


def test_a_crs_mismatch_raises_a_catchable_exception(monkeypatch, tmp_path):
    """`on_mismatch=error` must reject with something derived from `Exception`.

    A `SystemExit` derives from `BaseException`, so a plain `except Exception` around the call cannot
    catch it. In the container that meant nothing reported the internal task token, and the
    WAIT_FOR_TASK_TOKEN task waited out its four-hour timeout on a failure detectable in seconds.
    """
    harness = _Harness(scan_layout=[1])
    harness.install(monkeypatch, validation_results=[_mismatch()])

    try:
        pipeline.run_pipeline(
            _config(tmp_path, on_mismatch=OnMismatch.ERROR), [tmp_path / "cloud.laz"]
        )
    except BaseException as exc:  # noqa: BLE001 - which base class it is, is the assertion
        raised = exc
    else:
        raised = None

    assert raised is not None, "a mismatch with on_mismatch=error must reject the run"
    assert isinstance(raised, Exception), (
        f"{type(raised).__name__} does not derive from Exception, so a plain `except Exception` "
        "cannot report it to Step Functions"
    )
    assert "CRS validation failed" in str(raised)
    assert "EPSG:32613" in str(raised), (
        "the detected CRS is what tells the operator what to fix, and core.py copies this message "
        "into the execution's error"
    )


def test_the_rejection_happens_before_any_point_is_read(monkeypatch, tmp_path):
    """The rejection is worth nothing if the container has already paid for the transform."""
    harness = _Harness(scan_layout=[5])
    harness.install(monkeypatch, validation_results=[_mismatch()])

    with pytest.raises(Exception):
        pipeline.run_pipeline(
            _config(tmp_path, on_mismatch=OnMismatch.ERROR), [tmp_path / "cloud.laz"]
        )

    assert harness.events == [], "no chunk should be read once validation has failed"


def test_a_mismatch_under_warn_still_transforms(monkeypatch, tmp_path):
    """The positive control for the two tests above.

    Without it, both are satisfied by a pipeline that rejects every run -- a worse regression than the
    one being fixed.
    """
    harness = _Harness(scan_layout=[2])
    harness.install(monkeypatch, validation_results=[_mismatch()])

    report = pipeline.run_pipeline(
        _config(tmp_path, on_mismatch=OnMismatch.WARN), [tmp_path / "cloud.laz"]
    )

    assert report.errors == []
    assert [path.name for path in report.output_files] == ["cloud_EPSG_27700.laz"]
    assert report.total_points_processed == 20


def test_a_report_is_written_even_when_the_input_yields_no_points(monkeypatch, tmp_path):
    """The output directory must exist before the report is written.

    Creating it lazily per scan leaves a zero-point input with nowhere to write `transform_report.json`,
    and the report is what carries the per-file errors an operator reads.
    """
    harness = _Harness(scan_layout=[])
    harness.install(monkeypatch)

    report = pipeline.run_pipeline(_config(tmp_path), [tmp_path / "cloud.laz"])

    assert report.output_files == []
    assert (tmp_path / "output" / "transform_report.json").is_file()


# --- compressLaz and outputFormats govern one property, so they must agree ------------------------
#
# S4-PIPELINES-026. `compress_laz` travelled from asset metadata / the template body into a dead
# `get_writer` argument and was discarded, so `compressLaz: false` with `outputFormats: ["laz"]` ran
# to a successful, still-compressed LAZ. The option is kept and VALIDATED: LAZ is the compressed LAS
# format, so the contradiction is refused rather than served with one setting dropped.
#
# These arms are the container half's only proof. The same rule is enforced earlier, in the
# constructPipeline Lambda, which terminates the run before Batch starts -- so no live execution arm
# reaches this code at all, whether it was written correctly, written at the unreachable
# local_test-guarded site in core.py, or never written.


def _compression_config(tmp_path, formats, compress_laz):
    return PipelineConfig(
        name="vams-coordinate-transform",
        version="1.0",
        source=SourceConfig(crs="EPSG:4326"),
        target=TargetConfig(crs="EPSG:27700"),
        transform=TransformConfig(chunk_size=100),
        validation=ValidationConfig(on_mismatch=OnMismatch.WARN),
        output=OutputConfig(
            formats=formats, directory=tmp_path / "output", compress_laz=compress_laz
        ),
    )


def test_uncompressed_laz_is_refused(monkeypatch, tmp_path):
    harness = _Harness(scan_layout=[1])
    harness.install(monkeypatch)

    with pytest.raises(ValueError) as excinfo:
        pipeline.run_pipeline(
            _compression_config(tmp_path, [OutputFormat.LAZ], False),
            [tmp_path / "cloud.laz"],
        )

    message = str(excinfo.value)
    # Both names have to appear, or an operator cannot tell which pair of settings disagreed.
    assert "compress_laz" in message and "laz" in message, message
    assert harness.events == [], (
        "the run must be refused before any chunk is read, so no partial output is produced"
    )


def test_uncompressed_laz_is_refused_alongside_other_formats(monkeypatch, tmp_path):
    """A laz entry anywhere in the format list is the contradiction, not only a list of exactly laz."""
    harness = _Harness(scan_layout=[1])
    harness.install(monkeypatch)

    with pytest.raises(ValueError):
        pipeline.run_pipeline(
            _compression_config(tmp_path, [OutputFormat.LAS, OutputFormat.LAZ], False),
            [tmp_path / "cloud.laz"],
        )


def test_uncompressed_las_is_accepted(monkeypatch, tmp_path):
    """The control that makes the two above mean something.

    `compress_laz=False` is refused for its CONTRADICTION with a laz output, not for being false --
    otherwise the option would have been removed rather than validated, and the uncompressed request
    would be inexpressible.
    """
    harness = _Harness(scan_layout=[1])
    harness.install(monkeypatch)

    report = pipeline.run_pipeline(
        _compression_config(tmp_path, [OutputFormat.LAS], False),
        [tmp_path / "cloud.laz"],
    )

    assert [path.name for path in report.output_files] == ["cloud_EPSG_27700.las"]


def test_compressed_laz_is_accepted(monkeypatch, tmp_path):
    """The second control: the default combination, which every existing run uses, is untouched."""
    harness = _Harness(scan_layout=[1])
    harness.install(monkeypatch)

    report = pipeline.run_pipeline(
        _compression_config(tmp_path, [OutputFormat.LAZ], True),
        [tmp_path / "cloud.laz"],
    )

    assert [path.name for path in report.output_files] == ["cloud_EPSG_27700.laz"]


def test_a_laz_free_format_list_is_accepted_with_the_default_true(monkeypatch, tmp_path):
    """The asymmetry, stated as a test: only false + laz is refused.

    `compress_laz` defaults to True, so rejecting True alongside a laz-free format list would fail
    every ordinary `outputFormats: ["las"]` run that never mentioned compression.
    """
    harness = _Harness(scan_layout=[1])
    harness.install(monkeypatch)

    report = pipeline.run_pipeline(
        _compression_config(tmp_path, [OutputFormat.LAS], True),
        [tmp_path / "cloud.laz"],
    )

    assert [path.name for path in report.output_files] == ["cloud_EPSG_27700.las"]
