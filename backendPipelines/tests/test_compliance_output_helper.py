#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""The compliance output helper writes the document the compliance callback reads.

``backendPipelines/common/compliance_output.py`` is the canonical copy a pipeline vendors, the way it
vendors ``manifestHelper.py``: a pipeline's Lambda code asset and container build context hold only its
own directory, so the canonical copy is not importable from a deployed pipeline. These tests cover the
helper's document and location, the two names the backend matches on (the file name the compliance
callback looks for and the ``complianceOutput: true`` flag its parser requires), and that every vendored
copy is byte-identical to the canonical one.

Durable (root CLAUDE.md Rule 13): a vendored copy can drift on any edit, and the backend's file name or
flag can change without this helper, so these are guardrails, not pins on one past change.
"""

import glob
import hashlib
import importlib.util
import json
import os
import re
import sys

import pytest

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINES_DIR = os.path.join(_REPO_ROOT, "backendPipelines")
CANONICAL_COPY = os.path.normpath(os.path.join(_PIPELINES_DIR, "common", "compliance_output.py"))
BACKEND_ENGINE = os.path.join(
    _REPO_ROOT, "backend", "backend", "common", "compliance", "evaluationEngine.py")


def _load_helper():
    """The canonical helper, executed under a name only this suite uses so a vendored copy of the
    same basename cannot shadow it (backendPipelines/CLAUDE.md, Test Conventions)."""
    spec = importlib.util.spec_from_file_location(
        "backendpipelines_common_compliance_output_undertest", CANONICAL_COPY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


helper = _load_helper()


class _RecordingS3Client:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)
        return {}


def _manifest(bucket="asset-bucket", results="db/asset/runs/exec-1/results/"):
    return {"outputs": {"bucket": bucket, "bucketRegion": "", "files": "f/", "previews": "p/",
                        "metadata": "m/", "results": results}}


@pytest.mark.unit
class TestDocument:
    def test_a_success_document_carries_the_flag_status_and_measurements(self):
        document = helper.build_compliance_output({"residual_error_mm": 0.42})
        assert document == {
            "complianceOutput": True,
            "status": "success",
            "measurements": {"residual_error_mm": 0.42},
            "errors": [],
            "pipelineMetadata": {},
        }

    def test_an_error_document_carries_its_messages(self):
        document = helper.build_compliance_output(
            {}, status="error", errors=["the mesh could not be read"], pipeline_metadata={"tool": "x"})
        assert document["status"] == "error"
        assert document["errors"] == ["the mesh could not be read"]
        assert document["pipelineMetadata"] == {"tool": "x"}


@pytest.mark.unit
class TestLocation:
    def test_the_manifest_results_prefix_is_the_location(self):
        assert helper.results_output_location(_manifest()) == (
            "s3://asset-bucket/db/asset/runs/exec-1/results/")

    @pytest.mark.parametrize("manifest", [
        {}, {"outputs": {}}, _manifest(bucket=""), _manifest(results=""),
    ])
    def test_a_manifest_without_a_results_location_is_refused(self, manifest):
        with pytest.raises(ValueError):
            helper.results_output_location(manifest)

    @pytest.mark.parametrize("path, expected", [
        ("s3://bucket/a/b/", ("bucket", "a/b/")),
        ("s3://bucket/a/b", ("bucket", "a/b/")),
        ("bucket/a/b//", ("bucket", "a/b/")),
        ("s3://bucket", ("bucket", "")),
    ])
    def test_a_results_path_splits_into_bucket_and_one_slash_prefix(self, path, expected):
        assert helper.parse_s3_path(path) == expected


@pytest.mark.unit
class TestWrite:
    def test_the_document_is_written_under_the_resolved_results_path(self):
        client = _RecordingS3Client()
        location = helper.write_compliance_output(
            client, {"vertex_count": 120345},
            results_s3_path="s3://asset-bucket/db/asset/runs/exec-1/results/")
        assert location == (
            "s3://asset-bucket/db/asset/runs/exec-1/results/compliance-output.json")
        (call,) = client.calls
        assert call["Bucket"] == "asset-bucket"
        assert call["Key"] == "db/asset/runs/exec-1/results/compliance-output.json"
        assert call["ContentType"] == "application/json"
        assert json.loads(call["Body"].decode("utf-8")) == helper.build_compliance_output(
            {"vertex_count": 120345})

    def test_the_manifest_gives_the_same_location(self):
        client = _RecordingS3Client()
        helper.write_compliance_output(client, {"x": 1}, manifest=_manifest())
        assert client.calls[0]["Key"] == "db/asset/runs/exec-1/results/compliance-output.json"

    def test_an_error_status_is_written_as_given(self):
        client = _RecordingS3Client()
        helper.write_compliance_output(
            client, {}, results_s3_path="s3://b/r/", status="error", errors=["unreadable input"])
        body = json.loads(client.calls[0]["Body"].decode("utf-8"))
        assert body["status"] == "error"
        assert body["errors"] == ["unreadable input"]


@pytest.mark.unit
class TestBackendContract:
    """The compliance callback reads only a results row whose path ends with the backend's file name,
    and its parser accepts only a document whose `complianceOutput` is the JSON boolean `true`."""

    def _engine_source(self):
        with open(BACKEND_ENGINE, encoding="utf-8") as handle:
            return handle.read()

    def test_the_file_name_matches_the_one_the_callback_reads(self):
        match = re.search(r'^COMPLIANCE_OUTPUT_FILE_NAME = "([^"]+)"$', self._engine_source(), re.M)
        assert match, "evaluationEngine.py no longer defines COMPLIANCE_OUTPUT_FILE_NAME"
        assert helper.COMPLIANCE_OUTPUT_FILE_NAME == match.group(1)

    def test_the_parser_still_requires_the_boolean_flag(self):
        assert 'document.get("complianceOutput") is not True' in self._engine_source()
        assert helper.build_compliance_output({})["complianceOutput"] is True


def _copies():
    pattern = os.path.join(_PIPELINES_DIR, "**", "compliance_output.py")
    return sorted(os.path.normpath(p) for p in glob.glob(pattern, recursive=True)
                  if "__pycache__" not in p)


def _digest(path):
    with open(path, "rb") as handle:
        return hashlib.md5(handle.read(), usedforsecurity=False).hexdigest()


def _rel(path):
    return os.path.relpath(path, _REPO_ROOT).replace("\\", "/")


@pytest.mark.unit
class TestVendoredCopiesAreByteIdentical:
    def test_the_glob_finds_the_canonical_copy(self):
        """Control for the identity assertion: a glob that matches nothing would satisfy it."""
        assert CANONICAL_COPY in _copies()

    def test_every_copy_matches_the_canonical_copy(self):
        canonical = _digest(CANONICAL_COPY)
        drifted = {_rel(c): _digest(c) for c in _copies() if _digest(c) != canonical}
        assert drifted == {}, (
            "compliance_output.py copies differ from the canonical copy; refresh them with\n"
            "  for f in $(find backendPipelines -name compliance_output.py); do "
            f"cp {_rel(CANONICAL_COPY)} \"$f\"; done\n"
            f"drifted: {drifted}")
