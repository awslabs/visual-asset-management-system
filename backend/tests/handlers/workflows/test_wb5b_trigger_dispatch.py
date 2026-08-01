# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for WB5b file-upload trigger delivery: the pure matcher (common/workflows/
triggerMatching.py) and the dispatcher handler (handlers/workflows/sfn/workflowTriggerDispatch.py)."""

import json
import os
import sys
import types

import pytest
from unittest.mock import MagicMock, patch

from backend.backend.common.workflows import triggerMatching as tm


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

DMOD = "backend.backend.handlers.workflows.sfn.workflowTriggerDispatch"


@pytest.mark.unit
class TestDispatcher:
    def test_iter_uploaded_objects_eventbridge_detail_in_sqs(self):
        event = {"Records": [{"body": json.dumps({
            "detail": {"bucket": {"name": "b1"}, "object": {"key": "a1/x.glb"}}})}]}
        assert list(wd._iter_uploaded_objects(event)) == [("b1", "a1/x.glb", "")]

    def test_iter_uploaded_objects_s3_records_in_sqs(self):
        event = {"Records": [{"body": json.dumps({
            "Records": [{"s3": {"bucket": {"name": "b2"}, "object": {"key": "a2/y.e57"}}}]})}]}
        assert list(wd._iter_uploaded_objects(event)) == [("b2", "a2/y.e57", "")]

    def test_iter_uploaded_objects_clean_detail_records(self):
        # The producer publishes a clean EventBridge detail carrying flat S3 Records.
        event = {"Records": [{"body": json.dumps({
            "detail": {"Records": [{"s3": {"bucket": {"name": "b3"}, "object": {"key": "a3/z.glb"}}}],
                       "ASSET_BUCKET_NAME": "b3"}})}]}
        assert list(wd._iter_uploaded_objects(event)) == [("b3", "a3/z.glb", "")]

    def test_iter_uploaded_objects_sns_notification_envelope(self):
        # Defensive: an SNS Notification envelope wrapping the S3 records is also unwrapped.
        inner = json.dumps({"Records": [{"s3": {"bucket": {"name": "b4"}, "object": {"key": "a4/w.obj"}}}]})
        event = {"Records": [{"body": json.dumps({"Type": "Notification", "Message": inner})}]}
        assert list(wd._iter_uploaded_objects(event)) == [("b4", "a4/w.obj", "")]

    def test_iter_uploaded_objects_carries_object_version_id(self):
        # S3 notification records carry the uploaded object's versionId; it pins the run to the
        # version that was uploaded rather than whatever is latest when the trigger fires.
        event = {"Records": [{"body": json.dumps({"detail": {"Records": [
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/1.glb", "versionId": "v1"}}},
            {"s3": {"bucket": {"name": "b"}, "object": {"key": "a/2.glb", "versionId": "null"}}}]}})}]}
        assert list(wd._iter_uploaded_objects(event)) == [
            ("b", "a/1.glb", "v1"), ("b", "a/2.glb", "")]

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
             patch(f"{DMOD}._dispatch_uploaded_file", side_effect=[RuntimeError("boom"), 1]):
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


@pytest.mark.unit
class TestResolveAssetRelativeKey:
    def _head(self, database_id="db1", asset_id="a1"):
        return {"Metadata": {"databaseid": database_id, "assetid": asset_id}}

    def test_relative_key_within_asset_location(self):
        with patch.object(wd.s3_client, "head_object", return_value=self._head()), \
             patch.object(wd.asset_storage_table, "get_item",
                          return_value={"Item": {"assetLocation": {"Key": "prefix/a1/"}}}):
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/sub/model.glb")[:3] == (
                "db1", "a1", "/sub/model.glb")

    def test_object_in_a_different_bucket_than_the_asset_rejected(self):
        # Same-prefix assets can live in different buckets, so the bucket must match too.
        with patch.object(wd.s3_client, "head_object", return_value=self._head()), \
             patch.object(wd.asset_storage_table, "get_item",
                          return_value={"Item": {"bucketId": "bk1",
                                                 "assetLocation": {"Key": "prefix/a1/"}}}), \
             patch.object(wd, "_asset_bucket_name", return_value="other-bucket"):
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/model.glb") is None

    def test_matching_bucket_resolves(self):
        with patch.object(wd.s3_client, "head_object", return_value=self._head()), \
             patch.object(wd.asset_storage_table, "get_item",
                          return_value={"Item": {"bucketId": "bk1",
                                                 "assetLocation": {"Key": "prefix/a1/"}}}), \
             patch.object(wd, "_asset_bucket_name", return_value="b1"):
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/model.glb")[:3] == (
                "db1", "a1", "/model.glb")

    def test_unresolvable_bucket_row_leaves_the_key_check_as_the_gate(self):
        with patch.object(wd.s3_client, "head_object", return_value=self._head()), \
             patch.object(wd.asset_storage_table, "get_item",
                          return_value={"Item": {"bucketId": "bk1",
                                                 "assetLocation": {"Key": "prefix/a1/"}}}), \
             patch.object(wd.s3_asset_buckets_table, "query", side_effect=RuntimeError("throttled")):
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/model.glb")[:3] == (
                "db1", "a1", "/model.glb")

    def test_key_outside_asset_location_rejected(self):
        # The metadata naming the asset is client-settable on a direct asset-bucket write, so an
        # object outside the named asset's own location must not bind to it.
        with patch.object(wd.s3_client, "head_object", return_value=self._head()), \
             patch.object(wd.asset_storage_table, "get_item",
                          return_value={"Item": {"assetLocation": {"Key": "prefix/a1/"}}}):
            assert wd._resolve_asset_relative_key("b1", "prefix/other/model.glb") is None

    def test_shared_name_prefix_is_not_containment(self):
        with patch.object(wd.s3_client, "head_object", return_value=self._head()), \
             patch.object(wd.asset_storage_table, "get_item",
                          return_value={"Item": {"assetLocation": {"Key": "prefix/a1"}}}):
            assert wd._resolve_asset_relative_key("b1", "prefix/a10/model.glb") is None

    def test_missing_asset_location_rejected(self):
        with patch.object(wd.s3_client, "head_object", return_value=self._head()), \
             patch.object(wd.asset_storage_table, "get_item", return_value={"Item": {}}):
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/model.glb") is None

    def test_version_id_passed_to_head_object(self):
        with patch.object(wd.s3_client, "head_object", return_value=self._head()) as m_head, \
             patch.object(wd.asset_storage_table, "get_item",
                          return_value={"Item": {"assetLocation": {"Key": "prefix/a1/"}}}):
            wd._resolve_asset_relative_key("b1", "prefix/a1/model.glb", "ver-3")
        assert m_head.call_args.kwargs["VersionId"] == "ver-3"
