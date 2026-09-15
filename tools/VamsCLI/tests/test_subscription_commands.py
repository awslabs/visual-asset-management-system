"""Tests for the subscription command group.

The subscriptions API carries six verbs that no CLI command could reach. The first class asserts
the HTTP method, the exact path, and the request body each `APIClient` method builds — including
the two DELETEs, which go to different routes and mean different things.
"""

import json
from unittest.mock import MagicMock, Mock

import pytest
import requests

from vamscli.main import cli
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import (
    APIError,
    AssetNotFoundError,
    InvalidSubscriptionDataError,
    SetupRequiredError,
    SubscriptionAlreadyExistsError,
    SubscriptionNotFoundError,
)


class _Response:
    """Minimal stand-in for a requests.Response carrying a JSON body."""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _recording_client(payload=None):
    """An APIClient whose transport is replaced by a recorder (see test_comment_commands)."""
    client = APIClient("https://api.example.com", profile_manager=Mock())
    calls = []

    def _fake_request(method, endpoint, include_auth=True, **kwargs):
        calls.append({'method': method, 'endpoint': endpoint, 'kwargs': kwargs})
        return _Response({} if payload is None else payload)

    client._make_request = _fake_request
    return client, calls


class TestSubscriptionRequestPathsAndMethods:
    """Each of the six subscription verbs reaches its own registered route."""

    def test_list_subscriptions_gets_the_collection_route(self):
        client, calls = _recording_client({'message': {'Items': []}})
        client.list_subscriptions()

        assert calls[0]['method'] == 'GET'
        assert calls[0]['endpoint'] == '/subscriptions'
        assert calls[0]['kwargs']['params'] == {}

    def test_list_subscriptions_forwards_only_the_pagination_given(self):
        client, calls = _recording_client({'message': {'Items': []}})
        client.list_subscriptions(max_items=10, page_size=5, starting_token='tok')

        assert calls[0]['kwargs']['params'] == {
            'maxItems': 10, 'pageSize': 5, 'startingToken': 'tok'}

    def test_create_subscription_posts_the_collection_route(self):
        client, calls = _recording_client({'message': 'success'})
        client.create_subscription('Asset Version Change', 'Asset', 'my-asset', ['alice@x.com'])

        assert calls[0]['method'] == 'POST'
        assert calls[0]['endpoint'] == '/subscriptions'
        assert calls[0]['kwargs']['json'] == {
            'eventName': 'Asset Version Change',
            'entityName': 'Asset',
            'entityId': 'my-asset',
            'subscribers': ['alice@x.com'],
        }

    def test_update_subscription_puts_the_collection_route(self):
        client, calls = _recording_client({'message': 'success'})
        client.update_subscription('Asset Version Change', 'Asset', 'my-asset',
                                   ['alice@x.com', 'bob@x.com'])

        assert calls[0]['method'] == 'PUT'
        assert calls[0]['endpoint'] == '/subscriptions'
        assert calls[0]['kwargs']['json']['subscribers'] == ['alice@x.com', 'bob@x.com']

    def test_delete_subscription_deletes_the_collection_route_with_a_body(self):
        client, calls = _recording_client({'message': 'success'})
        client.delete_subscription('Asset Version Change', 'Asset', 'my-asset', ['alice@x.com'])

        assert calls[0]['method'] == 'DELETE'
        assert calls[0]['endpoint'] == '/subscriptions'
        # All four keys are required on a DELETE too: the endpoint validates the subscribers list
        # and then removes the whole record. Dropping it is a 400, not a default.
        assert calls[0]['kwargs']['json'] == {
            'eventName': 'Asset Version Change',
            'entityName': 'Asset',
            'entityId': 'my-asset',
            'subscribers': ['alice@x.com'],
        }

    def test_unsubscribe_deletes_the_unsubscribe_route(self):
        client, calls = _recording_client({'message': 'success'})
        client.unsubscribe('Asset Version Change', 'Asset', 'my-asset', 'bob@x.com')

        # A different route from delete_subscription, and the one that leaves the record in place.
        assert calls[0]['method'] == 'DELETE'
        assert calls[0]['endpoint'] == '/unsubscribe'
        # The endpoint reads only the first entry, so the single subscriber is wrapped in a list.
        assert calls[0]['kwargs']['json']['subscribers'] == ['bob@x.com']

    def test_check_subscription_posts_the_check_route(self):
        client, calls = _recording_client({'message': 'success'})
        client.check_subscription('my-asset', 'alice@x.com')

        assert calls[0]['method'] == 'POST'
        assert calls[0]['endpoint'] == '/check-subscription'
        # This endpoint takes only these two fields; it fixes the event and entity itself.
        assert calls[0]['kwargs']['json'] == {'assetId': 'my-asset', 'userId': 'alice@x.com'}

    def test_the_two_deletes_are_not_the_same_request(self):
        """A regression guard: collapsing them would silently delete a whole subscription."""
        client_a, calls_a = _recording_client({'message': 'success'})
        client_a.delete_subscription('Asset Version Change', 'Asset', 'a', ['u@x.com'])
        client_b, calls_b = _recording_client({'message': 'success'})
        client_b.unsubscribe('Asset Version Change', 'Asset', 'a', 'u@x.com')

        assert calls_a[0]['endpoint'] != calls_b[0]['endpoint']


