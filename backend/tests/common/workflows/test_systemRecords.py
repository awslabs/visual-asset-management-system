# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The pure rules behind read-only system (deployment-owned) pipelines and workflows.

Three rules and one trust check, each asserted on both arms: the caller that IS exempt and the one
that is not, the body that IS a change and the one that only re-sends what is stored. The literals
are pinned because the CLI, the MCP server and the live smoke suite quote them.
"""

import pytest

from backend.backend.common.workflows import systemRecords as sr

IMPORT_CALL = {"lambdaCrossCall": {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}}

STORED_TEMPLATE = {
    "templateName": "High quality", "description": "d", "configFormat": "json",
    "allowCustomEdit": True, "inputInstructions": "how", "overrides": {"inputFileArity": "one"},
    "isDefault": True, "configBody": '{"k": "{{TAG}}"}', "webFormJson": "",
}

# The stored form build_file_upload_trigger_config writes for a shipped fileUpload trigger.
STORED_TRIGGER_CONFIG = {
    "inputFileFilters": {"allow": ["*.glb", "*.stl", "*.obj"], "exclude": []},
    "defaultTemplateIds": {"GLOBAL:preview-3d-thumbnail": "preview-3d-thumbnail-default"},
}


@pytest.mark.unit
class TestLiterals:
    def test_registry_literals_are_verbatim(self):
        assert sr.IMPORT_SOURCE_MARKER == "vamsSchemaImport"
        assert sr.SYSTEM_PIPELINE_READONLY_MESSAGE == \
            'System pipelines are read-only; only "enabled" may be changed.'
        assert sr.SYSTEM_WORKFLOW_READONLY_MESSAGE == \
            'System workflows are read-only; only "enabled" may be changed.'
        assert sr.SYSTEM_TEMPLATE_LOCKED_MESSAGE == \
            "Templates of system pipelines cannot be added or deleted."
        assert sr.SYSTEM_TRIGGER_LOCKED_MESSAGE == \
            "Triggers of system workflows cannot be added or deleted."

    def test_archive_refusals_name_the_system_rule(self):
        # The live suite matches these on the substring "system" (case-insensitive).
        for message in (sr.SYSTEM_PIPELINE_ARCHIVE_MESSAGE, sr.SYSTEM_WORKFLOW_ARCHIVE_MESSAGE):
            assert "system" in message.lower()
            assert "restored" in message

    def test_locked_field_messages_name_the_field(self):
        assert '"templateName"' in sr.system_template_locked_field_message("templateName")
        assert '"configBody"' in sr.system_template_locked_field_message("templateName")
        assert '"inputFileFilters"' in sr.system_trigger_locked_field_message("inputFileFilters")
        assert '"enabled"' in sr.system_trigger_locked_field_message("defaultTemplateIds")


@pytest.mark.unit
class TestIsSchemaImportCall:
    def test_the_importer_envelope_is_recognised(self):
        assert sr.is_schema_import_call(IMPORT_CALL) is True

    def test_system_user_without_the_marker_is_not_exempt(self):
        assert sr.is_schema_import_call({"lambdaCrossCall": {"userName": "SYSTEM_USER"}}) is False

    def test_the_marker_under_another_identity_is_not_exempt(self):
        assert sr.is_schema_import_call(
            {"lambdaCrossCall": {"userName": "alice", "source": "vamsSchemaImport"}}) is False

    def test_an_api_gateway_event_is_never_exempt(self):
        event = {"requestContext": {"http": {"method": "PUT", "path": "/x"},
                                    "authorizer": {"vams:tokens": '["SYSTEM_USER"]'}},
                 "body": '{"lambdaCrossCall": {"userName": "SYSTEM_USER", "source": "vamsSchemaImport"}}'}
        assert sr.is_schema_import_call(event) is False

    def test_malformed_envelopes_are_not_exempt(self):
        assert sr.is_schema_import_call(None) is False
        assert sr.is_schema_import_call({"lambdaCrossCall": "SYSTEM_USER"}) is False
        assert sr.is_schema_import_call({"lambdaCrossCall": {}}) is False


@pytest.mark.unit
class TestSystemUpdateAllowedFields:
    def test_enabled_alone_is_allowed(self):
        assert sr.system_update_allowed_fields({"enabled": False}, sr.SYSTEM_UPDATE_ALLOWED_FIELDS) is None

    def test_the_first_disallowed_field_is_named_in_sorted_order(self):
        body = {"enabled": True, "pipelineName": "x", "description": "y"}
        assert sr.system_update_allowed_fields(body, sr.SYSTEM_UPDATE_ALLOWED_FIELDS) == "description"

    def test_archived_is_disallowed_so_a_restore_is_refused(self):
        body = {"archived": False, "enabled": True}
        assert sr.system_update_allowed_fields(body, sr.SYSTEM_UPDATE_ALLOWED_FIELDS) == "archived"

    def test_none_valued_keys_are_not_supplied(self):
        assert sr.system_update_allowed_fields(
            {"enabled": True, "description": None}, sr.SYSTEM_UPDATE_ALLOWED_FIELDS) is None
        assert sr.system_update_allowed_fields({}, sr.SYSTEM_UPDATE_ALLOWED_FIELDS) is None


@pytest.mark.unit
class TestTemplateLockedFieldChanged:
    def test_the_full_web_body_with_unchanged_locked_fields_is_not_a_change(self):
        body = {"templateName": "High quality", "description": "d", "configFormat": "json",
                "configBody": '{"k": "{{TAG}}", "new": 1}', "inputInstructions": "how",
                "allowCustomEdit": True, "isDefault": True, "overrides": {"inputFileArity": "one"},
                "tagSchema": [], "webFormJson": "[]"}
        assert sr.template_locked_field_changed(body, STORED_TEMPLATE) is None

    def test_a_changed_locked_field_is_named(self):
        assert sr.template_locked_field_changed(
            {"templateName": "renamed", "configBody": "{}"}, STORED_TEMPLATE) == "templateName"
        assert sr.template_locked_field_changed({"isDefault": False}, STORED_TEMPLATE) == "isDefault"
        assert sr.template_locked_field_changed(
            {"overrides": {"inputFileArity": "multi"}}, STORED_TEMPLATE) == "overrides"

    def test_only_unlocked_fields_supplied_is_not_a_change(self):
        assert sr.template_locked_field_changed({"configBody": "{}"}, STORED_TEMPLATE) is None
        assert sr.template_locked_field_changed({"tagSchema": [{"tagKey": "T"}]}, STORED_TEMPLATE) is None

    def test_a_stored_row_missing_a_field_reads_as_its_default(self):
        stored = {"templateName": "t"}
        assert sr.template_locked_field_changed(
            {"description": "", "configFormat": "json", "allowCustomEdit": False,
             "inputInstructions": "", "overrides": {}, "isDefault": False}, stored) is None
        assert sr.template_locked_field_changed({"isDefault": True}, stored) == "isDefault"

    def test_the_unlocked_set_is_a_parameter(self):
        assert sr.template_locked_field_changed(
            {"description": "changed"}, STORED_TEMPLATE, unlocked={"description"}) is None


@pytest.mark.unit
class TestTriggerLockedFieldChanged:
    def test_the_stored_body_resent_verbatim_is_not_a_change(self):
        body = {"inputFileFilters": STORED_TRIGGER_CONFIG["inputFileFilters"],
                "defaultTemplateIds": STORED_TRIGGER_CONFIG["defaultTemplateIds"], "enabled": False}
        assert sr.trigger_locked_field_changed(body, STORED_TRIGGER_CONFIG) is None

    def test_a_reordered_allow_list_and_a_missing_exclude_key_are_not_changes(self):
        body = {"inputFileFilters": {"allow": ["*.obj", "*.glb", "*.stl"]}}
        assert sr.trigger_locked_field_changed(body, STORED_TRIGGER_CONFIG) is None

    def test_absent_locked_fields_are_taken_from_the_stored_trigger(self):
        assert sr.trigger_locked_field_changed({"enabled": False}, STORED_TRIGGER_CONFIG) is None
        assert sr.trigger_locked_field_changed({}, STORED_TRIGGER_CONFIG) is None

    def test_changed_filters_are_named(self):
        body = {"inputFileFilters": {"allow": ["*.png"], "exclude": []}}
        assert sr.trigger_locked_field_changed(body, STORED_TRIGGER_CONFIG) == "inputFileFilters"

    def test_changed_default_templates_are_named(self):
        body = {"inputFileFilters": STORED_TRIGGER_CONFIG["inputFileFilters"],
                "defaultTemplateIds": {"GLOBAL:preview-3d-thumbnail": "other"}}
        assert sr.trigger_locked_field_changed(body, STORED_TRIGGER_CONFIG) == "defaultTemplateIds"

    def test_a_non_mapping_value_is_a_change(self):
        assert sr.trigger_locked_field_changed(
            {"inputFileFilters": "*.glb"}, STORED_TRIGGER_CONFIG) == "inputFileFilters"

    def test_an_empty_stored_config_compares_as_empty_filters(self):
        assert sr.trigger_locked_field_changed({"inputFileFilters": {"allow": [], "exclude": []}}, {}) is None
        assert sr.trigger_locked_field_changed({"inputFileFilters": {"allow": ["*.png"]}}, {}) == "inputFileFilters"
