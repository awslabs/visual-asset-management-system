# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Batch-write accounting and CloudFormation reporting in the reindexer.

Guards S2-BACKEND-033. `batch_write_item` does not raise when DynamoDB throttles
-- it returns the writes it did NOT accept under `UnprocessedItems`. Neither the
create nor the delete loop inspected that response, so throttled 'touch' writes
were counted as successes and those assets/files were never reindexed. With
ClearIndexes the indexes had already been emptied, and the handler reported
SUCCESS to CloudFormation regardless, so search was silently incomplete with no
signal.

Three behaviours are asserted separately:

* the PARTIAL response -- rejected writes are retried and, once accepted, count
  as successes;
* the EXHAUSTED bound -- the retry is bounded (an unbounded retry in a Lambda
  trades data loss for a stack-hanging timeout), and what is left over SURFACES:
  counted as failed, recorded in `errors`, and turned into a FAILED
  CloudFormation response rather than swallowed;
* the create/delete pairing -- the touch is a create followed by a delete, and it
  is the DELETE that emits the stream event the indexer consumes. An asset whose
  create never landed must not be deleted (a delete of a non-existent item emits
  nothing) and must not be counted as a success.

Guards S2-BACKEND-174: the custom-resource event carries `ResponseURL`, a
presigned S3 PUT that lets its holder send an arbitrary SUCCESS/FAILED for the
in-flight stack operation. The handler logged the whole event. Redaction is
key-driven and would not have masked an interpolated URL, so the fix is to log
identifying fields only.
"""

import importlib.util
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_CR_REINDEXER_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..",
    "backend", "handlers", "indexing", "crReindexer.py",
)

RESPONSE_URL = ("https://cloudformation-custom-resource-response-useast1.s3.amazonaws.com/"
                "arn%3Aaws%3Acloudformation%3A.../presigned?X-Amz-Signature=deadbeef")

METADATA_TABLE = "test-file-metadata-table"


@pytest.fixture
def crReindexer():
    """Load the real crReindexer module by file path with boto3 stubbed."""
    with patch("boto3.client", return_value=MagicMock()), \
            patch("boto3.resource", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location(
            "crReindexer_under_test", os.path.abspath(_CR_REINDEXER_PATH))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _key_of(request):
    for verb, holder in (("PutRequest", "Item"), ("DeleteRequest", "Key")):
        body = request.get(verb)
        if body:
            return verb, body[holder]["databaseId:assetId:filePath"]["S"]
    return None, None


class _BatchWriter:
    """Scripted `batch_write_item`.

    `reject(verb, key, times_already_rejected)` decides whether this write comes
    back under `UnprocessedItems` -- which is how DynamoDB reports a throttle: a
    normal 200 response with the rejected writes echoed back.
    """

    # A retry loop that lost its bound would spin forever against a stub that
    # always rejects. The cap makes that a loud failure instead of a hung suite.
    MAX_CALLS = 25

    def __init__(self, reject):
        self._reject = reject
        self._rejected_counts = {}
        self.accepted = []
        self.requested = []
        self.calls = 0

    def batch_write_item(self, RequestItems=None, **kwargs):
        self.calls += 1
        assert self.calls <= self.MAX_CALLS, (
            f"batch_write_item called {self.calls} times: the retry is not bounded")
        (table, requests), = RequestItems.items()
        unprocessed = []
        for request in requests:
            verb, key = _key_of(request)
            self.requested.append((verb, key))
            seen = self._rejected_counts.get((verb, key), 0)
            if self._reject(verb, key, seen):
                self._rejected_counts[(verb, key)] = seen + 1
                unprocessed.append(request)
            else:
                self.accepted.append((verb, key))
        return {"UnprocessedItems": {table: unprocessed} if unprocessed else {}}


def _context(remaining_ms=900000):
    """A Lambda context whose fields are real values.

    `log_stream_name` reaches `json.dumps` in the CloudFormation response body and
    `get_remaining_time_in_millis` is compared against the time reserve, so a bare
    MagicMock would exercise the error paths instead of the ones under test.
    """
    context = MagicMock()
    context.log_stream_name = "2026/08/25/[$LATEST]abc123"
    context.get_remaining_time_in_millis.return_value = remaining_ms
    return context


def _utility(module, **kwargs):
    return module.ReindexUtility(
        asset_table_name="test-asset-table",
        s3_buckets_table_name="test-buckets-table",
        assets_metadata_table_name=METADATA_TABLE,
        **kwargs,
    )


ASSETS = [{"databaseId": "db1", "assetId": "a1"}, {"databaseId": "db1", "assetId": "a2"}]
A1 = "db1:a1:/"
A2 = "db1:a2:/"


@pytest.mark.unit
class TestBatchWriteWithRetry:
    def test_rejected_write_is_retried_and_counted_once(self, crReindexer):
        m = crReindexer
        writer = _BatchWriter(lambda verb, key, seen: key == A2 and seen < 1)
        with patch.object(m, "dynamodb_client", writer), patch("time.sleep"):
            written, pending = m.batch_write_with_retry(
                METADATA_TABLE,
                [{"PutRequest": {"Item": {"databaseId:assetId:filePath": {"S": key}}}}
                 for key in (A1, A2)])
        assert pending == []
        assert written == 2
        assert sorted(k for _, k in writer.accepted) == [A1, A2]

    def test_bound_is_finite_and_leftovers_are_returned(self, crReindexer):
        """An unbounded retry would trade silent data loss for a Lambda timeout,
        which hangs the stack. The walk stops and hands back what it could not
        write, so the caller can report it."""
        m = crReindexer
        writer = _BatchWriter(lambda verb, key, seen: key == A2)
        with patch.object(m, "dynamodb_client", writer), patch("time.sleep"):
            written, pending = m.batch_write_with_retry(
                METADATA_TABLE,
                [{"PutRequest": {"Item": {"databaseId:assetId:filePath": {"S": key}}}}
                 for key in (A1, A2)])
        assert written == 1
        assert [_key_of(r)[1] for r in pending] == [A2]
        assert m.BATCH_WRITE_MAX_ATTEMPTS >= 2, "a bound of 1 is not a retry"

    def test_empty_batch_issues_no_write(self, crReindexer):
        """Positive control on the stub: with nothing to write nothing is sent,
        so an empty tail batch cannot raise a validation error."""
        m = crReindexer
        writer = _BatchWriter(lambda verb, key, seen: False)
        with patch.object(m, "dynamodb_client", writer):
            assert m.batch_write_with_retry(METADATA_TABLE, []) == (0, [])
        assert writer.requested == []


@pytest.mark.unit
class TestTouchAccounting:
    def test_clean_run_counts_every_asset_once(self, crReindexer):
        """Positive control: with no rejections every asset is a success."""
        m = crReindexer
        writer = _BatchWriter(lambda verb, key, seen: False)
        with patch.object(m, "dynamodb_client", writer), patch("time.sleep"):
            results = _utility(m)._update_assets_in_metadata_table(ASSETS, "ts")
        assert (results["success"], results["failed"]) == (2, 0)
        assert results["errors"] == []

    def test_unwritten_create_is_not_deleted_and_not_a_success(self, crReindexer):
        """The reindex is driven by the DELETE's stream event. Deleting a record
        that was never created emits nothing, so the asset was not reindexed and
        must not be counted as written."""
        m = crReindexer
        writer = _BatchWriter(lambda verb, key, seen: verb == "PutRequest" and key == A2)
        with patch.object(m, "dynamodb_client", writer), patch("time.sleep"):
            results = _utility(m)._update_assets_in_metadata_table(ASSETS, "ts")

        assert results["success"] == 1
        assert results["failed"] == 1
        assert ("DeleteRequest", A2) not in writer.requested, \
            "a record that was never created must not be deleted"
        assert ("DeleteRequest", A1) in writer.accepted
        # The loss is recorded against the key, not just counted.
        assert any(A2 == (error.get("details") or "") for error in results["errors"]), \
            f"the unwritten key is not identified in errors: {results['errors']}"

    def test_unwritten_delete_is_counted_as_failed(self, crReindexer):
        """The pre-fix code did `results['success'] += len(batch)` for the delete
        loop, counting rejected deletes as successes."""
        m = crReindexer
        writer = _BatchWriter(lambda verb, key, seen: verb == "DeleteRequest" and key == A2)
        with patch.object(m, "dynamodb_client", writer), patch("time.sleep"):
            results = _utility(m)._update_assets_in_metadata_table(ASSETS, "ts")
        assert results["success"] == 1
        assert results["failed"] == 1

    def test_raised_exception_still_skips_the_delete(self, crReindexer):
        """A raised error (not a throttle) must behave the same way: the asset is
        failed and its delete is not issued."""
        m = crReindexer
        client = MagicMock()
        client.batch_write_item.side_effect = RuntimeError("service unavailable")
        with patch.object(m, "dynamodb_client", client), patch("time.sleep"):
            results = _utility(m)._update_assets_in_metadata_table(ASSETS, "ts")
        assert results["success"] == 0
        assert results["failed"] == len(ASSETS)


FILES = [
    {"databaseId": "db1", "original_asset_id": "a1", "relative_path": "/one.glb",
     "assetId": "a1", "file_path": "/one.glb"},
    {"databaseId": "db1", "original_asset_id": "a2", "relative_path": "/two.glb",
     "assetId": "a2", "file_path": "/two.glb"},
]


@pytest.mark.unit
class TestFileTouchAccounting:
    """The file loop is a second site of the same defect, so it is asserted
    separately rather than assumed to share the asset loop's behaviour."""

    def test_unwritten_create_is_not_deleted_and_not_a_success(self, crReindexer):
        m = crReindexer
        target = "db1:a2:/two.glb"
        writer = _BatchWriter(lambda verb, key, seen: verb == "PutRequest" and key == target)
        with patch.object(m, "dynamodb_client", writer), patch("time.sleep"):
            results = _utility(m)._update_files_in_metadata_table(FILES, "ts")
        assert results["success"] == 1
        assert results["failed"] == 1
        assert ("DeleteRequest", target) not in writer.requested

    def test_clean_run_counts_every_file_once(self, crReindexer):
        m = crReindexer
        writer = _BatchWriter(lambda verb, key, seen: False)
        with patch.object(m, "dynamodb_client", writer), patch("time.sleep"):
            results = _utility(m)._update_files_in_metadata_table(FILES, "ts")
        assert (results["success"], results["failed"]) == (2, 0)


