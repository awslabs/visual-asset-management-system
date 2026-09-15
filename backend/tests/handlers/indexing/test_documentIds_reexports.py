# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The indexers derive their document ids from ``common.indexing.documentIds`` and nowhere else.

``fileIndexer.build_file_document_id`` stays importable because ``test_fileIndexer_key_length.py``
addresses it there; ``assetIndexer`` binds ``build_asset_document_id``. Both modules read SSM at import,
so they are loaded by file path with boto3 stubbed, the way every live indexer test does.
"""

import ast
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
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_INDEXING_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing"
)

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


def _load_indexer(module_name):
    """Load a real indexer module by file path with boto3/SSM stubbed."""
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
                f"{module_name}_documentids_under_test",
                os.path.abspath(os.path.join(_INDEXING_DIR, f"{module_name}.py")),
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


@pytest.fixture
def fileIndexer():
    return _load_indexer("fileIndexer")


@pytest.fixture
def assetIndexer():
    return _load_indexer("assetIndexer")


@pytest.mark.unit
class TestFileIndexerReExport:
    def test_build_file_document_id_is_the_shared_function(self, fileIndexer):
        shared = sys.modules["common.indexing.documentIds"]
        assert fileIndexer.build_file_document_id is shared.build_file_document_id

    def test_ceiling_constant_is_re_exported(self, fileIndexer):
        assert fileIndexer.MAX_OPENSEARCH_DOCUMENT_ID_BYTES == 512

    def test_the_function_still_answers_the_key_length_tests_contract(self, fileIndexer):
        long_path = "/" + "d" * 990 + "/model.glb"
        doc_id = fileIndexer.build_file_document_id("db-1", "asset-1", long_path)
        assert len(doc_id.encode("utf-8")) == 512


@pytest.mark.unit
class TestAssetIndexerReExport:
    def test_build_asset_document_id_is_the_shared_function(self, assetIndexer):
        shared = sys.modules["common.indexing.documentIds"]
        assert assetIndexer.build_asset_document_id is shared.build_asset_document_id

    def test_index_path_uses_the_shared_id_for_an_archived_document(self, assetIndexer):
        """Drive index_asset_document with a stubbed OpenSearch manager and read the id it sent."""
        client = MagicMock()
        # index_asset_document returns ``response.get('result') in ['created', 'updated']``; an
        # unconfigured MagicMock answers a MagicMock there, and the membership test is False.
        client.index.return_value = {"result": "created"}
        manager = MagicMock()
        manager.is_available.return_value = True
        manager.get_client.return_value = client
        assetIndexer.opensearch_manager = manager
        document = MagicMock()
        document.str_databaseid = "db-1"
        document.str_assetid = "asset-1"
        document.bool_archived = True
        document.dict.return_value = {"str_databaseid": "db-1", "str_assetid": "asset-1"}
        assert assetIndexer.index_asset_document(document) is True
        assert client.index.call_args.kwargs["id"] == "db-1#deleted#asset-1"

    @pytest.mark.temporary  # pins the extraction of the inline asset _id f-strings from assetIndexer.py
    def test_no_inline_asset_id_f_string_remains(self):
        with open(os.path.abspath(os.path.join(_INDEXING_DIR, "assetIndexer.py")), encoding="utf-8") as handle:
            tree = ast.parse(handle.read())
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.JoinedStr):
                pieces = [p.value for p in node.values if isinstance(p, ast.Constant)]
                if any("#" == piece or piece.endswith("#") for piece in pieces) and any(
                    isinstance(p, ast.FormattedValue)
                    and isinstance(p.value, ast.Attribute)
                    and p.value.attr in ("str_assetid", "assetId")
                    for p in node.values
                ):
                    offenders.append(node.lineno)
                if any(piece == "#" for piece in pieces) and any(
                    isinstance(p, ast.FormattedValue)
                    and isinstance(p.value, ast.Name)
                    and p.value.id == "asset_id"
                    for p in node.values
                ):
                    offenders.append(node.lineno)
        assert offenders == [], f"assetIndexer.py still builds an asset _id inline at lines {offenders}"
