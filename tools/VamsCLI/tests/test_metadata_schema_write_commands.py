"""Tests for the metadata-schema create, update and delete commands.

The read side of the group shipped; the three write verbs the API and the web both expose could
not be reached from the CLI at all. The first class asserts the HTTP method, the path, and the
request body each `APIClient` method builds — including the two shapes that are easy to get
subtly wrong: the update keyed on a metadataSchemaId in the BODY rather than the path, and the
delete whose body must carry the confirmation interlock.
"""

import json
from unittest.mock import MagicMock, Mock

import pytest
import requests

from vamscli.main import cli
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import (
    DatabaseNotFoundError,
    InvalidMetadataSchemaDataError,
    MetadataSchemaDeletionError,
    MetadataSchemaNotFoundError,
    SetupRequiredError,
)


FIELDS = [{'metadataFieldKeyName': 'reviewer', 'metadataFieldValueType': 'string', 'required': True}]


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


class TestMetadataSchemaWriteRequestPathsAndMethods:
    def test_create_posts_the_collection_route(self):
        client, calls = _recording_client({'metadataSchemaId': 's-1', 'operation': 'create'})
        client.create_metadata_schema({
            'databaseId': 'my-database',
            'metadataSchemaEntityType': 'assetMetadata',
            'schemaName': 'Review',
            'fields': {'fields': FIELDS},
            'enabled': True,
        })

        assert calls[0]['method'] == 'POST'
        # The collection route, not a path-scoped variant: /metadataschema/{databaseId} is not a
        # route the API registers, and API Gateway rejects it before any handler runs.
        assert calls[0]['endpoint'] == '/metadataschema'
        assert calls[0]['kwargs']['json']['fields'] == {'fields': FIELDS}

    def test_update_puts_the_collection_route_with_the_id_in_the_body(self):
        client, calls = _recording_client({'metadataSchemaId': 's-1', 'operation': 'update'})
        client.update_metadata_schema('s-1', {'schemaName': 'Review v2'})

        assert calls[0]['method'] == 'PUT'
        assert calls[0]['endpoint'] == '/metadataschema'
        # The schema being updated is named in the body; there is no id-scoped PUT route.
        assert calls[0]['kwargs']['json'] == {
            'schemaName': 'Review v2', 'metadataSchemaId': 's-1'}

    def test_update_does_not_mutate_the_caller_s_dict(self):
        client, _ = _recording_client({'operation': 'update'})
        update_data = {'schemaName': 'Review v2'}
        client.update_metadata_schema('s-1', update_data)

        assert update_data == {'schemaName': 'Review v2'}

    def test_delete_deletes_the_id_scoped_route_with_the_confirmation_body(self):
        client, calls = _recording_client({'metadataSchemaId': 's-1', 'operation': 'delete'})
        client.delete_metadata_schema('my-database', 's-1')

        assert calls[0]['method'] == 'DELETE'
        assert calls[0]['endpoint'] == '/database/my-database/metadataSchema/s-1'
        # Without confirmDelete the endpoint rejects the request; the model's default is false.
        assert calls[0]['kwargs']['json'] == {'confirmDelete': True}

    def test_delete_addresses_the_global_scope_the_same_way(self):
        client, calls = _recording_client({'operation': 'delete'})
        client.delete_metadata_schema('GLOBAL', 's-1')

        assert calls[0]['endpoint'] == '/database/GLOBAL/metadataSchema/s-1'


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


class TestMetadataSchemaWriteErrorMapping:
    def test_a_rejected_schema_is_invalid_schema_data(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'controlledListKeys is required for INLINE_CONTROLLED_LIST type'}))
        with pytest.raises(InvalidMetadataSchemaDataError) as raised:
            client.create_metadata_schema({'databaseId': 'my-database'})
        assert 'controlledListKeys' in str(raised.value)

    def test_a_missing_database_is_a_database_not_found(self):
        # The handler reports this as a 400 through general_error, not a 404, so the message is the
        # only thing that separates it from a rejected schema definition.
        client = _client_with_transport(_ErrorResponse(400, {'message': 'Database does not exist'}))
        with pytest.raises(DatabaseNotFoundError):
            client.create_metadata_schema({'databaseId': 'nope'})

    def test_a_missing_schema_on_update_is_not_found(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'Metadata schema not found'}))
        with pytest.raises(MetadataSchemaNotFoundError):
            client.update_metadata_schema('gone', {'schemaName': 'x'})

    def test_a_rejected_delete_is_a_deletion_error(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'confirmDelete must be true for deletion'}))
        with pytest.raises(MetadataSchemaDeletionError):
            client.delete_metadata_schema('my-database', 's-1')


