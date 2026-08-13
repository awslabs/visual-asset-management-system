"""Metadata-read identity for every execute path, the databaseMetadata gate's effect on a NAMED
metadata-source database, the answer a request whose recorded inputs exceed one DynamoDB item gets, and
the per-step input narrowing the SFN input carries for steps 2..N.

Two things this module deliberately does not take from the shared fixtures:

  - `tests/conftest.py` stubs `handlers.auth.request_to_claims` to return claims for ANY event, which
    makes an invoke payload carrying `authorizer: None` look authenticated. The identity assertions here
    run the REAL claims extraction (loaded from its own file) over the payload the handler actually
    sends, so a payload that would fail in the metadata service fails here.
  - the metadata-service invoke is captured rather than mocked away, so the assertions are on the bytes
    that would reach the service.

The handler loads env vars and resource names at import; the seeds below mirror test_executeWorkflow.py
so this module imports standalone as well as inside a full-suite run."""
import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import botocore
import pytest

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

if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows import executeWorkflow as ew  # noqa: E402

MOD = "backend.backend.handlers.workflows.executeWorkflow"

_HANDLER_DIR = os.path.dirname(os.path.abspath(ew.__file__))
_AUTH_INIT = os.path.normpath(os.path.join(_HANDLER_DIR, "..", "auth", "__init__.py"))


