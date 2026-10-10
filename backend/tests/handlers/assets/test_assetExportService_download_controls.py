# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The export route applies the two download controls every other download surface applies.

`POST /database/{d}/assets/{a}/export` with `generatePresignedUrls` true signs a GET URL for each
exported file. `downloadAsset.py`, `streamAsset.py` and `streamAuxiliaryPreviewAsset.py` all
refuse to hand out file content for an asset whose `isDistributable` is false, and each writes a
file-download audit entry for every URL it issues. The export path did neither: it reported the
flag in the asset entry and signed regardless, and wrote nothing to the download audit log group.

Two properties are asserted per exported asset, each judged on that asset's own flag so an
export spanning linked assets with mixed flags keeps exporting all of them:

* a non-distributable asset's files carry no URL and the signer is never called for them;
* every URL issued is written to the download audit log in one `log_file_download_bulk` call
  per asset, listing exactly the files that received a URL and nothing else.

The audit assertions check the file list the helper is handed, not merely that it was called,
because a call carrying folders or archived files would record downloads that never happened.
`generatePresignedUrls` false is the control: no URL and no audit entry, unchanged.

A refusal is also logged, once per non-distributable asset, naming the database, the asset and
how many files went unsigned -- never a file key -- so the attempt has a server-side trace the
way the download route's refusal does. Two further guards pin the gate's place in the order of
controls: a Casbin-refused linked asset is never signed whatever its flag, and the stored flag
is read with the same truthiness rule `downloadAsset.py` applies.
"""

import re
from unittest.mock import MagicMock, patch

import pytest

# The loader from the fail-closed suite: assetExportService cannot be imported normally because
# the root conftest registers a mock `handlers` package that shadows the real one.
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
    _DB,
    _ASSET,
    _OTHER_ASSET,
)

_SIGNED_URL = "https://example.invalid/presigned"
_EVENT = {
    'requestContext': {
        'http': {'method': 'POST', 'path': f"/database/{_DB}/assets/{_ASSET}/export"},
        'authorizer': {'vams:tokens': '["alice"]'},
    },
    'pathParameters': {'databaseId': _DB, 'assetId': _ASSET},
}


def _asset_item(asset_id=_ASSET, distributable=True):
    return {
        'assetId': asset_id,
        'databaseId': _DB,
        'assetName': asset_id,
        'bucketId': 'bucket-1',
        'currentVersionId': '1',
        'isDistributable': distributable,
        'assetLocation': {'Key': f"{_DB}/{asset_id}/"},
    }


def _listing(asset_id):
    """What list_s3_files returns for one asset: two live files, a folder marker, an archived file.

    The folder and the archived file are the per-file controls: neither may receive a URL on a
    distributable asset, so neither may appear in the audit entry.
    """
    prefix = f"{_DB}/{asset_id}/"

    def row(name, is_folder=False, archived=False):
        key = f"{prefix}{name}"
        return {
            'fileName': name.rstrip('/'), 'key': key, 'relativePath': f"/{name}",
            'isFolder': is_folder, 'dateCreatedCurrentVersion': '2026-01-01T00:00:00',
            'storageClass': 'STANDARD', 'versionId': f"v-{name}", 'isArchived': archived,
            'primaryType': None, **({} if is_folder else {'size': 10}),
        }

    return [
        row('folder/', is_folder=True),
        row('model.glb'),
        row('old.glb', archived=True),
        row('texture.png'),
    ]


def _live_keys(asset_id):
    """The keys a distributable asset's export signs: live, non-folder files, in listing order."""
    prefix = f"{_DB}/{asset_id}/"
    return [f"{prefix}model.glb", f"{prefix}texture.png"]


class _Spies:
    def __init__(self):
        self.sign = MagicMock(return_value=_SIGNED_URL)
        self.audit = MagicMock(return_value=None)
        self.log = MagicMock()


