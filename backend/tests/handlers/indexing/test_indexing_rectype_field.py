# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The record-type discriminator must actually reach the indexed document.

``FileDocumentModel`` and ``AssetDocumentModel`` carry the discriminator as
``str_rectype``. A leading-underscore name cannot be used: pydantic v1 does not
treat one as a field -- it never appears in ``__fields__`` and never serializes,
so the attribute resolves to the raw ``FieldInfo`` object rather than a value.

The assertions run against the body the indexer actually sends to OpenSearch,
because the declaration alone proves nothing twice over: an underscore name is
not a field at all, and the indexer serializes with ``dict(exclude_unset=True)``,
which drops any field left at its default. The models therefore stamp the value
in a ``pre=True`` root validator so it is always in ``__fields_set__``.

Guards FIX-060 (S2-BACKEND-070): a leading-underscore ``_rectype`` declared with ``Field()`` is
not a pydantic v1 field, so the discriminator never reaches an indexed document.
"""

import importlib.util
import os
import re
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Env vars the indexers read at import time; SSM is stubbed in the fixtures below.
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_METADATA_STORAGE_TABLE_NAME", "test-file-metadata-table")
os.environ.setdefault("FILE_ATTRIBUTE_STORAGE_TABLE_NAME", "test-file-attr-table")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_VERSIONS_STORAGE_TABLE_NAME", "test-asset-versions-table")
os.environ.setdefault("ASSET_LINKS_STORAGE_TABLE_NAME", "test-links-table")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_ssm_stub = MagicMock()
_ssm_stub.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}


def _boto_client(name, *args, **kwargs):
    if name == "ssm":
        return _ssm_stub
    return MagicMock()


_INDEXING_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "indexing"
)
_SEARCH_SOURCE = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "search", "search.py"
)

# Any field name that carries the record-type discriminator. Matched loosely rather
# than pinned to 'str_rectype' so a rename shows up as a mismatch between the
# document, the mapping and the search field sets instead of as an absence.
_RECTYPE_NAME = re.compile(r"rectype", re.IGNORECASE)


def _load_indexer(module_name):
    """Load a real indexer module by file path with boto3/SSM stubbed.

    The mock `handlers` / `common` packages the root conftest registers shadow the
    real packages, so a normal import cannot reach the real module.
    """
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
                f"{module_name}_rectype_under_test",
                os.path.abspath(os.path.join(_INDEXING_DIR, f"{module_name}.py")),
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
    return module


def _captured_index_body(module, index_callable, document):
    """Run the module's index call against a stub OpenSearch client and return the body."""
    client = MagicMock()
    manager = MagicMock()
    manager.is_available.return_value = True
    manager.get_client.return_value = client
    with patch.object(module, "opensearch_manager", manager):
        index_callable(document)
    assert client.index.called, "the indexer never called client.index"
    return client.index.call_args.kwargs["body"]


def _rectype_entries(body):
    return {key: value for key, value in body.items() if _RECTYPE_NAME.search(key)}


@pytest.fixture
def file_indexer():
    return _load_indexer("fileIndexer")


@pytest.fixture
def asset_indexer():
    return _load_indexer("assetIndexer")


def _file_document(module):
    from models.indexing import FileIndexRequest
    request = FileIndexRequest(
        databaseId="db1", assetId="a1", filePath="/scans/pump.e57",
        bucketName="asset-bucket", s3Key="a1/scans/pump.e57", operation="index",
    )
    with patch.object(module, "find_preview_file_key", return_value=""):
        return module.build_file_document(
            request,
            {"assetName": "Pump", "tags": []},
            {"bucketId": "bucket-1", "bucketName": "asset-bucket", "baseAssetsPrefix": ""},
            {}, {}, None, False,
        )


def _asset_document(module):
    from models.indexing import AssetIndexRequest
    request = AssetIndexRequest(databaseId="db1", assetId="a1", operation="index")
    return module.build_asset_document(
        request,
        {"assetName": "Pump", "assetType": "e57", "description": "d",
         "isDistributable": True, "tags": []},
        {"bucketId": "bucket-1", "bucketName": "asset-bucket", "baseAssetsPrefix": ""},
        {}, {}, {}, False,
    )


@pytest.mark.unit
class TestIndexedBodyCarriesRecordType:
    """The discriminator must reach OpenSearch on every indexed document."""

    def test_file_document_body_carries_record_type_file(self, file_indexer):
        """The body sent to OpenSearch must identify the record as a file."""
        body = _captured_index_body(
            file_indexer, file_indexer.index_file_document, _file_document(file_indexer))
        entries = _rectype_entries(body)

        assert entries, f"no record-type field in the indexed file body; keys={sorted(body)}"
        assert set(entries.values()) == {"file"}, (
            f"record-type field(s) {entries} do not identify the document as a file"
        )

    def test_asset_document_body_carries_record_type_asset(self, asset_indexer):
        """The body sent to OpenSearch must identify the record as an asset."""
        body = _captured_index_body(
            asset_indexer, asset_indexer.index_asset_document, _asset_document(asset_indexer))
        entries = _rectype_entries(body)

        assert entries, f"no record-type field in the indexed asset body; keys={sorted(body)}"
        assert set(entries.values()) == {"asset"}, (
            f"record-type field(s) {entries} do not identify the document as an asset"
        )

    def test_indexed_body_is_not_empty(self, file_indexer):
        """Control: proves the capture path works, so a missing key is a real absence.

        Without this, a broken opensearch_manager patch would make the assertions
        above fail for the wrong reason.
        """
        body = _captured_index_body(
            file_indexer, file_indexer.index_file_document, _file_document(file_indexer))
        assert body.get("str_key") == "/scans/pump.e57"
        assert body.get("str_databaseid") == "db1"
        assert body.get("str_assetid") == "a1"


