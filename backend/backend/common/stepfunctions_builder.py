"""
Amazon States Language (ASL) Builder for Step Functions

This module provides utilities to manually build AWS Step Functions state machine
definitions in Amazon States Language (ASL) format, eliminating the need for the
heavy stepfunctions library dependency.

Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import json
from typing import Dict, List, Optional, Any, Tuple


def create_lambda_task_state(
    state_id: str,
    function_name: str,
    payload: Dict[str, Any],
    result_path: Optional[str] = None,
    wait_for_callback: bool = False,
    timeout_seconds: Optional[int] = None,
    heartbeat_seconds: Optional[int] = None,
    retry_config: Optional[Dict[str, Any]] = None,
    catch_config: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Create a Lambda task state in ASL format.
    
    Args:
        state_id: Unique identifier for this state
        function_name: Name of the Lambda function to invoke
        payload: Payload to pass to the Lambda function
        result_path: JSONPath to store the result (e.g., "$.stepName.output")
        wait_for_callback: If True, use waitForTaskToken pattern
        timeout_seconds: Timeout for callback tasks
        heartbeat_seconds: Heartbeat timeout for callback tasks
        retry_config: Retry configuration dictionary
        catch_config: List of catch configurations
        
    Returns:
        Dictionary representing the Lambda task state in ASL format
    """
    # Choose the appropriate resource ARN based on callback requirement
    if wait_for_callback:
        resource = "arn:aws:states:::lambda:invoke.waitForTaskToken"
    else:
        resource = "arn:aws:states:::lambda:invoke"
    
    state = {
        "Type": "Task",
        "Resource": resource,
        "Parameters": {
            "FunctionName": function_name,
            "Payload": payload
        }
    }
    
    # Add result path if specified
    if result_path:
        state["ResultPath"] = result_path
    
    # Add timeout for callback tasks
    if wait_for_callback and timeout_seconds:
        state["TimeoutSeconds"] = timeout_seconds
    
    # Add heartbeat for callback tasks
    if wait_for_callback and heartbeat_seconds:
        state["HeartbeatSeconds"] = heartbeat_seconds
    
    # Add retry configuration
    if retry_config:
        state["Retry"] = [retry_config]
    
    # Add catch configuration
    if catch_config:
        state["Catch"] = catch_config
    
    return state


def create_fail_state(
    state_id: str,
    cause: str,
    error: str = "States.TaskFailed"
) -> Dict[str, Any]:
    """
    Create a Fail state in ASL format.
    
    Args:
        state_id: Unique identifier for this state
        cause: Human-readable description of the failure
        error: Error code/name
        
    Returns:
        Dictionary representing the Fail state in ASL format
    """
    return {
        "Type": "Fail",
        "Cause": cause,
        "Error": error
    }


def create_retry_config(
    error_equals: List[str] = None,
    interval_seconds: int = 5,
    backoff_rate: float = 2.0,
    max_attempts: int = 2
) -> Dict[str, Any]:
    """
    Create a retry configuration for task states.
    
    Args:
        error_equals: List of error names to retry on (default: ["States.ALL"])
        interval_seconds: Initial retry interval in seconds
        backoff_rate: Multiplier for retry interval on each attempt
        max_attempts: Maximum number of retry attempts
        
    Returns:
        Dictionary representing retry configuration
    """
    if error_equals is None:
        error_equals = ["States.ALL"]
    
    return {
        "ErrorEquals": error_equals,
        "IntervalSeconds": interval_seconds,
        "BackoffRate": backoff_rate,
        "MaxAttempts": max_attempts
    }


def create_catch_config(
    error_equals: List[str],
    next_state: str,
    result_path: Optional[str] = None
) -> Dict[str, Any]:
    """
    Create a catch configuration for task states.
    
    Args:
        error_equals: List of error names to catch
        next_state: Name of the state to transition to on error
        result_path: JSONPath to store error information
        
    Returns:
        Dictionary representing catch configuration
    """
    catch = {
        "ErrorEquals": error_equals,
        "Next": next_state
    }
    
    if result_path:
        catch["ResultPath"] = result_path
    
    return catch


