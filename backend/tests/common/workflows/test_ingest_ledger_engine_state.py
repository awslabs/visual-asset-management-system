# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The ingest ledger's `engine-copying` state (#1271 + #1123): an output handed to the large object copy
engine is never claimed, waited on or taken over again, and settles only on its job's outcome."""

import boto3
import pytest
from moto import mock_aws

from backend.backend.common.workflows import executionOutputs as eo

TABLE = "t-of-engine-ledger"
RUN = "0123456789abcdef0123456789abcdef"
D = {"fileType": "file", "relativeFilePath": "big.e57", "s3Bucket": "run", "s3Key": "files/big.e57",
     "fileSize": 200, "contentType": "", "s3VersionId": "v1"}
NOW = 1_800_000_000


def _ddb():
    ddb = boto3.resource("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "pipelineExecutionId", "KeyType": "HASH"},
                   {"AttributeName": "fileType:relativeFilePath", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": "pipelineExecutionId", "AttributeType": "S"},
                              {"AttributeName": "fileType:relativeFilePath", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    return ddb


def _row(ddb):
    return eo.read_ingest_ledger(ddb, TABLE, RUN)["file:big.e57"]


@pytest.mark.unit
@pytest.mark.aws
class TestEngineCopyingRows:
    def test_only_the_holder_hands_off_and_a_repeat_with_the_same_job_is_a_no_op(self):
        with mock_aws():
            ddb = _ddb()
            assert eo.claim_ingest_rows(ddb, TABLE, RUN, [D], "a0", "u1", NOW) == [D]
            eo.mark_engine_copying(ddb, TABLE, RUN, [D], ["job-1"], "someone-else")
            assert _row(ddb)["ingestState"] == eo.INGEST_STATE_MOVING
            eo.mark_engine_copying(ddb, TABLE, RUN, [D], ["job-1"], "a0")
            row = _row(ddb)
            assert (row["ingestState"], row["engineJobId"]) == (eo.INGEST_STATE_ENGINE_COPYING, "job-1")
            assert "movingSince" not in row
            eo.mark_engine_copying(ddb, TABLE, RUN, [D], ["job-1"], "a1")
            assert _row(ddb)["engineJobId"] == "job-1"

    def test_no_claim_or_takeover_ever_succeeds_over_an_engine_row(self):
        with mock_aws():
            ddb = _ddb()
            eo.claim_ingest_rows(ddb, TABLE, RUN, [D], "a0", "u1", NOW)
            eo.mark_engine_copying(ddb, TABLE, RUN, [D], ["job-1"], "a0")
            # Far past the takeover age, and with an explicit takeover map: still the engine's.
            later = NOW + 48 * 3600
            assert eo.claim_ingest_rows(ddb, TABLE, RUN, [D], "a1", "u2", later) == []
            assert eo.claim_ingest_rows(ddb, TABLE, RUN, [D], "a1", "u2", later,
                                        takeover={"file:big.e57": ("a0", NOW)}) == []
            assert eo.is_live_moving_row(_row(ddb), "a1", later) is False
            # The end-state step's release of its own failed outputs leaves the engine row alone.
            eo.drop_ingest_ledger_rows(ddb, TABLE, RUN, [D], holder="a0")
            assert _row(ddb)["ingestState"] == eo.INGEST_STATE_ENGINE_COPYING

    @pytest.mark.parametrize("status,expected", [("SUCCEEDED", eo.INGEST_STATE_INGESTED), ("FAILED", None),
                                                 ("CANCELLED", None), (None, None)])
    def test_finalize_settles_on_the_job_outcome(self, status, expected):
        with mock_aws():
            ddb = _ddb()
            eo.claim_ingest_rows(ddb, TABLE, RUN, [D], "a0", "u1", NOW)
            eo.mark_engine_copying(ddb, TABLE, RUN, [D], ["job-1"], "a0")
            statuses = {"job-1": status} if status else {}
            eo.settle_engine_rows(ddb, TABLE, RUN, eo.read_ingest_ledger(ddb, TABLE, RUN), statuses, "SUCCEEDED")
            ledger = eo.read_ingest_ledger(ddb, TABLE, RUN)
            assert (ledger.get("file:big.e57") or {}).get("ingestState") == expected

    def test_a_row_naming_another_job_is_not_settled(self):
        with mock_aws():
            ddb = _ddb()
            eo.claim_ingest_rows(ddb, TABLE, RUN, [D], "a0", "u1", NOW)
            eo.mark_engine_copying(ddb, TABLE, RUN, [D], ["job-1"], "a0")
            stale = eo.read_ingest_ledger(ddb, TABLE, RUN)
            stale["file:big.e57"] = {**stale["file:big.e57"], "engineJobId": "job-0"}
            eo.settle_engine_rows(ddb, TABLE, RUN, stale, {"job-0": "SUCCEEDED"}, "SUCCEEDED")
            assert _row(ddb)["ingestState"] == eo.INGEST_STATE_ENGINE_COPYING


@pytest.mark.unit
@pytest.mark.aws
class TestMovingRowsTheEngineCopied:
    """Combined smoke D1: a `moving` row whose staged key a copy job of this run read from is settled
    from that job; a `moving` row no job reads from (a small-file move) is never touched."""

    SMALL = {**D, "relativeFilePath": "a.glb", "s3Key": "files/a.glb", "fileSize": 5}

    def _ledger(self, ddb):
        eo.claim_ingest_rows(ddb, TABLE, RUN, [D, self.SMALL], "a0", "u1", NOW)
        return eo.read_ingest_ledger(ddb, TABLE, RUN)

    @pytest.mark.parametrize("status,expected", [("SUCCEEDED", eo.INGEST_STATE_INGESTED), ("FAILED", None),
                                                 ("CANCELLED", None)])
    def test_an_ended_job_settles_the_row(self, status, expected):
        with mock_aws():
            ddb = _ddb()
            ledger = self._ledger(ddb)
            eo.settle_engine_rows(ddb, TABLE, RUN, ledger, {"job-1": status}, "SUCCEEDED",
                                  jobs_by_source_key={D["s3Key"]: ("job-1", status)},
                                  terminal_statuses=("SUCCEEDED", "FAILED", "CANCELLED"))
            after = eo.read_ingest_ledger(ddb, TABLE, RUN)
            assert (after.get("file:big.e57") or {}).get("ingestState") == expected
            # The small-file move's row is left exactly as it was.
            assert after["file:a.glb"] == ledger["file:a.glb"]

    def test_a_running_job_or_a_changed_row_is_left_alone(self):
        with mock_aws():
            ddb = _ddb()
            ledger = self._ledger(ddb)
            terminal = ("SUCCEEDED", "FAILED", "CANCELLED")
            eo.settle_engine_rows(ddb, TABLE, RUN, ledger, {}, "SUCCEEDED",
                                  jobs_by_source_key={D["s3Key"]: ("job-1", "RUNNING")},
                                  terminal_statuses=terminal)
            assert _row(ddb)["ingestState"] == eo.INGEST_STATE_MOVING
            # Taken over since the read (another holder): not settled from the stale read.
            eo.claim_ingest_rows(ddb, TABLE, RUN, [D], "a1", "u2", NOW + eo.INGEST_TAKEOVER_SECONDS + 1)
            eo.settle_engine_rows(ddb, TABLE, RUN, ledger, {}, "SUCCEEDED",
                                  jobs_by_source_key={D["s3Key"]: ("job-1", "SUCCEEDED")},
                                  terminal_statuses=terminal)
            row = _row(ddb)
            assert (row["ingestState"], row["ingestHolder"]) == (eo.INGEST_STATE_MOVING, "a1")
