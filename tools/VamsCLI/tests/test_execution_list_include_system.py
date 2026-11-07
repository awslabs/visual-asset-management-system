"""Tests for --include-system on the two execution list commands (#1121).

Runs of system workflows are hidden by both list routes unless the caller asks for them. The flag is
tri-state: absent leaves the service default (which shows a single system workflow's runs), and
--no-include-system hides them even then. The row's isSystem field passes through --json-output."""

import json

from vamscli.constants import EXECUTION_LIST_INCLUDE_SYSTEM_PARAM
from vamscli.main import cli


def _global_params(mocks):
    return mocks['api_client'].list_executions.call_args.kwargs['params']


def _asset_params(mocks):
    return mocks['api_client'].list_workflow_executions.call_args.kwargs['params']


class TestExecutionListIncludeSystem:
    def test_absent_sends_nothing_so_the_service_default_applies(self, cli_runner,
                                                                  generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['execution', 'list'])
            assert result.exit_code == 0
            assert EXECUTION_LIST_INCLUDE_SYSTEM_PARAM not in _global_params(mocks)

    def test_include_system_sends_true(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['execution', 'list', '--include-system'])
            assert result.exit_code == 0
            assert _global_params(mocks)[EXECUTION_LIST_INCLUDE_SYSTEM_PARAM] == 'true'

    def test_no_include_system_sends_false(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['execution', 'list', '-w', 'sys-wf',
                                             '--workflow-database-id', 'GLOBAL',
                                             '--no-include-system'])
            assert result.exit_code == 0
            assert _global_params(mocks)[EXECUTION_LIST_INCLUDE_SYSTEM_PARAM] == 'false'

    def test_every_auto_paginated_page_carries_the_flag(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.side_effect = [
                {'message': {'Items': [{'workflowExecutionId': 'e1'}], 'NextToken': 't1',
                             'includeSystem': True}},
                {'message': {'Items': [{'workflowExecutionId': 'e2'}], 'includeSystem': True}},
            ]
            result = cli_runner.invoke(cli, ['execution', 'list', '--include-system',
                                             '--auto-paginate', '--json-output'])
            assert result.exit_code == 0
            calls = mocks['api_client'].list_executions.call_args_list
            assert [c.kwargs['params'][EXECUTION_LIST_INCLUDE_SYSTEM_PARAM] for c in calls] == [
                'true', 'true']
            data = json.loads(result.output)
            assert data['includeSystem'] is True

    def test_json_output_passes_the_row_flag_through(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {
                'Items': [{'workflowExecutionId': 'e1', 'isSystem': True}],
                'includeSystem': True}}
            result = cli_runner.invoke(cli, ['execution', 'list', '--include-system',
                                             '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['Items'][0]['isSystem'] is True
            assert data['includeSystem'] is True

    def test_text_output_marks_system_rows_and_states_when_they_are_hidden(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {
                'Items': [{'workflowExecutionId': 'e1', 'isSystem': True}],
                'includeSystem': True, 'filterStartDate': '2026-01-01T00:00:00Z'}}
            shown = cli_runner.invoke(cli, ['execution', 'list', '--include-system'])
            assert 'System run: yes' in shown.output
            assert 'hidden' not in shown.output
            mocks['api_client'].list_executions.return_value = {'message': {
                'Items': [], 'includeSystem': False, 'filterStartDate': '2026-01-01T00:00:00Z'}}
            hidden = cli_runner.invoke(cli, ['execution', 'list'])
            assert 'System workflow runs hidden' in hidden.output


class TestWorkflowListExecutionsIncludeSystem:
    def test_absent_sends_nothing(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {'message': {'Items': []}}
            result = cli_runner.invoke(cli, ['workflow', 'list-executions', '-d', 'db', '-a', 'a1'])
            assert result.exit_code == 0
            assert EXECUTION_LIST_INCLUDE_SYSTEM_PARAM not in _asset_params(mocks)

    def test_include_and_no_include_send_true_and_false(self, cli_runner, generic_command_mocks):
        for flag, expected in (('--include-system', 'true'), ('--no-include-system', 'false')):
            with generic_command_mocks('workflow') as mocks:
                mocks['api_client'].list_workflow_executions.return_value = {
                    'message': {'Items': []}}
                result = cli_runner.invoke(cli, ['workflow', 'list-executions', '-d', 'db',
                                                 '-a', 'a1', flag])
                assert result.exit_code == 0
                assert _asset_params(mocks)[EXECUTION_LIST_INCLUDE_SYSTEM_PARAM] == expected

    def test_json_output_carries_the_flag_and_the_applied_value(self, cli_runner,
                                                                generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {'message': {
                'Items': [{'workflowExecutionId': 'e1', 'isSystem': False}],
                'includeSystem': False}}
            result = cli_runner.invoke(cli, ['workflow', 'list-executions', '-d', 'db', '-a', 'a1',
                                             '--json-output'])
            assert result.exit_code == 0
            data = json.loads(result.output)
            assert data['Items'][0]['isSystem'] is False
            assert data['includeSystem'] is False

    def test_auto_paginate_carries_the_applied_value(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.side_effect = [
                {'message': {'Items': [{'workflowExecutionId': 'e1'}], 'NextToken': 't',
                             'includeSystem': False}},
                {'message': {'Items': [{'workflowExecutionId': 'e2'}], 'includeSystem': False}},
            ]
            result = cli_runner.invoke(cli, ['workflow', 'list-executions', '-d', 'db', '-a', 'a1',
                                             '--auto-paginate', '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['includeSystem'] is False


class TestWorkflowIdWithoutItsDatabase:
    """-w alone is not a single-workflow scope, so the service keeps a system workflow's runs hidden.
    The text hint names the missing --workflow-database-id; --json-output stays the bare payload."""

    def test_global_hint_names_the_missing_database_option(self, cli_runner,
                                                           generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {
                'Items': [], 'includeSystem': False, 'filterStartDate': '2026-01-01T00:00:00Z'}}
            result = cli_runner.invoke(cli, ['execution', 'list', '-w', 'sys-wf'])
            assert result.exit_code == 0
            assert '--workflow-database-id' in result.output
            assert 'workflowDatabaseId' not in _global_params(mocks)

    def test_global_hint_without_w_does_not_mention_the_database_option(
            self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {
                'Items': [], 'includeSystem': False, 'filterStartDate': '2026-01-01T00:00:00Z'}}
            result = cli_runner.invoke(cli, ['execution', 'list', '-w', 'sys-wf',
                                             '--workflow-database-id', 'GLOBAL'])
            assert 'System workflow runs hidden' in result.output
            assert 'add --workflow-database-id' not in result.output

    def test_asset_hint_names_the_missing_database_option(self, cli_runner,
                                                          generic_command_mocks):
        with generic_command_mocks('workflow') as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {'message': {
                'Items': [], 'includeSystem': False, 'filterStartDate': '2026-01-01T00:00:00Z'}}
            result = cli_runner.invoke(cli, ['workflow', 'list-executions', '-d', 'db', '-a', 'a1',
                                             '-w', 'sys-wf'])
            assert result.exit_code == 0
            assert '--workflow-database-id' in result.output

    def test_json_output_has_no_hint(self, cli_runner, generic_command_mocks):
        with generic_command_mocks('execution') as mocks:
            mocks['api_client'].list_executions.return_value = {'message': {
                'Items': [], 'includeSystem': False}}
            result = cli_runner.invoke(cli, ['execution', 'list', '-w', 'sys-wf',
                                             '--json-output'])
            assert result.exit_code == 0
            assert json.loads(result.output)['includeSystem'] is False
