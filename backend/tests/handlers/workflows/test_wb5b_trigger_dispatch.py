# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for WB5b file-upload trigger delivery: the pure matcher (common/workflows/
triggerMatching.py) and the dispatcher handler (handlers/workflows/sfn/workflowTriggerDispatch.py)."""

import json
import os
import sys
import types

import pytest
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
    ResponseStreamingError,
)
from unittest.mock import MagicMock, patch

from backend.backend.common.workflows import triggerMatching as tm
from tests.common.fileAttributionFakes import (
    AttributionAssetTable,
    AttributionBucketsTable,
    asset_row,
    registration,
)


@pytest.mark.unit
class TestTriggerMatching:
    def _trigger(self, wf_db="db1", wf_id="wf1", enabled=True, allow=None, exclude=None, defaults=None):
        return {
            "triggerType": "fileUpload", "workflowDatabaseId": wf_db, "workflowId": wf_id,
            "enabled": enabled,
            "triggerConfig": {
                "inputFileFilters": {"allow": allow or [], "exclude": exclude or []},
                "defaultTemplateIds": defaults or {},
            },
        }

    def test_disabled_trigger_does_not_fire(self):
        rows = [self._trigger(enabled=False)]
        assert tm.match_fileupload_triggers(rows, "db1", "a1", "/x.glb") == []

    def test_database_scope(self):
        # A db2 trigger must not fire for a db1 upload; GLOBAL fires for any db.
        rows = [self._trigger(wf_db="db2"), self._trigger(wf_db="GLOBAL", wf_id="wfG")]
        matches = tm.match_fileupload_triggers(rows, "db1", "a1", "/x.glb")
        assert [m[1] for m in matches] == ["wfG"]

    def test_filter_allow_and_exclude(self):
        rows = [self._trigger(allow=[".glb"]), self._trigger(wf_id="wf2", allow=[".e57"])]
        matches = tm.match_fileupload_triggers(rows, "db1", "a1", "/model.glb")
        assert [m[1] for m in matches] == ["wf1"]  # only the .glb-allow trigger fires

    def test_exclude_wins(self):
        rows = [self._trigger(allow=[".glb"], exclude=["*/skip/*"])]
        assert tm.match_fileupload_triggers(rows, "db1", "a1", "/skip/model.glb") == []

    def test_body_carries_default_template_params_and_trigger_type(self):
        rows = [self._trigger(defaults={"db1:convert": "tpl-A", "GLOBAL:label": "tpl-B"})]
        matches = tm.match_fileupload_triggers(rows, "db1", "a1", "/x.glb", version_id="v9")
        assert len(matches) == 1
        _wfdb, _wfid, body = matches[0]
        assert body["triggerType"] == "fileUpload"
        assert body["inputFiles"][0] == {
            "databaseId": "db1", "assetId": "a1", "relativeFileKey": "/x.glb", "versionId": "v9"}
        assert body["outputAssetId"] == "a1" and body["outputDatabaseId"] == "db1"
        # defaultTemplateIds keyed by composite -> params keyed by pipelineId (last segment).
        assert body["pipelineExecutionParameters"]["convert"] == {"templateId": "tpl-A"}
        assert body["pipelineExecutionParameters"]["label"] == {"templateId": "tpl-B"}

    def test_arity_none_workflow_fires_with_no_input_files(self):
        """An arity-'none' workflow rejects any input file at launch, so a trigger emitting the
        uploaded file would make it permanently inert. The uploaded file still names the asset the run
        writes back to — the explicit output pair the zero-input launch path requires."""
        rows = [self._trigger(wf_id="wfNone")]
        matches = tm.match_fileupload_triggers(
            rows, "db1", "a1", "/prompt.txt", version_id="v3",
            input_file_arity_for=lambda wfdb, wf: "none")
        assert len(matches) == 1
        _wfdb, _wfid, body = matches[0]
        assert body["inputFiles"] == []
        assert body["outputAssetId"] == "a1" and body["outputDatabaseId"] == "db1"
        assert body["triggerType"] == "fileUpload"

    def test_other_arities_still_take_the_uploaded_file(self):
        rows = [self._trigger(wf_id="wf1")]
        for arity in ("one", "multi", "", None):
            _wfdb, _wfid, body = tm.match_fileupload_triggers(
                rows, "db1", "a1", "/x.glb",
                input_file_arity_for=lambda wfdb, wf, a=arity: a)[0]
            assert [f["relativeFileKey"] for f in body["inputFiles"]] == ["/x.glb"]
        # No lookup supplied at all reads the same way.
        _wfdb, _wfid, body = tm.match_fileupload_triggers(rows, "db1", "a1", "/x.glb")[0]
        assert len(body["inputFiles"]) == 1

    def test_arity_is_resolved_per_workflow(self):
        rows = [self._trigger(wf_id="wfNone"), self._trigger(wf_id="wfOne")]
        arities = {"wfNone": "none", "wfOne": "one"}
        bodies = {wf: body for _db, wf, body in tm.match_fileupload_triggers(
            rows, "db1", "a1", "/x.glb",
            input_file_arity_for=lambda wfdb, wf: arities[wf])}
        assert bodies["wfNone"]["inputFiles"] == []
        assert len(bodies["wfOne"]["inputFiles"]) == 1


# ---- Dispatcher handler ----

os.environ.setdefault("WORKFLOW_TRIGGERS_STORAGE_TABLE_NAME", "t-triggers")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "t-assets")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "t-buckets")
os.environ.setdefault("EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME", "t-execv2")

# handlers.workflows package __init__ imports get_task_builder at import; stub it.
if "common.workflows.stepfunctions_builder" not in sys.modules:
    _stub = types.ModuleType("common.workflows.stepfunctions_builder")
    _stub.get_task_builder = lambda *a, **k: None
    sys.modules["common.workflows.stepfunctions_builder"] = _stub

from backend.backend.handlers.workflows.sfn import workflowTriggerDispatch as wd
from backend.backend.common.workflows import executionLocks as el  # noqa: E402

DMOD = "backend.backend.handlers.workflows.sfn.workflowTriggerDispatch"


@pytest.mark.unit
class TestDispatcher:
    def test_iter_uploaded_objects_eventbridge_detail_in_sqs(self):
        event = {"Records": [{"body": json.dumps({
            "detail": {"bucket": {"name": "b1"}, "object": {"key": "a1/x.glb"}}})}]}
        assert list(wd._iter_uploaded_objects(event)) == [("b1", "a1/x.glb", "", None)]

    def test_iter_uploaded_objects_s3_records_in_sqs(self):
        event = {"Records": [{"body": json.dumps({
            "Records": [{"s3": {"bucket": {"name": "b2"}, "object": {"key": "a2/y.e57"}}}]})}]}
        assert list(wd._iter_uploaded_objects(event)) == [("b2", "a2/y.e57", "", None)]

    def test_iter_uploaded_objects_clean_detail_records(self):
        # The producer publishes a clean EventBridge detail carrying flat S3 Records.
        event = {"Records": [{"body": json.dumps({
            "detail": {"Records": [{"s3": {"bucket": {"name": "b3"}, "object": {"key": "a3/z.glb"}}}],
                       "ASSET_BUCKET_NAME": "b3"}})}]}
        assert list(wd._iter_uploaded_objects(event)) == [("b3", "a3/z.glb", "", None)]

    def test_iter_uploaded_objects_sns_notification_envelope(self):
        # Defensive: an SNS Notification envelope wrapping the S3 records is also unwrapped.
        inner = json.dumps({"Records": [{"s3": {"bucket": {"name": "b4"}, "object": {"key": "a4/w.obj"}}}]})
        event = {"Records": [{"body": json.dumps({"Type": "Notification", "Message": inner})}]}
        assert list(wd._iter_uploaded_objects(event)) == [("b4", "a4/w.obj", "", None)]

    def test_iter_uploaded_objects_carries_object_version_id(self):
        # S3 notification records carry the uploaded object's versionId; it pins the run to the
        # version that was uploaded rather than whatever is latest when the trigger fires.
        event = {"Records": [{"body": json.dumps({"detail": {"Records": [
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/1.glb", "versionId": "v1"}}},
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/2.glb", "versionId": "null"}}}]}})}]}
        assert list(wd._iter_uploaded_objects(event)) == [
            ("b", "a/1.glb", "v1", None), ("b", "a/2.glb", "", None)]

    def test_should_skip_key(self):
        assert wd._should_skip_key("folder/") is True
        assert wd._should_skip_key("a1/model.glb") is False

    def test_dispatch_launches_matching_triggers(self):
        trigger = {"triggerType": "fileUpload", "workflowDatabaseId": "GLOBAL", "workflowId": "wfG",
                   "enabled": True, "triggerConfig": {"inputFileFilters": {"allow": [".glb"]},
                                                      "defaultTemplateIds": {}}}
        with patch(f"{DMOD}._resolve_asset_relative_key", return_value=("db1", "a1", "/model.glb", "", "")), \
             patch(f"{DMOD}._invoke_execute", return_value=True) as m_invoke:
            launched = wd._dispatch_uploaded_file("b1", "a1/model.glb", [trigger])
        assert launched == 1
        m_invoke.assert_called_once()
        # The invoked body targets the uploaded file + fileUpload trigger type.
        _wfdb, _wfid, body = m_invoke.call_args.args
        assert body["triggerType"] == "fileUpload"
        assert body["inputFiles"][0]["relativeFileKey"] == "/model.glb"

    def test_dispatch_skips_reserved_key(self):
        with patch(f"{DMOD}._resolve_asset_relative_key") as m_resolve:
            launched = wd._dispatch_uploaded_file("b1", "folder/", [{}])
        assert launched == 0
        m_resolve.assert_not_called()

    def test_handler_no_triggers_short_circuits(self):
        with patch(f"{DMOD}._list_fileupload_triggers", return_value=[]):
            resp = wd.lambda_handler({"Records": []}, MagicMock())
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["workflowsLaunched"] == 0

    def test_per_file_failure_isolated(self):
        # A failure dispatching one file must not stop the batch (best-effort contract).
        event = {"Records": [{"body": json.dumps({"detail": {"Records": [
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/1.glb"}}},
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/2.glb"}}}]}})}]}
        with patch(f"{DMOD}._list_fileupload_triggers", return_value=[{"triggerType": "fileUpload"}]), \
             patch(f"{DMOD}._resolve_asset_relative_key",
                   side_effect=[RuntimeError("boom"), ("db", "a", "/2.glb", "", "")]), \
             patch(f"{DMOD}._launch_matching_triggers", return_value=1):
            resp = wd.lambda_handler(event, MagicMock())
        assert resp["statusCode"] == 200
        assert json.loads(resp["body"])["workflowsLaunched"] == 1  # second file still processed

    def test_trigger_enumeration_failure_raises_for_sqs_retry(self):
        # A total dispatch failure must raise so the SQS event source retries the batch and it
        # eventually reaches the DLQ, rather than returning 500 (which deletes the batch).
        with patch(f"{DMOD}._list_fileupload_triggers", side_effect=RuntimeError("throttled")):
            with pytest.raises(RuntimeError):
                wd.lambda_handler({"Records": []}, MagicMock())

    def test_batch_level_failure_outside_the_per_file_loop_raises(self):
        # A non-dict SQS record breaks enumeration itself; raising keeps the batch redeliverable
        # instead of letting SQS delete it.
        with patch(f"{DMOD}._list_fileupload_triggers", return_value=[{"triggerType": "fileUpload"}]), \
             patch(f"{DMOD}._iter_uploaded_objects", side_effect=AttributeError("not a dict")):
            with pytest.raises(AttributeError):
                wd.lambda_handler({"Records": [1]}, MagicMock())

    def test_unrecognized_envelope_warns(self):
        # A producer-side envelope change would otherwise silently stop every trigger.
        with patch(f"{DMOD}._list_fileupload_triggers", return_value=[{"triggerType": "fileUpload"}]), \
             patch.object(wd.logger, "warning") as m_warn:
            resp = wd.lambda_handler({"somethingElse": {}}, MagicMock())
        assert json.loads(resp["body"])["filesProcessed"] == 0
        m_warn.assert_called_once()

    def test_dispatch_threads_version_id(self):
        trigger = {"triggerType": "fileUpload", "workflowDatabaseId": "GLOBAL", "workflowId": "wfG",
                   "enabled": True, "triggerConfig": {"inputFileFilters": {"allow": [".glb"]},
                                                      "defaultTemplateIds": {}}}
        with patch(f"{DMOD}._resolve_asset_relative_key", return_value=("db1", "a1", "/model.glb", "", "")), \
             patch(f"{DMOD}._invoke_execute", return_value=True) as m_invoke:
            wd._dispatch_uploaded_file("b1", "a1/model.glb", [trigger], "ver-7")
        _wfdb, _wfid, body = m_invoke.call_args.args
        assert body["inputFiles"][0]["versionId"] == "ver-7"

    def test_dispatch_reads_the_targets_arity_from_its_workflow_record(self):
        # An arity-'none' workflow's trigger fires with no input files, sourced from the same
        # systemConfig read the chaining flag uses.
        trigger = {"triggerType": "fileUpload", "workflowDatabaseId": "GLOBAL", "workflowId": "wfNone",
                   "enabled": True, "triggerConfig": {"inputFileFilters": {"allow": []},
                                                      "defaultTemplateIds": {}}}
        wd._workflow_row_cache.clear()
        with patch(f"{DMOD}._resolve_asset_relative_key", return_value=("db1", "a1", "/p.txt", "", "")), \
             patch.object(wd.workflow_storage_table_v2, "get_item",
                          return_value={"Item": {"systemConfig": {"inputFileArity": "none"}}}), \
             patch(f"{DMOD}._invoke_execute", return_value=True) as m_invoke:
            wd._dispatch_uploaded_file("b1", "a1/p.txt", [trigger])
        _wfdb, _wfid, body = m_invoke.call_args.args
        assert body["inputFiles"] == []
        assert body["outputAssetId"] == "a1" and body["outputDatabaseId"] == "db1"

    def test_systemconfig_read_is_memoized_per_invocation(self):
        # One SQS batch can carry many objects for the same workflow; the record is read once.
        wd._workflow_row_cache.clear()
        with patch.object(wd.workflow_storage_table_v2, "get_item",
                          return_value={"Item": {"systemConfig": {"inputFileArity": "one",
                                                                  "allowWorkflowTriggerChaining": True}}}) as m_get:
            assert wd._workflow_input_file_arity("GLOBAL", "wf1") == "one"
            assert wd._workflow_allows_trigger_chaining("GLOBAL", "wf1") is True
            assert wd._workflow_input_file_arity("GLOBAL", "wf1") == "one"
        assert m_get.call_count == 1

    def test_unreadable_workflow_record_falls_back_to_conservative_defaults(self):
        wd._workflow_row_cache.clear()
        with patch.object(wd.workflow_storage_table_v2, "get_item",
                          side_effect=RuntimeError("throttled")):
            assert wd._workflow_input_file_arity("GLOBAL", "wf1") == ""
            assert wd._workflow_allows_trigger_chaining("GLOBAL", "wf1") is False


def _invoke_answering(status_code, message):
    """A lambda_client whose Invoke answers like executeWorkflowV2 with this status and body message."""
    payload = MagicMock()
    payload.read.return_value = json.dumps(
        {"statusCode": status_code, "body": json.dumps({"message": message})}).encode("utf-8")
    client = MagicMock()
    client.invoke.return_value = {"Payload": payload}
    return client


@pytest.mark.unit
class TestLaunchRefusedByAnExecutionLock:
    """A trigger whose launch is refused because a run already holds the asset, file or file version is
    the concurrency restriction doing its job (a repeated delivery of one file version, a burst under
    perAsset), so it is logged at INFO with the reason rather than as a failed launch."""

    @pytest.mark.parametrize("message", sorted(
        set(el.LOCK_CONFLICT_MESSAGE_BY_SCOPE.values()) | {el.LOCK_CONFLICT_MESSAGE}))
    def test_a_lock_conflict_is_logged_as_skipped_at_info(self, message):
        with patch(f"{DMOD}.lambda_client", _invoke_answering(400, message)), \
             patch(f"{DMOD}.logger") as log:
            assert wd._invoke_execute("db1", "wf1", {"triggerType": "fileUpload"}) is False
        log.warning.assert_not_called()
        logged = " ".join(str(c.args[0]) for c in log.info.call_args_list)
        assert "skipped" in logged and message in logged

    def test_any_other_400_is_still_a_warning(self):
        with patch(f"{DMOD}.lambda_client", _invoke_answering(400, "Workflow is disabled.")), \
             patch(f"{DMOD}.logger") as log:
            assert wd._invoke_execute("db1", "wf1", {"triggerType": "fileUpload"}) is False
        log.warning.assert_called_once()
        assert "skipped" not in " ".join(str(c.args[0]) for c in log.info.call_args_list)

    def test_a_lock_message_on_another_status_is_not_treated_as_a_conflict(self):
        with patch(f"{DMOD}.lambda_client",
                   _invoke_answering(500, el.LOCK_CONFLICT_MESSAGE)), \
             patch(f"{DMOD}.logger") as log:
            assert wd._invoke_execute("db1", "wf1", {"triggerType": "fileUpload"}) is False
        log.warning.assert_called_once()


@pytest.mark.unit
class TestExecuteInvokeIsNotRetried:
    """The executeWorkflowV2 Invoke is synchronous and launches an execution per delivered request, so
    a botocore retry of a slow-but-successful call would launch duplicate runs. The client must
    deliver exactly one attempt per call and wait out the callee's full runtime; only a connection that
    failed before delivery is sent again (TestConnectionFailureIsSentAgain)."""

    def test_invoke_client_delivers_one_attempt(self):
        # botocore normalizes to total_max_attempts (initial attempt included), so 1 means no retry.
        assert (wd.lambda_client.meta.config.retries or {}).get("total_max_attempts") == 1

    def test_read_timeout_covers_the_callees_runtime(self):
        # executeWorkflowV2 runs up to 15 minutes; a shorter read timeout would abandon a launch that
        # actually happened and report it as a failure.
        assert wd.lambda_client.meta.config.read_timeout >= 900

    def test_read_clients_keep_the_retrying_config(self):
        # Only the non-idempotent Invoke opts out; the read paths still retry throttles. Their
        # max_attempts=5 normalizes to 6 total attempts (5 retries on top of the initial one).
        assert wd.s3_client.meta.config.retries.get("total_max_attempts") == 6
        assert wd.dynamodb.meta.client.meta.config.retries.get("total_max_attempts") == 6


_ENDPOINT = "https://lambda.us-east-1.amazonaws.com/2015-03-31/functions/t-execv2/invocations"


def _closed():
    return ConnectionClosedError(endpoint_url=_ENDPOINT, error="RemoteDisconnected", request=None)


def _invoke_launching():
    """An Invoke response like executeWorkflowV2's 200 launch."""
    return _invoke_answering(200, "Workflow launched").invoke.return_value


