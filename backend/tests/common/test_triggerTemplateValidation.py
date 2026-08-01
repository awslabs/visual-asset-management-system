# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for common.workflows.triggerTemplateValidation + required_tags_without_default."""

from unittest.mock import MagicMock

import pytest

from backend.backend.common.workflows.templateTagSchema import required_tags_without_default
from backend.backend.common.workflows.triggerTemplateValidation import (
    validate_trigger_default_templates,
    validate_template_not_breaking_triggers,
    pipeline_trigger_template_warnings,
    triggers_referencing_template,
)


@pytest.mark.unit
class TestRequiredTagsWithoutDefault:
    def test_none_and_empty(self):
        assert required_tags_without_default(None) == []
        assert required_tags_without_default([]) == []

    def test_required_without_default_flagged(self):
        schema = [
            {"tagKey": "a", "required": True},
            {"tagKey": "b", "required": True, "default": "x"},
            {"tagKey": "c", "required": False},
            {"tagKey": "d", "required": True, "default": None},
        ]
        assert required_tags_without_default(schema) == ["a", "d"]

    def test_falsey_default_counts_as_default(self):
        # default of False / 0 / "" is a usable default (only None/absent counts as missing).
        schema = [
            {"tagKey": "flag", "required": True, "default": False},
            {"tagKey": "n", "required": True, "default": 0},
            {"tagKey": "s", "required": True, "default": ""},
        ]
        assert required_tags_without_default(schema) == []


@pytest.mark.unit
class TestValidateTriggerDefaultTemplates:
    def test_no_defaults_no_errors(self):
        assert validate_trigger_default_templates({}, lambda *a: None) == []

    def test_flags_required_without_default(self):
        loader = lambda db, p, t: [{"tagKey": "q", "required": True}]
        errors = validate_trigger_default_templates({"db1:pipe1": "tmpl1"}, loader)
        assert len(errors) == 1
        assert "q" in errors[0]

    def test_ok_when_default_present(self):
        loader = lambda db, p, t: [{"tagKey": "q", "required": True, "default": "hi"}]
        assert validate_trigger_default_templates({"db1:pipe1": "tmpl1"}, loader) == []

    def test_empty_template_id_skipped(self):
        called = []
        loader = lambda db, p, t: called.append((db, p, t)) or None
        assert validate_trigger_default_templates({"db1:pipe1": ""}, loader) == []
        assert called == []  # loader never invoked for an empty templateId


def _triggers_table_with(rows):
    table = MagicMock()
    table.query.return_value = {"Items": rows}
    return table


@pytest.mark.unit
class TestTemplateNotBreakingTriggers:
    def test_no_required_missing_no_error(self):
        # Even if trigger-referenced, a schema with no required-without-default tag is fine.
        table = _triggers_table_with([
            {"workflowDatabaseId": "db1", "workflowId": "wf1", "triggerType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "tmpl1"}}},
        ])
        errors = validate_template_not_breaking_triggers(
            table, MagicMock(), "db1", "pipe1", "tmpl1",
            [{"tagKey": "q", "required": True, "default": "x"}])
        assert errors == []

    def test_referenced_and_breaking_flags(self):
        table = _triggers_table_with([
            {"workflowDatabaseId": "db1", "workflowId": "wf1", "triggerType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "tmpl1"}}},
        ])
        errors = validate_template_not_breaking_triggers(
            table, MagicMock(), "db1", "pipe1", "tmpl1",
            [{"tagKey": "q", "required": True}])
        assert len(errors) == 1
        assert "q" in errors[0]
        # The client-facing message names no workflow/database ids (backend Rule 11).
        assert "db1:wf1" not in errors[0]

    def test_not_referenced_no_error(self):
        table = _triggers_table_with([
            {"workflowDatabaseId": "db1", "workflowId": "wf1", "triggerType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:other": "tmplX"}}},
        ])
        errors = validate_template_not_breaking_triggers(
            table, MagicMock(), "db1", "pipe1", "tmpl1",
            [{"tagKey": "q", "required": True}])
        assert errors == []


@pytest.mark.unit
class TestPipelineTriggerTemplateWarnings:
    def test_no_warning_when_not_require_template(self):
        assert pipeline_trigger_template_warnings(
            MagicMock(), lambda *a: None, "db1", "pipe1", False) == []

    def test_warns_when_triggered_workflow_has_no_default(self):
        wf_table = MagicMock()
        wf_table.scan.return_value = {"Items": [
            {"databaseId": "db1", "workflowId": "wf1",
             "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}]},
        ]}
        # Trigger exists but picked no default template for this pipeline.
        get_trigger = lambda db, wf: {"triggerType": "fileUpload",
                                      "triggerConfig": {"defaultTemplateIds": {}}}
        warnings = pipeline_trigger_template_warnings(wf_table, get_trigger, "db1", "pipe1", True)
        assert len(warnings) == 1
        assert "db1:wf1" in warnings[0]

    def test_no_warning_when_trigger_has_default(self):
        wf_table = MagicMock()
        wf_table.scan.return_value = {"Items": [
            {"databaseId": "db1", "workflowId": "wf1",
             "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}]},
        ]}
        get_trigger = lambda db, wf: {"triggerType": "fileUpload",
                                      "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "t1"}}}
        assert pipeline_trigger_template_warnings(wf_table, get_trigger, "db1", "pipe1", True) == []

    def test_no_warning_when_no_trigger(self):
        wf_table = MagicMock()
        wf_table.scan.return_value = {"Items": [
            {"databaseId": "db1", "workflowId": "wf1",
             "specifiedPipelines": [{"pipelineDatabaseId:pipelineId": "db1:pipe1"}]},
        ]}
        assert pipeline_trigger_template_warnings(
            wf_table, lambda db, wf: None, "db1", "pipe1", True) == []


@pytest.mark.unit
class TestTriggersReferencingTemplate:
    def test_finds_reference(self):
        table = _triggers_table_with([
            {"workflowDatabaseId": "db1", "workflowId": "wf1", "triggerType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "tmpl1"}}},
            {"workflowDatabaseId": "db1", "workflowId": "wf2", "triggerType": "fileUpload",
             "triggerConfig": {"defaultTemplateIds": {"db1:pipe1": "other"}}},
        ])
        hits = triggers_referencing_template(table, MagicMock(), "db1", "pipe1", "tmpl1")
        assert hits == [("db1", "wf1", "fileUpload")]
        assert table.query.call_args.kwargs["IndexName"] == "TriggersByTypeGSI"
        table.scan.assert_not_called()

    def test_read_error_returns_empty(self):
        table = MagicMock()
        table.query.side_effect = RuntimeError("throttled")
        assert triggers_referencing_template(table, MagicMock(), "db1", "pipe1", "tmpl1") == []