def _patches(m, assets, spies, listings=None, enforce=None):
    """Stub every read except the code under test; the signer, the audit helper and the logger are spies.

    `enforce` replaces the Casbin verdict (default: grant every asset) with a callable of
    (asset, action), so a batch can carry an asset the enforcer refuses.
    """
    enforcer = MagicMock()
    enforcer.return_value.enforce.return_value = True
    if enforce is not None:
        enforcer.return_value.enforce.side_effect = enforce
    details = {f"{_DB}:{asset['assetId']}": asset for asset in assets}
    listings = listings or {asset['assetId']: _listing(asset['assetId']) for asset in assets}

    def list_files(bucket, prefix, **kwargs):
        asset_id = prefix.rstrip('/').rsplit('/', 1)[-1]
        rows = listings[asset_id]
        if kwargs.get('exclude_folders'):
            rows = [row for row in rows if not row['isFolder']]
        return rows

    return [
        patch.object(m, "batch_get_assets", MagicMock(
            side_effect=lambda identifiers: {
                key: value for key, value in details.items()
                if key in {f"{i['databaseId']}:{i['assetId']}" for i in identifiers}})),
        patch.object(m, "CasbinEnforcer", enforcer),
        patch.object(m, "get_default_bucket_details", MagicMock(return_value={
            'bucketId': 'bucket-1', 'bucketName': 'bucket-name', 'baseAssetsPrefix': f"{_DB}/"})),
        patch.object(m, "list_s3_files", MagicMock(side_effect=list_files)),
        patch.object(m, "enrich_files_with_primary_type", MagicMock(return_value=None)),
        patch.object(m, "get_asset_version_info", MagicMock(return_value=None)),
        patch.object(m, "get_asset_file_versions", MagicMock(return_value=None)),
        patch.object(m, "get_asset_metadata", MagicMock(return_value={})),
        patch.object(m, "generate_presigned_url", spies.sign),
        patch.object(m, "log_file_download_bulk", spies.audit),
        patch.object(m, "logger", spies.log),
    ]


def _entries_by_asset(exported):
    """Key a batch's entries by asset id.

    An entry Casbin refused carries only `assetId`, `databaseId` and `unauthorizedAsset`; an
    exported one carries the lower-case `assetid` of the export model.
    """
    return {entry.get('assetid', entry.get('assetId')): entry for entry in exported}


def _run_batch(m, assets, spies, event=_EVENT, enforce=None, **request_overrides):
    identifiers = [{'databaseId': _DB, 'assetId': asset['assetId'], 'isRoot': index == 0}
                   for index, asset in enumerate(assets)]
    request_model = m.AssetExportRequestModel(
        includeFileMetadata=False, includeAssetMetadata=False, **request_overrides)
    patches = _patches(m, assets, spies, enforce=enforce)
    for one in patches:
        one.start()
    try:
        exported, _page_state = m.process_asset_batch(
            identifiers, request_model, {"tokens": ["alice"], "roles": []}, event)
    finally:
        for one in reversed(patches):
            one.stop()
    return _entries_by_asset(exported)


def _run_export(m, assets, spies, event=_EVENT, **request_overrides):
    """Drive export_assets in tree mode, which is the path handle_post_export takes."""
    tree = {
        'assetId': assets[0]['assetId'], 'databaseId': _DB,
        'children': [{'assetId': asset['assetId'], 'databaseId': _DB, 'children': []}
                     for asset in assets[1:]],
    }
    request_model = m.AssetExportRequestModel(
        includeFileMetadata=False, includeAssetMetadata=False,
        includeAssetLinkMetadata=False, **request_overrides)
    patches = _patches(m, assets, spies) + [
        patch.object(m, "get_asset_tree_via_lambda", MagicMock(return_value=tree)),
    ]
    for one in patches:
        one.start()
    try:
        response = m.export_assets(
            _DB, assets[0]['assetId'], request_model, {"tokens": ["alice"], "roles": []}, event)
    finally:
        for one in reversed(patches):
            one.stop()
    return _entries_by_asset(response['assets'])


def _urls(entry):
    return {file['relativePath']: file['presignedFileDownloadUrl'] for file in entry['files']}


def _audit_calls_by_asset(spies):
    """{assetId: (event, databaseId, file_entries, custom_data)} for each audit write."""
    by_asset = {}
    for call in spies.audit.call_args_list:
        event, database_id, asset_id, file_entries, custom_data = call.args
        assert asset_id not in by_asset, f"two audit writes for {asset_id}: {spies.audit.call_args_list}"
        by_asset[asset_id] = (event, database_id, file_entries, custom_data)
    return by_asset


def _info_lines(spies):
    """The message of every info-level line the handler emitted, rendered as the logger saw it."""
    return [str(call.args[0]) for call in spies.log.info.call_args_list]