def create_workflow_definition(
    states: List[Tuple[str, Dict[str, Any]]],
    comment: str = "VAMS Pipeline Workflow"
) -> Dict[str, Any]:
    """
    Create a complete ASL workflow definition from a list of states.
    
    Args:
        states: List of tuples (state_name, state_definition)
        comment: Description of the workflow
        
    Returns:
        Complete ASL workflow definition dictionary
    """
    if not states:
        raise ValueError("At least one state is required")
    
    states_dict = {}
    start_state_name = states[0][0]
    
    # Build states dictionary with proper transitions
    for i, (state_name, state_def) in enumerate(states):
        # Copy state definition to avoid modifying original
        state = dict(state_def)
        
        # Add Next pointer for all states except the last one
        # (unless the state already has Next, End, or is a Fail state)
        if i < len(states) - 1:
            if "Next" not in state and "End" not in state and state.get("Type") != "Fail":
                next_state_name = states[i + 1][0]
                state["Next"] = next_state_name
        else:
            # Last state should end (unless it's a Fail state or already has End/Next)
            if state.get("Type") != "Fail" and "Next" not in state and "End" not in state:
                state["End"] = True
        
        states_dict[state_name] = state
    
    return {
        "Comment": comment,
        "StartAt": start_state_name,
        "States": states_dict
    }


def create_state_machine(
    sf_client,
    name: str,
    definition: Dict[str, Any],
    role_arn: str,
    log_group_arn: str,
    state_machine_type: str = "STANDARD"
) -> str:
    """
    Create a Step Functions state machine using boto3 directly.
    
    Args:
        sf_client: boto3 Step Functions client
        name: Name for the state machine
        definition: ASL definition dictionary
        role_arn: ARN of the IAM role for the state machine
        log_group_arn: ARN of the CloudWatch log group
        state_machine_type: Type of state machine (STANDARD or EXPRESS)
        
    Returns:
        ARN of the created state machine
    """
    definition_json = json.dumps(definition, indent=2)
    
    response = sf_client.create_state_machine(
        name=name,
        definition=definition_json,
        roleArn=role_arn,
        type=state_machine_type,
        loggingConfiguration={
            'level': 'ALL',
            'includeExecutionData': True,
            'destinations': [{
                'cloudWatchLogsLogGroup': {
                    'logGroupArn': log_group_arn
                }
            }]
        },
        tracingConfiguration={
            'enabled': True
        }
    )
    
    return response['stateMachineArn']


def update_state_machine(
    sf_client,
    state_machine_arn: str,
    definition: Dict[str, Any],
    role_arn: str,
    log_group_arn: str
) -> None:
    """
    Update an existing Step Functions state machine.
    
    Args:
        sf_client: boto3 Step Functions client
        state_machine_arn: ARN of the state machine to update
        definition: New ASL definition dictionary
        role_arn: ARN of the IAM role for the state machine
        log_group_arn: ARN of the CloudWatch log group
    """
    definition_json = json.dumps(definition, indent=2)
    
    sf_client.update_state_machine(
        stateMachineArn=state_machine_arn,
        definition=definition_json,
        roleArn=role_arn,
        loggingConfiguration={
            'level': 'ALL',
            'includeExecutionData': True,
            'destinations': [{
                'cloudWatchLogsLogGroup': {
                    'logGroupArn': log_group_arn
                }
            }]
        },
        tracingConfiguration={
            'enabled': True
        }
    )


def format_s3_uri_with_states_format(
    bucket_param: str,
    path_template: str,
    execution_name_placeholder: str = "$$.Execution.Name"
) -> str:
    """
    Create a States.Format expression for dynamic S3 URIs.
    
    Args:
        bucket_param: JSONPath to the bucket name (e.g., "$.bucketAsset")
        path_template: Path template with {} placeholder for execution name
        execution_name_placeholder: JSONPath for execution name
        
    Returns:
        States.Format expression string
    """
    return f"States.Format('s3://{{}}/" + path_template + f"', {bucket_param}, {execution_name_placeholder})"


# ---------------------------------------------------------------------------
# Builder pattern for multi-type pipeline ASL generation
# ---------------------------------------------------------------------------

