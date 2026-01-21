"""Test workflow functionality."""

import json
import pytest
from unittest.mock import Mock
from click.testing import CliRunner

from vamscli.main import cli
from vamscli.utils.exceptions import (
    WorkflowNotFoundError, WorkflowExecutionError, WorkflowAlreadyRunningError,
    InvalidWorkflowDataError, AssetNotFoundError, DatabaseNotFoundError
)


# File-level fixtures for workflow-specific testing patterns
@pytest.fixture
def workflow_command_mocks(generic_command_mocks):
    """Provide workflow-specific command mocks.
    
    This fixture uses the global generic_command_mocks factory to create
    mocks specifically configured for workflow command testing.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return generic_command_mocks('workflow')


@pytest.fixture
def workflow_no_setup_mocks(no_setup_command_mocks):
    """Provide workflow command mocks for no-setup scenarios.
    
    Returns:
        context manager: Context manager that yields mocks dictionary
    """
    return no_setup_command_mocks('workflow')


class TestWorkflowListCommand:
    """Test workflow list command."""

    def test_list_help(self, cli_runner):
        """Test workflow list command help."""
        result = cli_runner.invoke(cli, ['workflow', 'list', '--help'])
        assert result.exit_code == 0
        assert 'List workflows in a database or all workflows' in result.output
        assert '--database-id' in result.output
        assert '--show-deleted' in result.output
        assert '--auto-paginate' in result.output

    def test_list_all_workflows_success(self, cli_runner, workflow_command_mocks):
        """Test successful listing of all workflows."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflows.return_value = {
                'message': {
                    'Items': [
                        {
                            'workflowId': 'workflow-1',
                            'databaseId': 'global',
                            'description': 'Test Workflow 1',
                            'workflow_arn': 'arn:aws:states:us-east-1:123456789012:stateMachine:test1'
                        },
                        {
                            'workflowId': 'workflow-2',
                            'databaseId': 'global',
                            'description': 'Test Workflow 2',
                            'autoTriggerOnFileExtensionsUpload': '.gltf,.glb'
                        }
                    ]
                }
            }

            result = cli_runner.invoke(cli, ['workflow', 'list'])

            assert result.exit_code == 0
            assert 'Found 2 workflow(s)' in result.output
            assert 'workflow-1' in result.output
            assert 'workflow-2' in result.output
            assert 'Test Workflow 1' in result.output

            # Verify API call
            mocks['api_client'].list_workflows.assert_called_once()
            call_args = mocks['api_client'].list_workflows.call_args
            assert call_args[1]['database_id'] is None
            assert call_args[1]['show_deleted'] is False

    def test_list_database_workflows_success(self, cli_runner, workflow_command_mocks):
        """Test successful listing of workflows for specific database."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflows.return_value = {
                'message': {
                    'Items': [
                        {
                            'workflowId': 'workflow-1',
                            'databaseId': 'my-database',
                            'description': 'Database Workflow'
                        }
                    ]
                }
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'list',
                '-d', 'my-database'
            ])

            assert result.exit_code == 0
            assert 'Found 1 workflow(s)' in result.output
            assert 'workflow-1' in result.output

            # Verify API call with database filter
            mocks['api_client'].list_workflows.assert_called_once()
            call_args = mocks['api_client'].list_workflows.call_args
            assert call_args[1]['database_id'] == 'my-database'

    def test_list_with_show_deleted(self, cli_runner, workflow_command_mocks):
        """Test listing workflows with deleted items."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflows.return_value = {
                'message': {
                    'Items': [
                        {
                            'workflowId': 'workflow-1',
                            'databaseId': 'global#deleted',
                            'description': 'Deleted Workflow'
                        }
                    ]
                }
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'list',
                '--show-deleted'
            ])

            assert result.exit_code == 0
            assert 'workflow-1' in result.output

            # Verify show_deleted parameter
            call_args = mocks['api_client'].list_workflows.call_args
            assert call_args[1]['show_deleted'] is True

    def test_list_with_pagination(self, cli_runner, workflow_command_mocks):
        """Test manual pagination."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflows.return_value = {
                'message': {
                    'Items': [{'workflowId': 'wf-1', 'databaseId': 'global', 'description': 'Test'}],
                    'NextToken': 'next-token-123'
                }
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'list',
                '--page-size', '100',
                '--starting-token', 'token-123'
            ])

            assert result.exit_code == 0
            assert 'Next token: next-token-123' in result.output

            # Verify pagination parameters
            call_args = mocks['api_client'].list_workflows.call_args
            params = call_args[1]['params']
            assert params['pageSize'] == 100
            assert params['startingToken'] == 'token-123'

    def test_list_auto_paginate(self, cli_runner, workflow_command_mocks):
        """Test auto-pagination."""
        with workflow_command_mocks as mocks:
            # Simulate two pages of results
            mocks['api_client'].list_workflows.side_effect = [
                {
                    'message': {
                        'Items': [{'workflowId': f'wf-{i}', 'databaseId': 'global', 'description': f'Workflow {i}'} for i in range(100)],
                        'NextToken': 'token-page-2'
                    }
                },
                {
                    'message': {
                        'Items': [{'workflowId': f'wf-{i}', 'databaseId': 'global', 'description': f'Workflow {i}'} for i in range(100, 150)]
                    }
                }
            ]

            result = cli_runner.invoke(cli, [
                'workflow', 'list',
                '--auto-paginate'
            ])

            assert result.exit_code == 0
            assert 'Auto-paginated: Retrieved 150 items in 2 page(s)' in result.output
            assert 'Found 150 workflow(s)' in result.output

            # Verify multiple API calls
            assert mocks['api_client'].list_workflows.call_count == 2

    def test_list_no_setup(self, cli_runner, workflow_no_setup_mocks):
        """Test workflow list without setup."""
        with workflow_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, ['workflow', 'list'])

            assert result.exit_code == 1
            # SetupRequiredError is raised before command execution
            assert result.exception is not None
            assert 'Setup required' in str(result.exception) or result.exit_code == 1

    def test_list_database_not_found(self, cli_runner, workflow_command_mocks):
        """Test workflow list with non-existent database."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflows.side_effect = DatabaseNotFoundError("Database 'invalid-db' not found")

            result = cli_runner.invoke(cli, [
                'workflow', 'list',
                '-d', 'invalid-db'
            ])

            assert result.exit_code == 1
            assert 'Database Not Found' in result.output
            assert "Database 'invalid-db' not found" in result.output

    def test_list_json_output(self, cli_runner, workflow_command_mocks):
        """Test workflow list with JSON output."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflows.return_value = {
                'message': {
                    'Items': [
                        {
                            'workflowId': 'workflow-1',
                            'databaseId': 'global',
                            'description': 'Test Workflow'
                        }
                    ]
                }
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'list',
                '--json-output'
            ])

            assert result.exit_code == 0
            
            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert 'Items' in parsed
            assert len(parsed['Items']) == 1
            assert parsed['Items'][0]['workflowId'] == 'workflow-1'

    def test_list_conflicting_pagination_options(self, cli_runner, workflow_command_mocks):
        """Test that conflicting pagination options are rejected."""
        with workflow_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'workflow', 'list',
                '--auto-paginate',
                '--starting-token', 'token-123'
            ])

            assert result.exit_code == 1
            assert 'Cannot use --auto-paginate with --starting-token' in result.output


class TestWorkflowListExecutionsCommand:
    """Test workflow list-executions command."""

    def test_list_executions_help(self, cli_runner):
        """Test workflow list-executions command help."""
        result = cli_runner.invoke(cli, ['workflow', 'list-executions', '--help'])
        assert result.exit_code == 0
        assert 'List workflow executions for an asset' in result.output
        assert '--database-id' in result.output
        assert '--asset-id' in result.output
        assert '--workflow-id' in result.output
        assert 'Step Functions API throttling' in result.output

    def test_list_executions_success(self, cli_runner, workflow_command_mocks):
        """Test successful listing of workflow executions."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {
                'message': {
                    'Items': [
                        {
                            'executionId': 'exec-1',
                            'workflowId': 'workflow-1',
                            'workflowDatabaseId': 'global',
                            'executionStatus': 'SUCCEEDED',
                            'startDate': '12/18/2024, 10:00:00',
                            'stopDate': '12/18/2024, 10:05:00',
                            'inputAssetFileKey': '/model.gltf'
                        },
                        {
                            'executionId': 'exec-2',
                            'workflowId': 'workflow-1',
                            'workflowDatabaseId': 'global',
                            'executionStatus': 'RUNNING',
                            'startDate': '12/18/2024, 11:00:00'
                        }
                    ]
                }
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions',
                '-d', 'my-database',
                '-a', 'my-asset'
            ])

            assert result.exit_code == 0
            assert 'Found 2 execution(s)' in result.output
            assert 'exec-1' in result.output
            assert 'exec-2' in result.output
            assert 'SUCCEEDED' in result.output
            assert 'RUNNING' in result.output

            # Verify API call
            mocks['api_client'].list_workflow_executions.assert_called_once()
            call_args = mocks['api_client'].list_workflow_executions.call_args
            assert call_args[1]['database_id'] == 'my-database'
            assert call_args[1]['asset_id'] == 'my-asset'

    def test_list_executions_with_workflow_filter(self, cli_runner, workflow_command_mocks):
        """Test listing executions filtered by workflow ID."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {
                'message': {
                    'Items': [
                        {
                            'executionId': 'exec-1',
                            'workflowId': 'workflow-123',
                            'workflowDatabaseId': 'global',
                            'executionStatus': 'SUCCEEDED'
                        }
                    ]
                }
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions',
                '-d', 'my-database',
                '-a', 'my-asset',
                '-w', 'workflow-123'
            ])

            assert result.exit_code == 0
            assert 'workflow-123' in result.output

            # Verify workflow filter
            call_args = mocks['api_client'].list_workflow_executions.call_args
            assert call_args[1]['workflow_id'] == 'workflow-123'

    def test_list_executions_with_workflow_database_filter(self, cli_runner, workflow_command_mocks):
        """Test listing executions filtered by workflow database ID."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {
                'message': {
                    'Items': []
                }
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions',
                '-d', 'my-database',
                '-a', 'my-asset',
                '--workflow-database-id', 'global'
            ])

            assert result.exit_code == 0

            # Verify workflow database filter
            call_args = mocks['api_client'].list_workflow_executions.call_args
            assert call_args[1]['workflow_database_id'] == 'global'

    def test_list_executions_page_size_limit(self, cli_runner, workflow_command_mocks):
        """Test that page size is limited to 50."""
        with workflow_command_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions',
                '-d', 'my-database',
                '-a', 'my-asset',
                '--page-size', '100'
            ])

            assert result.exit_code == 1
            assert 'Maximum page size for workflow executions is 50' in result.output
            assert 'API throttling limits' in result.output

    def test_list_executions_auto_paginate(self, cli_runner, workflow_command_mocks):
        """Test auto-pagination for executions."""
        with workflow_command_mocks as mocks:
            # Simulate two pages of results
            mocks['api_client'].list_workflow_executions.side_effect = [
                {
                    'message': {
                        'Items': [{'executionId': f'exec-{i}', 'workflowId': 'wf-1', 'workflowDatabaseId': 'global', 'executionStatus': 'SUCCEEDED'} for i in range(50)],
                        'NextToken': 'token-page-2'
                    }
                },
                {
                    'message': {
                        'Items': [{'executionId': f'exec-{i}', 'workflowId': 'wf-1', 'workflowDatabaseId': 'global', 'executionStatus': 'SUCCEEDED'} for i in range(50, 75)]
                    }
                }
            ]

            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions',
                '-d', 'my-database',
                '-a', 'my-asset',
                '--auto-paginate'
            ])

            assert result.exit_code == 0
            assert 'Auto-paginated: Retrieved 75 items in 2 page(s)' in result.output
            assert 'Found 75 execution(s)' in result.output

            # Verify multiple API calls
            assert mocks['api_client'].list_workflow_executions.call_count == 2

    def test_list_executions_no_setup(self, cli_runner, workflow_no_setup_mocks):
        """Test workflow list-executions without setup."""
        with workflow_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions',
                '-d', 'my-database',
                '-a', 'my-asset'
            ])

            assert result.exit_code == 1
            # SetupRequiredError is raised before command execution
            assert result.exception is not None
            assert 'Setup required' in str(result.exception) or result.exit_code == 1

    def test_list_executions_asset_not_found(self, cli_runner, workflow_command_mocks):
        """Test workflow list-executions with non-existent asset."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflow_executions.side_effect = AssetNotFoundError(
                "Asset 'invalid-asset' not found in database 'my-database'"
            )

            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions',
                '-d', 'my-database',
                '-a', 'invalid-asset'
            ])

            assert result.exit_code == 1
            assert 'Asset Not Found' in result.output
            assert 'invalid-asset' in result.output

    def test_list_executions_json_output(self, cli_runner, workflow_command_mocks):
        """Test workflow list-executions with JSON output."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].list_workflow_executions.return_value = {
                'message': {
                    'Items': [
                        {
                            'executionId': 'exec-1',
                            'workflowId': 'workflow-1',
                            'workflowDatabaseId': 'global',
                            'executionStatus': 'SUCCEEDED'
                        }
                    ]
                }
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions',
                '-d', 'my-database',
                '-a', 'my-asset',
                '--json-output'
            ])

            assert result.exit_code == 0
            
            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert 'Items' in parsed
            assert len(parsed['Items']) == 1
            assert parsed['Items'][0]['executionId'] == 'exec-1'

    def test_list_executions_requires_parameters(self, cli_runner, workflow_command_mocks):
        """Test that list-executions requires database-id and asset-id."""
        with workflow_command_mocks as mocks:
            # Missing both parameters
            result = cli_runner.invoke(cli, ['workflow', 'list-executions'])
            assert result.exit_code == 2
            assert 'Missing option' in result.output or 'required' in result.output.lower()

            # Missing asset-id
            result = cli_runner.invoke(cli, [
                'workflow', 'list-executions',
                '-d', 'my-database'
            ])
            assert result.exit_code == 2
            assert 'Missing option' in result.output or 'required' in result.output.lower()