@pytest.mark.unit
class TestConnectionFailureIsSentAgain:
    """#1521: a launch over a pooled connection the network closed while the dispatcher was idle failed
    with "Connection was closed before we received a valid response", executeWorkflow never had the
    call, and the dispatcher, which treats each launch as best-effort, dropped it: the upload's workflow
    never ran. A connection failure before delivery is now sent again right away, at most twice
    (common/workflows/executeWorkflowInvoke.py, shared with the reindex launcher); anything that answered
    or may have run is not."""

    @pytest.mark.parametrize("error", [
        _closed(),
        EndpointConnectionError(endpoint_url=_ENDPOINT, error="Name or service not known"),
        ConnectTimeoutError(endpoint_url=_ENDPOINT, error="timed out"),
    ], ids=["closed", "endpoint", "connect-timeout"])
    def test_a_connection_failure_is_sent_again_and_launches_once(self, error):
        client = MagicMock()
        client.invoke.side_effect = [error, _invoke_launching()]
        with patch(f"{DMOD}.lambda_client", client):
            assert wd._invoke_execute("db1", "wf1", {"triggerType": "fileUpload"}) is True
        assert client.invoke.call_count == 2
        first, second = (c.kwargs for c in client.invoke.call_args_list)
        # The same launch is sent again: same function, same event.
        assert first == second and first["FunctionName"] == wd.execute_workflow_v2_function

    def test_a_retried_launch_counts_once_for_the_uploaded_file(self):
        trigger = {"triggerType": "fileUpload", "workflowDatabaseId": "GLOBAL", "workflowId": "wfG",
                   "enabled": True, "triggerConfig": {"inputFileFilters": {"allow": [".glb"]},
                                                      "defaultTemplateIds": {}}}
        client = MagicMock()
        client.invoke.side_effect = [_closed(), _invoke_launching()]
        with patch(f"{DMOD}._resolve_asset_relative_key", return_value=("db1", "a1", "/model.glb", "", "")), \
             patch(f"{DMOD}._workflow_is_dispatchable", return_value=True), \
             patch(f"{DMOD}.lambda_client", client):
            assert wd._dispatch_uploaded_file("b1", "a1/model.glb", [trigger]) == 1
        assert client.invoke.call_count == 2

    def test_three_connection_failures_give_up_and_report_the_launch_failed(self):
        client = MagicMock()
        client.invoke.side_effect = _closed()
        with patch(f"{DMOD}.lambda_client", client), patch(f"{DMOD}.logger") as log:
            assert wd._invoke_execute("db1", "wf1", {"triggerType": "fileUpload"}) is False
        assert client.invoke.call_count == 3
        log.exception.assert_called_once()

    @pytest.mark.parametrize("message", sorted(
        set(el.LOCK_CONFLICT_MESSAGE_BY_SCOPE.values()) | {el.LOCK_CONFLICT_MESSAGE}))
    def test_a_lock_conflict_is_not_sent_again(self, message):
        client = _invoke_answering(400, message)
        with patch(f"{DMOD}.lambda_client", client):
            assert wd._invoke_execute("db1", "wf1", {"triggerType": "fileUpload"}) is False
        client.invoke.assert_called_once()

    @pytest.mark.parametrize("status", [400, 500])
    def test_an_answer_from_execute_workflow_is_not_sent_again(self, status):
        client = _invoke_answering(status, "refused")
        with patch(f"{DMOD}.lambda_client", client):
            assert wd._invoke_execute("db1", "wf1", {"triggerType": "fileUpload"}) is False
        client.invoke.assert_called_once()

    def test_a_function_error_is_not_sent_again(self):
        payload = MagicMock()
        payload.read.return_value = json.dumps({"errorMessage": "boom", "errorType": "Exception"}).encode()
        client = MagicMock()
        client.invoke.return_value = {"StatusCode": 200, "FunctionError": "Unhandled", "Payload": payload}
        with patch(f"{DMOD}.lambda_client", client):
            assert wd._invoke_execute("db1", "wf1", {"triggerType": "fileUpload"}) is False
        client.invoke.assert_called_once()

    @pytest.mark.parametrize("error", [
        ReadTimeoutError(endpoint_url=_ENDPOINT, error="Read timed out"),
        ResponseStreamingError(error="IncompleteRead"),
        ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "Rate Exceeded."}}, "Invoke"),
    ], ids=["read-timeout", "response-stream", "service-error"])
    def test_a_failure_after_delivery_or_a_service_error_is_not_sent_again(self, error):
        # A read timeout comes after the 900 s executeWorkflow can run and a broken stream after it
        # answered, so the call may have launched; a service error is Lambda's answer.
        client = MagicMock()
        client.invoke.side_effect = error
        with patch(f"{DMOD}.lambda_client", client):
            assert wd._invoke_execute("db1", "wf1", {"triggerType": "fileUpload"}) is False
        client.invoke.assert_called_once()


