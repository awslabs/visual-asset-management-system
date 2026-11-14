# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The record-level completion checks shared by the upload completion and ingest-asset."""

import pytest

from common.assetUploads import (
    UPLOAD_EXPIRED,
    UPLOAD_RECORD_MISMATCH,
    UPLOAD_TYPE_MISMATCH,
    UploadCompletionRefusal,
    completion_refusal,
)

NOW = 1_800_000_000


def _record(**overrides):
    record = {"uploadId": "u1", "assetId": "a1", "databaseId": "d1", "uploadType": "assetFile",
              "expiresAt": NOW + 60, "isExternalUpload": False}
    record.update(overrides)
    return {k: v for k, v in record.items() if v is not None}


@pytest.mark.unit
class TestCompletionRefusal:
    def test_a_completable_record_is_not_refused(self):
        assert completion_refusal(_record(), "d1", "a1", "assetFile", now=NOW) is None

    def test_a_record_left_completed_with_errors_stays_completable(self):
        assert completion_refusal(_record(status="completed_with_errors"), "d1", "a1", "assetFile", now=NOW) is None

    @pytest.mark.parametrize("database_id,asset_id", [("d2", "a1"), ("d1", "a2")])
    def test_a_record_of_another_database_or_asset_is_refused(self, database_id, asset_id):
        assert completion_refusal(_record(), database_id, asset_id, "assetFile", now=NOW) == \
            UploadCompletionRefusal(UPLOAD_RECORD_MISMATCH, audited=False)

    def test_a_record_of_another_upload_type_is_refused(self):
        assert completion_refusal(_record(), "d1", "a1", "assetPreview", now=NOW) == \
            UploadCompletionRefusal(UPLOAD_TYPE_MISMATCH, audited=False)

    @pytest.mark.parametrize("expires_at", [NOW - 1, 0, None, "not-a-number"],
                             ids=["past", "zero", "missing", "unreadable"])
    def test_an_expired_record_is_refused_and_audited(self, expires_at):
        assert completion_refusal(_record(expiresAt=expires_at), "d1", "a1", "assetFile", now=NOW) == \
            UploadCompletionRefusal(UPLOAD_EXPIRED, audited=True)

    def test_a_workflow_output_record_is_refused_and_audited(self):
        assert completion_refusal(_record(isExternalUpload=True), "d1", "a1", "assetFile", now=NOW) == \
            UploadCompletionRefusal(UPLOAD_TYPE_MISMATCH, audited=True)

    def test_the_first_failing_check_wins(self):
        record = _record(databaseId="d2", uploadType="assetPreview", expiresAt=0, isExternalUpload=True)
        assert completion_refusal(record, "d1", "a1", "assetFile", now=NOW).reason == UPLOAD_RECORD_MISMATCH
