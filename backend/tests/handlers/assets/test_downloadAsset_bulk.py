# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

# Env vars downloadAsset requires at import time
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

_DOWNLOAD_ASSET_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets", "downloadAsset.py"
)

_cached_module = None


def _load():
    """Load the real downloadAsset module by file path with boto3 stubbed.

    The mock handlers package registered by the root conftest shadows the real
    package, so a normal import cannot reach the real module.
    """
    global _cached_module
    if _cached_module is not None:
        return _cached_module

    # downloadAsset imports version-resolution helpers from assetVersions and
    # CasbinEnforcer from handlers.authz, which the mock handlers packages do
    # not provide. Stub them for the load.
    stub_names = ("handlers.assets.assetVersions", "handlers.authz", "handlers.auth")
    saved = {name: sys.modules.get(name) for name in stub_names}
    versions_stub = types.ModuleType("handlers.assets.assetVersions")
    versions_stub.resolve_file_version_from_asset_version = MagicMock(return_value=None)
    versions_stub.resolve_asset_version_id_from_alias = MagicMock(return_value=None)
    sys.modules["handlers.assets.assetVersions"] = versions_stub
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["test-user"], "roles": []})
    sys.modules["handlers.auth"] = auth_stub

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                "downloadAsset_under_test", os.path.abspath(_DOWNLOAD_ASSET_PATH)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
    _cached_module = module
    return module


def _wire_asset_context(m):
    """Configure the module so asset-level checks pass."""
    m.get_asset_details = MagicMock(return_value={
        'databaseId': 'db1', 'assetId': 'asset1', 'isDistributable': True,
        'bucketId': 'bucket-1', 'assetLocation': {'Key': 'asset1/'}
    })
    m.get_default_bucket_details = MagicMock(return_value={
        'bucketId': 'bucket-1', 'bucketName': 'test-bucket', 'baseAssetsPrefix': ''
    })
    m.validateS3AssetExtensionsAndContentType = MagicMock(return_value=True)
    m.validateUnallowedFileExtensionAndContentType = MagicMock(return_value=True)
    m.check_s3_object_exists = MagicMock(return_value=True)
    m.is_delete_marker = MagicMock(return_value=False)
    m.s3 = MagicMock()
    m.s3.head_object.return_value = {'ContentType': 'text/plain', 'ContentLength': 10}
    m.s3.generate_presigned_url.side_effect = (
        lambda op, Params, ExpiresIn: f"https://signed.example/{Params['Key']}"
    )


def _make_head_404(m, missing_predicate):
    """Make head_object raise 404 for keys matching the predicate."""
    def _head(Bucket, Key, **kwargs):
        if missing_predicate(Key):
            raise ClientError({"Error": {"Code": "404"}}, "HeadObject")
        return {'ContentType': 'text/plain', 'ContentLength': 10}
    m.s3.head_object.side_effect = _head


def _bulk_request(m, keys, **extra):
    body = {'downloadType': 'assetFile', 'keys': keys}
    body.update(extra)
    # Parse through the real request model to exercise its validation
    from aws_lambda_powertools.utilities.parser import parse
    from models.assetsV3 import DownloadAssetRequestModel
    return parse(body, model=DownloadAssetRequestModel)