_fa = sys.modules["common.fileAttribution"]
_al = sys.modules["common.assetLocations"]
REG = registration("bk1", "b1", "prefix/")
ASSET_A1 = asset_row("db1", "a1", "bk1", "prefix/a1/", "b1", indexed=False)
ASSET_A2 = asset_row("db1", "a2", "bk1", "prefix/a2/", "b1", indexed=False)
CUSTOM = asset_row("db1", "x-building-a", "bk1", "prefix/projects/building-a/", "b1")
FOREIGN = asset_row("db9", "a1", "bk9", "prefix/a1/", "other-bucket", indexed=False)


@pytest.fixture
def tables(monkeypatch):
    """Wire the dispatcher to in-memory asset and registration tables; the index is not ready
    unless a test says so."""
    _fa.clear_registration_cache()
    _al.clear_location_cache()
    monkeypatch.setattr(_fa, "index_ready", lambda name: False)

    def wire(rows, ready=False):
        monkeypatch.setattr(_fa, "index_ready", lambda name: ready)
        monkeypatch.setattr(wd, "asset_storage_table", AttributionAssetTable(rows))
        monkeypatch.setattr(wd, "s3_asset_buckets_table", AttributionBucketsTable(
            [REG, registration("bk9", "other-bucket", "/")]))
    return wire


