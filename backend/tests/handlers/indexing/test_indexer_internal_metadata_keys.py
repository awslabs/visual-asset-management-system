# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The indexers leave VAMS_ and leading-underscore metadata keys out of the search documents.

common.dynamoDbMetadataKeys.is_internal_metadata_field marks those keys as internal. The asset
indexer's metadata read and both of the file indexer's reads (file metadata and file attributes)
skip them, and a record without a key is skipped without cutting the read short.
"""

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_V2_NAME", "test-links-table")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_INDEXING_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing"
)


def _boto_client(name, *args, **kwargs):
    client = MagicMock()
    if name == "ssm":
        client.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}
    return client


def _load_indexer(module_name):
    saved = {name: sys.modules.get(name) for name in ("handlers.auth", "handlers.authz")}
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["mock_token"]})
    sys.modules["handlers.auth"] = auth_stub
    try:
        with patch("boto3.client", side_effect=_boto_client), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                f"{module_name}_internal_keys_under_test",
                os.path.abspath(os.path.join(_INDEXING_DIR, f"{module_name}.py")),
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


class _OnePageTable:
    def __init__(self, items):
        self._items = items

    def query(self, **kwargs):
        return {"Items": list(self._items)}


def _metadata_row(key, value):
    return {"metadataKey": key, "metadataValue": value, "metadataValueType": "string"}


def _attribute_row(key, value):
    return {"attributeKey": key, "attributeValue": value, "attributeValueType": "string"}


# A keyless record sits between the internal keys and the visible one, so a skip that raised on
# it would end the read before 'visible' is reached.
METADATA_ROWS = [
    _metadata_row("_hidden", "x"),
    _metadata_row("VAMS_k", "y"),
    {"metadataValue": "orphan", "metadataValueType": "string"},
    _metadata_row("visible", "z"),
]
ATTRIBUTE_ROWS = [
    _attribute_row("_hidden", "x"),
    _attribute_row("VAMS_k", "y"),
    {"attributeValue": "orphan", "attributeValueType": "string"},
    _attribute_row("visible", "z"),
]


@pytest.mark.unit
class TestInternalMetadataKeysAreNotIndexed:
    def test_asset_metadata_holds_only_visible_keys(self):
        m = _load_indexer("assetIndexer")
        with patch.object(m, "asset_file_metadata_table", _OnePageTable(METADATA_ROWS)):
            metadata = m.get_asset_metadata("db1", "a1")
        assert metadata == {"visible": "z"}

    def test_file_metadata_and_attributes_hold_only_visible_keys(self):
        m = _load_indexer("fileIndexer")
        with patch.object(m, "asset_file_metadata_table", _OnePageTable(METADATA_ROWS)), \
                patch.object(m, "file_attribute_table", _OnePageTable(ATTRIBUTE_ROWS)):
            metadata, attributes = m.get_file_metadata("db1", "a1", "/part.stp")
        assert metadata == {"visible": "z"}
        assert attributes == {"visible": "z"}

    def test_lowercase_vams_prefix_is_an_ordinary_key(self):
        m = _load_indexer("assetIndexer")
        rows = [_metadata_row("vams_note", "kept")]
        with patch.object(m, "asset_file_metadata_table", _OnePageTable(rows)):
            assert m.get_asset_metadata("db1", "a1") == {"vams_note": "kept"}
