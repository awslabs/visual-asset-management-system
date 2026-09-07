# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Exporting an asset must cost per ASSET, not per file, and must page inside one asset.

`list_s3_files` used to issue one synchronous `head_object` per listed object -- no worker pool,
no cap, not even skipped for folder markers -- and `process_asset_batch` added two DynamoDB GSI
queries per file (`includeFileMetadata` defaults True). A 5,000-file asset therefore needed
~5,000 S3 round trips plus 10,000 queries on a synchronous API Gateway integration, so
`POST /database/{d}/assets/{a}/export` worked only against small fixtures and 504'd on a
production-sized asset. `maxAssets` paged across assets; nothing paged below one asset.

Three properties are asserted, each of which fails against that code:

* the listing itself issues NO per-object call -- `list_object_versions` carries `VersionId`
  inline, so the version no longer comes from a `head_object`;
* the stored metadata and attributes of a whole asset are read in one query each, so the query
  count is a function of the asset count and not of the file count;
* an asset holding more files than one page's budget is exported over successive pages, each
  resuming where the last stopped, with every file appearing exactly once.

The S3 fake below deliberately serves BOTH the objects paginator and `list_object_versions`. A
fake that only served the versions listing would make the old code fail inside its own
`try/except`, return an empty list, and satisfy "no head_object was called" while the defect was
fully intact -- the assertion would pass for the wrong reason.
"""

import base64
import datetime
import json
from unittest.mock import MagicMock, patch

import pytest

# Reuse the loader from the fail-closed suite: assetExportService cannot be imported normally
# because the root conftest registers a mock `handlers` package that shadows the real one.
from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
    _DB,
    _ASSET,
)

_LAST_MODIFIED = datetime.datetime(2026, 1, 1)
_PREFIX = f"{_DB}/{_ASSET}/"
# The version a head_object would report. Distinct from the listing's VersionId so a test can
# tell which call the exported value came from.
_HEAD_VERSION_ID = "version-from-head-object"


def _keys(count, prefix=_PREFIX):
    """`count` object keys under one asset prefix, in the order S3 lists them."""
    return [f"{prefix}file{index:03d}.glb" for index in range(count)]


class _FakeS3:
    """Serves the objects paginator, the versions listing, and head_object, counting each."""

    def __init__(self, keys_by_prefix):
        self.keys_by_prefix = keys_by_prefix
        self.head_calls = []
        self.versions_calls = []
        self.objects_pages_served = 0

    def _keys_for(self, prefix, start_after=None):
        keys = sorted(self.keys_by_prefix.get(prefix, []))
        if start_after is not None:
            keys = [key for key in keys if key > start_after]
        return keys

    # -- the listing the pre-fix code used --------------------------------------------------
    def get_paginator(self, operation_name):
        assert operation_name == 'list_objects_v2'
        fake = self

        class _Paginator:
            def paginate(self, **kwargs):
                fake.objects_pages_served += 1
                yield {
                    'Contents': [
                        {'Key': key, 'Size': 10, 'LastModified': _LAST_MODIFIED,
                         'StorageClass': 'STANDARD'}
                        for key in fake._keys_for(kwargs['Prefix'])
                    ]
                }

        return _Paginator()

    # -- the listing that carries VersionId inline --------------------------------------------
    def list_object_versions(self, **kwargs):
        self.versions_calls.append(kwargs)
        keys = self._keys_for(kwargs['Prefix'], kwargs.get('KeyMarker'))
        versions = []
        for key in keys:
            # A superseded version of every key, so a reader that ignores IsLatest is caught.
            versions.append({'Key': key, 'VersionId': f"current-{key}", 'IsLatest': True,
                             'Size': 10, 'LastModified': _LAST_MODIFIED,
                             'StorageClass': 'STANDARD'})
            versions.append({'Key': key, 'VersionId': f"older-{key}", 'IsLatest': False,
                             'Size': 9, 'LastModified': _LAST_MODIFIED,
                             'StorageClass': 'STANDARD'})
        return {'Versions': versions, 'DeleteMarkers': [], 'IsTruncated': False}

    def head_object(self, **kwargs):
        self.head_calls.append(kwargs['Key'])
        return {'VersionId': _HEAD_VERSION_ID, 'Metadata': {'vams-primarytype': 'model'}}

    def generate_presigned_url(self, *args, **kwargs):
        return "https://example.invalid/presigned"


class _CountingQueryClient:
    """A low-level DynamoDB stub that answers every query with no rows and counts the calls."""

    def __init__(self):
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return {'Items': []}


def _asset_item(asset_id=_ASSET, prefix=None):
    return {
        'assetId': asset_id,
        'databaseId': _DB,
        'assetName': asset_id,
        'bucketId': 'bucket-1',
        'currentVersionId': '1',
        'assetLocation': {'Key': prefix or f"{_DB}/{asset_id}/"},
    }


def _offline_patches(m, s3, query_client, assets):
    """Stub everything except the code under test: the listing, paging and read counting."""
    enforcer = MagicMock()
    enforcer.return_value.enforce.return_value = True
    details = {f"{_DB}:{asset['assetId']}": asset for asset in assets}
    return [
        patch.object(m, "s3_client", s3),
        patch.object(m, "dynamodb_client", query_client),
        patch.object(m, "batch_get_assets", MagicMock(
            side_effect=lambda identifiers: {
                key: value for key, value in details.items()
                if key in {f"{i['databaseId']}:{i['assetId']}" for i in identifiers}})),
        patch.object(m, "CasbinEnforcer", enforcer),
        patch.object(m, "get_default_bucket_details", MagicMock(return_value={
            'bucketId': 'bucket-1', 'bucketName': 'bucket-name', 'baseAssetsPrefix': f"{_DB}/"})),
        patch.object(m, "get_asset_version_info", MagicMock(return_value=None)),
        patch.object(m, "get_asset_file_versions", MagicMock(return_value=None)),
        # Asset-level metadata is one query per asset either way; stubbed out so the counted
        # queries are only the per-file ones.
        patch.object(m, "get_asset_metadata", MagicMock(return_value={})),
    ]


def _run_batch(m, s3, query_client, assets, **request_overrides):
    identifiers = [{'databaseId': _DB, 'assetId': asset['assetId'], 'isRoot': False}
                   for asset in assets]
    request_model = m.AssetExportRequestModel(**request_overrides)
    patches = _offline_patches(m, s3, query_client, assets)
    for one in patches:
        one.start()
    try:
        return m.process_asset_batch(
            identifiers, request_model, {"tokens": ["alice"], "roles": []},
            file_budget=request_overrides.get('maxFiles'))
    finally:
        for one in reversed(patches):
            one.stop()


def _run_export(m, s3, query_client, assets, starting_token=None, **request_overrides):
    """Drive export_assets in single-asset mode, which is where an oversized asset is exported."""
    request_model = m.AssetExportRequestModel(
        startingToken=starting_token, fetchAssetRelationships=False, **request_overrides)
    patches = _offline_patches(m, s3, query_client, assets)
    for one in patches:
        one.start()
    try:
        return m.export_assets(
            _DB, assets[0]['assetId'], request_model, {"tokens": ["alice"], "roles": []},
            {'requestContext': {}})
    finally:
        for one in reversed(patches):
            one.stop()


@pytest.mark.unit
class TestTheListingIssuesNoPerObjectCall:
    def test_listing_ten_objects_issues_no_head_object(self):
        """The distinguishing assertion: the pre-fix loop called head_object once per object."""
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _keys(10)})

        with patch.object(m, "s3_client", s3):
            files = m.list_s3_files("bucket-name", _PREFIX)

        assert len(files) == 10, f"the listing did not return every object: {files}"
        assert s3.head_calls == [], (
            f"{len(s3.head_calls)} head_object call(s) for a 10-object listing; the version and "
            f"size come from the listing itself, so the per-object call is the N+1 being removed")

    def test_the_version_comes_from_the_listing_and_is_the_current_one(self):
        """Positive control on the same call: dropping the head must not drop the version.

        Asserting only "no head_object" would be satisfied by a listing that returned
        versionId 'null' for everything, which silently breaks the version-mismatch flag and
        every presigned URL. The superseded version in the fake also catches a reader that
        ignores IsLatest.
        """
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _keys(3)})

        with patch.object(m, "s3_client", s3):
            files = m.list_s3_files("bucket-name", _PREFIX)

        assert [file['versionId'] for file in files] == [
            f"current-{key}" for key in _keys(3)], files
        assert _HEAD_VERSION_ID not in [file['versionId'] for file in files]
        assert [file['relativePath'] for file in files] == [
            '/file000.glb', '/file001.glb', '/file002.glb'], files
        assert files[0]['size'] == 10 and files[0]['isFolder'] is False, files[0]

    def test_primary_type_is_read_once_per_exported_file_and_never_for_a_folder(self):
        """primaryType lives only in the object's own metadata, so it keeps a bounded read.

        The folder marker is the control: the pre-fix loop headed it too.
        """
        m = _load_asset_export_service()
        keys = _keys(3) + [f"{_PREFIX}folder/"]
        s3 = _FakeS3({_PREFIX: keys})
        query_client = _CountingQueryClient()

        assets, _page_state = _run_batch(
            m, s3, query_client, [_asset_item()], includeFolderFiles=True)

        exported = assets[0]['files']
        assert len(exported) == 4, exported
        assert sorted(s3.head_calls) == sorted(_keys(3)), (
            f"primaryType was read for {s3.head_calls}; expected exactly the three non-folder "
            f"files and no folder marker")
        assert all(file['primaryType'] == 'model'
                   for file in exported if not file['isFolder']), exported

    def test_a_file_dropped_by_another_filter_costs_no_object_metadata_read(self):
        """The read is for the files the page exports, not for every file it listed.

        Reading primaryType for a file an extension filter drops puts the removed per-object
        call straight back: ten textures excluded from a one-model export still cost ten
        HeadObject calls, which is the whole of the N+1 on an asset whose export is filtered.
        """
        m = _load_asset_export_service()
        keys = [f"{_PREFIX}t{index:02d}.png" for index in range(10)] + [f"{_PREFIX}model.glb"]
        s3 = _FakeS3({_PREFIX: keys})

        assets, _page_state = _run_batch(
            m, s3, _CountingQueryClient(), [_asset_item()], fileExtensions=['.glb'])

        assert [file['relativePath'] for file in assets[0]['files']] == ['/model.glb'], (
            assets[0]['files'])
        assert s3.head_calls == [f"{_PREFIX}model.glb"], (
            f"primaryType was read for {len(s3.head_calls)} file(s): {s3.head_calls}; only the "
            f"one file the page exports needs it")

    def test_the_primary_type_filter_still_selects_on_the_value_it_reads(self):
        """Positive control: deferring that filter must not stop it being applied.

        includeOnlyPrimaryTypeFiles is the one filter that reads primaryType, so it runs after
        the read. A refactor that deferred it and forgot to re-apply it would export every file.
        """
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _keys(3)})
        without_primary_type = f"{_PREFIX}file001.glb"

        def _head(**kwargs):
            s3.head_calls.append(kwargs['Key'])
            if kwargs['Key'] == without_primary_type:
                return {'VersionId': _HEAD_VERSION_ID, 'Metadata': {}}
            return {'VersionId': _HEAD_VERSION_ID, 'Metadata': {'vams-primarytype': 'model'}}

        s3.head_object = _head

        assets, _page_state = _run_batch(
            m, s3, _CountingQueryClient(), [_asset_item()],
            includeOnlyPrimaryTypeFiles=True)

        assert [file['relativePath'] for file in assets[0]['files']] == [
            '/file000.glb', '/file002.glb'], assets[0]['files']
        assert sorted(s3.head_calls) == sorted(_keys(3)), (
            "the filter selects on primaryType, so every candidate's value must be read first")


class _PagingVersionsS3:
    """Serves list_object_versions in small pages, honouring both continuation markers.

    A version listing is capped per call and counts VERSION entries, not keys, so an asset whose
    files carry several versions each needs the continuation markers threaded to see every key
    (backend/CLAUDE.md Rule 14). The fake pages at three entries -- fewer than the requested
    MaxKeys, which S3 is free to do -- and raises if a resume arrives on a marker pair it never
    handed out, so a listing that threads the wrong marker fails rather than silently repeating.
    """

    PAGE = 3

    def __init__(self, entries):
        self.entries = entries          # ordered [(key, versionId, isLatest, isDeleteMarker)]
        self.calls = []

    def list_object_versions(self, **kwargs):
        self.calls.append(kwargs)
        key_marker = kwargs.get('KeyMarker')
        version_marker = kwargs.get('VersionIdMarker')
        start = 0
        if key_marker and version_marker:
            for index, entry in enumerate(self.entries):
                if entry[0] == key_marker and entry[1] == version_marker:
                    start = index + 1
                    break
            else:
                raise AssertionError(
                    f"the listing resumed from a marker pair S3 never returned: "
                    f"{key_marker}/{version_marker}")
        elif key_marker:
            start = next((index for index, entry in enumerate(self.entries)
                          if entry[0] > key_marker), len(self.entries))

        window = self.entries[start:start + self.PAGE]
        versions, delete_markers = [], []
        for key, version_id, is_latest, is_delete_marker in window:
            row = {'Key': key, 'VersionId': version_id, 'IsLatest': is_latest,
                   'LastModified': _LAST_MODIFIED}
            if is_delete_marker:
                delete_markers.append(row)
            else:
                row.update({'Size': 7, 'StorageClass': 'STANDARD'})
                versions.append(row)

        truncated = start + self.PAGE < len(self.entries)
        response = {'Versions': versions, 'DeleteMarkers': delete_markers,
                    'IsTruncated': truncated}
        if truncated:
            response['NextKeyMarker'] = window[-1][0]
            response['NextVersionIdMarker'] = window[-1][1]
        return response


def _versioned_entries():
    """Four keys with two entries each; c.glb is topped by a delete marker."""
    entries = []
    for name in ('a.glb', 'b.glb', 'c.glb', 'd.glb'):
        key = f"{_PREFIX}{name}"
        if name == 'c.glb':
            entries.append((key, 'delete-marker-1', True, True))
            entries.append((key, 'superseded-1', False, False))
        else:
            entries.append((key, f"current-{name}", True, False))
            entries.append((key, f"older-{name}", False, False))
    return entries


@pytest.mark.unit
class TestTheListingPagesToExhaustion:
    def test_a_key_on_a_later_page_is_still_listed(self):
        """Eight version entries at three per page: d.glb is only on the third page.

        Reading one page, or threading only KeyMarker, drops keys from the export with no error
        -- the files simply are not in the bundle.
        """
        m = _load_asset_export_service()
        s3 = _PagingVersionsS3(_versioned_entries())

        with patch.object(m, "s3_client", s3):
            files = m.list_s3_files("bucket-name", _PREFIX)

        assert [file['relativePath'] for file in files] == [
            '/a.glb', '/b.glb', '/d.glb'], files
        # An UPPER bound on the cost. That all three pages were READ is already proved by
        # the relativePath assertion above; what remains is that no page was re-read.
        assert s3.calls, 'the listing made no call at all'
        assert len(s3.calls) <= 3, (
            f'the listing made {len(s3.calls)} call(s) for three pages of entries')

    def test_a_key_whose_latest_entry_is_a_delete_marker_is_not_listed(self):
        """The objects listing omits such a key, and the export must report the same set."""
        m = _load_asset_export_service()
        s3 = _PagingVersionsS3(_versioned_entries())

        with patch.object(m, "s3_client", s3):
            keys = [file['key'] for file in m.list_s3_files("bucket-name", _PREFIX)]

        assert f"{_PREFIX}c.glb" not in keys, (
            "a deleted file was listed; its newest entry is a delete marker, so its last content "
            "version must not be exported as the file's current state")
        assert keys, "the fixture must list something, or the absence above is vacuous"

    def test_only_the_current_version_of_a_key_is_listed(self):
        """Control: the superseded entries must be skipped, not exported as extra files."""
        m = _load_asset_export_service()
        s3 = _PagingVersionsS3(_versioned_entries())

        with patch.object(m, "s3_client", s3):
            files = m.list_s3_files("bucket-name", _PREFIX)

        assert [file['versionId'] for file in files] == [
            'current-a.glb', 'current-b.glb', 'current-d.glb'], files

    def test_the_cap_and_the_resume_both_work_across_pages(self):
        """The budget and the resume key are what page an asset, so both must survive paging."""
        m = _load_asset_export_service()

        capped_s3 = _PagingVersionsS3(_versioned_entries())
        with patch.object(m, "s3_client", capped_s3):
            capped = m.list_s3_files("bucket-name", _PREFIX, max_objects=2)
        assert [file['relativePath'] for file in capped] == ['/a.glb', '/b.glb', '/d.glb'], (
            "a cap of two returns one extra entry so the caller can tell more remain")

        resumed_s3 = _PagingVersionsS3(_versioned_entries())
        with patch.object(m, "s3_client", resumed_s3):
            resumed = m.list_s3_files(
                "bucket-name", _PREFIX, start_after_key=f"{_PREFIX}a.glb")
        assert [file['relativePath'] for file in resumed] == ['/b.glb', '/d.glb'], resumed


@pytest.mark.unit
class TestStoredRowsAreReadPerAssetNotPerFile:
    def test_query_count_does_not_grow_with_the_file_count(self):
        """The pre-fix path issued two GSI queries per file: 2 for one file, 20 for ten."""
        m = _load_asset_export_service()

        counts = {}
        for file_count in (1, 10):
            s3 = _FakeS3({_PREFIX: _keys(file_count)})
            query_client = _CountingQueryClient()
            assets, _page_state = _run_batch(m, s3, query_client, [_asset_item()])
            assert len(assets[0]['files']) == file_count, assets[0]['files']
            counts[file_count] = len(query_client.queries)

        assert counts[10] == counts[1], (
            f"the stored-row read scales with the file count: {counts[1]} query/queries for one "
            f"file and {counts[10]} for ten")
        assert counts[10] == 2, (
            f"expected one query per table for the whole asset, got {counts[10]}")

    def test_two_assets_cost_twice_one_asset_and_not_twice_per_file(self):
        """Sensitivity control: the read must still be per asset, not hoisted to per request."""
        m = _load_asset_export_service()
        assets_in = [_asset_item("asset-a"), _asset_item("asset-b")]
        s3 = _FakeS3({f"{_DB}/asset-a/": _keys(4, f"{_DB}/asset-a/"),
                      f"{_DB}/asset-b/": _keys(4, f"{_DB}/asset-b/")})
        query_client = _CountingQueryClient()

        exported, _page_state = _run_batch(m, s3, query_client, assets_in)

        assert len(exported) == 2, exported
        assert len(query_client.queries) == 4, (
            f"expected two queries per asset, got {len(query_client.queries)}")
        assert {kwargs['IndexName'] for kwargs in query_client.queries} == {
            'DatabaseIdAssetIdIndex'}, query_client.queries

    def test_a_prefetched_row_still_reaches_the_exported_file(self):
        """Positive control: the asset-wide read must still populate each file's own block.

        Without this, a prefetch that returned nothing would satisfy the query-count assertions
        while emptying every file's metadata in the bundle.
        """
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _keys(2)})

        class _RowServingClient(_CountingQueryClient):
            def query(self, **kwargs):
                self.queries.append(kwargs)
                if kwargs['TableName'] != m.asset_file_metadata_table_name:
                    return {'Items': []}
                return {'Items': [{
                    'databaseId:assetId:filePath': {'S': f"{_DB}:{_ASSET}:/file000.glb"},
                    'metadataKey': {'S': 'partNumber'},
                    'metadataValue': {'S': 'PN-1'},
                    'metadataValueType': {'S': 'string'},
                }]}

        assets, _page_state = _run_batch(m, s3, _RowServingClient(), [_asset_item()])

        by_path = {file['relativePath']: file for file in assets[0]['files']}
        assert by_path['/file000.glb']['metadata'] == {
            'partNumber': {'valueType': 'string', 'value': 'PN-1'}}, by_path['/file000.glb']
        assert by_path['/file001.glb']['metadata'] == {}, (
            "a file with no stored row must not inherit its sibling's metadata")


@pytest.mark.unit
class TestAnAssetLargerThanThePageIsPagedWithin:
    def _pages(self, m, file_count, max_files):
        """Walk export_assets to exhaustion, returning one (assets, NextToken) per page."""
        s3 = _FakeS3({_PREFIX: _keys(file_count)})
        pages = []
        token = None
        for _guard in range(file_count + 3):
            response = _run_export(
                m, s3, _CountingQueryClient(), [_asset_item()],
                starting_token=token, maxFiles=max_files)
            pages.append(response)
            token = response.get('NextToken')
            if not token:
                break
        assert not token, "the export never stopped paging"
        return pages

    def test_a_page_stops_at_the_file_budget_and_reports_the_asset_as_partial(self):
        """Before the fix a single asset was always exported whole, with no NextToken."""
        m = _load_asset_export_service()
        pages = self._pages(m, file_count=5, max_files=2)

        first = pages[0]
        assert len(first['assets']) == 1, first['assets']
        assert [file['relativePath'] for file in first['assets'][0]['files']] == [
            '/file000.glb', '/file001.glb'], first['assets'][0]['files']
        assert first['assets'][0]['files_truncated'] is True, (
            "a partial file list that does not say so reads as the asset's whole content")
        assert first['NextToken'], "there is no way to reach the asset's remaining files"

    def test_the_token_resumes_inside_the_same_asset(self):
        m = _load_asset_export_service()
        pages = self._pages(m, file_count=5, max_files=2)

        assert len(pages) == 3, [len(page['assets']) for page in pages]
        assert [file['relativePath'] for file in pages[1]['assets'][0]['files']] == [
            '/file002.glb', '/file003.glb'], pages[1]['assets'][0]['files']
        resumed = json.loads(base64.b64decode(pages[0]['NextToken'].encode('utf-8')))
        assert resumed['fileResumeAfterKey'] == f"{_PREFIX}file001.glb", resumed

    def test_every_file_is_exported_exactly_once_across_the_pages(self):
        """The property that makes paging usable: no gap and no duplicate at a page boundary."""
        m = _load_asset_export_service()
        pages = self._pages(m, file_count=5, max_files=2)

        exported = [file['relativePath'] for page in pages for asset in page['assets']
                    for file in asset['files']]
        assert sorted(exported) == [
            '/file000.glb', '/file001.glb', '/file002.glb', '/file003.glb', '/file004.glb'], exported
        assert len(exported) == len(set(exported)), f"a file was exported twice: {exported}"
        assert pages[-1]['assets'][0]['files_truncated'] is False, pages[-1]['assets'][0]

    def test_an_asset_inside_the_budget_is_exported_whole_with_no_token(self):
        """Positive control: the ceiling must not page an export that already fits.

        Without this, a fix that always truncated would satisfy every assertion above.
        """
        m = _load_asset_export_service()
        pages = self._pages(m, file_count=5, max_files=2000)

        assert len(pages) == 1, f"a 5-file asset was paged against a 2,000-file budget: {pages}"
        assert len(pages[0]['assets'][0]['files']) == 5
        assert pages[0]['assets'][0]['files_truncated'] is False
        assert not pages[0]['NextToken'], pages[0]


@pytest.mark.unit
class TestAPageDoesNotSeparateAFileFromItsPreview:
    """A preview key is its base file's key plus the suffix, so S3 lists the two adjacently.

    A cut inside that run exports the base file with an empty `previewFile` AND drops the preview
    object entirely -- the page carrying it holds no base file to attach it to, so the preview is
    not exported as a file of its own either. That is a silent difference between a paged export
    and an unpaged one, which is why the unpaged export is the assertion's reference below.
    """

    # 0aaa sorts first so the cut at 2 lands on bbb's preview with a file to give the group back.
    _KEYS = [f"{_PREFIX}0aaa.glb", f"{_PREFIX}bbb.glb", f"{_PREFIX}bbb.glb.previewFile.png",
             f"{_PREFIX}ccc.glb"]

    def _walk(self, m, max_files):
        s3 = _FakeS3({_PREFIX: list(self._KEYS)})
        pages, token = [], None
        for _guard in range(len(self._KEYS) + 3):
            response = _run_export(
                m, s3, _CountingQueryClient(), [_asset_item()],
                starting_token=token, maxFiles=max_files)
            pages.append(response)
            token = response.get('NextToken')
            if not token:
                break
        assert not token, "the export never stopped paging"
        return pages

    def _previews_by_path(self, pages):
        return {file['relativePath']: file['previewFile']
                for page in pages for asset in page['assets'] for file in asset['files']}

    def test_a_paged_export_reports_the_same_preview_as_an_unpaged_one(self):
        """The distinguishing assertion: the cut used to land between bbb.glb and its preview."""
        m = _load_asset_export_service()

        paged = self._previews_by_path(self._walk(m, max_files=2))
        unpaged = self._previews_by_path(self._walk(m, max_files=2000))

        assert paged == unpaged, (
            f"paging changed the exported preview associations: paged={paged} unpaged={unpaged}")
        assert unpaged['/bbb.glb'] == '/bbb.glb.previewFile.png', (
            "the fixture must actually carry a preview, or the comparison is vacuous")

    def test_every_base_file_is_still_exported_exactly_once(self):
        """Control: deferring the group must not lose or repeat a file, nor stop the walk."""
        m = _load_asset_export_service()

        exported = [file['relativePath'] for page in self._walk(m, max_files=2)
                    for asset in page['assets'] for file in asset['files']]

        assert sorted(exported) == ['/0aaa.glb', '/bbb.glb', '/ccc.glb'], exported
        assert len(exported) == len(set(exported)), f"a file was exported twice: {exported}"

    def test_a_group_larger_than_the_whole_budget_still_advances(self):
        """The one case the cut cannot avoid: backing off would leave the page empty.

        A budget of one file cannot hold a base file and its preview, so the split is accepted
        rather than emitting a page with nothing in it, which could never advance the listing.
        """
        m = _load_asset_export_service()

        pages = self._walk(m, max_files=1)

        exported = [file['relativePath'] for page in pages
                    for asset in page['assets'] for file in asset['files']]
        assert sorted(exported) == ['/0aaa.glb', '/bbb.glb', '/ccc.glb'], exported


@pytest.mark.unit
class TestThePrefetchedBlockIsALookupNotAQuery:
    def test_a_prefetched_block_is_read_without_a_query_and_the_path_is_normalized(self):
        """The lookup key is the asset-relative path with one leading slash (Rule 13)."""
        m = _load_asset_export_service()
        client = _CountingQueryClient()
        prefetched = {'/folder/file.txt': {'k': {'value': 'v', 'valueType': None}}}

        with patch.object(m, "dynamodb_client", client):
            found = m.get_file_metadata(_DB, _ASSET, "folder/file.txt", prefetched=prefetched)
            absent = m.get_file_metadata(_DB, _ASSET, "/other.txt", prefetched=prefetched)

        assert found == prefetched['/folder/file.txt'], found
        assert absent == {}, "a path the asset-wide read had no row for must read as empty"
        assert client.queries == [], (
            f"a prefetched block still issued {len(client.queries)} query/queries")

    def test_a_malformed_row_is_reported_once_for_the_asset(self):
        """A report per file path would grow the log with the asset, on every page.

        `log_absent_stored_fields` exists because reporting per row scaled the log volume with
        the export; an asset-wide read must not reintroduce that per path.
        """
        m = _load_asset_export_service()
        rows = [{'databaseId:assetId:filePath': {'S': f"{_DB}:{_ASSET}:/f{index}.glb"},
                 'metadataKey': {'S': 'legacyKey'}} for index in range(3)]

        class _RowClient(_CountingQueryClient):
            def query(self, **kwargs):
                self.queries.append(kwargs)
                return {'Items': rows}

        log = MagicMock()
        with patch.object(m, "dynamodb_client", _RowClient()), patch.object(m, "logger", log):
            blocks = m.prefetch_file_metadata(_DB, _ASSET)

        assert set(blocks) == {'/f0.glb', '/f1.glb', '/f2.glb'}, blocks
        lines = [str(call) for call in log.warning.call_args_list]
        # An UPPER bound: the claim is that the absent attribute is reported once for the
        # asset rather than once per file, so what matters is that the count does not grow
        # with the file count.
        assert lines, 'nothing was logged, so the line count below asserts nothing'
        assert len(lines) <= 2, (
            f'expected at most one line per absent attribute for the whole asset, got: {lines}')
        assert "3 stored metadata row(s)" in lines[0], lines


@pytest.mark.unit
class TestThePageBudgetSpansTheAssetsOfOnePage:
    def test_an_asset_the_budget_cannot_start_is_left_for_the_next_page(self):
        """The budget bounds the whole page, so ten assets cannot each spend it."""
        m = _load_asset_export_service()
        assets_in = [_asset_item("asset-a"), _asset_item("asset-b")]
        s3 = _FakeS3({f"{_DB}/asset-a/": _keys(3, f"{_DB}/asset-a/"),
                      f"{_DB}/asset-b/": _keys(3, f"{_DB}/asset-b/")})

        exported, page_state = _run_batch(m, s3, _CountingQueryClient(), assets_in, maxFiles=3)

        assert [asset['assetid'] for asset in exported] == ["asset-a"], (
            "the second asset was exported although the page's file budget was spent")
        assert page_state['lastCompletedPosition'] == 0, page_state
        assert page_state['resumeAfterKey'] is None, (
            "the first asset was exported whole, so nothing inside it needs resuming")

    def test_both_assets_fit_when_the_budget_allows(self):
        """Positive control for the same rule: the budget must not truncate what fits."""
        m = _load_asset_export_service()
        assets_in = [_asset_item("asset-a"), _asset_item("asset-b")]
        s3 = _FakeS3({f"{_DB}/asset-a/": _keys(3, f"{_DB}/asset-a/"),
                      f"{_DB}/asset-b/": _keys(3, f"{_DB}/asset-b/")})

        exported, page_state = _run_batch(m, s3, _CountingQueryClient(), assets_in, maxFiles=6)

        assert [asset['assetid'] for asset in exported] == ["asset-a", "asset-b"], exported
        assert all(asset['files_truncated'] is False for asset in exported), exported
        assert page_state['lastCompletedPosition'] == 1, page_state