def _real_request_to_claims():
    """The production claims extraction, loaded from its own file so the conftest stub (which answers
    claims for any event) cannot stand in for it."""
    spec = importlib.util.spec_from_file_location("_real_auth_claims_for_identity_test", _AUTH_INIT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.request_to_claims


def _api_event():
    return {"requestContext": {"http": {"method": "POST", "path": "/workflows/db1/wf1/execute"},
                               "authorizer": {"jwt": {"claims": {"vams:tokens": '["user1"]'}}}},
            "pathParameters": {"workflowDatabaseId": "db1", "workflowId": "wf1"},
            "queryStringParameters": {},
            "body": json.dumps({})}


def _cross_call_event():
    """The event shape workflowTriggerDispatch._invoke_execute sends: no authorizer at all."""
    return {"requestContext": {"http": {"method": "POST", "path": "/workflows/db1/wf1/execute"}},
            "pathParameters": {"workflowDatabaseId": "db1", "workflowId": "wf1"},
            "queryStringParameters": {},
            "body": json.dumps({}),
            "lambdaCrossCall": {"userName": "SYSTEM_USER"}}


def _captured_payloads(fetch, event):
    """Run one metadata fetch against a captured invoke, returning (result, [payloads sent])."""
    payloads = []

    def _capture(payload):
        payloads.append(payload)
        stream = MagicMock()
        stream.read.return_value = json.dumps({
            "statusCode": 200,
            "body": json.dumps({"metadata": [{"metadataKey": "k", "metadataValue": "v"}]}),
        }).encode("utf-8")
        return {"Payload": stream}

    with patch(f"{MOD}._metadata_service_lambda", side_effect=_capture):
        result = fetch(event)
    return result, payloads


@pytest.mark.unit
class TestMetadataReadIdentity:
    """Every metadata read of the envelope carries an identity the metadata service can extract."""

    _FETCHES = {
        "asset": lambda event: ew._fetch_metadata("db1", "a1", {}, event),
        "file metadata": lambda event: ew._fetch_file_metadata("db1", "a1", "/f.glb", "metadata", event),
        "file attributes": lambda event: ew._fetch_file_metadata("db1", "a1", "/f.glb", "attribute", event),
        "database": lambda event: ew._fetch_database_metadata("db1", event),
    }

    @pytest.mark.parametrize("label", sorted(_FETCHES))
    def test_a_cross_call_read_resolves_to_the_cross_call_identity(self, label):
        real_claims = _real_request_to_claims()
        result, payloads = _captured_payloads(self._FETCHES[label], _cross_call_event())
        assert len(payloads) == 1
        # The real extraction over the payload the handler sends: no TypeError, and the cross-call
        # identity is what the metadata service would authorize as.
        claims = real_claims(payloads[0])
        assert claims["tokens"] == ["SYSTEM_USER"]
        # An `authorizer: None` key is exactly what breaks the extraction, so it must not be sent.
        assert "authorizer" not in payloads[0]["requestContext"]
        assert result == [{"metadataKey": "k", "metadataValue": "v"}]

    @pytest.mark.parametrize("label", sorted(_FETCHES))
    def test_an_api_read_forwards_the_callers_authorizer(self, label):
        real_claims = _real_request_to_claims()
        _result, payloads = _captured_payloads(self._FETCHES[label], _api_event())
        claims = real_claims(payloads[0])
        assert claims["tokens"] == ["user1"]
        assert "lambdaCrossCall" not in payloads[0]

    def test_the_asset_and_file_reads_keep_their_query_parameters(self):
        _result, asset_payloads = _captured_payloads(
            lambda event: ew._fetch_metadata("db1", "a1", {"versionId": "v7"}, event),
            _cross_call_event())
        assert asset_payloads[0]["queryStringParameters"] == {"versionId": "v7"}
        assert asset_payloads[0]["requestContext"]["http"] == {
            "path": "/database/db1/assets/a1/metadata", "method": "GET"}
        assert asset_payloads[0]["pathParameters"] == {"databaseId": "db1", "assetId": "a1"}

        _result, file_payloads = _captured_payloads(
            lambda event: ew._fetch_file_metadata("db1", "a1", "/f.glb", "attribute", event),
            _cross_call_event())
        assert file_payloads[0]["queryStringParameters"] == {"filePath": "/f.glb", "type": "attribute"}
        assert file_payloads[0]["requestContext"]["http"] == {
            "path": "/database/db1/assets/a1/metadata/file", "method": "GET"}

    def test_a_failed_asset_read_is_logged_rather_than_silently_empty(self):
        # A metadata-service error answers a payload with no statusCode, which is otherwise
        # indistinguishable from an asset carrying nothing.
        stream = MagicMock()
        stream.read.return_value = json.dumps(
            {"errorType": "TypeError", "errorMessage": "boom"}).encode("utf-8")
        with patch(f"{MOD}._metadata_service_lambda", return_value={"Payload": stream}), \
             patch(f"{MOD}.logger") as m_logger:
            assert ew._fetch_metadata("db1", "a1", {}, _cross_call_event()) == []
        assert m_logger.warning.called

    def test_a_failed_file_read_is_logged_rather_than_silently_empty(self):
        stream = MagicMock()
        stream.read.return_value = json.dumps(
            {"errorType": "TypeError", "errorMessage": "boom"}).encode("utf-8")
        with patch(f"{MOD}._metadata_service_lambda", return_value={"Payload": stream}), \
             patch(f"{MOD}.logger") as m_logger:
            assert ew._fetch_file_metadata("db1", "a1", "/f.glb", "metadata",
                                           _cross_call_event()) == []
        assert m_logger.warning.called

    def test_an_absent_payload_is_logged_for_the_asset_and_file_reads(self):
        with patch(f"{MOD}._metadata_service_lambda", return_value={"Payload": ""}), \
             patch(f"{MOD}.logger") as m_logger:
            assert ew._fetch_metadata("db1", "a1", {}, _cross_call_event()) == []
            assert ew._fetch_file_metadata("db1", "a1", "/f.glb", "metadata",
                                           _cross_call_event()) == []
        assert m_logger.warning.call_count == 2

    def test_a_trigger_launched_run_captures_asset_and_file_metadata(self):
        """The end-to-end shape of the failure: a fileUpload-triggered execute (cross-call, no
        authorizer) must hand its pipelines a populated envelope."""
        from backend.tests.handlers.workflows import test_executeWorkflow as tew
        real_claims = _real_request_to_claims()
        payloads = []

        def _capture(payload):
            payloads.append(payload)
            # Answer metadata only for a payload whose identity the metadata service can extract.
            try:
                authorized = bool(real_claims(json.loads(json.dumps(payload)))["tokens"])
            except Exception:
                return {"Payload": ""}
            if not authorized:
                return {"Payload": ""}
            stream = MagicMock()
            stream.read.return_value = json.dumps({
                "statusCode": 200,
                "body": json.dumps({"metadata": [{"metadataKey": "PROMPT", "metadataValue": "p"}]}),
            }).encode("utf-8")
            return {"Payload": stream}

        workflow = dict(tew._WORKFLOW)
        workflow["systemConfig"] = dict(tew._WORKFLOW["systemConfig"])
        workflow["systemConfig"]["metadataInputs"] = {
            "assetMetadata": True, "fileMetadata": True, "fileAttributes": True,
            "databaseMetadata": True}
        p = tew.TestExecuteOrchestration()._patches(workflow=workflow)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        event = _cross_call_event()
        event["body"] = json.dumps(body)
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._metadata_service_lambda", side_effect=_capture), \
             patch(f"{MOD}.s3c") as m_s3, patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ew.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        assert payloads, "no metadata-service read was attempted"
        metadata_puts = [c for c in m_s3.put_object.call_args_list
                         if c.kwargs.get("Key", "").endswith("metadata.json")]
        assert metadata_puts, "the execution metadata file was not written"
        envelope = json.loads(metadata_puts[0].kwargs["Body"].decode("utf-8"))
        asset_record = next(f for f in envelope["assets"][0]["files"] if f["fileKey"] == "/")
        assert asset_record["metadata"] == {"PROMPT": "p"}
        file_record = next(f for f in envelope["assets"][0]["files"] if f["fileKey"] == "/f.glb")
        assert file_record["metadata"] == {"PROMPT": "p"}


@pytest.mark.unit
class TestNamedSourceDatabaseAgainstTheGate:
    """A named metadata-source database and the workflow's databaseMetadata gate. The write path and
    the read path (executionService gates on the persisted inputMetadataDatabaseId) must agree."""

    def _harness(self, database_metadata):
        from backend.tests.handlers.workflows import test_executeWorkflow as tew
        workflow, pipeline = tew.TestExecuteOrchestration()._results_only_workflow()
        workflow = dict(workflow)
        workflow["systemConfig"] = dict(workflow["systemConfig"])
        workflow["systemConfig"]["metadataInputs"] = {
            "assetMetadata": True, "fileMetadata": False, "fileAttributes": False,
            "databaseMetadata": database_metadata}
        return tew, tew.TestExecuteOrchestration()._patches(workflow=workflow, pipeline=pipeline)

    def _run(self, tew, patches, body, enforcer=None):
        tables = {}
        enforcer_patch = (patch(f"{MOD}.CasbinEnforcer", return_value=enforcer) if enforcer
                          else patches["enforcer"])
        with patches["get_workflow"], patches["get_pipeline"], patches["default_bucket"], \
             patches["asset_bucket"], patches["exists"], patches["claims"], enforcer_patch, \
             patch(f"{MOD}._get_asset",
                   side_effect=lambda d, a: {"databaseId": d, "assetId": a, "assetName": a,
                                             "bucketId": "bkt-1", "assetLocation": {"Key": f"{a}/"}}), \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}._fetch_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_file_metadata", return_value=[]), \
             patch(f"{MOD}._fetch_database_metadata", return_value=[]) as m_db, \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.side_effect = lambda name: tables.setdefault(name, MagicMock())
            resp = ew.lambda_handler(tew._event(body=body), MagicMock())
        return resp, tables, m_db

    def test_the_gate_off_persists_no_source_database_so_the_read_path_cannot_gate_on_it(self):
        tew, patches = self._harness(database_metadata=False)
        resp, tables, m_db = self._run(
            tew, patches, {"inputFiles": [], "metadataSourceDatabaseId": "finance-db"})
        assert resp["statusCode"] == 200, resp["body"]
        m_db.assert_not_called()
        cfg_row = tables["t-wf-cfg"].put_item.call_args_list[0].kwargs["Item"]
        # executionService._metadata_source_entities reads inputMetadataDatabaseId and requires
        # database GET on it, so recording a database the launch never authorized locks the launcher
        # out of their own execution.
        assert cfg_row["inputMetadataDatabaseId"] == ""
        assert cfg_row["metadataSourceDatabases"] == []

    def test_the_gate_off_names_the_unused_source_database_in_a_warning(self):
        tew, patches = self._harness(database_metadata=False)
        resp, _tables, _m_db = self._run(
            tew, patches, {"inputFiles": [], "metadataSourceDatabaseId": "finance-db"})
        warnings = json.loads(resp["body"])["message"]["warnings"] or []
        named = [w for w in warnings if "finance-db" in w]
        assert len(named) == 1
        assert "databaseMetadata input is turned off" in named[0]

    def test_the_gate_off_makes_no_authorization_decision_it_then_records(self):
        # The gate-off run must not reach a state where an unauthorized database is persisted: with
        # the id dropped there is nothing to authorize and nothing to record.
        tew, patches = self._harness(database_metadata=False)
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.return_value = True
        resp, tables, _m_db = self._run(
            tew, patches, {"inputFiles": [], "metadataSourceDatabaseId": "finance-db"},
            enforcer=enforcer)
        assert resp["statusCode"] == 200, resp["body"]
        database_objects = [c.args[0] for c in enforcer.enforce.call_args_list
                            if c.args[0].get("object__type") == "database"]
        assert database_objects == []
        cfg_row = tables["t-wf-cfg"].put_item.call_args_list[0].kwargs["Item"]
        assert cfg_row["inputMetadataDatabaseId"] == ""

    def test_the_gate_on_still_authorizes_and_records_the_named_database(self):
        tew, patches = self._harness(database_metadata=True)
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.return_value = True
        resp, tables, m_db = self._run(
            tew, patches, {"inputFiles": [], "metadataSourceDatabaseId": "finance-db"},
            enforcer=enforcer)
        assert resp["statusCode"] == 200, resp["body"]
        database_objects = [c.args[0] for c in enforcer.enforce.call_args_list
                            if c.args[0].get("object__type") == "database"]
        assert database_objects == [{"databaseId": "finance-db", "object__type": "database"}]
        m_db.assert_called_once()
        cfg_row = tables["t-wf-cfg"].put_item.call_args_list[0].kwargs["Item"]
        assert cfg_row["inputMetadataDatabaseId"] == "finance-db"
        assert cfg_row["metadataSourceDatabases"] == ["finance-db"]

    def test_the_gate_on_still_denies_a_named_database_the_caller_cannot_read(self):
        tew, patches = self._harness(database_metadata=True)
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.side_effect = lambda obj, action, *a, **k: (
            obj.get("object__type") != "database")
        resp, _tables, _m_db = self._run(
            tew, patches, {"inputFiles": [], "metadataSourceDatabaseId": "finance-db"},
            enforcer=enforcer)
        assert resp["statusCode"] == 403


