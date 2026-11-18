# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A Zip Archive run's archive fires the SYSTEM GenAI metadata trigger once, and nothing loops.

An ordinary Zip Archive workflow run (GLOBAL/utility-zip-archive, not a Download as Zip job, which writes
to the auxiliary bucket) ingests its archive into the input asset as a new file version, stamped with the
run's provenance (`vams-changesource: workflowExecution`, `vams-changeworkflowid: utility-zip-archive`); an
archive over 1 GiB lands through the large object copy engine with the same provenance plus the engine's
upload token, in one object version and so one upload event (#1141,
`tests/handlers/assets/test_large_file_workflow_provenance.py`). The upload dispatcher then matches the
trigger rows of the shipped system bundles, read here from their `vamsSchema` files with each trigger armed
as a deployment arms it:

- the SYSTEM GenAI metadata workflow allows trigger chaining and admits every file but previews, so the
  archive starts exactly one run of it, on the archive itself;
- the Zip Archive bundle ships no trigger (and trigger add is refused on a system workflow), so no file,
  the SYSTEM GenAI outputs included, can start a Zip Archive run;
- the SYSTEM GenAI run writes metadata and attributes, not files, and a file it did write would not fire
  its own trigger (a workflow never re-fires on its own output).
"""

import json
from pathlib import Path

import pytest

from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_FILE_METADATA_UPDATE,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)
from common.workflows.triggerMatching import match_fileupload_triggers

REPO_ROOT = Path(__file__).resolve().parents[4]
BUNDLES = {
    "system-genai-metadata": REPO_ROOT / "backendPipelines/system/genAiMetadata/vamsSchema",
    "system-genai-asset-summary": REPO_ROOT / "backendPipelines/system/genAiMetadata/vamsSchema/assetSummary",
    "preview-3d-thumbnail": REPO_ROOT / "backendPipelines/preview/3dThumbnail/vamsSchema",
    "utility-zip-archive": REPO_ROOT / "backendPipelines/utility/zipArchive/vamsSchema",
}
ZIP_ARCHIVE = "utility-zip-archive"
SYSTEM_GENAI = "system-genai-metadata"


def _workflow(workflow_id):
    return json.loads((BUNDLES[workflow_id] / "workflow.json").read_text())


def _trigger_rows():
    """The fileUpload trigger rows the shipped bundles register, armed as with
    autoRegisterAutoTriggerOnFileUpload: true."""
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


def _chaining(workflow_database_id, workflow_id):
    return bool((_workflow(workflow_id).get("systemConfig") or {}).get("allowWorkflowTriggerChaining"))


def _match(relative_key, change_workflow_id=ZIP_ARCHIVE, change_source=VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION):
    return match_fileupload_triggers(
        _trigger_rows(), "db1", "a1", relative_key, version_id="v7",
        change_source=change_source, change_workflow_id=change_workflow_id, chaining_allowed_for=_chaining)


@pytest.mark.unit
class TestZipArchiveOutputFiresTheSystemPipelineOnce:
    def test_the_bundles_declare_what_the_rule_relies_on(self):
        assert _chaining("GLOBAL", SYSTEM_GENAI) is True
        assert _chaining("GLOBAL", ZIP_ARCHIVE) is False
        assert not _workflow(ZIP_ARCHIVE).get("triggers")
        assert [r["workflowId"] for r in _trigger_rows()].count(SYSTEM_GENAI) == 1

    @pytest.mark.parametrize("name", ["/a1-archive.zip", "/exports/scans.tar", "/bundle.tar.gz"])
    def test_one_run_of_the_system_genai_workflow_on_the_archive(self, name):
        matches = _match(name)
        assert [(db, wid) for db, wid, _body in matches] == [("GLOBAL", SYSTEM_GENAI)]
        body = matches[0][2]
        assert body["inputFiles"] == [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": name,
                                       "versionId": "v7"}]
        target = (body["outputDatabaseId"], body["outputAssetId"], body["triggerType"])
        assert target == ("db1", "a1", "fileUpload")

    def test_no_file_can_start_a_zip_archive_run(self):
        for writer in ("", SYSTEM_GENAI, ZIP_ARCHIVE, "some-user-workflow"):
            source = VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION if writer else "upload"
            for name in ("/a1-archive.zip", "/model.glb", "/notes.txt"):
                assert ZIP_ARCHIVE not in [wid for _db, wid, _b in _match(name, writer, source)]

    def test_the_system_genai_workflow_never_refires_on_its_own_output(self):
        assert [wid for _db, wid, _b in _match("/a1-archive.zip", SYSTEM_GENAI)] == []

    def test_its_metadata_write_back_fires_nothing(self):
        assert _match("/a1-archive.zip", "", VAMS_CHANGE_SOURCE_FILE_METADATA_UPDATE) == []