class TestMetadataSchemaCreate:
    def test_create_builds_the_request_from_the_options(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].create_metadata_schema.return_value = {
                'success': True, 'message': 'created', 'metadataSchemaId': 's-1',
                'operation': 'create', 'timestamp': '2026-09-01T00:00:00Z'}
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'my-database', '-e', 'assetMetadata',
                '-n', 'Review', '-f', json.dumps(FIELDS)])

            assert result.exit_code == 0
            assert mocks['api_client'].create_metadata_schema.call_args[0][0] == {
                'databaseId': 'my-database',
                'metadataSchemaEntityType': 'assetMetadata',
                'schemaName': 'Review',
                # A bare array is wrapped into the {'fields': [...]} object the API takes.
                'fields': {'fields': FIELDS},
                'enabled': True,
            }

    def test_create_accepts_the_wrapped_fields_object(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].create_metadata_schema.return_value = {'metadataSchemaId': 's-1'}
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'my-database', '-e', 'assetMetadata',
                '-n', 'Review', '-f', json.dumps({'fields': FIELDS})])

            assert result.exit_code == 0
            assert mocks['api_client'].create_metadata_schema.call_args[0][0]['fields'] == {
                'fields': FIELDS}

    def test_create_reads_fields_from_a_file(self, cli_runner, generic_command_mocks, tmp_path):
        fields_file = tmp_path / "fields.json"
        fields_file.write_text(json.dumps(FIELDS), encoding='utf-8')

        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].create_metadata_schema.return_value = {'metadataSchemaId': 's-1'}
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'my-database', '-e', 'assetMetadata',
                '-n', 'Review', '-f', str(fields_file)])

            assert result.exit_code == 0
            assert mocks['api_client'].create_metadata_schema.call_args[0][0]['fields'] == {
                'fields': FIELDS}

    def test_create_sends_disabled_when_asked(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].create_metadata_schema.return_value = {'metadataSchemaId': 's-1'}
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'my-database', '-e', 'fileMetadata',
                '-n', 'CAD', '-f', json.dumps(FIELDS),
                '--file-key-type-restriction', '.stp,.step', '--disabled'])

            assert result.exit_code == 0
            sent = mocks['api_client'].create_metadata_schema.call_args[0][0]
            assert sent['enabled'] is False
            assert sent['fileKeyTypeRestriction'] == '.stp,.step'

    def test_create_omits_the_file_restriction_when_not_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].create_metadata_schema.return_value = {'metadataSchemaId': 's-1'}
            cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'my-database', '-e', 'assetMetadata',
                '-n', 'Review', '-f', json.dumps(FIELDS)])

            # The endpoint rejects the field on entity types that do not support it, so an absent
            # option must not become an empty string in the body.
            assert 'fileKeyTypeRestriction' not in \
                mocks['api_client'].create_metadata_schema.call_args[0][0]

    def test_create_rejects_an_unknown_entity_type(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'my-database', '-e', 'somethingElse',
                '-n', 'Review', '-f', json.dumps(FIELDS)])

            assert result.exit_code != 0
            mocks['api_client'].create_metadata_schema.assert_not_called()

    def test_create_rejects_unparseable_fields(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'my-database', '-e', 'assetMetadata',
                '-n', 'Review', '-f', '{not json at all'])

            assert result.exit_code != 0
            mocks['api_client'].create_metadata_schema.assert_not_called()

    def test_create_rejects_an_object_without_a_fields_array(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'my-database', '-e', 'assetMetadata',
                '-n', 'Review', '-f', json.dumps({'columns': FIELDS})])

            assert result.exit_code != 0
            mocks['api_client'].create_metadata_schema.assert_not_called()

    def test_create_invalid_schema_data(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].create_metadata_schema.side_effect = InvalidMetadataSchemaDataError(
                "controlledListKeys is required for INLINE_CONTROLLED_LIST type")
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'my-database', '-e', 'assetMetadata',
                '-n', 'Review', '-f', json.dumps(FIELDS)])
            assert result.exit_code != 0

    def test_create_database_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].create_metadata_schema.side_effect = DatabaseNotFoundError(
                "Database does not exist")
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'nope', '-e', 'assetMetadata',
                '-n', 'Review', '-f', json.dumps(FIELDS)])
            assert result.exit_code != 0

    def test_create_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('metadata_schema'):
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'create', '-d', 'my-database', '-e', 'assetMetadata',
                '-n', 'Review', '-f', json.dumps(FIELDS)])
            # Global exception handling: SetupRequiredError propagates rather than being
            # printed, so `result.output` is empty and the message lives on the exception.
            # This matches the convention in test_tag_commands.py and its siblings.
            assert result.exit_code == 1
            assert result.exception
            assert isinstance(result.exception, SetupRequiredError)


