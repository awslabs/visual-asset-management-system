# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An N-file upload through the upload API is evaluated exactly once, at its completion.

The upload completion copies every file into the asset and only then stamps `lastUploadAt` on the
asset row; the stamp's stream MODIFY evaluates the asset with every file in place. A file event for
an object the upload API wrote (`vams-changesource: upload`) that reaches the trigger before that
stamp therefore waits for it (outcome `deferred`) instead of evaluating a partial file set, and one
that arrives after the completion's evaluation started is covered by it. Only an object with other
provenance (a direct bucket write) is judged by its own event time."""

import pytest

from backend.tests.common.test_indexerEvents import indexer_message, s3_event_record
from backend.tests.handlers.compliance._harness import ASSET, DB, SCHEMA
from backend.tests.handlers.compliance.test_complianceTrigger import (
    AUTO_EVAL_DB, _coverage_lines, _run, _s3_head, _sns_event, _stream_message)

PREVIOUS_UPLOAD = "2026-03-01T11:00:00+00:00"
PREVIOUS_EVALUATION = "2026-03-01T11:00:01+00:00"
COMPLETION_STAMP = "2026-03-01T12:00:05.000000+00:00"
COMPLETION_EVALUATION = "2026-03-01T12:00:07+00:00"
FILE_EVENT_TIMES = [f"2026-03-01T12:00:0{n}.{n}00Z" for n in range(1, 5)]


def _row(last_upload_at):
    row = {"databaseId": DB, "assetId": ASSET}
    if last_upload_at:
        row["lastUploadAt"] = last_upload_at
    return [row]


def _record(last_evaluated_at):
    return {"schemaName": SCHEMA, "schemaSource": "database", "lastEvaluationId": "eval-x",
            "lastEvaluatedAt": last_evaluated_at, "lastEvaluationStatus": "completed"}


def _file_messages(names=("a.stl", "b.stl", "c.stl", "d.stl")):
    return [indexer_message(s3_event_record(f"{ASSET}/{name}", event_time=event_time))
            for name, event_time in zip(names, FILE_EVENT_TIMES)]


@pytest.mark.unit
class TestAnUploadIsEvaluatedOnceAtItsCompletion:

    def test_a_four_file_upload_with_every_file_event_early_yields_one_evaluation(self):
        evaluations = 0
        # 1. The four file events, each its own queue batch, before the completion stamp.
        for message in _file_messages():
            mocks = _run(_sns_event(message), database_item=AUTO_EVAL_DB,
                         compliance_record=_record(PREVIOUS_EVALUATION),
                         asset_rows=_row(PREVIOUS_UPLOAD), head=_s3_head("upload"))
            evaluations += mocks["run_evaluation"].call_count
            assert [line.rsplit(" ", 1)[1] for line in _coverage_lines(mocks)] == [
                "outcome=deferred"]
        assert evaluations == 0
        # 2. The completion stamp's MODIFY evaluates.
        mocks = _run(_sns_event(_stream_message(change_source="upload",
                                                change_at=COMPLETION_STAMP)),
                     database_item=AUTO_EVAL_DB, compliance_record=_record(PREVIOUS_EVALUATION))
        evaluations += mocks["run_evaluation"].call_count
        # 3. A late duplicate delivery of a file event, after that evaluation started, is covered.
        mocks = _run(_sns_event(_file_messages()[0]), database_item=AUTO_EVAL_DB,
                     compliance_record=_record(COMPLETION_EVALUATION),
                     asset_rows=_row(COMPLETION_STAMP), head=_s3_head("upload"))
        evaluations += mocks["run_evaluation"].call_count
        assert evaluations == 1

    def test_the_file_events_and_the_completion_in_one_batch_yield_one_evaluation(self):
        event = _sns_event(*_file_messages()[:2],
                           _stream_message(change_source="upload", change_at=COMPLETION_STAMP),
                           *_file_messages()[2:])
        mocks = _run(event, database_item=AUTO_EVAL_DB,
                     compliance_record=_record(PREVIOUS_EVALUATION),
                     asset_rows=_row(PREVIOUS_UPLOAD), head=_s3_head("upload"))
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_upload_object_of_an_asset_with_no_completion_stamp_yet_waits(self):
        mocks = _run(_sns_event(_file_messages()[0]), database_item=AUTO_EVAL_DB,
                     compliance_record=_record(PREVIOUS_EVALUATION), asset_rows=_row(None),
                     head=_s3_head("upload"))
        mocks["run_evaluation"].assert_not_called()
        assert len(_coverage_lines(mocks)) == 1
        assert _coverage_lines(mocks)[0].endswith("outcome=deferred")

    def test_an_upload_object_the_completion_already_stamped_is_judged_by_the_stamp(self):
        """The object was copied before the stamp; an evaluation that started before the stamp did
        not see it, so it is evaluated, and one that started after is not."""
        mocks = _run(_sns_event(_file_messages()[0]), database_item=AUTO_EVAL_DB,
                     compliance_record=_record(PREVIOUS_EVALUATION),
                     asset_rows=_row(COMPLETION_STAMP), head=_s3_head("upload"))
        mocks["run_evaluation"].assert_called_once()
        mocks = _run(_sns_event(_file_messages()[0]), database_item=AUTO_EVAL_DB,
                     compliance_record=_record(COMPLETION_EVALUATION),
                     asset_rows=_row(COMPLETION_STAMP), head=_s3_head("upload"))
        mocks["run_evaluation"].assert_not_called()

    def test_a_direct_bucket_write_is_judged_by_its_own_event_time(self):
        mocks = _run(_sns_event(_file_messages()[0]), database_item=AUTO_EVAL_DB,
                     compliance_record=_record(PREVIOUS_EVALUATION),
                     asset_rows=_row(PREVIOUS_UPLOAD), head=_s3_head())
        mocks["run_evaluation"].assert_called_once_with(DB, ASSET, SCHEMA, "SYSTEM_USER")

    def test_an_unbound_database_reads_no_object_to_decide(self):
        mocks = _run(_sns_event(_file_messages()[0]),
                     database_item={"databaseId": DB, "complianceAutoEval": False},
                     asset_rows=_row(PREVIOUS_UPLOAD), head=_s3_head("upload"))
        mocks["s3"].head_object.assert_not_called()
        mocks["run_evaluation"].assert_not_called()
