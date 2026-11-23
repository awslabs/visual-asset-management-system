# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The OpenSearch indexers read their index name and endpoint on first use, bounded, cached (#1820).

Both indexers read the two values at import, with Rule 6's client (five adaptive attempts, 60 s
timeouts), and built the OpenSearch client at import from them. Under Parameter Store throttling the
reads ran toward Lambda's 10-second init limit, and Lambda ran init again inside the invoke. The reads
now happen when a batch is handled, through common.ssmParameters: a read that cannot finish fails the
whole batch before any record is handled, every record is reported for redelivery, and the container
backs off before it reads again.

Each indexer is loaded by path with ``boto3.client`` replaced by a recorder; the helper the indexer
reads through is this test's fresh ``common.ssmParameters``, on a fake clock.
"""

import importlib.util
import itertools
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.tests.ssmParametersStub import FakeClock, install_clock

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-links-table")

SSM_ENV = {
    "OPENSEARCH_FILE_INDEX_SSM_PARAM": "/test/file-index",
    "OPENSEARCH_ASSET_INDEX_SSM_PARAM": "/test/asset-index",
    "OPENSEARCH_ENDPOINT_SSM_PARAM": "/test/endpoint",
}

_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing")
_loads = itertools.count()
INDEXERS = ["assetIndexer", "fileIndexer"]


def _always_throttled(Name):
    raise ClientError({"Error": {"Code": "ThrottlingException", "Message": "Rate exceeded"},
                       "ResponseMetadata": {"HTTPStatusCode": 400}}, "GetParameter")


def _answer(Name):
    return {"Parameter": {"Value": f"value-of-{Name}"}}


class _Recorder:
    """Stands in for ``boto3.client``: every service gets a MagicMock, SSM one shared stub."""

    def __init__(self):
        self.ssm = MagicMock()
        self.ssm.get_parameter.side_effect = _answer

    def __call__(self, name, *args, **kwargs):
        return self.ssm if name == "ssm" else MagicMock()


def _load(module_name, monkeypatch, recorder):
    for key, value in SSM_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("OPENSEARCH_TYPE", "provisioned")
    authz = types.ModuleType("handlers.authz")
    authz.CasbinEnforcer = MagicMock()
    auth = types.ModuleType("handlers.auth")
    auth.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    monkeypatch.setitem(sys.modules, "handlers.authz", authz)
    monkeypatch.setitem(sys.modules, "handlers.auth", auth)
    with patch("boto3.client", side_effect=recorder), patch("boto3.resource", return_value=MagicMock()):
        spec = importlib.util.spec_from_file_location(
            f"{module_name}_parameter_reads_{next(_loads)}", os.path.abspath(os.path.join(_DIR, f"{module_name}.py")))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
    return module


def _batch(*message_ids):
    """SQS records whose body is no SNS notification: handled without touching OpenSearch."""
    return {"Records": [{"messageId": message_id, "eventSource": "aws:sqs", "body": "{}"}
                        for message_id in message_ids]}


def _failed_ids(response):
    return sorted(item["itemIdentifier"] for item in response.get("batchItemFailures", []))


@pytest.mark.unit
@pytest.mark.parametrize("module_name", INDEXERS)
class TestNoReadAtImport:
    def test_importing_the_indexer_reads_no_parameter(self, module_name, monkeypatch):
        recorder = _Recorder()
        _load(module_name, monkeypatch, recorder)
        assert recorder.ssm.get_parameter.call_count == 0, "the indexer read Parameter Store while importing"

    def test_importing_the_indexer_creates_no_opensearch_client(self, module_name, monkeypatch):
        """The client is built from the endpoint, so it waits for the first use too."""
        opensearchpy = MagicMock()
        with patch.dict(sys.modules, {"opensearchpy": opensearchpy}):
            module = _load(module_name, monkeypatch, _Recorder())
        assert module.opensearch_manager._client is None
        opensearchpy.OpenSearch.assert_not_called()


@pytest.mark.unit
@pytest.mark.parametrize("module_name", INDEXERS)
class TestThrottledFirstUse:
    @pytest.fixture
    def throttled(self, module_name, monkeypatch):
        recorder = _Recorder()
        module = _load(module_name, monkeypatch, recorder)
        recorder.ssm.get_parameter.side_effect = _always_throttled
        clock = install_clock(sys.modules["common.ssmParameters"], FakeClock(), monkeypatch)
        return module, recorder, clock

    def test_the_whole_batch_is_reported_failed_and_nothing_is_handled(self, throttled, monkeypatch):
        module, recorder, clock = throttled
        handled = MagicMock(side_effect=AssertionError("a record was handled without the index settings"))
        for name in ("handle_asset_stream", "handle_metadata_stream", "handle_asset_links_stream",
                     "handle_s3_notification"):
            if hasattr(module, name):
                monkeypatch.setattr(module, name, handled)
        started = clock.now

        with patch("boto3.client", side_effect=recorder):
            response = module.lambda_handler(_batch("m1", "m2"), None)

        assert _failed_ids(response) == ["m1", "m2"]
        handled.assert_not_called()
        assert recorder.ssm.get_parameter.call_count > 1, "the throttled read was never retried"
        assert clock.now - started <= sys.modules["common.ssmParameters"].PARAMETER_READ_BUDGET_SECONDS

    def test_the_next_batch_inside_the_back_off_window_reads_nothing(self, throttled):
        module, recorder, _ = throttled
        with patch("boto3.client", side_effect=recorder):
            module.lambda_handler(_batch("m1"), None)
            calls = recorder.ssm.get_parameter.call_count
            response = module.lambda_handler(_batch("m2", "m3"), None)
        assert _failed_ids(response) == ["m2", "m3"]
        assert recorder.ssm.get_parameter.call_count == calls

    def test_the_batch_after_the_window_reads_again_and_is_handled(self, throttled):
        module, recorder, clock = throttled
        with patch("boto3.client", side_effect=recorder):
            module.lambda_handler(_batch("m1"), None)
            clock.advance(60)
            recorder.ssm.get_parameter.side_effect = _answer
            response = module.lambda_handler(_batch("m2"), None)
        assert _failed_ids(response) == []
        assert module.opensearch_endpoint == "value-of-/test/endpoint"


@pytest.mark.unit
@pytest.mark.parametrize("module_name", INDEXERS)
class TestReadOncePerContainer:
    def test_the_two_values_are_read_on_the_first_batch_and_cached(self, module_name, monkeypatch):
        recorder = _Recorder()
        module = _load(module_name, monkeypatch, recorder)
        with patch("boto3.client", side_effect=recorder):
            first = module.lambda_handler(_batch("m1"), None)
            second = module.lambda_handler(_batch("m2"), None)

        assert _failed_ids(first) == _failed_ids(second) == []
        names = sorted(call.kwargs["Name"] for call in recorder.ssm.get_parameter.call_args_list)
        index_param = "/test/asset-index" if module_name == "assetIndexer" else "/test/file-index"
        assert names == sorted(["/test/endpoint", index_param])
        index = module.opensearch_asset_index if module_name == "assetIndexer" else module.opensearch_file_index
        assert index == f"value-of-{index_param}"

    def test_the_opensearch_client_is_created_on_first_use(self, module_name, monkeypatch):
        recorder = _Recorder()
        module = _load(module_name, monkeypatch, recorder)
        opensearchpy = MagicMock()
        with patch("boto3.client", side_effect=recorder), patch.dict(sys.modules, {"opensearchpy": opensearchpy}):
            assert module.opensearch_manager.is_available() is True
        hosts = opensearchpy.OpenSearch.call_args.kwargs["hosts"]
        assert hosts == [{"host": "value-of-/test/endpoint", "port": 443}]

    def test_a_client_that_cannot_read_its_endpoint_is_unavailable(self, module_name, monkeypatch):
        recorder = _Recorder()
        module = _load(module_name, monkeypatch, recorder)
        recorder.ssm.get_parameter.side_effect = _always_throttled
        install_clock(sys.modules["common.ssmParameters"], FakeClock(), monkeypatch)
        with patch("boto3.client", side_effect=recorder), patch.dict(sys.modules, {"opensearchpy": MagicMock()}):
            assert module.opensearch_manager.is_available() is False
        assert module.opensearch_endpoint is None
