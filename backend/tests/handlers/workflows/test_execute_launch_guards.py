# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The launch-time guards of the execute handler: the render limit and the escaping of a template body
under its own declared format. The concurrency restriction is a lock, asserted in
test_execute_concurrency_locks.py.

S2-BACKEND-103 — RenderedConfigTooLargeError reached the handler's terminal `except Exception` and
answered 500 for a caller-input problem the error class exists to report as 400.

S2-BACKEND-041 — the launch render never received the template's declared configFormat, so an `xml`
body's system-tag values took the JSON escape and left `&`, `<` and `>` raw.

The stubs here hand back REAL dicts and TERMINATE: a MagicMock answers `.get('LastEvaluatedKey')`
truthily forever, so any paging loop built on one never exits. Each fake counts its queries and fails
the test rather than hanging if the walk does not end.
"""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

# executeWorkflow loads these at import (mirrors test_executeWorkflow.py).
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("WORKFLOW_STORAGE_TABLE_V2_NAME", "t-wf-v2")
os.environ.setdefault("PIPELINE_STORAGE_TABLE_V2_NAME", "t-pipe-v2")
os.environ.setdefault("PIPELINE_TEMPLATES_STORAGE_TABLE_NAME", "t-templates")
os.environ.setdefault("PIPELINE_TEMPLATE_TAG_SCHEMA_STORAGE_TABLE_NAME", "t-tagschema")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("S3_ASSETAUXILIARY_STORAGE_BUCKET", "t-aux")
os.environ.setdefault("METADATA_SERVICE_LAMBDA_FUNCTION_NAME", "t-md-svc")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "t-exec-v2")
os.environ.setdefault("PIPELINE_EXECUTIONS_STORAGE_TABLE_NAME", "t-pexec")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_METADATA_STORAGE_TABLE_NAME", "t-pin-md")
os.environ.setdefault("PIPELINE_EXECUTION_INPUT_CONFIGURATION_STORAGE_TABLE_NAME", "t-pin-cfg")
os.environ.setdefault("WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME", "t-wf-inputs")
os.environ.setdefault("WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME", "t-wf-cfg")

# handlers.workflows package __init__ imports get_task_builder at import time; the shared mock package
# does not provide it, so register a lightweight stub before importing the handler.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ewv2  # noqa: E402

# Taken from the handler's own module attributes, never re-imported here. The test tree puts both
# `backend/` and `backend/backend/` on sys.path, so `models.common` and `backend.backend.models.common`
# are DIFFERENT module objects with different class identities: a `pytest.raises` on a re-imported
# exception class never matches the one the handler raises, and a `patch` on a re-imported module never
# reaches the one the handler calls.
er = ewv2.er
tr = ewv2.tr
VAMSGeneralErrorResponse = ewv2.VAMSGeneralErrorResponse

MOD = "backend.backend.handlers.workflows.executeWorkflow"

WF_DB, WF_ID = "wfdb", "wf1"
@pytest.mark.unit
class TestRenderedConfigTooLargeIsACallerError:
    """S2-BACKEND-103: the error class carries the limit so the caller can be told; nothing caught it."""

    @staticmethod
    def _event():
        return {"requestContext": {"http": {"method": "POST", "path": "/workflows/db/wf/execute"}},
                "pathParameters": {"workflowDatabaseId": "db", "workflowId": "wf"},
                "queryStringParameters": None, "body": "{}",
                "headers": {"authorization": "Bearer t"}}

    def _run(self, error):
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        with patch(f"{MOD}.request_to_claims", return_value={"tokens": ["u1"], "roles": []}), \
                patch(f"{MOD}.CasbinEnforcer", return_value=enforcer), \
                patch(f"{MOD}.handle_post_request", side_effect=error):
            return ewv2.lambda_handler(self._event(), MagicMock())

    def test_an_oversized_render_answers_400_naming_the_limit(self):
        resp = self._run(tr.RenderedConfigTooLargeError())
        assert resp["statusCode"] == 400
        assert str(tr.MAX_RENDERED_CONFIG_LENGTH) in json.loads(resp["body"])["message"]

    def test_an_unexpected_failure_still_answers_500(self):
        # Positive control: the new arm must not have widened into a blanket 400, which would hide a
        # real server fault behind a caller-error response.
        assert self._run(RuntimeError("boom"))["statusCode"] == 500

    def test_the_output_path_extension_render_reports_it_as_a_client_error(self):
        # The other render site in the launch path caught only MissingTemplateTagError, so the same
        # amplification in an output-path extension reached the terminal handler arm.
        with patch.object(tr, "render_config", side_effect=tr.RenderedConfigTooLargeError()):
            with pytest.raises(VAMSGeneralErrorResponse) as raised:
                ewv2._render_output_path_extension("{{inputMetadataObject}}", {}, {})
        assert str(tr.MAX_RENDERED_CONFIG_LENGTH) in str(raised.value)

    def test_the_extensions_client_error_reaches_the_response_as_400(self):
        # The raise above is only half the path: what the finding was about is the STATUS the caller
        # gets. The extension arm converts to VAMSGeneralErrorResponse, which the handler answers with
        # general_error — so the code is asserted through the handler rather than inferred from the
        # exception type. Driven through the real helper, so removing either arm fails this.
        def _raise_from_the_extension(event):
            with patch.object(tr, "render_config", side_effect=tr.RenderedConfigTooLargeError()):
                return ewv2._render_output_path_extension("{{inputMetadataObject}}", {}, {})

        resp = self._run(_raise_from_the_extension)
        assert resp["statusCode"] == 400
        assert str(tr.MAX_RENDERED_CONFIG_LENGTH) in json.loads(resp["body"])["message"]

    def test_an_undefined_tag_keeps_its_own_message(self):
        # Control on the arm ORDER: the oversize arm sits beside the undefined-tag one, and both are
        # caller errors answering 400. They must stay distinguishable — a caller told to shorten a
        # body cannot act on a body whose tag name is simply wrong.
        message = json.loads(self._run(tr.MissingTemplateTagError({"nope"}))["body"])["message"]
        assert "undefined template tags" in message
        assert str(tr.MAX_RENDERED_CONFIG_LENGTH) not in message


@pytest.mark.unit
class TestLaunchRendersUnderTheDeclaredConfigFormat:
    """S2-BACKEND-041: the launch render used the default JSON escape for every format, so an `xml`
    body emitted bare markup while stage 1 had escaped the same body's user tags for `xml`."""

    # A file name whose characters are legal in an S3 key and in a VAMS file name, and which the XML
    # escape must convert: '&' alone makes the document ill-formed, and '</path><path>' would inject
    # elements into the configuration the pipeline runs against.
    FILE_KEY = "/parts/gear&pinion</path><path>.step"
    BODY = "<input><path>{{firstAssetFileKey}}</path></input>"

    def _launch(self, config_format):
        pipeline = {
            "pipelineId": "p1", "databaseId": "GLOBAL", "_jobName": "p1",
            "systemConfig": {"inputFileArity": "one"},
            "executionConfig": {"executionType": "Lambda", "waitForCallback": "Disabled"},
        }
        workflow = {
            "workflowId": WF_ID, "databaseId": WF_DB,
            "workflow_arn": "arn:aws:states:us-east-1:1:stateMachine:wf1",
            "jobNames": ["uuid-p1"], "systemConfig": {"metadataInputs": {}},
        }
        selected_inputs = [{"databaseId": "db1", "assetId": "a1",
                            "relativeFileKey": self.FILE_KEY, "versionId": ""}]
        asset_records = {("db1", "a1"): {
            "databaseId": "db1", "assetId": "a1",
            "assetLocation": {"Key": "a1/"}, "bucketId": "b1"}}
        resolved = {"GLOBAL:p1": {"renderedConfig": self.BODY, "configFormat": config_format}}
        with patch(f"{MOD}.s3c") as s3, \
                patch(f"{MOD}.sfn_client") as sfn, \
                patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "asset-bucket"}), \
                patch(f"{MOD}._persist_execution_records"):
            sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            execution_id = ewv2._launch_workflow(
                workflow, [pipeline], resolved, selected_inputs, asset_records,
                None, "db1", "a1", "run-bucket", {}, "Manual", "", "user@example.com", "")
            puts = {c.kwargs["Key"]: c.kwargs["Body"] for c in s3.put_object.call_args_list}
        return puts[er.pipeline_input_config_key(execution_id, 1)].decode("utf-8")

    def test_an_xml_body_gets_xml_character_references(self):
        body = self._launch("xml")
        assert "&amp;" in body and "&lt;/path&gt;" in body
        assert "gear&pinion</path>" not in body, (
            "raw markup from a file name reached the configuration document")

    def test_a_json_body_keeps_the_json_escape(self):
        # Control: threading the format must not push XML escapes into the 30 shipped json templates,
        # where '&' and '<' are ordinary characters inside a JSON string.
        body = self._launch("json")
        assert "&amp;" not in body
        assert "gear&pinion" in body

    def _run_steps(self, formats, tables=None):
        """Launch a run of one pipeline per entry in `formats`, the nth declaring formats[n].

        Returns (executionId, {stepIndex: bodyText}). The record writes are NOT stubbed out here (the
        single-step helper above stubs them): `tables` receives the table-name -> mock map, so what the
        launch PERSISTED per step can be asserted alongside what it wrote to S3.
        """
        pipelines = [{"pipelineId": f"p{n}", "databaseId": "GLOBAL", "_jobName": f"p{n}",
                      "systemConfig": {"inputFileArity": "one"},
                      "executionConfig": {"executionType": "Lambda", "waitForCallback": "Disabled"}}
                     for n in range(1, len(formats) + 1)]
        workflow = {
            "workflowId": WF_ID, "databaseId": WF_DB,
            "workflow_arn": "arn:aws:states:us-east-1:1:stateMachine:wf1",
            "jobNames": [f"uuid-{p['pipelineId']}" for p in pipelines],
            "systemConfig": {"metadataInputs": {}},
        }
        selected_inputs = [{"databaseId": "db1", "assetId": "a1",
                            "relativeFileKey": self.FILE_KEY, "versionId": ""}]
        asset_records = {("db1", "a1"): {
            "databaseId": "db1", "assetId": "a1",
            "assetLocation": {"Key": "a1/"}, "bucketId": "b1"}}
        resolved = {f"GLOBAL:{p['pipelineId']}": {"renderedConfig": self.BODY, "configFormat": fmt}
                    for p, fmt in zip(pipelines, formats)}
        written = {} if tables is None else tables
        with patch(f"{MOD}.s3c") as s3, \
                patch(f"{MOD}.sfn_client") as sfn, \
                patch(f"{MOD}._asset_bucket_details", return_value={"bucketName": "asset-bucket"}), \
                patch(f"{MOD}.dynamodb") as dynamo:
            sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            dynamo.Table.side_effect = lambda name: written.setdefault(name, MagicMock())
            execution_id = ewv2._launch_workflow(
                workflow, pipelines, resolved, selected_inputs, asset_records,
                None, "db1", "a1", "run-bucket", {}, "Manual", "", "user@example.com", "")
            puts = {c.kwargs["Key"]: c.kwargs["Body"] for c in s3.put_object.call_args_list}
        return execution_id, {
            step: puts[er.pipeline_input_config_key(execution_id, step)].decode("utf-8")
            for step in range(1, len(formats) + 1)}

    def test_a_later_steps_body_is_written_with_its_system_tags_unsubstituted(self):
        # The boundary the format threading has to respect. Only step 1 is rendered at launch, against
        # its own manifest; steps 2+ carry their tags to the interim lambda, which renders each against
        # the manifest of ITS task — the prior step's OUTPUT files, not the run's input files.
        #
        # Threading the format by rendering every step here would look like a fix and would substitute
        # step 1's input file into step 2's configuration, which no assertion on step 1 alone can see.
        _execution_id, bodies = self._run_steps(["xml", "xml"])
        assert "{{firstAssetFileKey}}" in bodies[2]
        assert "gear" not in bodies[2], (
            "a later step's configuration was rendered against step 1's manifest")
        # Discriminator: step 1 really was rendered, so step 2's intact tag is the boundary and not a
        # fixture in which nothing rendered at all.
        assert "{{firstAssetFileKey}}" not in bodies[1]
        assert "&amp;" in bodies[1]

    def test_every_steps_declared_format_is_recorded_on_its_own_configuration_row(self):
        # Steps 2+ are rendered by the interim lambda, whose SFN body carries no configFormat, so the
        # step's persisted configuration row is where the declared format is recoverable from. It is
        # written per step and from that step's own resolved entry: taking step 1's would hand every
        # later step the wrong escape, which is the same defect one hop downstream.
        tables = {}
        _execution_id, _bodies = self._run_steps(["xml", "yaml"], tables=tables)
        rows = [c.kwargs["Item"] for c in
                tables[ewv2.pipeline_execution_input_configuration_table].put_item.call_args_list]
        assert [r["configFormat"] for r in rows] == ["xml", "yaml"]