@pytest.mark.unit
class TestOversizedRecordAnswer:
    """A record the request made too large for one DynamoDB item is answered with what to change, not
    an internal error. The record writers apply their own budgets; the fields outside them (template tag
    values, the metadata-source lists) can still push an item past 400 KB."""

    def _item_size_error(self):
        return botocore.exceptions.ClientError(
            {"Error": {"Code": "ValidationException",
                       "Message": "Item size has exceeded the maximum allowed size"},
             "ResponseMetadata": {"HTTPStatusCode": 400}},
            "PutItem")

    def test_an_item_size_rejection_answers_400_naming_what_to_reduce(self):
        from backend.tests.handlers.workflows import test_executeWorkflow as tew
        p = tew.TestExecuteOrchestration()._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb"), \
             patch(f"{MOD}._persist_execution_records", side_effect=self._item_size_error()):
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            resp = ew.lambda_handler(tew._event(body=body), MagicMock())
        assert resp["statusCode"] == 400, resp["body"]
        message = json.loads(resp["body"])["message"]
        assert "too large to store" in message
        assert "template tag values" in message
        # The started execution is stopped by the record-write path, so nothing keeps running.
        m_sfn.stop_execution.assert_called_once()

    def test_another_validation_exception_is_still_an_internal_error(self):
        # Only the item-size message is the caller's to fix; the other ValidationException causes
        # (malformed key, bad update expression) are faults.
        from backend.tests.handlers.workflows import test_executeWorkflow as tew
        p = tew.TestExecuteOrchestration()._patches()
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"}]}
        err = botocore.exceptions.ClientError(
            {"Error": {"Code": "ValidationException",
                       "Message": "ExpressionAttributeNames contains invalid key"},
             "ResponseMetadata": {"HTTPStatusCode": 400}},
            "PutItem")
        with p["get_workflow"], p["get_pipeline"], p["get_asset"], p["default_bucket"], \
             p["asset_bucket"], p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb"), \
             patch(f"{MOD}._persist_execution_records", side_effect=err):
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            resp = ew.lambda_handler(tew._event(body=body), MagicMock())
        assert resp["statusCode"] == 500

    def test_the_item_size_probe_reads_the_message_not_the_code(self):
        assert ew._is_item_size_rejection(self._item_size_error()) is True
        assert ew._is_item_size_rejection(botocore.exceptions.ClientError(
            {"Error": {"Code": "ValidationException", "Message": "Some other problem"}},
            "PutItem")) is False
        assert ew._is_item_size_rejection(botocore.exceptions.ClientError(
            {"Error": {"Code": "ValidationException"}}, "PutItem")) is False