@pytest.mark.unit
class TestFailureReasons:
    def test_clean_results_report_no_reason(self, crReindexer):
        """Positive control: a clean run must still be able to report SUCCESS."""
        m = crReindexer
        assert m.reindex_failure_reasons({
            "clear_indexes": {"asset_index": {"success": True},
                              "file_index": {"success": True}},
            "assets": {"failed_count": 0, "errors": []},
            "files": {"failed_count": 0, "errors": []},
        }) == []

    @pytest.mark.parametrize("results,expected_fragment", [
        ({"assets": {"failed_count": 3, "errors": []}}, "3 assets"),
        ({"files": {"failed_count": 7, "errors": []}}, "7 files"),
        ({"assets": {"failed_count": 0, "errors": [{"error": "x"}]}}, "assets"),
        ({"clear_indexes_error": "connection refused"}, "connection refused"),
        ({"clear_indexes": {"asset_index": {"success": False},
                            "file_index": {"success": True}}}, "asset_index"),
    ])
    def test_each_kind_of_incompleteness_is_reported(self, crReindexer, results,
                                                     expected_fragment):
        reasons = crReindexer.reindex_failure_reasons(results)
        assert reasons, f"no reason reported for {results}"
        assert any(expected_fragment in reason for reason in reasons), reasons


