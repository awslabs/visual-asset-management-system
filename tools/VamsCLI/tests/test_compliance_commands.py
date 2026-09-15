"""Tests for the `vamscli compliance` command group.

The first two classes pin the `APIClient` layer: the HTTP method, path and body each compliance
method builds (with a transport recorder), and how the handlers' error bodies are mapped onto the
compliance exception family (with an error placed at the transport boundary). The command classes
then cover each command's happy path, its `--json-output` purity, and an API error path through
`generic_command_mocks('compliance')`.

Two shapes are easy to get wrong and are asserted explicitly: a POST to a route that takes no
fields still carries an empty JSON object (the handlers parse the body unconditionally), and the
`complianceAutoEval` flag is sent on the database binding only, because the asset route never
reads it.
"""

import json
from unittest.mock import MagicMock, Mock

import pytest
import requests

from vamscli.main import cli
from vamscli.utils.api_client import APIClient
from vamscli.utils.exceptions import (
    AssetNotFoundError,
    ComplianceCascadeNotFoundError,
    ComplianceSchemaNotFoundError,
    DatabaseNotFoundError,
    InvalidComplianceDataError,
    SetupRequiredError,
)


SCHEMA_BODY = {
    "schemaFormat": "vams-rules-v1",
    "rules": {
        "has-owner": {
            "ruleType": "metadata",
            "enforcement": "quarantine",
            "metadataSchemaRef": {"databaseId": "GLOBAL", "schemaName": "ownership"},
            "checks": [{"name": "required-fields", "validateRequired": True}],
        }
    },
}

SCHEMA_RECORD = {
    "schemaName": "cad-quality",
    "databaseId": "GLOBAL",
    "description": "CAD deliverable checks",
    "schemaBody": SCHEMA_BODY,
    "version": 2,
    "createdAt": "2026-09-01T00:00:00+00:00",
}

STATE_RECORD = {
    "databaseId": "my-database",
    "assetId": "my-asset",
    "complianceState": "quarantined",
    "schemaName": "cad-quality",
    "schemaSource": "database",
    "quarantineReason": "wall-thickness below minimum",
}

CASCADE_RECORD = {
    "cascadeId": "casc-1",
    "state": "pending_approval",
    "triggeredByDatabaseId": "my-database",
    "triggeredByAssetId": "my-asset",
    "triggerReason": "manual trigger",
    "createdAt": "2026-09-01T00:00:00+00:00",
}

AUDIT_ENTRY = {
    "entryId": "e-1",
    "timestamp": "2026-09-01T00:00:00+00:00",
    "eventType": "quarantine_released",
    "databaseId": "my-database",
    "assetId": "my-asset",
    "actor": "user-1",
    "details": "{}",
}


# --- APIClient layer ------------------------------------------------------------


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


