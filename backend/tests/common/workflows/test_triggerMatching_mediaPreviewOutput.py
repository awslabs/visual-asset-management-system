# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Two system workflows start on one image or video upload, and their preview outputs start nothing.

With media previews enabled, an image or video upload matches the Media Preview Thumbnail trigger
(GLOBAL/preview-media-thumbnail, `*.png` ... `*.m2ts`) and the SYSTEM GenAI metadata trigger (every file
but `*.previewFile.*`); a 3D file matches the 3D Preview Thumbnail and the SYSTEM GenAI triggers. The rows
are read here from the shipped `vamsSchema` / `vamsSchemaMedia` files with each trigger armed as a
deployment arms it.

A preview run writes `<file>.previewFile.<ext>` into the asset as a workflow execution. Such an object
starts no run of any workflow, and the guard is the dispatcher's key filter
(`workflowTriggerDispatch._should_skip_key`, `s3PathPatterns.EXCLUDED_FILE_PATH_PATTERNS`), applied before
any trigger is matched: the matcher alone would not stop it, because the media workflow allows trigger
chaining and its `*.gif` filter admits a 3D preview (`model.glb.previewFile.gif`) another workflow wrote.
The SYSTEM GenAI workflow's own filter excludes `*.previewFile.*`, and a workflow never re-fires on its own
output, so the matcher refuses those cases too. The vector indexer ignores a preview object before any
lookup. A compliance evaluation is not started for an object a workflow execution wrote
(`complianceTrigger`, covered in `tests/handlers/compliance/test_complianceTrigger.py`).
"""

import json
import os
import sys
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION
from common.workflows.triggerMatching import match_fileupload_triggers

os.environ.setdefault("WORKFLOW_TRIGGERS_STORAGE_TABLE_NAME", "t-triggers")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows.sfn import workflowTriggerDispatch as wd  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[4]
MEDIA = "preview-media-thumbnail"
THREE_D = "preview-3d-thumbnail"
SYSTEM_GENAI = "system-genai-metadata"
BUNDLES = {
    SYSTEM_GENAI: REPO_ROOT / "backendPipelines/system/genAiMetadata/vamsSchema",
    "system-genai-asset-summary": REPO_ROOT / "backendPipelines/system/genAiMetadata/vamsSchema/assetSummary",
    THREE_D: REPO_ROOT / "backendPipelines/preview/3dThumbnail/vamsSchema",
    MEDIA: REPO_ROOT / "backendPipelines/preview/3dThumbnail/vamsSchemaMedia",
    "utility-zip-archive": REPO_ROOT / "backendPipelines/utility/zipArchive/vamsSchema",
}
# Preview objects the two preview workflows write beside their inputs.
PREVIEWS = [
    ("/photos/site.png.previewFile.jpg", MEDIA),
    ("/scans/texture.tif.previewFile.jpg", MEDIA),
    ("/clips/walkthrough.mp4.previewFile.gif", MEDIA),
    ("/models/pump.glb.previewFile.gif", THREE_D),
]


def _workflow(workflow_id):
    return json.loads((BUNDLES[workflow_id] / "workflow.json").read_text())


def _trigger_rows():
    """The fileUpload trigger rows the shipped bundles register, armed (autoRegisterAutoTriggerOnFileUpload
    and useMediaPreviews.autoRegisterAutoTriggerOnFileUpload true)."""
    rows = []
    for workflow_id in BUNDLES:
        for index, trigger in enumerate(_workflow(workflow_id).get("triggers") or []):
            rows.append({
                "workflowDatabaseId": "GLOBAL", "workflowId": workflow_id,
                "triggerType": trigger["triggerType"] if index == 0 else f"{trigger['triggerType']}#t{index}",
                "enabled": True,
                "triggerConfig": {"inputFileFilters": trigger.get("inputFileFilters") or {},
                                  "defaultTemplateIds": trigger.get("defaultTemplateIds") or {}},
            })
    return rows


def _chaining(_workflow_database_id, workflow_id):
    return bool((_workflow(workflow_id).get("systemConfig") or {}).get("allowWorkflowTriggerChaining"))


def _fired(relative_key, writer="", source="upload"):
    matches = match_fileupload_triggers(
        _trigger_rows(), "db1", "a1", relative_key, version_id="v1",
        change_source=source, change_workflow_id=writer, chaining_allowed_for=_chaining)
    return sorted(workflow_id for _db, workflow_id, _body in matches)


@pytest.mark.unit
class TestAnUploadStartsEachMatchingSystemWorkflowOnce:
    def test_the_media_bundle_declares_what_the_rule_relies_on(self):
        assert _chaining("GLOBAL", MEDIA) is True
        assert [row["workflowId"] for row in _trigger_rows()].count(MEDIA) == 1
        assert "*.previewFile.*" in _workflow(SYSTEM_GENAI)["triggers"][0]["inputFileFilters"]["exclude"]

    @pytest.mark.parametrize("name", ["/photos/site.png", "/scans/texture.tif", "/clips/walkthrough.mp4",
                                      "/photos/large.webp"])
    def test_an_image_or_video_starts_the_media_and_the_system_genai_workflows(self, name):
        assert _fired(name) == [MEDIA, SYSTEM_GENAI]

    def test_a_3d_file_starts_the_3d_preview_and_the_system_genai_workflows(self):
        assert _fired("/models/pump.glb") == [THREE_D, SYSTEM_GENAI]

    def test_a_file_neither_preview_reads_starts_only_the_system_genai_workflow(self):
        assert _fired("/docs/report.pdf") == [SYSTEM_GENAI]


@pytest.mark.unit
class TestAPreviewObjectStartsNothing:
    @pytest.mark.parametrize("key, writer", PREVIEWS)
    def test_the_dispatcher_drops_it_before_matching(self, key, writer):
        s3_key = "xa1" + key
        assert wd._should_skip_key(s3_key) is True
        with patch.object(wd, "_resolve_asset_relative_key") as resolve, \
                patch.object(wd, "_invoke_execute") as invoke:
            assert wd._dispatch_uploaded_file("assets", s3_key, _trigger_rows(), "v1") == 0
        resolve.assert_not_called()
        invoke.assert_not_called()

    @pytest.mark.parametrize("key, writer", PREVIEWS)
    def test_the_system_genai_workflow_and_the_writer_refuse_it_in_the_matcher_too(self, key, writer):
        fired = _fired(key, writer, VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION)
        assert SYSTEM_GENAI not in fired and writer not in fired

    def test_without_the_dispatcher_filter_the_media_trigger_would_take_a_3d_preview(self):
        # Why the key filter, not the matcher, is the guard: the media workflow chains and its *.gif
        # filter admits the 3D workflow's GIF preview.
        assert _fired("/models/pump.glb.previewFile.gif", THREE_D, VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION) == [MEDIA]
        assert wd._should_skip_key("xa1/models/pump.glb.previewFile.gif") is True

    def test_an_ordinary_image_is_not_dropped(self):
        assert wd._should_skip_key("xa1/photos/site.png") is False


@pytest.mark.unit
class TestACogConversionOutputGetsNoMediaPreview:
    """The COG Conversion pipeline (#1601) writes `<file>.cog.tif` beside its input. The media preview schema
    excludes `*.cog.tif` (pipeline, workflow and trigger), so the output is not previewed again: the source
    `.tif` already has a preview and a COG can exceed the preview pixel budget."""

    def test_the_media_schema_excludes_the_cog_output_everywhere(self):
        bundle = BUNDLES[MEDIA]
        pipeline = json.loads((bundle / "pipeline.json").read_text())
        workflow = _workflow(MEDIA)
        assert pipeline["systemConfig"]["inputFileFilters"]["exclude"] == ["*.cog.tif"]
        assert workflow["systemConfig"]["inputFileFilters"]["exclude"] == ["*.cog.tif"]
        assert workflow["triggers"][0]["inputFileFilters"]["exclude"] == ["*.cog.tif"]

    @pytest.mark.parametrize("name", ["/maps/dem.tif.cog.tif", "/maps/dem.tiff.cog.tif"])
    def test_a_cog_output_starts_only_the_system_genai_workflow(self, name):
        assert _fired(name) == [SYSTEM_GENAI]

    @pytest.mark.parametrize("name", ["/maps/dem.tif", "/maps/dem.tiff", "/maps/dem.cog.tiff"])
    def test_the_source_tiff_is_still_previewed(self, name):
        assert MEDIA in _fired(name)