class _ErrorResponse:
    """A non-2xx response placed at the TRANSPORT boundary (see test_comment_commands for why)."""

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


class TestSubscriptionErrorMapping:
    """The endpoint reports every business failure as a 400, so the message is what discriminates."""

    def test_an_already_subscribed_user_is_its_own_error(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'Subscription already exists for some of the specified subscribers.'}))
        with pytest.raises(SubscriptionAlreadyExistsError):
            client.create_subscription('Asset Version Change', 'Asset', 'a', ['u@x.com'])

    def test_a_missing_subscription_on_update_is_not_found(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'Subscription does not exists for eventName.'}))
        with pytest.raises(SubscriptionNotFoundError):
            client.update_subscription('Asset Version Change', 'Asset', 'a', ['u@x.com'])

    def test_a_missing_subscription_on_unsubscribe_is_not_found(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'Subscription does not exists for eventName.'}))
        with pytest.raises(SubscriptionNotFoundError):
            client.unsubscribe('Asset Version Change', 'Asset', 'a', 'u@x.com')

    def test_any_other_400_is_invalid_data_rather_than_not_found(self):
        # The discriminating branch has to fall the other way too, or every 400 would read as a
        # missing subscription and the operator would be told to create one that already exists.
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'Subscriber u@x.com does not have a valid email to use.'}))
        with pytest.raises(InvalidSubscriptionDataError):
            client.create_subscription('Asset Version Change', 'Asset', 'a', ['u@x.com'])

    def test_an_unresolvable_asset_is_an_asset_not_found(self):
        client = _client_with_transport(_ErrorResponse(404, {'message': 'Asset not found'}))
        with pytest.raises(AssetNotFoundError):
            client.create_subscription('Asset Version Change', 'Asset', 'nope', ['u@x.com'])


class TestSubscriptionList:
    def test_list_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].list_subscriptions.return_value = {
                'message': {'Items': [{
                    'eventName': 'Asset Version Change',
                    'entityName': 'Asset',
                    'entityId': 'my-asset',
                    'subscribers': ['alice@example.com'],
                    'entityValue': 'My Asset',
                    'databaseId': 'my-database',
                }]}
            }
            result = cli_runner.invoke(cli, ['subscription', 'list'])

            assert result.exit_code == 0
            assert 'alice@example.com' in result.output
            mocks['api_client'].list_subscriptions.assert_called_once_with(
                max_items=None, page_size=None, starting_token=None)

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].list_subscriptions.return_value = {
                'message': {'Items': [{'entityId': 'my-asset'}], 'NextToken': 'tok'}}
            result = cli_runner.invoke(cli, ['subscription', 'list', '--json-output'])

            assert result.exit_code == 0
            payload = json.loads(result.output)
            assert payload['Items'][0]['entityId'] == 'my-asset'
            assert payload['NextToken'] == 'tok'

    def test_list_api_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].list_subscriptions.side_effect = APIError("boom")
            result = cli_runner.invoke(cli, ['subscription', 'list'])
            assert result.exit_code != 0

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('subscription'):
            result = cli_runner.invoke(cli, ['subscription', 'list'])
            # Global exception handling: SetupRequiredError propagates rather than being
            # printed, so `result.output` is empty and the message lives on the exception.
            # This matches the convention in test_tag_commands.py and its siblings.
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)


class TestSubscriptionCreate:
    def test_create_sends_every_required_field(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].create_subscription.return_value = {'message': 'success'}
            result = cli_runner.invoke(cli, [
                'subscription', 'create', '-i', 'my-asset',
                '-s', 'alice@example.com', '-s', 'bob@example.com'])

            assert result.exit_code == 0
            # The event and entity default to the only pair the API accepts.
            mocks['api_client'].create_subscription.assert_called_once_with(
                'Asset Version Change', 'Asset', 'my-asset',
                ['alice@example.com', 'bob@example.com'])

    def test_create_honours_an_explicit_event_and_entity(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].create_subscription.return_value = {'message': 'success'}
            result = cli_runner.invoke(cli, [
                'subscription', 'create', '-i', 'my-asset', '-s', 'alice@example.com',
                '--event-name', 'Asset Version Change', '--entity-name', 'Asset'])

            assert result.exit_code == 0
            assert mocks['api_client'].create_subscription.call_args[0][0] == 'Asset Version Change'

    def test_create_requires_a_subscriber(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            result = cli_runner.invoke(cli, ['subscription', 'create', '-i', 'my-asset'])

            assert result.exit_code != 0
            mocks['api_client'].create_subscription.assert_not_called()

    def test_create_already_exists(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].create_subscription.side_effect = SubscriptionAlreadyExistsError(
                "Subscription already exists for some of the specified subscribers.")
            result = cli_runner.invoke(cli, [
                'subscription', 'create', '-i', 'my-asset', '-s', 'alice@example.com'])
            assert result.exit_code != 0

    def test_create_asset_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].create_subscription.side_effect = AssetNotFoundError("Asset not found")
            result = cli_runner.invoke(cli, [
                'subscription', 'create', '-i', 'nope', '-s', 'alice@example.com'])
            assert result.exit_code != 0