class TestComplianceRequestPathsAndMethods:
    def test_list_schemas_gets_the_collection_route_with_the_database_filter(self):
        client, calls = _recording_client({'schemas': []})
        client.list_compliance_schemas(database_id='my-database')
        assert calls[0]['method'] == 'GET'
        assert calls[0]['endpoint'] == '/compliance/schemas'
        assert calls[0]['kwargs']['params'] == {'databaseId': 'my-database'}

    def test_list_schemas_sends_no_filter_when_none_is_given(self):
        client, calls = _recording_client({'schemas': []})
        client.list_compliance_schemas()
        assert 'params' not in calls[0]['kwargs']

    def test_get_schema_gets_the_named_route(self):
        client, calls = _recording_client(SCHEMA_RECORD)
        client.get_compliance_schema('cad-quality')
        assert calls[0]['method'] == 'GET'
        assert calls[0]['endpoint'] == '/compliance/schemas/cad-quality'

    def test_create_schema_posts_the_collection_route(self):
        client, calls = _recording_client({'schemaName': 'cad-quality', 'internalVersion': 1})
        client.create_compliance_schema({'schemaName': 'cad-quality', 'schemaBody': SCHEMA_BODY})
        assert calls[0]['method'] == 'POST'
        assert calls[0]['endpoint'] == '/compliance/schemas'
        assert calls[0]['kwargs']['json'] == {'schemaName': 'cad-quality', 'schemaBody': SCHEMA_BODY}

    def test_update_schema_puts_the_named_route_without_mutating_the_input(self):
        client, calls = _recording_client({'schemaName': 'cad-quality', 'internalVersion': 2})
        update_data = {'schemaBody': SCHEMA_BODY}
        client.update_compliance_schema('cad-quality', update_data)
        assert calls[0]['method'] == 'PUT'
        assert calls[0]['endpoint'] == '/compliance/schemas/cad-quality'
        assert calls[0]['kwargs']['json'] == {'schemaBody': SCHEMA_BODY}
        assert update_data == {'schemaBody': SCHEMA_BODY}

    def test_delete_schema_deletes_the_named_route(self):
        client, calls = _recording_client({'message': 'Success'})
        client.delete_compliance_schema('cad-quality')
        assert calls[0]['method'] == 'DELETE'
        assert calls[0]['endpoint'] == '/compliance/schemas/cad-quality'

    def test_bindings_gets_the_database_route(self):
        client, calls = _recording_client({'databaseId': 'my-database'})
        client.get_compliance_bindings('my-database')
        assert calls[0]['method'] == 'GET'
        assert calls[0]['endpoint'] == '/compliance/bind/my-database'

    def test_bind_database_puts_the_schema_and_the_auto_eval_flag(self):
        client, calls = _recording_client({'schemaName': 'cad-quality'})
        client.bind_compliance_schema('my-database', 'cad-quality', auto_eval=False)
        assert calls[0]['method'] == 'PUT'
        assert calls[0]['endpoint'] == '/compliance/bind/my-database'
        assert calls[0]['kwargs']['json'] == {'schemaName': 'cad-quality', 'complianceAutoEval': False}

    def test_bind_database_omits_the_flag_when_not_given(self):
        client, calls = _recording_client({'schemaName': 'cad-quality'})
        client.bind_compliance_schema('my-database', 'cad-quality')
        assert calls[0]['kwargs']['json'] == {'schemaName': 'cad-quality'}

    def test_bind_asset_puts_the_asset_route_and_never_sends_the_flag(self):
        # The asset route reads schemaName only; a flag sent there would look honoured and be ignored.
        client, calls = _recording_client({'schemaName': 'cad-quality'})
        client.bind_compliance_schema('my-database', 'cad-quality', asset_id='my-asset', auto_eval=False)
        assert calls[0]['endpoint'] == '/compliance/bind/my-database/my-asset'
        assert calls[0]['kwargs']['json'] == {'schemaName': 'cad-quality'}

    def test_unbind_database_and_asset_delete_their_routes(self):
        client, calls = _recording_client({'message': 'removed'})
        client.unbind_compliance_schema('my-database')
        client.unbind_compliance_schema('my-database', asset_id='my-asset')
        assert [c['method'] for c in calls] == ['DELETE', 'DELETE']
        assert calls[0]['endpoint'] == '/compliance/bind/my-database'
        assert calls[1]['endpoint'] == '/compliance/bind/my-database/my-asset'

    def test_evaluate_posts_the_schema_name_when_given(self):
        client, calls = _recording_client({'evaluationId': 'ev-1'})
        client.evaluate_asset_compliance('my-database', 'my-asset', schema_name='cad-quality')
        assert calls[0]['method'] == 'POST'
        assert calls[0]['endpoint'] == '/compliance/evaluate/my-database/my-asset'
        assert calls[0]['kwargs']['json'] == {'schemaName': 'cad-quality'}

    def test_evaluate_posts_an_empty_object_when_no_schema_is_given(self):
        # The handler parses the body unconditionally, so a POST without one fails inside it.
        client, calls = _recording_client({'evaluationId': 'ev-1'})
        client.evaluate_asset_compliance('my-database', 'my-asset')
        assert calls[0]['kwargs']['json'] == {}

    def test_sweep_posts_the_schema_route_with_an_empty_body(self):
        client, calls = _recording_client({'assetsTriggered': []})
        client.sweep_compliance_schema('cad-quality')
        assert calls[0]['method'] == 'POST'
        assert calls[0]['endpoint'] == '/compliance/sweep/cad-quality'
        assert calls[0]['kwargs']['json'] == {}

    def test_evaluations_and_state_routes(self):
        client, calls = _recording_client({})
        client.list_compliance_evaluations('my-database', 'my-asset')
        client.get_compliance_state('my-database', 'my-asset')
        client.get_database_compliance_state('my-database')
        assert [c['endpoint'] for c in calls] == [
            '/compliance/evaluations/my-database/my-asset',
            '/compliance/state/my-database/my-asset',
            '/compliance/state/my-database',
        ]
        assert all(c['method'] == 'GET' for c in calls)
        assert 'params' not in calls[0]['kwargs']

    def test_evaluations_forwards_the_page_size_as_max_items_and_the_token(self):
        # The route reads its page size from `maxItems` (not `pageSize`) and resumes on `startingToken`.
        client, calls = _recording_client({'evaluations': []})
        client.list_compliance_evaluations('my-database', 'my-asset', max_items=10, starting_token='tok')
        assert calls[0]['kwargs']['params'] == {'maxItems': 10, 'startingToken': 'tok'}

    def test_quarantine_routes_and_bodies(self):
        client, calls = _recording_client({})
        client.list_quarantined_assets()
        client.release_quarantine('my-database', 'my-asset')
        client.release_quarantine('my-database', 'my-asset', reason='fixed')
        client.grant_quarantine_exception('my-database', 'my-asset', 'waived')
        assert (calls[0]['method'], calls[0]['endpoint']) == ('GET', '/compliance/quarantine')
        assert 'params' not in calls[0]['kwargs']
        assert calls[1]['endpoint'] == '/compliance/quarantine/my-database/my-asset/release'
        assert calls[1]['kwargs']['json'] == {}
        assert calls[2]['kwargs']['json'] == {'reason': 'fixed'}
        assert calls[3]['endpoint'] == '/compliance/quarantine/my-database/my-asset/exception'
        assert calls[3]['kwargs']['json'] == {'reason': 'waived'}

    def test_cascade_routes_and_bodies(self):
        client, calls = _recording_client({})
        client.list_compliance_cascades()
        client.get_compliance_cascade('casc-1')
        client.create_compliance_cascade('my-database', 'my-asset')
        client.create_compliance_cascade('my-database', 'my-asset', reason='revised', require_approval=False)
        client.approve_compliance_cascade('casc-1', reason='ok')
        client.reject_compliance_cascade('casc-1')
        assert [(c['method'], c['endpoint']) for c in calls] == [
            ('GET', '/compliance/cascades'),
            ('GET', '/compliance/cascades/casc-1'),
            ('POST', '/compliance/cascades'),
            ('POST', '/compliance/cascades'),
            ('POST', '/compliance/cascades/casc-1/approve'),
            ('POST', '/compliance/cascades/casc-1/reject'),
        ]
        assert calls[2]['kwargs']['json'] == {
            'databaseId': 'my-database', 'assetId': 'my-asset', 'requireApproval': True}
        assert calls[3]['kwargs']['json'] == {
            'databaseId': 'my-database', 'assetId': 'my-asset', 'requireApproval': False,
            'reason': 'revised'}
        assert calls[4]['kwargs']['json'] == {'reason': 'ok'}
        assert calls[5]['kwargs']['json'] == {}

    def test_audit_query_forwards_every_filter_it_is_given(self):
        client, calls = _recording_client({'entries': []})
        client.query_compliance_audit(event_type='compliance_check', start_date='2026-09-01',
                                      end_date='2026-09-30', limit=10)
        assert calls[0]['method'] == 'GET'
        assert calls[0]['endpoint'] == '/compliance/audit'
        assert calls[0]['kwargs']['params'] == {
            'eventType': 'compliance_check', 'startDate': '2026-09-01', 'endDate': '2026-09-30',
            'limit': 10}

    def test_audit_query_sends_no_params_when_nothing_is_narrowed(self):
        client, calls = _recording_client({'entries': []})
        client.query_compliance_audit()
        assert 'params' not in calls[0]['kwargs']

    def test_asset_audit_gets_the_asset_route_with_the_date_window(self):
        client, calls = _recording_client({'entries': []})
        client.get_asset_compliance_audit('my-database', 'my-asset', start_date='2026-09-01', limit=5)
        assert calls[0]['endpoint'] == '/compliance/audit/my-database/my-asset'
        assert calls[0]['kwargs']['params'] == {'startDate': '2026-09-01', 'limit': 5}


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


