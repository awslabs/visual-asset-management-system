# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The export route applies the executable blocklist before it signs a download URL (issue #406).

`downloadAsset.py`, `streamAsset.py` and `streamAuxiliaryPreviewAsset.py` each `head_object` the
file they are about to serve and refuse with a 400 when `validateUnallowedFileExtensionAndContentType`
rejects its key or its reported `ContentType`. The export path signed every live file of a
distributable asset without that check, so a `.exe` that reached a bucket by a direct S3 write or
a workflow output -- upload rejects it -- got a working URL from export where download refused.

The export now heads the version it is about to sign and runs the shared helper on the result.
A blocklisted file is withheld, not refused: it stays in the listing with
`presignedFileDownloadUrl: None`, exactly the #399 shape for a non-distributable asset, and it
is absent from the download audit entry because nothing was downloaded. A `head_object` that
fails is treated the same way, so a file whose type cannot be verified is never signed.

The helper under test is the real one from `common/s3.py`, loaded from source with the real
blocklists (the conftest stand-in always returns True). `TOOL.EXE` with a benign content type is
the case that depends on #412: the helper lower-cases the extension before the membership test,
so the file is withheld only when that fix is on the branch.

The cost guard is pinned from both sides: no `head_object` is issued when URLs are not requested
or when the asset is not distributable, so only a file that is about to be signed pays the read.
"""

import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

# The loader and the shared fixtures of the #399 suite: assetExportService cannot be imported
# normally because the root conftest registers a mock `handlers` package that shadows the real one.
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
    _DB,
    _ASSET,
)
from tests.handlers.assets.test_assetExportService_download_controls import (  # noqa: E402
    _SIGNED_URL,
    _EVENT,
    _Spies,
    _asset_item,
    _audit_calls_by_asset,
    _entries_by_asset,
    _patches,
    _urls,
)

_PREFIX = f"{_DB}/{_ASSET}/"
_WITHHELD_TAG = "Export withheld download URL for blocklisted file type"

_REPO_BACKEND = os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend")
_S3_SOURCE = os.path.join(_REPO_BACKEND, "common", "s3.py")
_CONSTANTS_SOURCE = os.path.join(_REPO_BACKEND, "common", "constants.py")


def _load_real_blocklist_helper():
    """validateUnallowedFileExtensionAndContentType from the real source, with the real lists.

    tests/conftest.py replaces `common.s3` with a stand-in that always returns True and
    `common.constants` with a MagicMock, so the helper is executed from its own source segment
    with the blocklists read from the real constants module by path -- the way
    tests/common/test_s3_blocklist_case_insensitive.py loads it.
    """
    spec = importlib.util.spec_from_file_location("constants_for_export_blocklist", _CONSTANTS_SOURCE)
    constants = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(constants)

    with open(_S3_SOURCE, encoding="utf-8") as f:
        source = f.read()
    start = source.index("def validateUnallowedFileExtensionAndContentType(")
    end = source.index("\ndef validateS3AssetExtensionsAndContentType(", start)
    namespace = {
        "os": os,
        "logger": MagicMock(),
        "UNALLOWED_MIME_LIST": constants.UNALLOWED_MIME_LIST,
        "UNALLOWED_FILE_EXTENSION_LIST": constants.UNALLOWED_FILE_EXTENSION_LIST,
    }
    exec(compile(source[start:end], _S3_SOURCE, "exec"), namespace)
    return namespace["validateUnallowedFileExtensionAndContentType"]


def _row(name, version_id=None):
    """One live file of the asset. `version_id` overrides the per-file `v-<name>` default verbatim."""
    return {
        'fileName': name, 'key': f"{_PREFIX}{name}", 'relativePath': f"/{name}",
        'isFolder': False, 'dateCreatedCurrentVersion': '2026-01-01T00:00:00',
        'storageClass': 'STANDARD', 'versionId': version_id if version_id is not None else f"v-{name}",
        'isArchived': False, 'primaryType': None, 'size': 10,
    }


class _S3:
    """A stand-in for the module's s3_client that answers head_object per key.

    `content_types` maps a file name to the ContentType the head reports; a name in `failing`
    raises the ClientError S3 raises for a version that is gone. Every other client call keeps
    the MagicMock behaviour the suites rely on.
    """

    def __init__(self, content_types, failing=()):
        self.client = MagicMock()
        self.client.head_object = MagicMock(side_effect=self._head)
        self._content_types = content_types
        self._failing = set(failing)
        self.heads = []

    def _head(self, **params):
        self.heads.append(params)
        name = params['Key'][len(_PREFIX):]
        if name in self._failing:
            raise ClientError({'Error': {'Code': 'NoSuchVersion', 'Message': 'gone'}}, 'HeadObject')
        return {'ContentType': self._content_types[name], 'ContentLength': 10}


def _run(m, spies, rows, s3, asset=None, validate=None, **request_overrides):
    """Drive process_asset_batch for one asset with the given listing, s3 client and helper."""
    asset = asset or _asset_item(distributable=True)
    request_model = m.AssetExportRequestModel(
        includeFileMetadata=False, includeAssetMetadata=False, **request_overrides)
    patches = _patches(m, [asset], spies, listings={_ASSET: rows}) + [
        patch.object(m, "s3_client", s3.client),
        patch.object(m, "validateUnallowedFileExtensionAndContentType",
                     validate or _load_real_blocklist_helper()),
    ]
    for one in patches:
        one.start()
    try:
        exported, _page_state = m.process_asset_batch(
            [{'databaseId': _DB, 'assetId': _ASSET, 'isRoot': True}],
            request_model, {"tokens": ["alice"], "roles": []}, _EVENT)
    finally:
        for one in reversed(patches):
            one.stop()
    return _entries_by_asset(exported)[_ASSET]


def _warning_lines(spies):
    return [str(call.args[0]) for call in spies.log.warning.call_args_list]


def _audited_paths(spies):
    audited = _audit_calls_by_asset(spies)
    return [entry["filePath"] for entry in audited[_ASSET][2]] if _ASSET in audited else []


# model.glb is the clean control; tool.exe is blocked on both lists; TOOL.EXE carries a content
# type on no list, so only the (case-insensitive) extension check can withhold it.
_MIXED = {
    'model.glb': 'model/gltf-binary',
    'tool.exe': 'application/x-msdownload',
    'TOOL.EXE': 'application/octet-stream',
}


@pytest.mark.unit
class TestBlocklistedFilesAreListedWithoutAUrl:
    def test_exe_files_get_no_url_while_the_clean_file_is_signed_and_audited(self):
        """The distinguishing assertion: the pre-fix loop signed all three."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3(_MIXED)

        entry = _run(m, spies, [_row(name) for name in _MIXED], s3, generatePresignedUrls=True)

        assert _urls(entry) == {
            '/model.glb': _SIGNED_URL,
            '/tool.exe': None,
            '/TOOL.EXE': None,
        }, _urls(entry)
        withheld = [file for file in entry['files'] if file['presignedFileDownloadUrl'] is None]
        assert all(file['presignedFileDownloadExpiresIn'] is None for file in withheld), withheld
        assert [file['relativePath'] for file in entry['files']] == ['/model.glb', '/tool.exe', '/TOOL.EXE']
        spies.sign.assert_called_once()
        assert spies.sign.call_args.args[1] == f"{_PREFIX}model.glb"
        assert _audited_paths(spies) == [f"{_PREFIX}model.glb"]

    def test_an_upper_case_extension_is_withheld(self):
        """TOOL.EXE with a benign content type: only a case-insensitive extension check catches it.

        This is the case that depends on #412. Without that fix the helper compares `.EXE`
        against a lower-case list, passes it, and the export signs the file.
        """
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3({'TOOL.EXE': 'application/octet-stream'})

        entry = _run(m, spies, [_row('TOOL.EXE')], s3, generatePresignedUrls=True)

        assert _urls(entry) == {'/TOOL.EXE': None}, _urls(entry)
        spies.sign.assert_not_called()
        spies.audit.assert_not_called()

    def test_a_clean_extension_with_a_blocklisted_content_type_is_withheld(self):
        """The content-type branch of the helper decides on its own: notes.txt is on no extension list."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3({'model.glb': 'model/gltf-binary', 'notes.txt': 'application/x-msdownload'})

        entry = _run(m, spies, [_row('model.glb'), _row('notes.txt')], s3, generatePresignedUrls=True)

        assert _urls(entry) == {'/model.glb': _SIGNED_URL, '/notes.txt': None}, _urls(entry)
        assert _audited_paths(spies) == [f"{_PREFIX}model.glb"]

    def test_the_entry_keeps_its_shape_and_the_export_succeeds(self):
        """Withhold, do not fail: the asset entry is complete and reports its flag unchanged."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3(_MIXED)

        entry = _run(m, spies, [_row(name) for name in _MIXED], s3, generatePresignedUrls=True)

        assert entry['assetid'] == _ASSET
        assert entry['isdistributable'] is True
        assert entry.get('unauthorizedAsset') is None, entry
        assert len(entry['files']) == 3
        spies.log.exception.assert_not_called()


