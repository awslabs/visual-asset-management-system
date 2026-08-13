"""Tests for pipeline commands (CRUD + templates + tag schema)."""

import json
import pytest

from vamscli.main import cli
from vamscli.utils.exceptions import (
    PipelineNotFoundError, PipelineAlreadyExistsError, InvalidPipelineDataError,
    PipelineTemplateNotFoundError, PipelineTemplateAlreadyExistsError, InvalidPipelineTemplateDataError,
)


class TestPipelineList:
    def test_list_all_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].list_pipelines.return_value = {
                'message': {'Items': [{'pipelineId': 'p1', 'pipelineName': 'Pipe One',
                                       'executionConfig': {'executionType': 'Lambda'}}]}
            }
            result = cli_runner.invoke(cli, ['pipeline', 'list'])
            assert result.exit_code == 0
            assert 'p1' in result.output

    def test_list_by_database(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].list_pipelines.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['pipeline', 'list', '-d', 'my-db'])
            assert result.exit_code == 0
            assert mocks['api_client'].list_pipelines.call_args.kwargs['database_id'] == 'my-db'

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].list_pipelines.return_value = {'message': {'Items': [{'pipelineId': 'p1'}]}}
            result = cli_runner.invoke(cli, ['pipeline', 'list', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['Items'][0]['pipelineId'] == 'p1'

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('pipeline'):
            result = cli_runner.invoke(cli, ['pipeline', 'list'])
            assert result.exit_code != 0


class TestPipelineGet:
    def test_get_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].get_pipeline.return_value = {
                'message': {'pipelineId': 'p1', 'pipelineName': 'Pipe One',
                            'executionConfig': {'executionType': 'Lambda'},
                            'templates': [{'templateId': 't1'}]}}
            result = cli_runner.invoke(cli, ['pipeline', 'get', '-d', 'my-db', '-p', 'p1'])
            assert result.exit_code == 0
            assert 'p1' in result.output

    def test_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].get_pipeline.side_effect = PipelineNotFoundError("Not found")
            result = cli_runner.invoke(cli, ['pipeline', 'get', '-d', 'my-db', '-p', 'bad'])
            assert result.exit_code != 0


class TestPipelineCreate:
    def test_create_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].create_pipeline.return_value = {
                'message': {'pipelineId': 'gen', 'pipelineName': 'New Pipe',
                            'executionConfig': {'executionType': 'Lambda'}}}
            result = cli_runner.invoke(cli, [
                'pipeline', 'create', '-d', 'my-db', '-n', 'New Pipe',
                '--execution-config', '{"executionType": "Lambda"}'])
            assert result.exit_code == 0
            body = mocks['api_client'].create_pipeline.call_args.args[1]
            assert body['pipelineName'] == 'New Pipe'
            assert body['executionConfig']['executionType'] == 'Lambda'

    def test_create_invalid_json_execution_config(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline'):
            result = cli_runner.invoke(cli, [
                'pipeline', 'create', '-d', 'my-db', '-n', 'X', '--execution-config', 'not-json'])
            assert result.exit_code != 0
            assert 'Invalid JSON' in result.output

    def test_create_already_exists(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].create_pipeline.side_effect = PipelineAlreadyExistsError("exists")
            result = cli_runner.invoke(cli, [
                'pipeline', 'create', '-d', 'my-db', '-n', 'X', '-p', 'dup'])
            assert result.exit_code != 0

    def test_create_invalid_data(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].create_pipeline.side_effect = InvalidPipelineDataError("bad")
            result = cli_runner.invoke(cli, ['pipeline', 'create', '-d', 'my-db', '-n', 'X'])
            assert result.exit_code != 0

    def test_create_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('pipeline'):
            result = cli_runner.invoke(cli, ['pipeline', 'create', '-d', 'my-db', '-n', 'X'])
            assert result.exit_code != 0

    def test_create_success_with_warnings(self, cli_runner, generic_command_mocks):
        warning = ("pipeline 'New Pipe' requires a template and is part of auto-triggered "
                   "workflow 'db1:wf1' (trigger 'fileUpload'), but that trigger has not chosen "
                   "a default template for it.")
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].create_pipeline.return_value = {
                'message': {'pipelineId': 'gen', 'pipelineName': 'New Pipe',
                            'executionConfig': {'executionType': 'Lambda'}},
                'warnings': [warning]}
            result = cli_runner.invoke(cli, [
                'pipeline', 'create', '-d', 'my-db', '-n', 'New Pipe',
                '--execution-config', '{"executionType": "Lambda"}'])
            assert result.exit_code == 0
            assert 'requires a template' in result.output

    def test_create_warnings_carried_into_json(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].create_pipeline.return_value = {
                'message': {'pipelineId': 'gen'},
                'warnings': ['some warning']}
            result = cli_runner.invoke(cli, [
                'pipeline', 'create', '-d', 'my-db', '-n', 'New Pipe', '--json-output'])
            assert result.exit_code == 0
            # JSON mode emits the unwrapped message envelope with the warnings array carried along.
            data = json.loads(result.output)
            assert data['pipelineId'] == 'gen'
            assert data['warnings'] == ['some warning']


