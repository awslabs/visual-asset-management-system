# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The zip service's synchronous cross-calls are sent once (#1125 in-dev D8, the #1205 rule).

The launch of the zipArchive workflow, the abort and the details read were invoked with the
retrying client (botocore's 60 s read timeout, five adaptive attempts). A launch that ran longer, or
whose answer was lost, was sent again and started a second zipArchive run that the job row never
recorded. The client now has a read timeout above the invoked functions' 900 s timeout and no
automatic retry; only a throttle, which Lambda refuses before the function runs, is retried.
"""

import json
from unittest.mock import MagicMock, patch

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError

from backend.tests.handlers.workflows.test_processOutput_cross_call_client import _SilentEndpoint
from backend.tests.handlers.zipDownloads import zipHarness as h

pytestmark = pytest.mark.unit


@pytest.fixture
def env():
    h.AUTH.__init__()
    e = h.load_handlers()
    h.put_asset(e)
    e.s3.add("xasset1/a.glb", size=100, version="va1")
    return e


def _throttle():
    return ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "Rate exceeded"}},
                       "Invoke")


def _ok(execution_id="exec-1"):
    payload = MagicMock()
    payload.read.return_value = json.dumps({"statusCode": 200, "body": json.dumps(
        {"message": {"executionId": execution_id}})}).encode("utf-8")
    return {"Payload": payload}


def _row():
    return {"requestId": "r1", "userId": "alice", "databaseId": "db1", "assetId": "asset1",
            "manifestKey": "vams.preparedDownloads/db1/asset1/h/r1/manifest.json"}


def _create(e):
    h.AUTH.user = "alice"
    return e.svc.lambda_handler(h.api_event(
        "POST", h.asset_path("db1", "asset1"), {"databaseId": "db1", "assetId": "asset1"},
        body={"selection": [{"type": "file", "relativePath": "/a.glb"}]}), None)


class TestTheClientConfig:
    def test_read_timeout_is_above_the_invoked_functions_timeout(self, env):
        assert env.svc.invoke_config.read_timeout == env.svc.CROSS_CALL_READ_TIMEOUT_SECONDS > 900

    def test_botocore_does_not_retry_it(self, env):
        assert env.svc.invoke_config.retries["total_max_attempts"] == 1

    def test_a_timed_out_launch_is_sent_once(self, env):
        endpoint = _SilentEndpoint()
        try:
            # The service's own retry setting, with a short read timeout so the test does not wait 910 s.
            client = boto3.client("lambda", region_name="us-east-1", endpoint_url=endpoint.url,
                                  aws_access_key_id="x", aws_secret_access_key="x",
                                  config=env.svc.invoke_config.merge(Config(read_timeout=1, connect_timeout=1)))
            env.svc.lambda_client = client
            with pytest.raises(ReadTimeoutError):
                env.svc._launch(_row(), [{"relativePath": "/a.glb"}], False)
        finally:
            endpoint.close()
        assert endpoint.requests == 1


class TestEveryCrossCallIsSentOnce:
    def test_a_timed_out_launch_fails_the_job_without_a_second_run(self, env):
        lam = MagicMock()
        lam.invoke.side_effect = ReadTimeoutError(endpoint_url="https://lambda")
        env.svc.lambda_client = lam
        response = _create(env)
        lam.invoke.assert_called_once()
        assert lam.invoke.call_args.kwargs["FunctionName"] == "executeFn"
        assert response["statusCode"] == 500
        row = next(iter(env.zip.items.values()))
        assert row["status"] == "FAILED" and row["errorMessage"] == env.zd.MSG_LAUNCH_FAILED

    @pytest.mark.parametrize("call", ["abort", "details"])
    def test_the_abort_and_the_details_read_are_sent_once(self, env, call):
        lam = MagicMock()
        lam.invoke.side_effect = ReadTimeoutError(endpoint_url="https://lambda")
        env.svc.lambda_client = lam
        fn = env.svc._abort_execution if call == "abort" else env.svc._check_run_ended
        assert fn("exec-1") is False
        lam.invoke.assert_called_once()
        assert lam.invoke.call_args.kwargs["InvocationType"] == "RequestResponse"


class TestAThrottleIsRetried:
    def test_a_throttled_launch_is_sent_again(self, env):
        lam = MagicMock()
        lam.invoke.side_effect = [_throttle(), _throttle(), _ok("exec-9")]
        env.svc.lambda_client = lam
        with patch.object(env.svc.time, "sleep"):
            assert env.svc._launch(_row(), [{"relativePath": "/a.glb"}], False) == "exec-9"
        assert lam.invoke.call_count == 3

    def test_throttling_stops_after_the_bound(self, env):
        lam = MagicMock()
        lam.invoke.side_effect = _throttle()
        env.svc.lambda_client = lam
        with patch.object(env.svc.time, "sleep"), pytest.raises(ClientError):
            env.svc._launch(_row(), [{"relativePath": "/a.glb"}], False)
        assert lam.invoke.call_count == env.svc.CROSS_CALL_THROTTLE_ATTEMPTS

    def test_another_error_is_not_retried(self, env):
        lam = MagicMock()
        lam.invoke.side_effect = ClientError({"Error": {"Code": "ServiceException", "Message": "x"}},
                                             "Invoke")
        env.svc.lambda_client = lam
        with pytest.raises(ClientError):
            env.svc._launch(_row(), [{"relativePath": "/a.glb"}], False)
        lam.invoke.assert_called_once()
