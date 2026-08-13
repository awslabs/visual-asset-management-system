# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Trigger-chaining rules: which uploaded files may fire a workflow's fileUpload trigger.

The rule is deliberately asymmetric. A workflow NEVER fires on output it wrote itself, whatever
`allowWorkflowTriggerChaining` says, so an A->A loop cannot be enabled. The flag governs
CROSS-workflow chaining only — letting a preview or metadata workflow run on a conversion pipeline's
output while chained triggering stays opt-in.
"""

import pytest

from common.workflows.triggerMatching import (
    chaining_allows_trigger,
    match_fileupload_triggers,
)

WF_EXEC = "workflowExecution"


def _trigger(workflow_id, database_id="GLOBAL", allow=None, enabled=True):
    return {
        "triggerType": "fileUpload",
        "workflowDatabaseId": database_id,
        "workflowId": workflow_id,
        "enabled": enabled,
        "triggerConfig": {
            "inputFileFilters": {"allow": allow if allow is not None else [], "exclude": []},
            "defaultTemplateIds": {},
        },
    }


@pytest.mark.unit
class TestChainingDecision:
    def test_non_workflow_writes_always_fire(self):
        """A user upload, a direct S3 write, a copy/move — the ordinary trigger path is untouched by
        chaining, whatever the flag says."""
        for source in ("upload", "direct", "fileCopy", "fileMove", "fileRename", ""):
            assert chaining_allows_trigger("wfA", source, "", False) is True
            assert chaining_allows_trigger("wfA", source, "", True) is True

    def test_self_output_never_fires_even_with_the_flag_on(self):
        """The A->A loop is not enable-able. This is the whole reason the flag is not a plain
        'allow workflow output' switch."""
        assert chaining_allows_trigger("wfA", WF_EXEC, "wfA", False) is False
        assert chaining_allows_trigger("wfA", WF_EXEC, "wfA", True) is False

    def test_other_workflow_output_requires_the_flag(self):
        assert chaining_allows_trigger("wfA", WF_EXEC, "wfB", False) is False
        assert chaining_allows_trigger("wfA", WF_EXEC, "wfB", True) is True

    def test_unknown_origin_is_treated_as_another_workflow(self):
        """A workflow-sourced file with no recorded originating workflow cannot be proven to be
        self-output, so it still requires the explicit opt-in rather than being allowed outright."""
        assert chaining_allows_trigger("wfA", WF_EXEC, "", False) is False
        assert chaining_allows_trigger("wfA", WF_EXEC, "", True) is True


@pytest.mark.unit
class TestMatchFileUploadTriggersChaining:
    def test_user_upload_matches_without_consulting_the_flag(self):
        """The flag lookup may hit DynamoDB, so it must not run for an ordinary upload."""
        calls = []

        def lookup(db, wf):
            calls.append((db, wf))
            return True

        matches = match_fileupload_triggers(
            [_trigger("wfA")], "db1", "a1", "/model.glb", "v1",
            change_source="upload", change_workflow_id="",
            chaining_allowed_for=lookup)
        assert [m[1] for m in matches] == ["wfA"]
        assert calls == [], "the chaining flag must not be read for a non-workflow write"

    def test_self_output_is_dropped(self):
        matches = match_fileupload_triggers(
            [_trigger("wfA")], "db1", "a1", "/out.glb", "v1",
            change_source=WF_EXEC, change_workflow_id="wfA",
            chaining_allowed_for=lambda db, wf: True)
        assert matches == []

    def test_other_workflow_output_fires_only_for_opted_in_workflows(self):
        """The realistic case: a conversion workflow writes a GLTF; the preview workflow has chaining
        on and fires, a second workflow that has it off does not."""
        rows = [_trigger("wfPreview"), _trigger("wfOther")]
        opted_in = {"wfPreview"}
        matches = match_fileupload_triggers(
            rows, "db1", "a1", "/converted.gltf", "v1",
            change_source=WF_EXEC, change_workflow_id="wfConvert",
            chaining_allowed_for=lambda db, wf: wf in opted_in)
        assert [m[1] for m in matches] == ["wfPreview"]

    def test_omitting_the_lookup_reproduces_the_pre_chaining_behavior(self):
        """With no lookup supplied, no workflow opts in — workflow output never re-triggers, which is
        exactly how the system behaved before chaining existed."""
        matches = match_fileupload_triggers(
            [_trigger("wfA")], "db1", "a1", "/out.glb", "v1",
            change_source=WF_EXEC, change_workflow_id="wfB")
        assert matches == []

    def test_input_file_filters_still_apply_to_a_chained_trigger(self):
        """Chaining is permission to be considered, not a bypass: the file must still match the
        trigger's own filters."""
        rows = [_trigger("wfPreview", allow=["*.gltf"])]
        common = dict(change_source=WF_EXEC, change_workflow_id="wfConvert",
                      chaining_allowed_for=lambda db, wf: True)
        assert [m[1] for m in match_fileupload_triggers(
            rows, "db1", "a1", "/converted.gltf", "v1", **common)] == ["wfPreview"]
        # A non-matching extension is filtered out even though chaining is allowed.
        assert match_fileupload_triggers(
            rows, "db1", "a1", "/converted.txt", "v1", **common) == []

    def test_a_disabled_trigger_does_not_fire_when_chaining_is_allowed(self):
        rows = [_trigger("wfPreview", enabled=False)]
        assert match_fileupload_triggers(
            rows, "db1", "a1", "/converted.gltf", "v1",
            change_source=WF_EXEC, change_workflow_id="wfConvert",
            chaining_allowed_for=lambda db, wf: True) == []

    def test_backwards_compatible_call_without_provenance_args(self):
        """Existing callers that pass no provenance keep working: an unknown change source is treated
        as a non-workflow write, which is the ordinary trigger path."""
        matches = match_fileupload_triggers([_trigger("wfA")], "db1", "a1", "/model.glb", "v1")
        assert [m[1] for m in matches] == ["wfA"]