from abc import ABC, abstractmethod


class TaskStateBuilder(ABC):
    """Base class for Step Functions task state builders.
    Builders produce Task state dicts with Type, Resource, Parameters,
    Retry, and Catch. They do NOT add Next/End — that is handled by
    create_workflow_definition().
    """

    @abstractmethod
    def build_task_state(self, pipeline: Dict[str, Any], state_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        pass

    def build_payload(self, pipeline: Dict[str, Any], path_context: Dict[str, Any]) -> Dict[str, Any]:
        """Shared payload construction — identical JSON body for all types."""
        payload = {
            "body": {
                "inputS3AssetFilePath.$": path_context.get("inputS3AssetFilePath"),
                "outputS3AssetFilesPath.$": path_context.get("outputS3AssetFilesPath"),
                "outputS3AssetPreviewPath.$": path_context.get("outputS3AssetPreviewPath"),
                "outputS3AssetMetadataPath.$": path_context.get("outputS3AssetMetadataPath"),
                "inputOutputS3AssetAuxiliaryFilesPath.$": path_context.get("inputOutputS3AssetAuxiliaryFilesPath"),
                "bucketAssetAuxiliary.$": "$.bucketAssetAuxiliary",
                "bucketAsset.$": "$.bucketAsset",
                "assetId.$": "$.assetId",
                "databaseId.$": "$.databaseId",
                "workflowDatabaseId.$": "$.workflowDatabaseId",
                "workflowId.$": "$.workflowId",
                "inputAssetFileKey.$": "$.inputAssetFileKey",
                "inputAssetLocationKey.$": "$.inputAssetLocationKey",
                "outputType": pipeline.get("outputType", ""),
                "inputMetadata.$": "$.inputMetadata",
                "inputParameters": pipeline.get('inputParameters', ''),
                "executingUserName.$": "$.executingUserName",
                "executingRequestContext.$": "$.executingRequestContext"
            }
        }
        return payload

    def apply_callback(self, payload: Dict[str, Any], pipeline: Dict[str, Any]) -> Dict[str, Any]:
        if pipeline.get('waitForCallback') == 'Enabled':
            payload["body"]["TaskToken.$"] = "$$.Task.Token"
        return payload

    def _parse_user_resource(self, pipeline: Dict[str, Any]) -> Dict[str, Any]:
        """Parse userProvidedResource JSON string into a dict. Defaults resourceType to Lambda."""
        user_resource_str = pipeline.get('userProvidedResource', '{}')
        if isinstance(user_resource_str, str):
            user_resource = json.loads(user_resource_str) if user_resource_str else {}
        else:
            user_resource = user_resource_str
        if 'resourceType' not in user_resource:
            user_resource['resourceType'] = 'Lambda'
        return user_resource

    def _apply_callback_timeout(self, state: Dict[str, Any], pipeline: Dict[str, Any]) -> Dict[str, Any]:
        if pipeline.get('waitForCallback') == 'Enabled':
            timeout = pipeline.get('taskTimeout') or 86400
            state["TimeoutSeconds"] = int(timeout)
            # HeartbeatSeconds is optional — only set if explicitly provided.
            # Many pipelines don't implement heartbeat logic; omitting it
            # means Step Functions won't fail the task for missing heartbeats.
            heartbeat = pipeline.get('taskHeartbeatTimeout')
            if heartbeat and str(heartbeat).strip():
                state["HeartbeatSeconds"] = int(heartbeat)
        return state


class LambdaTaskBuilder(TaskStateBuilder):
    """Builder for Lambda invoke task states.

    Uses ResultPath $.{state_name}.output to store each pipeline step's
    response without overwriting the workflow state or other steps' results.
    """

    def build_task_state(self, pipeline: Dict[str, Any], state_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        user_resource = self._parse_user_resource(pipeline)
        wait_for_callback = pipeline.get('waitForCallback') == 'Enabled'

        if wait_for_callback:
            resource = "arn:aws:states:::lambda:invoke.waitForTaskToken"
        else:
            resource = "arn:aws:states:::lambda:invoke"

        state = {
            "Type": "Task",
            "Resource": resource,
            "ResultPath": f"$.{state_name}.output",
            "Parameters": {
                "FunctionName": user_resource.get('resourceId', ''),
                "Payload": payload
            },
            "Retry": [create_retry_config()],
            "Catch": [create_catch_config(
                error_equals=["States.ALL"],
                next_state="WorkflowProcessingJobFailed"
            )]
        }

        state = self._apply_callback_timeout(state, pipeline)
        return state


class SqsTaskBuilder(TaskStateBuilder):
    """Builder for SQS sendMessage task states.

    Uses ResultPath $.{state_name}.output to store each pipeline step's
    response without overwriting the workflow state or other steps' results.
    """

    def build_task_state(self, pipeline: Dict[str, Any], state_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        user_resource = self._parse_user_resource(pipeline)
        wait_for_callback = pipeline.get('waitForCallback') == 'Enabled'

        if wait_for_callback:
            resource = "arn:aws:states:::sqs:sendMessage.waitForTaskToken"
        else:
            resource = "arn:aws:states:::sqs:sendMessage"

        state = {
            "Type": "Task",
            "Resource": resource,
            "ResultPath": f"$.{state_name}.output",
            "Parameters": {
                "QueueUrl": user_resource.get('resourceId', ''),
                "MessageBody": payload
            },
            "Retry": [create_retry_config()],
            "Catch": [create_catch_config(
                error_equals=["States.ALL"],
                next_state="WorkflowProcessingJobFailed"
            )]
        }

        state = self._apply_callback_timeout(state, pipeline)
        return state


class EventBridgeTaskBuilder(TaskStateBuilder):
    """Builder for EventBridge putEvents task states.

    The payload is placed directly as the Detail object in the Entries
    Parameters. Step Functions automatically serializes the Detail object
    to a JSON string when calling the EventBridge PutEvents API.

    This approach eliminates the need for a preceding Pass state and
    States.JsonToString, and allows $$.Task.Token to be resolved
    correctly for the .waitForTaskToken callback pattern.
    """

    def build_task_state(self, pipeline: Dict[str, Any], state_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        user_resource = self._parse_user_resource(pipeline)
        wait_for_callback = pipeline.get('waitForCallback') == 'Enabled'

        if wait_for_callback:
            resource = "arn:aws:states:::events:putEvents.waitForTaskToken"
        else:
            resource = "arn:aws:states:::events:putEvents"

        event_bus_name = user_resource.get('resourceId', 'default') or 'default'
        event_source = user_resource.get('eventSource', 'vams.pipeline') or 'vams.pipeline'
        event_detail_type = user_resource.get('eventDetailType') or pipeline.get('pipelineId', state_name)

        state = {
            "Type": "Task",
            "Resource": resource,
            "ResultPath": f"$.{state_name}.output",
            "Parameters": {
                "Entries": [
                    {
                        "EventBusName": event_bus_name,
                        "Source": event_source,
                        "DetailType": event_detail_type,
                        "Detail": payload
                    }
                ]
            },
            "Retry": [create_retry_config()],
            "Catch": [create_catch_config(
                error_equals=["States.ALL"],
                next_state="WorkflowProcessingJobFailed"
            )]
        }

        state = self._apply_callback_timeout(state, pipeline)
        return state


# ---------------------------------------------------------------------------
# Builder registry
# ---------------------------------------------------------------------------

TASK_BUILDERS: Dict[str, TaskStateBuilder] = {
    "Lambda": LambdaTaskBuilder(),
    "SQS": SqsTaskBuilder(),
    "EventBridge": EventBridgeTaskBuilder(),
}


def get_task_builder(pipeline_execution_type: str) -> TaskStateBuilder:
    """Return the appropriate TaskStateBuilder for the given execution type.

    Args:
        pipeline_execution_type: One of "Lambda", "SQS", or "EventBridge".

    Returns:
        A TaskStateBuilder instance.

    Raises:
        ValueError: If the execution type is not supported.
    """
    builder = TASK_BUILDERS.get(pipeline_execution_type)
    if not builder:
        raise ValueError(f"Unsupported pipeline execution type: {pipeline_execution_type}")
    return builder
