"""Workflow management commands for VamsCLI."""

import json
from typing import Dict, Any, Optional

import click

from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error, output_warning
from ..utils.exceptions import (
    WorkflowNotFoundError, WorkflowExecutionError, WorkflowAlreadyRunningError,
    InvalidWorkflowDataError, AssetNotFoundError, DatabaseNotFoundError
)


def format_workflow_output(workflow: Dict[str, Any]) -> str:
    """Format a single workflow for CLI display."""
    lines = []
    lines.append(f"ID: {workflow.get('workflowId', 'N/A')}")
    lines.append(f"Database: {workflow.get('databaseId', 'N/A')}")
    lines.append(f"Description: {workflow.get('description', 'N/A')}")
    
    # Auto-trigger extensions
    auto_trigger = workflow.get('autoTriggerOnFileExtensionsUpload', '')
    if auto_trigger:
        lines.append(f"Auto-trigger Extensions: {auto_trigger}")
    
    # Workflow ARN
    workflow_arn = workflow.get('workflow_arn', '')
    if workflow_arn:
        lines.append(f"ARN: {workflow_arn}")
    
    return '\n'.join(lines)


def format_execution_output(execution: Dict[str, Any]) -> str:
    """Format a single workflow execution for CLI display."""
    lines = []
    lines.append(f"Execution ID: {execution.get('executionId', 'N/A')}")
    lines.append(f"Workflow ID: {execution.get('workflowId', 'N/A')}")
    lines.append(f"Workflow Database: {execution.get('workflowDatabaseId', 'N/A')}")
    lines.append(f"Status: {execution.get('executionStatus', 'N/A')}")
    
    start_date = execution.get('startDate', '')
    if start_date:
        lines.append(f"Start Date: {start_date}")
    
    stop_date = execution.get('stopDate', '')
    if stop_date:
        lines.append(f"Stop Date: {stop_date}")
    
    input_file = execution.get('inputAssetFileKey', '')
    if input_file:
        lines.append(f"Input File: {input_file}")
    
    return '\n'.join(lines)


@click.group()
def workflow():
    """Workflow management commands."""
    pass