def _cfn_event(operation="assets", clear_indexes="false"):
    return {
        "RequestType": "Update",
        "ResponseURL": RESPONSE_URL,
        "StackId": "arn:aws:cloudformation:us-east-1:1:stack/vams/abc",
        "RequestId": "req-1",
        "LogicalResourceId": "ReindexResource",
        "ResourceProperties": {"Operation": operation, "ClearIndexes": clear_indexes},
    }


def _clean_asset_results():
    return {"success_count": 2, "failed_count": 0, "total_count": 2, "errors": []}


@pytest.mark.unit
class TestCloudFormationResponse:
    """The custom resource runs behind the CDK provider framework. Its onEvent
    wrapper submits SUCCESS for any invocation that RETURNS and FAILED only when
    the handler raises (aws-cdk-lib provider-framework `safeHandler`), and it
    passes the real ResponseURL through to this handler. So a FAILED that the
    handler only PUTs itself races the framework's SUCCESS on the same presigned
    URL -- the raise is what makes the failure deterministic."""

    def test_partial_reindex_reports_failed_and_raises(self, crReindexer):
        m = crReindexer
        results = {"success_count": 1, "failed_count": 1, "total_count": 2,
                   "errors": [{"error": "Batch create left unprocessed items"}]}
        with patch.object(m.ReindexUtility, "reindex_assets", return_value=results), \
                patch.object(m, "send_cfn_response") as responder:
            with pytest.raises(m.ReindexIncompleteError):
                m.lambda_handler(_cfn_event(), _context())
        assert responder.call_args.args[2] == "FAILED"

    def test_reindex_crash_reports_failed_and_raises(self, crReindexer):
        m = crReindexer
        with patch.object(m.ReindexUtility, "reindex_assets",
                          side_effect=RuntimeError("opensearch unreachable")), \
                patch.object(m, "send_cfn_response") as responder:
            with pytest.raises(m.ReindexIncompleteError):
                m.lambda_handler(_cfn_event(), _context())
        assert responder.call_args.args[2] == "FAILED"

    def test_missing_table_configuration_raises_for_a_stack_event(self, crReindexer):
        m = crReindexer
        with patch.object(m, "ASSET_STORAGE_TABLE_NAME", ""), \
                patch.object(m, "send_cfn_response") as responder:
            with pytest.raises(m.ReindexIncompleteError):
                m.lambda_handler(_cfn_event(), _context())
        assert responder.call_args.args[2] == "FAILED"

    def test_missing_table_configuration_returns_for_a_direct_invocation(self, crReindexer):
        """Positive control: a direct invocation is not a stack operation, so it
        answers with a status code rather than raising."""
        m = crReindexer
        with patch.object(m, "ASSET_STORAGE_TABLE_NAME", ""):
            response = m.lambda_handler({"operation": "assets"}, _context())
        assert response["statusCode"] == 500

    def test_clean_reindex_reports_success(self, crReindexer):
        """Positive control for the test above."""
        m = crReindexer
        with patch.object(m.ReindexUtility, "reindex_assets",
                          return_value=_clean_asset_results()), \
                patch.object(m, "send_cfn_response") as responder:
            response = m.lambda_handler(_cfn_event(), _context())
        assert responder.call_args.args[2] == "SUCCESS"
        assert response["statusCode"] == 200

    def test_delete_request_always_succeeds(self, crReindexer):
        """A FAILED response to a Delete would block the stack from deleting."""
        m = crReindexer
        event = _cfn_event()
        event["RequestType"] = "Delete"
        with patch.object(m, "send_cfn_response") as responder:
            m.lambda_handler(event, _context())
        assert responder.call_args.args[2] == "SUCCESS"

    def test_direct_invocation_surfaces_failures_in_the_body(self, crReindexer):
        m = crReindexer
        results = {"success_count": 0, "failed_count": 4, "total_count": 4, "errors": []}
        with patch.object(m.ReindexUtility, "reindex_assets", return_value=results):
            response = m.lambda_handler({"operation": "assets"}, _context())
        body = json.loads(response["body"])
        assert body["failures"], "a direct invocation reported no failures for a partial run"