def _binding(asset_id="a1", database_id="db1", bucket_id="bk1"):
    return {"databaseId": database_id, "assetId": asset_id, "bucketId": bucket_id,
            "relativePath": "/x", "resolvedBy": "fastPath"}


@pytest.mark.unit
class TestResolveAssetRelativeKey:
    """Before the location index is ready and without a binding: the pinned version's stamp, then the
    first folder, each bound only in the event's bucket and inside the asset's own location."""

    def _head(self, database_id="db1", asset_id="a1"):
        return {"Metadata": {"databaseid": database_id, "assetid": asset_id}}

    def test_relative_key_within_asset_location(self, tables):
        tables([ASSET_A1])
        with patch.object(wd.s3_client, "head_object", return_value=self._head()):
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/sub/model.glb")[:3] == (
                "db1", "a1", "/sub/model.glb")

    def test_object_in_a_different_bucket_than_the_asset_rejected(self, tables):
        # Same-prefix assets can live in different buckets, so the bucket must match too.
        tables([FOREIGN])
        with patch.object(wd.s3_client, "head_object", return_value=self._head("db9", "a1")):
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/model.glb") is None

    def test_an_unreadable_registration_is_raised_for_a_retry(self, tables):
        tables([ASSET_A1])
        with patch.object(wd.s3_client, "head_object", return_value=self._head()), \
             patch.object(wd.s3_asset_buckets_table, "query", side_effect=RuntimeError("throttled")):
            with pytest.raises(_fa.RegistrationUnavailableError):
                wd._resolve_asset_relative_key("b1", "prefix/a1/model.glb", "v1", _binding())

    def test_a_registration_that_does_not_exist_binds_nothing_and_logs_an_error(self, tables):
        orphan = asset_row("db1", "a1", "bk-gone", "prefix/a1/", "b1", indexed=False)
        tables([orphan])
        with patch.object(wd.s3_client, "head_object", return_value=self._head()), \
             patch.object(_fa.logger, "error") as m_error:
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/model.glb", "v1",
                                                  _binding(bucket_id="bk-gone")) is None
        assert any("does not exist" in str(c.args[0]) for c in m_error.call_args_list)

    def test_key_outside_asset_location_rejected(self, tables):
        # The metadata naming the asset is client-settable on a direct asset-bucket write, so an
        # object outside the named asset's own location must not bind to it.
        tables([ASSET_A1])
        with patch.object(wd.s3_client, "head_object", return_value=self._head()):
            assert wd._resolve_asset_relative_key("b1", "prefix/other/model.glb") is None

    def test_shared_name_prefix_is_not_containment(self, tables):
        single = asset_row("db1", "a1", "bk1", "prefix/a1", "b1", indexed=False)
        tables([single])
        with patch.object(wd.s3_client, "head_object", return_value=self._head()):
            assert wd._resolve_asset_relative_key("b1", "prefix/a10/model.glb") is None

    def test_version_id_passed_to_head_object(self, tables):
        tables([ASSET_A1])
        with patch.object(wd.s3_client, "head_object", return_value=self._head()) as m_head:
            wd._resolve_asset_relative_key("b1", "prefix/a1/model.glb", "ver-3")
        assert m_head.call_args.kwargs["VersionId"] == "ver-3"


