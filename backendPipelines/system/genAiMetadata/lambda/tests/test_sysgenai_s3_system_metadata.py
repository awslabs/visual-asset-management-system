#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""sys_file.s3Metadata records the object's own user metadata, never the keys VAMS writes for itself.

Every asset object VAMS writes carries system keys in its user metadata: the asset context keys
(`assetid`, `databaseid`, `uploadid`), the change provenance (`vams-changesource`, `vams-changeuserid`,
the workflow and execution ids, the copy and move sources), the lifecycle status (`vams-status`), and on a
file the large object copy engine wrote (a workflow output over 1 GiB, a Zip Archive run's archive) the
engine's upload token `vams-copyuploadtoken`. The file attributes, the analysis request and the file's search
document are no place for them: the file indexer leaves them out of search (backend
`common.s3MetadataKeys.is_system_metadata_key`), and the user-assigned primary type is the one `vams-` key it
keeps. sys_file follows the same rule, and the bound on recorded keys counts the user's keys only.
"""

import importlib.util
import json
import os
from unittest.mock import MagicMock

import pytest

import sysgenai_harness as h

MANIFEST_KEY = "pipelines/system-genai-metadata/E1/analysis.json"
CONFIG_KEY = "pipelines/workflowExecutionInputs/E1/pipeline1/config.json"
DEFAULT_CONFIG = {"seedWithExistingMetadata": True, "resolveDependencies": True, "renderViews": 8,
                  "maxTextChars": 12000, "writeAssetKeywords": False, "embeddingIncludeTextExcerpt": True,
                  "writeExtractedMetadata": True, "extractGeoLocation": True, "videoSegmentSeconds": 0,
                  "contentChunking": True,
                  "classificationVocabulary": {"categories": {}, "styles": [], "materials": [], "colors": [],
                                               "allowUnlisted": True}}
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), *[os.pardir] * 5))
BACKEND_KEYS_PATH = os.path.join(REPO_ROOT, "backend", "backend", "common", "s3MetadataKeys.py")

# What VAMS stamps on an archive a Zip Archive run wrote into an asset through the copy engine.
VAMS_KEYS = {
    "assetid": "xidM",
    "databaseid": "dbM",
    "uploadid": "u-1",
    "vams-changesource": "workflowExecution",
    "vams-changeuserid": "someone@example.com",
    "vams-changeworkflowid": "utility-zip-archive",
    "vams-changeworkflowexecutionid": "E9",
    "vams-copyuploadtoken": "0b1c2d3e-token",
    "vams-status": "",
}
USER_KEYS = {"recorded-by": "scanner-7", "site": "plant A"}


def _state():
    return {
        "jobName": "PipelineJob_20260908_101010_123_abcdef01",
        "externalSfnTaskToken": h.TASK_TOKEN,
        "inputS3AssetFilePath": "s3://abkt/xidM/archives/survey.zip",
        "outputS3AssetFilesPath": "s3://abkt/pipelines/sgm/sgm/output/E1/files/",
        "outputS3AssetPreviewPath": "s3://abkt/pipelines/sgm/sgm/output/E1/previews/",
        "outputS3AssetMetadataPath": "s3://abkt/pipelines/sgm/sgm/output/E1/metadata/",
        "outputS3AssetResultsPath": "s3://abkt/pipelines/sgm/sgm/output/E1/results/",
        "inputOutputS3AssetAuxiliaryFilesPath": "s3://aux/pipelines/system-genai-metadata/E1/",
        "inputMetadataS3Location": "s3://abkt/pipelines/workflowExecutionInputs/E1/metadata.json",
        "inputConfigurationS3Location": f"s3://abkt/{CONFIG_KEY}",
        "assetId": "xidM",
        "databaseId": "dbM",
        "bucketId": "bkt-01",
        "relativePath": "/archives/survey.zip",
        "versionId": "v1",
        "workflowExecutionId": "E1",
        "orchestrationEventPrefix": "vams.prod.execution.E1.pipeline.P1",
    }


def _run(metadata):
    content = b"PK\x03\x04" + b"\x00" * 60
    s3 = h.FakeS3({("abkt", "xidM/archives/survey.zip"): content})
    s3.heads[("abkt", "xidM/archives/survey.zip")] = {
        "ContentLength": len(content), "ETag": '"abc123"', "ContentType": "application/zip", "VersionId": "v1",
        "Metadata": metadata}
    s3.put_json("abkt", CONFIG_KEY, DEFAULT_CONFIG)
    mod = h.load_handler("constructPipeline")
    mod.s3_client = s3
    mod.lambda_handler(_state(), MagicMock())
    return mod, s3.json_at("aux", MANIFEST_KEY)


def _backend_keys():
    spec = importlib.util.spec_from_file_location("backend_s3MetadataKeys_for_sysgenai", BACKEND_KEYS_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
class TestSystemKeysAreLeftOut:
    def test_a_vams_written_archive_records_only_the_users_keys(self):
        _mod, manifest = _run({**VAMS_KEYS, **USER_KEYS})
        assert manifest["attributes"]["sys_file"]["s3Metadata"] == USER_KEYS

    def test_the_manifest_the_analysis_and_the_attribute_rows_read_carries_no_system_key(self):
        # generateMetadata sends `manifest.attributes` as the request's "File attributes (JSON)" and the end
        # step writes each group as a file attribute row, so the manifest is the one place to keep them out.
        _mod, manifest = _run({**VAMS_KEYS, **USER_KEYS})
        text = json.dumps(manifest["attributes"])
        for key, value in VAMS_KEYS.items():
            assert f'"{key}"' not in text
            if value:
                assert value not in text, key

    def test_only_system_keys_record_no_s3_metadata(self):
        _mod, manifest = _run(dict(VAMS_KEYS))
        assert "s3Metadata" not in manifest["attributes"]["sys_file"]

    def test_the_user_assigned_primary_type_is_kept(self):
        _mod, manifest = _run({**VAMS_KEYS, "vams-primarytype": "pointcloud"})
        assert manifest["attributes"]["sys_file"]["s3Metadata"] == {"vams-primarytype": "pointcloud"}

    def test_system_keys_take_no_place_under_the_key_bound(self):
        mod = h.load_handler("constructPipeline")
        system = {f"vams-change{index:03d}": "x" for index in range(mod.S3_METADATA_MAX_KEYS)}
        user = {f"user-{index}": "v" for index in range(3)}
        assert mod.s3_user_metadata({"Metadata": {**system, **VAMS_KEYS, **user}}) == user

    def test_keys_are_compared_without_case(self):
        mod = h.load_handler("constructPipeline")
        assert mod.s3_user_metadata({"Metadata": {"AssetId": "a", "VAMS-ChangeUserId": "u", "Site": "s"}}) == {
            "Site": "s"}


@pytest.mark.unit
class TestTheRuleMirrorsTheBackend:
    def test_the_constants_match(self):
        keys = _backend_keys()
        mod = h.load_handler("constructPipeline")
        assert mod.S3_SYSTEM_METADATA_KEYS == keys.ASSET_CONTEXT_METADATA_KEYS
        assert mod.S3_SYSTEM_METADATA_PREFIX == keys.VAMS_METADATA_KEY_PREFIX
        assert mod.S3_SEARCHABLE_SYSTEM_METADATA_KEYS == keys.SEARCHABLE_VAMS_METADATA_KEYS

    def test_every_key_the_backend_defines_is_classified_the_same(self):
        keys = _backend_keys()
        mod = h.load_handler("constructPipeline")
        defined = sorted(value for name, value in vars(keys).items()
                         if name.endswith("_METADATA_KEY") and isinstance(value, str))
        assert "vams-copyuploadtoken" in defined and "vams-changeuserid" in defined  # control
        for key in defined:
            searchable = key in keys.SEARCHABLE_VAMS_METADATA_KEYS
            assert mod.is_system_metadata_key(key) == (keys.is_system_metadata_key(key) and not searchable), key
