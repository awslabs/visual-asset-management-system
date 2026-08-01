"""Tests for workflow commands (CRUD + triggers + asset-less execute + per-asset execution list)."""

import json
import pytest

from vamscli.main import cli
from vamscli.utils.exceptions import (
    WorkflowNotFoundError, WorkflowExecutionError, WorkflowAlreadyRunningError,
    InvalidWorkflowDataError, WorkflowTriggerNotFoundError, InvalidWorkflowTriggerDataError,
    AssetNotFoundError, DatabaseNotFoundError,
)


class TestWorkflowList:
    def test_list_all_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.return_value = {
                'message': {'Items': [{'workflowId': 'wf1', 'workflowName': 'WF One',
                                       'specifiedPipelines': [{'pipelineDatabaseId': 'db1', 'pipelineId': 'p1'}]}]}}
            result = cli_runner.invoke(cli, ['workflow', 'list'])
            assert result.exit_code == 0
            assert 'wf1' in result.output

    def test_list_by_database(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['workflow', 'list', '-d', 'my-db'])
            assert result.exit_code == 0
            assert mocks['api_client'].list_workflows.call_args.kwargs['database_id'] == 'my-db'

    def test_list_shows_execution_count_when_present(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.return_value = {
                'message': {'Items': [{'workflowId': 'wf1', 'workflowName': 'WF One',
                                       'executionCount': 42}]}}
            result = cli_runner.invoke(cli, ['workflow', 'list'])
            assert result.exit_code == 0
            assert 'Executions: 42' in result.output

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.return_value = {'message': {'Items': [{'workflowId': 'wf1'}]}}
            result = cli_runner.invoke(cli, ['workflow', 'list', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['Items'][0]['workflowId'] == 'wf1'

    def test_list_auto_paginate(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.side_effect = [
                {'message': {'Items': [{'workflowId': 'wf1'}], 'NextToken': 'tok'}},
                {'message': {'Items': [{'workflowId': 'wf2'}]}},
            ]
            result = cli_runner.invoke(cli, ['workflow', 'list', '--auto-paginate'])
            assert result.exit_code == 0
            assert mocks['api_client'].list_workflows.call_count == 2

    def test_list_database_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.side_effect = DatabaseNotFoundError("Not found")
            result = cli_runner.invoke(cli, ['workflow', 'list', '-d', 'bad'])
            assert result.exit_code != 0

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('workflow'):
            result = cli_runner.invoke(cli, ['workflow', 'list'])
            assert result.exit_code != 0


class TestWorkflowGet:
    def test_get_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].get_workflow.return_value = {
                'message': {'workflowId': 'wf1', 'workflowName': 'WF One',
                            'triggers': [{'triggerType': 'fileUpload', 'enabled': True}]}}
            result = cli_runner.invoke(cli, ['workflow', 'get', '-d', 'my-db', '-w', 'wf1'])
            assert result.exit_code == 0
            assert 'wf1' in result.output

    def test_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].get_workflow.side_effect = WorkflowNotFoundError("nope")
            result = cli_runner.invoke(cli, ['workflow', 'get', '-d', 'my-db', '-w', 'bad'])
            assert result.exit_code != 0


class TestWorkflowCreate:
    def test_create_with_pipeline_refs(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].create_workflow.return_value = {
                'message': {'workflowId': 'gen', 'workflowName': 'New WF'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'create', '-d', 'my-db', '-n', 'New WF',
                '--pipeline', 'global:conv:to-glb', '--pipeline', 'my-db:labeler'])
            assert result.exit_code == 0
            body = mocks['api_client'].create_workflow.call_args.args[1]
            assert body['specifiedPipelines'][0] == {
                'pipelineDatabaseId': 'global', 'pipelineId': 'conv', 'defaultTemplateId': 'to-glb'}
            assert body['specifiedPipelines'][1] == {'pipelineDatabaseId': 'my-db', 'pipelineId': 'labeler'}

    def test_create_requires_pipeline(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow'):
            result = cli_runner.invoke(cli, ['workflow', 'create', '-d', 'my-db', '-n', 'X'])
            assert result.exit_code != 0
            assert 'at least one pipeline' in result.output.lower()

    def test_create_invalid_pipeline_ref(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow'):
            result = cli_runner.invoke(cli, [
                'workflow', 'create', '-d', 'my-db', '-n', 'X', '--pipeline', 'badref'])
            assert result.exit_code != 0

    def test_create_invalid_data(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].create_workflow.side_effect = InvalidWorkflowDataError("bad")
            result = cli_runner.invoke(cli, [
                'workflow', 'create', '-d', 'my-db', '-n', 'X', '--pipeline', 'db:p'])
            assert result.exit_code != 0


class TestWorkflowUpdate:
    def test_update_description(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].update_workflow.return_value = {'message': {'workflowId': 'wf1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'update', '-d', 'my-db', '-w', 'wf1', '--description', 'updated'])
            assert result.exit_code == 0
            assert mocks['api_client'].update_workflow.call_args.args[2] == {'description': 'updated'}

    def test_update_pipelines(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].update_workflow.return_value = {'message': {'workflowId': 'wf1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'update', '-d', 'my-db', '-w', 'wf1', '--pipeline', 'db:p2'])
            assert result.exit_code == 0
            assert mocks['api_client'].update_workflow.call_args.args[2]['specifiedPipelines'] == [
                {'pipelineDatabaseId': 'db', 'pipelineId': 'p2'}]

    def test_update_requires_field(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow'):
            result = cli_runner.invoke(cli, ['workflow', 'update', '-d', 'my-db', '-w', 'wf1'])
            assert result.exit_code != 0

    def test_update_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].update_workflow.side_effect = WorkflowNotFoundError("nope")
            result = cli_runner.invoke(cli, [
                'workflow', 'update', '-d', 'my-db', '-w', 'wf1', '--description', 'x'])
            assert result.exit_code != 0


class TestWorkflowDelete:
    def test_delete_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].delete_workflow.return_value = {'message': 'Workflow archived'}
            result = cli_runner.invoke(cli, ['workflow', 'delete', '-d', 'my-db', '-w', 'wf1'])
            assert result.exit_code == 0


class TestWorkflowUnarchive:
    def test_unarchive_clears_archived_and_reenables(self, cli_runner, generic_command_mocks):
        """Archiving sets enabled=False too, so unarchive must re-enable or the workflow comes back
        unexecutable. Only these two flags are sent — any other field would overwrite stored values."""
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].update_workflow.return_value = {
                'message': {'workflowId': 'wf1', 'workflowName': 'WF One', 'archived': False}}
            result = cli_runner.invoke(cli, ['workflow', 'unarchive', '-d', 'my-db', '-w', 'wf1'])
            assert result.exit_code == 0
            mocks['api_client'].update_workflow.assert_called_once_with(
                'my-db', 'wf1', {'archived': False, 'enabled': True})

    def test_unarchive_keep_disabled(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].update_workflow.return_value = {
                'message': {'workflowId': 'wf1', 'archived': False}}
            result = cli_runner.invoke(
                cli, ['workflow', 'unarchive', '-d', 'my-db', '-w', 'wf1', '--keep-disabled'])
            assert result.exit_code == 0
            mocks['api_client'].update_workflow.assert_called_once_with(
                'my-db', 'wf1', {'archived': False})

    def test_unarchive_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].update_workflow.return_value = {
                'message': {'workflowId': 'wf1', 'archived': False}}
            result = cli_runner.invoke(
                cli, ['workflow', 'unarchive', '-d', 'my-db', '-w', 'wf1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['archived'] is False

    def test_unarchive_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].update_workflow.side_effect = WorkflowNotFoundError("nope")
            result = cli_runner.invoke(cli, ['workflow', 'unarchive', '-d', 'my-db', '-w', 'wf1'])
            assert result.exit_code != 0

    def test_unarchive_invalid_data(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].update_workflow.side_effect = InvalidWorkflowDataError("bad")
            result = cli_runner.invoke(cli, ['workflow', 'unarchive', '-d', 'my-db', '-w', 'wf1'])
            assert result.exit_code != 0


class TestWorkflowTrigger:
    def test_trigger_list(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_triggers.return_value = {
                'message': {'Items': [{'triggerType': 'fileUpload', 'enabled': True}]}}
            result = cli_runner.invoke(cli, ['workflow', 'trigger', 'list', '-d', 'my-db', '-w', 'wf1'])
            assert result.exit_code == 0
            assert 'fileUpload' in result.output

    def test_trigger_get(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].get_workflow_trigger.return_value = {
                'message': {'triggerType': 'fileUpload', 'enabled': True}}
            result = cli_runner.invoke(cli, ['workflow', 'trigger', 'get', '-d', 'my-db', '-w', 'wf1'])
            assert result.exit_code == 0

    def test_trigger_set(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].set_workflow_trigger.return_value = {
                'message': {'triggerType': 'fileUpload', 'enabled': True}}
            result = cli_runner.invoke(cli, [
                'workflow', 'trigger', 'set', '-d', 'my-db', '-w', 'wf1',
                '--input-file-filters', '{"allow": ["*.glb"], "exclude": []}', '--enable'])
            assert result.exit_code == 0
            body = mocks['api_client'].set_workflow_trigger.call_args.args[3]
            assert body['enabled'] is True
            assert body['inputFileFilters'] == {'allow': ['*.glb'], 'exclude': []}

    def test_trigger_set_invalid_type(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].set_workflow_trigger.side_effect = InvalidWorkflowTriggerDataError("unsupported")
            result = cli_runner.invoke(cli, [
                'workflow', 'trigger', 'set', '-d', 'my-db', '-w', 'wf1', '-t', 'badType'])
            assert result.exit_code != 0

    def test_trigger_delete(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].delete_workflow_trigger.return_value = {'message': 'Trigger deleted'}
            result = cli_runner.invoke(cli, ['workflow', 'trigger', 'delete', '-d', 'my-db', '-w', 'wf1'])
            assert result.exit_code == 0

    def test_trigger_get_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].get_workflow_trigger.side_effect = WorkflowTriggerNotFoundError("nope")
            result = cli_runner.invoke(cli, ['workflow', 'trigger', 'get', '-d', 'my-db', '-w', 'wf1'])
            assert result.exit_code != 0


class TestWorkflowExecute:
    def test_execute_with_input_file_refs(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--input-file', 'my-db:asset1:/model.glb'])
            assert result.exit_code == 0
            assert 'e1' in result.output
            args = mocks['api_client'].execute_workflow.call_args.args
            assert args[0] == 'global' and args[1] == 'wf1'
            assert args[2]['inputFiles'][0] == {
                'databaseId': 'my-db', 'assetId': 'asset1', 'relativeFileKey': '/model.glb'}

    def test_execute_with_version_in_ref(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--input-file', 'my-db:asset1:/model.glb:v123'])
            assert result.exit_code == 0
            assert mocks['api_client'].execute_workflow.call_args.args[2]['inputFiles'][0]['versionId'] == 'v123'

    def test_execute_no_input_files_allowed(self, cli_runner, generic_command_mocks):
        # 0 input files is valid (arity validated server-side).
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1'])
            assert result.exit_code == 0
            assert mocks['api_client'].execute_workflow.call_args.args[2]['inputFiles'] == []

    def test_execute_invalid_input_ref(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow'):
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--input-file', 'too:few'])
            assert result.exit_code != 0

    def test_execute_pipeline_parameters(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--pipeline-parameters', '{"conv": {"templateId": "to-glb"}}'])
            assert result.exit_code == 0
            body = mocks['api_client'].execute_workflow.call_args.args[2]
            assert body['pipelineExecutionParameters'] == {'conv': {'templateId': 'to-glb'}}

    def test_execute_omits_the_prefix_so_the_workflow_default_applies(self, cli_runner,
                                                                      generic_command_mocks):
        """No --output-path-prefix must leave the key OUT of the body, which is what lets the backend
        substitute the workflow's own default prefix."""
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1'])
            assert result.exit_code == 0
            body = mocks['api_client'].execute_workflow.call_args.args[2]
            assert 'outputFileBaseExecutionPathExtension' not in body

    def test_execute_sends_a_prefix_with_its_template_tags_unresolved(self, cli_runner,
                                                                     generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--output-path-prefix', '/{{jobName}}/'])
            assert result.exit_code == 0
            body = mocks['api_client'].execute_workflow.call_args.args[2]
            assert body['outputFileBaseExecutionPathExtension'] == '/{{jobName}}/'

    def test_execute_sends_an_explicitly_empty_prefix(self, cli_runner, generic_command_mocks):
        """An empty string is a deliberate "asset root". Dropping it as falsy would re-apply the
        workflow default the caller passed the flag to opt out of."""
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--output-path-prefix', ''])
            assert result.exit_code == 0
            body = mocks['api_client'].execute_workflow.call_args.args[2]
            assert body['outputFileBaseExecutionPathExtension'] == ''

    def test_execute_workflow_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.side_effect = WorkflowNotFoundError("nope")
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'bad',
                '--input-file', 'db:a:/f'])
            assert result.exit_code != 0

    def test_execute_conflicting_running(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.side_effect = WorkflowAlreadyRunningError("running")
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--input-file', 'db:a:/f'])
            assert result.exit_code != 0

    def test_execute_execution_error(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.side_effect = WorkflowExecutionError("bad")
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--input-file', 'db:a:/f'])
            assert result.exit_code != 0


class TestWorkflowListExecutions:
    def test_list_executions_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {
                'message': {'Items': [{'workflowExecutionId': 'e1', 'executionStatus': 'SUCCEEDED'}]}}
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'asset1'])
            assert result.exit_code == 0
            assert 'e1' in result.output

    def test_list_executions_workflow_database_filter(self, cli_runner, generic_command_mocks):
        # The --workflow-database-id filter must reach the api_client (which sends it as a GET body,
        # since the route is GET-only). Guards against regressing to a POST on a GET-only route.
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'asset1',
                '--workflow-database-id', 'global'])
            assert result.exit_code == 0
            assert mocks['api_client'].list_workflow_executions.call_args.kwargs['workflow_database_id'] == 'global'

    def test_list_executions_empty_page_surfaces_next_token(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {
                'message': {'Items': [], 'NextToken': 'tok-abc'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'asset1'])
            assert result.exit_code == 0
            assert 'tok-abc' in result.output
            assert 'more pages available' in result.output

    def test_list_executions_page_size_cap(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow'):
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'asset1', '--page-size', '100'])
            assert result.exit_code != 0
            assert 'page size' in result.output.lower()

    def test_list_executions_asset_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.side_effect = AssetNotFoundError("nope")
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'bad'])
            assert result.exit_code != 0