class TestPipelineUpdate:
    def test_update_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].update_pipeline.return_value = {
                'message': {'pipelineId': 'p1', 'pipelineName': 'Renamed',
                            'executionConfig': {'executionType': 'Lambda'}}}
            result = cli_runner.invoke(cli, [
                'pipeline', 'update', '-d', 'my-db', '-p', 'p1', '--description', 'updated'])
            assert result.exit_code == 0
            assert mocks['api_client'].update_pipeline.call_args.args[2] == {'description': 'updated'}

    def test_update_disable(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].update_pipeline.return_value = {'message': {'pipelineId': 'p1'}}
            result = cli_runner.invoke(cli, ['pipeline', 'update', '-d', 'my-db', '-p', 'p1', '--disable'])
            assert result.exit_code == 0
            assert mocks['api_client'].update_pipeline.call_args.args[2]['enabled'] is False

    def test_update_requires_a_field(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline'):
            result = cli_runner.invoke(cli, ['pipeline', 'update', '-d', 'my-db', '-p', 'p1'])
            assert result.exit_code != 0
            assert 'at least one field' in result.output.lower()

    def test_update_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].update_pipeline.side_effect = PipelineNotFoundError("nope")
            result = cli_runner.invoke(cli, [
                'pipeline', 'update', '-d', 'my-db', '-p', 'p1', '--description', 'x'])
            assert result.exit_code != 0


class TestPipelineDelete:
    def test_delete_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].delete_pipeline.return_value = {'message': 'Pipeline archived'}
            result = cli_runner.invoke(cli, ['pipeline', 'delete', '-d', 'my-db', '-p', 'p1'])
            assert result.exit_code == 0

    def test_delete_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].delete_pipeline.side_effect = PipelineNotFoundError("nope")
            result = cli_runner.invoke(cli, ['pipeline', 'delete', '-d', 'my-db', '-p', 'p1'])
            assert result.exit_code != 0


