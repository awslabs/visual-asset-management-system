# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Input validation on the asset-export request model, the asset-history query
model, and the indexing request models.

maxAssets bounds a per-asset fan-out (DynamoDB reads plus S3 listing/head calls
per asset, and one presigned URL per included file), so an unbounded value drives
Lambda runtime and response size past the 6 MB synchronous response limit. The
indexing models are built by the indexer Lambdas from S3/DynamoDB values rather
than by an API caller, so their bounds are generous and exist to keep a malformed
upstream record from reaching OpenSearch.
"""

import importlib.util
import os

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError


def _real_validate():
    """Load the real validate() dispatcher straight from its source file.

    `tests/conftest.py` replaces the dispatcher with a permissive stub
    (`lambda params: (True, "")`), under which a model root_validator asserts
    nothing. Loading the real module by path keeps these tests honest.
    """
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        'backend', 'common', 'validators.py',
    )
    spec = importlib.util.spec_from_file_location('_real_validators_assetexport', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate


@pytest.fixture(autouse=True)
def use_real_validators(monkeypatch):
    """Point the export model at the real dispatcher for every test here."""
    import models.assetExport as export_models
    monkeypatch.setattr(export_models, 'validate', _real_validate())


@pytest.mark.unit
class TestAssetExportRequestModel:
    def test_accepts_an_empty_body(self):
        from models.assetExport import AssetExportRequestModel
        model = parse({}, model=AssetExportRequestModel)
        assert model.maxAssets == 100
        assert model.fileExtensions is None

    def test_accepts_max_assets_at_the_cap(self):
        from models.assetExport import AssetExportRequestModel, MAX_ASSETS_PER_EXPORT_PAGE
        model = parse({"maxAssets": MAX_ASSETS_PER_EXPORT_PAGE}, model=AssetExportRequestModel)
        assert model.maxAssets == MAX_ASSETS_PER_EXPORT_PAGE

    def test_rejects_max_assets_above_the_cap(self):
        """An unbounded maxAssets lets one request drive unbounded per-asset fan-out.
        The cap matches the maximum already published in the OpenAPI spec."""
        from models.assetExport import AssetExportRequestModel, MAX_ASSETS_PER_EXPORT_PAGE
        with pytest.raises(ValidationError):
            parse({"maxAssets": MAX_ASSETS_PER_EXPORT_PAGE + 1}, model=AssetExportRequestModel)

    def test_rejects_a_huge_max_assets(self):
        from models.assetExport import AssetExportRequestModel
        with pytest.raises(ValidationError):
            parse({"maxAssets": 100000000}, model=AssetExportRequestModel)

    def test_rejects_max_assets_below_one(self):
        from models.assetExport import AssetExportRequestModel
        with pytest.raises(ValidationError):
            parse({"maxAssets": 0}, model=AssetExportRequestModel)

    def test_accepts_normal_extension_filters(self):
        from models.assetExport import AssetExportRequestModel
        model = parse({"fileExtensions": [".pdf", ".jpg"]}, model=AssetExportRequestModel)
        assert model.fileExtensions == [".pdf", ".jpg"]

    def test_rejects_more_extension_filters_than_the_cap(self):
        from models.assetExport import AssetExportRequestModel, MAX_FILE_EXTENSION_FILTERS
        exts = [f".e{i}" for i in range(MAX_FILE_EXTENSION_FILTERS + 1)]
        with pytest.raises(ValidationError):
            parse({"fileExtensions": exts}, model=AssetExportRequestModel)

    def test_rejects_an_oversized_extension_filter(self):
        from models.assetExport import AssetExportRequestModel
        with pytest.raises(ValidationError):
            parse({"fileExtensions": ["." + "a" * 300]}, model=AssetExportRequestModel)

    def test_rejects_an_oversized_pagination_token(self):
        from models.assetExport import AssetExportRequestModel, MAX_EXPORT_TOKEN_LENGTH
        with pytest.raises(ValidationError):
            parse({"startingToken": "a" * (MAX_EXPORT_TOKEN_LENGTH + 1)},
                  model=AssetExportRequestModel)

    def test_unknown_fields_are_dropped(self):
        from models.assetExport import AssetExportRequestModel
        model = parse({"unexpectedField": "ignored"}, model=AssetExportRequestModel)
        assert not hasattr(model, "unexpectedField")


@pytest.mark.unit
class TestAssetHistoryRequestModel:
    def test_accepts_a_normal_token(self):
        from models.assetHistory import GetAssetHistoryRequestModel
        model = parse({"startingToken": "eyJhIjoxfQ=="}, model=GetAssetHistoryRequestModel)
        assert model.startingToken == "eyJhIjoxfQ=="

    def test_rejects_an_oversized_pagination_token(self):
        from models.assetHistory import (
            GetAssetHistoryRequestModel, MAX_HISTORY_TOKEN_LENGTH,
        )
        with pytest.raises(ValidationError):
            parse({"startingToken": "a" * (MAX_HISTORY_TOKEN_LENGTH + 1)},
                  model=GetAssetHistoryRequestModel)


def _file_index_body(**overrides):
    body = {
        "databaseId": "smoke-db",
        "assetId": "xa31832dc-ca88-42ba-88cd-37fa9bb0cec9",
        "filePath": "/scans/pump.e57",
        "bucketName": "vams-assets-bucket",
        "s3Key": "xa31832dc/scans/pump.e57",
        "operation": "index",
    }
    body.update(overrides)
    return body


@pytest.mark.unit
class TestFileIndexRequest:
    def test_accepts_a_normal_record(self):
        from models.indexing import FileIndexRequest
        model = parse(_file_index_body(), model=FileIndexRequest)
        assert model.operation == "index"

    @pytest.mark.parametrize("operation", ["index", "delete"])
    def test_accepts_both_supported_operations(self, operation):
        from models.indexing import FileIndexRequest
        model = parse(_file_index_body(operation=operation), model=FileIndexRequest)
        assert model.operation == operation

    def test_rejects_an_unsupported_operation(self):
        """operation is dispatched on; an unknown value reached the dispatch and
        was echoed back in the error message."""
        from models.indexing import FileIndexRequest
        with pytest.raises(ValidationError):
            parse(_file_index_body(operation="drop-index"), model=FileIndexRequest)

    def test_rejects_an_s3_key_past_the_s3_limit(self):
        from models.indexing import FileIndexRequest, MAX_S3_KEY_LENGTH
        with pytest.raises(ValidationError):
            parse(_file_index_body(s3Key="a" * (MAX_S3_KEY_LENGTH + 1)), model=FileIndexRequest)

    def test_rejects_an_overlong_bucket_name(self):
        from models.indexing import FileIndexRequest, MAX_BUCKET_NAME_LENGTH
        with pytest.raises(ValidationError):
            parse(_file_index_body(bucketName="b" * (MAX_BUCKET_NAME_LENGTH + 1)),
                  model=FileIndexRequest)

    def test_rejects_a_negative_file_size(self):
        from models.indexing import FileIndexRequest
        with pytest.raises(ValidationError):
            parse(_file_index_body(fileSize=-1), model=FileIndexRequest)

    def test_rejects_an_empty_database_id(self):
        from models.indexing import FileIndexRequest
        with pytest.raises(ValidationError):
            parse(_file_index_body(databaseId=""), model=FileIndexRequest)


def _asset_index_body(**overrides):
    body = {
        "databaseId": "smoke-db",
        "assetId": "xa31832dc-ca88-42ba-88cd-37fa9bb0cec9",
        "operation": "index",
    }
    body.update(overrides)
    return body


@pytest.mark.unit
class TestAssetIndexRequest:
    def test_accepts_a_normal_record(self):
        from models.indexing import AssetIndexRequest
        model = parse(_asset_index_body(assetName="Pump Scan"), model=AssetIndexRequest)
        assert model.assetName == "Pump Scan"

    def test_accepts_a_unicode_asset_name(self):
        """Asset names legitimately carry unicode; the bound is length only."""
        from models.indexing import AssetIndexRequest
        model = parse(_asset_index_body(assetName="Café Scan (rev2)"), model=AssetIndexRequest)
        assert model.assetName == "Café Scan (rev2)"

    def test_rejects_an_unsupported_operation(self):
        from models.indexing import AssetIndexRequest
        with pytest.raises(ValidationError):
            parse(_asset_index_body(operation="reindex-everything"), model=AssetIndexRequest)

    def test_rejects_an_overlong_description(self):
        from models.indexing import AssetIndexRequest, MAX_INDEX_DESCRIPTION_LENGTH
        with pytest.raises(ValidationError):
            parse(_asset_index_body(description="d" * (MAX_INDEX_DESCRIPTION_LENGTH + 1)),
                  model=AssetIndexRequest)

    def test_rejects_more_tags_than_the_cap(self):
        from models.indexing import AssetIndexRequest, MAX_INDEX_TAGS
        tags = [f"tag{i}" for i in range(MAX_INDEX_TAGS + 1)]
        with pytest.raises(ValidationError):
            parse(_asset_index_body(tags=tags), model=AssetIndexRequest)

    def test_rejects_an_overlong_bucket_prefix(self):
        from models.indexing import AssetIndexRequest, MAX_S3_KEY_LENGTH
        with pytest.raises(ValidationError):
            parse(_asset_index_body(bucketPrefix="p" * (MAX_S3_KEY_LENGTH + 1)),
                  model=AssetIndexRequest)