@pytest.mark.unit
class TestBindingFromTheEvent:
    """Bucket sync forwards which asset holds the file (`vamsBinding`), so a direct write needs no stamp
    on any version: the pinned version is read once, for the write's provenance only."""

    UNSTAMPED = {"Metadata": {}}

    def test_a_direct_write_binds_from_the_event_with_one_head(self, tables):
        tables([ASSET_A1])
        with patch.object(wd.s3_client, "head_object", return_value=self.UNSTAMPED) as m_head:
            resolved = wd._resolve_asset_relative_key("b1", "prefix/a1/scan.e57", "ver-1", _binding())
        assert resolved[:3] == ("db1", "a1", "/scan.e57")
        assert m_head.called
        assert {c.kwargs.get("VersionId") for c in m_head.call_args_list} == {"ver-1"}

    def test_a_binding_into_a_custom_root_location(self, tables):
        tables([CUSTOM])
        with patch.object(wd.s3_client, "head_object", return_value=self.UNSTAMPED):
            resolved = wd._resolve_asset_relative_key(
                "b1", "prefix/projects/building-a/sub/scan.e57", "ver-1", _binding("x-building-a"))
        assert resolved[:3] == ("db1", "x-building-a", "/sub/scan.e57")

    def test_a_binding_to_a_registration_of_another_bucket_is_not_used(self, tables):
        tables([FOREIGN], ready=True)
        with patch.object(wd.s3_client, "head_object", return_value=self.UNSTAMPED):
            assert wd._resolve_asset_relative_key(
                "b1", "prefix/a1/scan.e57", "ver-1", _binding("a1", "db9", "bk9")) is None

    def test_provenance_comes_from_the_pinned_version(self, tables):
        tables([ASSET_A1])
        pinned = {"Metadata": {"vams-changesource": "workflowExecution", "vams-changeworkflowid": "wf7"}}
        with patch.object(wd.s3_client, "head_object", return_value=pinned):
            resolved = wd._resolve_asset_relative_key("b1", "prefix/a1/scan.e57", "ver-1", _binding())
        assert resolved[3:] == ("workflowExecution", "wf7")

    def test_without_a_binding_an_unstamped_normal_file_binds_by_its_first_folder(self, tables):
        # An event queued before bucket sync carried the binding, before the index is ready.
        tables([ASSET_A1])
        with patch.object(wd.s3_client, "head_object", return_value=self.UNSTAMPED) as m_head:
            resolved = wd._resolve_asset_relative_key("b1", "prefix/a1/scan.e57", "ver-1")
        assert resolved[:3] == ("db1", "a1", "/scan.e57")
        assert {c.kwargs.get("VersionId") for c in m_head.call_args_list} == {"ver-1"}

    def test_a_copy_carrying_another_assets_stamp_binds_where_it_sits(self, tables):
        # A server-side copy from a1's folder into a2's keeps a1's ids on the written version.
        tables([ASSET_A1, ASSET_A2])
        with patch.object(wd.s3_client, "head_object", return_value={"Metadata": {"databaseid": "db1",
                                                                                 "assetid": "a1"}}):
            resolved = wd._resolve_asset_relative_key("b1", "prefix/a2/scan.e57", "ver-1")
        assert resolved[:3] == ("db1", "a2", "/scan.e57")

    def test_without_a_binding_a_custom_root_file_binds_once_the_index_answers(self, tables):
        tables([CUSTOM], ready=True)
        with patch.object(wd.s3_client, "head_object", return_value=self.UNSTAMPED):
            resolved = wd._resolve_asset_relative_key("b1", "prefix/projects/building-a/x.e57", "ver-1")
        assert resolved[:3] == ("db1", "x-building-a", "/x.e57")

    def test_a_neighbour_of_a_single_file_asset_binds_nothing(self, tables):
        single = asset_row("db1", "one", "bk1", "prefix/model.glb", "b1")
        tables([single], ready=True)
        with patch.object(wd.s3_client, "head_object", return_value=self.UNSTAMPED):
            assert wd._resolve_asset_relative_key("b1", "prefix/model.glb.bak", "ver-1") is None

    def test_a_direct_write_with_a_binding_launches_once_pinned_to_the_uploaded_version(self, tables):
        tables([ASSET_A1])
        trigger = {"triggerType": "fileUpload", "workflowDatabaseId": "GLOBAL", "workflowId": "wfG",
                   "enabled": True, "triggerConfig": {"inputFileFilters": {"allow": [".e57"]},
                                                      "defaultTemplateIds": {}}}
        wd._workflow_row_cache.clear()
        event = {"Records": [{"body": json.dumps({"detail": {"Records": [
            {"s3": {"bucket": {"name": "b1"}, "object": {"key": "prefix/a1/scan.e57", "versionId": "ver-1"}},
             "vamsBinding": _binding()}]}})}]}
        [(bucket, key, version_id, binding)] = list(wd._iter_uploaded_objects(event))
        with patch.object(wd.s3_client, "head_object", return_value=self.UNSTAMPED), \
             patch.object(wd.workflow_storage_table_v2, "get_item",
                          return_value={"Item": {"systemConfig": {"inputFileArity": "one"}}}), \
             patch(f"{DMOD}._invoke_execute", return_value=True) as m_invoke:
            launched = wd._dispatch_uploaded_file(bucket, key, [trigger], version_id, binding)
        assert launched == 1
        _wfdb, _wfid, body = m_invoke.call_args.args
        assert body["inputFiles"] == [{"databaseId": "db1", "assetId": "a1",
                                       "relativeFileKey": "/scan.e57", "versionId": "ver-1"}]


