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


@pytest.mark.unit
class TestExecuteInvokeIsNotRetried:
    """The executeWorkflowV2 Invoke is synchronous and launches an execution per delivered request, so
    a botocore retry of a slow-but-successful call would launch duplicate runs. The client must
    deliver exactly one attempt and wait out the callee's full runtime."""

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


@pytest.mark.unit
class TestUnstampedPinnedVersion:
    """A file written straight to the asset bucket arrives without `databaseid` / `assetid`. Bucket
    sync stamps them by copying the object onto itself, which creates a NEWER version, and then
    forwards the event for the ORIGINAL version — so the version the dispatcher pins carries no ids,
    while the current version does."""

    PINNED_UNSTAMPED = {"Metadata": {}}
    CURRENT_STAMPED = {"Metadata": {"databaseid": "db1", "assetid": "a1", "vams-changesource": "direct"}}
    ASSET = {"Item": {"assetLocation": {"Key": "prefix/a1/"}}}

    def test_binds_through_the_current_version(self):
        with patch.object(wd.s3_client, "head_object",
                          side_effect=[self.PINNED_UNSTAMPED, self.CURRENT_STAMPED]) as m_head, \
             patch.object(wd.asset_storage_table, "get_item", return_value=self.ASSET):
            resolved = wd._resolve_asset_relative_key("b1", "prefix/a1/scan.e57", "ver-1")
        assert resolved is not None
        assert resolved[:3] == ("db1", "a1", "/scan.e57")
        # The pinned HEAD first, then one unpinned HEAD of the current version.
        assert [c.kwargs.get("VersionId") for c in m_head.call_args_list] == ["ver-1", None]

    def test_provenance_comes_from_the_pinned_version(self):
        # The current version can already be a later write; who wrote the PINNED version is what the
        # chaining guard judges, so a later workflow write must not block this direct write's trigger.
        current = {"Metadata": {"databaseid": "db1", "assetid": "a1",
                                "vams-changesource": "workflowExecution",
                                "vams-changeworkflowid": "wfLater"}}
        with patch.object(wd.s3_client, "head_object", side_effect=[self.PINNED_UNSTAMPED, current]), \
             patch.object(wd.asset_storage_table, "get_item", return_value=self.ASSET):
            resolved = wd._resolve_asset_relative_key("b1", "prefix/a1/scan.e57", "ver-1")
        assert resolved[3:] == ("", "")

    def test_stamped_pinned_version_needs_one_head(self):
        with patch.object(wd.s3_client, "head_object",
                          return_value={"Metadata": {"databaseid": "db1", "assetid": "a1"}}) as m_head, \
             patch.object(wd.asset_storage_table, "get_item", return_value=self.ASSET):
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/scan.e57", "ver-1") is not None
        assert m_head.call_count == 1

    def test_unversioned_unstamped_object_is_skipped_after_one_head(self):
        # Without a pinned version the first HEAD already read the current object; there is nothing
        # newer to consult.
        with patch.object(wd.s3_client, "head_object", return_value=self.PINNED_UNSTAMPED) as m_head:
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/scan.e57") is None
        assert m_head.call_count == 1

    def test_skipped_when_the_current_version_is_unstamped_too(self):
        # Bucket sync forwards a record whose stamp failed; neither version names an asset.
        with patch.object(wd.s3_client, "head_object",
                          side_effect=[self.PINNED_UNSTAMPED, self.PINNED_UNSTAMPED]) as m_head:
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/scan.e57", "ver-1") is None
        # Skipped after reading the current version, not at the pinned one.
        assert m_head.call_count == 2

    def test_skipped_when_the_current_version_cannot_be_read(self):
        # A delete marker as the current version answers the unpinned HEAD with 404.
        with patch.object(wd.s3_client, "head_object",
                          side_effect=[self.PINNED_UNSTAMPED, RuntimeError("404 Not Found")]) as m_head:
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/scan.e57", "ver-1") is None
        assert m_head.call_count == 2

    def test_fallback_binding_still_requires_containment(self):
        # The ids on the current version are client-settable too; the object must still sit inside
        # the named asset's own location.
        current = {"Metadata": {"databaseid": "db1", "assetid": "a2"}}
        with patch.object(wd.s3_client, "head_object", side_effect=[self.PINNED_UNSTAMPED, current]), \
             patch.object(wd.asset_storage_table, "get_item",
                          return_value={"Item": {"assetLocation": {"Key": "prefix/a2/"}}}) as m_get:
            assert wd._resolve_asset_relative_key("b1", "prefix/a1/scan.e57", "ver-1") is None
        # Rejected by the containment check on the ids read from the current version.
        m_get.assert_called_once_with(Key={"databaseId": "db1", "assetId": "a2"})

    def test_rebinds_through_the_current_version_when_the_pinned_ids_do_not_bind(self):
        # A server-side copy from asset a1's folder into a2's carries a1's ids on the written
        # version; bucket sync re-stamps the newer version with a2's, which is where the object sits.
        pinned = {"Metadata": {"databaseid": "db1", "assetid": "a1"}}
        current = {"Metadata": {"databaseid": "db1", "assetid": "a2", "vams-changesource": "direct"}}
        locations = {"a1": "prefix/a1/", "a2": "prefix/a2/"}

        def _get_item(Key):
            return {"Item": {"assetLocation": {"Key": locations[Key["assetId"]]}}}

        with patch.object(wd.s3_client, "head_object", side_effect=[pinned, current]) as m_head, \
             patch.object(wd.asset_storage_table, "get_item", side_effect=_get_item) as m_get:
            resolved = wd._resolve_asset_relative_key("b1", "prefix/a2/scan.e57", "ver-1")
        assert resolved is not None
        assert resolved[:3] == ("db1", "a2", "/scan.e57")
        # The provenance still comes from the pinned version, which carries none.
        assert resolved[3:] == ("", "")
        assert [c.kwargs.get("VersionId") for c in m_head.call_args_list] == ["ver-1", None]
        assert [c.kwargs["Key"]["assetId"] for c in m_get.call_args_list] == ["a1", "a2"]

    def test_skipped_when_neither_version_binds(self):
        # Control for the rebind above: both versions name a1 and the object sits outside a1's
        # location, so the retry reads the current version once and still skips.
        stamped = {"Metadata": {"databaseid": "db1", "assetid": "a1"}}
        with patch.object(wd.s3_client, "head_object", side_effect=[stamped, stamped]) as m_head, \
             patch.object(wd.asset_storage_table, "get_item", return_value=self.ASSET):
            assert wd._resolve_asset_relative_key("b1", "prefix/other/scan.e57", "ver-1") is None
        assert m_head.call_count == 2

    def test_binds_a_direct_write_under_a_bucket_existing_key_folder(self):
        # An asset created with bucketExistingKey keeps its files under the operator's folder, which
        # is not named after the asset ID. The ids read from the current version bind the write to
        # that asset, and the relative key is taken from the asset's own location.
        current = {"Metadata": {"databaseid": "db1", "assetid": "x-building-a"}}
        asset = {"Item": {"assetLocation": {"Key": "prefix/projects/building-a/"}}}
        with patch.object(wd.s3_client, "head_object",
                          side_effect=[self.PINNED_UNSTAMPED, current]) as m_head, \
             patch.object(wd.asset_storage_table, "get_item", return_value=asset) as m_get:
            resolved = wd._resolve_asset_relative_key(
                "b1", "prefix/projects/building-a/sub/scan.e57", "ver-1")
        assert resolved is not None
        assert resolved[:3] == ("db1", "x-building-a", "/sub/scan.e57")
        m_get.assert_called_once_with(Key={"databaseId": "db1", "assetId": "x-building-a"})
        assert [c.kwargs.get("VersionId") for c in m_head.call_args_list] == ["ver-1", None]

    def test_direct_write_launches_once_pinned_to_the_uploaded_version(self):
        trigger = {"triggerType": "fileUpload", "workflowDatabaseId": "GLOBAL", "workflowId": "wfG",
                   "enabled": True, "triggerConfig": {"inputFileFilters": {"allow": [".e57"]},
                                                      "defaultTemplateIds": {}}}
        wd._workflow_row_cache.clear()
        with patch.object(wd.s3_client, "head_object",
                          side_effect=[self.PINNED_UNSTAMPED, self.CURRENT_STAMPED]), \
             patch.object(wd.asset_storage_table, "get_item", return_value=self.ASSET), \
             patch.object(wd.workflow_storage_table_v2, "get_item",
                          return_value={"Item": {"systemConfig": {"inputFileArity": "one"}}}), \
             patch(f"{DMOD}._invoke_execute", return_value=True) as m_invoke:
            launched = wd._dispatch_uploaded_file("b1", "prefix/a1/scan.e57", [trigger], "ver-1")
        assert launched == 1
        _wfdb, _wfid, body = m_invoke.call_args.args
        assert body["inputFiles"] == [{"databaseId": "db1", "assetId": "a1",
                                       "relativeFileKey": "/scan.e57", "versionId": "ver-1"}]
