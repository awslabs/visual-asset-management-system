# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Action-audit entries for the orchestration write paths.

Every mutating orchestration operation records an ACTIONS audit entry, the same way the asset handlers
record file uploads/downloads and the auth handlers record permission changes. Before this, none of
these paths audited at all: an execution launch, a pipeline edit, or an admin-only permanent delete left
no audit trail, while `auditLogging.log_actions` sat in the tree with zero callers.

Two properties matter as much as the entries themselves:

  - **After the write.** An entry is emitted only once the write succeeded, so a failed write is never
    audited as a success. A test that only checks "was it called" would pass with the call placed
    first, so the ordering is asserted where it is observable.
  - **Silent-fail.** An audit failure must never break the operation it describes. The writers already
    swallow their own exceptions; these tests pin that the CALLERS do not reintroduce a failure path.

Payloads deliberately carry ids, counts and flags — never rendered configuration bodies or tag values,
which can hold prompts and credential-shaped strings.
"""

import os

import pytest
from unittest.mock import MagicMock, patch

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_FILES_STORAGE_TABLE_NAME", "t-pin-files")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_FILES_STORAGE_TABLE_NAME", "t-of")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_METADATA_STORAGE_TABLE_NAME", "t-om")
os.environ.setdefault("PIPELINE_EXECUTION_OUTPUT_RESULTS_STORAGE_TABLE_NAME", "t-or")
os.environ.setdefault("PIPELINE_EXECUTION_LOGS_STORAGE_TABLE_NAME", "t-logs")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_NAME", "t-workflows")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_NAME", "t-pipelines")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

from backend.backend.handlers.workflows import executionService as es
from backend.backend.handlers.workflows import workflowTriggerService as ts

ES = "backend.backend.handlers.workflows.executionService"
TS = "backend.backend.handlers.workflows.workflowTriggerService"


def _event():
    return {
        "requestContext": {"http": {"method": "DELETE", "path": "/x"}, "authorizer": {}},
        "pathParameters": {},
        "queryStringParameters": {},
    }


@pytest.mark.unit
class TestExecutionAbortAudit:
    """Aborting stops a run mid-flight, so who stopped it belongs in the audit trail."""

    MAIN = {"workflowExecutionId": "E1", "workflowId": "wf1",
            "workflowDatabaseId": "wf-db", "executionStatus": "RUNNING"}

    def _run(self, audit_mock, persist_mock=None):
        es.claims_and_roles = {"tokens": ["u1"]}
        with patch(f"{ES}.log_actions", audit_mock), \
             patch(f"{ES}.get_execution_main_row", return_value=dict(self.MAIN)), \
             patch(f"{ES}.authorize_abort", return_value=(True, "")), \
             patch(f"{ES}.get_workflow_execution_configuration_row", return_value={}), \
             patch(f"{ES}.get_pipeline_execution_rows", return_value=[]), \
             patch(f"{ES}._persist_reconciled_main_row", persist_mock or MagicMock()), \
             patch(f"{ES}.dynamodb") as ddb, \
             patch(f"{ES}.sfn"):
            ddb.Table.return_value = MagicMock()
            return es.abort_execution(_event(), "E1")

    def test_an_abort_is_audited_with_the_execution_and_workflow(self):
        audit = MagicMock()
        resp = self._run(audit)
        assert resp["statusCode"] == 200
        audit.assert_called_once()
        _event_arg, secondary, payload = audit.call_args.args
        assert secondary == "workflowExecutionAbort"
        assert payload["executionId"] == "E1"
        assert payload["workflowId"] == "wf1"
        assert payload["operation"] == "abort"

    def test_the_audit_entry_follows_the_write(self):
        # The row is marked ABORTED before the entry is emitted, so a write that failed is never
        # audited as a success. A test that only asserted "was it called" would pass with the call
        # placed first, so the ORDER is what is pinned.
        order = MagicMock()
        self._run(order.audit, persist_mock=order.persist)
        names = [call[0] for call in order.mock_calls if call[0] in ("persist", "audit")]
        assert "persist" in names and "audit" in names, names
        assert names.index("persist") < names.index("audit"), names


@pytest.mark.unit
class TestTriggerAudit:
    """A trigger changes what runs AUTOMATICALLY, with no caller behind it, so both setting and
    removing one are audited."""

    def test_setting_a_trigger_is_audited(self):
        audit = MagicMock()
        request = MagicMock()
        request.inputFileFilters = None
        request.defaultTemplateIds = None
        request.enabled = True
        with patch(f"{TS}.log_actions", audit), \
             patch(f"{TS}._triggers_table") as table, \
             patch(f"{TS}.get_trigger", return_value=None), \
             patch(f"{TS}._row_to_response") as to_resp:
            table.return_value = MagicMock()
            to_resp.return_value = MagicMock(dict=lambda: {})
            ts.set_trigger("db", "wf1", "fileUpload", request, _event())
        audit.assert_called_once()
        _e, secondary, payload = audit.call_args.args
        assert secondary == "workflowTriggerSet"
        assert payload["triggerType"] == "fileUpload"
        assert payload["workflowId"] == "wf1"

    def test_deleting_a_trigger_is_audited(self):
        audit = MagicMock()
        with patch(f"{TS}.log_actions", audit), \
             patch(f"{TS}._triggers_table") as table, \
             patch(f"{TS}.get_trigger", return_value={"triggerType": "fileUpload"}):
            table.return_value = MagicMock()
            ts.delete_trigger("db", "wf1", "fileUpload", _event())
        audit.assert_called_once()
        _e, secondary, payload = audit.call_args.args
        assert secondary == "workflowTriggerDelete"
        assert payload["operation"] == "delete"

    def test_a_trigger_that_does_not_exist_is_not_audited_as_deleted(self):
        # The entry follows the write. A 404 wrote nothing, so it must not appear in the audit trail.
        audit = MagicMock()
        with patch(f"{TS}.log_actions", audit), \
             patch(f"{TS}._triggers_table"), \
             patch(f"{TS}.get_trigger", return_value=None):
            resp = ts.delete_trigger("db", "wf1", "fileUpload", _event())
        assert resp["statusCode"] != 200
        audit.assert_not_called()


@pytest.mark.unit
class TestAuditPayloadsCarryNoSecrets:
    """Rendered configuration bodies and tag values can hold prompts and credential-shaped strings, so
    no audit payload may carry them. The keys are asserted by name rather than by scanning values,
    because an empty fixture would make a value scan pass vacuously."""

    FORBIDDEN = {"configBody", "renderedConfig", "tagValues", "templateValues",
                 "inputConfiguration", "parameters"}

    def test_no_orchestration_audit_payload_names_a_body_or_tag_field(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[3] / "backend" / "handlers"
        sources = [
            root / "workflows" / "executeWorkflow.py",
            root / "workflows" / "executionService.py",
            root / "workflows" / "workflowService.py",
            root / "workflows" / "workflowTriggerService.py",
            root / "pipelines" / "pipelineService.py",
            root / "pipelines" / "pipelineTemplateService.py",
        ]
        seen_calls = 0
        for path in sources:
            text = path.read_text(encoding="utf-8")
            # Each log_actions(...) payload literal, non-greedily to the closing brace.
            for block in re.findall(r"log_actions\((.*?)\n    \}\)", text, re.DOTALL):
                seen_calls += 1
                for key in self.FORBIDDEN:
                    assert f'"{key}"' not in block, f"{path.name} audits {key}"
        # If the scan matched nothing the assertions above are vacuous.
        assert seen_calls >= 6, f"only {seen_calls} log_actions payloads found; check the regex"