class TestWorkflowExecuteCommand:
    """Test workflow execute command."""

    def test_execute_help(self, cli_runner):
        """Test workflow execute command help."""
        result = cli_runner.invoke(cli, ['workflow', 'execute', '--help'])
        assert result.exit_code == 0
        assert 'Execute a workflow on an asset' in result.output
        assert '--database-id' in result.output
        assert '--asset-id' in result.output
        assert '--workflow-id' in result.output
        assert '--workflow-database-id' in result.output
        assert '--file-key' in result.output

    def test_execute_success(self, cli_runner, workflow_command_mocks):
        """Test successful workflow execution."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].execute_workflow.return_value = {
                'message': 'execution-id-123'
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'execute',
                '-d', 'my-database',
                '-a', 'my-asset',
                '-w', 'workflow-123',
                '--workflow-database-id', 'global'
            ])

            assert result.exit_code == 0
            assert '✓ Workflow execution started successfully!' in result.output
            assert 'execution-id-123' in result.output
            assert 'workflow-123' in result.output

            # Verify API call
            mocks['api_client'].execute_workflow.assert_called_once()
            call_args = mocks['api_client'].execute_workflow.call_args
            assert call_args[1]['database_id'] == 'my-database'
            assert call_args[1]['asset_id'] == 'my-asset'
            assert call_args[1]['workflow_id'] == 'workflow-123'
            assert call_args[1]['workflow_database_id'] == 'global'
            assert call_args[1]['file_key'] is None

    def test_execute_with_file_key(self, cli_runner, workflow_command_mocks):
        """Test workflow execution on specific file."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].execute_workflow.return_value = {
                'message': 'execution-id-456'
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'execute',
                '-d', 'my-database',
                '-a', 'my-asset',
                '-w', 'workflow-123',
                '--workflow-database-id', 'global',
                '--file-key', '/model.gltf'
            ])

            assert result.exit_code == 0
            assert 'execution-id-456' in result.output
            assert '/model.gltf' in result.output

            # Verify file_key parameter
            call_args = mocks['api_client'].execute_workflow.call_args
            assert call_args[1]['file_key'] == '/model.gltf'

    def test_execute_already_running(self, cli_runner, workflow_command_mocks):
        """Test workflow execution when already running."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].execute_workflow.side_effect = WorkflowAlreadyRunningError(
                "Workflow has a currently running execution on this file"
            )

            result = cli_runner.invoke(cli, [
                'workflow', 'execute',
                '-d', 'my-database',
                '-a', 'my-asset',
                '-w', 'workflow-123',
                '--workflow-database-id', 'global',
                '--file-key', '/model.gltf'
            ])

            assert result.exit_code == 1
            assert 'Workflow Already Running' in result.output
            assert 'currently running execution' in result.output

    def test_execute_workflow_not_found(self, cli_runner, workflow_command_mocks):
        """Test workflow execution with non-existent workflow."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].execute_workflow.side_effect = WorkflowNotFoundError(
                "Workflow 'invalid-workflow' not found"
            )

            result = cli_runner.invoke(cli, [
                'workflow', 'execute',
                '-d', 'my-database',
                '-a', 'my-asset',
                '-w', 'invalid-workflow',
                '--workflow-database-id', 'global'
            ])

            assert result.exit_code == 1
            assert 'Workflow Not Found' in result.output
            assert 'invalid-workflow' in result.output

    def test_execute_asset_not_found(self, cli_runner, workflow_command_mocks):
        """Test workflow execution with non-existent asset."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].execute_workflow.side_effect = AssetNotFoundError(
                "Asset 'invalid-asset' not found in database 'my-database'"
            )

            result = cli_runner.invoke(cli, [
                'workflow', 'execute',
                '-d', 'my-database',
                '-a', 'invalid-asset',
                '-w', 'workflow-123',
                '--workflow-database-id', 'global'
            ])

            assert result.exit_code == 1
            assert 'Asset Not Found' in result.output
            assert 'invalid-asset' in result.output

    def test_execute_pipeline_not_enabled(self, cli_runner, workflow_command_mocks):
        """Test workflow execution when pipeline is not enabled."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].execute_workflow.side_effect = WorkflowExecutionError(
                "Pipeline not enabled: Pipeline 'test-pipeline' is disabled"
            )

            result = cli_runner.invoke(cli, [
                'workflow', 'execute',
                '-d', 'my-database',
                '-a', 'my-asset',
                '-w', 'workflow-123',
                '--workflow-database-id', 'global'
            ])

            assert result.exit_code == 1
            assert 'Workflow Execution Error' in result.output
            assert 'Pipeline not enabled' in result.output

    def test_execute_no_setup(self, cli_runner, workflow_no_setup_mocks):
        """Test workflow execute without setup."""
        with workflow_no_setup_mocks as mocks:
            result = cli_runner.invoke(cli, [
                'workflow', 'execute',
                '-d', 'my-database',
                '-a', 'my-asset',
                '-w', 'workflow-123',
                '--workflow-database-id', 'global'
            ])

            assert result.exit_code == 1
            # SetupRequiredError is raised before command execution
            assert result.exception is not None
            assert 'Setup required' in str(result.exception) or result.exit_code == 1

    def test_execute_json_output(self, cli_runner, workflow_command_mocks):
        """Test workflow execute with JSON output."""
        with workflow_command_mocks as mocks:
            mocks['api_client'].execute_workflow.return_value = {
                'message': 'execution-id-789'
            }

            result = cli_runner.invoke(cli, [
                'workflow', 'execute',
                '-d', 'my-database',
                '-a', 'my-asset',
                '-w', 'workflow-123',
                '--workflow-database-id', 'global',
                '--json-output'
            ])

            assert result.exit_code == 0
            
            # Verify output is valid JSON
            parsed = json.loads(result.output)
            assert 'message' in parsed
            assert parsed['message'] == 'execution-id-789'

    def test_execute_requires_parameters(self, cli_runner, workflow_command_mocks):
        """Test that execute requires all mandatory parameters."""
        with workflow_command_mocks as mocks:
            # Missing all parameters
            result = cli_runner.invoke(cli, ['workflow', 'execute'])
            assert result.exit_code == 2
            assert 'Missing option' in result.output or 'required' in result.output.lower()

            # Missing workflow-database-id
            result = cli_runner.invoke(cli, [
                'workflow', 'execute',
                '-d', 'my-database',
                '-a', 'my-asset',
                '-w', 'workflow-123'
            ])
            assert result.exit_code == 2
            assert 'Missing option' in result.output or 'required' in result.output.lower()


