"""
Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0

Auto-pagination must combine an asset that arrives over several pages into ONE entry.

The export API bounds the files one page returns (`maxFiles`, default 2,000) across all of that
page's assets. An asset holding more files than the budget is therefore returned on more than one
page, each entry carrying a different slice of its `files` and reporting `files_truncated`, with
`NextToken` resuming that same asset rather than moving on to the next one.

`export_with_auto_pagination` accumulated pages with `all_assets.extend(page['assets'])`, which
turns that into two records for one asset in the combined result -- each one looking like the whole
of it, and `assetsRetrieved` counting the asset twice. Nothing errors: the caller receives a
200-shaped success whose `files` list, read per record, is silently short. `--auto-paginate` is the
DEFAULT, so this is what an ordinary `vamscli assets export` returns for a large asset.

The load-bearing assertions are the per-asset ones: "the walk followed every page" is satisfied
equally by a result that holds every page's entry separately, which is exactly the defect.
"""

import json
from unittest.mock import MagicMock

import pytest

from vamscli.commands.assetsExport import export_with_auto_pagination, merge_paged_asset
from vamscli.constants import DEFAULT_EXPORT_MAX_FILES, MAX_EXPORT_MAX_FILES
from vamscli.main import cli


_DB = "test-db"
_ASSET = "big-asset"


def _asset_page(asset_id, file_names, truncated, next_token):
    """One export page carrying one asset with the named files."""
    return {
        'assets': [{
            'is_root_lookup_asset': True,
            'databaseid': _DB,
            'assetid': asset_id,
            'assetname': asset_id,
            'files': [{'relativePath': f"/{name}", 'key': f"{_DB}/{asset_id}/{name}"}
                      for name in file_names],
            'files_truncated': truncated,
        }],
        'relationships': [] if next_token else None,
        'NextToken': next_token,
        'totalAssetsInTree': 1,
        'assetsInThisPage': 1,
    }


def _three_pages_of_one_asset():
    return [
        _asset_page(_ASSET, ['a.glb', 'b.glb'], True, 'token-2'),
        _asset_page(_ASSET, ['c.glb', 'd.glb'], True, 'token-3'),
        _asset_page(_ASSET, ['e.glb'], False, None),
    ]


def _run_auto_pagination(pages):
    api_client = MagicMock()
    api_client.export_asset.side_effect = pages
    combined = export_with_auto_pagination(
        api_client, _DB, _ASSET, {'maxFiles': 2}, json_output=True)
    return combined, api_client


class TestAnAssetSpanningPagesBecomesOneEntry:
    def test_the_combined_result_holds_one_record_for_the_asset(self):
        """The distinguishing assertion: `extend` produced three records for one asset."""
        combined, _client = _run_auto_pagination(_three_pages_of_one_asset())

        ids = [asset['assetid'] for asset in combined['assets']]
        assert ids == [_ASSET], (
            f"the same asset was recorded {len(ids)} times: {ids}. Each record carries only part "
            f"of its files, so a consumer reading one of them sees a short file list")

    def test_the_single_record_carries_every_file_from_every_page(self):
        """Merging must join the file lists, not keep the first page's and drop the rest."""
        combined, _client = _run_auto_pagination(_three_pages_of_one_asset())

        paths = [file['relativePath'] for file in combined['assets'][0]['files']]
        assert paths == ['/a.glb', '/b.glb', '/c.glb', '/d.glb', '/e.glb'], paths
        assert len(paths) == len(set(paths)), f"a file was recorded twice: {paths}"

    def test_the_merged_record_no_longer_reports_itself_as_partial(self):
        """`files_truncated` describes one page; the combined record is complete."""
        combined, _client = _run_auto_pagination(_three_pages_of_one_asset())

        assert combined['assets'][0]['files_truncated'] is False, combined['assets'][0]

    def test_the_asset_count_counts_the_asset_once(self):
        """`assetsRetrieved` is what the CLI prints as 'Assets retrieved'."""
        combined, client = _run_auto_pagination(_three_pages_of_one_asset())

        assert combined['assetsRetrieved'] == 1, combined
        assert combined['pagesRetrieved'] == 3, combined
        assert client.export_asset.call_count == 3

    def test_the_token_is_still_followed_to_exhaustion(self):
        """Control: the merge must not stop the walk one page early.

        The token is read inside the side effect rather than from `call_args_list`, because the
        caller reuses and mutates one `export_params` dict -- every recorded call refers to that
        same object, so after the walk all of them read as the LAST token.
        """
        pages = _three_pages_of_one_asset()
        tokens = []

        def _record(_database_id, _asset_id, export_params):
            tokens.append(export_params.get('startingToken'))
            return pages[len(tokens) - 1]

        api_client = MagicMock()
        api_client.export_asset.side_effect = _record
        export_with_auto_pagination(
            api_client, _DB, _ASSET, {'maxFiles': 2}, json_output=True)

        assert tokens == [None, 'token-2', 'token-3'], tokens


