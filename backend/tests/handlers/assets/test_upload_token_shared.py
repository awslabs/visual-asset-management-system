# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""One upload token key and one identity helper for the two "a landed object wins" deciders (#1125
on #1123).

The large object copy engine (P2b) and the Download as Zip archive (P4) both decide whether a
multipart upload landed by the identity of THAT upload, never by presence or size: each records a
fresh token on its job row, stamps it on CreateMultipartUpload as object metadata, and later
compares the key's current object with the row. Both now use ``common.s3MetadataKeys``'s
``VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY`` and ``is_own_upload_object``.

Also pins zipArchive mode 2 (a zipArchive run writing a large archive into an asset): the archive is a
workflow output, so it reaches the asset through the copy engine, whose landed decision is the
engine job's own token. A re-run of the same zipArchive run puts an earlier archive of the same size at
the asset key; it must never count as the new run's landed archive.
"""

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.backend.common import s3MetadataKeys as keys
from backend.tests.handlers.assets.test_largeObjectCopy_engine import (  # noqa: F401 (fixture)
    BODY, engine, env, loc)
from backend.tests.handlers.assets.test_largeObjectCopy_reconcile import (  # noqa: F401 (fixture)
    EXEC_ARN, _described, machine)

TOKEN_KEY = "vams-copyuploadtoken"
LEGACY_ZIP_TOKEN_KEY = "vams-zip-upload-token"  # the zip archive's key before the two were shared
SIZE = 10 * 1024 * 1024


def _zd():
    # The zip rules module; it imports the shared key and helper from common.s3MetadataKeys.
    from backend.backend.common import zipDownloads as zd
    return zd


def _head(size=SIZE, metadata=None, version="v1"):
    return {"ContentLength": size, "ETag": '"abc-3"', "VersionId": version, "Metadata": metadata or {}}


def _not_found():
    return ClientError({"Error": {"Code": "404", "Message": "Not Found"}}, "HeadObject")


# --------------------------------------------------------------------------------------------- #
# The one key and the one helper
# --------------------------------------------------------------------------------------------- #

@pytest.mark.unit
class TestOneKeyOneHelper:
    def test_the_key_value(self):
        assert keys.VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY == TOKEN_KEY
        assert TOKEN_KEY.startswith(keys.VAMS_METADATA_KEY_PREFIX)
        assert TOKEN_KEY not in keys.SEARCHABLE_VAMS_METADATA_KEYS

    def test_both_deciders_use_the_shared_key_and_helper(self):
        zd = _zd()
        assert zd.ARCHIVE_UPLOAD_TOKEN_METADATA == keys.VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY
        assert loc.is_own_upload_object.__module__ == zd.is_own_upload_object.__module__ \
            == "common.s3MetadataKeys"
        assert loc.is_own_upload_object.__name__ == zd.is_own_upload_object.__name__ \
            == "is_own_upload_object"

    @pytest.mark.parametrize("head, token, expected", [
        (_head(metadata={TOKEN_KEY: "t1"}), "t1", True),
        (_head(metadata={TOKEN_KEY: "t2"}), "t1", False),     # another upload's object
        (_head(), "t1", False),                                # present, same size, no token
        (_head(metadata={LEGACY_ZIP_TOKEN_KEY: "t1"}), "t1", False),
        (_head(metadata={TOKEN_KEY: ""}), "", False),           # no token on the row
        (_head(metadata={TOKEN_KEY: "t1"}), None, False),
        ({}, "t1", False),
        (None, "t1", False),
        ({"ContentLength": SIZE, "Metadata": None}, "t1", False),
    ])
    def test_identity_never_presence_or_size(self, head, token, expected):
        assert keys.is_own_upload_object(head, token) is expected


# --------------------------------------------------------------------------------------------- #
# Each decider on a same-size object at the key
# --------------------------------------------------------------------------------------------- #

@pytest.mark.unit
class TestZipLandedArchive:
    ROW = {"archiveKey": "vams.preparedDownloads/k/out.zip", "archiveUploadToken": "zip-tok"}

    def _s3(self, head=None, error=None):
        s3 = MagicMock()
        if error is not None:
            s3.head_object.side_effect = error
        else:
            s3.head_object.return_value = head
        return s3

    def test_own_archive_lands_with_its_size(self):
        s3 = self._s3(_head(size=1234, metadata={TOKEN_KEY: "zip-tok"}))
        assert _zd().landed_archive(s3, "aux", self.ROW) == (True, 1234)

    @pytest.mark.parametrize("metadata", [
        {}, {TOKEN_KEY: "another-job"}, {LEGACY_ZIP_TOKEN_KEY: "zip-tok"}])
    def test_an_earlier_same_size_object_does_not_land(self, metadata):
        s3 = self._s3(_head(size=1234, metadata=metadata))
        assert _zd().landed_archive(s3, "aux", self.ROW) == (False, None)

    def test_an_engine_landed_object_is_not_this_jobs_archive(self):
        # Same key, other writer: the token, not the key name, is the identity.
        s3 = self._s3(_head(metadata={TOKEN_KEY: "engine-job-token"}))
        assert _zd().landed_archive(s3, "aux", self.ROW) == (False, None)

    def test_a_row_without_a_token_never_heads(self):
        s3 = self._s3(_head(metadata={TOKEN_KEY: "zip-tok"}))
        assert _zd().landed_archive(s3, "aux", {"archiveKey": "k"}) == (False, None)
        s3.head_object.assert_not_called()

    def test_no_object(self):
        assert _zd().landed_archive(self._s3(error=_not_found()), "aux", self.ROW) == (False, None)


@pytest.mark.unit
class TestEngineLandedObjectVersion:
    ROW = {"destBucket": "b", "destKey": "a1/x.zip", "destUploadToken": "eng-tok",
           "sourceSize": SIZE, "partCount": 3}

    def _s3(self, head):
        s3 = MagicMock()
        s3.head_object.return_value = head
        return s3

    def test_own_object_lands_with_its_version(self):
        assert loc.landed_object_version(
            self._s3(_head(metadata={TOKEN_KEY: "eng-tok"}, version="v9")), self.ROW) == (True, "v9")

    @pytest.mark.parametrize("metadata", [{}, {TOKEN_KEY: "zip-tok"}, {LEGACY_ZIP_TOKEN_KEY: "eng-tok"}])
    def test_planned_size_and_parts_alone_do_not_land(self, metadata):
        # ContentLength == sourceSize and an ETag of partCount parts: what the old rule accepted.
        assert loc.landed_object_version(self._s3(_head(metadata=metadata)), self.ROW) == (False, "")

    def test_a_row_without_a_token_never_heads(self):
        s3 = self._s3(_head(metadata={TOKEN_KEY: "eng-tok"}))
        row = {k: v for k, v in self.ROW.items() if k != "destUploadToken"}
        assert loc.landed_object_version(s3, row) == (False, "")
        s3.head_object.assert_not_called()


# --------------------------------------------------------------------------------------------- #
# zipArchive mode 2: a large archive written into an asset lands through the engine's token
# --------------------------------------------------------------------------------------------- #

ARCHIVE = "xasset1-archive.zip"
DEST = {"Bucket": "asset-bucket", "Key": "a1/" + ARCHIVE}


def _staged(scope):
    # The runner writes the archive at the root of the run's outputS3AssetFilesPath.
    return f"pipelines/archive/j/output/{scope}/files/{ARCHIVE}"


class _ArchiveRerun:
    """Run e1 of a zipArchive run landed its archive in the asset; run e2 of the same zipArchive run
    produced an archive of exactly the same size (new bytes) and its copy job is prepared and fully
    copied, Complete not run yet. Run e2's job metadata also carries the shared token key with a
    foreign value, which the engine's own token must override."""

    def __init__(self, env):
        self.env = env
        s3 = env["s3"]
        first = self._start("e1", BODY, {})
        engine.complete(self._prepare_and_copy(first))
        self.old = s3.head_object(**DEST)
        self.old_token = loc.get_job_row(env["jobs"], first["jobId"])["destUploadToken"]
        self.new_body = bytes(reversed(BODY))
        self.job = self._start("e2", self.new_body, {TOKEN_KEY: "foreign-token"})
        self.pj = self._prepare_and_copy(self.job)
        env["audit"].reset_mock()
        env["lambda"].reset_mock()
        loc.update_job_row(env["jobs"], self.job["jobId"], {"executionArn": EXEC_ARN})

    def _start(self, scope, body, extra_metadata):
        self.env["s3"].put_object(Bucket="run-bucket", Key=_staged(scope), Body=body,
                                  ContentType="application/zip")
        job = loc.build_job(
            kind=loc.KIND_WORKFLOW_OUTPUT,
            source={"bucket": "run-bucket", "key": _staged(scope)},
            dest={"bucket": DEST["Bucket"], "key": DEST["Key"]},
            metadata={"databaseid": "db", "assetid": "a1", **extra_metadata}, content_headers={},
            database_id="db", asset_id="a1", upload_id="y1", upload_type="assetFile",
            relative_key=ARCHIVE, change_user_id="u1", scope_id=scope, workflow_execution_id=scope,
            workflow_id="utility-zip-archive", workflow_database_id="GLOBAL", batch_id=scope)
        starter = MagicMock(**{"start_execution.return_value": {"executionArn": f"arn-{scope}"}})
        loc.start_copy_job(self.env["jobs"], starter, "arn:aws:states:r:1:stateMachine:sm", job)
        return job

    @staticmethod
    def _prepare_and_copy(job):
        prepared = engine.prepare(job)
        for batch in prepared["batches"]:
            engine.copy_batch(prepared["job"], batch)
        return prepared["job"]

    def row(self):
        return loc.get_job_row(self.env["jobs"], self.job["jobId"])

    def complete_by_hand(self):
        s3 = self.env["s3"]
        parts = s3.list_parts(UploadId=self.pj["uploadIdS3"], **DEST)["Parts"]
        s3.complete_multipart_upload(
            UploadId=self.pj["uploadIdS3"], **DEST,
            MultipartUpload={"Parts": [{"PartNumber": p["PartNumber"], "ETag": p["ETag"]} for p in parts]})

    def cancel(self):
        self.env["sfn"].describe_execution.return_value = _described(
            self.job, status="ABORTED", error=loc.STOP_ERROR_CANCELLED)
        return loc.cancel_execution_copy_jobs(self.env["jobs"], self.env["sfn"], self.env["s3"], "e2")

    def console_stop(self):
        self.env["sfn"].describe_execution.return_value = _described(self.job, status="ABORTED", error="")

    def current(self):
        return self.env["s3"].head_object(**DEST)

    def holds_old(self):
        now = self.current()
        return now["ETag"] == self.old["ETag"] and now.get("VersionId") == self.old.get("VersionId")


