# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`CoordinateTransformer` must reject a NaN or infinite coordinate where the reprojection produces it,
naming the count, the first offending input coordinate and the CRS pair.

Run from the container directory:  python -m pytest tests/test_transform_non_finite_rejection.py -q

pyproj marks a point it cannot transform as inf rather than raising. Left alone, a NaN or inf coordinate
propagates through the spill's running min/max into the LAS header offsets and scales, laspy writes a
cloud whose X reads back as NaN with +/-DBL_MAX bounds, the VAMS route then fails post-write on
"X bounds inverted" and the `coord-xform transform --config` route reports success. Neither names the
coordinate.

These drive the REAL `coord_xform.transform` with only its `pyproj` binding replaced, so the constructor,
the chunk path and the camera path exercised here are the ones the container runs. The last test loads
`coord_xform/pipeline.py` under a private name (conftest stubs the public one for `core.py`'s late
import) and shows the rejection arriving in `PipelineReport.errors`, which is what `core.py` raises to
the operator.
"""

import importlib
import importlib.util
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

    `coord_xform.transform` annotates return types as `pyproj.CRS`, which is evaluated at import time,
    so a bare `ModuleType` is not enough to let it import.
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

import coord_xform.transform as transform_module  # noqa: E402
from coord_xform.config import (  # noqa: E402
    OutputConfig,
    OutputFormat,
    PipelineConfig,
    SourceConfig,
    TargetConfig,
    TransformConfig,
)
from coord_xform.models import (  # noqa: E402
    CameraExtrinsics,
    InputFormat,
    PointChunk,
    ScanDataset,
)

SOURCE_CRS = "EPSG:4326"
TARGET_CRS = "EPSG:27700"


def _config(tmp_path):
    return PipelineConfig(
        name="vams-coordinate-transform",
        version="1.0",
        source=SourceConfig(crs=SOURCE_CRS),
        target=TargetConfig(crs=TARGET_CRS),
        transform=TransformConfig(chunk_size=100),
        output=OutputConfig(formats=[OutputFormat.LAZ], directory=tmp_path / "output"),
    )


class _FakeCrs:
    """Enough of `pyproj.CRS` for the constructor, `_is_cross_unit_transform` and `to_wkt`."""

    axis_info = []

    def __init__(self, spec):
        self.spec = spec

    def to_wkt(self):
        return f"WKT({self.spec})"


def _fake_pyproj(projection):
    """A `pyproj` stand-in whose `Transformer.transform` is the given callable."""
    crs = types.SimpleNamespace(
        from_epsg=lambda code: _FakeCrs(f"EPSG:{code}"),
        from_proj4=lambda text: _FakeCrs(text),
        from_wkt=lambda text: _FakeCrs(text),
    )
    transformer = types.SimpleNamespace(
        from_crs=lambda source, target, always_xy: types.SimpleNamespace(
            transform=projection
        )
    )
    return types.SimpleNamespace(CRS=crs, Transformer=transformer)


def _projection_failing_at(failures):
    """A projection doubling X and Y, with `failures` = {row: (column, marker)} written afterwards.

    The doubling makes the transformed output distinguishable from the input, so the control can tell a
    pass-through from a real result and the failure message can be checked for naming the INPUT.
    """

    def project(x, y, z):
        out = [
            np.asarray(x, dtype=np.float64) * 2.0,
            np.asarray(y, dtype=np.float64) * 2.0,
            np.asarray(z, dtype=np.float64).copy(),
        ]
        for row, (column, marker) in failures.items():
            out[column][row] = marker
        return tuple(out)

    return project


def _transformer(monkeypatch, tmp_path, projection):
    monkeypatch.setattr(transform_module, "pyproj", _fake_pyproj(projection))
    return transform_module.CoordinateTransformer(_config(tmp_path))


_FIVE_POINTS = np.array(
    [
        [-0.1275, 51.5072, 10.0],
        [1.5, 51.5, 11.0],
        [-3.1883, 55.9533, 12.0],
        [178.0, -0.5, 13.0],
        [-1.2577, 51.7520, 14.0],
    ],
    dtype=np.float64,
)


def test_a_chunk_reprojecting_to_nan_is_rejected_naming_the_count_and_the_first_input(
    monkeypatch, tmp_path
):
    """Rows 1, 3 and 4 fail, on different axes; the message counts three and names row 1's INPUT."""
    transformer = _transformer(
        monkeypatch,
        tmp_path,
        _projection_failing_at({1: (0, np.nan), 3: (0, np.nan), 4: (1, np.nan)}),
    )

    with pytest.raises(transform_module.NonFiniteCoordinateError) as excinfo:
        transformer.transform_chunk(PointChunk(xyz=_FIVE_POINTS))

    message = str(excinfo.value)
    assert "3 of 5 points" in message, message
    assert SOURCE_CRS in message and TARGET_CRS in message, message
    # The first offending row is 1, not 0, and it is named by its input coordinate so an operator can
    # find the point in the source file.
    assert "[1.5, 51.5, 11.0]" in message, message
    assert "[-0.1275, 51.5072, 10.0]" not in message, message
    # The produced value is reported as it is, not replaced by a number.
    assert "nan" in message, message