@pytest.mark.unit
class TestWithheldFilesAreLogged:
    def test_one_warning_per_withheld_file_naming_ids_and_the_extension_only(self):
        """The line an operator searches for carries the ids and the extension -- never the key."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3(_MIXED)

        _run(m, spies, [_row(name) for name in _MIXED], s3, generatePresignedUrls=True)

        lines = [line for line in _warning_lines(spies) if _WITHHELD_TAG in line]
        assert len(lines) == 2, _warning_lines(spies)
        for line in lines:
            assert _ASSET in line and _DB in line, line
            assert ".exe" in line, line
            for name in _MIXED:
                assert f"{_PREFIX}{name}" not in line, line
                assert name not in line, line
            assert _PREFIX not in line, line

    def test_no_warning_when_every_file_is_clean(self):
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3({'model.glb': 'model/gltf-binary'})

        _run(m, spies, [_row('model.glb')], s3, generatePresignedUrls=True)

        assert [line for line in _warning_lines(spies) if _WITHHELD_TAG in line] == []


@pytest.mark.unit
class TestHeadObjectFailureWithholdsTheFile:
    def test_a_file_whose_head_fails_is_not_signed_and_the_rest_are(self):
        """A type that cannot be verified is not a pass; the export continues for the other files."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3({'model.glb': 'model/gltf-binary', 'texture.png': 'image/png'}, failing={'texture.png'})

        entry = _run(m, spies, [_row('model.glb'), _row('texture.png')], s3, generatePresignedUrls=True)

        assert _urls(entry) == {'/model.glb': _SIGNED_URL, '/texture.png': None}, _urls(entry)
        assert _audited_paths(spies) == [f"{_PREFIX}model.glb"]
        spies.sign.assert_called_once()
        spies.log.exception.assert_not_called()

    def test_the_failure_is_logged_once_with_ids_and_no_key(self):
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3({'model.glb': 'model/gltf-binary', 'texture.png': 'image/png'}, failing={'texture.png'})

        _run(m, spies, [_row('model.glb'), _row('texture.png')], s3, generatePresignedUrls=True)

        lines = [line for line in _warning_lines(spies) if 'HeadObject' in line]
        assert len(lines) == 1, _warning_lines(spies)
        assert _ASSET in lines[0] and _DB in lines[0], lines[0]
        assert 'texture.png' not in lines[0], lines[0]