class TestDistinctAssetsAreStillSeparateRecords:
    """Positive control: merging by identity must not collapse two different assets."""

    def test_two_assets_stay_two_records_with_their_own_files(self):
        pages = [
            _asset_page('asset-a', ['a.glb'], False, 'token-2'),
            _asset_page('asset-b', ['b.glb'], False, None),
        ]
        combined, _client = _run_auto_pagination(pages)

        assert [asset['assetid'] for asset in combined['assets']] == ['asset-a', 'asset-b']
        assert [[file['relativePath'] for file in asset['files']]
                for asset in combined['assets']] == [['/a.glb'], ['/b.glb']]
        assert combined['assetsRetrieved'] == 2

    def test_the_same_asset_id_in_two_databases_stays_two_records(self):
        """Identity is databaseid AND assetid -- an asset id is unique only within its database."""
        all_assets, index = [], {}
        merge_paged_asset(all_assets, index,
                          {'databaseid': 'db-1', 'assetid': 'shared', 'files': [{'k': 1}]})
        merge_paged_asset(all_assets, index,
                          {'databaseid': 'db-2', 'assetid': 'shared', 'files': [{'k': 2}]})

        assert len(all_assets) == 2, all_assets
        assert [asset['databaseid'] for asset in all_assets] == ['db-1', 'db-2']

    def test_an_unauthorized_placeholder_is_kept_as_its_own_record(self):
        """It carries assetId/databaseId and no files, so it must not fold into anything."""
        all_assets, index = [], {}
        merge_paged_asset(all_assets, index,
                          {'databaseid': _DB, 'assetid': _ASSET, 'files': []})
        merge_paged_asset(all_assets, index,
                          {'assetId': 'denied-1', 'databaseId': _DB, 'unauthorizedAsset': True})
        merge_paged_asset(all_assets, index,
                          {'assetId': 'denied-2', 'databaseId': _DB, 'unauthorizedAsset': True})

        assert len(all_assets) == 3, all_assets
        assert sum(1 for asset in all_assets if asset.get('unauthorizedAsset')) == 2


class TestTheMaxFilesOptionReachesTheRequest:
    @pytest.fixture
    def export_mocks(self, generic_command_mocks):
        return generic_command_mocks('assetsExport')

    def _empty_page(self):
        return {'assets': [], 'relationships': [], 'NextToken': None,
                'totalAssetsInTree': 0, 'assetsInThisPage': 0}

    def test_the_default_matches_the_backend_default(self, cli_runner, export_mocks):
        with export_mocks as mocks:
            mocks['api_client'].export_asset.return_value = self._empty_page()

            result = cli_runner.invoke(cli, ['assets', 'export', '-d', _DB, '-a', _ASSET])

            assert result.exit_code == 0, result.output
            params = mocks['api_client'].export_asset.call_args[0][2]
            assert params['maxFiles'] == DEFAULT_EXPORT_MAX_FILES, params

    def test_an_explicit_value_is_sent(self, cli_runner, export_mocks):
        with export_mocks as mocks:
            mocks['api_client'].export_asset.return_value = self._empty_page()

            result = cli_runner.invoke(cli, [
                'assets', 'export', '-d', _DB, '-a', _ASSET, '--max-files', '25'])

            assert result.exit_code == 0, result.output
            assert mocks['api_client'].export_asset.call_args[0][2]['maxFiles'] == 25

    @pytest.mark.parametrize("value", ['0', str(MAX_EXPORT_MAX_FILES + 1)])
    def test_a_value_outside_the_accepted_range_is_refused(self, cli_runner, export_mocks, value):
        """Refused locally rather than sent for the backend to reject with a 400."""
        with export_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'assets', 'export', '-d', _DB, '-a', _ASSET, '--max-files', value])

            assert result.exit_code == 2, result.output
            assert f"must be between 1 and {MAX_EXPORT_MAX_FILES}" in result.output
            mocks['api_client'].export_asset.assert_not_called()


class TestTheCommandOutputCarriesTheMergedAsset:
    """End to end through the command, which is where the default --auto-paginate applies."""

    @pytest.fixture
    def export_mocks(self, generic_command_mocks):
        return generic_command_mocks('assetsExport')

    def test_json_output_holds_one_entry_with_all_five_files(self, cli_runner, export_mocks):
        with export_mocks as mocks:
            mocks['api_client'].export_asset.side_effect = _three_pages_of_one_asset()

            result = cli_runner.invoke(cli, [
                'assets', 'export', '-d', _DB, '-a', _ASSET, '--max-files', '2',
                '--json-output'])

            assert result.exit_code == 0, result.output
            data = json.loads(result.output)
            assert len(data['assets']) == 1, data['assets']
            assert len(data['assets'][0]['files']) == 5, data['assets'][0]['files']
            assert data['assetsRetrieved'] == 1
            assert data['pagesRetrieved'] == 3