@pytest.mark.unit
class TestDownloadRequestModelBulk:
    def _parse(self, body):
        from aws_lambda_powertools.utilities.parser import parse
        from models.assetsV3 import DownloadAssetRequestModel
        return parse(body, model=DownloadAssetRequestModel)

    def test_single_key_still_valid(self):
        model = self._parse({'downloadType': 'assetFile', 'key': '/file.txt'})
        assert model.key == '/file.txt'
        assert model.keys is None

    def test_bulk_keys_valid(self):
        # String entries normalize to {key, versionId=None} (latest)
        model = self._parse({'downloadType': 'assetFile', 'keys': ['/a.txt', '/dir/b.txt']})
        assert model.keys == [{'key': '/a.txt', 'versionId': None},
                              {'key': '/dir/b.txt', 'versionId': None}]

    def test_bulk_keys_with_per_file_versions(self):
        # Object entries carry their own versionId; strings mean latest
        model = self._parse({'downloadType': 'assetFile', 'keys': [
            {'key': '/a.txt', 'versionId': 'v-a'},
            {'key': '/b.txt'},
            '/c.txt'
        ]})
        assert model.keys == [
            {'key': '/a.txt', 'versionId': 'v-a'},
            {'key': '/b.txt', 'versionId': None},
            {'key': '/c.txt', 'versionId': None},
        ]

    def test_per_file_version_conflicts_with_asset_version(self):
        with pytest.raises(Exception):
            self._parse({'downloadType': 'assetFile',
                        'keys': [{'key': '/a.txt', 'versionId': 'v-a'}],
                        'assetVersionId': '2'})

    def test_object_key_without_key_field_rejected(self):
        with pytest.raises(Exception):
            self._parse({'downloadType': 'assetFile', 'keys': [{'versionId': 'v-a'}]})

    def test_asset_version_with_plain_string_keys_ok(self):
        # A whole-set asset version pin is fine with plain (latest) string keys
        model = self._parse({'downloadType': 'assetFile', 'keys': ['/a.txt', '/b.txt'],
                            'assetVersionId': '2'})
        assert model.assetVersionId == '2'
        assert all(e['versionId'] is None for e in model.keys)

    def test_key_and_keys_mutually_exclusive(self):
        with pytest.raises(Exception):
            self._parse({'downloadType': 'assetFile', 'key': '/a.txt', 'keys': ['/b.txt']})

    def test_keys_not_allowed_for_preview(self):
        with pytest.raises(Exception):
            self._parse({'downloadType': 'assetPreview', 'keys': ['/a.txt']})

    def test_empty_keys_rejected(self):
        with pytest.raises(Exception):
            self._parse({'downloadType': 'assetFile', 'keys': []})

    def test_keys_over_limit_rejected(self):
        from models.assetsV3 import MAX_KEYS_PER_DOWNLOAD_REQUEST
        keys = [f'/f{i}.txt' for i in range(MAX_KEYS_PER_DOWNLOAD_REQUEST + 1)]
        with pytest.raises(Exception):
            self._parse({'downloadType': 'assetFile', 'keys': keys})

    def test_keys_at_limit_accepted(self):
        from models.assetsV3 import MAX_KEYS_PER_DOWNLOAD_REQUEST
        keys = [f'/f{i}.txt' for i in range(MAX_KEYS_PER_DOWNLOAD_REQUEST)]
        model = self._parse({'downloadType': 'assetFile', 'keys': keys})
        assert len(model.keys) == MAX_KEYS_PER_DOWNLOAD_REQUEST

    def test_version_id_not_allowed_with_keys(self):
        with pytest.raises(Exception):
            self._parse({'downloadType': 'assetFile', 'keys': ['/a.txt'], 'versionId': 'v1'})

    def test_asset_version_id_allowed_with_keys(self):
        model = self._parse({'downloadType': 'assetFile', 'keys': ['/a.txt'],
                             'assetVersionId': '2'})
        assert model.assetVersionId == '2'

    def test_invalid_key_pattern_rejected(self):
        # Keys must be leading-slash relative paths. The test environment mocks
        # the validate() dispatcher, so exercise the real array validator by
        # loading the actual validators module from its file path.
        import importlib.util
        validators_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "backend", "common", "validators.py"
        )
        spec = importlib.util.spec_from_file_location(
            "validators_under_test", os.path.abspath(validators_path)
        )
        validators = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validators)

        # Bulk keys use the download-key validator: full asset keys and
        # relative paths both accepted; only traversal and empties rejected.
        (valid, message) = validators.validate_download_key_array(
            'keys', ['assetId/no-leading-slash.txt']
        )
        assert valid is True

        (valid, message) = validators.validate_download_key_array(
            'keys', ['/good/path.txt', '/another.glb']
        )
        assert valid is True

        (valid, message) = validators.validate_download_key_array(
            'keys', ['/has/../traversal.txt']
        )
        assert valid is False

        (valid, message) = validators.validate_relative_file_path_array('keys', 'not-a-list')
        assert valid is False