@pytest.mark.unit
class TestNoHeadObjectWhenNothingWouldBeSigned:
    def test_urls_not_requested_issues_no_head_object(self):
        """Cost guard: the check runs only for a file that is about to be signed."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3(_MIXED)

        entry = _run(m, spies, [_row(name) for name in _MIXED], s3)

        assert s3.heads == [], s3.heads
        assert all(url is None for url in _urls(entry).values()), _urls(entry)
        spies.sign.assert_not_called()

    def test_a_non_distributable_asset_issues_no_head_object(self):
        """The distributable gate runs first; nothing of a non-distributable asset is read."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3(_MIXED)

        entry = _run(m, spies, [_row(name) for name in _MIXED], s3,
                     asset=_asset_item(distributable=False), generatePresignedUrls=True)

        assert s3.heads == [], s3.heads
        assert entry['isdistributable'] is False
        assert all(url is None for url in _urls(entry).values()), _urls(entry)
        spies.sign.assert_not_called()


@pytest.mark.unit
class TestTheHeadIsPinnedToTheVersionBeingSigned:
    def test_one_head_per_live_file_carrying_the_listing_version(self):
        """The type checked is the type served: the head names the version the signer is handed."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3(_MIXED)

        _run(m, spies, [_row(name) for name in _MIXED], s3, generatePresignedUrls=True)

        assert [head['Key'] for head in s3.heads] == [f"{_PREFIX}{name}" for name in _MIXED], s3.heads
        assert all(head['Bucket'] == 'bucket-name' for head in s3.heads), s3.heads
        assert [head['VersionId'] for head in s3.heads] == [f"v-{name}" for name in _MIXED], s3.heads
        signed_key, signed_version = spies.sign.call_args.args[1], spies.sign.call_args.args[2]
        assert (signed_key, signed_version) == (f"{_PREFIX}model.glb", "v-model.glb")

    def test_an_unversioned_listing_heads_without_a_version_id(self):
        """`'null'` is what an unversioned bucket lists; the signer drops it, so must the head."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3({'model.glb': 'model/gltf-binary'})

        entry = _run(m, spies, [_row('model.glb', version_id='null')], s3, generatePresignedUrls=True)

        assert len(s3.heads) == 1 and 'VersionId' not in s3.heads[0], s3.heads
        assert _urls(entry) == {'/model.glb': _SIGNED_URL}, _urls(entry)
