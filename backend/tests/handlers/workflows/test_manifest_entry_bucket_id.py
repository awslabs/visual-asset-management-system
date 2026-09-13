# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Manifest ``inputFiles[]`` entries carry ``bucketId``, the asset bucket's registration id.

An entry already locates its file by bucket NAME (``bucket``, ``key``, ``versionId``) and names its asset
(``databaseId``, ``assetId``, ``assetRootS3Key``). The registration id is a different thing: the key of
the asset-buckets table row that carries the bucket's name and ``baseAssetsPrefix``. A consumer that must
resolve that row from the manifest alone -- the vector indexer, reading the id off the embedding event a
pipeline publishes -- cannot get it from the name, so the entry carries it. The execute handler has the
asset row in hand when it writes pipeline 1's manifest and already reads its ``bucketId`` to look the
bucket name up; the same value is written to the entry.

The default is the empty string, never an absent key, so ``entry["bucketId"]`` is safe on any entry and
a pipeline that requires the id tests for ``""``. The guide that tells a pipeline author what an entry
carries is read from the repository, so a key added to the entry without documenting it turns this red.
"""

import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

# executeWorkflow loads these at import.
for _k, _v in {
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "PIPELINE_STORAGE_TABLE_V2_NAME": "t-pipe-v2",
    "PIPELINE_TEMPLATES_STORAGE_TABLE_NAME": "t-templates",
    "PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME": "t-tagschema",
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "S3_ASSETAUXILIARY_STORAGE_BUCKET": "t-aux",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME": "t-pin-md",
    "PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME": "t-pin-cfg",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-cfg",
}.items():
    os.environ.setdefault(_k, _v)

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.common.workflows import executionRecords as er  # noqa: E402
from backend.backend.handlers.workflows import executeWorkflow as ewv2  # noqa: E402

MOD = "backend.backend.handlers.workflows.executeWorkflow"

REPO_ROOT = Path(__file__).resolve().parents[4]
GUIDE = REPO_ROOT / "documentation" / "docusaurus-site" / "docs" / "pipelines" / "custom-pipelines.md"

# The full self-locating shape a pipeline may index.
ENTRY_KEYS = {"relativePath", "databaseId", "assetId", "assetRootS3Key", "auxPreviewPrefix",
              "bucket", "bucketId", "key", "versionId"}


def _entry(**overrides):
    kwargs = dict(relative_path="/models/pump.glb", bucket="asset-bucket", key="xidA/models/pump.glb",
                  version_id="v1", database_id="db1", asset_id="xidA", asset_root_s3_key="xidA/",
                  aux_preview_prefix="db1/xidA/models/pump.glb/preview")
    kwargs.update(overrides)
    return er.build_manifest_entry(**kwargs)


@pytest.mark.unit
class TestBuildManifestEntry:
    def test_the_entry_carries_the_registration_id(self):
        assert _entry(bucket_id="bkt-01")["bucketId"] == "bkt-01"

    def test_the_id_is_distinct_from_the_bucket_name(self):
        entry = _entry(bucket_id="bkt-01")
        assert entry["bucket"] == "asset-bucket" and entry["bucketId"] == "bkt-01"

    def test_the_default_is_an_empty_string(self):
        assert _entry()["bucketId"] == ""

    def test_none_is_written_as_an_empty_string(self):
        assert _entry(bucket_id=None)["bucketId"] == ""

    def test_the_key_is_always_present(self):
        assert "bucketId" in _entry()
        assert "bucketId" in er.build_manifest_entry("/a.glb", "bkt", "x/a.glb")

    def test_the_entry_key_set(self):
        """A key added to or removed from the entry is a manifest contract change every vendored
        consumer sees, so the whole set is pinned, with and without the optional arguments."""
        assert set(_entry(bucket_id="bkt-01")) == ENTRY_KEYS
        assert set(er.build_manifest_entry("/a.glb", "bkt", "x/a.glb")) == ENTRY_KEYS


@pytest.mark.unit
class TestExecuteWorkflowThreadsTheAssetRowsBucketId:
    def _entries(self, selected, asset_records):
        # The name lookup is keyed on the same id the entry records, so the stubbed name encodes it.
        with patch(f"{MOD}._asset_bucket_details",
                   side_effect=lambda bucket_id: {"bucketName": f"name-of-{bucket_id}",
                                                  "baseAssetsPrefix": ""}):
            return ewv2._build_input_manifest_entries(selected, asset_records)

    def test_the_asset_rows_bucket_id_is_written_to_the_entry(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]
        asset_records = {("db1", "a1"): {"bucketId": "bkt-1", "assetLocation": {"Key": "a1/"}}}
        entries = self._entries(selected, asset_records)
        assert entries[0]["bucketId"] == "bkt-1"
        # Positive control: the id written is the id the bucket-name lookup used.
        assert entries[0]["bucket"] == "name-of-bkt-1"

    def test_each_file_carries_its_own_assets_id(self):
        selected = [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
                    {"databaseId": "db2", "assetId": "a2", "relativeFileKey": "/g.glb"}]
        asset_records = {("db1", "a1"): {"bucketId": "bkt-1", "assetLocation": {"Key": "a1/"}},
                         ("db2", "a2"): {"bucketId": "bkt-2", "assetLocation": {"Key": "a2/"}}}
        entries = self._entries(selected, asset_records)
        assert [e["bucketId"] for e in entries] == ["bkt-1", "bkt-2"]
        assert [e["bucket"] for e in entries] == ["name-of-bkt-1", "name-of-bkt-2"]


def _reading_the_manifest_section():
    text = GUIDE.read_text(encoding="utf-8")
    start = text.index("### Reading the manifest")
    end = text.index("### The metadata envelope", start)
    return text[start:end]


@pytest.mark.unit
class TestTheGuideDocumentsTheEntry:
    def test_reading_the_manifest_names_every_entry_key(self):
        """The one user-facing description of the manifest names every key an entry carries, as a code
        span, so a pipeline author building a manifest for a local test writes the shape the handler
        writes."""
        section = _reading_the_manifest_section()
        for key in sorted(ENTRY_KEYS):
            assert f"`{key}`" in section, key
