# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The two trigger-type vocabularies of a workflow execution, and the set they are not.

An execute request names its trigger in the lowercase request form (`EXECUTE_TRIGGER_TYPES`); the
execution row stores the canonical form (`TRIGGER_TYPES`); `TRIGGER_TYPE_TO_STORED` is the only bridge,
and the execute handler reads it as ``TRIGGER_TYPE_TO_STORED.get(request_model.triggerType, "Manual")``.
The list endpoints filter on the stored form, so every surface that enumerates values must say which
vocabulary it means -- the two are never copied onto each other, which the rejection tests pin.

A third set is a different thing: the workflow trigger KINDS a workflow can carry
(`models.workflows.TRIGGER_TYPES`, `workflowRecords.TRIGGER_TYPES`). A reindex execution is launched by
invoking the execute handler directly, not through a workflow trigger, so that set stays ``("fileUpload",)``.
"""

import pytest

from backend.backend.common.workflows import workflowRecords as wr
from backend.backend.models import executions as ex
from backend.backend.models import workflows as wf_models


def _record(trigger_type):
    return ex.WorkflowExecutionRecord(
        workflowExecutionId="e1", workflowId="wf", workflowDatabaseId="GLOBAL", triggerType=trigger_type)


@pytest.mark.unit
class TestExecutionTriggerVocabularies:
    def test_the_stored_vocabulary(self):
        assert ex.TRIGGER_TYPES == ("Manual", "File-Upload", "System-Reindex")

    def test_the_execute_request_vocabulary(self):
        assert ex.EXECUTE_TRIGGER_TYPES == ("manual", "fileUpload", "systemReindex")

    def test_the_mapping_is_a_bijection_between_the_two(self):
        assert tuple(ex.TRIGGER_TYPE_TO_STORED) == ex.EXECUTE_TRIGGER_TYPES
        assert tuple(ex.TRIGGER_TYPE_TO_STORED.values()) == ex.TRIGGER_TYPES
        assert len(set(ex.TRIGGER_TYPE_TO_STORED.values())) == len(ex.TRIGGER_TYPES)

    def test_system_reindex_maps_to_its_stored_form(self):
        assert ex.TRIGGER_TYPE_TO_STORED["systemReindex"] == "System-Reindex"

    def test_the_execute_handler_lookup_resolves_the_value(self):
        # The exact expression executeWorkflow evaluates, including its fallback.
        assert ex.TRIGGER_TYPE_TO_STORED.get("systemReindex", "Manual") == "System-Reindex"

    def test_the_execute_request_accepts_the_request_form(self):
        assert ex.ExecuteWorkflowRequestV2Model(triggerType="systemReindex").triggerType == "systemReindex"

    def test_the_execute_request_rejects_the_stored_form(self):
        with pytest.raises(ValueError):
            ex.ExecuteWorkflowRequestV2Model(triggerType="System-Reindex")

    def test_the_execution_record_accepts_the_stored_form(self):
        assert _record("System-Reindex").triggerType == "System-Reindex"

    def test_the_execution_record_rejects_the_request_form(self):
        with pytest.raises(ValueError):
            _record("systemReindex")


@pytest.mark.unit
class TestWorkflowTriggerKindsAreUnchanged:
    def test_the_model_kind_set(self):
        assert wf_models.TRIGGER_TYPES == ("fileUpload",)

    def test_the_record_builder_kind_set(self):
        assert wr.TRIGGER_TYPES == ("fileUpload",)