@pytest.mark.unit
class TestDownloadAssetFilesBulk:
    def test_bulk_generates_url_per_key(self):
        m = _load()
        _wire_asset_context(m)
        request = _bulk_request(m, ['/a.txt', '/dir/b.txt', '/c.txt'])

        response = m.download_asset_files_bulk('db1', 'asset1', request)

        assert len(response.files) == 3
        by_key = {f.key: f for f in response.files}
        assert by_key['/a.txt'].success is True
        assert 'signed.example' in by_key['/dir/b.txt'].downloadUrl
        # Top-level downloadUrl carries the first successful URL
        assert response.downloadUrl == response.files[0].downloadUrl
        assert response.downloadType == 'assetFile'

    def test_bulk_per_file_versions_signed_with_that_version(self):
        m = _load()
        _wire_asset_context(m)
        from aws_lambda_powertools.utilities.parser import parse
        from models.assetsV3 import DownloadAssetRequestModel
        request = parse({'downloadType': 'assetFile', 'keys': [
            {'key': '/a.txt', 'versionId': 'ver-a'},
            '/b.txt'
        ]}, model=DownloadAssetRequestModel)

        response = m.download_asset_files_bulk('db1', 'asset1', request)

        by_key = {f.key: f for f in response.files}
        assert by_key['/a.txt'].versionId == 'ver-a'
        assert by_key['/b.txt'].versionId is None
        # The versioned file's presigned URL must carry that VersionId.
        # Keys are normalized against the asset base key (asset1/) before signing.
        version_calls = {
            c.kwargs['Params']['Key']: c.kwargs['Params'].get('VersionId')
            for c in m.s3.generate_presigned_url.call_args_list
        }
        assert version_calls.get('asset1/a.txt') == 'ver-a'
        assert version_calls.get('asset1/b.txt') is None

    def test_missing_file_is_soft_failure_with_warning(self):
        m = _load()
        _wire_asset_context(m)
        # Second key does not exist in S3
        _make_head_404(m, lambda key: 'missing' in key)
        request = _bulk_request(m, ['/good.txt', '/missing.txt'])

        response = m.download_asset_files_bulk('db1', 'asset1', request)

        by_key = {f.key: f for f in response.files}
        assert by_key['/good.txt'].success is True
        assert by_key['/missing.txt'].success is False
        assert by_key['/missing.txt'].error
        assert by_key['/missing.txt'].downloadUrl is None
        # Warning message reflects skipped paths
        assert 'Warning' in response.message and 'skipped' in response.message

    def test_all_files_failing_raises(self):
        m = _load()
        _wire_asset_context(m)
        _make_head_404(m, lambda key: True)
        request = _bulk_request(m, ['/a.txt', '/b.txt'])

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.download_asset_files_bulk('db1', 'asset1', request)

    def test_asset_level_checks_run_once(self):
        m = _load()
        _wire_asset_context(m)
        request = _bulk_request(m, [f'/f{i}.txt' for i in range(25)])

        m.download_asset_files_bulk('db1', 'asset1', request)

        # Asset lookup and bucket lookup happen once, not per key
        assert m.get_asset_details.call_count == 1
        assert m.get_default_bucket_details.call_count == 1
        # One URL per key
        assert m.s3.generate_presigned_url.call_count == 25

    def test_non_distributable_asset_hard_fails(self):
        m = _load()
        _wire_asset_context(m)
        m.get_asset_details = MagicMock(return_value={
            'databaseId': 'db1', 'assetId': 'asset1', 'isDistributable': False,
            'bucketId': 'bucket-1', 'assetLocation': {'Key': 'asset1/'}
        })
        request = _bulk_request(m, ['/a.txt'])

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.download_asset_files_bulk('db1', 'asset1', request)

    def test_single_file_response_shape_unchanged(self):
        # Backwards compatibility: single-key requests keep the original shape
        m = _load()
        _wire_asset_context(m)
        from aws_lambda_powertools.utilities.parser import parse
        from models.assetsV3 import DownloadAssetRequestModel
        request = parse({'downloadType': 'assetFile', 'key': '/a.txt'},
                        model=DownloadAssetRequestModel)

        response = m.download_asset_file('db1', 'asset1', request)

        data = response.dict()
        assert data['downloadUrl']
        assert data['files'] is None
        assert data['downloadType'] == 'assetFile'
