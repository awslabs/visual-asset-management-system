# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A workflow output that is a Word document (.docx) or a JavaScript file (.js), written with its real
content type, is ingested; a macro-enabled Word document still stops the output step.

``verify_get_path_objects`` heads every listed output object and runs the shared blocklist helper on
its key and the content type the pipeline wrote it with. The helper here is the real one from
``common/s3.py`` with the real merged lists (the conftest stand-in always allows).
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

for _k, _v in {
    "S3_ASSET_BUCKETS_STORAGE_TABLE_NAME": "t-buckets",
    "METADATA_SERVICE_LAMBDA_FUNCTION_NAME": "t-md-svc",
    "FILE_UPLOAD_LAMBDA_FUNCTION_NAME": "t-upload",
    "ASSET_STORAGE_TABLE_NAME": "t-assets",
    "ASSET_UPLOAD_TABLE_NAME": "t-asset-upload",
    "DATABASE_STORAGE_TABLE_NAME": "t-db",
    "WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME": "t-exec-v2",
    "PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME": "t-pexec",
    "PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME": "t-of",
    "PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME": "t-om",
    "PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME": "t-or",
    "PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME": "t-logs",
    # Read at import too; seeded here so this file collects on its own.
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-wf-inputs",
    "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME": "t-wf-locks",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-wf-config",
}.items():
    os.environ.setdefault(_k, _v)

# The handlers.workflows package __init__ imports get_task_builder from
# common.workflows.stepfunctions_builder at import time; the shared test mock package does not
# provide that submodule. These tests do not exercise ASL generation.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _sf_builder_stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _sf_builder_stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _sf_builder_stub

from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po  # noqa: E402

RUN_BUCKET = "run-io-bucket"
OUTPUT_PREFIX = "pipelines/p1/JOB/output/E1/files/"
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
JS_TYPE = "application/javascript"
DOCM_TYPE = "application/vnd.ms-word.document.macroEnabled.12"
REFUSAL = "potentially malicious executable type object"

_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")


def _real_blocklist_helper():
    spec = importlib.util.spec_from_file_location(
        "constants_for_output_docx_js", os.path.join(_BACKEND, "common", "constants.py"))
    constants = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(constants)
    s3_path = os.path.join(_BACKEND, "common", "s3.py")
    with open(s3_path, encoding="utf-8") as f:
        source = f.read()
    start = source.index("def validateUnallowedFileExtensionAndContentType(")
    end = source.index("\ndef validateS3AssetExtensionsAndContentType(", start)
    namespace = {"os": os, "logger": MagicMock(),
                 "UNALLOWED_MIME_LIST": constants.UNALLOWED_MIME_LIST,
                 "UNALLOWED_FILE_EXTENSION_LIST": constants.UNALLOWED_FILE_EXTENSION_LIST}
    exec(compile(source[start:end], s3_path, "exec"), namespace)
    return namespace["validateUnallowedFileExtensionAndContentType"]


def _verify(outputs):
    """Run verify_get_path_objects over one listing page of ``outputs`` ({name: content type})."""
    keys = {f"{OUTPUT_PREFIX}{name}": content_type for name, content_type in outputs.items()}
    paginator = MagicMock()
    paginator.paginate.return_value = iter([{"Contents": [{"Key": key, "Size": 1} for key in keys]}])
    with patch.object(po, "s3c") as m_s3, \
            patch.object(po, "validateUnallowedFileExtensionAndContentType", _real_blocklist_helper()):
        m_s3.get_paginator.return_value = paginator
        m_s3.head_object.side_effect = lambda Bucket, Key: {"ContentType": keys[Key], "VersionId": "v1"}
        return po.verify_get_path_objects(RUN_BUCKET, OUTPUT_PREFIX)


@pytest.mark.unit
class TestOutputIngestContentTypes:
    def test_docx_and_js_outputs_are_ingested_with_their_types(self):
        result = _verify({"report.docx": DOCX_TYPE, "viewer.js": JS_TYPE, "model.glb": "model/gltf-binary"})
        assert {obj["Key"][len(OUTPUT_PREFIX):]: obj["ContentType"] for obj in result["Contents"]} == {
            "report.docx": DOCX_TYPE, "viewer.js": JS_TYPE, "model.glb": "model/gltf-binary"}

    @pytest.mark.parametrize("name, content_type", [
        pytest.param("macro.docm", "application/octet-stream", id="docm-extension"),
        pytest.param("renamed.docx", DOCM_TYPE, id="macro-enabled-type"),
    ])
    def test_a_macro_enabled_word_document_stops_the_step(self, name, content_type):
        with pytest.raises(Exception, match=REFUSAL):
            _verify({"report.docx": DOCX_TYPE, name: content_type})
