# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The trigger-type vocabularies of a workflow execution, and the set they are not.

An execute request names its trigger in the lowercase request form (`EXECUTE_TRIGGER_TYPES`); the
execution row stores the canonical form (`TRIGGER_TYPES`); `TRIGGER_TYPE_TO_STORED` is the only bridge,
and the execute handler reads it as ``TRIGGER_TYPE_TO_STORED.get(request_model.triggerType, "Manual")``.
The list endpoints filter on the stored form, so every surface that enumerates values must say which
vocabulary it means -- the two are never copied onto each other, which the rejection tests pin.

A stored row may also hold a system trigger value (``System-<Identifier>``, common.workflows.systemPipelines,
#1135), set only by a marked system-pipeline call and never a request value. The vector-search reindex
runs are recorded under one of them, ``System-Reindex``; rows earlier releases wrote with that value stay
readable, and the earlier request value ``systemReindex`` is refused.

A further set is a different thing: the workflow trigger KINDS a workflow can carry
(`models.workflows.TRIGGER_TYPES`, `workflowRecords.TRIGGER_TYPES`). A system run is launched by invoking the
execute handler directly, not through a workflow trigger, so that set stays ``("fileUpload",)``.
"""

import pytest

from backend.backend.common.workflows import systemPipelines as sp
from backend.backend.common.workflows import workflowRecords as wr
from backend.backend.models import executions as ex
from backend.backend.models import workflows as wf_models


def _record(trigger_type):
    return ex.WorkflowExecutionRecord(
        workflowExecutionId="e1", workflowId="wf", workflowDatabaseId="GLOBAL", triggerType=trigger_type)


@pytest.mark.unit
class TestExecutionTriggerVocabularies:
    def test_the_stored_vocabulary(self):
        assert ex.TRIGGER_TYPES == ("Manual", "File-Upload")

    def test_the_execute_request_vocabulary(self):
        assert ex.EXECUTE_TRIGGER_TYPES == ("manual", "fileUpload")

    def test_the_mapping_is_a_bijection_between_the_two(self):
        assert tuple(ex.TRIGGER_TYPE_TO_STORED) == ex.EXECUTE_TRIGGER_TYPES
        assert tuple(ex.TRIGGER_TYPE_TO_STORED.values()) == ex.TRIGGER_TYPES
        assert len(set(ex.TRIGGER_TYPE_TO_STORED.values())) == len(ex.TRIGGER_TYPES)

    def test_no_feature_value_is_in_either_vocabulary(self):
        assert "systemReindex" not in ex.EXECUTE_TRIGGER_TYPES
        assert "System-Reindex" not in ex.TRIGGER_TYPES
        assert ex.TRIGGER_TYPE_TO_STORED.get("systemReindex", "Manual") == "Manual"

    @pytest.mark.parametrize("value", ["systemReindex", "System-Reindex"])
    def test_the_execute_request_refuses_the_reindex_values(self, value):
        with pytest.raises(ValueError):
            ex.ExecuteWorkflowRequestV2Model(triggerType=value)

    def test_a_stored_reindex_row_stays_readable_as_a_system_trigger_value(self):
        assert sp.is_system_trigger("System-Reindex")
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
