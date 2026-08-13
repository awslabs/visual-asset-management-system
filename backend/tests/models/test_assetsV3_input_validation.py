# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Input-validation coverage for the assetsV3 request models.

Each malformed-input test is paired with a legitimate-input test for the same
field, so a validator that is tightened into rejecting real traffic fails here
rather than in production. Pydantic v1 spells the regex constraint `regex=`,
not `pattern=` — a `pattern=` kwarg is silently swallowed and enforces nothing,
so these tests assert behavior rather than field declarations.
"""

import importlib.util
import os

import pytest
from aws_lambda_powertools.utilities.parser import parse, ValidationError

from models.assetsV3 import (
    AssetLocationModel,
    AssetPreviewLocationModel,
    CompleteExternalUploadRequestModel,
    CompleteUploadRequestModel,
    CopyFileRequestModel,
    CreateAssetRequestModel,
    DeleteFileRequestModel,
    FileInfoRequestModel,
    InitializeUploadRequestModel,
    MAX_FILES_PER_UPLOAD_REQUEST,
    MAX_S3_KEY_LENGTH,
    MoveFileRequestModel,
    SetPrimaryFileRequestModel,
    UnarchiveFileRequestModel,
)


def _load_real_validate():
    """Load the real validate() dispatcher straight from its source path.

    The root `tests/conftest.py` replaces `common.validators.validate` with a
    permissive `lambda params: (True, "")` stub so older tests that predate the
    dispatcher keep passing. These tests assert the dispatcher's actual reject
    behavior, so they need the real function.
    """
    validators_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'backend', 'common', 'validators.py',
    )
    spec = importlib.util.spec_from_file_location('_real_validators_for_assetsv3', validators_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate


@pytest.fixture(autouse=True)
def real_validate(monkeypatch):
    """Point the assetsV3 module at the real dispatcher for every test here."""
    import models.assetsV3 as assets_v3
    monkeypatch.setattr(assets_v3, 'validate', _load_real_validate())


def _create_asset_body(**overrides):
    body = {
        'databaseId': 'test-db',
        'assetName': 'My Asset',
        'description': 'a description',
        'isDistributable': True,
    }
    body.update(overrides)
    return body


def _external_upload_body(**overrides):
    body = {
        'assetId': 'a1',
        'databaseId': 'test-db',
        'uploadType': 'assetFile',
        'files': [{'relativeKey': '/a.glb', 'tempKey': 'tmp/a.glb'}],
    }
    body.update(overrides)
    return body


@pytest.mark.unit
class TestAssetFilePathValidation:
    """File-path fields reach S3 keys, so traversal must be rejected."""

    # Every request model carrying a caller-supplied asset file path.
    TRAVERSAL_CASES = [
        (FileInfoRequestModel, {'filePath': '/a/../../etc/passwd'}),
        (UnarchiveFileRequestModel, {'filePath': '/../x.glb'}),
        (DeleteFileRequestModel, {'filePath': '/../x.glb', 'confirmPermanentDelete': True}),
        (MoveFileRequestModel, {'sourcePath': '/../../etc/passwd', 'destinationPath': '/b.glb'}),
        (MoveFileRequestModel, {'sourcePath': '/a.glb', 'destinationPath': '/../../etc/passwd'}),
        (CopyFileRequestModel, {'sourcePath': '/../a.glb', 'destinationPath': '/b.glb'}),
        (SetPrimaryFileRequestModel, {'filePath': '/../a.glb', 'primaryType': 'primary'}),
    ]

    @pytest.mark.parametrize("model,body", TRAVERSAL_CASES)
    def test_rejects_path_traversal(self, model, body):
        with pytest.raises(ValidationError):
            parse(body, model=model)

    @pytest.mark.parametrize("model,body", [
        (FileInfoRequestModel, {'filePath': '/folder/my file.glb'}),
        (UnarchiveFileRequestModel, {'filePath': '/folder/my file.glb'}),
        (DeleteFileRequestModel, {'filePath': '/a.glb', 'confirmPermanentDelete': True}),
        (MoveFileRequestModel, {'sourcePath': '/a.glb', 'destinationPath': '/dir/b.glb'}),
        (CopyFileRequestModel, {'sourcePath': '/a.glb', 'destinationPath': '/dir/b.glb'}),
        (SetPrimaryFileRequestModel, {'filePath': '/a.glb', 'primaryType': 'primary'}),
    ])
    def test_accepts_legitimate_paths(self, model, body):
        assert parse(body, model=model) is not None

    def test_rejects_backslash_separator(self):
        # S3 treats '\' as a literal key character, so a Windows-style traversal
        # would otherwise survive the '..' check on the forward-slash form.
        with pytest.raises(ValidationError):
            parse({'filePath': '/a\\..\\b.glb'}, model=FileInfoRequestModel)

    def test_accepts_full_asset_prefixed_key_form(self):
        # The file APIs accept both '/dir/file' and 'assetId/dir/file'; the web
        # file manager sends the full S3 key, so the leading '/' is not required.
        model = parse({'sourcePath': 'assetId/dir/a.glb',
                       'destinationPath': 'assetId/dir/b.glb'}, model=MoveFileRequestModel)
        assert model.sourcePath == 'assetId/dir/a.glb'

    def test_accepts_unicode_and_spaces_in_path(self):
        model = parse({'filePath': '/dossiér/my ünicode file.glb'}, model=FileInfoRequestModel)
        assert model.filePath == '/dossiér/my ünicode file.glb'

    def test_rejects_path_over_s3_key_limit(self):
        with pytest.raises(ValidationError):
            parse({'filePath': '/' + ('a' * MAX_S3_KEY_LENGTH)}, model=FileInfoRequestModel)


@pytest.mark.unit
class TestAssetIdentifierValidation:
    """databaseId uses ID; assetId uses ASSET_ID; assetName uses OBJECT_NAME."""

    def test_rejects_too_short_database_id(self):
        with pytest.raises(ValidationError):
            parse(_create_asset_body(databaseId='ab'), model=CreateAssetRequestModel)

    def test_rejects_database_id_with_separator(self):
        with pytest.raises(ValidationError):
            parse(_create_asset_body(databaseId='db/../other'), model=CreateAssetRequestModel)

    def test_accepts_legitimate_database_id(self):
        assert parse(_create_asset_body(databaseId='test-db_1'),
                     model=CreateAssetRequestModel) is not None

    def test_rejects_asset_id_traversal(self):
        with pytest.raises(ValidationError):
            parse(_create_asset_body(assetId='../evil'), model=CreateAssetRequestModel)

    def test_accepts_asset_id_with_dots_and_spaces(self):
        # Asset ids legitimately carry dots and spaces, which the stricter ID
        # validator would reject — ASSET_ID is the correct rule here.
        model = parse(_create_asset_body(assetId='my.asset v2'), model=CreateAssetRequestModel)
        assert model.assetId == 'my.asset v2'

    def test_rejects_asset_name_with_quote_injection(self):
        with pytest.raises(ValidationError):
            parse(_create_asset_body(assetName='A"; DROP TABLE'), model=CreateAssetRequestModel)

    def test_accepts_asset_name_with_spaces_and_punctuation(self):
        assert parse(_create_asset_body(assetName='My Asset-v1.2'),
                     model=CreateAssetRequestModel) is not None

    def test_rejects_copy_destination_identifier_traversal(self):
        with pytest.raises(ValidationError):
            parse({'sourcePath': '/a.glb', 'destinationPath': '/b.glb',
                   'destinationAssetId': '../evil'}, model=CopyFileRequestModel)

    def test_accepts_copy_destination_identifiers(self):
        model = parse({'sourcePath': '/a.glb', 'destinationPath': '/b.glb',
                       'destinationAssetId': 'other.asset',
                       'destinationDatabaseId': 'other-db'}, model=CopyFileRequestModel)
        assert model.destinationDatabaseId == 'other-db'


@pytest.mark.unit
class TestUploadRequestValidation:
    """Upload keys build S3 keys; the collections bound Lambda work per request."""

    def test_rejects_relative_key_traversal(self):
        with pytest.raises(ValidationError):
            parse({'assetId': 'a1', 'databaseId': 'test-db', 'uploadType': 'assetFile',
                   'files': [{'relativeKey': '/../../evil.txt', 'file_size': 5}]},
                  model=InitializeUploadRequestModel)

    def test_accepts_legitimate_relative_key(self):
        model = parse({'assetId': 'a1', 'databaseId': 'test-db', 'uploadType': 'assetFile',
                       'files': [{'relativeKey': '/dir/my file.glb', 'file_size': 5}]},
                      model=InitializeUploadRequestModel)
        assert model.files[0].relativeKey == '/dir/my file.glb'

    def test_rejects_completion_relative_key_traversal(self):
        with pytest.raises(ValidationError):
            parse({'assetId': 'a1', 'databaseId': 'test-db', 'uploadType': 'assetFile',
                   'files': [{'relativeKey': '/../evil.txt', 'uploadIdS3': 'u1',
                              'parts': [{'PartNumber': 1, 'ETag': 'e'}]}]},
                  model=CompleteUploadRequestModel)

    def test_accepts_legitimate_completion(self):
        model = parse({'assetId': 'a1', 'databaseId': 'test-db', 'uploadType': 'assetFile',
                       'files': [{'relativeKey': '/a.glb', 'uploadIdS3': 'u1',
                                  'parts': [{'PartNumber': 1, 'ETag': 'e'}]}]},
                      model=CompleteUploadRequestModel)
        assert model.files[0].parts[0].PartNumber == 1

    def test_rejects_part_number_below_s3_minimum(self):
        with pytest.raises(ValidationError):
            parse({'assetId': 'a1', 'databaseId': 'test-db', 'uploadType': 'assetFile',
                   'files': [{'relativeKey': '/a.glb', 'uploadIdS3': 'u1',
                              'parts': [{'PartNumber': 0, 'ETag': 'e'}]}]},
                  model=CompleteUploadRequestModel)

    def test_rejects_completion_file_list_over_cap(self):
        files = [{'relativeKey': f'/f{i}.glb', 'uploadIdS3': f'u{i}', 'parts': []}
                 for i in range(MAX_FILES_PER_UPLOAD_REQUEST + 1)]
        with pytest.raises(ValidationError):
            parse({'assetId': 'a1', 'databaseId': 'test-db',
                   'uploadType': 'assetFile', 'files': files},
                  model=CompleteUploadRequestModel)

    def test_rejects_external_temp_key_traversal(self):
        with pytest.raises(ValidationError):
            parse(_external_upload_body(
                files=[{'relativeKey': '/a.glb', 'tempKey': 'tmp/../../other/a.glb'}]),
                model=CompleteExternalUploadRequestModel)

    def test_rejects_malformed_source_bucket(self):
        # sourceBucket names the bucket the temp files are copied FROM.
        with pytest.raises(ValidationError):
            parse(_external_upload_body(sourceBucket='Bad_Bucket!'),
                  model=CompleteExternalUploadRequestModel)

    def test_accepts_legitimate_source_bucket(self):
        model = parse(_external_upload_body(sourceBucket='my-vams-run-bucket-123'),
                      model=CompleteExternalUploadRequestModel)
        assert model.sourceBucket == 'my-vams-run-bucket-123'


@pytest.mark.unit
class TestAssetLocationModel:
    """Key holds a STORED bucket-root-relative S3 key, not an asset-relative path."""

    @pytest.mark.parametrize("model", [AssetLocationModel, AssetPreviewLocationModel])
    def test_accepts_stored_key_without_leading_slash(self, model):
        # Requiring a leading '/' here would make the archive and permanent-delete
        # paths in assetService.py silently skip their S3 cleanup.
        assert model(Key='bucketPrefix/myAsset/').Key == 'bucketPrefix/myAsset/'

    @pytest.mark.parametrize("model", [AssetLocationModel, AssetPreviewLocationModel])
    def test_rejects_traversal_in_stored_key(self, model):
        with pytest.raises(ValueError):
            model(Key='../../other-asset/')
