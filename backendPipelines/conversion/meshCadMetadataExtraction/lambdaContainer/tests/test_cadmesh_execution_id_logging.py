#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every line a CAD/mesh metadata extraction run logs carries the run's workflow execution id,
including the lines the extractors log.

VAMS shows a run's log for this step by reading the function's Amazon CloudWatch log group, which
every run of the function shares, for the lines that contain the workflow execution id as a literal
term (``executionService.get_execution_logs``). The warnings ``metadata_extractors/*.py`` log when a
section of a model cannot be read name the section and not the run, so unless the id is on the line
the warning reaches CloudWatch and is missing from the run's log in VAMS.

The handler and the real ``metadata_extractors`` package run here with the container's real logger
(``common/logger.py`` on Powertools), with only ``cadquery`` and ``trimesh`` stubbed, and the JSON
lines the shared handler writes are read back. No path in these runs contains the execution id, so a
line carries it only through the logger.
"""

import importlib.util
import io
import json
import logging
import os
import sys
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("aws_lambda_powertools")
# Imported before the fixture snapshots sys.modules, so restoring the snapshot never drops it and the
# next import of mesh_extractor does not reload NumPy.
pytest.importorskip("numpy")

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICE = "cadMetadataExtractionPipeline"
EXECUTION_ID = "5be1c7d04a2f4e6b8c9d0a1b2c3d4e5f"
OTHER_EXECUTION_ID = "a0b1c2d3e4f5465788796a5b4c3d2e1f"

ASSET_BUCKET = "abkt"
OUTPUT_BUCKET = "obkt"
MANIFEST_KEY = "runs/r1/inputs/manifest.json"
METADATA_PREFIX = "runs/r1/metadata/"

MESH_WARNING = "Error extracting geometric metadata"
CAD_WARNING = "Error extracting shape statistics"


class _UnreadableMesh:
    """A loaded mesh whose bounds cannot be read: the geometric section warns and the run goes on."""

    @property
    def bounds(self):
        raise ValueError("bounds are not available")


class _UnreadableSolid:
    """An imported STEP solid whose bounding box and topology cannot be read."""

    def ShapeType(self):
        return "Solid"

    def BoundingBox(self):
        raise ValueError("bounding box is not available")

    def Solids(self):
        raise ValueError("topology is not available")


class _Scene:
    pass


def _trimesh_stub():
    module = types.ModuleType("trimesh")
    module.Scene = _Scene
    module.load = lambda path: _UnreadableMesh()
    return module


def _cadquery_stub():
    module = types.ModuleType("cadquery")
    module.Shape = object
    module.Workplane = object
    module.importers = SimpleNamespace(importStep=lambda path: _UnreadableSolid(),
                                       importDXF=MagicMock())
    return module


def _load_by_path(name, path, package_dir=None):
    kwargs = {"submodule_search_locations": [package_dir]} if package_dir else {}
    spec = importlib.util.spec_from_file_location(name, path, **kwargs)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def handler(monkeypatch):
    """``(lambda module, buffer)``: the handler and the real extractors on the container's logger.

    Sibling modules stub ``common.logger`` and ``metadata_extractors`` in ``sys.modules``; the real
    modules are loaded under those names only for this test and the stubs come back afterwards.
    Powertools configures the ``SERVICE`` logger once per process, so its handler's stream is pointed
    at ``buffer`` here and the appended keys are cleared on the way out. ``safeLogger`` replaces
    ``sys.excepthook``; ``monkeypatch`` puts the original back.
    """
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    managed = [name for name in sys.modules
               if name.split(".")[0] in ("common", "metadata_extractors", "trimesh", "cadquery")]
    with patch.dict(sys.modules):
        for name in managed:
            del sys.modules[name]
        sys.modules["trimesh"] = _trimesh_stub()
        sys.modules["cadquery"] = _cadquery_stub()
        common = types.ModuleType("common")
        sys.modules["common"] = common
        common.logger = _load_by_path("common.logger", os.path.join(_LAMBDA_DIR, "common", "logger.py"))
        extractors_dir = os.path.join(_LAMBDA_DIR, "metadata_extractors")
        _load_by_path("metadata_extractors", os.path.join(extractors_dir, "__init__.py"), extractors_dir)
        with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
            module = _load_by_path("cadmesh_pipeline_lambda_execution_id_logging",
                                   os.path.join(_LAMBDA_DIR, "lambda.py"))

        stream_handler = logging.getLogger(SERVICE).handlers[0]
        buffer = io.StringIO()
        previous_stream = stream_handler.setStream(buffer)
        try:
            yield module, buffer
        finally:
            stream_handler.setStream(previous_stream)
            stream_handler.formatter.remove_keys(["workflowExecutionId"])


class _Storage:
    """Amazon S3 as the handler sees it: the run's manifest and the input file."""

    def __init__(self, input_key):
        manifest = {
            "inputFiles": [{"bucket": ASSET_BUCKET, "key": input_key,
                            "relativePath": "/" + os.path.basename(input_key)}],
            "outputs": {"bucket": OUTPUT_BUCKET, "metadata": METADATA_PREFIX},
        }
        self.objects = {
            (ASSET_BUCKET, MANIFEST_KEY): json.dumps(manifest).encode("utf-8"),
            (ASSET_BUCKET, input_key): b"model bytes",
        }
        self.uploaded = []

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def download_fileobj(self, Bucket, Key, Fileobj):
        Fileobj.write(self.objects[(Bucket, Key)])

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None, Config=None, Callback=None):
        self.uploaded.append(Key)


_ABSENT = object()


def _run(mod, buffer, execution_id, input_key="assets/a1/part.obj"):
    """Run the handler once; return its response, the raw lines it logged, and those lines parsed."""
    storage = _Storage(input_key)
    body = {"inputManifestS3Location": f"s3://{ASSET_BUCKET}/{MANIFEST_KEY}"}
    if execution_id is not _ABSENT:
        body["workflowExecutionId"] = execution_id
    start = len(buffer.getvalue())
    with patch.object(mod, "s3_client", storage):
        response = mod.lambda_handler({"body": json.dumps(body)}, MagicMock())
    assert storage.uploaded == [f"{METADATA_PREFIX}{os.path.basename(input_key)}.attribute.json"]
    raw_lines = buffer.getvalue()[start:].splitlines()
    return response, raw_lines, [json.loads(line) for line in raw_lines]


def _the_line(raw_lines, records, prefix):
    matches = [(raw, record) for raw, record in zip(raw_lines, records)
               if str(record.get("message", "")).startswith(prefix)]
    assert len(matches) == 1, f"expected one line starting {prefix!r}, got {records}"
    return matches[0]


@pytest.mark.unit
class TestTheRunsLinesCarryItsExecutionId:
    @pytest.mark.parametrize("input_key,prefix", [
        ("assets/a1/part.obj", MESH_WARNING),
        ("assets/a1/part.step", CAD_WARNING),
    ], ids=["mesh-extractor", "cad-extractor"])
    def test_an_extractor_warning_carries_the_execution_id(self, handler, input_key, prefix):
        mod, buffer = handler
        response, raw_lines, records = _run(mod, buffer, EXECUTION_ID, input_key)
        assert response["statusCode"] == 200
        raw, record = _the_line(raw_lines, records, prefix)
        assert record["level"] == "WARNING"
        # The message itself does not name the run, so the id on the line comes from the logger.
        assert EXECUTION_ID not in record["message"]
        assert record["workflowExecutionId"] == EXECUTION_ID
        # The execution logs API matches the id as a literal term in the line's text.
        assert EXECUTION_ID in raw

    def test_every_line_of_the_run_carries_the_execution_id(self, handler):
        mod, buffer = handler
        _, raw_lines, records = _run(mod, buffer, EXECUTION_ID)
        # The handler's own lines and the extractor's both reach the run's log.
        _the_line(raw_lines, records, "Metadata extraction request received")
        _the_line(raw_lines, records, "Extracting mesh metadata from")
        assert all(record.get("workflowExecutionId") == EXECUTION_ID for record in records), records


@pytest.mark.unit
class TestARunWithoutAnExecutionId:
    @pytest.mark.parametrize("execution_id", [_ABSENT, "", None, 42], ids=["absent", "empty", "null", "number"])
    def test_it_extracts_and_logs_without_an_id(self, handler, execution_id):
        mod, buffer = handler
        response, _, records = _run(mod, buffer, execution_id)
        assert response["statusCode"] == 200
        assert records
        assert not any("workflowExecutionId" in record for record in records), records

    def test_a_reused_environment_does_not_carry_the_previous_runs_id(self, handler):
        mod, buffer = handler
        _run(mod, buffer, EXECUTION_ID)

        _, raw_lines, records = _run(mod, buffer, OTHER_EXECUTION_ID)
        assert all(record.get("workflowExecutionId") == OTHER_EXECUTION_ID for record in records)
        assert not any(EXECUTION_ID in raw for raw in raw_lines)

        _, raw_lines, records = _run(mod, buffer, _ABSENT)
        assert not any("workflowExecutionId" in record for record in records), records
        assert not any(OTHER_EXECUTION_ID in raw for raw in raw_lines)
