# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A relocated S3 version never fires a fileUpload trigger.

The v2.6 to v2.7 upgrade step `assetLocationFileToFolder` moves a single-file asset's file into its own
folder by copying every version, oldest first, to a new key at the same asset path, stamped
`fileRelocate`. Each copy carries content the file already held and on which the triggers already fired.
Stamped `fileMove`, every copy ran the file's fileUpload workflows (the default GenAI metadata and 3D
thumbnail workflows: Amazon Bedrock analysis and an embedding per copied version, and a thumbnail run that
replaced the moved preview), so a relocation is settled by its source alone, like a restore.
"""

import sys

import pytest

from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_FILE_MOVE, VAMS_CHANGE_SOURCE_RESTORE_VALUES
from common.workflows.triggerMatching import chaining_allows_trigger, match_fileupload_triggers

# The real modules (the root conftest can register a mock `common` package; a submodule import is real).
s3MetadataKeys = sys.modules["common.s3MetadataKeys"]
triggerMatching = sys.modules["common.workflows.triggerMatching"]

# The value the upgrade step stamps (pinned to the step by infra/test/platform/migrationTooling.test.ts).
RELOCATE = "fileRelocate"


def _trigger(workflow_id):
    return {
        "triggerType": "fileUpload",
        "workflowDatabaseId": "GLOBAL",
        "workflowId": workflow_id,
        "enabled": True,
        "triggerConfig": {"inputFileFilters": {"allow": [], "exclude": []}, "defaultTemplateIds": {}},
    }


DEFAULT_TRIGGERS = [_trigger("system-genai-metadata"), _trigger("preview-3d-thumbnail")]


@pytest.mark.unit
class TestRelocationSourceSet:
    def test_the_rule_uses_the_canonical_set(self):
        relocation = getattr(s3MetadataKeys, "VAMS_CHANGE_SOURCE_RELOCATION_VALUES", None)
        assert getattr(triggerMatching, "RELOCATION_CHANGE_SOURCES", None) is relocation
        assert relocation == frozenset({RELOCATE})

    def test_the_source_is_a_recognized_value_and_not_a_restore(self):
        assert getattr(s3MetadataKeys, "VAMS_CHANGE_SOURCE_FILE_RELOCATE", None) == RELOCATE
        assert RELOCATE in s3MetadataKeys.VAMS_CHANGE_SOURCE_VALUES
        assert RELOCATE not in VAMS_CHANGE_SOURCE_RESTORE_VALUES

    def test_a_user_move_is_not_a_relocation(self):
        assert VAMS_CHANGE_SOURCE_FILE_MOVE not in getattr(s3MetadataKeys, "VAMS_CHANGE_SOURCE_RELOCATION_VALUES", ())


@pytest.mark.unit
class TestARelocatedVersionLaunchesNothing:
    @pytest.mark.parametrize("flag", [False, True])
    @pytest.mark.parametrize("origin", ["", "system-genai-metadata"])
    def test_it_is_never_eligible(self, flag, origin):
        assert chaining_allows_trigger("system-genai-metadata", RELOCATE, origin,
                                       flag) is False

    def test_no_trigger_matches_and_no_workflow_row_is_read(self):
        lookups = []

        def lookup(db, wf):
            lookups.append((db, wf))
            return True

        matches = match_fileupload_triggers(
            DEFAULT_TRIGGERS, "db1", "a1", "/five.glb", "vCopy",
            change_source=RELOCATE, change_workflow_id="",
            chaining_allowed_for=lookup, input_file_arity_for=lookup)
        assert matches == []
        assert lookups == []

    def test_control_a_user_move_still_launches_both(self):
        matches = match_fileupload_triggers(
            DEFAULT_TRIGGERS, "db1", "a1", "/five.glb", "vMoved",
            change_source=VAMS_CHANGE_SOURCE_FILE_MOVE, change_workflow_id="")
        assert [m[1] for m in matches] == ["system-genai-metadata", "preview-3d-thumbnail"]