class TestPipelineUnarchive:
    def test_unarchive_clears_archived_and_reenables(self, cli_runner, generic_command_mocks):
        """Archiving sets enabled=False too, so unarchive must re-enable or the pipeline comes back
        unusable. Only these two flags are sent — any other field would overwrite stored values."""
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].update_pipeline.return_value = {
                'message': {'pipelineId': 'p1', 'pipelineName': 'Pipe One', 'archived': False}}
            result = cli_runner.invoke(cli, ['pipeline', 'unarchive', '-d', 'my-db', '-p', 'p1'])
            assert result.exit_code == 0
            mocks['api_client'].update_pipeline.assert_called_once_with(
                'my-db', 'p1', {'archived': False, 'enabled': True})

    def test_unarchive_keep_disabled(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].update_pipeline.return_value = {
                'message': {'pipelineId': 'p1', 'archived': False}}
            result = cli_runner.invoke(
                cli, ['pipeline', 'unarchive', '-d', 'my-db', '-p', 'p1', '--keep-disabled'])
            assert result.exit_code == 0
            mocks['api_client'].update_pipeline.assert_called_once_with(
                'my-db', 'p1', {'archived': False})

    def test_unarchive_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].update_pipeline.return_value = {
                'message': {'pipelineId': 'p1', 'archived': False}}
            result = cli_runner.invoke(
                cli, ['pipeline', 'unarchive', '-d', 'my-db', '-p', 'p1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['archived'] is False

    def test_unarchive_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].update_pipeline.side_effect = PipelineNotFoundError("nope")
            result = cli_runner.invoke(cli, ['pipeline', 'unarchive', '-d', 'my-db', '-p', 'p1'])
            assert result.exit_code != 0

    def test_unarchive_invalid_data(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].update_pipeline.side_effect = InvalidPipelineDataError("bad")
            result = cli_runner.invoke(cli, ['pipeline', 'unarchive', '-d', 'my-db', '-p', 'p1'])
            assert result.exit_code != 0


class TestPipelineTemplate:
    def test_template_list_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].list_pipeline_templates.return_value = {
                'message': {'Items': [{'templateId': 't1', 'templateName': 'T1', 'configFormat': 'json'}]}}
            result = cli_runner.invoke(cli, ['pipeline', 'template', 'list', '-d', 'my-db', '-p', 'p1'])
            assert result.exit_code == 0
            assert 't1' in result.output

    def test_template_get_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].get_pipeline_template.return_value = {
                'message': {'templateId': 't1', 'templateName': 'T1', 'configFormat': 'json'}}
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'get', '-d', 'my-db', '-p', 'p1', '-t', 't1'])
            assert result.exit_code == 0

    def test_template_create_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].create_pipeline_template.return_value = {
                'message': {'templateId': 'gen', 'templateName': 'New T', 'configFormat': 'json'}}
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'create', '-d', 'my-db', '-p', 'p1', '-n', 'New T',
                '--config-body', '{"foo": "bar"}'])
            assert result.exit_code == 0
            body = mocks['api_client'].create_pipeline_template.call_args.args[2]
            assert body['configBody'] == '{"foo": "bar"}'

    def test_template_create_already_exists(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].create_pipeline_template.side_effect = PipelineTemplateAlreadyExistsError("dup")
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'create', '-d', 'my-db', '-p', 'p1', '-n', 'X', '-t', 'dup'])
            assert result.exit_code != 0

    def test_template_create_trigger_template_error(self, cli_runner, generic_command_mocks):
        # A 400 with structured triggerTemplateErrors is flattened by the API client into an
        # InvalidPipelineTemplateDataError; the command must surface the message to the user.
        msg = ("this template is a trigger default for workflow(s) [db1:wf1] and has required "
               "tag(s) with no default value: q. Give each a default value or make it optional.")
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].create_pipeline_template.side_effect = InvalidPipelineTemplateDataError(msg)
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'create', '-d', 'my-db', '-p', 'p1', '-n', 'X',
                '--config-body', '{"foo": "bar"}'])
            assert result.exit_code != 0
            assert 'required tag' in result.output

    def test_template_update_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].update_pipeline_template.return_value = {
                'message': {'templateId': 't1', 'templateName': 'Renamed', 'configFormat': 'json'}}
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'update', '-d', 'my-db', '-p', 'p1', '-t', 't1', '-n', 'Renamed'])
            assert result.exit_code == 0

    def test_template_update_requires_field(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline'):
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'update', '-d', 'my-db', '-p', 'p1', '-t', 't1'])
            assert result.exit_code != 0

    def test_template_delete_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].delete_pipeline_template.return_value = {'message': 'Template deleted'}
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'delete', '-d', 'my-db', '-p', 'p1', '-t', 't1', '--yes'])
            assert result.exit_code == 0

    def test_template_delete_requires_confirmation(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'delete', '-d', 'my-db', '-p', 'p1', '-t', 't1'], input='n\n')
            assert result.exit_code != 0
            mocks['api_client'].delete_pipeline_template.assert_not_called()

    def test_template_create_sends_description(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].create_pipeline_template.return_value = {'message': {'templateId': 't1'}}
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'create', '-d', 'my-db', '-p', 'p1', '-n', 'T1',
                '--description', 'OBJ output'])
            assert result.exit_code == 0
            body = mocks['api_client'].create_pipeline_template.call_args[0][2]
            assert body['description'] == 'OBJ output'

    def test_template_list_drains_pages(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].list_pipeline_templates.return_value = {
                'message': {'Items': [{'templateId': 't1'}, {'templateId': 't2'}]}}
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'list', '-d', 'my-db', '-p', 'p1'])
            assert result.exit_code == 0
            assert 't2' in result.output

    def test_template_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].get_pipeline_template.side_effect = PipelineTemplateNotFoundError("nope")
            result = cli_runner.invoke(cli, [
                'pipeline', 'template', 'get', '-d', 'my-db', '-p', 'p1', '-t', 'bad'])
            assert result.exit_code != 0


