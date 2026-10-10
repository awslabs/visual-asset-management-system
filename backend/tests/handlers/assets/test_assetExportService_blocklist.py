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
fails is treated the same way -- whatever raised, an S3 error or a transport error -- so a file
whose type cannot be verified is never signed and only that file is withheld.

The heads run as one pooled pre-pass per asset (`mark_files_allowed_for_download`, the shape of
the module's primaryType read), over exactly the files the signing loop considers: live,
non-folder, non-archived. The loop consults the per-file verdict and makes no S3 call, so a
signed export of an asset holding thousands of files costs a bounded number of round trips, not
one per file in series. Head order is therefore not deterministic and the tests do not pin it.

The helper under test is the real one from `common/s3.py`, loaded from source with the real
blocklists (the conftest stand-in always returns True). `TOOL.EXE` with a benign content type is
the case that depends on #412: the helper lower-cases the extension before the membership test,
so the file is withheld only when that fix is on the branch.

The cost guard is pinned from every side: no `head_object` is issued when URLs are not requested,
when the asset is not distributable, or for a folder or an archived file, so only a file that is
about to be signed pays the read -- once, in the pre-pass, never again in the loop.
"""

import importlib.util
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError

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


def _row(name, version_id=None, is_folder=False, archived=False):
    """One file of the asset. `version_id` overrides the per-file `v-<name>` default verbatim.

    A folder marker or an archived file is a row the signing loop skips, so neither is headed.
    """
    return {
        'fileName': name.rstrip('/'), 'key': f"{_PREFIX}{name}", 'relativePath': f"/{name}",
        'isFolder': is_folder, 'dateCreatedCurrentVersion': '2026-01-01T00:00:00',
        'storageClass': 'STANDARD', 'versionId': version_id if version_id is not None else f"v-{name}",
        'isArchived': archived, 'primaryType': None, **({} if is_folder else {'size': 10}),
    }


class _S3:
    """A stand-in for the module's s3_client that answers head_object per key.

    `content_types` maps a file name to the ContentType the head reports; a name in `failing`
    raises the ClientError S3 raises for a version that is gone; a name in `raising` raises the
    exception instance mapped to it -- the botocore transport errors that are not ClientErrors.
    Every other client call keeps the MagicMock behaviour the suites rely on. Heads are recorded
    in call order, which under the pool is not listing order.
    """

    def __init__(self, content_types, failing=(), raising=None):
        self.client = MagicMock()
        self.client.head_object = MagicMock(side_effect=self._head)
        self._content_types = content_types
        self._failing = set(failing)
        self._raising = raising or {}
        self._lock = threading.Lock()
        self.heads = []

    def _head(self, **params):
        with self._lock:
            self.heads.append(params)
        name = params['Key'][len(_PREFIX):]
        if name in self._failing:
            raise ClientError({'Error': {'Code': 'NoSuchVersion', 'Message': 'gone'}}, 'HeadObject')
        if name in self._raising:
            raise self._raising[name]
        return {'ContentType': self._content_types[name], 'ContentLength': 10}


class _PoolSpy:
    """Stands in for the module's ThreadPoolExecutor and records every max_workers it was built with."""

    def __init__(self):
        self.max_workers = []

    def __call__(self, max_workers=None, **kwargs):
        self.max_workers.append(max_workers)
        return ThreadPoolExecutor(max_workers=max_workers, **kwargs)


def _run(m, spies, rows, s3, asset=None, validate=None, extra_patches=(), **request_overrides):
    """Drive process_asset_batch for one asset with the given listing, s3 client and helper."""
    asset = asset or _asset_item(distributable=True)
    request_model = m.AssetExportRequestModel(
        includeFileMetadata=False, includeAssetMetadata=False, **request_overrides)
    patches = _patches(m, [asset], spies, listings={_ASSET: rows}) + [
        patch.object(m, "s3_client", s3.client),
        patch.object(m, "validateUnallowedFileExtensionAndContentType",
                     validate or _load_real_blocklist_helper()),
    ] + list(extra_patches)
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


def _heads_by_key(s3):
    """{Key: head params}, asserting each key was headed exactly once."""
    by_key = {}
    for head in s3.heads:
        assert head['Key'] not in by_key, f"{head['Key']} headed twice: {s3.heads}"
        by_key[head['Key']] = head
    return by_key


def _prepass_spy(m, s3, heads_after):
    """Patch for the pre-pass that runs the real one and records how many heads it had made on return.

    The loop runs after the pre-pass, so the final `len(s3.heads)` equal to the recorded count
    is the proof that the loop made none.
    """
    real = m.mark_files_allowed_for_download

    def spy(*args, **kwargs):
        real(*args, **kwargs)
        heads_after.append(len(s3.heads))

    return patch.object(m, "mark_files_allowed_for_download", spy)


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

    def test_a_transport_error_on_one_head_withholds_that_file_and_nothing_else(self):
        """A botocore error that is not a ClientError -- a read timeout past the retries -- is a
        failed head like any other: the file is withheld, its siblings are signed, the asset entry
        is complete. Before, it propagated out of the worker and dropped the whole asset."""
        m = _load_asset_export_service()
        spies = _Spies()
        timeout = ReadTimeoutError(endpoint_url=f"https://s3.invalid/bucket-name/{_PREFIX}texture.png")
        s3 = _S3({'model.glb': 'model/gltf-binary', 'texture.png': 'image/png', 'notes.txt': 'text/plain'},
                 raising={'texture.png': timeout})

        entry = _run(m, spies, [_row('model.glb'), _row('texture.png'), _row('notes.txt')], s3,
                     generatePresignedUrls=True)

        assert _urls(entry) == {
            '/model.glb': _SIGNED_URL, '/texture.png': None, '/notes.txt': _SIGNED_URL,
        }, _urls(entry)
        assert entry['assetid'] == _ASSET and entry.get('unauthorizedAsset') is None, entry
        assert len(entry['files']) == 3
        assert sorted(_audited_paths(spies)) == [f"{_PREFIX}model.glb", f"{_PREFIX}notes.txt"]
        spies.log.exception.assert_not_called()

    def test_a_transport_error_is_logged_by_its_class_with_ids_and_no_key(self):
        """The error's own message names the request URL, which carries the key; the line does not."""
        m = _load_asset_export_service()
        spies = _Spies()
        timeout = ReadTimeoutError(endpoint_url=f"https://s3.invalid/bucket-name/{_PREFIX}texture.png")
        s3 = _S3({'model.glb': 'model/gltf-binary', 'texture.png': 'image/png'},
                 raising={'texture.png': timeout})

        _run(m, spies, [_row('model.glb'), _row('texture.png')], s3, generatePresignedUrls=True)

        lines = [line for line in _warning_lines(spies) if 'HeadObject' in line]
        assert len(lines) == 1, _warning_lines(spies)
        assert _ASSET in lines[0] and _DB in lines[0], lines[0]
        assert 'ReadTimeoutError' in lines[0], lines[0]
        assert 'texture.png' not in lines[0] and _PREFIX not in lines[0], lines[0]
        assert 's3.invalid' not in lines[0], lines[0]


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

    def test_folders_and_archived_files_are_not_headed(self):
        """The pre-pass visits exactly the rows the signing loop considers: a folder marker and an
        archived file the request asked to list cost no read on a signed, distributable asset."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3({'model.glb': 'model/gltf-binary', 'old.glb': 'model/gltf-binary'})
        rows = [_row('folder/', is_folder=True), _row('model.glb'), _row('old.glb', archived=True)]

        entry = _run(m, spies, rows, s3,
                     generatePresignedUrls=True, includeFolderFiles=True, includeArchivedFiles=True)

        assert [head['Key'] for head in s3.heads] == [f"{_PREFIX}model.glb"], s3.heads
        assert _urls(entry) == {'/folder/': None, '/model.glb': _SIGNED_URL, '/old.glb': None}, _urls(entry)
        assert _audited_paths(spies) == [f"{_PREFIX}model.glb"]

    def test_a_listing_of_only_folders_and_archived_files_issues_no_head_and_builds_no_pool(self):
        """Nothing would be signed, so nothing is read -- and no worker pool is spun up for it."""
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3({'old.glb': 'model/gltf-binary'})
        pools = _PoolSpy()
        rows = [_row('folder/', is_folder=True), _row('old.glb', archived=True)]

        entry = _run(m, spies, rows, s3,
                     generatePresignedUrls=True, includeFolderFiles=True, includeArchivedFiles=True,
                     extra_patches=[patch.object(m, "ThreadPoolExecutor", pools)])

        assert s3.heads == [], s3.heads
        assert _urls(entry) == {'/folder/': None, '/old.glb': None}, _urls(entry)
        spies.sign.assert_not_called()
        spies.audit.assert_not_called()
        # The listing pool and the per-asset pool of this one-asset batch, one worker each, are
        # the only pools built: no file pool.
        assert pools.max_workers == [1, 1], pools.max_workers

    def test_urls_not_requested_builds_no_file_pool(self):
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3(_MIXED)
        pools = _PoolSpy()

        _run(m, spies, [_row(name) for name in _MIXED], s3,
             extra_patches=[patch.object(m, "ThreadPoolExecutor", pools)])

        assert s3.heads == [], s3.heads
        assert pools.max_workers == [1, 1], pools.max_workers


@pytest.mark.unit
class TestTheHeadIsPinnedToTheVersionBeingSigned:
    def test_one_head_per_live_file_carrying_the_listing_version(self):
        """The type checked is the type served: the head names the version the signer is handed.

        The heads run through a pool, so their order is not the listing's; each key is headed
        exactly once with its own version.
        """
        m = _load_asset_export_service()
        spies = _Spies()
        s3 = _S3(_MIXED)

        _run(m, spies, [_row(name) for name in _MIXED], s3, generatePresignedUrls=True)

        heads = _heads_by_key(s3)
        assert sorted(heads) == sorted(f"{_PREFIX}{name}" for name in _MIXED), s3.heads
        assert all(head['Bucket'] == 'bucket-name' for head in heads.values()), s3.heads
        assert {key: head['VersionId'] for key, head in heads.items()} == {
            f"{_PREFIX}{name}": f"v-{name}" for name in _MIXED}, s3.heads
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


@pytest.mark.unit
class TestTheHeadsRunAsOnePooledPrePass:
    """A signed export of a large asset heads every file through the bounded pool, once, and the
    signing loop makes no S3 call of its own -- the shape that keeps the request inside the API
    Gateway integration timeout for an asset of thousands of files."""

    _LARGE = 1500

    @staticmethod
    def _large_listing():
        """1,500 live files; every 100th is a `.exe` so the pass has something to withhold."""
        content_types, rows = {}, []
        for index in range(TestTheHeadsRunAsOnePooledPrePass._LARGE):
            name = f"part-{index:04d}.exe" if index % 100 == 0 else f"part-{index:04d}.glb"
            content_types[name] = 'application/x-msdownload' if name.endswith('.exe') else 'model/gltf-binary'
            rows.append(_row(name))
        return content_types, rows

    def test_a_1500_file_asset_is_headed_once_per_file_in_the_pre_pass_and_never_in_the_loop(self):
        m = _load_asset_export_service()
        spies = _Spies()
        content_types, rows = self._large_listing()
        s3 = _S3(content_types)
        pools = _PoolSpy()
        heads_after_prepass = []

        entry = _run(m, spies, rows, s3, generatePresignedUrls=True, extra_patches=[
            patch.object(m, "ThreadPoolExecutor", pools),
            _prepass_spy(m, s3, heads_after_prepass),
        ])

        # The pre-pass ran once for the asset and had made all 1,500 heads when it returned;
        # the loop added none. The mock's call_args_list is append-only and exact under the
        # pool's threads; call_count is derived from it without a lock, so it is not used.
        assert heads_after_prepass == [self._LARGE], heads_after_prepass
        assert len(s3.client.head_object.call_args_list) == self._LARGE
        assert len(s3.heads) == self._LARGE
        heads = _heads_by_key(s3)
        assert sorted(heads) == sorted(row['key'] for row in rows)
        assert all(heads[row['key']]['VersionId'] == row['versionId'] for row in rows)
        # The listing pool and the per-asset pool of this one-asset batch, one worker each, then
        # one file pool capped at the module constant -- never a pool sized to the file count.
        assert sorted(pools.max_workers) == [1, 1, m.MAX_PARALLEL_FILE_WORKERS], pools.max_workers
        assert max(pools.max_workers) < self._LARGE

        urls = _urls(entry)
        assert len(urls) == self._LARGE
        withheld = sorted(path for path, url in urls.items() if url is None)
        assert withheld == sorted(f"/{name}" for name in content_types if name.endswith('.exe'))
        assert len(withheld) == self._LARGE // 100
        assert spies.sign.call_count == self._LARGE - len(withheld)
        assert sorted(_audited_paths(spies)) == sorted(
            f"{_PREFIX}{name}" for name in content_types if not name.endswith('.exe'))
        assert len([line for line in _warning_lines(spies) if _WITHHELD_TAG in line]) == len(withheld)
        spies.log.exception.assert_not_called()

    def test_a_failed_head_in_the_pool_withholds_its_file_and_the_pass_completes(self):
        """One gone version and one transport error among many: two withheld, the rest signed."""
        m = _load_asset_export_service()
        spies = _Spies()
        content_types, rows = self._large_listing()
        gone, timed_out = 'part-0001.glb', 'part-0002.glb'
        s3 = _S3(content_types, failing={gone},
                 raising={timed_out: ReadTimeoutError(endpoint_url="https://s3.invalid/")})

        entry = _run(m, spies, rows, s3, generatePresignedUrls=True)

        assert len(s3.heads) == self._LARGE
        urls = _urls(entry)
        assert urls[f"/{gone}"] is None and urls[f"/{timed_out}"] is None
        assert urls['/part-0003.glb'] == _SIGNED_URL
        expected_withheld = self._LARGE // 100 + 2
        assert sum(1 for url in urls.values() if url is None) == expected_withheld
        assert spies.sign.call_count == self._LARGE - expected_withheld
        assert len([line for line in _warning_lines(spies) if 'HeadObject' in line]) == 2
        spies.log.exception.assert_not_called()