class TestComplianceErrorMapping:
    def test_a_404_on_a_schema_is_schema_not_found(self):
        client = _client_with_transport(_ErrorResponse(404, {'message': "Schema 'gone' not found"}))
        with pytest.raises(ComplianceSchemaNotFoundError):
            client.get_compliance_schema('gone')

    def test_a_400_saying_the_schema_is_not_found_is_schema_not_found(self):
        # The update handler reports a missing schema through validation_error, not a 404.
        client = _client_with_transport(_ErrorResponse(400, {'message': "Schema 'gone' not found"}))
        with pytest.raises(ComplianceSchemaNotFoundError):
            client.update_compliance_schema('gone', {'description': 'x'})

    def test_a_404_on_a_cascade_is_cascade_not_found(self):
        client = _client_with_transport(_ErrorResponse(404, {'message': "Cascade 'gone' not found"}))
        with pytest.raises(ComplianceCascadeNotFoundError):
            client.get_compliance_cascade('gone')

    def test_a_cascade_no_longer_pending_is_cascade_not_found(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'Cascade not found or not in pending_approval state'}))
        with pytest.raises(ComplianceCascadeNotFoundError):
            client.approve_compliance_cascade('casc-1')

    def test_a_missing_asset_is_asset_not_found(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'Asset not found in compliance tracking'}))
        with pytest.raises(AssetNotFoundError):
            client.release_quarantine('my-database', 'my-asset')

    def test_an_asset_message_quoting_a_database_id_is_still_asset_not_found(self):
        # The asset handler quotes `databaseId:assetId`; a database id containing "database" must
        # not steer the classification.
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'Asset my-database:my-asset not found'}))
        with pytest.raises(AssetNotFoundError):
            client.evaluate_asset_compliance('my-database', 'my-asset')

    def test_a_missing_database_is_database_not_found(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': "Database 'nope' not found"}))
        with pytest.raises(DatabaseNotFoundError):
            client.get_compliance_bindings('nope')

    def test_any_other_rejection_is_invalid_compliance_data(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': 'Asset is not quarantined (state: compliant)'}))
        with pytest.raises(InvalidComplianceDataError) as raised:
            client.release_quarantine('my-database', 'my-asset')
        assert 'not quarantined' in str(raised.value)

    def test_the_server_message_reaches_the_user(self):
        client = _client_with_transport(_ErrorResponse(400, {
            'message': "Invalid schema: Rule 'x' has invalid ruleType: none"}))
        with pytest.raises(InvalidComplianceDataError) as raised:
            client.create_compliance_schema({'schemaName': 'x', 'schemaBody': {'rules': {}}})
        assert 'invalid ruleType' in str(raised.value)


# --- Commands -------------------------------------------------------------------


class TestComplianceSchemaCommands:
    def test_list_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_compliance_schemas.return_value = {'schemas': [SCHEMA_RECORD]}
            result = cli_runner.invoke(cli, ['compliance', 'schema', 'list'])
            assert result.exit_code == 0
            assert 'cad-quality' in result.output
            assert 'has-owner' in result.output
            mocks['api_client'].list_compliance_schemas.assert_called_once_with(database_id=None)

    def test_list_forwards_the_database_filter(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_compliance_schemas.return_value = {'schemas': []}
            result = cli_runner.invoke(cli, ['compliance', 'schema', 'list', '-d', 'my-database'])
            assert result.exit_code == 0
            assert 'No compliance schemas found' in result.output
            mocks['api_client'].list_compliance_schemas.assert_called_once_with(database_id='my-database')

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_compliance_schemas.return_value = {'schemas': [SCHEMA_RECORD]}
            result = cli_runner.invoke(cli, ['compliance', 'schema', 'list', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'schemas': [SCHEMA_RECORD]}

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('compliance'):
            result = cli_runner.invoke(cli, ['compliance', 'schema', 'list'])
            # SetupRequiredError propagates to the global handler rather than being printed here.
            assert result.exit_code == 1
            assert isinstance(result.exception, SetupRequiredError)

    def test_get_success_shows_the_body(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_schema.return_value = SCHEMA_RECORD
            result = cli_runner.invoke(cli, ['compliance', 'schema', 'get', '-n', 'cad-quality'])
            assert result.exit_code == 0
            assert 'vams-rules-v1' in result.output
            assert 'metadataSchemaRef' in result.output
            mocks['api_client'].get_compliance_schema.assert_called_once_with('cad-quality')

    def test_get_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_schema.return_value = SCHEMA_RECORD
            result = cli_runner.invoke(cli, ['compliance', 'schema', 'get', '-n', 'cad-quality',
                                             '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == SCHEMA_RECORD

    def test_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_schema.side_effect = ComplianceSchemaNotFoundError(
                "Schema 'gone' not found")
            result = cli_runner.invoke(cli, ['compliance', 'schema', 'get', '-n', 'gone'])
            assert result.exit_code != 0
            assert 'Compliance Schema Not Found' in result.output

    def test_get_not_found_json_error_is_pure(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_schema.side_effect = ComplianceSchemaNotFoundError(
                "Schema 'gone' not found")
            result = cli_runner.invoke(cli, ['compliance', 'schema', 'get', '-n', 'gone', '--json-output'])
            assert result.exit_code == 1
            assert json.loads(result.output) == {
                'error': "Schema 'gone' not found", 'error_type': 'ComplianceSchemaNotFoundError'}

    def test_create_reads_the_body_from_a_file(self, cli_runner, generic_command_mocks, tmp_path):
        schema_file = tmp_path / "cad-quality.json"
        schema_file.write_text(json.dumps(SCHEMA_BODY), encoding='utf-8')
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].create_compliance_schema.return_value = {
                'message': "Schema 'cad-quality' registered as v1", 'schemaName': 'cad-quality',
                'databaseId': 'GLOBAL', 'internalVersion': 1}
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'create', '-n', 'cad-quality', '--schema-file', str(schema_file)])
            assert result.exit_code == 0
            assert 'registered' in result.output
            mocks['api_client'].create_compliance_schema.assert_called_once_with({
                'schemaName': 'cad-quality', 'schemaBody': SCHEMA_BODY})

    def test_create_sends_scope_and_description_when_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].create_compliance_schema.return_value = {'schemaName': 'cad-quality'}
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'create', '-n', 'cad-quality',
                '--schema-file', json.dumps(SCHEMA_BODY), '-d', 'my-database',
                '--description', 'CAD checks'])
            assert result.exit_code == 0
            assert mocks['api_client'].create_compliance_schema.call_args[0][0] == {
                'schemaName': 'cad-quality', 'schemaBody': SCHEMA_BODY,
                'databaseId': 'my-database', 'description': 'CAD checks'}

    def test_create_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].create_compliance_schema.return_value = {
                'schemaName': 'cad-quality', 'internalVersion': 1}
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'create', '-n', 'cad-quality',
                '--schema-file', json.dumps(SCHEMA_BODY), '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'schemaName': 'cad-quality', 'internalVersion': 1}

    def test_create_rejects_an_unreadable_schema_file(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'create', '-n', 'cad-quality', '--schema-file', '/no/such/file.json'])
            assert result.exit_code != 0
            assert 'neither a readable file path' in result.output
            mocks['api_client'].create_compliance_schema.assert_not_called()

    def test_create_rejects_a_body_that_is_not_an_object(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'create', '-n', 'cad-quality', '--schema-file', '["not", "an", "object"]'])
            assert result.exit_code != 0
            mocks['api_client'].create_compliance_schema.assert_not_called()

    def test_create_invalid_schema(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].create_compliance_schema.side_effect = InvalidComplianceDataError(
                "Invalid schema: At least one rule is required")
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'create', '-n', 'cad-quality', '--schema-file', json.dumps(SCHEMA_BODY)])
            assert result.exit_code != 0
            assert 'At least one rule is required' in result.output

    def test_update_sends_only_the_options_given(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].update_compliance_schema.return_value = {
                'schemaName': 'cad-quality', 'internalVersion': 2}
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'update', '-n', 'cad-quality', '--description', 'Tightened'])
            assert result.exit_code == 0
            mocks['api_client'].update_compliance_schema.assert_called_once_with(
                'cad-quality', {'description': 'Tightened'})

    def test_update_replaces_the_body_from_a_file(self, cli_runner, generic_command_mocks, tmp_path):
        schema_file = tmp_path / "v2.json"
        schema_file.write_text(json.dumps(SCHEMA_BODY), encoding='utf-8')
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].update_compliance_schema.return_value = {'internalVersion': 2}
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'update', '-n', 'cad-quality', '--schema-file', str(schema_file),
                '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'internalVersion': 2}
            mocks['api_client'].update_compliance_schema.assert_called_once_with(
                'cad-quality', {'schemaBody': SCHEMA_BODY})

    def test_update_requires_at_least_one_change(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            result = cli_runner.invoke(cli, ['compliance', 'schema', 'update', '-n', 'cad-quality'])
            assert result.exit_code != 0
            mocks['api_client'].update_compliance_schema.assert_not_called()

    def test_update_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].update_compliance_schema.side_effect = ComplianceSchemaNotFoundError(
                "Schema 'gone' not found")
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'update', '-n', 'gone', '--description', 'x'])
            assert result.exit_code != 0

    def test_delete_requires_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            result = cli_runner.invoke(cli, ['compliance', 'schema', 'delete', '-n', 'cad-quality'])
            assert result.exit_code != 0
            assert 'confirm' in result.output.lower()
            mocks['api_client'].delete_compliance_schema.assert_not_called()

    def test_delete_requires_confirm_in_json_mode(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'delete', '-n', 'cad-quality', '--json-output'])
            assert result.exit_code != 0
            assert json.loads(result.output)['error'] == 'Confirmation required'
            mocks['api_client'].delete_compliance_schema.assert_not_called()

    def test_delete_with_confirm(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].delete_compliance_schema.return_value = {'message': 'Success'}
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'delete', '-n', 'cad-quality', '--confirm'])
            assert result.exit_code == 0
            assert 'removed' in result.output
            mocks['api_client'].delete_compliance_schema.assert_called_once_with('cad-quality')

    def test_delete_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].delete_compliance_schema.return_value = {'message': 'Success'}
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'delete', '-n', 'cad-quality', '--confirm', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'message': 'Success'}

    def test_delete_of_a_bound_schema_is_rejected(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].delete_compliance_schema.side_effect = InvalidComplianceDataError(
                "Compliance schema 'cad-quality' deletion failed: schema is bound to 2 database(s)")
            result = cli_runner.invoke(cli, [
                'compliance', 'schema', 'delete', '-n', 'cad-quality', '--confirm'])
            assert result.exit_code != 0
            assert 'bound' in result.output