class TestSubscriptionUpdate:
    def test_update_sends_the_replacement_list(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].update_subscription.return_value = {'message': 'success'}
            result = cli_runner.invoke(cli, [
                'subscription', 'update', '-i', 'my-asset', '-s', 'carol@example.com'])

            assert result.exit_code == 0
            mocks['api_client'].update_subscription.assert_called_once_with(
                'Asset Version Change', 'Asset', 'my-asset', ['carol@example.com'])

    def test_update_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].update_subscription.side_effect = SubscriptionNotFoundError(
                "Subscription does not exists for eventName.")
            result = cli_runner.invoke(cli, [
                'subscription', 'update', '-i', 'my-asset', '-s', 'carol@example.com'])
            assert result.exit_code != 0


class TestSubscriptionDelete:
    def test_delete_requires_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            result = cli_runner.invoke(cli, [
                'subscription', 'delete', '-i', 'my-asset', '-s', 'alice@example.com'])

            assert result.exit_code != 0
            mocks['api_client'].delete_subscription.assert_not_called()

    def test_delete_requires_confirm_in_json_mode(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            result = cli_runner.invoke(cli, [
                'subscription', 'delete', '-i', 'my-asset', '-s', 'alice@example.com',
                '--json-output'])

            assert result.exit_code != 0
            mocks['api_client'].delete_subscription.assert_not_called()
            assert json.loads(result.output)['error'] == 'Confirmation required'

    def test_delete_with_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].delete_subscription.return_value = {'message': 'success'}
            result = cli_runner.invoke(cli, [
                'subscription', 'delete', '-i', 'my-asset', '-s', 'alice@example.com', '--confirm'])

            assert result.exit_code == 0
            mocks['api_client'].delete_subscription.assert_called_once_with(
                'Asset Version Change', 'Asset', 'my-asset', ['alice@example.com'])

    def test_delete_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].delete_subscription.side_effect = SubscriptionNotFoundError(
                "Subscription not found for the specified event and entity")
            result = cli_runner.invoke(cli, [
                'subscription', 'delete', '-i', 'my-asset', '-s', 'alice@example.com', '--confirm'])
            assert result.exit_code != 0


class TestSubscriptionUnsubscribe:
    def test_unsubscribe_requires_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            result = cli_runner.invoke(cli, [
                'subscription', 'unsubscribe', '-i', 'my-asset', '-s', 'bob@example.com'])

            assert result.exit_code != 0
            mocks['api_client'].unsubscribe.assert_not_called()

    def test_unsubscribe_with_confirm_passes_one_subscriber(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].unsubscribe.return_value = {'message': 'success'}
            result = cli_runner.invoke(cli, [
                'subscription', 'unsubscribe', '-i', 'my-asset', '-s', 'bob@example.com',
                '--confirm'])

            assert result.exit_code == 0
            # A single value, not a list: the endpoint removes one subscriber per call.
            mocks['api_client'].unsubscribe.assert_called_once_with(
                'Asset Version Change', 'Asset', 'my-asset', 'bob@example.com')

    def test_unsubscribe_does_not_call_delete_subscription(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].unsubscribe.return_value = {'message': 'success'}
            cli_runner.invoke(cli, [
                'subscription', 'unsubscribe', '-i', 'my-asset', '-s', 'bob@example.com',
                '--confirm'])

            mocks['api_client'].delete_subscription.assert_not_called()

    def test_unsubscribe_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].unsubscribe.side_effect = SubscriptionNotFoundError(
                "Subscription does not exists for eventName.")
            result = cli_runner.invoke(cli, [
                'subscription', 'unsubscribe', '-i', 'my-asset', '-s', 'bob@example.com',
                '--confirm'])
            assert result.exit_code != 0


class TestSubscriptionCheck:
    def test_check_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].check_subscription.return_value = {'message': 'success'}
            result = cli_runner.invoke(cli, [
                'subscription', 'check', '-a', 'my-asset', '-u', 'alice@example.com'])

            assert result.exit_code == 0
            mocks['api_client'].check_subscription.assert_called_once_with(
                'my-asset', 'alice@example.com')

    def test_check_reports_an_absent_subscription(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            # Not subscribed is also a 200, so the answer is the message rather than the status.
            mocks['api_client'].check_subscription.return_value = {
                'message': "Subscription doesn't exists."}
            result = cli_runner.invoke(cli, [
                'subscription', 'check', '-a', 'my-asset', '-u', 'alice@example.com',
                '--json-output'])

            assert result.exit_code == 0
            assert json.loads(result.output) == "Subscription doesn't exists."

    def test_check_invalid_data(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('subscription') as mocks:
            mocks['api_client'].check_subscription.side_effect = InvalidSubscriptionDataError(
                "userId and assetId are required fields.")
            result = cli_runner.invoke(cli, [
                'subscription', 'check', '-a', 'my-asset', '-u', 'bad user'])
            assert result.exit_code != 0
