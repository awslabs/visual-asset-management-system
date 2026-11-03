#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Every line the handler logs for a workflow run carries the run's workflow execution id.

VAMS shows a run's log for this step by reading the function's Amazon CloudWatch log group, which
every run of the function shares, for the lines that contain the workflow execution id as a literal
term (``executionService.get_execution_logs``). A warning from ``referenced_files.py`` -- a material
library or texture skipped, a file whose references cannot be read -- names a file and not the run,
so unless the id is on the line the warning reaches CloudWatch and is missing from the run's log in
VAMS, and a run that dropped its materials reads as clean.

The handler runs here with the container's real logger (``common/logger.py`` on Powertools) writing
to a buffer, against an in-memory asset, and the JSON lines it wrote are read back. No path in these
runs contains the execution id, so a line carries it only through the logger.
"""

import importlib.util
import io
import json
import logging
import os
import sys
import types
import uuid
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("aws_lambda_powertools")

from botocore.exceptions import ClientError  # noqa: E402

_LAMBDA_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _LAMBDA_DIR not in sys.path:
    sys.path.insert(0, _LAMBDA_DIR)

# Stub common.logger + trimesh as the sibling modules do, so whichever module is collected first
# leaves the same sys.modules behind. The real logger is loaded from its file below.
if "common" not in sys.modules:
    _common_pkg = types.ModuleType("common")
    _common_logger = types.ModuleType("common.logger")
    _common_logger.safeLogger = lambda **kw: MagicMock()
    _common_pkg.logger = _common_logger
    sys.modules["common"] = _common_pkg
    sys.modules["common.logger"] = _common_logger
if "trimesh" not in sys.modules:
    sys.modules["trimesh"] = MagicMock()

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_REGION", "us-east-1")

EXECUTION_ID = "3f9c2a7be41d4c0f9a6e5d8b7c1a2f30"
OTHER_EXECUTION_ID = "8d1e6b40a2c94f7e93b05a6c4d2e1f78"

ASSET_BUCKET = "abkt"
OUTPUT_BUCKET = "obkt"
MANIFEST_KEY = "runs/r1/inputs/manifest.json"
CONFIGURATION_KEY = "runs/r1/inputs/configuration.json"
INPUT_KEY = "assets/a1/box.obj"

# An OBJ naming a material library the asset does not hold: skipped, with a warning.
OBJ_MISSING_MTL = b"mtllib box.mtl\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"
# A material library that is not UTF-8: its references cannot be read, with a warning. (An OBJ input
# that is not UTF-8 fails the run before its references are read, #910.)
MTL_KEY = "assets/a1/box.mtl"
MTL_NOT_UTF8 = b"newmtl m\n# caf\xe9\nKd 1 0 0\nmap_Kd box.png\n"

SKIPPED_WARNING = "Skipped box.mtl, referenced by box.obj"
UNREADABLE_WARNING = "Could not read the references in box.mtl"


def _load_handler():
    spec = importlib.util.spec_from_file_location(
        "trimesh_pipeline_lambda_execution_id_logging", os.path.join(_LAMBDA_DIR, "lambda.py"))
    module = importlib.util.module_from_spec(spec)
    with patch("boto3.client", MagicMock()), patch("boto3.resource", MagicMock()):
        spec.loader.exec_module(module)
    return module


@pytest.fixture
def container_logger(monkeypatch):
    """``(logger, buffer)``: the container's logger, built by ``common/logger.py``, writing to ``buffer``.

    Loaded by file path because ``common`` in ``sys.modules`` may be a sibling module's stub. Each
    logger gets its own service name, since Powertools sets a named logger up once and a second
    Logger of that name would keep the first one's handler. ``safeLogger`` replaces
    ``sys.excepthook``; ``monkeypatch`` puts the original back.
    """
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    spec = importlib.util.spec_from_file_location(
        f"conversion_3dbasic_common_logger_{uuid.uuid4().hex}",
        os.path.join(_LAMBDA_DIR, "common", "logger.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    buffer = io.StringIO()
    logger = module.safeLogger(service=f"conversionTrimeshPipeline-{uuid.uuid4().hex}",
                               logger_handler=logging.StreamHandler(buffer))
    return logger, buffer


class _Storage:
    """Amazon S3 as the handler sees it: the run's manifest and configuration, and the input's asset.
    HeadObject on a key it does not hold answers 404."""

    def __init__(self, input_bytes, asset_files=None):
        manifest = {
            "inputFiles": [{"bucket": ASSET_BUCKET, "key": INPUT_KEY, "relativePath": "/box.obj"}],
            "outputs": {"bucket": OUTPUT_BUCKET, "files": "runs/r1/files/"},
        }
        self.objects = {
            (ASSET_BUCKET, MANIFEST_KEY): json.dumps(manifest).encode("utf-8"),
            (ASSET_BUCKET, CONFIGURATION_KEY): json.dumps({"outputType": ".glb"}).encode("utf-8"),
            (ASSET_BUCKET, INPUT_KEY): input_bytes,
        }
        for key, data in (asset_files or {}).items():
            self.objects[(ASSET_BUCKET, key)] = data
        self.uploaded = []

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)])}

    def head_object(self, Bucket, Key):
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "404", "Message": "Not Found"},
                               "ResponseMetadata": {"HTTPStatusCode": 404}}, "HeadObject")
        return {"ContentLength": len(self.objects[(Bucket, Key)])}

    def download_fileobj(self, Bucket, Key, Fileobj, ExtraArgs=None):
        Fileobj.write(self.objects[(Bucket, Key)])

    def upload_file(self, Filename, Bucket, Key, ExtraArgs=None, Config=None, Callback=None):
        self.uploaded.append(Key)


class _Mesh:
    vertices = [[0.0, 0.0, 0.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]
    faces = [[0, 1, 2]]

    def export(self, path, file_type=None):
        with open(path, "wb") as handle:
            handle.write(b"glTF")


_TRIMESH = SimpleNamespace(load=lambda path, resolver=None: _Mesh())

# A body with no workflowExecutionId key at all, as a direct invocation sends.
_ABSENT = object()


def _body(execution_id):
    body = {
        "inputManifestS3Location": f"s3://{ASSET_BUCKET}/{MANIFEST_KEY}",
        "inputConfigurationS3Location": f"s3://{ASSET_BUCKET}/{CONFIGURATION_KEY}",
    }
    if execution_id is not _ABSENT:
        body["workflowExecutionId"] = execution_id
    return body


def _run(mod, logger, buffer, execution_id, input_bytes=OBJ_MISSING_MTL, asset_files=None):
    """Run the handler once; return its response, the raw lines it logged, and those lines parsed."""
    storage = _Storage(input_bytes, asset_files)
    start = len(buffer.getvalue())
    # The input is read through s3_client on both release lines; the upload goes through s3_client
    # or s3.meta.client depending on the line, so both point at the same in-memory storage.
    with patch.object(mod, "logger", logger), patch.object(mod, "s3_client", storage), \
            patch.object(mod, "s3", SimpleNamespace(meta=SimpleNamespace(client=storage))), \
            patch.object(mod, "trimesh", _TRIMESH):
        response = mod.lambda_handler({"body": json.dumps(_body(execution_id))}, MagicMock())
    assert storage.uploaded == ["runs/r1/files/box.glb"]
    raw_lines = buffer.getvalue()[start:].splitlines()
    return response, raw_lines, [json.loads(line) for line in raw_lines]


def _the_line(raw_lines, records, prefix):
    matches = [(raw, record) for raw, record in zip(raw_lines, records)
               if str(record.get("message", "")).startswith(prefix)]
    assert len(matches) == 1, f"expected one line starting {prefix!r}, got {records}"
    return matches[0]


@pytest.mark.unit
class TestTheRunsLinesCarryItsExecutionId:
    @pytest.mark.parametrize("asset_files,prefix", [
        (None, SKIPPED_WARNING),
        ({MTL_KEY: MTL_NOT_UTF8}, UNREADABLE_WARNING),
    ], ids=["material-library-skipped", "references-unreadable"])
    def test_a_referenced_file_warning_carries_the_execution_id(self, container_logger, asset_files,
                                                                prefix):
        logger, buffer = container_logger
        response, raw_lines, records = _run(_load_handler(), logger, buffer, EXECUTION_ID,
                                            asset_files=asset_files)
        assert response["statusCode"] == 200
        raw, record = _the_line(raw_lines, records, prefix)
        assert record["level"] == "WARNING"
        # The message itself does not name the run, so the id on the line comes from the logger.
        assert EXECUTION_ID not in record["message"]
        assert record["workflowExecutionId"] == EXECUTION_ID
        # The execution logs API matches the id as a literal term in the line's text.
        assert EXECUTION_ID in raw

    def test_every_line_of_the_run_carries_the_execution_id(self, container_logger):
        logger, buffer = container_logger
        _, _, records = _run(_load_handler(), logger, buffer, EXECUTION_ID)
        assert [r["message"] for r in records if r.get("workflowExecutionId") != EXECUTION_ID] == []
        # In-band control: the run logged its usual lines, the warning among them.
        messages = [r["message"] for r in records]
        assert "Conversion request received" in messages and "Conversion complete" in messages
        assert any(m.startswith(SKIPPED_WARNING) for m in messages)


@pytest.mark.unit
class TestARunWithoutAnExecutionId:
    @pytest.mark.parametrize("execution_id", [_ABSENT, "", None, 42],
                             ids=["absent", "empty", "null", "not-a-string"])
    def test_it_converts_and_logs_without_an_id(self, container_logger, execution_id):
        logger, buffer = container_logger
        response, raw_lines, records = _run(_load_handler(), logger, buffer, execution_id)
        assert response["statusCode"] == 200
        _the_line(raw_lines, records, SKIPPED_WARNING)
        assert [r["message"] for r in records if "workflowExecutionId" in r] == []

    def test_a_reused_environment_does_not_carry_the_previous_runs_id(self, container_logger):
        # A warm Lambda execution environment keeps the module, and with it the logger and its keys.
        logger, buffer = container_logger
        mod = _load_handler()
        _run(mod, logger, buffer, EXECUTION_ID)
        _, _, without = _run(mod, logger, buffer, _ABSENT)
        assert [r["message"] for r in without if "workflowExecutionId" in r] == []
        _run(mod, logger, buffer, EXECUTION_ID)
        _, _, other = _run(mod, logger, buffer, OTHER_EXECUTION_ID)
        assert {r.get("workflowExecutionId") for r in other} == {OTHER_EXECUTION_ID}