@pytest.mark.unit
class TestArchiveRunLandsByTheEngineToken:
    def test_the_rerun_archive_has_the_size_the_old_rule_accepted(self, env, machine):
        run = _ArchiveRerun(env)
        row = run.row()
        assert run.old["ContentLength"] == int(row["sourceSize"]) == len(run.new_body)
        assert run.old["ETag"].endswith(f'-{int(row["partCount"])}"')
        # ...so only the token tells them apart.
        assert row["destUploadToken"] not in ("", "foreign-token", run.old_token)
        assert run.old["Metadata"][TOKEN_KEY] == run.old_token

    def test_cancel_before_complete_keeps_the_earlier_archive(self, env, machine):
        run = _ArchiveRerun(env)
        assert run.cancel() == []
        row = run.row()
        assert row["status"] == loc.STATUS_CANCELLED and "destVersionId" not in row
        assert run.holds_old()

    def test_console_stop_before_complete_keeps_the_earlier_archive(self, env, machine):
        run = _ArchiveRerun(env)
        run.console_stop()
        engine.reconcile(EXEC_ARN)
        row = run.row()
        assert row["status"] == loc.STATUS_FAILED and "destVersionId" not in row
        assert run.holds_old()

    def test_cancel_after_complete_landed_keeps_the_new_archive(self, env, machine):
        run = _ArchiveRerun(env)
        run.complete_by_hand()
        assert run.cancel() == []
        row = run.row()
        assert row["status"] == loc.STATUS_SUCCEEDED
        now = run.current()
        assert row["destVersionId"] == now["VersionId"] and not run.holds_old()
        # The landed object carries this job's token, not the foreign value in the job metadata.
        assert now["Metadata"][TOKEN_KEY] == row["destUploadToken"]
        assert env["s3"].get_object(**DEST)["Body"].read() == run.new_body
