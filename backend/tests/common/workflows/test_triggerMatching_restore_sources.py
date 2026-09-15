# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A restored S3 version never fires a fileUpload trigger.

`fileUnarchive` copies the newest content version forward under a new version id; `assetUnarchive` is
the asset-level counterpart. Both carry content the file already had, on which every applicable trigger
has already fired, so neither is trigger-eligible -- whatever wrote the content originally and whatever
`allowWorkflowTriggerChaining` says. Every other change source keeps its existing eligibility: `upload`,
`direct`, `fileCopy`, `fileMove`, `fileRename` and `fileRevert` fire, `workflowExecution` follows the
chaining rule. One case per source value, for both the decision function and the matcher.
"""

import pytest

from common.s3MetadataKeys import (
    VAMS_CHANGE_SOURCE_ASSET_UNARCHIVE,
    VAMS_CHANGE_SOURCE_DIRECT,
    VAMS_CHANGE_SOURCE_FILE_COPY,
    VAMS_CHANGE_SOURCE_FILE_MOVE,
    VAMS_CHANGE_SOURCE_FILE_RENAME,
    VAMS_CHANGE_SOURCE_FILE_REVERT,
    VAMS_CHANGE_SOURCE_FILE_UNARCHIVE,
    VAMS_CHANGE_SOURCE_RESTORE_VALUES,
    VAMS_CHANGE_SOURCE_UPLOAD,
    VAMS_CHANGE_SOURCE_VALUES,
    VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION,
)
from common.workflows.triggerMatching import (
    RESTORE_CHANGE_SOURCES,
    chaining_allows_trigger,
    match_fileupload_triggers,
)

RESTORE_SOURCES = [VAMS_CHANGE_SOURCE_FILE_UNARCHIVE, VAMS_CHANGE_SOURCE_ASSET_UNARCHIVE]
ELIGIBLE_SOURCES = [
    VAMS_CHANGE_SOURCE_UPLOAD,
    VAMS_CHANGE_SOURCE_DIRECT,
    VAMS_CHANGE_SOURCE_FILE_COPY,
    VAMS_CHANGE_SOURCE_FILE_MOVE,
    VAMS_CHANGE_SOURCE_FILE_RENAME,
    VAMS_CHANGE_SOURCE_FILE_REVERT,
    "",  # an object written before provenance was stamped
]


def _trigger(workflow_id="wfA", enabled=True):
    return {
        "triggerType": "fileUpload",
        "workflowDatabaseId": "GLOBAL",
        "workflowId": workflow_id,
        "enabled": enabled,
        "triggerConfig": {"inputFileFilters": {"allow": [], "exclude": []}, "defaultTemplateIds": {}},
    }


@pytest.mark.unit
class TestRestoreSourceSet:
    def test_the_rule_uses_the_canonical_set(self):
        assert RESTORE_CHANGE_SOURCES is VAMS_CHANGE_SOURCE_RESTORE_VALUES
        assert RESTORE_CHANGE_SOURCES == frozenset(RESTORE_SOURCES)

    def test_the_set_names_recognized_change_sources_only(self):
        assert RESTORE_CHANGE_SOURCES <= VAMS_CHANGE_SOURCE_VALUES

    def test_revert_is_not_a_restore(self):
        """A reverted version may predate the triggers or come from a failed run, so it stays eligible."""
        assert VAMS_CHANGE_SOURCE_FILE_REVERT not in RESTORE_CHANGE_SOURCES

    def test_every_change_source_is_classified(self):
        """Each recognized value is either a restore, workflow output, or an eligible ordinary write."""
        classified = set(RESTORE_SOURCES) | {VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION} | set(ELIGIBLE_SOURCES)
        # Archive sources create no S3 object version (a delete marker is not a version), so they never
        # reach the trigger path; they are listed so the classification stays total.
        classified |= {"fileArchive", "assetArchive"}
        assert VAMS_CHANGE_SOURCE_VALUES <= classified


@pytest.mark.unit
class TestChainingAllowsTrigger:
    @pytest.mark.parametrize("source", RESTORE_SOURCES)
    @pytest.mark.parametrize("flag", [False, True])
    @pytest.mark.parametrize("origin", ["", "wfA", "wfB"])
    def test_a_restore_is_never_eligible(self, source, flag, origin):
        assert chaining_allows_trigger("wfA", source, origin, flag) is False

    @pytest.mark.parametrize("source", ELIGIBLE_SOURCES)
    @pytest.mark.parametrize("flag", [False, True])
    def test_an_ordinary_write_stays_eligible(self, source, flag):
        assert chaining_allows_trigger("wfA", source, "", flag) is True

    def test_workflow_output_keeps_the_chaining_rule(self):
        assert chaining_allows_trigger("wfA", VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION, "wfA", True) is False
        assert chaining_allows_trigger("wfA", VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION, "wfB", False) is False
        assert chaining_allows_trigger("wfA", VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION, "wfB", True) is True


@pytest.mark.unit
class TestMatchFileUploadTriggers:
    @pytest.mark.parametrize("source", RESTORE_SOURCES)
    def test_a_restore_matches_no_trigger_and_reads_no_workflow(self, source):
        lookups = []

        def lookup(db, wf):
            lookups.append((db, wf))
            return True

        matches = match_fileupload_triggers(
            [_trigger("wfA"), _trigger("wfB")], "db1", "a1", "/report.pdf", "vNew",
            change_source=source, change_workflow_id="",
            chaining_allowed_for=lookup, input_file_arity_for=lookup)
        assert matches == []
        assert lookups == [], "a restore is settled by its source; no workflow row is read"

    @pytest.mark.parametrize("source", ELIGIBLE_SOURCES)
    def test_an_ordinary_write_matches_as_before(self, source):
        matches = match_fileupload_triggers(
            [_trigger("wfA")], "db1", "a1", "/report.pdf", "v1",
            change_source=source, change_workflow_id="")
        assert [m[1] for m in matches] == ["wfA"]
        assert matches[0][2]["inputFiles"][0]["versionId"] == "v1"

    def test_workflow_output_still_follows_the_chaining_flag(self):
        rows = [_trigger("wfPreview"), _trigger("wfOther")]
        matches = match_fileupload_triggers(
            rows, "db1", "a1", "/out.glb", "v1",
            change_source=VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION, change_workflow_id="wfConvert",
            chaining_allowed_for=lambda db, wf: wf == "wfPreview")
        assert [m[1] for m in matches] == ["wfPreview"]