@pytest.mark.unit
class TestBucketScanResolvesFromTheKey:
    """S2-BACKEND-033's other half: the scan issued one HeadObject per S3 object
    purely to read the assetid/databaseid metadata, which at realistic object
    counts exhausts the Lambda's 15-minute ceiling before the run finishes -- and
    a run killed mid-way sends no CloudFormation response at all.

    The assetId now comes from the key (the first segment beneath the base prefix)
    and the databaseId from a cached, bucket-scoped GSI lookup. HeadObject remains
    as the fallback for a key that does not resolve to an active asset."""

    def _scan(self, m, keys, resolver):
        s3 = MagicMock()
        s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": key} for key in keys]}]
        s3.head_object.return_value = {
            "Metadata": {"assetid": "zz", "databaseid": "dbZ"}}
        utility = _utility(m)
        with patch.object(m, "s3_client", s3), \
                patch.object(m.ReindexUtility, "_resolve_database_id",
                             side_effect=lambda bucket_id, asset_id: resolver(asset_id)):
            results = utility._process_bucket("bucket", "", dry_run=True,
                                              bucket_id="b1")
        return results, s3

    def test_resolvable_keys_need_no_head_object(self, crReindexer):
        m = crReindexer
        results, s3 = self._scan(m, ["a1/model.glb", "a1/scan.e57"],
                                 lambda asset_id: "db1")
        assert results["total"] == 2
        assert s3.head_object.call_args_list == [], \
            "a HeadObject per object is what exhausts the Lambda's runtime"

    def test_unresolvable_key_still_falls_back_to_object_metadata(self, crReindexer):
        """Positive control: the fallback is intact, so removing the per-object
        HeadObject did not drop the objects it used to resolve (an archived
        asset's record has moved to the {databaseId}#deleted partition)."""
        m = crReindexer
        results, s3 = self._scan(m, ["a1/model.glb", "zz/orphan.bin"],
                                 lambda asset_id: "db1" if asset_id == "a1" else None)
        assert results["total"] == 2
        headed = [call.kwargs["Key"] for call in s3.head_object.call_args_list]
        assert headed == ["zz/orphan.bin"]