@pytest.mark.unit
class TestNonDistributableAssetGetsNoUrl:
    def test_no_file_carries_a_url_and_the_signer_is_never_called(self):
        """The distinguishing assertion: the pre-fix loop signed every live file regardless."""
        m = _load_asset_export_service()
        spies = _Spies()

        exported = _run_batch(
            m, [_asset_item(distributable=False)], spies, generatePresignedUrls=True)

        entry = exported[_ASSET]
        assert entry['isdistributable'] is False
        assert sorted(_urls(entry)) == ['/model.glb', '/texture.png'], _urls(entry)
        assert all(file['presignedFileDownloadUrl'] is None for file in entry['files']), _urls(entry)
        assert all(file['presignedFileDownloadExpiresIn'] is None for file in entry['files']), entry['files']
        spies.sign.assert_not_called()

    def test_no_audit_entry_is_written_when_nothing_was_signed(self):
        """An audit entry records a download that happened; none did."""
        m = _load_asset_export_service()
        spies = _Spies()

        _run_batch(m, [_asset_item(distributable=False)], spies, generatePresignedUrls=True)

        spies.audit.assert_not_called()

    def test_a_missing_flag_is_treated_as_not_distributable(self):
        """Same default the download routes apply: `asset.get('isDistributable', False)`."""
        m = _load_asset_export_service()
        spies = _Spies()
        asset = _asset_item()
        del asset['isDistributable']

        exported = _run_batch(m, [asset], spies, generatePresignedUrls=True)

        assert exported[_ASSET]['isdistributable'] is False
        assert all(file['presignedFileDownloadUrl'] is None for file in exported[_ASSET]['files'])
        spies.sign.assert_not_called()
        spies.audit.assert_not_called()

    def test_the_asset_is_still_exported_with_its_files_and_flag(self):
        """Control: the gate withholds URLs, it does not drop the asset or fail the export.

        An export may span linked assets with mixed flags; the dedicated download routes are
        where a hard refusal belongs. The response already carries isdistributable, so the
        client can tell why the URLs are absent.
        """
        m = _load_asset_export_service()
        spies = _Spies()

        exported = _run_batch(
            m, [_asset_item(distributable=False)], spies,
            generatePresignedUrls=True, includeFolderFiles=True, includeArchivedFiles=True)

        entry = exported[_ASSET]
        assert sorted(_urls(entry)) == ['/folder/', '/model.glb', '/old.glb', '/texture.png'], _urls(entry)
        assert all(url is None for url in _urls(entry).values()), _urls(entry)
        assert entry['isdistributable'] is False


@pytest.mark.unit
class TestDistributableAssetIsSignedAndAudited:
    def test_live_files_get_urls_and_folders_and_archived_files_do_not(self):
        """Positive control on the gate: a distributable asset still gets its URLs.

        Asserting only "non-distributable gets nothing" is satisfied by a gate that signs
        nothing for anyone, which breaks every export client that asked for URLs.
        """
        m = _load_asset_export_service()
        spies = _Spies()

        exported = _run_batch(
            m, [_asset_item(distributable=True)], spies,
            generatePresignedUrls=True, includeFolderFiles=True, includeArchivedFiles=True)

        urls = _urls(exported[_ASSET])
        assert urls == {
            '/folder/': None,
            '/model.glb': _SIGNED_URL,
            '/old.glb': None,
            '/texture.png': _SIGNED_URL,
        }, urls
        assert sorted(call.args[1] for call in spies.sign.call_args_list) == [
            f"{_DB}/{_ASSET}/model.glb", f"{_DB}/{_ASSET}/texture.png"]
        signed = [file for file in exported[_ASSET]['files'] if file['presignedFileDownloadUrl']]
        assert all(file['presignedFileDownloadExpiresIn'] == 3600 for file in signed), signed

    def test_one_audit_write_per_asset_listing_exactly_the_signed_files(self):
        """The audit entry names the files that received a URL -- no folder, no archived file."""
        m = _load_asset_export_service()
        spies = _Spies()

        _run_batch(
            m, [_asset_item(distributable=True)], spies,
            generatePresignedUrls=True, includeFolderFiles=True, includeArchivedFiles=True)

        spies.audit.assert_called_once()
        event, database_id, file_entries, custom_data = _audit_calls_by_asset(spies)[_ASSET]
        assert event is _EVENT
        assert database_id == _DB
        assert file_entries == [
            {"filePath": key, "versionId": f"v-{key.rsplit('/', 1)[-1]}"}
            for key in _live_keys(_ASSET)
        ], file_entries
        assert custom_data == {"downloadType": "export"}

    def test_a_file_the_signer_could_not_sign_is_not_audited(self):
        """Log only files that actually got a URL: generate_presigned_url returns None on failure."""
        m = _load_asset_export_service()
        spies = _Spies()
        texture_key = _live_keys(_ASSET)[1]
        spies.sign.side_effect = lambda bucket, key, version_id: (
            None if key == texture_key else _SIGNED_URL)

        exported = _run_batch(m, [_asset_item(distributable=True)], spies, generatePresignedUrls=True)

        urls = _urls(exported[_ASSET])
        assert urls == {'/model.glb': _SIGNED_URL, '/texture.png': None}, urls
        _event, _db, file_entries, _custom = _audit_calls_by_asset(spies)[_ASSET]
        assert [entry["filePath"] for entry in file_entries] == [_live_keys(_ASSET)[0]], file_entries