class TestMetadataSchemaUpdate:
    def test_update_sends_only_the_options_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].update_metadata_schema.return_value = {
                'metadataSchemaId': 's-1', 'operation': 'update', 'message': 'updated'}
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'update', '-s', 's-1', '-n', 'Review v2'])

            assert result.exit_code == 0
            mocks['api_client'].update_metadata_schema.assert_called_once_with(
                's-1', {'schemaName': 'Review v2'})

    def test_update_can_disable_a_schema(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].update_metadata_schema.return_value = {'metadataSchemaId': 's-1'}
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'update', '-s', 's-1', '--disabled'])

            assert result.exit_code == 0
            mocks['api_client'].update_metadata_schema.assert_called_once_with(
                's-1', {'enabled': False})

    def test_update_can_enable_a_schema(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].update_metadata_schema.return_value = {'metadataSchemaId': 's-1'}
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'update', '-s', 's-1', '--enabled'])

            assert result.exit_code == 0
            mocks['api_client'].update_metadata_schema.assert_called_once_with(
                's-1', {'enabled': True})

    def test_update_requires_at_least_one_change(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            result = cli_runner.invoke(cli, ['metadata-schema', 'update', '-s', 's-1'])

            # The endpoint rejects a body with nothing to change; refusing here says why.
            assert result.exit_code != 0
            mocks['api_client'].update_metadata_schema.assert_not_called()

    def test_update_replaces_the_field_set(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].update_metadata_schema.return_value = {'metadataSchemaId': 's-1'}
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'update', '-s', 's-1', '-f', json.dumps(FIELDS)])

            assert result.exit_code == 0
            assert mocks['api_client'].update_metadata_schema.call_args[0][1]['fields'] == {
                'fields': FIELDS}

    def test_update_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].update_metadata_schema.side_effect = MetadataSchemaNotFoundError(
                "Metadata schema not found")
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'update', '-s', 'gone', '-n', 'Review v2'])
            assert result.exit_code != 0


class TestMetadataSchemaDelete:
    def test_delete_requires_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'delete', '-d', 'my-database', '-s', 's-1'])

            assert result.exit_code != 0
            mocks['api_client'].delete_metadata_schema.assert_not_called()

    def test_delete_requires_confirm_in_json_mode(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'delete', '-d', 'my-database', '-s', 's-1', '--json-output'])

            assert result.exit_code != 0
            mocks['api_client'].delete_metadata_schema.assert_not_called()
            assert json.loads(result.output)['error'] == 'Confirmation required'

    def test_delete_with_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].delete_metadata_schema.return_value = {
                'metadataSchemaId': 's-1', 'operation': 'delete', 'message': 'deleted'}
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'delete', '-d', 'my-database', '-s', 's-1', '--confirm'])

            assert result.exit_code == 0
            mocks['api_client'].delete_metadata_schema.assert_called_once_with('my-database', 's-1')

    def test_delete_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].delete_metadata_schema.side_effect = MetadataSchemaNotFoundError(
                "Metadata schema not found")
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'delete', '-d', 'my-database', '-s', 'gone', '--confirm'])
            assert result.exit_code != 0

    def test_delete_rejected(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('metadata_schema') as mocks:
            mocks['api_client'].delete_metadata_schema.side_effect = MetadataSchemaDeletionError(
                "deletion failed")
            result = cli_runner.invoke(cli, [
                'metadata-schema', 'delete', '-d', 'my-database', '-s', 's-1', '--confirm'])
            assert result.exit_code != 0


class TestFieldsNormalization:
    """--fields accepts either shape, and rejects anything that is neither."""

    def test_an_array_of_definitions_is_wrapped(self):
        from vamscli.commands.metadata_schema import normalize_fields_input
        assert normalize_fields_input(json.dumps(FIELDS)) == {'fields': FIELDS}

    def test_a_scalar_is_rejected(self):
        from vamscli.commands.metadata_schema import normalize_fields_input
        import click
        with pytest.raises(click.BadParameter):
            normalize_fields_input('"just a string"')
