"""Tests for execution operations commands (list, details, logs, abort, rerun, permanent-delete)."""

import json
import pytest

from vamscli.main import cli
from vamscli.utils.exceptions import (
    ExecutionNotFoundError, ExecutionInProgressError, InvalidExecutionDataError,
)


class TestExecutionList:
    def test_list_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {
                'message': {'Items': [{'workflowExecutionId': 'e1', 'workflowId': 'wf1',
                                       'workflowDatabaseId': 'db1', 'executionStatus': 'SUCCEEDED'}]}}
            result = cli_runner.invoke(cli, ['execution', 'list'])
            assert result.exit_code == 0
            assert 'e1' in result.output

    def test_list_filters_passed(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, [
                'execution', 'list', '-w', 'wf1', '--status', 'RUNNING', '--group-id', 'grp1'])
            assert result.exit_code == 0
            params = mocks['api_client'].list_executions.call_args.kwargs['params']
            assert params['workflowId'] == 'wf1'
            assert params['status'] == 'RUNNING'
            assert params['groupId'] == 'grp1'

    def test_list_json_output(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {
                'message': {'Items': [{'workflowExecutionId': 'e1'}]}}
            result = cli_runner.invoke(cli, ['execution', 'list', '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['Items'][0]['workflowExecutionId'] == 'e1'

    def test_list_auto_paginate(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.side_effect = [
                {'message': {'Items': [{'workflowExecutionId': 'e1'}], 'NextToken': 'tok'}},
                {'message': {'Items': [{'workflowExecutionId': 'e2'}]}},
            ]
            result = cli_runner.invoke(cli, ['execution', 'list', '--auto-paginate'])
            assert result.exit_code == 0
            assert mocks['api_client'].list_executions.call_count == 2

    def test_list_no_setup(self, cli_runner, no_setup_command_mocks):
        with no_setup_command_mocks('execution'):
            result = cli_runner.invoke(cli, ['execution', 'list'])
            assert result.exit_code != 0


class TestExecutionDetails:
    def test_details_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details.return_value = {
                'message': {'workflowExecutionId': 'e1', 'workflowId': 'wf1', 'workflowDatabaseId': 'db1',
                            'executionStatus': 'SUCCEEDED',
                            'pipelines': [{'name': 'p1', 'executionStatus': 'SUCCEEDED'}],
                            'inputFiles': [{'databaseId': 'db1', 'assetId': 'a1', 'inputAssetFileKey': '/f.glb'}],
                            'outputs': {'files': [{'relativeFilePath': '/out.glb'}]}}}
            result = cli_runner.invoke(cli, ['execution', 'details', 'e1'])
            assert result.exit_code == 0
            assert 'e1' in result.output
            assert 'p1' in result.output

    def test_details_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_details.side_effect = ExecutionNotFoundError("nope")
            result = cli_runner.invoke(cli, ['execution', 'details', 'bad'])
            assert result.exit_code != 0


class TestExecutionLogs:
    def test_logs_truncated_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_logs.return_value = {
                'message': {'mode': 'truncated', 'executionLog': 'log text', 'executionError': ''}}
            result = cli_runner.invoke(cli, ['execution', 'logs', 'e1'])
            assert result.exit_code == 0
            assert 'log text' in result.output
            assert mocks['api_client'].get_execution_logs.call_args.kwargs['params']['mode'] == 'truncated'

    def test_logs_full_mode(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_logs.return_value = {
                'message': {'mode': 'full', 'events': [{'timestamp': 1, 'message': 'evt'}]}}
            result = cli_runner.invoke(cli, ['execution', 'logs', 'e1', '--mode', 'full', '--limit', '10'])
            assert result.exit_code == 0
            params = mocks['api_client'].get_execution_logs.call_args.kwargs['params']
            assert params['mode'] == 'full'
            assert params['limit'] == 10

    def test_logs_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].get_execution_logs.side_effect = ExecutionNotFoundError("nope")
            result = cli_runner.invoke(cli, ['execution', 'logs', 'bad'])
            assert result.exit_code != 0


class TestExecutionAbort:
    def test_abort_single_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].abort_execution.return_value = {'message': 'Execution aborted'}
            result = cli_runner.invoke(cli, ['execution', 'abort', 'e1'])
            assert result.exit_code == 0
            assert mocks['api_client'].abort_execution.call_args.kwargs['group_id'] is None

    def test_abort_group_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].abort_execution.return_value = {
                'message': {'groupId': 'grp1', 'results': [{'executionId': 'e1', 'status': 'aborted'}]}}
            result = cli_runner.invoke(cli, ['execution', 'abort', 'e1', '--group-id', 'grp1'])
            assert result.exit_code == 0
            assert mocks['api_client'].abort_execution.call_args.kwargs['group_id'] == 'grp1'

    def test_abort_requires_target(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution'):
            result = cli_runner.invoke(cli, ['execution', 'abort'])
            assert result.exit_code != 0

    def test_abort_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].abort_execution.side_effect = ExecutionNotFoundError("nope")
            result = cli_runner.invoke(cli, ['execution', 'abort', 'bad'])
            assert result.exit_code != 0


class TestExecutionRerun:
    def test_rerun_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].rerun_execution.return_value = {'message': {'executionId': 'e2'}}
            result = cli_runner.invoke(cli, ['execution', 'rerun', 'e1'])
            assert result.exit_code == 0
            assert 'e2' in result.output

    def test_rerun_with_group(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].rerun_execution.return_value = {'message': {'executionId': 'e2', 'executionGroupId': 'grp1'}}
            result = cli_runner.invoke(cli, ['execution', 'rerun', 'e1', '--execution-group-id', 'grp1'])
            assert result.exit_code == 0
            assert mocks['api_client'].rerun_execution.call_args.kwargs['execution_group_id'] == 'grp1'

    def test_rerun_unavailable(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].rerun_execution.side_effect = InvalidExecutionDataError("unavailable")
            result = cli_runner.invoke(cli, ['execution', 'rerun', 'e1'])
            assert result.exit_code != 0


class TestExecutionPermanentDelete:
    def test_permanent_delete_success(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].permanent_delete_execution.return_value = {
                'message': 'Execution records permanently deleted'}
            result = cli_runner.invoke(cli, ['execution', 'permanent-delete', 'e1', '--yes'])
            assert result.exit_code == 0

    def test_permanent_delete_in_progress(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].permanent_delete_execution.side_effect = ExecutionInProgressError("in progress")
            result = cli_runner.invoke(cli, ['execution', 'permanent-delete', 'e1', '--yes'])
            assert result.exit_code != 0

    def test_permanent_delete_json_skips_prompt(self, cli_runner, generic_command_mocks):
        # In JSON mode the interactive confirm is skipped (no stdin needed).
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].permanent_delete_execution.return_value = {'message': 'deleted'}
            result = cli_runner.invoke(cli, ['execution', 'permanent-delete', 'e1', '--json-output'])
            assert result.exit_code == 0
            mocks['api_client'].permanent_delete_execution.assert_called_once()

    def test_permanent_delete_not_found(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].permanent_delete_execution.side_effect = ExecutionNotFoundError("nope")
            result = cli_runner.invoke(cli, ['execution', 'permanent-delete', 'bad', '--yes'])
            assert result.exit_code != 0