@pytest.mark.unit
class TestPerStepInputNarrowingInTheSfnInput:
    """Steps 2..N have their manifests assembled by the interim lambda, which can only narrow the run's
    selection to a step's own share if it is told that step's filters and arity. Both travel beside
    stepMetadataS3Keys, one entry per pipeline in workflow order."""

    _FILTERS_P1 = {"allow": ["*.glb"], "exclude": ["*.previewFile.*"]}
    _FILTERS_P2 = {"allow": ["*.ply"], "exclude": []}

    def _two_step(self):
        """A two-pipeline workflow whose steps declare different filters and arities."""
        from backend.tests.handlers.workflows import test_executeWorkflow as tew
        workflow = dict(tew._WORKFLOW)
        workflow["systemConfig"] = dict(tew._WORKFLOW["systemConfig"])
        workflow["systemConfig"]["inputFileArity"] = "multi"
        workflow["jobNames"] = ["job-p1", "job-p2"]
        workflow["specifiedPipelines"] = [
            {"pipelineDatabaseId": "db1", "pipelineId": "p1", "jobName": "p1"},
            {"pipelineDatabaseId": "db1", "pipelineId": "p2", "jobName": "p2"},
        ]

        def _pipeline(system_config, pipeline_id):
            record = dict(tew._PIPELINE)
            record["pipelineId"] = pipeline_id
            record["systemConfig"] = {**tew._PIPELINE["systemConfig"], **system_config}
            return record

        pipelines = {
            "p1": _pipeline({"inputFileArity": "multi", "inputFileFilters": self._FILTERS_P1}, "p1"),
            "p2": _pipeline({"inputFileArity": "one", "inputFileFilters": self._FILTERS_P2}, "p2"),
        }
        return tew, workflow, pipelines

    def _launch(self, tew, workflow, pipelines, template_overrides=None):
        p = tew.TestExecuteOrchestration()._patches(workflow=workflow)
        body = {"inputFiles": [{"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.glb"},
                               {"databaseId": "db1", "assetId": "a1", "relativeFileKey": "/f.ply"}]}
        resolve = ew._resolve_pipeline_configs

        def _resolve_with_overrides(*args, **kwargs):
            errors, resolved = resolve(*args, **kwargs)
            for composite, entry in (resolved or {}).items():
                entry["templateOverrides"] = (template_overrides or {}).get(composite, {})
            return errors, resolved

        with p["get_workflow"], p["get_asset"], p["default_bucket"], p["asset_bucket"], \
             p["exists"], p["enforcer"], p["claims"], \
             patch(f"{MOD}._get_pipeline", side_effect=lambda db, pid: dict(pipelines[pid])), \
             patch(f"{MOD}._resolve_pipeline_configs", side_effect=_resolve_with_overrides), \
             patch(f"{MOD}._running_execution_exists", return_value=False), \
             patch(f"{MOD}.s3c"), patch(f"{MOD}.sfn_client") as m_sfn, \
             patch(f"{MOD}.dynamodb") as m_dynamo:
            m_sfn.start_execution.return_value = {"executionArn": "arn:exec"}
            m_dynamo.Table.return_value = MagicMock()
            resp = ew.lambda_handler(tew._event(body=body), MagicMock())
        assert resp["statusCode"] == 200, resp["body"]
        return json.loads(m_sfn.start_execution.call_args.kwargs["input"])

    def test_each_step_contributes_its_own_filters_and_arity_in_workflow_order(self):
        tew, workflow, pipelines = self._two_step()
        sent = self._launch(tew, workflow, pipelines)
        assert sent["stepInputFilters"] == [self._FILTERS_P1, self._FILTERS_P2]
        assert sent["stepInputArity"] == ["multi", "one"]
        # One entry per pipeline, aligned index-for-index with the keys the ASL already indexes.
        assert len(sent["stepInputFilters"]) == len(sent["stepMetadataS3Keys"])
        assert len(sent["stepInputArity"]) == len(sent["stepMetadataS3Keys"])

    def test_a_step_declaring_no_arity_resolves_to_the_default_rather_than_an_empty_string(self):
        tew, workflow, pipelines = self._two_step()
        pipelines["p2"]["systemConfig"] = {
            k: v for k, v in pipelines["p2"]["systemConfig"].items() if k != "inputFileArity"}
        pipelines["p1"]["systemConfig"]["inputFileArity"] = None
        sent = self._launch(tew, workflow, pipelines)
        assert sent["stepInputArity"] == ["one", "one"]

    def test_a_template_override_is_what_travels_not_the_pipelines_declaration(self):
        # Filters and arity are template-overridable, so the value the interim lambda applies must be
        # the effective one for THIS execution.
        tew, workflow, pipelines = self._two_step()
        overridden = {"allow": ["*.glb"], "exclude": []}
        sent = self._launch(tew, workflow, pipelines, template_overrides={
            "db1:p2": {"inputFileFilters": overridden, "inputFileArity": "multi"}})
        assert sent["stepInputFilters"] == [self._FILTERS_P1, overridden]
        assert sent["stepInputArity"] == ["multi", "multi"]
