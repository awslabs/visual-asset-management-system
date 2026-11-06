# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The large-file queue consumer hands files over the engine threshold to the copy engine (#1123),
building on #587's retry semantics (a retryable failure is raised so SQS redelivers and
dead-letters; a rejection is consumed).

- at or under the threshold (default 5 GiB): today's in-function copy, unchanged;
- over it: a copy job is started (upload kind, the staged object pinned) and nothing is copied,
  deleted or audited here, since the engine finishes the file;
- a job that cannot be started is retryable: the message is redelivered and the staged object kept;
- a message queued before this change for a workflow output (it carries workflowExecutionId) is
  handled the same way, with workflow provenance;
- with no engine configured (the variable is unset) every file keeps today's path."""

import os
from unittest.mock import MagicMock, patch

import pytest

from backend.tests.handlers.assets import test_sqsUploadFileLarge_retry_dead_letter as base

GIB = 1024 ** 3
ARN = "arn:aws:states:us-east-1:111122223333:stateMachine:LargeObjectCopy-x"


class _Env(base._Env):
    def __init__(self, size, arn=ARN, start_error=None, **kw):
        super().__init__(**kw)
        self.s3.head_object.return_value = {**self.s3.head_object.return_value,
                                             "ContentLength": size, "VersionId": "v1", "ETag": '"e"'}
        self.arn = arn
        self.start = MagicMock(side_effect=start_error)

    def __enter__(self):
        super().__enter__()
        sq = self.sq
        env = {sq.loc.ENV_STATE_MACHINE_ARN: self.arn} if self.arn else {}
        self._extra = [
            patch.dict(os.environ, env, clear=False),
            patch.object(sq, "copy_jobs_table", return_value=MagicMock()),
            patch.object(sq.loc, "start_copy_job", self.start),
        ]
        if not self.arn:
            os.environ.pop(sq.loc.ENV_STATE_MACHINE_ARN, None)
        for p in self._extra:
            p.start()
        return self

    def __exit__(self, *exc):
        for p in reversed(self._extra):
            p.stop()
        return super().__exit__(*exc)


def _run(env, file_info, receive_count=1):
    with env:
        return env.sq.lambda_handler(base._event(file_info, receive_count), base._context())


@pytest.mark.unit
class TestEngineHandOff:
    def test_a_file_at_the_threshold_is_copied_here_as_before(self):
        env = _Env(size=5 * GIB)
        _run(env, base._user_upload_file_info())
        env.start.assert_not_called()
        env.copy.assert_called_once()
        assert env.audit.call_count == 1

    def test_a_file_over_the_threshold_is_handed_to_the_engine(self):
        env = _Env(size=5 * GIB + 1)
        _run(env, base._user_upload_file_info())
        env.copy.assert_not_called()
        env.audit.assert_not_called()  # the engine writes the completion entry
        assert not [k for k in env.deleted_keys() if k[1].startswith("temp-uploads/")]
        job = env.start.call_args.args[3]
        assert job["kind"] == "upload"
        assert job["source"] == {"bucket": "asset-bucket", "key": "temp-uploads/up-1/scan.laz",
                                 "region": "", "versionId": "v1", "etag": '"e"', "size": 5 * GIB + 1}
        assert job["dest"]["key"] == "asset-1/big/scan.laz"
        assert job["metadata"]["vams-changesource"] == "upload"
        assert job["deleteSourceOnSuccess"] is True

    def test_a_job_that_cannot_start_is_retried_and_keeps_the_staged_object(self):
        env = _Env(size=8 * GIB, start_error=RuntimeError("throttled"))
        with pytest.raises(RuntimeError):
            _run(env, base._user_upload_file_info())
        assert env.deleted_keys() == []

    def test_a_queued_workflow_output_keeps_its_provenance(self):
        env = _Env(size=60 * GIB)
        _run(env, base._external_upload_file_info())
        job = env.start.call_args.args[3]
        assert job["source"]["bucket"] == "run-bucket"
        assert job["metadata"]["vams-changeworkflowexecutionid"] == "exec-1"
        assert job["batchId"] == ""  # no batch: the run that queued it has already ended

    def test_without_an_engine_every_file_keeps_todays_path(self):
        env = _Env(size=60 * GIB, arn="")
        _run(env, base._user_upload_file_info())
        env.start.assert_not_called()
        env.copy.assert_called_once()

    def test_a_blocked_type_is_still_rejected_before_any_hand_off(self):
        env = _Env(size=60 * GIB, extension_ok=False)
        _run(env, base._user_upload_file_info())
        env.start.assert_not_called()
