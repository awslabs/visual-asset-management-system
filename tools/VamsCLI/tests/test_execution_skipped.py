"""#998: SKIPPED execution records (file-upload trigger launches a concurrency restriction refused) in
the execution list, the asset execution list and execution details."""

import json

from vamscli.main import cli

SKIP_ITEM = {'workflowExecutionId': 's1', 'workflowId': 'wf1', 'workflowDatabaseId': 'db1',
             'executionStatus': 'SKIPPED', 'executionStartDate': '2026-11-06T10:00:00Z',
             'startDate': '2026-11-06T10:00:00Z', 'skippedLaunchCount': 49, 'skipScope': 'asset',
             'skipReason': 'A conflicting execution of this workflow is already running for this asset.'}

SKIP_DETAILS = {
    'workflowExecutionId': 's1', 'workflowId': 'wf1', 'workflowDatabaseId': 'db1',
    'executionStatus': 'SKIPPED', 'pipelines': [], 'inputFiles': [],
    'skip': {'reason': 'A conflicting execution of this workflow is already running for this asset.',
             'scope': 'asset', 'count': 49, 'firstSkippedDate': '2026-11-06T10:00:00Z',
             'lastSkippedDate': '2026-11-06T10:09:00Z',
             'files': [{'databaseId': 'db1', 'assetId': 'a1', 'inputAssetFileKey': '/a1/f.glb',
                        'versionId': 'v1'}],
             'filesTruncated': True, 'blockingExecutionId': 'e-blocking'},
}


class TestSkippedExecutionList:
    def test_list_shows_the_skip_count_and_reason(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': [SKIP_ITEM]}}
            result = cli_runner.invoke(cli, ['execution', 'list'])
            assert result.exit_code == 0
            assert 'Status: SKIPPED' in result.output
            assert 'Skipped launches: 49' in result.output
            assert 'already running for this asset' in result.output

    def test_a_non_skipped_row_has_no_skip_lines(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': [
                {'workflowExecutionId': 'e1', 'executionStatus': 'SUCCEEDED'}]}}
            result = cli_runner.invoke(cli, ['execution', 'list'])
            assert result.exit_code == 0
            assert 'Skipped launches' not in result.output

    def test_the_status_filter_accepts_skipped(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['execution', 'list', '--status', 'SKIPPED'])
            assert result.exit_code == 0
            assert mocks['api_client'].list_executions.call_args.kwargs['params']['status'] == 'SKIPPED'

    def test_json_output_passes_the_skip_fields_through(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': [SKIP_ITEM]}}
            result = cli_runner.invoke(cli, ['execution', 'list', '--json-output'])
            assert result.exit_code == 0
            item = json.loads(result.output)['Items'][0]
            assert item['skippedLaunchCount'] == 49 and item['skipScope'] == 'asset'


class TestSkippedExecutionDetails:
    def test_details_show_the_skip_block(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details.return_value = {'message': SKIP_DETAILS}
            result = cli_runner.invoke(cli, ['execution', 'details', 's1'])
            assert result.exit_code == 0
            assert 'Skipped launches: 49 (asset)' in result.output
            assert 'Blocked by: e-blocking' in result.output
            assert 'db1:a1/a1/f.glb  (v v1)' in result.output
            assert 'more files exist' in result.output

    def test_details_omit_the_blocking_run_the_api_withheld(self, cli_runner, generic_command_mocks):
        withheld = dict(SKIP_DETAILS, skip={k: v for k, v in SKIP_DETAILS['skip'].items()
                                            if k != 'blockingExecutionId'})
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details.return_value = {'message': withheld}
            result = cli_runner.invoke(cli, ['execution', 'details', 's1'])
            assert result.exit_code == 0
            assert 'Blocked by' not in result.output

    def test_details_json_output_is_the_api_message(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details.return_value = {'message': SKIP_DETAILS}
            result = cli_runner.invoke(cli, ['execution', 'details', 's1', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['skip']['count'] == 49


class TestSkippedAssetExecutionList:
    def test_the_asset_list_shows_the_skip_count(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {
                'message': {'Items': [dict(SKIP_ITEM, inputAssetFileKey='/a1/f.glb')]}}
            result = cli_runner.invoke(cli, ['workflow', 'list-executions', '-d', 'db1', '-a', 'a1'])
            assert result.exit_code == 0, result.output
            assert 'Status: SKIPPED' in result.output
            assert 'Skipped launches: 49' in result.output


class TestExcludeStatusOption:
    def test_execution_list_sends_exclude_status(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['execution', 'list', '--exclude-status', 'SKIPPED,FAILED'])
            assert result.exit_code == 0, result.output
            params = mocks['api_client'].list_executions.call_args.kwargs['params']
            assert params['excludeStatus'] == 'SKIPPED,FAILED'

    def test_execution_list_without_it_sends_no_exclusion(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['execution', 'list'])
            assert result.exit_code == 0
            assert 'excludeStatus' not in mocks['api_client'].list_executions.call_args.kwargs['params']

    def test_the_asset_list_sends_exclude_status(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['workflow', 'list-executions', '-d', 'db1', '-a', 'a1',
                                             '--exclude-status', 'SKIPPED'])
            assert result.exit_code == 0, result.output
            params = mocks['api_client'].list_workflow_executions.call_args.kwargs['params']
            assert params['excludeStatus'] == 'SKIPPED'