@pytest.mark.unit
class TestTimeGuard:
    """The reindex walks every asset and every S3 object inside a function already
    at the 15-minute maximum. Being killed mid-run sends no CloudFormation
    response at all, so the stack waits on its own timeout with the indexes
    possibly already cleared. Stopping early converts that into a reported
    failure."""

    def test_run_stops_and_reports_when_time_runs_out(self, crReindexer):
        m = crReindexer
        utility = _utility(m, time_remaining_ms=lambda: 1000, min_remaining_ms=60000)
        with patch.object(m.ReindexUtility, "_scan_asset_table",
                          return_value=[{"databaseId": "db1", "assetId": f"a{i}"}
                                        for i in range(5)]):
            results = utility.reindex_assets(dry_run=True)
        assert results["failed_count"] == 5
        assert any(error.get("type") == "timeout_guard" for error in results["errors"])
        # An aborted run must not be reportable as SUCCESS.
        assert m.reindex_failure_reasons({"assets": results})

    def test_ample_time_processes_everything(self, crReindexer):
        """Positive control: the guard does not fire on a healthy run."""
        m = crReindexer
        utility = _utility(m, time_remaining_ms=lambda: 900000, min_remaining_ms=60000)
        with patch.object(m.ReindexUtility, "_scan_asset_table",
                          return_value=[{"databaseId": "db1", "assetId": "a1"}]):
            results = utility.reindex_assets(dry_run=True)
        assert (results["failed_count"], results["success_count"]) == (0, 1)
        assert m.reindex_failure_reasons({"assets": results}) == []

    def test_no_clock_means_no_guard(self, crReindexer):
        """A caller with no context (a script) keeps the previous behaviour."""
        m = crReindexer
        assert _utility(m).out_of_time() is False


@pytest.mark.unit
class TestFileReindexAbortReporting:
    """The file half of the timeout path, asserted separately from the asset half.

    This is where the finding said the timeout actually bites -- thousands of
    files per asset across every bucket. The reporting is also wired differently
    here: an aborted bucket adds nothing to `failed`, so `failed_count` stays 0
    and the abort reaches the CloudFormation response ONLY through the errors
    list. That coupling is what makes an aborted file reindex report FAILED
    rather than SUCCESS, so it is asserted directly instead of being inferred
    from the asset-side guard.
    """

    BUCKETS = [
        {"bucketName": "bucket-one", "baseAssetsPrefix": "", "bucketId": "b1"},
        {"bucketName": "bucket-two", "baseAssetsPrefix": "", "bucketId": "b2"},
    ]

    def _run(self, m, remaining_ms):
        s3 = MagicMock()
        # A list, not a generator: both buckets must be able to walk it.
        s3.get_paginator.return_value.paginate.return_value = [
            {"Contents": [{"Key": "a1/model.glb"}]}]
        utility = _utility(m, time_remaining_ms=lambda: remaining_ms,
                          min_remaining_ms=60000)
        with patch.object(m, "s3_client", s3), \
                patch.object(m.ReindexUtility, "_scan_s3_buckets_table",
                             return_value=self.BUCKETS), \
                patch.object(m.ReindexUtility, "_resolve_database_id",
                             return_value="db1"):
            return utility.reindex_files(dry_run=True)

    def test_abort_stops_the_walk_and_cannot_report_success(self, crReindexer):
        m = crReindexer
        results = self._run(m, remaining_ms=1000)

        assert results.get("aborted") is True
        assert results["buckets_processed"] == 1, \
            "a bucket that could not finish must not be followed by another"
        assert any(error.get("type") == "timeout_guard"
                   for error in results["errors"])
        # An aborted bucket contributes no failures, so the count alone cannot
        # carry the signal -- pinned so a change here has to stay deliberate.
        assert results["failed_count"] == 0
        assert m.reindex_failure_reasons({"files": results}), \
            "an aborted file reindex would be reported to CloudFormation as SUCCESS"

    def test_ample_time_walks_every_bucket_and_reports_success(self, crReindexer):
        """Positive control: the guard does not fire on a healthy run, and a
        complete file reindex is still reportable as SUCCESS."""
        m = crReindexer
        results = self._run(m, remaining_ms=900000)

        assert results.get("aborted") is not True
        assert results["buckets_processed"] == 2
        assert results["total_count"] == 2
        assert m.reindex_failure_reasons({"files": results}) == []


