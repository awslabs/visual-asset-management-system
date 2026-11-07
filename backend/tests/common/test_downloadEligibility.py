# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.downloadEligibility: the checks a file passes before VAMS hands out its content.

downloadAsset.py delegates to this module (its own suites, test_downloadAsset_bulk.py and
test_downloadAsset_key_containment.py, pin the handler's behaviour end to end); these tests pin the
module's own contract, which the Download as Zip service will call directly: every collaborator is
passed in, every refusal is a VAMSGeneralErrorResponse with a generic message (400, or 410 for a
delete-marker version), and the checks run in a fixed order so a refused key never reaches S3.

The module is the REAL one, registered by tests/conftest.py. common.s3 is the test mock there (its
blocklist always allows), so each test passes the blocklist and listing it wants explicitly.
"""

import os
import sys
import threading
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

de = sys.modules['common.downloadEligibility']
from models.common import VAMSGeneralErrorResponse  # noqa: E402

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "common", "downloadEligibility.py"
)

UNSLASHED_BASE = "assets/pfx"
SLASHED_BASE = "assets/pfx/"
ESCAPE_KEY = "assets/pfxOTHER/secret.txt"
BLOCKED_CONTENT_TYPE = "application/x-msdownload"


def _msg(message):
    """The message as a caller sees it: VAMSGeneralErrorResponse prefixes it."""
    return f"VAMS General Error: {message}"


def _client_error(code):
    return ClientError({"Error": {"Code": code}}, "HeadObject")


def _allow_unless_blocked(key, content_type):
    return content_type != BLOCKED_CONTENT_TYPE


def _s3(head=None, head_error=None):
    client = MagicMock()
    if head_error is not None:
        client.head_object.side_effect = head_error
    else:
        client.head_object.return_value = head or {'ContentType': 'text/plain', 'ContentLength': 1}
    return client


def _check(s3_client, raw_key, base=SLASHED_BASE, **kwargs):
    kwargs.setdefault('is_allowed', _allow_unless_blocked)
    kwargs.setdefault('list_objects', MagicMock(return_value=[]))
    kwargs.setdefault('object_exists', MagicMock(return_value=True))
    return de.check_file_eligibility('db1', 'asset1', s3_client, 'bucket', base, raw_key, **kwargs)


@pytest.mark.unit
class TestIsFileArchived:
    @pytest.mark.parametrize("head", [
        {'Metadata': {'vams-status': 'archived'}},
        {'Metadata': {'vams-status': 'deleted'}},
        {'StorageClass': 'GLACIER'},
        {'StorageClass': 'DEEP_ARCHIVE'},
    ])
    def test_archived_shapes(self, head):
        assert de.is_file_archived(head) is True

    @pytest.mark.parametrize("head", [
        {},
        {'Metadata': {}},
        {'Metadata': {'vams-status': 'active'}, 'StorageClass': 'STANDARD'},
        {'StorageClass': 'GLACIER_IR'},
        {'StorageClass': 'INTELLIGENT_TIERING'},
        {'StorageClass': 'STANDARD_IA'},
    ])
    def test_live_shapes(self, head):
        """Control: readable storage classes and a live status are not archived."""
        assert de.is_file_archived(head) is False

    def test_status_key_is_the_canonical_one(self):
        """The metadata key is the shared constant, not a local literal."""
        from common.s3MetadataKeys import VAMS_STATUS_METADATA_KEY, VAMS_STATUS_ARCHIVED
        assert de.is_file_archived({'Metadata': {VAMS_STATUS_METADATA_KEY: VAMS_STATUS_ARCHIVED}})


@pytest.mark.unit
class TestDistributableAsset:
    def test_missing_asset_is_not_found(self):
        """An archived asset is not in the live table, so its getter returns None."""
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            de.require_distributable_asset(None)
        assert str(e.value) == _msg("Asset not found in database")

    @pytest.mark.parametrize("asset", [
        {'assetLocation': {'Key': 'a/'}},
        {'isDistributable': False, 'assetLocation': {'Key': 'a/'}},
    ])
    def test_not_distributable_refused(self, asset):
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            de.require_distributable_asset(asset)
        assert str(e.value) == _msg("Asset not distributable")

    def test_missing_location_refused(self):
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            de.require_distributable_asset({'isDistributable': True})
        assert str(e.value) == _msg("Asset location not found")

    def test_context_reads_each_record_once(self):
        get_asset = MagicMock(return_value={
            'isDistributable': True, 'bucketId': 'b-1', 'assetLocation': {'Key': 'asset1/'}})
        get_bucket = MagicMock(return_value={'bucketName': 'bucket-name'})

        assert de.get_distributable_asset_context(
            'db1', 'asset1', get_asset=get_asset, get_bucket_details=get_bucket
        ) == ('bucket-name', 'asset1/')
        get_asset.assert_called_once_with('db1', 'asset1')
        get_bucket.assert_called_once_with('b-1')

    def test_refused_asset_never_reads_the_bucket(self):
        get_bucket = MagicMock()
        with pytest.raises(VAMSGeneralErrorResponse):
            de.get_distributable_asset_context(
                'db1', 'asset1',
                get_asset=MagicMock(return_value={'isDistributable': False}),
                get_bucket_details=get_bucket)
        get_bucket.assert_not_called()


@pytest.mark.unit
class TestResolveAssetFileKey:
    @pytest.mark.parametrize("base", [UNSLASHED_BASE, SLASHED_BASE])
    @pytest.mark.parametrize("raw_key,expected", [
        ('/dir/f.glb', 'assets/pfx/dir/f.glb'),
        ('dir/f.glb', 'assets/pfx/dir/f.glb'),
        ('assets/pfx/dir/f.glb', 'assets/pfx/dir/f.glb'),
        ('/', 'assets/pfx/'),
    ])
    def test_accepted_shapes(self, base, raw_key, expected):
        assert de.resolve_asset_file_key(base, raw_key) == expected

    @pytest.mark.parametrize("base", [UNSLASHED_BASE, SLASHED_BASE])
    @pytest.mark.parametrize("raw_key", [None, ''])
    def test_no_key_is_the_base_key_verbatim(self, base, raw_key):
        assert de.resolve_asset_file_key(base, raw_key) == base

    def test_sibling_prefix_escape_refused_without_echoing_the_key(self):
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            de.resolve_asset_file_key(UNSLASHED_BASE, ESCAPE_KEY)
        assert str(e.value) == _msg("Requested file is not part of this asset")
        assert ESCAPE_KEY not in str(e.value)

    def test_leading_slash_sibling_shape_stays_inside_the_asset(self):
        """'/assets/pfxOTHER/x' is asset-relative, so it is joined under the base, not refused."""
        assert de.resolve_asset_file_key(UNSLASHED_BASE, '/assets/pfxOTHER/x') == \
            'assets/pfx/assets/pfxOTHER/x'

    def test_over_long_resolved_key_refused(self):
        from common.validators import exceeds_s3_object_key_limit
        relative = '/' + 'a' * 1020
        assert exceeds_s3_object_key_limit(SLASHED_BASE + relative.lstrip('/'))
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            de.resolve_asset_file_key(SLASHED_BASE, relative)
        assert str(e.value) == _msg("File key exceeds the maximum S3 object key length")

    def test_key_at_the_limit_accepted(self):
        """Control: the length bound is the S3 limit, not something tighter."""
        relative = 'a' * (1024 - len(SLASHED_BASE))
        assert de.resolve_asset_file_key(SLASHED_BASE, relative) == SLASHED_BASE + relative

    @pytest.mark.parametrize("base", [UNSLASHED_BASE, SLASHED_BASE])
    @pytest.mark.parametrize("final_key", ['assets/pfx/dir/f.glb', 'assets/pfx//dir/f.glb'])
    def test_relative_key_for_version_snapshots(self, base, final_key):
        assert de.asset_relative_key(final_key, base) == 'dir/f.glb'


@pytest.mark.unit
class TestResolvePinnedVersionId:
    def test_asset_version_id(self):
        resolve_file = MagicMock(return_value='s3-v')
        resolve_alias = MagicMock()
        assert de.resolve_pinned_version_id(
            'db1', 'asset1', 'dir/f.glb', asset_version_id='2',
            resolve_file_version=resolve_file, resolve_alias=resolve_alias) == 's3-v'
        resolve_file.assert_called_once_with('db1', 'asset1', '2', 'dir/f.glb')
        resolve_alias.assert_not_called()

    def test_alias_resolves_through_its_version(self):
        resolve_file = MagicMock(return_value='s3-v')
        resolve_alias = MagicMock(return_value='7')
        assert de.resolve_pinned_version_id(
            'db1', 'asset1', 'dir/f.glb', asset_version_alias='release',
            resolve_file_version=resolve_file, resolve_alias=resolve_alias) == 's3-v'
        resolve_alias.assert_called_once_with('db1', 'asset1', 'release')
        resolve_file.assert_called_once_with('db1', 'asset1', '7', 'dir/f.glb')

    def test_alias_wins_over_id(self):
        resolve_file = MagicMock(return_value='s3-v')
        resolve_alias = MagicMock(return_value='7')
        de.resolve_pinned_version_id(
            'db1', 'asset1', 'k', asset_version_id='2', asset_version_alias='release',
            resolve_file_version=resolve_file, resolve_alias=resolve_alias)
        resolve_file.assert_called_once_with('db1', 'asset1', '7', 'k')

    def test_resolver_refusal_propagates(self):
        resolve_file = MagicMock(side_effect=VAMSGeneralErrorResponse("File not found in asset version"))
        with pytest.raises(VAMSGeneralErrorResponse):
            de.resolve_pinned_version_id('db1', 'asset1', 'k', asset_version_id='2',
                                         resolve_file_version=resolve_file, resolve_alias=MagicMock())

    def test_no_pin_is_a_caller_bug(self):
        with pytest.raises(ValueError):
            de.resolve_pinned_version_id('db1', 'asset1', 'k',
                                         resolve_file_version=MagicMock(), resolve_alias=MagicMock())

    def test_missing_resolvers_is_a_caller_bug(self):
        with pytest.raises(ValueError):
            de.resolve_pinned_version_id('db1', 'asset1', 'k', asset_version_id='2')


@pytest.mark.unit
class TestS3ObjectExists:
    def test_present(self):
        client = _s3()
        assert de.s3_object_exists(client, 'b', 'k') is True
        client.head_object.assert_called_once_with(Bucket='b', Key='k')

    def test_version_is_headed(self):
        client = _s3()
        de.s3_object_exists(client, 'b', 'k', 'v1')
        client.head_object.assert_called_once_with(Bucket='b', Key='k', VersionId='v1')

    def test_404_is_absent(self):
        assert de.s3_object_exists(_s3(head_error=_client_error('404')), 'b', 'k') is False

    def test_other_errors_raise(self):
        """A failed check is never read as absent (or present)."""
        with pytest.raises(ClientError):
            de.s3_object_exists(_s3(head_error=_client_error('403')), 'b', 'k')


def _prefix_client(keys, blocked=(), error=()):
    client = MagicMock()

    def _head(Bucket, Key, **kwargs):
        if Key in error:
            raise _client_error('ThrottlingException')
        if Key in blocked:
            return {'ContentType': BLOCKED_CONTENT_TYPE}
        return {'ContentType': 'text/plain'}
    client.head_object.side_effect = _head
    listing = MagicMock(return_value=[{'Key': k} for k in keys])
    return client, listing


@pytest.mark.unit
class TestValidatePrefixContentTypes:
    KEYS = [f"{SLASHED_BASE}f{i}.bin" for i in range(40)]

    def test_every_object_checked_once(self):
        client, listing = _prefix_client(self.KEYS)
        assert de.validate_prefix_content_types(
            client, 'b', SLASHED_BASE, list_objects=listing, is_allowed=_allow_unless_blocked) is True
        listing.assert_called_once_with('b', SLASHED_BASE, client=client)
        assert client.head_object.call_args_list
        assert sorted(c.kwargs['Key'] for c in client.head_object.call_args_list) == sorted(self.KEYS)

    def test_one_blocked_object_refuses_the_prefix(self):
        for _ in range(5):
            client, listing = _prefix_client(self.KEYS, blocked={self.KEYS[37]})
            assert de.validate_prefix_content_types(
                client, 'b', SLASHED_BASE, list_objects=listing, is_allowed=_allow_unless_blocked) is False

    def test_head_error_propagates(self):
        client, listing = _prefix_client(self.KEYS, error={self.KEYS[11]})
        with pytest.raises(ClientError):
            de.validate_prefix_content_types(
                client, 'b', SLASHED_BASE, list_objects=listing, is_allowed=_allow_unless_blocked)

    def test_empty_prefix_costs_no_heads(self):
        client, listing = _prefix_client([])
        assert de.validate_prefix_content_types(
            client, 'b', SLASHED_BASE, list_objects=listing, is_allowed=_allow_unless_blocked) is True
        client.head_object.assert_not_called()

    def test_pool_never_exceeds_max_workers(self):
        in_flight = {'now': 0, 'peak': 0}
        lock = threading.Lock()
        gate = threading.Event()
        client = MagicMock()

        def _head(Bucket, Key, **kwargs):
            with lock:
                in_flight['now'] += 1
                in_flight['peak'] = max(in_flight['peak'], in_flight['now'])
            gate.wait(0.01)
            with lock:
                in_flight['now'] -= 1
            return {'ContentType': 'text/plain'}
        client.head_object.side_effect = _head
        listing = MagicMock(return_value=[{'Key': k} for k in self.KEYS])

        assert de.validate_prefix_content_types(
            client, 'b', SLASHED_BASE, max_workers=3, list_objects=listing,
            is_allowed=_allow_unless_blocked) is True
        assert 1 <= in_flight['peak'] <= 3

    def test_defaults_are_the_shared_common_s3_helpers(self):
        """With no overrides the module lists to exhaustion and applies the one blocklist helper."""
        # Asserted on the source: other suites re-register common.s3 during a run, so an identity
        # check against sys.modules would depend on collection order.
        source = open(_MODULE_PATH).read()
        assert "from common.s3 import list_all_objects, validateUnallowedFileExtensionAndContentType" in source
        assert "list_objects = list_objects or list_all_objects" in source
        assert "is_allowed = is_allowed or validateUnallowedFileExtensionAndContentType" in source


@pytest.mark.unit
class TestCheckFileEligibilitySingleFile:
    def test_latest_file_passes(self):
        client = _s3()
        assert _check(client, '/dir/f.glb') == ('assets/pfx/dir/f.glb', None)
        client.head_object.assert_called_once_with(Bucket='bucket', Key='assets/pfx/dir/f.glb')

    def test_requested_version_is_headed_and_returned(self):
        client = _s3()
        assert _check(client, '/f.glb', version_id='v-old') == ('assets/pfx/f.glb', 'v-old')
        client.head_object.assert_called_once_with(Bucket='bucket', Key='assets/pfx/f.glb', VersionId='v-old')

    @pytest.mark.parametrize("code", ['404', 'NoSuchKey'])
    def test_missing_file(self, code):
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            _check(_s3(head_error=_client_error(code)), '/f.glb')
        assert str(e.value) == _msg("File not found in S3")
        assert getattr(e.value, 'status_code', 400) == 400

    @pytest.mark.parametrize("code", ['405', 'MethodNotAllowed'])
    def test_delete_marker_version_is_410(self, code):
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            _check(_s3(head_error=_client_error(code)), '/f.glb', version_id='v-dm')
        assert str(e.value) == _msg("File version has been archived and cannot be downloaded")
        assert e.value.status_code == 410

    def test_other_head_error_is_a_generic_refusal(self):
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            _check(_s3(head_error=_client_error('AccessDenied')), '/f.glb')
        assert str(e.value) == _msg("Error checking file in S3")

    def test_blocked_type_refused(self):
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            _check(_s3(head={'ContentType': BLOCKED_CONTENT_TYPE}), '/f.bin')
        assert str(e.value) == _msg("Unallowed file extension or content type in asset file")

    def test_blocklist_sees_the_resolved_key_and_reported_type(self):
        is_allowed = MagicMock(return_value=True)
        _check(_s3(head={'ContentType': 'model/gltf-binary'}), '/dir/f.glb', is_allowed=is_allowed)
        is_allowed.assert_called_once_with('assets/pfx/dir/f.glb', 'model/gltf-binary')

    def test_escape_refused_before_any_s3_call(self):
        client = _s3()
        with pytest.raises(VAMSGeneralErrorResponse):
            _check(client, ESCAPE_KEY, base=UNSLASHED_BASE)
        client.head_object.assert_not_called()


@pytest.mark.unit
class TestCheckFileEligibilityPin:
    def test_pin_replaces_the_per_file_version_and_is_headed(self):
        client = _s3()
        resolve_file = MagicMock(return_value='v-pinned')
        final_key, version_id = _check(
            client, 'assets/pfx/dir/f.glb', base=UNSLASHED_BASE, asset_version_id='2',
            resolve_file_version=resolve_file, resolve_alias=MagicMock())
        assert (final_key, version_id) == ('assets/pfx/dir/f.glb', 'v-pinned')
        resolve_file.assert_called_once_with('db1', 'asset1', '2', 'dir/f.glb')
        client.head_object.assert_called_once_with(
            Bucket='bucket', Key='assets/pfx/dir/f.glb', VersionId='v-pinned')

    def test_pin_refusal_happens_before_any_s3_call(self):
        client = _s3()
        resolve_file = MagicMock(side_effect=VAMSGeneralErrorResponse("File not found in asset version"))
        with pytest.raises(VAMSGeneralErrorResponse):
            _check(client, '/f.glb', asset_version_id='2',
                   resolve_file_version=resolve_file, resolve_alias=MagicMock())
        client.head_object.assert_not_called()

    def test_pinned_delete_marker_is_410(self):
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            _check(_s3(head_error=_client_error('405')), '/f.glb', asset_version_alias='release',
                   resolve_file_version=MagicMock(return_value='v-dm'),
                   resolve_alias=MagicMock(return_value='3'))
        assert e.value.status_code == 410


@pytest.mark.unit
class TestCheckFileEligibilityPrefix:
    KEYS = [f"{SLASHED_BASE}f{i}.bin" for i in range(12)]

    def test_allowed_prefix_passes(self):
        client, listing = _prefix_client(self.KEYS)
        exists = MagicMock(return_value=True)
        assert _check(client, '/', list_objects=listing, object_exists=exists) == (SLASHED_BASE, None)
        exists.assert_called_once_with('bucket', SLASHED_BASE)
        assert client.head_object.call_count == len(self.KEYS)

    def test_one_blocked_object_refuses_the_whole_prefix(self):
        client, listing = _prefix_client(self.KEYS, blocked={self.KEYS[-1]})
        exists = MagicMock(return_value=True)
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            _check(client, '/', list_objects=listing, object_exists=exists)
        assert str(e.value) == _msg("Unallowed file extension or content type in asset file")
        exists.assert_not_called()

    def test_missing_prefix_refused(self):
        client, listing = _prefix_client([])
        with pytest.raises(VAMSGeneralErrorResponse) as e:
            _check(client, '/', list_objects=listing, object_exists=MagicMock(return_value=False))
        assert str(e.value) == _msg("File not found in S3")

    def test_default_existence_check_uses_the_passed_client(self):
        client, listing = _prefix_client([])
        de.check_file_eligibility('db1', 'asset1', client, 'bucket', SLASHED_BASE, '/',
                                  list_objects=listing, is_allowed=_allow_unless_blocked)
        client.head_object.assert_called_once_with(Bucket='bucket', Key=SLASHED_BASE)


@pytest.mark.unit
class TestModuleHygiene:
    """The module is shared library code: no clients at import, no service name of its own."""

    def test_no_aws_client_or_table_at_import(self):
        source = open(_MODULE_PATH).read()
        assert "boto3" not in source
        assert "get_table_name" not in source

    def test_logger_is_unnamed(self):
        source = open(_MODULE_PATH).read()
        assert "logger = safeLogger()" in source
        assert "service_name" not in source
