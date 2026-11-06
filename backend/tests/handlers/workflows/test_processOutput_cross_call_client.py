# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The output step's synchronous cross-calls are sent once (#1205).

The file completion and the metadata service were invoked with botocore's default 60 s read timeout
and retries, so a completion that ran 82 s was sent a second and a third time while the first still
ran, and the run was recorded FAILED although its files landed. The synchronous client now has a
read timeout above the invoked functions' 900 s timeout and no automatic retry; only a throttle,
which Lambda refuses before the function runs, is retried.

TestNoSecondInvokeOnTimeout drives the real botocore client against a local endpoint that accepts
the request and never answers: with the step's synchronous retry setting the request is sent once,
where the asynchronous client's setting (the one the cross-calls used) sends it again.
"""

import socket
import threading

import pytest
from botocore.config import Config
from botocore.exceptions import ClientError, ReadTimeoutError
from unittest.mock import MagicMock, patch

import os

import boto3

for _k, _v in {
    "WORKFLOW_STORAGE_TABLE_V2_NAME": "t-wf-v2",
    "WORKFLOW_EXECUTION_INPUTS_STORAGE_TABLE_NAME": "t-inputs",
    "WORKFLOW_EXECUTION_LOCKS_STORAGE_TABLE_NAME": "t-locks",
    "WORKFLOW_EXECUTION_CONFIGURATION_STORAGE_TABLE_NAME": "t-config",
    "LARGE_OBJECT_COPY_JOBS_STORAGE_TABLE_NAME": "t-copy-jobs",
}.items():
    os.environ.setdefault(_k, _v)

from backend.tests.handlers.workflows import test_processOutput_ingestion_outcome as base  # noqa: E402

po = base.po


class _SilentEndpoint:
    """A local HTTP endpoint that accepts every request and never answers; counts the requests."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(16)
        self.requests = 0
        self.connections = []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return f"http://127.0.0.1:{self.sock.getsockname()[1]}"

    def _serve(self):
        self.sock.settimeout(0.1)
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except socket.timeout:
                continue
            self.connections.append(conn)
            conn.settimeout(2)
            try:
                if conn.recv(65536):
                    self.requests += 1
            except socket.timeout:
                pass

    def close(self):
        self.stop.set()
        self.thread.join(2)
        for conn in self.connections:
            conn.close()
        self.sock.close()


def _client(config, endpoint):
    # The step's own retry setting, with a short read timeout so the test does not wait 910 s.
    return boto3.client("lambda", region_name="us-east-1", endpoint_url=endpoint.url,
                        aws_access_key_id="x", aws_secret_access_key="x",
                        config=config.merge(Config(read_timeout=1, connect_timeout=1)))


@pytest.mark.unit
class TestTheSynchronousClientConfig:
    def test_read_timeout_is_above_the_invoked_functions_timeout(self):
        config = po.invoke_client.meta.config
        assert config.read_timeout == po.CROSS_CALL_READ_TIMEOUT_SECONDS > 900

    def test_botocore_does_not_retry_it(self):
        assert po.invoke_client.meta.config.retries["total_max_attempts"] == 1

    def test_the_cross_calls_use_it(self):
        with patch.object(po, "invoke_client") as invoke_client, patch.object(po, "client") as event_client:
            po._lambda_file_ingestion({"a": 1})
            po._lambda_metadata_service({"b": 2})
        assert [c.kwargs["FunctionName"] for c in invoke_client.invoke.call_args_list] == [
            po.file_upload_function, po.metadata_service_function]
        assert {c.kwargs["InvocationType"] for c in invoke_client.invoke.call_args_list} == {"RequestResponse"}
        event_client.invoke.assert_not_called()


@pytest.mark.unit
class TestNoSecondInvokeOnTimeout:
    def test_a_timed_out_completion_is_sent_once(self):
        endpoint = _SilentEndpoint()
        try:
            client = _client(po.invoke_client.meta.config, endpoint)
            with patch.object(po, "invoke_client", client), pytest.raises(ReadTimeoutError):
                po._lambda_file_ingestion({"body": "{}"})
        finally:
            endpoint.close()
        assert endpoint.requests == 1

    def test_the_old_retrying_setting_sent_it_again(self):
        # The control: the setting the cross-calls used to share retries a read timeout.
        endpoint = _SilentEndpoint()
        try:
            client = _client(Config(retries={"max_attempts": 2, "mode": "standard"}), endpoint)
            with pytest.raises(ReadTimeoutError):
                client.invoke(FunctionName="f", InvocationType="RequestResponse", Payload=b"{}")
        finally:
            endpoint.close()
        assert endpoint.requests > 1

    def test_a_timed_out_completion_fails_the_write_back_without_a_repeat(self):
        invoke_client = MagicMock()
        invoke_client.invoke.side_effect = ReadTimeoutError(endpoint_url="https://lambda")
        with patch.object(po, "invoke_client", invoke_client):
            result = po.process_external_upload(
                "upl-1", "asset1", "db1", "assetFile", ["a/model.glb"], "a/", {"http": {}})
        assert result is None
        invoke_client.invoke.assert_called_once()


@pytest.mark.unit
class TestAThrottleIsRetried:
    def _throttle(self):
        return ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "Rate exceeded"}},
                           "Invoke")

    def test_a_throttled_invoke_is_sent_again(self):
        invoke_client = MagicMock()
        invoke_client.invoke.side_effect = [self._throttle(), self._throttle(), {"Payload": "ok"}]
        with patch.object(po, "invoke_client", invoke_client), patch.object(po.time, "sleep"):
            assert po._lambda_file_ingestion({}) == {"Payload": "ok"}
        assert invoke_client.invoke.call_count == 3

    def test_throttling_stops_after_the_bound(self):
        invoke_client = MagicMock()
        invoke_client.invoke.side_effect = self._throttle()
        with patch.object(po, "invoke_client", invoke_client), patch.object(po.time, "sleep"), \
                pytest.raises(ClientError):
            po._lambda_file_ingestion({})
        assert invoke_client.invoke.call_count == po.CROSS_CALL_THROTTLE_ATTEMPTS

    def test_another_error_is_not_retried(self):
        invoke_client = MagicMock()
        invoke_client.invoke.side_effect = ClientError(
            {"Error": {"Code": "ServiceException", "Message": "x"}}, "Invoke")
        with patch.object(po, "invoke_client", invoke_client), pytest.raises(ClientError):
            po._lambda_file_ingestion({})
        invoke_client.invoke.assert_called_once()