@pytest.mark.unit
class TestResponseUrlIsNotLogged:
    def test_presigned_response_url_never_reaches_the_logger(self, crReindexer):
        m = crReindexer
        recorder = MagicMock()
        with patch.object(m, "logger", recorder), \
                patch.object(m, "http", MagicMock()), \
                patch.object(m.ReindexUtility, "reindex_assets",
                             return_value=_clean_asset_results()):
            m.lambda_handler(_cfn_event(), _context())

        rendered = []
        for call in recorder.method_calls:
            rendered.append(repr(call))
        rendered = "\n".join(rendered)
        assert "X-Amz-Signature" not in rendered
        assert RESPONSE_URL not in rendered
        # Positive control: the handler DOES log the event's identifying fields,
        # so the assertion above is not passing because nothing was logged.
        assert "ReindexResource" in rendered
        assert "Update" in rendered

    def test_module_uses_safelogger(self, crReindexer):
        """Rule 5: the stdlib logger bypasses mask_sensitive_data entirely."""
        m = crReindexer
        assert "logging" not in sys.modules.get(
            "crReindexer_under_test", m).__dict__, \
            "the raw logging module is imported again"
        assert m.logger.__class__.__module__ != "logging", \
            f"logger is a stdlib logger: {type(m.logger)}"

    def test_a_failed_response_delivery_does_not_log_the_presigned_url(self, crReindexer):
        """The delivery failure is the second way the URL reaches CloudWatch.

        urllib3 puts the request URI in its own exception message
        (`<pool>: Max retries exceeded with url: /...?X-Amz-Signature=...`), so
        interpolating the exception publishes the presigned ResponseURL just as
        dumping the event did. This is asserted against `send_cfn_response`
        directly, and with a transport that RAISES: every other test in this file
        either patches the function out or hands it a stub that answers, so the
        except branch is not otherwise reached.
        """
        m = crReindexer
        recorder = MagicMock()
        failing_http = MagicMock()
        failing_http.request.side_effect = m.urllib3.exceptions.MaxRetryError(
            "HTTPSConnectionPool(host='cloudformation-custom-resource-response-"
            "useast1.s3.amazonaws.com', port=443)",
            RESPONSE_URL,
            OSError("connection refused"),
        )

        with patch.object(m, "logger", recorder), \
                patch.object(m, "http", failing_http):
            m.send_cfn_response(_cfn_event(), _context(), "FAILED",
                                reason="Reindexing did not complete")

        rendered = "\n".join(repr(call) for call in recorder.method_calls)
        assert "X-Amz-Signature" not in rendered
        assert RESPONSE_URL not in rendered
        # Positive control: the delivery failure is still reported, and still names
        # the fault, so the two assertions above are not passing on silence.
        assert "Failed to send CloudFormation response" in rendered
        assert "MaxRetryError" in rendered

    def test_a_delivered_response_still_reaches_the_presigned_url(self, crReindexer):
        """Positive control: the URL must still be USED, only never logged."""
        m = crReindexer
        recorder = MagicMock()
        delivering_http = MagicMock()
        delivering_http.request.return_value = MagicMock(status=200)

        with patch.object(m, "logger", recorder), \
                patch.object(m, "http", delivering_http):
            m.send_cfn_response(_cfn_event(), _context(), "SUCCESS")

        assert delivering_http.request.call_args.args[1] == RESPONSE_URL
        rendered = "\n".join(repr(call) for call in recorder.method_calls)
        assert RESPONSE_URL not in rendered
        assert "CloudFormation response status" in rendered
