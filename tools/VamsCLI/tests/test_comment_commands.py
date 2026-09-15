"""Tests for the comment command group.

The comments API carries six verbs that no CLI command could reach. The first class here is the
check that would have caught that: it asserts the HTTP method and the exact path each `APIClient`
method requests, so a missing, renamed, or mis-built route fails in the suite rather than against a
live deployment. `tests/test_constants_contract.py` separately proves each path constant names a
route the backend registers.
"""

import json
from unittest.mock import MagicMock, Mock

import pytest
import requests

from vamscli import constants
from vamscli.main import cli
from vamscli.utils.api_client import APIClient, build_comment_path
from vamscli.utils.exceptions import (
    AssetNotFoundError,
    CommentNotFoundError,
    SetupRequiredError,
    InvalidCommentDataError,
)


class _Response:
    """Minimal stand-in for a requests.Response carrying a JSON body."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _recording_client(payload=None):
    """An APIClient whose transport is replaced by a recorder.

    Every verb funnels through `_make_request`, so stubbing that one method records the HTTP
    method, the endpoint, and the request body for whichever verb the method under test chose —
    the three things a caller cannot see from a return value.
    """
    client = APIClient("https://api.example.com", profile_manager=Mock())
    calls = []

    def _fake_request(method, endpoint, include_auth=True, **kwargs):
        calls.append({'method': method, 'endpoint': endpoint, 'kwargs': kwargs})
        return _Response({} if payload is None else payload)

    client._make_request = _fake_request
    return client, calls


class TestCommentRequestPathsAndMethods:
    """Each of the six comment verbs reaches the route the backend registers, with its own method."""

    def test_list_asset_comments_gets_the_asset_route(self):
        client, calls = _recording_client({'message': []})
        client.list_asset_comments('my-asset')

        assert calls[0]['method'] == 'GET'
        assert calls[0]['endpoint'] == '/comments/assets/my-asset'
        # An omitted pagination option leaves the deployment's own default in force.
        assert calls[0]['kwargs']['params'] == {}

    def test_list_asset_comments_forwards_only_the_pagination_given(self):
        client, calls = _recording_client({'message': []})
        client.list_asset_comments('my-asset', max_items=25, page_size=5, starting_token='tok')

        assert calls[0]['kwargs']['params'] == {
            'maxItems': 25, 'pageSize': 5, 'startingToken': 'tok'}

    def test_list_asset_version_comments_gets_the_version_route(self):
        client, calls = _recording_client({'message': []})
        client.list_asset_version_comments('my-asset', 'v1')

        assert calls[0]['method'] == 'GET'
        assert calls[0]['endpoint'] == '/comments/assets/my-asset/assetVersionId/v1'

    def test_get_comment_gets_the_composite_key_route(self):
        client, calls = _recording_client({'message': {'commentBody': 'hi'}})
        client.get_comment('my-asset', 'v1', 'c-1')

        assert calls[0]['method'] == 'GET'
        assert calls[0]['endpoint'] == '/comments/assets/my-asset/assetVersionId:commentId/v1:c-1'

    def test_add_comment_posts_the_composite_key_route_with_the_body(self):
        client, calls = _recording_client({'message': 'Succeeded'})
        client.add_comment('my-asset', 'v1', 'c-1', 'a remark')

        assert calls[0]['method'] == 'POST'
        assert calls[0]['endpoint'] == '/comments/assets/my-asset/assetVersionId:commentId/v1:c-1'
        # commentBody is the only field the handler reads, and it is required.
        assert calls[0]['kwargs']['json'] == {'commentBody': 'a remark'}

    def test_update_comment_puts_the_composite_key_route_with_the_body(self):
        client, calls = _recording_client({'message': 'Succeeded'})
        client.update_comment('my-asset', 'v1', 'c-1', 'a corrected remark')

        assert calls[0]['method'] == 'PUT'
        assert calls[0]['endpoint'] == '/comments/assets/my-asset/assetVersionId:commentId/v1:c-1'
        assert calls[0]['kwargs']['json'] == {'commentBody': 'a corrected remark'}

    def test_delete_comment_deletes_the_composite_key_route(self):
        client, calls = _recording_client({'message': 'Comment deleted'})
        client.delete_comment('my-asset', 'v1', 'c-1')

        assert calls[0]['method'] == 'DELETE'
        assert calls[0]['endpoint'] == '/comments/assets/my-asset/assetVersionId:commentId/v1:c-1'
        # The endpoint takes no body; sending one would be silently ignored, so none is built.
        assert 'json' not in calls[0]['kwargs']

    def test_every_comment_route_the_registry_declares_is_requested_by_a_method(self):
        """The four declared comment paths are covered, as templates rather than as one instance."""
        requested = set()
        for call in (
            lambda c: c.list_asset_comments('a'),
            lambda c: c.list_asset_version_comments('a', 'v'),
            lambda c: c.get_comment('a', 'v', 'c'),
            lambda c: c.add_comment('a', 'v', 'c', 'body'),
            lambda c: c.update_comment('a', 'v', 'c', 'body'),
            lambda c: c.delete_comment('a', 'v', 'c'),
        ):
            client, calls = _recording_client({'message': {'commentBody': 'x'}})
            call(client)
            requested.add((calls[0]['method'], calls[0]['endpoint']))

        assert requested == {
            ('GET', '/comments/assets/a'),
            ('GET', '/comments/assets/a/assetVersionId/v'),
            ('GET', '/comments/assets/a/assetVersionId:commentId/v:c'),
            ('POST', '/comments/assets/a/assetVersionId:commentId/v:c'),
            ('PUT', '/comments/assets/a/assetVersionId:commentId/v:c'),
            ('DELETE', '/comments/assets/a/assetVersionId:commentId/v:c'),
        }


class TestCompositeCommentKey:
    """The `assetVersionId:commentId` segment is built, never formatted."""

    def test_the_constant_cannot_be_formatted(self):
        # This is why build_comment_path exists: str.format reads `commentId` as a format spec.
        with pytest.raises(ValueError):
            constants.API_COMMENTS_ASSET_VERSION_COMMENT.format(assetId='a', assetVersionId='v')

    def test_the_two_parts_are_joined_with_a_colon(self):
        assert build_comment_path('a', 'v1', 'c-1') == (
            '/comments/assets/a/assetVersionId:commentId/v1:c-1')

    @pytest.mark.parametrize('asset_version_id, comment_id', [
        ('v1:extra', 'c-1'),
        ('v1', 'c:1'),
    ])
    def test_a_colon_in_either_part_is_rejected(self, asset_version_id, comment_id):
        # The handlers split the segment and read element [1] as the commentId, so a third colon
        # shifts which value is validated. Rejecting here keeps that request unconstructible.
        with pytest.raises(InvalidCommentDataError):
            build_comment_path('a', asset_version_id, comment_id)

    def test_an_empty_part_is_rejected(self):
        with pytest.raises(InvalidCommentDataError):
            build_comment_path('a', '', 'c-1')


class TestGetCommentAbsence:
    """A comment that does not exist is a 200 with an empty payload, not a 404."""

    def test_an_empty_payload_raises_not_found(self):
        client, _ = _recording_client({'message': {}})
        with pytest.raises(CommentNotFoundError):
            client.get_comment('my-asset', 'v1', 'missing')

    def test_a_present_comment_is_returned_with_its_envelope(self):
        client, _ = _recording_client({'message': {'commentBody': 'hi'}})
        assert client.get_comment('my-asset', 'v1', 'c-1') == {'message': {'commentBody': 'hi'}}


class _ErrorResponse:
    """A non-2xx response placed at the TRANSPORT boundary.

    Stubbing `_make_request` (as the classes above do) would make an error-mapping test vacuous:
    the stub would raise the HTTPError itself, so the test would pass whether or not the method
    asks `_make_request` to leave the error as an HTTPError. Stubbing `session.request` instead
    leaves the real `_make_request` in the path, where `raise_http_errors` is what decides between
    the method's own status mapping and a generic `APIError`.
    """

    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.text = json.dumps(payload)
        self.content = self.text.encode('utf-8')
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        raise requests.exceptions.HTTPError(str(self.status_code), response=self)


def _client_with_transport(response):
    """An APIClient whose HTTP session returns `response`, with the real request pipeline intact."""
    profile_manager = MagicMock()
    profile_manager.is_override_token.return_value = False
    profile_manager.is_token_expired.return_value = False
    profile_manager.load_auth_profile.return_value = None

    client = APIClient("https://api.example.com", profile_manager=profile_manager)
    client.session.request = lambda *args, **kwargs: response
    return client


class TestCommentErrorMapping:
    """A rejected request reaches the comment exception the commands catch, not a generic error."""

    def test_a_404_on_update_is_a_comment_not_found(self):
        client = _client_with_transport(_ErrorResponse(404, {'message': 'Record not found'}))
        with pytest.raises(CommentNotFoundError) as raised:
            client.update_comment('a', 'v', 'c', 'body')
        # The handler reports a missing comment and an unresolvable asset with the same status, so
        # its message has to survive into the error the operator sees.
        assert 'Record not found' in str(raised.value)

    def test_a_404_on_a_listing_is_an_asset_not_found(self):
        client = _client_with_transport(_ErrorResponse(404, {'message': 'Asset not found'}))
        with pytest.raises(AssetNotFoundError):
            client.list_asset_comments('a')

    def test_a_400_on_add_is_invalid_comment_data(self):
        client = _client_with_transport(_ErrorResponse(400, {'message': 'commentBody is too long'}))
        with pytest.raises(InvalidCommentDataError) as raised:
            client.add_comment('a', 'v', 'c', 'x' * 20000)
        assert 'too long' in str(raised.value)


class TestCommentList:
    def test_list_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].list_asset_comments.return_value = {
                'message': [{
                    'assetVersionId:commentId': 'v1:c-1',
                    'commentBody': 'first remark',
                    'commentOwnerUsername': 'alice@example.com',
                    'dateCreated': '2026-09-01T00:00:00.000Z',
                }]
            }
            result = cli_runner.invoke(cli, ['comment', 'list', '-a', 'my-asset'])

            assert result.exit_code == 0
            assert 'first remark' in result.output
            mocks['api_client'].list_asset_comments.assert_called_once_with(
                'my-asset', max_items=None, page_size=None, starting_token=None)

    def test_list_with_a_version_uses_the_version_route(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].list_asset_version_comments.return_value = {'message': []}
            result = cli_runner.invoke(cli, ['comment', 'list', '-a', 'my-asset', '-v', 'v1'])

            assert result.exit_code == 0
            mocks['api_client'].list_asset_version_comments.assert_called_once_with(
                'my-asset', 'v1', max_items=None, page_size=None, starting_token=None)
            mocks['api_client'].list_asset_comments.assert_not_called()

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].list_asset_comments.return_value = {
                'message': [{'assetVersionId:commentId': 'v1:c-1'}]}
            result = cli_runner.invoke(cli, ['comment', 'list', '-a', 'my-asset', '--json-output'])

            assert result.exit_code == 0
            # The envelope is unwrapped for output, as the pipeline and execution groups do.
            assert json.loads(result.output) == [{'assetVersionId:commentId': 'v1:c-1'}]

    def test_list_asset_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].list_asset_comments.side_effect = AssetNotFoundError("Asset not found")
            result = cli_runner.invoke(cli, ['comment', 'list', '-a', 'nope'])
            assert result.exit_code != 0

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('comment'):
            result = cli_runner.invoke(cli, ['comment', 'list', '-a', 'my-asset'])
            # Global exception handling: SetupRequiredError propagates rather than being
            # printed, so `result.output` is empty and the message lives on the exception.
            # This matches the convention in test_tag_commands.py and its siblings.
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)


class TestCommentGet:
    def test_get_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].get_comment.return_value = {
                'message': {'assetVersionId:commentId': 'v1:c-1', 'commentBody': 'a remark'}}
            result = cli_runner.invoke(
                cli, ['comment', 'get', '-a', 'my-asset', '-v', 'v1', '-c', 'c-1'])

            assert result.exit_code == 0
            assert 'a remark' in result.output
            mocks['api_client'].get_comment.assert_called_once_with('my-asset', 'v1', 'c-1')

    def test_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].get_comment.side_effect = CommentNotFoundError("not found")
            result = cli_runner.invoke(
                cli, ['comment', 'get', '-a', 'my-asset', '-v', 'v1', '-c', 'gone'])
            assert result.exit_code != 0


class TestCommentAdd:
    def test_add_sends_the_body_and_the_supplied_id(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].add_comment.return_value = {'message': 'Succeeded'}
            result = cli_runner.invoke(cli, [
                'comment', 'add', '-a', 'my-asset', '-v', 'v1', '-c', 'c-1', '-b', 'a remark'])

            assert result.exit_code == 0
            mocks['api_client'].add_comment.assert_called_once_with(
                'my-asset', 'v1', 'c-1', 'a remark')

    def test_add_generates_a_comment_id_and_reports_it(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].add_comment.return_value = {'message': 'Succeeded'}
            result = cli_runner.invoke(cli, [
                'comment', 'add', '-a', 'my-asset', '-v', 'v1', '-b', 'a remark',
                '--json-output'])

            assert result.exit_code == 0
            generated = mocks['api_client'].add_comment.call_args[0][2]
            assert generated, "no comment id was generated for the request"
            # The endpoint acknowledges with a status string only, so the id has to come back here
            # or the caller cannot address the comment it just created.
            assert json.loads(result.output)['commentId'] == generated

    def test_add_invalid_data(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].add_comment.side_effect = InvalidCommentDataError("too long")
            result = cli_runner.invoke(cli, [
                'comment', 'add', '-a', 'my-asset', '-v', 'v1', '-b', 'x' * 10])
            assert result.exit_code != 0


class TestCommentUpdate:
    def test_update_sends_the_new_body(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].update_comment.return_value = {'message': 'Succeeded'}
            result = cli_runner.invoke(cli, [
                'comment', 'update', '-a', 'my-asset', '-v', 'v1', '-c', 'c-1',
                '-b', 'corrected'])

            assert result.exit_code == 0
            mocks['api_client'].update_comment.assert_called_once_with(
                'my-asset', 'v1', 'c-1', 'corrected')

    def test_update_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].update_comment.side_effect = CommentNotFoundError("Record not found")
            result = cli_runner.invoke(cli, [
                'comment', 'update', '-a', 'my-asset', '-v', 'v1', '-c', 'gone', '-b', 'x'])
            assert result.exit_code != 0


class TestCommentDelete:
    def test_delete_requires_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            result = cli_runner.invoke(cli, [
                'comment', 'delete', '-a', 'my-asset', '-v', 'v1', '-c', 'c-1'])

            assert result.exit_code != 0
            # The request must not have been made — a confirmation gate that still deletes is worse
            # than none at all.
            mocks['api_client'].delete_comment.assert_not_called()

    def test_delete_requires_confirm_in_json_mode(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            result = cli_runner.invoke(cli, [
                'comment', 'delete', '-a', 'my-asset', '-v', 'v1', '-c', 'c-1', '--json-output'])

            assert result.exit_code != 0
            mocks['api_client'].delete_comment.assert_not_called()
            assert json.loads(result.output)['error'] == 'Confirmation required'

    def test_delete_with_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].delete_comment.return_value = {'message': 'Comment deleted'}
            result = cli_runner.invoke(cli, [
                'comment', 'delete', '-a', 'my-asset', '-v', 'v1', '-c', 'c-1', '--confirm'])

            assert result.exit_code == 0
            mocks['api_client'].delete_comment.assert_called_once_with('my-asset', 'v1', 'c-1')

    def test_delete_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('comment') as mocks:
            mocks['api_client'].delete_comment.side_effect = CommentNotFoundError("Record not found")
            result = cli_runner.invoke(cli, [
                'comment', 'delete', '-a', 'my-asset', '-v', 'v1', '-c', 'gone', '--confirm'])
            assert result.exit_code != 0