class TestPipelineTagSchema:
    def test_tag_schema_get_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].get_pipeline_template_tag_schema.return_value = {
                'message': {'fields': [{'tagKey': 'quality', 'type': 'enum'}]}}
            result = cli_runner.invoke(cli, [
                'pipeline', 'tag-schema', 'get', '-d', 'my-db', '-p', 'p1', '-t', 't1'])
            assert result.exit_code == 0
            assert 'quality' in result.output

    def test_tag_schema_set_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].set_pipeline_template_tag_schema.return_value = {'message': {'fields': []}}
            result = cli_runner.invoke(cli, [
                'pipeline', 'tag-schema', 'set', '-d', 'my-db', '-p', 'p1', '-t', 't1',
                '--fields', '[{"tagKey": "q", "type": "string"}]'])
            assert result.exit_code == 0
            fields = mocks['api_client'].set_pipeline_template_tag_schema.call_args.args[3]
            assert fields == [{'tagKey': 'q', 'type': 'string'}]

    def test_tag_schema_set_explicit_empty_list_clears(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].set_pipeline_template_tag_schema.return_value = {'message': {'fields': []}}
            result = cli_runner.invoke(cli, [
                'pipeline', 'tag-schema', 'set', '-d', 'my-db', '-p', 'p1', '-t', 't1',
                '--fields', '[]'])
            assert result.exit_code == 0
            assert mocks['api_client'].set_pipeline_template_tag_schema.call_args.args[3] == []

    def test_tag_schema_set_rejects_non_list(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline'):
            result = cli_runner.invoke(cli, [
                'pipeline', 'tag-schema', 'set', '-d', 'my-db', '-p', 'p1', '-t', 't1',
                '--fields', '{"not": "a list"}'])
            assert result.exit_code != 0

    def test_tag_schema_set_invalid_data(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('pipeline') as mocks:
            mocks['api_client'].set_pipeline_template_tag_schema.side_effect = \
                InvalidPipelineTemplateDataError("bad tag")
            result = cli_runner.invoke(cli, [
                'pipeline', 'tag-schema', 'set', '-d', 'my-db', '-p', 'p1', '-t', 't1',
                '--fields', '[{"tagKey": "q"}]'])
            assert result.exit_code != 0


class TestPipelineTemplateListPagination:
    """The templates list handler returns one page plus a NextToken; the client drains it."""

    def _client(self):
        from unittest.mock import MagicMock
        from vamscli.utils.api_client import APIClient
        profile_manager = MagicMock()
        profile_manager.is_override_token.return_value = False
        profile_manager.load_auth_profile.return_value = {}
        return APIClient("https://example.com/api", profile_manager=profile_manager)

    def _response(self, body):
        from unittest.mock import MagicMock
        resp = MagicMock()
        resp.status_code = 200
        resp.content = b"{}"
        resp.headers = {}
        resp.json.return_value = body
        return resp

    def test_all_pages_are_drained(self):
        from unittest.mock import patch
        client = self._client()
        pages = [
            self._response({'message': {'Items': [{'templateId': 't1'}], 'NextToken': 'tok'}}),
            self._response({'message': {'Items': [{'templateId': 't2'}]}}),
        ]
        with patch.object(client, 'get', side_effect=pages) as mock_get:
            result = client.list_pipeline_templates('db1', 'p1')

        assert [t['templateId'] for t in result['message']['Items']] == ['t1', 't2']
        assert mock_get.call_args_list[1].kwargs['params'] == {'startingToken': 'tok'}