@pytest.mark.unit
class TestUrlsNotRequestedIsUnchanged:
    def test_no_url_and_no_audit_entry_for_a_distributable_asset(self):
        """Control: without generatePresignedUrls nothing is signed, so nothing is audited."""
        m = _load_asset_export_service()
        spies = _Spies()

        exported = _run_batch(m, [_asset_item(distributable=True)], spies)

        entry = exported[_ASSET]
        assert entry['isdistributable'] is True
        assert all(file['presignedFileDownloadUrl'] is None for file in entry['files']), _urls(entry)
        spies.sign.assert_not_called()
        spies.audit.assert_not_called()


@pytest.mark.unit
class TestLinkedAssetsWithMixedFlags:
    def test_each_asset_is_judged_on_its_own_flag(self):
        """One batch, one distributable asset and one that is not: URLs and audit for the first only."""
        m = _load_asset_export_service()
        spies = _Spies()
        assets = [_asset_item(_ASSET, distributable=False), _asset_item(_OTHER_ASSET, distributable=True)]

        exported = _run_batch(m, assets, spies, generatePresignedUrls=True)

        assert set(exported) == {_ASSET, _OTHER_ASSET}, exported.keys()
        assert exported[_ASSET]['isdistributable'] is False
        assert all(url is None for url in _urls(exported[_ASSET]).values()), _urls(exported[_ASSET])
        assert _urls(exported[_OTHER_ASSET]) == {
            '/model.glb': _SIGNED_URL, '/texture.png': _SIGNED_URL}, _urls(exported[_OTHER_ASSET])

        assert sorted(call.args[1] for call in spies.sign.call_args_list) == [
            f"{_DB}/{_OTHER_ASSET}/model.glb", f"{_DB}/{_OTHER_ASSET}/texture.png"]
        audited = _audit_calls_by_asset(spies)
        assert set(audited) == {_OTHER_ASSET}, audited
        assert [entry["filePath"] for entry in audited[_OTHER_ASSET][2]] == _live_keys(_OTHER_ASSET)

    def test_two_distributable_assets_get_one_audit_write_each(self):
        """The write is per asset, keyed on that asset's id, not one write for the whole page."""
        m = _load_asset_export_service()
        spies = _Spies()
        assets = [_asset_item(_ASSET, distributable=True), _asset_item(_OTHER_ASSET, distributable=True)]

        _run_batch(m, assets, spies, generatePresignedUrls=True)

        audited = _audit_calls_by_asset(spies)
        assert set(audited) == {_ASSET, _OTHER_ASSET}, audited
        for asset_id, (_event, database_id, file_entries, custom_data) in audited.items():
            assert database_id == _DB
            assert [entry["filePath"] for entry in file_entries] == _live_keys(asset_id), file_entries
            assert custom_data == {"downloadType": "export"}


@pytest.mark.unit
class TestCasbinDenialOutranksTheDistributableFlag:
    def test_a_denied_linked_asset_gets_no_url_and_no_audit_write_even_when_distributable(self):
        """Tier-2 authorization is decided before the flag is read.

        A linked asset Casbin refuses is reported as unauthorized and never listed, so however it
        is flagged it is never signed and never audited. Today that holds structurally (the
        refusal keeps the asset out of the worker); this pins it against a reordering.
        """
        m = _load_asset_export_service()
        spies = _Spies()
        assets = [_asset_item(_ASSET, distributable=True), _asset_item(_OTHER_ASSET, distributable=True)]

        exported = _run_batch(
            m, assets, spies, generatePresignedUrls=True,
            enforce=lambda asset, action: asset['assetId'] != _OTHER_ASSET)

        assert set(exported) == {_ASSET, _OTHER_ASSET}, exported.keys()
        denied = exported[_OTHER_ASSET]
        assert denied.get('unauthorizedAsset') is True, denied
        assert 'files' not in denied, denied
        assert _urls(exported[_ASSET]) == {
            '/model.glb': _SIGNED_URL, '/texture.png': _SIGNED_URL}, _urls(exported[_ASSET])

        assert sorted(call.args[1] for call in spies.sign.call_args_list) == [
            f"{_DB}/{_ASSET}/model.glb", f"{_DB}/{_ASSET}/texture.png"]
        assert set(_audit_calls_by_asset(spies)) == {_ASSET}, spies.audit.call_args_list


