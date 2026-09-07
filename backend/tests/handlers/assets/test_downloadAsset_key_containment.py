# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""FIX-008: a caller-supplied download key must stay inside its own asset prefix.

``resolve_and_sign_file_key`` decides whether a caller-supplied key is already
asset-prefixed with a bare ``raw_key.startswith(asset_base_key)`` test. The base
key comes from ``assetLocation.Key``, which is ``<prefix><assetId>/`` for normally
created assets but can lack the trailing slash when the asset was created against
an existing bucket key. With a base of ``assets/pfx``, the key
``assets/pfxOTHER/secret.txt`` passes ``startswith`` and is signed verbatim, so a
caller authorized on one asset can read any sibling object in the same bucket --
including another database's assets or a registered external bucket.

The permitted-input matrix in this file is the counterweight: the web viewer
passes the FULL S3 key from listFiles, the CLI passes a leading-slash relative
path, and the recursive/whole-asset download passes ``/`` or no key at all. All
of those must keep resolving to exactly the same S3 key after containment is
enforced, and a version-pinned download must keep deriving the same asset-relative
key so the pin does not silently degrade to "latest".

FIX-046 shares the same code path: the whole-asset (prefix) download validates
every object under the asset prefix with one HeadObject each, which is what makes
a realistic asset exceed the API Gateway integration timeout. The last class
covers that fan-out, which must stay fail-closed and must not sample.
"""

import os
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

# Env vars downloadAsset requires at import time (mirrors test_downloadAsset_bulk).
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

# Reuse the real downloadAsset loader so the single and bulk paths under test are
# the deployed code rather than a re-implementation.
from tests.handlers.assets.test_downloadAsset_bulk import _load  # noqa: E402

# Base key of the asset under test WITHOUT a trailing slash -- the shape
# createAsset produces for a bucketExistingKey asset, and the shape that makes
# the startswith test unsafe.
UNSLASHED_BASE = "assets/pfx"
SLASHED_BASE = "assets/pfx/"
# A sibling object that shares the base key's string prefix but belongs to a
# different asset.
ESCAPE_KEY = "assets/pfxOTHER/secret.txt"


def _wire(m, base_key):
    """Point the module at a mocked S3/asset context with the given asset base key."""
    m.get_asset_details = MagicMock(return_value={
        'databaseId': 'db1', 'assetId': 'asset1', 'isDistributable': True,
        'bucketId': 'bucket-1', 'assetLocation': {'Key': base_key},
    })
    m.get_default_bucket_details = MagicMock(return_value={
        'bucketId': 'bucket-1', 'bucketName': 'test-bucket', 'baseAssetsPrefix': '',
    })
    m.validateS3AssetExtensionsAndContentType = MagicMock(return_value=True)
    m.validateUnallowedFileExtensionAndContentType = MagicMock(return_value=True)
    # Empty prefix listing by default: the module is cached across tests, so every
    # test starts from a prefix that costs no HeadObject calls.
    m.list_all_objects = MagicMock(return_value=[])
    m.check_s3_object_exists = MagicMock(return_value=True)
    m.is_delete_marker = MagicMock(return_value=False)
    m.resolve_file_version_from_asset_version = MagicMock(return_value=None)
    m.resolve_asset_version_id_from_alias = MagicMock(return_value=None)
    m.s3 = MagicMock()
    m.s3.head_object.return_value = {'ContentType': 'text/plain', 'ContentLength': 10}
    m.s3.generate_presigned_url.side_effect = (
        lambda op, Params, ExpiresIn: f"https://signed.example/{Params['Key']}"
    )
    return m


def _parse(body):
    from aws_lambda_powertools.utilities.parser import parse
    from models.assetsV3 import DownloadAssetRequestModel
    return parse(body, model=DownloadAssetRequestModel)


def _signed_keys(m):
    return [c.kwargs['Params']['Key'] for c in m.s3.generate_presigned_url.call_args_list]


@pytest.mark.unit
class TestDownloadKeyContainment:
    """FIX-008 -- the escape itself."""

    def test_single_path_rejects_sibling_prefix_escape(self):
        """FIX-008: single-key download of a prefix-sharing sibling must be refused.

        Asserting the refusal alone is not enough -- generate_presigned_url must
        never have been reached, otherwise a URL was already minted (and audit
        logged) before the error surfaced.
        """
        m = _wire(_load(), UNSLASHED_BASE)
        request = _parse({'downloadType': 'assetFile', 'key': ESCAPE_KEY})

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.download_asset_file('db1', 'asset1', request)

        assert m.s3.generate_presigned_url.call_count == 0, (
            f"a presigned URL was generated for keys {_signed_keys(m)}"
        )

    def test_bulk_path_rejects_sibling_prefix_escape(self):
        """FIX-008: the bulk path must refuse the escape per-entry, not per-request.

        A rejected bulk key is a soft failure (success=False on that entry), so the
        assertion is on the entry. The legitimate key in the same request is the
        control: it must still be signed, which proves the rejection is targeted
        rather than the whole request failing.
        """
        m = _wire(_load(), UNSLASHED_BASE)
        request = _parse({'downloadType': 'assetFile', 'keys': ['/legit.txt', ESCAPE_KEY]})

        response = m.download_asset_files_bulk('db1', 'asset1', request)

        by_key = {f.key: f for f in response.files}
        assert by_key['/legit.txt'].success is True
        assert by_key[ESCAPE_KEY].success is False
        assert by_key[ESCAPE_KEY].downloadUrl is None
        assert ESCAPE_KEY not in _signed_keys(m), (
            f"the escaping key was signed; signed keys were {_signed_keys(m)}"
        )


@pytest.mark.unit
class TestPermittedKeyShapesUnchanged:
    """Anti-over-tightening control for FIX-008.

    Every accepted caller shape, against a base key both with and without a
    trailing slash. These pass today and must keep producing the identical S3 key
    once containment is enforced -- a containment assert that assumes
    asset-relative input breaks the web viewers (which pass the full key from
    listFiles) and the recursive whole-asset download (which passes '/' or
    nothing).
    """

    @pytest.mark.parametrize("base_key", [UNSLASHED_BASE, SLASHED_BASE])
    @pytest.mark.parametrize("raw_key,expected_key", [
        ('/dir/f.glb', 'assets/pfx/dir/f.glb'),            # CLI / vamscli relative shape
        ('assets/pfx/dir/f.glb', 'assets/pfx/dir/f.glb'),  # web DynamicViewer files[0].key
        (None, None),                                      # key omitted -> asset root
        ('/', 'assets/pfx/'),                              # recursive prefix download
    ])
    def test_permitted_shape_resolves_to_same_key(self, base_key, raw_key, expected_key):
        m = _wire(_load(), base_key)
        body = {'downloadType': 'assetFile'}
        if raw_key is not None:
            body['key'] = raw_key
        request = _parse(body)

        response = m.download_asset_file('db1', 'asset1', request)

        # An omitted key resolves to the base key verbatim, whatever its shape.
        want = expected_key if expected_key is not None else base_key
        assert _signed_keys(m) == [want]
        assert response.downloadUrl == f"https://signed.example/{want}"


@pytest.mark.unit
class TestVersionPinInvariance:
    """FIX-008 control: changing how final_key is formed must not break the pin.

    The asset-version block strips the slash-normalized base off final_key to get
    the asset-relative key it looks up in the version snapshot. If containment
    enforcement reshapes final_key, that lookup can silently miss and the download
    degrades to 'latest' -- wrong bytes with a 200 status.
    """

    @pytest.mark.parametrize("base_key", [UNSLASHED_BASE, SLASHED_BASE])
    @pytest.mark.parametrize("raw_key", ['/dir/f.glb', 'assets/pfx/dir/f.glb'])
    def test_asset_version_id_resolves_same_relative_key(self, base_key, raw_key):
        m = _wire(_load(), base_key)
        # The pinned version deliberately differs from 'latest' (None), so a
        # silent fallback to latest fails this test rather than passing it.
        m.resolve_file_version_from_asset_version = MagicMock(return_value='v-pinned')
        request = _parse({'downloadType': 'assetFile', 'key': raw_key, 'assetVersionId': '2'})

        m.download_asset_file('db1', 'asset1', request)

        m.resolve_file_version_from_asset_version.assert_called_once_with(
            'db1', 'asset1', '2', 'dir/f.glb')
        params = m.s3.generate_presigned_url.call_args.kwargs['Params']
        assert params['Key'] == 'assets/pfx/dir/f.glb'
        assert params['VersionId'] == 'v-pinned'

    @pytest.mark.parametrize("raw_key", ['/dir/f.glb', 'assets/pfx/dir/f.glb'])
    def test_asset_version_alias_resolves_same_relative_key(self, raw_key):
        m = _wire(_load(), UNSLASHED_BASE)
        m.resolve_asset_version_id_from_alias = MagicMock(return_value='7')
        m.resolve_file_version_from_asset_version = MagicMock(return_value='v-alias')
        request = _parse({'downloadType': 'assetFile', 'key': raw_key,
                          'assetVersionIdAlias': 'release'})

        m.download_asset_file('db1', 'asset1', request)

        m.resolve_file_version_from_asset_version.assert_called_once_with(
            'db1', 'asset1', '7', 'dir/f.glb')
        assert m.s3.generate_presigned_url.call_args.kwargs['Params']['VersionId'] == 'v-alias'


@pytest.mark.unit
class TestDownloadAssetRequestModelKeyValidation:
    """FIX-008: the single `key` field is validated to the same standard as bulk `keys`.

    `keys` runs DOWNLOAD_KEY_ARRAY; `key` now runs `validate_asset_file_path`, so the
    single-file path is no longer the looser of the two. Note that the length bound below
    was live before this fix and the path checks were not: the field's only whitespace
    handling was a `strip_whitespace=` declaration, which is inert in Pydantic v1, so a
    whitespace-only key was reaching the handler. The rejection below comes from
    `validate_asset_file_path`, not from trimming — the key is still passed through verbatim.
    """

    def test_key_rejects_traversal_segments(self):
        with pytest.raises(Exception):
            _parse({'downloadType': 'assetFile', 'key': '/dir/../../other/secret.txt'})

    def test_key_rejects_whitespace_only(self):
        with pytest.raises(Exception):
            _parse({'downloadType': 'assetFile', 'key': '   '})

    def test_key_rejects_backslash(self):
        with pytest.raises(Exception):
            _parse({'downloadType': 'assetFile', 'key': 'assets/pfx/dir\\f.glb'})

    def test_a_legitimate_key_still_parses(self):
        """Paired control: the validator must not reject the ordinary case.

        Without this, all three rejection tests above are satisfied equally by a validator
        that rejects every key — which would break every single-file download.
        """
        parsed = _parse({'downloadType': 'assetFile', 'key': '/dir/sub/model.glb'})
        assert parsed.key == '/dir/sub/model.glb'

    def test_key_over_s3_limit_rejected(self):
        """Control: the length bound that IS live today must stay live."""
        from models.assetsV3 import MAX_S3_KEY_LENGTH
        with pytest.raises(Exception):
            _parse({'downloadType': 'assetFile', 'key': '/' + ('a' * MAX_S3_KEY_LENGTH)})

    @pytest.mark.parametrize("accepted", ['/dir/file.txt', 'assets/pfx/dir/file.txt'])
    def test_accepted_key_forms_stay_accepted(self, accepted):
        """Control: both caller shapes must survive attaching a validator to `key`."""
        model = _parse({'downloadType': 'assetFile', 'key': accepted})
        assert model.key == accepted

    @pytest.mark.parametrize("accepted", ['/dir/file.txt', 'assets/pfx/dir/file.txt'])
    def test_bulk_validator_accepts_the_same_forms(self, accepted):
        """Control: no single-vs-bulk divergence -- the defect being closed.

        Exercised through the real validators module (loaded by path) because the
        model-level bulk validation is what `key` should be aligned with.
        """
        import importlib.util
        validators_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..", "backend", "common", "validators.py"
        )
        spec = importlib.util.spec_from_file_location(
            "validators_key_containment_under_test", os.path.abspath(validators_path)
        )
        validators = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(validators)

        assert validators.validate_download_key_array('keys', [accepted])[0] is True
        assert validators.validate_download_key_array(
            'keys', ['/dir/../../other/secret.txt'])[0] is False
        assert validators.validate_download_key_array('keys', ['   '])[0] is False


# FIX-046 fixtures. 50 objects with the violation late in the listing: with a
# worker pool the violating object's future can complete in any order, so a
# single run can pass by luck.
PREFIX_OBJECT_COUNT = 50
VIOLATING_INDEX = 47
BLOCKED_CONTENT_TYPE = "application/x-msdownload"


def _wire_prefix(m, blocked_index=None, error_index=None):
    """Give the module a whole-asset prefix listing and per-object head responses."""
    keys = [f"{SLASHED_BASE}f{i}.bin" for i in range(PREFIX_OBJECT_COUNT)]
    m.list_all_objects = MagicMock(return_value=[{'Key': k} for k in keys])
    # Blocklist decision driven by the content type the head returns, so the
    # per-object check is real rather than a constant.
    m.validateUnallowedFileExtensionAndContentType = (
        lambda key, content_type: content_type != BLOCKED_CONTENT_TYPE
    )

    blocked_key = keys[blocked_index] if blocked_index is not None else None
    error_key = keys[error_index] if error_index is not None else None

    def _head(Bucket, Key, **kwargs):
        if Key == error_key:
            raise ClientError({'Error': {'Code': 'ThrottlingException'}}, 'HeadObject')
        if Key == blocked_key:
            return {'ContentType': BLOCKED_CONTENT_TYPE, 'ContentLength': 10}
        return {'ContentType': 'text/plain', 'ContentLength': 10}

    m.s3.head_object.side_effect = _head
    return keys


def _headed_keys(m):
    """Keys passed to head_object.

    Read from call_args_list rather than call_count: the list append is atomic
    under the GIL while the counter's read-modify-write is not, and these calls
    are issued from a worker pool.
    """
    return [c.kwargs['Key'] for c in m.s3.head_object.call_args_list]


@pytest.mark.unit
class TestPrefixContentTypeValidationFanOut:
    """FIX-046 -- the whole-asset prefix validation fans out and stays fail-closed.

    Every object under the asset prefix costs one HeadObject, so a realistic asset
    (thousands of files) cannot finish serially inside the API Gateway integration
    timeout. Parallelising the check must not make it probabilistic: the violating
    object must lose the race in every ordering, no object may go unchecked, and a
    HeadObject failure must never read as "allowed".
    """

    def test_violating_object_fails_prefix_on_every_run(self):
        for _ in range(10):
            m = _wire(_load(), SLASHED_BASE)
            _wire_prefix(m, blocked_index=VIOLATING_INDEX)
            assert m.validate_prefix_content_types('test-bucket', SLASHED_BASE) is False

    def test_all_allowed_objects_pass_with_exactly_one_head_each(self):
        """Negative control: proves the listing is walked, not short-circuited."""
        m = _wire(_load(), SLASHED_BASE)
        keys = _wire_prefix(m)

        assert m.validate_prefix_content_types('test-bucket', SLASHED_BASE) is True
        assert len(_headed_keys(m)) == PREFIX_OBJECT_COUNT
        assert set(_headed_keys(m)) == set(keys)

    def test_head_object_error_is_never_read_as_allowed(self):
        """Pinned semantics: a HeadObject failure propagates rather than passing.

        Returning True would sign the prefix on a throttled check; returning False
        would report the asset as containing a disallowed file. The caller turns a
        raised error into a failed download with no URL minted.
        """
        for _ in range(5):
            m = _wire(_load(), SLASHED_BASE)
            _wire_prefix(m, error_index=VIOLATING_INDEX)
            with pytest.raises(ClientError):
                m.validate_prefix_content_types('test-bucket', SLASHED_BASE)

    def test_empty_prefix_costs_no_head_calls(self):
        m = _wire(_load(), SLASHED_BASE)
        assert m.validate_prefix_content_types('test-bucket', SLASHED_BASE) is True
        assert _headed_keys(m) == []

    def test_pool_is_bounded_and_connections_cover_the_workers(self):
        """Without enough pooled connections the threads serialize and gain nothing."""
        m = _load()
        assert 1 < m.MAX_PARALLEL_S3_WORKERS <= 100
        assert m.s3_config.max_pool_connections >= m.MAX_PARALLEL_S3_WORKERS
        assert m.s3_config.retries['mode'] == 'adaptive'

    def test_whole_asset_download_still_rejects_a_blocked_object(self):
        """The download itself must still refuse a prefix holding a blocked object."""
        m = _wire(_load(), SLASHED_BASE)
        _wire_prefix(m, blocked_index=VIOLATING_INDEX)
        request = _parse({'downloadType': 'assetFile', 'key': '/'})

        with pytest.raises(m.VAMSGeneralErrorResponse):
            m.download_asset_file('db1', 'asset1', request)

        assert m.s3.generate_presigned_url.call_count == 0

    def test_whole_asset_download_signs_prefix_when_every_object_is_allowed(self):
        """Positive control: the permitted whole-asset download still succeeds."""
        m = _wire(_load(), SLASHED_BASE)
        _wire_prefix(m)
        request = _parse({'downloadType': 'assetFile', 'key': '/'})

        response = m.download_asset_file('db1', 'asset1', request)

        assert _signed_keys(m) == [SLASHED_BASE]
        assert response.downloadUrl == f"https://signed.example/{SLASHED_BASE}"
        assert len(_headed_keys(m)) == PREFIX_OBJECT_COUNT