@workflow.command()
@click.option('-d', '--database-id', help='Database ID to list workflows from (optional for all workflows)')
@click.option('--show-deleted', is_flag=True, help='Include deleted workflows')
@click.option('--page-size', type=int, help='Number of items per page')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default: 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, database_id: Optional[str], show_deleted: bool, page_size: int,
         max_items: int, starting_token: str, auto_paginate: bool, json_output: bool):
    """
    List workflows in a database or all workflows.
    
    This command lists workflows from a specific database or all workflows across
    all databases if no database ID is specified.
    
    Examples:
        # Basic listing (uses API defaults)
        vamscli workflow list -d my-database
        
        # Auto-pagination to fetch all items (default: up to 10,000)
        vamscli workflow list -d my-database --auto-paginate
        
        # Auto-pagination with custom limit
        vamscli workflow list -d my-database --auto-paginate --max-items 5000
        
        # Auto-pagination with custom page size
        vamscli workflow list -d my-database --auto-paginate --page-size 500
        
        # Manual pagination with page size
        vamscli workflow list -d my-database --page-size 200
        vamscli workflow list -d my-database --starting-token "token123" --page-size 200
        
        # With filters
        vamscli workflow list -d my-database --show-deleted
        vamscli workflow list --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Validate pagination options
        if auto_paginate and starting_token:
            raise click.ClickException(
                "Cannot use --auto-paginate with --starting-token. "
                "Use --auto-paginate for automatic pagination, or --starting-token for manual pagination."
            )
        
        # Warn if max-items used without auto-paginate
        if max_items and not auto_paginate:
            output_warning("--max-items only applies with --auto-paginate. Ignoring --max-items.", json_output)
            max_items = None
        
        if database_id:
            status_msg = f"Listing workflows in database '{database_id}'..."
        else:
            status_msg = "Listing all workflows..."
        
        if auto_paginate:
            # Auto-pagination mode: fetch all items up to max_items (default 10,000)
            max_total_items = max_items or 10000
            output_status(f"{status_msg[:-3]} (auto-paginating up to {max_total_items} items)...", json_output)
            
            all_items = []
            next_token = None
            total_fetched = 0
            page_count = 0
            
            while True:
                page_count += 1
                
                # Prepare query parameters for this page
                params = {}
                if page_size:
                    params['pageSize'] = page_size
                if next_token:
                    params['startingToken'] = next_token
                
                # Make API call
                page_result = api_client.list_workflows(
                    database_id=database_id,
                    show_deleted=show_deleted,
                    params=params
                )
                
                # Extract items from message wrapper
                message = page_result.get('message', {})
                items = message.get('Items', [])
                all_items.extend(items)
                total_fetched += len(items)
                
                # Show progress in CLI mode
                if not json_output:
                    output_status(f"Fetched {total_fetched} workflows (page {page_count})...", False)
                
                # Check if we should continue
                next_token = message.get('NextToken')
                if not next_token or total_fetched >= max_total_items:
                    break
            
            # Create final result
            result = {
                'Items': all_items,
                'totalItems': len(all_items),
                'autoPaginated': True,
                'pageCount': page_count
            }
            
            if total_fetched >= max_total_items and next_token:
                result['note'] = f"Reached maximum of {max_total_items} items. More items may be available."
            
        else:
            # Manual pagination mode: single API call
            output_status(status_msg, json_output)
            
            # Prepare query parameters
            params = {}
            if page_size:
                params['pageSize'] = page_size
            if starting_token:
                params['startingToken'] = starting_token
            
            # Get the workflows
            api_result = api_client.list_workflows(
                database_id=database_id,
                show_deleted=show_deleted,
                params=params
            )
            
            # Extract from message wrapper
            result = api_result.get('message', {})
        
        def format_workflows_list(data):
            """Format workflows list for CLI display."""
            items = data.get('Items', [])
            if not items:
                return "No workflows found."
            
            lines = []
            
            # Show auto-pagination info if present
            if data.get('autoPaginated'):
                lines.append(f"\nAuto-paginated: Retrieved {data.get('totalItems', 0)} items in {data.get('pageCount', 0)} page(s)")
                if data.get('note'):
                    lines.append(f"⚠️  {data['note']}")
                lines.append("")
            
            lines.append(f"Found {len(items)} workflow(s):")
            lines.append("-" * 80)
            
            for wf in items:
                lines.append(format_workflow_output(wf))
                lines.append("-" * 80)
            
            # Show nextToken for manual pagination
            if not data.get('autoPaginated') and data.get('NextToken'):
                lines.append(f"\nNext token: {data['NextToken']}")
                lines.append("Use --starting-token to get the next page")
            
            return '\n'.join(lines)
        
        output_result(result, json_output, cli_formatter=format_workflows_list)
        
        return result
        
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))


@workflow.command('list-executions')
@click.option('-d', '--database-id', required=True, help='Database ID containing the asset')
@click.option('-a', '--asset-id', required=True, help='Asset ID to list executions for')
@click.option('-w', '--workflow-id', help='Filter by specific workflow ID')
@click.option('--workflow-database-id', help='Workflow\'s database ID (for filtering)')
@click.option('--page-size', type=int, help='Number of items per page (max 50 due to API throttling)')
@click.option('--max-items', type=int, help='Maximum total items to fetch (only with --auto-paginate, default: 10000)')
@click.option('--starting-token', help='Token for pagination (manual pagination)')
@click.option('--auto-paginate', is_flag=True, help='Automatically fetch all items')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list_executions(ctx: click.Context, database_id: str, asset_id: str, workflow_id: Optional[str],
                   workflow_database_id: Optional[str], page_size: int, max_items: int,
                   starting_token: str, auto_paginate: bool, json_output: bool):
    """
    List workflow executions for an asset.
    
    This command lists workflow executions for a specific asset. You can optionally
    filter by a specific workflow ID.
    
    Note: Due to Step Functions API throttling, page size is limited to 50 items.
    Use --auto-paginate to fetch more items across multiple pages.
    
    Examples:
        # List all executions for an asset
        vamscli workflow list-executions -d my-db -a my-asset
        
        # Filter by specific workflow
        vamscli workflow list-executions -d my-db -a my-asset -w workflow-123
        
        # Filter by workflow database
        vamscli workflow list-executions -d my-db -a my-asset --workflow-database-id global
        
        # Auto-pagination to fetch all executions
        vamscli workflow list-executions -d my-db -a my-asset --auto-paginate
        
        # Manual pagination with custom page size
        vamscli workflow list-executions -d my-db -a my-asset --page-size 25
        vamscli workflow list-executions -d my-db -a my-asset --starting-token "token123"
        
        # JSON output
        vamscli workflow list-executions -d my-db -a my-asset --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        # Validate pagination options
        if auto_paginate and starting_token:
            raise click.ClickException(
                "Cannot use --auto-paginate with --starting-token. "
                "Use --auto-paginate for automatic pagination, or --starting-token for manual pagination."
            )
        
        # Warn if max-items used without auto-paginate
        if max_items and not auto_paginate:
            output_warning("--max-items only applies with --auto-paginate. Ignoring --max-items.", json_output)
            max_items = None
        
        # Validate page size (API limit is 50 due to Step Functions throttling)
        if page_size and page_size > 50:
            raise click.ClickException(
                "Maximum page size for workflow executions is 50 due to API throttling limits. "
                "Use --auto-paginate to fetch more items across multiple pages."
            )
        
        # Set default page size to 50 if not specified
        if not page_size:
            page_size = 50
        
        status_msg = f"Listing workflow executions for asset '{asset_id}' in database '{database_id}'..."
        
        if auto_paginate:
            # Auto-pagination mode: fetch all items up to max_items (default 10,000)
            max_total_items = max_items or 10000
            output_status(f"{status_msg[:-3]} (auto-paginating up to {max_total_items} items)...", json_output)
            
            all_items = []
            next_token = None
            total_fetched = 0
            page_count = 0
            
            while True:
                page_count += 1
                
                # Prepare query parameters for this page
                params = {
                    'pageSize': page_size  # Always use page_size (max 50)
                }
                if next_token:
                    params['startingToken'] = next_token
                
                # Make API call
                page_result = api_client.list_workflow_executions(
                    database_id=database_id,
                    asset_id=asset_id,
                    workflow_database_id=workflow_database_id,
                    workflow_id=workflow_id,
                    params=params
                )
                
                # Extract items from message wrapper
                message = page_result.get('message', {})
                items = message.get('Items', [])
                all_items.extend(items)
                total_fetched += len(items)
                
                # Show progress in CLI mode
                if not json_output:
                    output_status(f"Fetched {total_fetched} executions (page {page_count})...", False)
                
                # Check if we should continue
                next_token = message.get('NextToken')
                if not next_token or total_fetched >= max_total_items:
                    break
            
            # Create final result
            result = {
                'Items': all_items,
                'totalItems': len(all_items),
                'autoPaginated': True,
                'pageCount': page_count
            }
            
            if total_fetched >= max_total_items and next_token:
                result['note'] = f"Reached maximum of {max_total_items} items. More items may be available."
            
        else:
            # Manual pagination mode: single API call
            output_status(status_msg, json_output)
            
            # Prepare query parameters
            params = {
                'pageSize': page_size  # Always use page_size (max 50)
            }
            if starting_token:
                params['startingToken'] = starting_token
            
            # Get the workflow executions
            api_result = api_client.list_workflow_executions(
                database_id=database_id,
                asset_id=asset_id,
                workflow_database_id=workflow_database_id,
                workflow_id=workflow_id,
                params=params
            )
            
            # Extract from message wrapper
            result = api_result.get('message', {})
        
        def format_executions_list(data):
            """Format workflow executions list for CLI display."""
            items = data.get('Items', [])
            if not items:
                return "No workflow executions found."
            
            lines = []
            
            # Show auto-pagination info if present
            if data.get('autoPaginated'):
                lines.append(f"\nAuto-paginated: Retrieved {data.get('totalItems', 0)} items in {data.get('pageCount', 0)} page(s)")
                if data.get('note'):
                    lines.append(f"⚠️  {data['note']}")
                lines.append("")
            
            lines.append(f"Found {len(items)} execution(s):")
            lines.append("-" * 80)
            
            for execution in items:
                lines.append(format_execution_output(execution))
                lines.append("-" * 80)
            
            # Show nextToken for manual pagination
            if not data.get('autoPaginated') and data.get('NextToken'):
                lines.append(f"\nNext token: {data['NextToken']}")
                lines.append("Use --starting-token to get the next page")
            
            return '\n'.join(lines)
        
        output_result(result, json_output, cli_formatter=format_executions_list)
        
        return result
        
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=f"Use 'vamscli assets get -d {database_id} {asset_id}' to check if the asset exists."
        )
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))