@pytest.mark.unit
class TestRecordTypeDeclarationsAgree:
    """The mapping and the search field sets must name the same key as the document.

    One spelling has to hold across all three, so a rename that misses the mapping
    or the search core-field set leaves a different half broken.
    """

    def test_mappings_declare_exactly_one_record_type_property(self):
        """Control: the mappings' side of the contract, which is intact today.

        Keeps the consistency test below honest -- if the mapping property were
        missing too, that test could pass vacuously.
        """
        from models.indexing import FileIndexMapping, AssetIndexMapping
        for mapping_cls in (FileIndexMapping, AssetIndexMapping):
            properties = mapping_cls.get_mapping()["mappings"]["properties"]
            names = [name for name in properties if _RECTYPE_NAME.search(name)]
            assert len(names) == 1, (
                f"{mapping_cls.__name__} declares record-type properties {names}"
            )

    def test_document_record_type_key_matches_the_index_mapping(self, file_indexer):
        """The key in the document must be the key the index maps."""
        from models.indexing import FileIndexMapping
        body = _captured_index_body(
            file_indexer, file_indexer.index_file_document, _file_document(file_indexer))
        document_keys = set(_rectype_entries(body))
        mapped_keys = {name for name in
                       FileIndexMapping.get_mapping()["mappings"]["properties"]
                       if _RECTYPE_NAME.search(name)}

        assert document_keys == mapped_keys, (
            f"document record-type key(s) {document_keys} do not match the mapped "
            f"key(s) {mapped_keys}"
        )

    def test_search_core_fields_reference_the_document_key(self, file_indexer):
        """The search layer's core-field sets must name the live key.

        Read from source text rather than by importing search.py, which bootstraps
        an OpenSearch client at module load.
        """
        body = _captured_index_body(
            file_indexer, file_indexer.index_file_document, _file_document(file_indexer))
        document_keys = set(_rectype_entries(body))
        assert document_keys, (
            "the indexed document carries no record-type key, so search.py's "
            "`_rectype` references are dead"
        )

        with open(os.path.abspath(_SEARCH_SOURCE), "r", encoding="utf-8") as handle:
            search_source = handle.read()
        for key in document_keys:
            assert f"'{key}'" in search_source or f'"{key}"' in search_source, (
                f"search.py does not reference the document's record-type key {key!r}"
            )


@pytest.mark.unit
class TestRecordTypeIsARealField:
    """The declaration mechanism, which is what made the field inert.

    Guards the two independent ways the value can vanish again: a name pydantic v1
    drops from ``__fields__``, and a default that ``exclude_unset`` discards.
    """

    def test_models_expose_the_record_type_as_a_pydantic_field(self):
        from models.indexing import FileDocumentModel, AssetDocumentModel
        for model_cls in (FileDocumentModel, AssetDocumentModel):
            names = [name for name in model_cls.__fields__ if _RECTYPE_NAME.search(name)]
            assert len(names) == 1, (
                f"{model_cls.__name__}.__fields__ carries record-type fields {names}; "
                f"a leading-underscore name is silently dropped by pydantic v1. "
                f"fields={sorted(model_cls.__fields__)}"
            )
            assert not names[0].startswith("_"), (
                f"{model_cls.__name__} names the record type {names[0]!r}"
            )
            # Pydantic v1 collects unrecognized Field() kwargs into extra instead of
            # raising, so an inert annotation looks load-bearing.
            assert not model_cls.__fields__[names[0]].field_info.extra, (
                f"{model_cls.__name__}.{names[0]} swallowed unknown Field() kwargs: "
                f"{model_cls.__fields__[names[0]].field_info.extra}"
            )

    def test_record_type_survives_exclude_unset_without_being_passed(self):
        """The indexers serialize with ``exclude_unset=True`` and never pass the value."""
        from models.indexing import FileDocumentModel, AssetDocumentModel
        file_doc = FileDocumentModel(str_key="/a.glb", str_databaseid="db1", str_assetid="a1")
        asset_doc = AssetDocumentModel(str_databaseid="db1", str_assetid="a1")

        assert _rectype_entries(file_doc.dict(exclude_unset=True)) == {"str_rectype": "file"}
        assert _rectype_entries(asset_doc.dict(exclude_unset=True)) == {"str_rectype": "asset"}

    def test_legacy_underscore_key_is_not_indexed(self, file_indexer, asset_indexer):
        """A partial revert that emits both spellings is a mapping conflict, not a fix."""
        for indexer, index_callable, document in (
            (file_indexer, file_indexer.index_file_document, _file_document(file_indexer)),
            (asset_indexer, asset_indexer.index_asset_document, _asset_document(asset_indexer)),
        ):
            body = _captured_index_body(indexer, index_callable, document)
            assert "_rectype" not in body, (
                f"the indexed body still carries the legacy `_rectype` key; keys={sorted(body)}"
            )