def _sqs_event(*keys):
    """An SQS batch of asset.file.uploaded events, one message per key, each with a binding to a1."""
    return {"Records": [
        {"messageId": f"m{i}", "body": json.dumps({"detail": {"Records": [
            {"s3": {"bucket": {"name": "b1"}, "object": {"key": key, "versionId": f"v{i}"}},
             "vamsBinding": _binding()}]}})}
        for i, key in enumerate(keys)]}


@pytest.mark.unit
class TestRegistrationReadErrorsRetry:
    """A bucket registration that cannot be read is not a missing one: the message is retried (a batch
    item failure) and logged at WARNING, and none of its workflows launch. A registration that does
    not exist binds nothing (ERROR), and the message is not retried."""

    TRIGGER = {"triggerType": "fileUpload", "workflowDatabaseId": "GLOBAL", "workflowId": "wfG",
               "enabled": True, "triggerConfig": {"inputFileFilters": {"allow": [".e57"]},
                                                  "defaultTemplateIds": {}}}

    def _run(self, event, query_side_effect=None):
        with patch.object(wd.s3_client, "head_object", return_value={"Metadata": {}}), \
             patch(f"{DMOD}._list_fileupload_triggers", return_value=[self.TRIGGER]), \
             patch.object(wd.workflow_storage_table_v2, "get_item",
                          return_value={"Item": {"systemConfig": {"inputFileArity": "one"}}}), \
             patch(f"{DMOD}._invoke_execute", return_value=True) as m_invoke, \
             patch.object(wd.logger, "warning") as m_warning:
            if query_side_effect is not None:
                with patch.object(wd.s3_asset_buckets_table, "query", side_effect=query_side_effect):
                    response = wd.lambda_handler(event, MagicMock())
            else:
                response = wd.lambda_handler(event, MagicMock())
        return response, m_invoke, m_warning

    def test_a_throttled_registration_read_retries_its_message_and_launches_nothing(self, tables):
        tables([ASSET_A1])
        response, m_invoke, m_warning = self._run(_sqs_event("prefix/a1/scan.e57"),
                                                  query_side_effect=RuntimeError("ThrottlingException"))
        assert response["batchItemFailures"] == [{"itemIdentifier": "m0"}]
        assert not m_invoke.called
        assert any("is retried" in str(c.args[0]) for c in m_warning.call_args_list)

    def test_a_readable_registration_launches_and_reports_no_failure(self, tables):
        tables([ASSET_A1])
        response, m_invoke, _ = self._run(_sqs_event("prefix/a1/scan.e57", "prefix/a1/other.e57"))
        assert response["batchItemFailures"] == []
        assert m_invoke.called
        launched_keys = {c.args[2]["inputFiles"][0]["relativeFileKey"] for c in m_invoke.call_args_list}
        assert launched_keys == {"/scan.e57", "/other.e57"}

    def test_a_missing_registration_is_not_retried(self, tables):
        orphan = asset_row("db1", "a1", "bk-gone", "prefix/a1/", "b1", indexed=False)
        tables([orphan])
        event = _sqs_event("prefix/a1/scan.e57")
        body = json.loads(event["Records"][0]["body"])
        body["detail"]["Records"][0]["vamsBinding"]["bucketId"] = "bk-gone"
        event["Records"][0]["body"] = json.dumps(body)
        response, m_invoke, _ = self._run(event)
        assert response["batchItemFailures"] == []
        assert not m_invoke.called

    def test_a_direct_invocation_raises_for_the_caller_to_retry(self, tables):
        tables([ASSET_A1])
        event = {"detail": {"Records": [{"s3": {"bucket": {"name": "b1"},
                                                "object": {"key": "prefix/a1/scan.e57"}},
                                         "vamsBinding": _binding()}]}}
        with pytest.raises(_fa.RegistrationUnavailableError):
            self._run(event, query_side_effect=RuntimeError("timeout"))
