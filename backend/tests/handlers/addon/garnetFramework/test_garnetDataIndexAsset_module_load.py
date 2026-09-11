# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Per-resource-name module-load contract for the Garnet asset indexer.

S2-BACKEND-067: every resource name the asset indexer needs must be resolved
inside the one module-load `try/except` that re-raises, and every table must be
built unconditionally from the resolved name. When a name was swallowed into
`None` the module imported cleanly and the failure surfaced later inside a broad
per-record `except`, so the stream/S3 event was discarded with a 200/500 that the
SQS event-source mapping treats as processed.

`tests/handlers/addon/garnetFramework/test_garnet_module_load_and_sync_action.py`
makes every name unresolvable at once, which the first `get_table_name` call
already aborts on. These tests fail exactly one name at a time, so a name moved
back out of the shared block -- the partial-resolution shape, where only the
later tables are `None` -- is caught for each of the six names individually.

A fresh copy of the module is loaded by file path rather than reloaded: a failed
`importlib.reload` mutates the live module in place and would leave the other
tests in this directory running against a half-initialized module.
"""

import importlib
import importlib.util
import json
import os
from unittest.mock import MagicMock, patch

import pytest

_GARNET_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "..",
    "backend", "handlers", "addon", "garnetFramework",
)

# ResourceKeys attribute -> (resolved-name module attribute, table module attribute)
_REQUIRED = {
    "ASSET_STORAGE_TABLE": ("asset_storage_table_name", "asset_storage_table"),
    "ASSET_FILE_METADATA_STORAGE_TABLE": (
        "asset_file_metadata_storage_table_name", "asset_file_metadata_table"),
    "S3_ASSET_BUCKETS_STORAGE_TABLE": (
        "s3_asset_buckets_storage_table_name", "s3_asset_buckets_table"),
    "ASSET_LINKS_STORAGE_TABLE_V2": (
        "asset_links_storage_table_v2_name", "asset_links_table"),
    "ASSET_LINKS_METADATA_STORAGE_TABLE": (
        "asset_links_metadata_storage_table_name", "asset_links_metadata_table"),
    "ASSET_VERSIONS_STORAGE_TABLE": (
        "asset_versions_storage_table_name", "asset_versions_table"),
}


def _param_key(key_attr):
    """The SSM parameter suffix for a ResourceKeys entry, read from the module the
    handler will import. `common.resourceNames` is re-loaded per test by the root
    conftest, so the ResourceParamKey objects are matched on this stable string
    rather than by identity."""
    return getattr(importlib.import_module("common.resourceNames").ResourceKeys,
                   key_attr).param_key


def _resolved_name(param_key):
    return "resolved-" + param_key.rsplit("/", 1)[-1]


def _resolver(failing_param_key=None, error=None):
    """A `get_table_name` stand-in that resolves every name except one."""
    def get_table_name(key):
        if failing_param_key is not None and key.param_key == failing_param_key:
            raise error
        return _resolved_name(key.param_key)
    return get_table_name


def _stream_record(source_arn):
    """One SQS record carrying the SNS-wrapped DynamoDB stream notification the
    indexer is subscribed to."""
    return {
        "eventSource": "aws:sqs",
        "body": json.dumps({
            "Type": "Notification",
            "Message": json.dumps({"eventSourceARN": source_arn, "eventName": "MODIFY"}),
        }),
    }


def _stream_arn(table_name):
    return (f"arn:aws:dynamodb:us-east-1:123456789012:table/{table_name}"
            "/stream/2026-01-01T00:00:00.000")


def _load_fresh(unique_suffix):
    """Load an independent copy of the asset indexer by file path."""
    path = os.path.abspath(os.path.join(_GARNET_DIR, "garnetDataIndexAsset.py"))
    spec = importlib.util.spec_from_file_location(
        f"garnetDataIndexAsset_module_load_{unique_suffix}", path)
    module = importlib.util.module_from_spec(spec)
    resource = MagicMock()
    with patch("boto3.resource", return_value=resource), \
            patch("boto3.client", return_value=MagicMock()):
        spec.loader.exec_module(module)
    return module, resource


@pytest.mark.unit
class TestAssetIndexerModuleLoadFailsClosed:
    """S2-BACKEND-067: one unresolvable name must abort the cold start."""

    @pytest.mark.parametrize("key_attr", sorted(_REQUIRED))
    def test_import_raises_when_a_single_resource_name_is_unresolvable(self, key_attr):
        param_key = _param_key(key_attr)
        error = RuntimeError(f"SSM unreachable for {param_key}")
        with patch("common.resourceNames.get_table_name",
                   side_effect=_resolver(param_key, error)):
            with pytest.raises(RuntimeError) as excinfo:
                _load_fresh(f"fail_{key_attr}")
        assert param_key in str(excinfo.value), \
            "the original resolution failure must propagate unchanged"

    def test_import_raises_when_a_required_env_var_is_missing(self):
        """The queue URL and API endpoint share the block, so the whole block --
        not only the SSM reads -- fails the cold start."""
        with patch("common.resourceNames.get_table_name", side_effect=_resolver()):
            with patch.dict(os.environ):
                os.environ.pop("GARNET_INGESTION_QUEUE_URL", None)
                with pytest.raises(KeyError):
                    _load_fresh("fail_env")

    def test_import_builds_every_table_from_its_resolved_name(self):
        """Positive control for the tests above: with every name resolvable the
        module imports, each table is built, and each is built from its OWN
        resolved name. Without this a constructor error unrelated to resolution
        would make the negative tests pass for the wrong reason, and a name
        silently dropped from the block would go unnoticed."""
        with patch("common.resourceNames.get_table_name", side_effect=_resolver()):
            module, resource = _load_fresh("ok")

        constructed = [call.args[0] for call in resource.Table.call_args_list]
        for key_attr, (name_attr, table_attr) in _REQUIRED.items():
            expected = _resolved_name(_param_key(key_attr))
            assert getattr(module, name_attr) == expected
            assert getattr(module, table_attr) is not None, f"{table_attr} resolved to None"
            assert expected in constructed, f"{table_attr} was not built from {expected}"

    def test_resolved_asset_storage_name_is_the_source_arn_dispatch_operand(self):
        """`lambda_handler` routes a stream record by testing the resolved name
        against the record's `eventSourceARN`, so the name must be a string. The
        third arm drives the swallowed-name shape directly: `None in source_arn`
        raises TypeError, which the per-record `except json.JSONDecodeError` does
        not catch, so the whole batch is answered 500 and every record in it is
        deleted by the event-source mapping unprocessed."""
        with patch("common.resourceNames.get_table_name", side_effect=_resolver()):
            module, _ = _load_fresh("dispatch")
        name = module.asset_storage_table_name

        with patch.object(module, "handle_asset_stream", return_value=True) as routed:
            response = module.lambda_handler(
                {"Records": [_stream_record(_stream_arn(name))]}, MagicMock())
        assert routed.call_count == 1, "the resolved name did not match its own stream ARN"
        assert response["statusCode"] == 200
        assert response["body"]["successful_records"] == 1

        # Control: the dispatch is driven by the name, not taken unconditionally.
        with patch.object(module, "handle_asset_stream", return_value=True) as routed:
            response = module.lambda_handler(
                {"Records": [_stream_record(_stream_arn("some-other-table"))]}, MagicMock())
        assert routed.call_count == 0
        assert response["body"]["failed_records"] == 1

        module.asset_storage_table_name = None
        with patch.object(module, "handle_asset_stream", return_value=True) as routed:
            response = module.lambda_handler(
                {"Records": [_stream_record(_stream_arn(name))]}, MagicMock())
        assert routed.call_count == 0
        assert response["statusCode"] == 500