def test_the_inf_marker_pyproj_uses_for_a_failed_point_is_rejected_too(monkeypatch, tmp_path):
    """pyproj signals a point it cannot transform with inf, not NaN, so isnan alone would miss it."""
    transformer = _transformer(monkeypatch, tmp_path, _projection_failing_at({2: (1, np.inf)}))

    with pytest.raises(transform_module.NonFiniteCoordinateError) as excinfo:
        transformer.transform_chunk(PointChunk(xyz=_FIVE_POINTS[:4]))

    message = str(excinfo.value)
    assert "1 of 4 points" in message, message
    assert "[-3.1883, 55.9533, 12.0]" in message, message


def test_a_finite_chunk_passes_through_unchanged(monkeypatch, tmp_path):
    """Control: the guard fires on non-finite output only, and it does not touch a finite result."""
    transformer = _transformer(monkeypatch, tmp_path, _projection_failing_at({}))

    result = transformer.transform_chunk(PointChunk(xyz=_FIVE_POINTS))

    expected = _FIVE_POINTS.copy()
    expected[:, 0] *= 2.0
    expected[:, 1] *= 2.0
    assert np.array_equal(result.xyz, expected)
    assert np.isfinite(result.residual_error_mm)


def test_the_rejection_is_a_valueerror():
    """The pipeline's per-file wrapper catches `Exception`; keeping this a ValueError also lets a caller
    that already handles bad input handle it without learning a new type."""
    assert issubclass(transform_module.NonFiniteCoordinateError, ValueError)


def test_a_camera_position_reprojecting_to_nan_is_rejected(monkeypatch, tmp_path):
    """A camera position goes straight into a JSON sidecar, where json.dump writes NaN unquoted."""
    transformer = _transformer(
        monkeypatch, tmp_path, lambda x, y, z: (float("nan"), y * 2.0, z)
    )
    camera = CameraExtrinsics(position=np.array([-0.1275, 51.5072, 10.0]))

    with pytest.raises(transform_module.NonFiniteCoordinateError) as excinfo:
        transformer.transform_camera(camera)

    message = str(excinfo.value)
    assert "1 of 1 camera positions" in message, message
    assert "[-0.1275, 51.5072, 10.0]" in message, message
    assert SOURCE_CRS in message and TARGET_CRS in message, message


def test_a_finite_camera_position_passes_through(monkeypatch, tmp_path):
    """Control for the camera path."""
    transformer = _transformer(monkeypatch, tmp_path, lambda x, y, z: (x * 2.0, y * 2.0, z))
    camera = CameraExtrinsics(position=np.array([-0.1275, 51.5072, 10.0]))

    moved = transformer.transform_camera(camera)

    assert np.array_equal(moved.position, np.array([-0.255, 103.0144, 10.0]))


def _load_pipeline_module():
    """Load `coord_xform/pipeline.py` under a private name, leaving conftest's stub registered."""
    path = os.path.join(_CONTAINER, "coord_xform", "pipeline.py")
    spec = importlib.util.spec_from_file_location(
        "coord_xform_pipeline_non_finite_under_test", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_rejection_reaches_the_report_as_a_per_file_error_and_nothing_is_written(
    monkeypatch, tmp_path
):
    """The route the operator sees: `core.py` raises `PipelineReport.errors` verbatim, so the count and
    the coordinate have to survive `run_pipeline`'s per-file wrapper -- and no writer may have been
    handed the cloud in the meantime."""
    pipeline = _load_pipeline_module()
    # The pipeline must be holding the same class this file patches, or the fake pyproj below would not
    # be the one it reprojects with and the assertion could pass against a different transformer.
    assert pipeline.CoordinateTransformer is transform_module.CoordinateTransformer

    monkeypatch.setattr(
        transform_module,
        "pyproj",
        _fake_pyproj(_projection_failing_at({4: (0, np.nan), 7: (2, np.inf)})),
    )
    monkeypatch.setattr(pipeline, "validate_inputs", lambda config, inputs: [])
    monkeypatch.setattr(
        pipeline, "discover_scan_dataset", lambda path: ScanDataset(point_cloud_path=path)
    )
    monkeypatch.setattr(pipeline, "detect_format", lambda path: InputFormat.LAZ)

    class _Reader:
        def read_chunks(self, path, chunk_size):
            yield PointChunk(xyz=np.tile(_FIVE_POINTS, (2, 1)))

    monkeypatch.setattr(pipeline, "get_reader", lambda fmt: _Reader())

    writes = []

    class _Writer:
        def write(self, path, spill, crs_wkt):
            writes.append(path)

    monkeypatch.setattr(pipeline, "get_writer", lambda fmt: _Writer())

    report = pipeline.run_pipeline(_config(tmp_path), [tmp_path / "cloud.laz"])

    assert len(report.errors) == 1, report.errors
    error = report.errors[0]
    assert "NonFiniteCoordinateError" in error, error
    assert "2 of 10 points" in error, error
    assert "[-1.2577, 51.752, 14.0]" in error, error
    assert SOURCE_CRS in error and TARGET_CRS in error, error
    assert writes == [], "a cloud carrying a non-finite coordinate must not reach a writer"
    assert report.output_files == []
