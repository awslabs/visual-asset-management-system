"""#870: the asset read commands take --show-archived to read an archived asset.

Archiving moves an asset's record to its database's archived partition, while its files,
versions, metadata, links and comments stay under the original ids. The read routes find the
archived record only when asked with showArchived=true. These tests pin, for each read command,
that --show-archived sends the parameter, that leaving it off sends nothing new (a live asset's
requests are unchanged), and that --json-output stays a single JSON document.
"""

import json
from unittest.mock import Mock

import pytest

from vamscli.commands.asset_links import asset_links
from vamscli.commands.asset_version import asset_version
from vamscli.commands.comment import comment
from vamscli.commands.file import file
from vamscli.commands.metadata import metadata
from vamscli.utils.api_client import APIClient


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _recording_client(payload=None):
    """An APIClient whose transport records each request's endpoint and query parameters."""
    client = APIClient("https://api.example.com", profile_manager=Mock())
    calls = []

    def _fake_request(method, endpoint, include_auth=True, **kwargs):
        calls.append({'method': method, 'endpoint': endpoint, 'params': kwargs.get('params')})
        return _Response({} if payload is None else payload)

    client._make_request = _fake_request
    return client, calls


_CLIENT_READS = [
    ("asset-version-get",
     lambda c, archived: c.get_asset_version('db1', 'a1', '1', show_archived=archived)),
    ("asset-links",
     lambda c, archived: c.get_asset_links_for_asset('db1', 'a1', show_archived=archived)),
    ("asset-metadata",
     lambda c, archived: c.get_asset_metadata_v2('db1', 'a1', show_archived=archived)),
    ("file-metadata",
     lambda c, archived: c.get_file_metadata_v2('db1', 'a1', '/a.txt', show_archived=archived)),
    ("asset-link-metadata",
     lambda c, archived: c.get_asset_link_metadata_v2('link-1', show_archived=archived)),
    ("asset-comments",
     lambda c, archived: c.list_asset_comments('a1', show_archived=archived)),
    ("asset-version-comments",
     lambda c, archived: c.list_asset_version_comments('a1', '1', show_archived=archived)),
]


class TestAPIClientShowArchived:
    @pytest.mark.parametrize("read", [r[1] for r in _CLIENT_READS], ids=[r[0] for r in _CLIENT_READS])
    def test_sends_show_archived_when_asked(self, read):
        client, calls = _recording_client()
        read(client, True)
        assert calls[0]['method'] == 'GET'
        assert calls[0]['params']['showArchived'] == 'true'

    @pytest.mark.parametrize("read", [r[1] for r in _CLIENT_READS], ids=[r[0] for r in _CLIENT_READS])
    def test_sends_nothing_new_by_default(self, read):
        client, calls = _recording_client()
        read(client, False)
        assert 'showArchived' not in (calls[0]['params'] or {})


_COMMANDS = [
    # (id, group, args, module for the mocks, api method, expected showArchived forwarding)
    ("file-list", file, ['list', '-d', 'db1', '-a', 'a1'], 'file', 'list_asset_files'),
    ("file-info", file, ['info', '-d', 'db1', '-a', 'a1', '-p', '/a.txt'], 'file', 'get_file_info'),
    ("asset-version-get", asset_version, ['get', '-d', 'db1', '-a', 'a1', '-v', '1'],
     'asset_version', 'get_asset_version'),
    ("asset-links-list", asset_links, ['list', '-d', 'db1', '--asset-id', 'a1'],
     'asset_links', 'get_asset_links_for_asset'),
    ("metadata-asset-list", metadata, ['asset', 'list', '-d', 'db1', '-a', 'a1'],
     'metadata', 'get_asset_metadata_v2'),
    ("metadata-file-list", metadata,
     ['file', 'list', '-d', 'db1', '-a', 'a1', '--file-path', '/a.txt', '--type', 'metadata'],
     'metadata', 'get_file_metadata_v2'),
    ("metadata-asset-link-list", metadata, ['asset-link', 'list', '--asset-link-id', 'link-1'],
     'metadata', 'get_asset_link_metadata_v2'),
    ("comment-list", comment, ['list', '-a', 'a1'], 'comment', 'list_asset_comments'),
]

_REPLIES = {
    'list_asset_files': {'items': [{'fileName': 'a.txt', 'relativePath': '/a.txt', 'isArchived': True}]},
    'get_file_info': {'fileName': 'a.txt', 'relativePath': '/a.txt', 'isArchived': True},
    'get_asset_version': {'assetVersionId': '1', 'files': []},
    'get_asset_links_for_asset': {'related': [], 'parents': [], 'children': []},
    'get_asset_metadata_v2': {'metadata': []},
    'get_file_metadata_v2': {'metadata': []},
    'get_asset_link_metadata_v2': {'metadata': []},
    'list_asset_comments': {'message': []},
}


def _show_archived_sent(api_method, mock_call):
    """Whether a recorded APIClient call asked for the archived asset."""
    args, kwargs = mock_call
    if api_method in ('list_asset_files', 'get_file_info'):
        params = kwargs.get('params', args[2] if len(args) > 2 else {})
        return (params or {}).get('showArchived') == 'true'
    return kwargs.get('show_archived') is True


class TestCommandsShowArchived:
    @pytest.mark.parametrize("case", _COMMANDS, ids=[c[0] for c in _COMMANDS])
    def test_flag_reads_the_archived_asset(self, case, cli_runner, generic_command_mocks):
        _id, group, args, module, api_method = case
        with generic_command_mocks(module) as mocks:
            getattr(mocks['api_client'], api_method).return_value = _REPLIES[api_method]
            result = cli_runner.invoke(group, args + ['--show-archived', '--json-output'])
            assert result.exit_code == 0, result.output
            # --json-output stays one JSON document
            json.loads(result.output)
            api = getattr(mocks['api_client'], api_method)
            assert api.called
            assert _show_archived_sent(api_method, api.call_args)

    @pytest.mark.parametrize("case", _COMMANDS, ids=[c[0] for c in _COMMANDS])
    def test_without_the_flag_reads_as_before(self, case, cli_runner, generic_command_mocks):
        _id, group, args, module, api_method = case
        with generic_command_mocks(module) as mocks:
            getattr(mocks['api_client'], api_method).return_value = _REPLIES[api_method]
            result = cli_runner.invoke(group, args + ['--json-output'])
            assert result.exit_code == 0, result.output
            api = getattr(mocks['api_client'], api_method)
            assert not _show_archived_sent(api_method, api.call_args)

    def test_file_list_auto_paginate_asks_on_every_page(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('file') as mocks:
            mocks['api_client'].list_asset_files.side_effect = [
                {'items': [{'fileName': 'a'}], 'NextToken': 't1'},
                {'items': [{'fileName': 'b'}]},
            ]
            result = cli_runner.invoke(file, ['list', '-d', 'db1', '-a', 'a1', '--auto-paginate',
                                              '--show-archived', '--json-output'])
            assert result.exit_code == 0, result.output
            calls = mocks['api_client'].list_asset_files.call_args_list
            assert len(calls) == 2
            assert all(_show_archived_sent('list_asset_files', c) for c in calls)

    def test_file_list_json_input_show_archived(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('file') as mocks:
            mocks['api_client'].list_asset_files.return_value = _REPLIES['list_asset_files']
            result = cli_runner.invoke(file, [
                'list', '-d', 'db1', '-a', 'a1', '--json-output',
                '--json-input', json.dumps({'show_archived': True})])
            assert result.exit_code == 0, result.output
            assert _show_archived_sent('list_asset_files',
                                       mocks['api_client'].list_asset_files.call_args)
