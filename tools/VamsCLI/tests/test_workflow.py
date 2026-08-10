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

    def test_create_with_job_name(self, cli_runner, generic_command_mocks):
        # A job name becomes a folder in the step's output path, so it must be settable from the
        # shorthand ref and not only through --specified-pipelines JSON.
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].create_workflow.return_value = {'message': {'workflowId': 'gen'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'create', '-d', 'my-db', '-n', 'WF',
                '--pipeline', 'global:conv:to-glb:convert-for-web'])
            assert result.exit_code == 0
            body = mocks['api_client'].create_workflow.call_args.args[1]
            assert body['specifiedPipelines'][0] == {
                'pipelineDatabaseId': 'global', 'pipelineId': 'conv',
                'defaultTemplateId': 'to-glb', 'jobName': 'convert-for-web'}

    def test_create_with_job_name_and_no_default_template(self, cli_runner, generic_command_mocks):
        # The segments are positional, so an empty third segment sets a job name without a template.
        # defaultTemplateId must be ABSENT rather than empty — an empty string would be sent as a
        # template id the backend then tries to resolve.
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].create_workflow.return_value = {'message': {'workflowId': 'gen'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'create', '-d', 'my-db', '-n', 'WF',
                '--pipeline', 'global:conv::convert-for-web'])
            assert result.exit_code == 0
            body = mocks['api_client'].create_workflow.call_args.args[1]
            assert body['specifiedPipelines'][0] == {
                'pipelineDatabaseId': 'global', 'pipelineId': 'conv', 'jobName': 'convert-for-web'}

    @pytest.mark.parametrize("ref", [
        'db:pipe:tpl:job:extra',   # a 5th segment is not a ref shape the parser supports
        ':pipe',                   # blank database
        'db:',                     # blank pipeline
    ])
    def test_create_rejects_malformed_refs(self, ref, cli_runner, generic_command_mocks):
        # Silently dropping the extra segment (or accepting a blank id) would create a workflow
        # pointing somewhere the author did not intend.
        with generic_command_mocks('workflow'):
            result = cli_runner.invoke(cli, [
                'workflow', 'create', '-d', 'my-db', '-n', 'X', '--pipeline', ref])
            assert result.exit_code != 0

    def test_json_pipeline_list_passes_through_verbatim(self, cli_runner, generic_command_mocks):
        # --specified-pipelines stays the escape hatch for any field the shorthand cannot express.
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].create_workflow.return_value = {'message': {'workflowId': 'gen'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'create', '-d', 'my-db', '-n', 'WF', '--specified-pipelines',
                '[{"pipelineDatabaseId":"global","pipelineId":"conv","jobName":"step-one"}]'])
            assert result.exit_code == 0
            body = mocks['api_client'].create_workflow.call_args.args[1]
            assert body['specifiedPipelines'][0]['jobName'] == 'step-one'

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

    def test_execute_keeps_a_colon_in_the_file_key(self, cli_runner, generic_command_mocks):
        """A ':' is legal in an S3 key, so everything past the second colon is the key. Splitting
        further would send a truncated path the caller never typed."""
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--input-file', 'my-db:asset1:/scans/scan:2024.las'])
            assert result.exit_code == 0
            assert mocks['api_client'].execute_workflow.call_args.args[2]['inputFiles'][0] == {
                'databaseId': 'my-db', 'assetId': 'asset1',
                'relativeFileKey': '/scans/scan:2024.las'}

    def test_execute_keeps_multiple_colons_in_the_file_key(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--input-file', 'my-db:asset1:/exports/2024-01-01T12:30:00Z/scan.las'])
            assert result.exit_code == 0
            assert (mocks['api_client'].execute_workflow.call_args.args[2]['inputFiles'][0]
                    ['relativeFileKey'] == '/exports/2024-01-01T12:30:00Z/scan.las')

    def test_execute_pins_a_version_through_the_json_option(self, cli_runner, generic_command_mocks):
        """versionId is a named field of --input-files, which is unambiguous where the positional
        ref is not."""
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--input-files', '[{"databaseId": "my-db", "assetId": "asset1", '
                                 '"relativeFileKey": "/model.glb", "versionId": "v123"}]'])
            assert result.exit_code == 0
            assert mocks['api_client'].execute_workflow.call_args.args[2]['inputFiles'][0][
                'versionId'] == 'v123'

    @pytest.mark.parametrize('ref', [
        ':asset1:/model.glb',
        'my-db::/model.glb',
        'my-db:asset1:',
    ])
    def test_execute_rejects_an_empty_ref_segment(self, ref, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow'):
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--input-file', ref])
            assert result.exit_code != 0

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


class TestWorkflowExecuteMetadataSources:
    """The metadata-source selection: entities the run reads stored metadata from, which are not
    input files. A source asset ref is 2-segment ('db:asset') precisely because it carries no file
    key, so it must not be parsed by the 3-segment --input-file rule."""

    def test_execute_sends_metadata_source_assets(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--metadata-source-asset', 'my-db:asset1',
                '--metadata-source-asset', 'other-db:asset2'])
            assert result.exit_code == 0
            body = mocks['api_client'].execute_workflow.call_args.args[2]
            assert body['metadataSourceAssets'] == [
                {'databaseId': 'my-db', 'assetId': 'asset1'},
                {'databaseId': 'other-db', 'assetId': 'asset2'}]
            # A source is never an input file, so it must not leak into inputFiles.
            assert body['inputFiles'] == []

    def test_execute_sends_the_metadata_source_database(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--metadata-source-database', 'my-db'])
            assert result.exit_code == 0
            body = mocks['api_client'].execute_workflow.call_args.args[2]
            assert body['metadataSourceDatabaseId'] == 'my-db'

    def test_execute_omits_both_fields_when_no_source_is_named(self, cli_runner,
                                                               generic_command_mocks):
        """Naming a metadata source is always optional, so an unnamed selection must leave the keys
        out of the body rather than send empty values."""
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1'])
            assert result.exit_code == 0
            body = mocks['api_client'].execute_workflow.call_args.args[2]
            assert 'metadataSourceAssets' not in body
            assert 'metadataSourceDatabaseId' not in body

    def test_execute_accepts_metadata_source_assets_as_json(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--metadata-source-assets', '[{"databaseId": "my-db", "assetId": "asset1"}]'])
            assert result.exit_code == 0
            body = mocks['api_client'].execute_workflow.call_args.args[2]
            assert body['metadataSourceAssets'] == [{'databaseId': 'my-db', 'assetId': 'asset1'}]

    @pytest.mark.parametrize('ref', [
        'only-one-segment',
        'my-db:asset1:/model.glb',   # a 3-segment input-file ref, not a metadata source
        ':asset1',
        'my-db:',
        '',
    ])
    def test_execute_rejects_a_malformed_source_ref(self, ref, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--metadata-source-asset', ref])
            assert result.exit_code != 0
            assert not mocks['api_client'].execute_workflow.called

    @pytest.mark.parametrize('value', ['GLOBAL', 'global', 'Global', ' global '])
    def test_execute_rejects_global_as_the_metadata_source_database(self, value, cli_runner,
                                                                   generic_command_mocks):
        """GLOBAL is the all-databases keyword, not a readable database. It is legal for
        --workflow-database-id and inside a source asset's ref, so it must be refused here by name."""
        with generic_command_mocks('workflow') as mocks:
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--metadata-source-database', value])
            assert result.exit_code != 0
            assert 'GLOBAL' in result.output
            assert not mocks['api_client'].execute_workflow.called

    def test_execute_still_accepts_global_inside_a_source_asset_ref(self, cli_runner,
                                                                   generic_command_mocks):
        """An asset may live in the GLOBAL database, so a source asset's databaseId may be GLOBAL —
        only the standalone database selection rejects it."""
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {'executionId': 'e1'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--metadata-source-asset', 'GLOBAL:asset1'])
            assert result.exit_code == 0
            body = mocks['api_client'].execute_workflow.call_args.args[2]
            assert body['metadataSourceAssets'] == [{'databaseId': 'GLOBAL', 'assetId': 'asset1'}]

    def test_execute_rejects_a_non_list_metadata_source_json(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--metadata-source-assets', '{"databaseId": "my-db"}'])
            assert result.exit_code != 0
            assert not mocks['api_client'].execute_workflow.called

    def test_execute_surfaces_response_warnings(self, cli_runner, generic_command_mocks):
        """A capped metadata capture or an unreadable source database comes back as a warning on an
        otherwise successful execute. Swallowing it would report inputs the run never read."""
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {
                'executionId': 'e1',
                'warnings': ["Metadata for asset 'a1' was truncated at the per-entity cap.",
                             "Database 'other-db' could not be read."]}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--metadata-source-database', 'my-db'])
            assert result.exit_code == 0
            assert 'Warnings:' in result.output
            assert 'truncated at the per-entity cap' in result.output
            assert "Database 'other-db' could not be read." in result.output

    def test_execute_warnings_reach_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].execute_workflow.return_value = {'message': {
                'executionId': 'e1', 'warnings': ['Metadata truncated.']}}
            result = cli_runner.invoke(cli, [
                'workflow', 'execute', '--workflow-database-id', 'global', '-w', 'wf1',
                '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['warnings'] == ['Metadata truncated.']


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

    def test_list_executions_sends_the_date_range(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'asset1',
                '--filter-start-date', '2024-01-01T00:00:00Z',
                '--filter-end-date', '2024-06-01T00:00:00Z'])
            assert result.exit_code == 0
            params = mocks['api_client'].list_workflow_executions.call_args.kwargs['params']
            assert params['filterStartDate'] == '2024-01-01T00:00:00Z'
            assert params['filterEndDate'] == '2024-06-01T00:00:00Z'

    def test_list_executions_sends_the_date_range_on_every_page(self, cli_runner,
                                                               generic_command_mocks):
        # A bound missing from page 2 onward widens the window mid-walk, so the combined result
        # includes rows outside the range the caller asked for.
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.side_effect = [
                {'message': {'Items': [{'workflowExecutionId': 'e1'}], 'NextToken': 't1'}},
                {'message': {'Items': [{'workflowExecutionId': 'e2'}]}},
            ]
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'asset1', '--auto-paginate',
                '--filter-start-date', '2024-01-01T00:00:00Z'])
            assert result.exit_code == 0
            for call in mocks['api_client'].list_workflow_executions.call_args_list:
                assert call.kwargs['params']['filterStartDate'] == '2024-01-01T00:00:00Z'

    def test_list_executions_omits_the_dates_when_not_supplied(self, cli_runner,
                                                              generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'asset1'])
            assert result.exit_code == 0
            params = mocks['api_client'].list_workflow_executions.call_args.kwargs['params']
            assert 'filterStartDate' not in params and 'filterEndDate' not in params

    def test_list_executions_reports_the_applied_window_on_an_empty_list(self, cli_runner,
                                                                        generic_command_mocks):
        # An asset whose history predates the default window lists as empty; the echoed window is
        # what tells the caller a floor was applied rather than that no history exists.
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {
                'message': {'Items': [], 'filterStartDate': '2026-05-11T00:00:00Z'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'asset1'])
            assert result.exit_code == 0
            assert 'No workflow executions found.' in result.output
            assert '2026-05-11T00:00:00Z' in result.output

    def test_list_executions_auto_paginate_max_items_keeps_the_token(self, cli_runner,
                                                                    generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {
                'message': {'Items': [{'workflowExecutionId': 'e1'}], 'NextToken': 'tok-resume'}}
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'asset1', '--auto-paginate',
                '--max-items', '1', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['NextToken'] == 'tok-resume'
            assert '--starting-token tok-resume' in data['note']

    def test_list_executions_asset_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.side_effect = AssetNotFoundError("nope")
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions', '-d', 'my-db', '-a', 'bad'])
            assert result.exit_code != 0


class TestWorkflowListTriggerCounts:
    """The workflow list surfaces trigger counts and can filter on them."""

    def test_has_triggers_filter_is_passed_through(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['workflow', 'list', '--has-triggers', 'true'])
            assert result.exit_code == 0
            assert mocks['api_client'].list_workflows.call_args.kwargs['params']['hasTriggers'] == 'true'

    def test_has_triggers_filter_is_passed_through_when_auto_paginating(self, cli_runner,
                                                                       generic_command_mocks):
        # The filter has to reach EVERY page request, not just the first — otherwise page 2 onward
        # comes back unfiltered and the combined result silently includes non-matching workflows.
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['workflow', 'list', '--has-triggers', 'false',
                                             '--auto-paginate'])
            assert result.exit_code == 0
            assert mocks['api_client'].list_workflows.call_args.kwargs['params']['hasTriggers'] == 'false'

    def test_omitting_the_filter_sends_no_param(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['workflow', 'list'])
            assert result.exit_code == 0
            assert 'hasTriggers' not in mocks['api_client'].list_workflows.call_args.kwargs['params']

    def test_an_invalid_filter_value_is_rejected_by_click(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            result = cli_runner.invoke(cli, ['workflow', 'list', '--has-triggers', 'maybe'])
            assert result.exit_code != 0
            mocks['api_client'].list_workflows.assert_not_called()

    def test_counts_are_rendered(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.return_value = {'message': {'Items': [
                {'workflowId': 'wf1', 'triggerCount': 2, 'triggersEnabledCount': 2},
            ]}}
            result = cli_runner.invoke(cli, ['workflow', 'list'])
            assert result.exit_code == 0
            assert 'Triggers: 2' in result.output

    def test_a_partly_disabled_trigger_set_says_so(self, cli_runner, generic_command_mocks):
        # "2 (1 enabled)" is the state that explains a workflow that looks configured but only
        # partly fires; rendering just "2" would hide it.
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.return_value = {'message': {'Items': [
                {'workflowId': 'wf1', 'triggerCount': 2, 'triggersEnabledCount': 1},
            ]}}
            result = cli_runner.invoke(cli, ['workflow', 'list'])
            assert result.exit_code == 0
            assert 'Triggers: 2 (1 enabled)' in result.output

    def test_zero_triggers_is_shown_rather_than_omitted(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflows.return_value = {'message': {'Items': [
                {'workflowId': 'wf1', 'triggerCount': 0, 'triggersEnabledCount': 0},
            ]}}
            result = cli_runner.invoke(cli, ['workflow', 'list'])
            assert result.exit_code == 0
            assert 'Triggers: 0' in result.output

    def test_a_details_response_still_lists_the_trigger_types(self, cli_runner,
                                                             generic_command_mocks):
        # `workflow get` returns the trigger rows themselves; those keep taking precedence over the
        # counts so the detail view still names each trigger type.
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].get_workflow.return_value = {'message': {
                'workflowId': 'wf1', 'triggers': [{'triggerType': 'fileUpload'}]}}
            result = cli_runner.invoke(cli, ['workflow', 'get', '-d', 'db1', '-w', 'wf1'])
            assert result.exit_code == 0
            assert 'Triggers: fileUpload' in result.output
