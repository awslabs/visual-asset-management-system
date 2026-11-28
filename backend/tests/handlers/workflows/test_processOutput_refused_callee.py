# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The output step ends its attempt, retryably, when a function it calls could not start (#1187).

When Parameter Store throttles a burst of cold starts, a VAMS function whose resource names were
refused at import answers a synchronous invoke with an unhandled function error of type
``ResourceNameUnavailableError``: it never ran its handler. The process-output step called the file
ingestion function, the metadata service and the copy engine that way, recorded a failed write-back
and returned, so the run ended FAILED although nothing had gone wrong with its outputs. Now the step
releases its ingest-ledger claims on what the refused call did not ingest and raises
``ResourceNameUnavailableError``, which the workflow definition retries; the retried attempt records
what earlier calls settled and ingests the rest, each output once, and no older staged version of an
output is left behind. Any other callee error keeps the recorded write-back failure.

The harness of test_processOutput_retry_records_all_outputs runs the real step against the real upload
handler, with the buckets, the upload records and the ingest ledger in moto. Retries are driven here the
way the state's retrier drives them: a raised ``ResourceNameUnavailableError`` starts the next attempt.
The refusal is detected by the function error's type, never by its message.
"""

import io
import json
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.workflows import test_processOutput_retry_records_all_outputs as h
from backend.backend.handlers.workflows.sfn import processWorkflowExecutionOutput as po
from backend.backend.handlers.assets import uploadFile as uf
from backend.backend.common.workflows import executionOutputs as eo

# The module objects the step itself uses, so exception classes and patches are the ones it sees.
loc = po.loc

ERROR = "ResourceNameUnavailableError"
METADATA_PREFIX = f"pipelines/p1/JOB/output/{h.EXEC}/metadata/"


RESOLVER_MESSAGE = ("Resource name not resolved while SSM sweeps back off after a failure: "
                    "dynamoTables/s3AssetBucketsStorage")


def _function_error(error_type, message=RESOLVER_MESSAGE):
    """A synchronous invoke answered with an unhandled function error, as Lambda returns it for an
    error raised while the function loaded (or ran)."""
    body = {"errorMessage": message, "errorType": error_type, "requestId": "r-1", "stackTrace": []}
    return {"StatusCode": 200, "FunctionError": "Unhandled",
            "Payload": io.BytesIO(json.dumps(body).encode("utf-8"))}


def _is_refusal(error):
    return type(error).__name__ == ERROR


def _run(refuse_upload=None, refuse_engine=None, refuse_metadata=None, with_preview=False,
         with_large=False, with_metadata=False, files_per_call=None, real_stamp=False,
         upload_error_type=None, max_attempts=5):
    """Run the output step until an attempt answers (or max_attempts attempts raised the refusal).

    ``refuse_upload(attempt, call_index)``, ``refuse_engine(attempt, operation)`` and
    ``refuse_metadata(attempt)`` decide whether that call answers with the refusal. ``upload_error_type``
    makes every ingestion call of attempt 0 answer with that other function error instead."""
    s3 = boto3.client("s3", region_name=h.REGION)
    h._stage(s3, with_preview=with_preview, with_large=with_large)
    if with_metadata:
        s3.put_object(Bucket=h.RUN_BUCKET, Key=METADATA_PREFIX + "asset.metadata.json",
                      Body=json.dumps({"type": "metadata", "metadata": [
                          {"metadataKey": "k1", "metadataValue": "v1", "metadataValueType": "string"}]}))
    ddb, uploads = h._tables()
    enforcer = MagicMock(**{"enforce.return_value": True, "enforceAPI.return_value": True})
    engine = h._FakeCopyEngine(s3)
    attempt = [0]
    calls_this_attempt = [0]
    handed = []
    metadata_calls = []
    engine_calls = []

    def invoke_upload(payload):
        index = calls_this_attempt[0]
        calls_this_attempt[0] += 1
        # Each output is sent as its path in the asset with a leading '/'; recorded here by its name
        files = sorted(f["relativeKey"].lstrip("/") for f in json.loads(payload["body"])["files"])
        if refuse_upload and refuse_upload(attempt[0], index):
            handed.append((attempt[0], files, "refused"))
            return _function_error(ERROR)
        if upload_error_type and attempt[0] == 0:
            handed.append((attempt[0], files, "failed"))
            # The resolver's own message under another error type: not a refusal.
            return _function_error(upload_error_type)
        handed.append((attempt[0], files, "called"))
        context = MagicMock(**{"get_remaining_time_in_millis.return_value": 900_000})
        response = uf.lambda_handler(json.loads(json.dumps(payload)), context)
        return {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(response).encode("utf-8"))}

    def invoke_metadata(_event):
        if refuse_metadata and refuse_metadata(attempt[0]):
            metadata_calls.append((attempt[0], "refused"))
            return _function_error(ERROR)
        metadata_calls.append((attempt[0], "called"))
        answer = {"statusCode": 200, "body": json.dumps({"message": "ok"})}
        return {"StatusCode": 200, "Payload": io.BytesIO(json.dumps(answer).encode("utf-8"))}

    def invoke_engine(FunctionName, InvocationType, Payload):
        request = json.loads(Payload)
        operation = request.pop("operation")
        request.pop("action", None)
        if refuse_engine and refuse_engine(attempt[0], operation):
            engine_calls.append((attempt[0], operation, "refused"))
            return _function_error(ERROR, message="Resource name not resolved while SSM sweeps back off "
                                                  "after a failure: dynamoTables/largeObjectCopyJobsStorage")
        engine_calls.append((attempt[0], operation, "called"))
        return {"StatusCode": 200,
                "Payload": io.BytesIO(json.dumps(engine.call(operation, **request)).encode("utf-8"))}

    event = h._event(with_preview)
    if with_metadata:
        event["body"]["metadataPathKey"] = METADATA_PREFIX

    with ExitStack() as stack:
        p = lambda *a, **k: stack.enter_context(patch.object(*a, **k))  # noqa: E731
        p(po, "lookup_existing_asset", return_value={
            "databaseId": h.DATABASE_ID, "assetId": h.ASSET_ID, "bucketId": "b1"})
        p(po, "get_default_bucket_details", return_value={
            "bucketId": "b1", "bucketName": h.ASSET_BUCKET, "baseAssetsPrefix": "assets/"})
        p(po, "_fetch_execution_logs", return_value=("", ""))
        p(po, "_execution_start_date", return_value="")
        p(po, "_refresh_execution_log")
        p(po, "verify_get_path_objects", side_effect=lambda bucket, prefix: h._listing(s3, prefix))
        p(po, "asset_upload_table", uploads)
        p(po, "dynamodb", ddb)
        if real_stamp:
            p(po, "s3r", boto3.resource("s3", region_name=h.REGION))
        else:
            p(po, "_stamp_output_objects", return_value=True)
        p(po, "_lambda_file_ingestion", side_effect=invoke_upload)
        p(po, "_lambda_metadata_service", side_effect=invoke_metadata)
        if files_per_call:
            real_chunks = po._completion_chunks
            p(po, "_completion_chunks", side_effect=lambda objects: real_chunks(objects, max_files=files_per_call))
        p(po, "_wait_sleep")
        record = p(po, "record_execution_outputs")
        p(po, "s3c", s3)
        p(po, "engine_invoke_client", MagicMock(**{"invoke.side_effect": invoke_engine}))
        p(po, "copy_engine_function_name", "vams-copy-engine")
        p(po, "_mark_ingesting", return_value=True)
        p(uf, "request_to_claims", return_value={"tokens": ["SYSTEM_USER"], "roles": ["SYSTEM"]})
        p(uf, "CasbinEnforcer", return_value=enforcer)
        p(uf, "asset_upload_table", uploads)
        p(uf, "get_asset_details", return_value={
            "assetId": h.ASSET_ID, "databaseId": h.DATABASE_ID, "bucketId": "b1",
            "assetLocation": {"Key": f"assets/{h.ASSET_ID}/"}})
        p(uf, "get_default_bucket_details", return_value={
            "bucketId": "b1", "bucketName": h.ASSET_BUCKET, "baseAssetsPrefix": "assets/",
            "bucketRegion": h.REGION})
        p(uf, "bucket_region_for_name", return_value=h.REGION)
        p(uf, "get_database_details", return_value={"databaseId": h.DATABASE_ID})
        p(uf, "validateS3AssetExtensionsAndContentType", return_value=True)
        p(uf, "determine_asset_type", return_value=".glb")
        p(uf, "send_subscription_email")
        p(uf, "log_file_upload")
        p(uf, "update_asset_attributes")
        p(uf, "s3", s3)
        p(uf, "copy_s3_object_repeat_safe", new=h.repeat_safe_copy_on(s3))
        p(uf, "MAX_PARALLEL_S3_WORKERS", 1)
        p(uf, "LARGE_FILE_THRESHOLD_BYTES", h.THRESHOLD)
        p(po.loc, "LARGE_FILE_THRESHOLD_BYTES", h.THRESHOLD)
        p(uf, "start_output_copy_job", side_effect=engine.start)

        raised = []
        ledgers = []
        answer = None
        for i in range(max_attempts):
            attempt[0] = i
            calls_this_attempt[0] = 0
            body = json.loads(json.dumps(event))
            body["body"]["outputStepAttempt"] = i
            try:
                answer = po.lambda_handler(body, h._ctx(i))
                break
            except Exception as e:  # recorded: the state's retrier decides on the error name
                raised.append(type(e).__name__)
                ledgers.append(eo.read_ingest_ledger(ddb, po.pipeline_execution_output_files_table, h.EXEC))
                if not _is_refusal(e):
                    break
        finalized = None
        if answer and answer.get("outputIngestionPending"):
            engine.land()
            finalized = po.lambda_handler(
                {"body": {"mode": po.MODE_FINALIZE, "batchId": h.EXEC, "workflowExecutionId": h.EXEC}},
                h._ctx(9))
        ledger = eo.read_ingest_ledger(ddb, po.pipeline_execution_output_files_table, h.EXEC)
        versions = {}
        for v in s3.list_object_versions(Bucket=h.ASSET_BUCKET).get("Versions", []):
            versions.setdefault(v["Key"], []).append(v["VersionId"])
        recorded = []
        status = error = None
        if record.call_args:
            kwargs = record.call_args.kwargs
            recorded = sorted((f["fileType"], f["relativeFilePath"].lstrip("/")) for f in kwargs["output_files"])
            status, error = kwargs["execution_status"], kwargs["execution_error"]
        staged_versions = sorted(
            v["Key"] for v in s3.list_object_versions(Bucket=h.RUN_BUCKET).get("Versions", [])
            if v["Key"].startswith((h.FILES_PREFIX, h.PREVIEW_PREFIX)))
    return {"answer": answer, "raised": raised, "ledgers": ledgers, "ledger": ledger, "handed": handed,
            "versions": versions, "recorded": recorded, "status": status, "error": error,
            "record_calls": record.call_count, "staged_versions": staged_versions,
            "metadata_calls": metadata_calls, "engine_calls": engine_calls, "starts": engine.starts,
            "finalized": finalized}


def _states(ledger):
    return {row.get("s3Key", "").rsplit("/", 1)[-1]: row.get("ingestState") for row in ledger.values()}


def _once(result, names):
    for name in names:
        assert len(result["versions"][h._asset_key(name)]) == 1, result["versions"]


@pytest.mark.unit
@pytest.mark.aws
class TestARefusedFileIngestionIsRetried:
    def test_the_attempt_raises_the_retryable_error_and_records_nothing(self):
        with mock_aws():
            result = _run(refuse_upload=lambda attempt, _i: attempt == 0, max_attempts=1)
        assert result["raised"] == [ERROR]
        assert result["record_calls"] == 0
        # The claims of the refused call are released: nothing is left `moving` for a retry to wait on.
        assert _states(result["ledgers"][0]) == {}

    def test_the_retry_ingests_every_output_once_and_the_run_succeeds(self):
        with mock_aws():
            result = _run(refuse_upload=lambda attempt, _i: attempt == 0)
        assert result["raised"] == [ERROR]
        assert result["answer"]["statusCode"] == 200
        assert result["status"] == "SUCCEEDED"
        assert result["recorded"] == sorted(("file", n) for n in h.SMALL)
        _once(result, h.SMALL)
        assert set(_states(result["ledger"]).values()) == {eo.INGEST_STATE_INGESTED}

    def test_a_later_call_refused_keeps_what_an_earlier_call_of_the_attempt_moved(self):
        # One file per completion call: attempt 0 moves a.glb, then its call for b.glb is refused.
        with mock_aws():
            result = _run(refuse_upload=lambda attempt, i: attempt == 0 and i == 1, files_per_call=1)
        assert result["handed"] == [(0, [h.SMALL[0]], "called"), (0, [h.SMALL[1]], "refused"),
                                    (1, [h.SMALL[1]], "called")]
        assert _states(result["ledgers"][0]) == {h.SMALL[0]: eo.INGEST_STATE_INGESTED}
        assert result["answer"]["statusCode"] == 200 and result["status"] == "SUCCEEDED"
        # a.glb is recorded from the ledger, not ingested again; b.glb is ingested by the retry.
        assert result["recorded"] == sorted(("file", n) for n in h.SMALL)
        _once(result, h.SMALL)

    def test_repeated_refusals_are_each_released_until_one_call_answers(self):
        with mock_aws():
            result = _run(refuse_upload=lambda attempt, _i: attempt < 3)
        assert result["raised"] == [ERROR] * 3
        assert all(_states(ledger) == {} for ledger in result["ledgers"])
        assert result["status"] == "SUCCEEDED"
        _once(result, h.SMALL)

    def test_a_refused_preview_call_is_released_and_retried(self):
        with mock_aws():
            result = _run(refuse_upload=lambda attempt, i: attempt == 0 and i == 0, with_preview=True)
        assert result["handed"][0] == (0, [h.PREVIEW], "refused")
        assert _states(result["ledgers"][0]) == {}
        assert result["status"] == "SUCCEEDED"
        assert result["recorded"] == sorted([("file", n) for n in h.SMALL] + [("preview", h.PREVIEW)])
        _once(result, h.SMALL)
        assert len(result["versions"][f"assets/previews/{h.ASSET_ID}/{h.PREVIEW}"]) == 1

    def test_a_refused_large_file_hand_off_starts_one_copy_job(self):
        with mock_aws():
            result = _run(refuse_upload=lambda attempt, _i: attempt == 0, with_large=True)
        assert result["raised"] == [ERROR]
        assert len(result["starts"]) == 1
        assert result["finalized"]["finalized"] is True
        assert result["recorded"] == sorted(("file", n) for n in h.SMALL + (h.LARGE,))
        _once(result, h.SMALL + (h.LARGE,))

    def test_with_the_real_stamp_every_output_lands_once_and_nothing_stays_staged(self):
        # The stamp writes a new version of each staged object; a refused attempt deletes the version
        # it listed (the stamped copy holds the content), so no older version is left behind.
        with mock_aws():
            result = _run(refuse_upload=lambda attempt, _i: attempt < 2, real_stamp=True)
        assert result["raised"] == [ERROR, ERROR]
        assert result["staged_versions"] == [], result["staged_versions"]
        assert result["status"] == "SUCCEEDED"
        assert result["recorded"] == sorted(("file", n) for n in h.SMALL)
        _once(result, h.SMALL)


@pytest.mark.unit
@pytest.mark.aws
class TestTheReleaseNeverRemovesTheCurrentVersion:
    def _staged(self, s3, stamp):
        s3.create_bucket(Bucket=h.RUN_BUCKET)
        s3.put_bucket_versioning(Bucket=h.RUN_BUCKET, VersioningConfiguration={"Status": "Enabled"})
        listed = s3.put_object(Bucket=h.RUN_BUCKET, Key=h.FILES_PREFIX + "a.glb", Body=b"data")["VersionId"]
        if stamp:
            s3.copy_object(Bucket=h.RUN_BUCKET, Key=h.FILES_PREFIX + "a.glb", MetadataDirective="REPLACE",
                           CopySource={"Bucket": h.RUN_BUCKET, "Key": h.FILES_PREFIX + "a.glb"},
                           Metadata={"stamped": "1"})
        return {"fileType": "file", "relativeFilePath": "a.glb", "s3Bucket": h.RUN_BUCKET,
                "s3Key": h.FILES_PREFIX + "a.glb", "s3VersionId": listed}

    @pytest.mark.parametrize("stamp", [True, False])
    def test_the_content_stays_staged(self, stamp):
        with mock_aws():
            s3 = boto3.client("s3", region_name=h.REGION)
            descriptor = self._staged(s3, stamp)
            with patch.object(po, "s3c", s3), patch.object(po, "_drop_ingest_ledger_rows") as drop:
                po._release_refused_call(h.EXEC, [descriptor], {descriptor["s3Key"]}, "attempt-0", h.RUN_BUCKET)
            versions = s3.list_object_versions(Bucket=h.RUN_BUCKET).get("Versions", [])
            body = s3.get_object(Bucket=h.RUN_BUCKET, Key=descriptor["s3Key"])["Body"].read()
        assert body == b"data"
        # Stamped: the listed (older) version is removed, the stamped copy stays. Not stamped: the
        # listed version is the current one and is kept.
        assert len(versions) == 1
        assert (versions[0]["VersionId"] == descriptor["s3VersionId"]) is (not stamp)
        drop.assert_called_once()


@pytest.mark.unit
@pytest.mark.aws
class TestOtherCalleeErrorsKeepTheRecordedFailure:
    @pytest.mark.parametrize("error_type", ["KeyError", "Runtime.Unknown", "ThrottlingException"])
    def test_a_different_function_error_records_the_write_back_failure(self, error_type):
        with mock_aws():
            result = _run(upload_error_type=error_type, max_attempts=1)
        assert result["raised"] == []
        assert result["answer"]["statusCode"] == 200
        assert result["status"] == "FAILED"
        assert po.FILE_WRITE_BACK_FAILURE in result["error"]
        # The claims are released as before, for a later run of the step.
        assert _states(result["ledger"]) == {}

    def test_the_refusal_is_read_from_the_error_type_not_the_message(self):
        # The resolver's message under another error type is not a refusal.
        with mock_aws():
            result = _run(upload_error_type="ValueError", max_attempts=1)
        assert result["status"] == "FAILED"

    def test_an_answer_without_a_function_error_is_not_a_refusal(self):
        response = {"StatusCode": 200, "Payload": io.BytesIO(b"{}")}
        po._raise_if_refused_at_start(response, {"errorType": ERROR}, "file ingestion")


@pytest.mark.unit
@pytest.mark.aws
class TestARefusedEngineCallIsRetried:
    def test_a_refused_recorded_jobs_call_retries_and_finishes_with_one_job(self):
        refused = {"done": False}

        def refuse(attempt, operation):
            if attempt == 0 and operation == loc.OUTPUT_STEP_RECORDED_JOBS and not refused["done"]:
                refused["done"] = True
                return True
            return False

        with mock_aws():
            result = _run(refuse_engine=refuse, with_large=True)
        assert result["raised"] == [ERROR]
        assert (0, loc.OUTPUT_STEP_RECORDED_JOBS, "refused") in result["engine_calls"]
        # The small outputs attempt 0 moved are recorded from the ledger; the large one stays with its job.
        assert _states(result["ledgers"][0]) == {
            h.SMALL[0]: eo.INGEST_STATE_INGESTED, h.SMALL[1]: eo.INGEST_STATE_INGESTED,
            h.LARGE: eo.INGEST_STATE_ENGINE_COPYING}
        assert len(set(result["starts"])) == 1
        assert result["finalized"]["finalized"] is True
        assert result["recorded"] == sorted(("file", n) for n in h.SMALL + (h.LARGE,))
        _once(result, h.SMALL + (h.LARGE,))

    def test_a_refused_record_batch_call_retries(self):
        with mock_aws():
            result = _run(refuse_engine=lambda attempt, op: attempt == 0 and op == loc.OUTPUT_STEP_RECORD_BATCH,
                          with_large=True)
        assert result["raised"] == [ERROR]
        assert result["finalized"]["finalized"] is True
        _once(result, h.SMALL + (h.LARGE,))

    def test_call_engine_carries_the_engine_error_type(self):
        client = MagicMock(**{"invoke.return_value": _function_error(ERROR)})
        with pytest.raises(loc.EngineCallFailed) as failed:
            loc.call_engine(client, "engine", loc.OUTPUT_STEP_RECORDED_JOBS, workflowExecutionId="e")
        assert failed.value.error_type == ERROR

    @pytest.mark.parametrize("error_type", ["KeyError", ""])
    def test_another_engine_failure_stays_engine_call_failed(self, error_type):
        with patch.object(po.loc, "call_engine", side_effect=loc.EngineCallFailed("x", error_type=error_type)):
            with pytest.raises(loc.EngineCallFailed):
                po._engine(loc.OUTPUT_STEP_RECORDED_JOBS, workflowExecutionId="e")

    def test_an_engine_that_could_not_start_raises_the_retryable_error(self):
        with patch.object(po.loc, "call_engine", side_effect=loc.EngineCallFailed("x", error_type=ERROR)):
            with pytest.raises(Exception) as raised:
                po._engine(loc.OUTPUT_STEP_RECORDED_JOBS, workflowExecutionId="e")
        assert _is_refusal(raised.value)


@pytest.mark.unit
@pytest.mark.aws
class TestARefusedMetadataCallIsRetried:
    def test_the_attempt_raises_and_the_retry_applies_the_metadata(self):
        with mock_aws():
            result = _run(refuse_metadata=lambda attempt: attempt == 0, with_metadata=True)
        assert result["raised"] == [ERROR]
        assert result["metadata_calls"] == [(0, "refused"), (1, "called")]
        assert result["status"] == "SUCCEEDED"
        # The files attempt 0 moved before the metadata call are recorded once, from the ledger.
        assert [c for c in result["handed"] if c[0] == 1] == []
        assert result["recorded"] == sorted(("file", n) for n in h.SMALL)
        _once(result, h.SMALL)

    def test_process_metadata_file_raises_on_a_refusal_and_not_on_another_error(self):
        with mock_aws():
            s3 = boto3.client("s3", region_name=h.REGION)
            s3.create_bucket(Bucket=h.RUN_BUCKET)
            s3.put_object(Bucket=h.RUN_BUCKET, Key=METADATA_PREFIX + "asset.metadata.json",
                          Body=json.dumps({"metadata": [{"metadataKey": "k", "metadataValue": "v"}]}))
            args = (h.RUN_BUCKET, METADATA_PREFIX + "asset.metadata.json", METADATA_PREFIX, h.DATABASE_ID,
                    h.ASSET_ID, None, "metadata", None)
            with patch.object(po, "s3c", s3):
                with patch.object(po, "_lambda_metadata_service", return_value=_function_error(ERROR)):
                    with pytest.raises(Exception) as raised:
                        po.process_metadata_file(*args)
                    assert _is_refusal(raised.value)
                with patch.object(po, "_lambda_metadata_service",
                                  return_value=_function_error("KeyError", message="other")):
                    rejected = po.process_metadata_file(*args)
        assert not _is_refusal(rejected) and rejected is not None