@workflow.command()
@click.option('-d', '--database-id', required=True, help='Database ID containing the asset')
@click.option('-a', '--asset-id', required=True, help='Asset ID to execute workflow on')
@click.option('-w', '--workflow-id', required=True, help='Workflow ID to execute')
@click.option('--workflow-database-id', required=True, help='Workflow\'s database ID')
@click.option('--file-key', help='Specific file key to run workflow on (optional)')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def execute(ctx: click.Context, database_id: str, asset_id: str, workflow_id: str,
           workflow_database_id: str, file_key: Optional[str], json_output: bool):
    """
    Execute a workflow on an asset.
    
    This command executes a workflow on a specific asset. You can optionally
    specify a file key to run the workflow on a specific file within the asset.
    
    The workflow must be enabled and all its pipelines must be accessible.
    The command will check if the workflow is already running on the specified
    file and prevent duplicate executions.
    
    Examples:
        # Execute workflow on entire asset
        vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global
        
        # Execute workflow on specific file
        vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global --file-key "/model.gltf"
        
        # Execute with JSON output
        vamscli workflow execute -d my-db -a my-asset -w workflow-123 --workflow-database-id global --json-output
    """
    # Setup/auth already validated by decorator
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    api_client = APIClient(config['api_gateway_url'], profile_manager)
    
    try:
        if file_key:
            output_status(f"Executing workflow '{workflow_id}' on file '{file_key}' in asset '{asset_id}'...", json_output)
        else:
            output_status(f"Executing workflow '{workflow_id}' on asset '{asset_id}'...", json_output)
        
        # Execute the workflow
        result = api_client.execute_workflow(
            database_id=database_id,
            asset_id=asset_id,
            workflow_id=workflow_id,
            workflow_database_id=workflow_database_id,
            file_key=file_key
        )
        
        def format_execute_result(data):
            """Format workflow execution result for CLI display."""
            lines = []
            
            # Extract execution ID from message
            execution_id = data.get('message', 'N/A')
            
            lines.append(f"Execution ID: {execution_id}")
            lines.append(f"Workflow ID: {workflow_id}")
            lines.append(f"Workflow Database: {workflow_database_id}")
            lines.append(f"Asset ID: {asset_id}")
            lines.append(f"Database ID: {database_id}")
            
            if file_key:
                lines.append(f"File Key: {file_key}")
            
            lines.append("")
            lines.append("The workflow has been started successfully.")
            lines.append(f"Use 'vamscli workflow list-executions -d {database_id} -a {asset_id}' to check execution status.")
            
            return '\n'.join(lines)
        
        output_result(
            result,
            json_output,
            success_message="✓ Workflow execution started successfully!",
            cli_formatter=format_execute_result
        )
        
        return result
        
    except WorkflowNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Workflow Not Found",
            helpful_message=f"Use 'vamscli workflow list -d {workflow_database_id}' to see available workflows."
        )
        raise click.ClickException(str(e))
    except WorkflowAlreadyRunningError as e:
        output_error(
            e,
            json_output,
            error_type="Workflow Already Running",
            helpful_message=f"Use 'vamscli workflow list-executions -d {database_id} -a {asset_id}' to check execution status."
        )
        raise click.ClickException(str(e))
    except WorkflowExecutionError as e:
        output_error(
            e,
            json_output,
            error_type="Workflow Execution Error",
            helpful_message="Ensure all pipelines in the workflow are enabled and accessible."
        )
        raise click.ClickException(str(e))
    except AssetNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Asset Not Found",
            helpful_message=f"Use 'vamscli assets get -d {database_id} {asset_id}' to check if the asset exists."
        )
        raise click.ClickException(str(e))
    except DatabaseNotFoundError as e:
        output_error(
            e,
            json_output,
            error_type="Database Not Found",
            helpful_message="Use 'vamscli database list' to see available databases."
        )
        raise click.ClickException(str(e))
    except InvalidWorkflowDataError as e:
        output_error(
            e,
            json_output,
            error_type="Invalid Workflow Data",
            helpful_message="Check your workflow parameters and try again."
        )
        raise click.ClickException(str(e))