class TestComplianceBindingCommands:
    def test_bind_database(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].bind_compliance_schema.return_value = {
                'message': "Schema 'cad-quality' bound to database 'my-database'",
                'schemaName': 'cad-quality', 'databaseId': 'my-database', 'assetsPendingEvaluation': 3}
            result = cli_runner.invoke(cli, [
                'compliance', 'bind', '-n', 'cad-quality', '-d', 'my-database'])
            assert result.exit_code == 0
            assert 'assetsPendingEvaluation: 3' in result.output
            mocks['api_client'].bind_compliance_schema.assert_called_once_with(
                'my-database', 'cad-quality', asset_id=None, auto_eval=True)

    def test_bind_database_without_auto_eval(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].bind_compliance_schema.return_value = {'schemaName': 'cad-quality'}
            result = cli_runner.invoke(cli, [
                'compliance', 'bind', '-n', 'cad-quality', '-d', 'my-database', '--no-auto-eval'])
            assert result.exit_code == 0
            mocks['api_client'].bind_compliance_schema.assert_called_once_with(
                'my-database', 'cad-quality', asset_id=None, auto_eval=False)

    def test_bind_asset(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].bind_compliance_schema.return_value = {
                'schemaName': 'cad-quality', 'schemaSource': 'asset'}
            result = cli_runner.invoke(cli, [
                'compliance', 'bind', '-n', 'cad-quality', '-d', 'my-database', '-a', 'my-asset',
                '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['schemaSource'] == 'asset'
            mocks['api_client'].bind_compliance_schema.assert_called_once_with(
                'my-database', 'cad-quality', asset_id='my-asset', auto_eval=None)

    def test_bind_asset_refuses_the_database_only_flag(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            result = cli_runner.invoke(cli, [
                'compliance', 'bind', '-n', 'cad-quality', '-d', 'my-database', '-a', 'my-asset',
                '--no-auto-eval'])
            assert result.exit_code != 0
            mocks['api_client'].bind_compliance_schema.assert_not_called()

    def test_bind_schema_not_visible(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].bind_compliance_schema.side_effect = InvalidComplianceDataError(
                "Schema is not available for this database.")
            result = cli_runner.invoke(cli, [
                'compliance', 'bind', '-n', 'other-db-schema', '-d', 'my-database'])
            assert result.exit_code != 0
            assert 'not available' in result.output

    def test_unbind_database(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].unbind_compliance_schema.return_value = {
                'message': "Schema binding removed from database 'my-database'",
                'databaseId': 'my-database', 'previousSchema': 'cad-quality',
                'removedComplianceRecords': 3}
            result = cli_runner.invoke(cli, ['compliance', 'unbind', '-d', 'my-database'])
            assert result.exit_code == 0
            assert 'previousSchema: cad-quality' in result.output
            mocks['api_client'].unbind_compliance_schema.assert_called_once_with(
                'my-database', asset_id=None)

    def test_unbind_asset_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].unbind_compliance_schema.return_value = {
                'fallbackSchema': 'cad-quality', 'schemaSource': 'database'}
            result = cli_runner.invoke(cli, [
                'compliance', 'unbind', '-d', 'my-database', '-a', 'my-asset', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['fallbackSchema'] == 'cad-quality'
            mocks['api_client'].unbind_compliance_schema.assert_called_once_with(
                'my-database', asset_id='my-asset')

    def test_unbind_database_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].unbind_compliance_schema.side_effect = DatabaseNotFoundError(
                "Database 'nope' not found")
            result = cli_runner.invoke(cli, ['compliance', 'unbind', '-d', 'nope'])
            assert result.exit_code != 0
            assert 'Database Not Found' in result.output

    def test_bindings(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_bindings.return_value = {
                'databaseId': 'my-database', 'databaseSchema': 'cad-quality',
                'complianceAutoEval': True,
                'assetOverrides': [{'assetId': 'my-asset', 'schemaName': 'strict-cad',
                                    'complianceState': 'pending_evaluation'}],
                'assetOverrideCount': 1}
            result = cli_runner.invoke(cli, ['compliance', 'bindings', '-d', 'my-database'])
            assert result.exit_code == 0
            assert 'Database Schema: cad-quality' in result.output
            assert 'my-asset: strict-cad' in result.output

    def test_bindings_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_bindings.return_value = {'databaseId': 'my-database'}
            result = cli_runner.invoke(cli, [
                'compliance', 'bindings', '-d', 'my-database', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'databaseId': 'my-database'}

    def test_bindings_database_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_bindings.side_effect = DatabaseNotFoundError(
                "Database 'nope' not found")
            result = cli_runner.invoke(cli, ['compliance', 'bindings', '-d', 'nope'])
            assert result.exit_code != 0


class TestComplianceEvaluationCommands:
    def test_evaluate_shows_rule_results(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].evaluate_asset_compliance.return_value = {
                'message': 'Evaluation completed', 'evaluationId': 'ev-1', 'schemaName': 'cad-quality',
                'verdict': 'quarantined', 'complianceState': 'quarantined',
                'ruleResults': [{'ruleName': 'has-owner', 'enforcement': 'quarantine',
                                 'passed': False, 'message': 'owner missing'}],
                'pipelineRulesPending': 1}
            result = cli_runner.invoke(cli, ['compliance', 'evaluate', '-d', 'my-database', '-a', 'my-asset'])
            assert result.exit_code == 0
            assert 'Verdict: quarantined' in result.output
            assert '✗ has-owner [quarantine]: owner missing' in result.output
            assert 'Pipeline Rules Pending: 1' in result.output
            mocks['api_client'].evaluate_asset_compliance.assert_called_once_with(
                'my-database', 'my-asset', schema_name=None)

    def test_evaluate_forwards_the_schema_and_is_pure_in_json_mode(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].evaluate_asset_compliance.return_value = {
                'message': 'Evaluation triggered', 'evaluationId': 'ev-1', 'schemaName': 'cad-quality'}
            result = cli_runner.invoke(cli, [
                'compliance', 'evaluate', '-d', 'my-database', '-a', 'my-asset', '-n', 'cad-quality',
                '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['evaluationId'] == 'ev-1'
            mocks['api_client'].evaluate_asset_compliance.assert_called_once_with(
                'my-database', 'my-asset', schema_name='cad-quality')

    def test_evaluate_asset_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].evaluate_asset_compliance.side_effect = AssetNotFoundError(
                'Asset my-database:gone not found')
            result = cli_runner.invoke(cli, ['compliance', 'evaluate', '-d', 'my-database', '-a', 'gone'])
            assert result.exit_code != 0
            assert 'Asset Not Found' in result.output

    def test_evaluate_without_a_bound_schema(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].evaluate_asset_compliance.side_effect = InvalidComplianceDataError(
                'No schema specified and asset has no registered schema')
            result = cli_runner.invoke(cli, ['compliance', 'evaluate', '-d', 'my-database', '-a', 'my-asset'])
            assert result.exit_code != 0
            assert 'no registered schema' in result.output

    def test_sweep(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].sweep_compliance_schema.return_value = {
                'message': 'Sweep triggered for 2 assets', 'schemaName': 'cad-quality',
                'assetsTriggered': [{'databaseId': 'my-database', 'assetId': 'a1'},
                                    {'databaseId': 'my-database', 'assetId': 'a2'}]}
            result = cli_runner.invoke(cli, ['compliance', 'sweep', '-n', 'cad-quality'])
            assert result.exit_code == 0
            assert 'Assets Triggered: 2' in result.output
            assert 'my-database:a2' in result.output
            mocks['api_client'].sweep_compliance_schema.assert_called_once_with('cad-quality')

    def test_sweep_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].sweep_compliance_schema.return_value = {'assetsTriggered': []}
            result = cli_runner.invoke(cli, ['compliance', 'sweep', '-n', 'cad-quality', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'assetsTriggered': []}

    def test_sweep_schema_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].sweep_compliance_schema.side_effect = ComplianceSchemaNotFoundError(
                "Schema 'gone' not found")
            result = cli_runner.invoke(cli, ['compliance', 'sweep', '-n', 'gone'])
            assert result.exit_code != 0

    def test_state_for_an_asset(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_state.return_value = STATE_RECORD
            result = cli_runner.invoke(cli, ['compliance', 'state', '-d', 'my-database', '-a', 'my-asset'])
            assert result.exit_code == 0
            assert 'Compliance State: quarantined' in result.output
            assert 'Quarantine Reason: wall-thickness below minimum' in result.output
            mocks['api_client'].get_compliance_state.assert_called_once_with('my-database', 'my-asset')
            mocks['api_client'].get_database_compliance_state.assert_not_called()

    def test_state_for_a_database(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_database_compliance_state.return_value = {
                'databaseId': 'my-database', 'totalAssets': 2,
                'summary': {'compliant': 1, 'non_compliant': 0, 'pending_evaluation': 0,
                            'quarantined': 1, 'unknown': 0},
                'assets': [dict(STATE_RECORD, assetName='Bracket'),
                           {'assetId': 'a2', 'complianceState': 'compliant', 'schemaName': 'cad-quality'}]}
            result = cli_runner.invoke(cli, ['compliance', 'state', '-d', 'my-database'])
            assert result.exit_code == 0
            assert 'Tracked Assets: 2' in result.output
            assert 'quarantined=1' in result.output
            assert 'my-asset (Bracket): quarantined [cad-quality]' in result.output
            mocks['api_client'].get_database_compliance_state.assert_called_once_with('my-database')
            mocks['api_client'].get_compliance_state.assert_not_called()

    def test_state_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_state.return_value = STATE_RECORD
            result = cli_runner.invoke(cli, [
                'compliance', 'state', '-d', 'my-database', '-a', 'my-asset', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == STATE_RECORD

    def test_state_database_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_database_compliance_state.side_effect = DatabaseNotFoundError(
                "Database 'nope' not found")
            result = cli_runner.invoke(cli, ['compliance', 'state', '-d', 'nope'])
            assert result.exit_code != 0

    def test_evaluations(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_compliance_evaluations.return_value = {'evaluations': [
                {'evaluationId': 'ev-2', 'schemaName': 'cad-quality', 'verdict': 'compliant',
                 'evaluatedAt': '2026-09-02T00:00:00+00:00', 'executionId': 'exec-9'},
                {'evaluationId': 'ev-1', 'schemaName': 'cad-quality', 'status': 'pending'}]}
            result = cli_runner.invoke(cli, ['compliance', 'evaluations', '-d', 'my-database', '-a', 'my-asset'])
            assert result.exit_code == 0
            assert 'Found 2 evaluation(s)' in result.output
            assert 'Execution ID: exec-9' in result.output
            assert 'Next token' not in result.output
            mocks['api_client'].list_compliance_evaluations.assert_called_once_with(
                'my-database', 'my-asset', max_items=None, starting_token=None)

    def test_evaluations_forwards_the_paging_options_and_shows_the_token(self, cli_runner,
                                                                          generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_compliance_evaluations.return_value = {
                'evaluations': [{'evaluationId': 'ev-1'}], 'NextToken': 'tok-2'}
            result = cli_runner.invoke(cli, [
                'compliance', 'evaluations', '-d', 'my-database', '-a', 'my-asset',
                '--max-items', '1', '--starting-token', 'tok-1'])
            assert result.exit_code == 0
            assert 'Next token: tok-2' in result.output
            mocks['api_client'].list_compliance_evaluations.assert_called_once_with(
                'my-database', 'my-asset', max_items=1, starting_token='tok-1')

    def test_evaluations_rejects_a_page_size_over_the_cap(self, cli_runner, generic_command_mocks):
        from vamscli.constants import MAX_COMPLIANCE_EVALUATIONS_PAGE_SIZE
        with generic_command_mocks('compliance') as mocks:
            result = cli_runner.invoke(cli, [
                'compliance', 'evaluations', '-d', 'my-database', '-a', 'my-asset',
                '--max-items', str(MAX_COMPLIANCE_EVALUATIONS_PAGE_SIZE + 1)])
            assert result.exit_code == 2
            mocks['api_client'].list_compliance_evaluations.assert_not_called()

    def test_evaluations_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_compliance_evaluations.return_value = {'evaluations': []}
            result = cli_runner.invoke(cli, [
                'compliance', 'evaluations', '-d', 'my-database', '-a', 'my-asset', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'evaluations': []}

    def test_evaluations_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_compliance_evaluations.side_effect = InvalidComplianceDataError('rejected')
            result = cli_runner.invoke(cli, ['compliance', 'evaluations', '-d', 'my-database', '-a', 'my-asset'])
            assert result.exit_code != 0


class TestComplianceQuarantineCommands:
    def test_list(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_quarantined_assets.return_value = {
                'quarantinedAssets': [dict(STATE_RECORD, assetName='Bracket')]}
            result = cli_runner.invoke(cli, ['compliance', 'quarantine', 'list'])
            assert result.exit_code == 0
            assert 'Found 1 quarantined asset(s)' in result.output
            assert 'Asset Name: Bracket' in result.output

    def test_list_empty(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_quarantined_assets.return_value = {'quarantinedAssets': []}
            result = cli_runner.invoke(cli, ['compliance', 'quarantine', 'list'])
            assert result.exit_code == 0
            assert 'No quarantined assets' in result.output

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_quarantined_assets.return_value = {'quarantinedAssets': [STATE_RECORD]}
            result = cli_runner.invoke(cli, ['compliance', 'quarantine', 'list', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'quarantinedAssets': [STATE_RECORD]}

    def test_list_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_quarantined_assets.side_effect = InvalidComplianceDataError('rejected')
            result = cli_runner.invoke(cli, ['compliance', 'quarantine', 'list'])
            assert result.exit_code != 0

    def test_release(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].release_quarantine.return_value = {
                'message': 'Asset my-database:my-asset released from quarantine'}
            result = cli_runner.invoke(cli, [
                'compliance', 'quarantine', 'release', '-d', 'my-database', '-a', 'my-asset',
                '--reason', 'Source corrected'])
            assert result.exit_code == 0
            assert 'released from quarantine' in result.output
            mocks['api_client'].release_quarantine.assert_called_once_with(
                'my-database', 'my-asset', reason='Source corrected')

    def test_release_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].release_quarantine.return_value = {'message': 'released'}
            result = cli_runner.invoke(cli, [
                'compliance', 'quarantine', 'release', '-d', 'my-database', '-a', 'my-asset', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'message': 'released'}
            mocks['api_client'].release_quarantine.assert_called_once_with(
                'my-database', 'my-asset', reason=None)

    def test_release_of_an_asset_not_quarantined(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].release_quarantine.side_effect = InvalidComplianceDataError(
                'Asset is not quarantined (state: compliant)')
            result = cli_runner.invoke(cli, [
                'compliance', 'quarantine', 'release', '-d', 'my-database', '-a', 'my-asset'])
            assert result.exit_code != 0
            assert 'not quarantined' in result.output

    def test_exception(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].grant_quarantine_exception.return_value = {
                'message': 'Exception granted for my-database:my-asset', 'reason': 'Legacy part',
                'grantedBy': 'user-1'}
            result = cli_runner.invoke(cli, [
                'compliance', 'quarantine', 'exception', '-d', 'my-database', '-a', 'my-asset',
                '--reason', 'Legacy part'])
            assert result.exit_code == 0
            assert 'Granted By: user-1' in result.output
            mocks['api_client'].grant_quarantine_exception.assert_called_once_with(
                'my-database', 'my-asset', 'Legacy part')

    def test_exception_requires_a_reason(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            result = cli_runner.invoke(cli, [
                'compliance', 'quarantine', 'exception', '-d', 'my-database', '-a', 'my-asset'])
            assert result.exit_code == 2
            mocks['api_client'].grant_quarantine_exception.assert_not_called()

    def test_exception_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].grant_quarantine_exception.return_value = {'grantedBy': 'user-1'}
            result = cli_runner.invoke(cli, [
                'compliance', 'quarantine', 'exception', '-d', 'my-database', '-a', 'my-asset',
                '--reason', 'Legacy part', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'grantedBy': 'user-1'}

    def test_exception_asset_not_tracked(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].grant_quarantine_exception.side_effect = AssetNotFoundError(
                'Asset not found in compliance tracking')
            result = cli_runner.invoke(cli, [
                'compliance', 'quarantine', 'exception', '-d', 'my-database', '-a', 'gone',
                '--reason', 'x'])
            assert result.exit_code != 0


class TestComplianceCascadeCommands:
    def test_list(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_compliance_cascades.return_value = {'cascades': [CASCADE_RECORD]}
            result = cli_runner.invoke(cli, ['compliance', 'cascade', 'list'])
            assert result.exit_code == 0
            assert 'Cascade ID: casc-1' in result.output
            assert 'Triggered By: my-database:my-asset' in result.output

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_compliance_cascades.return_value = {'cascades': []}
            result = cli_runner.invoke(cli, ['compliance', 'cascade', 'list', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'cascades': []}

    def test_list_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].list_compliance_cascades.side_effect = InvalidComplianceDataError('rejected')
            result = cli_runner.invoke(cli, ['compliance', 'cascade', 'list'])
            assert result.exit_code != 0

    def test_get(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_cascade.return_value = CASCADE_RECORD
            result = cli_runner.invoke(cli, ['compliance', 'cascade', 'get', '-c', 'casc-1'])
            assert result.exit_code == 0
            assert 'State: pending_approval' in result.output
            mocks['api_client'].get_compliance_cascade.assert_called_once_with('casc-1')

    def test_get_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_cascade.return_value = CASCADE_RECORD
            result = cli_runner.invoke(cli, ['compliance', 'cascade', 'get', '-c', 'casc-1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == CASCADE_RECORD

    def test_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_compliance_cascade.side_effect = ComplianceCascadeNotFoundError(
                "Cascade 'gone' not found")
            result = cli_runner.invoke(cli, ['compliance', 'cascade', 'get', '-c', 'gone'])
            assert result.exit_code != 0
            assert 'Cascade Not Found' in result.output

    def test_create_waits_for_approval_by_default(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].create_compliance_cascade.return_value = {
                'message': 'Cascade created', 'cascadeId': 'casc-1', 'state': 'pending_approval'}
            result = cli_runner.invoke(cli, [
                'compliance', 'cascade', 'create', '-d', 'my-database', '-a', 'my-asset'])
            assert result.exit_code == 0
            assert 'State: pending_approval' in result.output
            mocks['api_client'].create_compliance_cascade.assert_called_once_with(
                'my-database', 'my-asset', reason=None, require_approval=True)

    def test_create_without_approval_and_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].create_compliance_cascade.return_value = {
                'cascadeId': 'casc-1', 'state': 'executing'}
            result = cli_runner.invoke(cli, [
                'compliance', 'cascade', 'create', '-d', 'my-database', '-a', 'my-asset',
                '--reason', 'Geometry revised', '--no-approval', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['state'] == 'executing'
            mocks['api_client'].create_compliance_cascade.assert_called_once_with(
                'my-database', 'my-asset', reason='Geometry revised', require_approval=False)

    def test_create_rejected(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].create_compliance_cascade.side_effect = InvalidComplianceDataError(
                'databaseId and assetId are required')
            result = cli_runner.invoke(cli, [
                'compliance', 'cascade', 'create', '-d', 'my-database', '-a', 'my-asset'])
            assert result.exit_code != 0

    def test_approve(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].approve_compliance_cascade.return_value = {
                'message': 'Cascade approved and executed', 'cascadeId': 'casc-1',
                'result': {'nodesExecuted': 2}}
            result = cli_runner.invoke(cli, [
                'compliance', 'cascade', 'approve', '-c', 'casc-1', '--reason', 'Reviewed'])
            assert result.exit_code == 0
            assert 'nodesExecuted' in result.output
            mocks['api_client'].approve_compliance_cascade.assert_called_once_with('casc-1', reason='Reviewed')

    def test_approve_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].approve_compliance_cascade.return_value = {'cascadeId': 'casc-1'}
            result = cli_runner.invoke(cli, ['compliance', 'cascade', 'approve', '-c', 'casc-1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'cascadeId': 'casc-1'}
            mocks['api_client'].approve_compliance_cascade.assert_called_once_with('casc-1', reason=None)

    def test_approve_not_pending(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].approve_compliance_cascade.side_effect = ComplianceCascadeNotFoundError(
                'Cascade not found or not in pending_approval state')
            result = cli_runner.invoke(cli, ['compliance', 'cascade', 'approve', '-c', 'casc-1'])
            assert result.exit_code != 0
            assert 'pending_approval' in result.output

    def test_reject(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].reject_compliance_cascade.return_value = {
                'message': 'Cascade rejected', 'cascadeId': 'casc-1'}
            result = cli_runner.invoke(cli, ['compliance', 'cascade', 'reject', '-c', 'casc-1'])
            assert result.exit_code == 0
            assert 'Cascade ID: casc-1' in result.output
            mocks['api_client'].reject_compliance_cascade.assert_called_once_with('casc-1', reason=None)

    def test_reject_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].reject_compliance_cascade.return_value = {'cascadeId': 'casc-1'}
            result = cli_runner.invoke(cli, [
                'compliance', 'cascade', 'reject', '-c', 'casc-1', '--reason', 'Already done', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'cascadeId': 'casc-1'}
            mocks['api_client'].reject_compliance_cascade.assert_called_once_with('casc-1', reason='Already done')

    def test_reject_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].reject_compliance_cascade.side_effect = ComplianceCascadeNotFoundError('gone')
            result = cli_runner.invoke(cli, ['compliance', 'cascade', 'reject', '-c', 'gone'])
            assert result.exit_code != 0


class TestComplianceAuditCommand:
    def test_global_listing(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].query_compliance_audit.return_value = {'entries': [AUDIT_ENTRY]}
            result = cli_runner.invoke(cli, ['compliance', 'audit'])
            assert result.exit_code == 0
            assert 'quarantine_released' in result.output
            assert 'Actor: user-1' in result.output
            mocks['api_client'].query_compliance_audit.assert_called_once_with(
                event_type=None, start_date=None, end_date=None, limit=None)
            mocks['api_client'].get_asset_compliance_audit.assert_not_called()

    def test_global_listing_forwards_every_filter(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].query_compliance_audit.return_value = {'entries': []}
            result = cli_runner.invoke(cli, [
                'compliance', 'audit', '--event-type', 'compliance_check',
                '--start-date', '2026-09-01T00:00:00Z', '--end-date', '2026-09-30T00:00:00Z',
                '--limit', '100'])
            assert result.exit_code == 0
            mocks['api_client'].query_compliance_audit.assert_called_once_with(
                event_type='compliance_check', start_date='2026-09-01T00:00:00Z',
                end_date='2026-09-30T00:00:00Z', limit=100)

    def test_asset_history(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].get_asset_compliance_audit.return_value = {'entries': [AUDIT_ENTRY]}
            result = cli_runner.invoke(cli, [
                'compliance', 'audit', '-d', 'my-database', '-a', 'my-asset', '--limit', '5'])
            assert result.exit_code == 0
            mocks['api_client'].get_asset_compliance_audit.assert_called_once_with(
                'my-database', 'my-asset', start_date=None, end_date=None, limit=5)
            mocks['api_client'].query_compliance_audit.assert_not_called()

    def test_asset_history_needs_both_ids(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            result = cli_runner.invoke(cli, ['compliance', 'audit', '-d', 'my-database'])
            assert result.exit_code != 0
            mocks['api_client'].query_compliance_audit.assert_not_called()
            mocks['api_client'].get_asset_compliance_audit.assert_not_called()

    def test_asset_history_refuses_the_event_type_filter(self, cli_runner, generic_command_mocks):
        # The per-asset route has no eventType filter; sending one would look honoured and be ignored.
        with generic_command_mocks('compliance') as mocks:
            result = cli_runner.invoke(cli, [
                'compliance', 'audit', '-d', 'my-database', '-a', 'my-asset', '--event-type', 'compliance_check'])
            assert result.exit_code != 0
            mocks['api_client'].get_asset_compliance_audit.assert_not_called()

    def test_a_full_page_is_flagged_as_possibly_incomplete(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].query_compliance_audit.return_value = {
                'entries': [dict(AUDIT_ENTRY, entryId=f'e-{i}') for i in range(3)]}
            result = cli_runner.invoke(cli, ['compliance', 'audit', '--limit', '3'])
            assert result.exit_code == 0
            assert 'limit in force (3)' in result.output

    def test_a_short_page_is_not_flagged(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].query_compliance_audit.return_value = {'entries': [AUDIT_ENTRY]}
            result = cli_runner.invoke(cli, ['compliance', 'audit', '--limit', '3'])
            assert result.exit_code == 0
            assert 'limit in force' not in result.output

    def test_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].query_compliance_audit.return_value = {'entries': [AUDIT_ENTRY]}
            result = cli_runner.invoke(cli, ['compliance', 'audit', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output) == {'entries': [AUDIT_ENTRY]}

    def test_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('compliance') as mocks:
            mocks['api_client'].query_compliance_audit.side_effect = InvalidComplianceDataError('rejected')
            result = cli_runner.invoke(cli, ['compliance', 'audit'])
            assert result.exit_code != 0


class TestSchemaBodyLoading:
    """--schema-file takes a path or an inline object, and rejects anything that is not an object."""

    def test_an_inline_object_is_accepted(self):
        from vamscli.commands.compliance import load_schema_body
        assert load_schema_body(json.dumps(SCHEMA_BODY)) == SCHEMA_BODY

    def test_a_file_is_read(self, tmp_path):
        from vamscli.commands.compliance import load_schema_body
        path = tmp_path / "schema.json"
        path.write_text(json.dumps(SCHEMA_BODY), encoding='utf-8')
        assert load_schema_body(str(path)) == SCHEMA_BODY

    def test_a_file_with_invalid_json_is_rejected(self, tmp_path):
        import click
        from vamscli.commands.compliance import load_schema_body
        path = tmp_path / "schema.json"
        path.write_text("{not json", encoding='utf-8')
        with pytest.raises(click.BadParameter):
            load_schema_body(str(path))

    def test_a_scalar_is_rejected(self):
        import click
        from vamscli.commands.compliance import load_schema_body
        with pytest.raises(click.BadParameter):
            load_schema_body('"just a string"')