@pytest.mark.unit
class TestStoredFlagTruthinessMatchesTheDownloadRoute:
    @pytest.mark.parametrize("stored", [None, 0, "", "false", "true", 1])
    def test_the_export_signs_exactly_when_download_asset_would_allow(self, stored):
        """downloadAsset.py refuses iff `not asset.get('isDistributable', False)`.

        The stored value is whatever a writer put there; the two routes must agree on every
        shape, including the string "false", which Python reads as truthy and both routes
        therefore treat as distributable. The entry's `isdistributable` reports the same verdict.
        """
        m = _load_asset_export_service()
        spies = _Spies()
        asset = _asset_item()
        asset['isDistributable'] = stored
        download_route_allows = not (not asset.get('isDistributable', False))

        exported = _run_batch(m, [asset], spies, generatePresignedUrls=True)

        entry = exported[_ASSET]
        assert entry['isdistributable'] is download_route_allows, (stored, entry['isdistributable'])
        if download_route_allows:
            assert _urls(entry) == {'/model.glb': _SIGNED_URL, '/texture.png': _SIGNED_URL}, _urls(entry)
            assert set(_audit_calls_by_asset(spies)) == {_ASSET}
        else:
            assert all(url is None for url in _urls(entry).values()), _urls(entry)
            spies.sign.assert_not_called()
            spies.audit.assert_not_called()


@pytest.mark.unit
class TestWithheldUrlsAreLogged:
    """The refusal leaves a server-side trace, the way the download route's refusal does.

    The line is found by the identifiers it must name, not by its wording, so a rephrasing
    does not fail these tests while a line that drops an identifier, adds a file key, or
    fires for the wrong asset does.
    """

    def test_one_info_line_per_non_distributable_asset_naming_ids_and_a_count_only(self):
        """A mixed batch: exactly one line for the refused asset, none for the signed one.

        The count is the files that would have been signed -- the two live files; the folder
        and the archived file were never candidates -- and no file key or name appears, so the
        line carries nothing safeLogger's key-driven redaction would miss.
        """
        m = _load_asset_export_service()
        spies = _Spies()
        assets = [_asset_item(_ASSET, distributable=False), _asset_item(_OTHER_ASSET, distributable=True)]

        _run_batch(
            m, assets, spies,
            generatePresignedUrls=True, includeFolderFiles=True, includeArchivedFiles=True)

        lines = _info_lines(spies)
        withheld = [line for line in lines if _ASSET in line]
        assert len(withheld) == 1, lines
        line = withheld[0]
        assert _DB in line, line
        without_ids = line.replace(_ASSET, "").replace(_DB, "")
        assert re.search(r"\b2\b", without_ids), line
        for row in _listing(_ASSET):
            assert row['key'] not in line, line
            assert row['fileName'] not in line, line
        assert [line for line in lines if _OTHER_ASSET in line] == [], lines

    def test_no_line_when_urls_were_not_requested(self):
        """Nothing was withheld: the caller did not ask for URLs, so the flag was never the reason."""
        m = _load_asset_export_service()
        spies = _Spies()

        _run_batch(m, [_asset_item(distributable=False)], spies)

        assert [line for line in _info_lines(spies) if _ASSET in line] == [], _info_lines(spies)


@pytest.mark.unit
class TestTheRequestEventReachesTheAuditEntry:
    def test_export_assets_threads_its_event_through_to_the_audit_write(self):
        """The audit helper attributes the entry to the caller it reads off the event.

        Driven through export_assets in tree mode -- the path handle_post_export takes -- with
        a root and one linked child, so the event the handler received is the one every audit
        write is handed.
        """
        m = _load_asset_export_service()
        spies = _Spies()
        assets = [_asset_item(_ASSET, distributable=True), _asset_item(_OTHER_ASSET, distributable=False)]
        event = dict(_EVENT, marker='this-request')

        exported = _run_export(m, assets, spies, event=event, generatePresignedUrls=True)

        assert set(exported) == {_ASSET, _OTHER_ASSET}, exported.keys()
        assert _urls(exported[_ASSET]) == {'/model.glb': _SIGNED_URL, '/texture.png': _SIGNED_URL}
        assert all(url is None for url in _urls(exported[_OTHER_ASSET]).values())
        audited = _audit_calls_by_asset(spies)
        assert set(audited) == {_ASSET}, audited
        assert audited[_ASSET][0] is event