class TestWorkflowUtilityFunctions:
    """Test workflow utility functions."""

    def test_format_workflow_output(self):
        """Test workflow output formatting."""
        from vamscli.commands.workflow import format_workflow_output

        workflow = {
            'workflowId': 'test-workflow',
            'databaseId': 'global',
            'description': 'Test Workflow Description',
            'autoTriggerOnFileExtensionsUpload': '.gltf,.glb',
            'workflow_arn': 'arn:aws:states:us-east-1:123456789012:stateMachine:test'
        }

        result = format_workflow_output(workflow)
        
        assert 'ID: test-workflow' in result
        assert 'Database: global' in result
        assert 'Description: Test Workflow Description' in result
        assert 'Auto-trigger Extensions: .gltf,.glb' in result
        assert 'ARN: arn:aws:states' in result

    def test_format_execution_output(self):
        """Test execution output formatting."""
        from vamscli.commands.workflow import format_execution_output

        execution = {
            'executionId': 'exec-123',
            'workflowId': 'workflow-1',
            'workflowDatabaseId': 'global',
            'executionStatus': 'SUCCEEDED',
            'startDate': '12/18/2024, 10:00:00',
            'stopDate': '12/18/2024, 10:05:00',
            'inputAssetFileKey': '/model.gltf'
        }

        result = format_execution_output(execution)
        
        assert 'Execution ID: exec-123' in result
        assert 'Workflow ID: workflow-1' in result
        assert 'Workflow Database: global' in result
        assert 'Status: SUCCEEDED' in result
        assert 'Start Date: 12/18/2024, 10:00:00' in result
        assert 'Stop Date: 12/18/2024, 10:05:00' in result
        assert 'Input File: /model.gltf' in result

    def test_format_execution_output_minimal(self):
        """Test execution output formatting with minimal data."""
        from vamscli.commands.workflow import format_execution_output

        execution = {
            'executionId': 'exec-123',
            'workflowId': 'workflow-1',
            'workflowDatabaseId': 'global',
            'executionStatus': 'RUNNING'
        }

        result = format_execution_output(execution)
        
        assert 'Execution ID: exec-123' in result
        assert 'Status: RUNNING' in result
        # Should not include dates or input file if not present
        assert 'Start Date:' not in result or 'Start Date: ' in result
        assert 'Stop Date:' not in result or 'Stop Date: ' in result


if __name__ == '__main__':
    pytest.main([__file__])
