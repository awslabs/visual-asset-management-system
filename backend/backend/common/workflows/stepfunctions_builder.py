"""
Amazon States Language (ASL) Builder for Step Functions

This module provides utilities to manually build AWS Step Functions state machine
definitions in Amazon States Language (ASL) format, eliminating the need for the
heavy stepfunctions library dependency.

Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import hashlib
import json
from typing import Dict, List, Optional, Any, Tuple

# Default AWS partition. Step Functions service-integration ARNs embed the partition
# (arn:{partition}:states:::...); in GovCloud/China/ISO it is NOT "aws" (e.g. "aws-us-gov"),
# so callers thread the deployment partition through. "aws" keeps commercial behavior unchanged.
DEFAULT_PARTITION = "aws"


def states_integration_arn(integration: str, partition: str = DEFAULT_PARTITION) -> str:
    """Partition-aware Step Functions service-integration ARN.

    Step Functions optimized integrations are referenced as
    ``arn:{partition}:states:::{integration}`` (e.g. ``lambda:invoke``,
    ``lambda:invoke.waitForTaskToken``, ``sqs:sendMessage``, ``events:putEvents``). The partition
    must match the deployment (``aws`` commercial, ``aws-us-gov`` GovCloud, ``aws-cn`` China,
    ``aws-iso``); an ``arn:aws:`` ARN is rejected by Step Functions in other partitions."""
    return f"arn:{partition or DEFAULT_PARTITION}:states:::{integration}"


def create_lambda_task_state(
    state_id: str,
    function_name: str,
    payload: Dict[str, Any],
    result_path: Optional[str] = None,
    wait_for_callback: bool = False,
    timeout_seconds: Optional[int] = None,
    heartbeat_seconds: Optional[int] = None,
    retry_config: Optional[Dict[str, Any]] = None,
    catch_config: Optional[List[Dict[str, Any]]] = None,
    partition: str = DEFAULT_PARTITION
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
        partition: AWS partition for the service-integration ARN (default "aws")

    Returns:
        Dictionary representing the Lambda task state in ASL format
    """
    # Choose the appropriate resource ARN based on callback requirement
    if wait_for_callback:
        resource = states_integration_arn("lambda:invoke.waitForTaskToken", partition)
    else:
        resource = states_integration_arn("lambda:invoke", partition)

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


# Errors a `.waitForTaskToken` task state must never retry: the token expired while the pipeline
# may still be running, so re-sending the invocation starts a second concurrent run of the same
# step with a fresh task token.
CALLBACK_NON_RETRYABLE_ERRORS = ["States.Timeout", "States.HeartbeatTimeout"]

# The only errors a `.waitForTaskToken` task state may retry: transport/service faults raised while
# the invocation is being delivered, so no pipeline run started. Once the pipeline holds the task
# token, every failure it reports arrives through SendTaskFailure as an application error, and
# retrying that re-runs the whole pipeline — hours of GPU work for a run that cannot succeed, into
# the same execution-scoped output prefix the previous attempt may still be draining.
CALLBACK_TRANSIENT_RETRYABLE_ERRORS = [
    "Lambda.ServiceException",
    "Lambda.AWSLambdaException",
    "Lambda.SdkClientException",
    "Lambda.TooManyRequestsException",
    "SQS.SdkClientException",
    "EventBridge.SdkClientException",
]


def create_task_retry_configs(wait_for_callback: bool = False) -> List[Dict[str, Any]]:
    """Retry list for a pipeline task state.

    Fire-and-forget states retry any error, which can only be an invocation failure. Callback
    states put the timeout errors first with MaxAttempts 0 — Step Functions uses the FIRST
    matching retrier, so a callback timeout falls through to the Catch instead of re-invoking
    the pipeline while the original run may still be in flight — and retry only the transient
    delivery faults, so an application failure the pipeline reported goes to the Catch.
    """
    if wait_for_callback:
        return [
            {"ErrorEquals": list(CALLBACK_NON_RETRYABLE_ERRORS), "MaxAttempts": 0},
            create_retry_config(error_equals=list(CALLBACK_TRANSIENT_RETRYABLE_ERRORS)),
        ]
    return [create_retry_config()]


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


# Errors States.ALL does not match, so routing EVERY failure to a handler needs an explicit entry
# for each. States.DataLimitExceeded is raised when a state's result — or its input after Parameters
# processing — exceeds the 256 KiB payload quota; with only a States.ALL catcher the execution fails
# straight to the top level, leaving the storage rows unreconciled.
CATCH_ALL_EXCLUDED_ERRORS = ["States.DataLimitExceeded"]


def create_catch_all_configs(
    next_state: str,
    result_path: Optional[str] = None
) -> List[Dict[str, Any]]:
    """Catcher list routing every catchable error to next_state.

    States.ALL must appear alone in a catcher, so each error it cannot match gets its own catcher
    ahead of it (Step Functions uses the first matching catcher).
    """
    catchers = [
        create_catch_config(error_equals=[error], next_state=next_state, result_path=result_path)
        for error in CATCH_ALL_EXCLUDED_ERRORS
    ]
    catchers.append(create_catch_config(error_equals=["States.ALL"], next_state=next_state,
                                        result_path=result_path))
    return catchers


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


# Step Functions hard quota on a state-machine definition (1 MB, not increasable). The aggregate is
# what counts: a per-step body that passes its own cap — a Deadline Cloud job template, for instance —
# can still push a multi-step workflow over the limit.
MAX_STATE_MACHINE_DEFINITION_BYTES = 1024 * 1024


def serialize_definition(definition: Dict[str, Any]) -> str:
    """Serialize an ASL definition for create/update, rejecting one over the definition quota.

    Raises ValueError so the save path reports a specific, actionable validation error instead of
    the opaque service rejection the create/update call would otherwise return.
    """
    definition_json = json.dumps(definition, indent=2)
    definition_bytes = len(definition_json.encode("utf-8"))
    if definition_bytes > MAX_STATE_MACHINE_DEFINITION_BYTES:
        raise ValueError(
            f"The workflow's state machine definition is {definition_bytes} bytes, over the "
            f"{MAX_STATE_MACHINE_DEFINITION_BYTES}-byte Step Functions limit. Reduce the number "
            "of pipeline steps, or the size of the pipelines' job templates.")
    return definition_json


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
    definition_json = serialize_definition(definition)
    
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
    definition_json = serialize_definition(definition)
    
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
        bucket_param: JSONPath to the bucket name (e.g., "$.workflowExecutionS3InputOutputBucket")
        path_template: Path template with {} placeholder for execution name
        execution_name_placeholder: JSONPath for execution name
        
    Returns:
        States.Format expression string
    """
    return f"States.Format('s3://{{}}/" + path_template + f"', {bucket_param}, {execution_name_placeholder})"


def create_interim_tracking_state(
    state_id: str,
    function_name: str,
    payload: Dict[str, Any],
    result_path: str,
    error_handler_state: str,
    partition: str = DEFAULT_PARTITION,
) -> Dict[str, Any]:
    """Interim pipeline-tracking Lambda state, inserted between two pipeline steps. Its result
    is stored at result_path; on error it routes to the shared error-handler state."""
    return {
        "Type": "Task",
        "Resource": states_integration_arn("lambda:invoke", partition),
        "ResultPath": result_path,
        "Parameters": {
            "FunctionName": function_name,
            "Payload": payload,
        },
        "Retry": [create_retry_config(
            error_equals=["States.ALL"], interval_seconds=5, backoff_rate=2.0, max_attempts=3)],
        "Catch": create_catch_all_configs(
            next_state=error_handler_state, result_path="$.errorInfo"),
    }


def create_error_handler_state(
    state_id: str,
    function_name: str,
    payload: Dict[str, Any],
    fail_state: str,
    partition: str = DEFAULT_PARTITION,
) -> Dict[str, Any]:
    """Error-handler Lambda state that every Catch routes to, then transitions to the Fail
    state. Its own errors are caught here too and still fall through to Fail."""
    return {
        "Type": "Task",
        "Resource": states_integration_arn("lambda:invoke", partition),
        "ResultPath": "$.errorHandlerResult",
        "Parameters": {
            "FunctionName": function_name,
            "Payload": payload,
        },
        "Retry": [create_retry_config(
            error_equals=["States.ALL"], interval_seconds=3, backoff_rate=2.0, max_attempts=2)],
        # If the error handler itself fails, still proceed to the Fail state.
        "Catch": create_catch_all_configs(next_state=fail_state),
        "Next": fail_state,
    }


# ---------------------------------------------------------------------------
# Builder pattern for multi-type pipeline ASL generation
# ---------------------------------------------------------------------------

from abc import ABC, abstractmethod


class TaskStateBuilder(ABC):
    """Base class for Step Functions task state builders.
    Builders produce Task state dicts with Type, Resource, Parameters,
    Retry, and Catch. They do NOT add Next/End — that is handled by
    create_workflow_definition().

    partition is the AWS partition embedded in the service-integration ARN
    (arn:{partition}:states:::...); it defaults to "aws" and is set per deployment.
    """

    def __init__(self, partition: str = DEFAULT_PARTITION):
        self.partition = partition or DEFAULT_PARTITION

    @abstractmethod
    def build_task_state(self, pipeline: Dict[str, Any], state_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        pass

    def build_payload(self, pipeline: Dict[str, Any], path_context: Dict[str, Any]) -> Dict[str, Any]:
        """Shared payload construction — identical JSON body for all task types
        (DeadlineCloud additionally appends the step's pipelineExecutionId, which its
        completion callback needs to locate the pipeline-execution row).

        The body carries the manifest + input-configuration S3 LOCATIONS plus the fields that are
        only available at the workflow-execution level (the asset/aux bucket names and asset keys,
        the workflow/execution identifiers, and the executing-user context). Everything a pipeline
        needs about its inputs/outputs (resolved input files, output/aux/metadata locations, asset
        identity, orchestration config) is read from the manifest at inputManifestS3Location."""
        payload = {
            "body": {
                # --- Workflow-execution identity ---
                "workflowDatabaseId.$": "$.workflowDatabaseId",
                "workflowId.$": "$.workflowId",
                "workflowExecutionId.$": "$.workflowExecutionId",

                # --- Workflow-execution I/O bucket only (the manifest carries each input file's own
                #     location + its aux bucket/preview prefix; no single triggering file key is
                #     threaded, so the body is input-file-agnostic and multi-file-ready) ---
                "workflowExecutionS3InputOutputBucket.$": "$.workflowExecutionS3InputOutputBucket",

                # --- Executing-user context ---
                "executingUserName.$": "$.executingUserName",
                "executingRequestContext.$": "$.executingRequestContext",
            }
        }

        # --- Input-location references the pipeline reads from S3 (manifest + per-pipeline
        #     config), appended only when the path context supplies them ---
        for env_field in (
            "inputManifestS3Location",
            "inputConfigurationS3Location",
        ):
            ref = path_context.get(env_field)
            if ref:
                payload["body"][f"{env_field}.$"] = ref
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
            resource = states_integration_arn("lambda:invoke.waitForTaskToken", self.partition)
        else:
            resource = states_integration_arn("lambda:invoke", self.partition)

        state = {
            "Type": "Task",
            "Resource": resource,
            "ResultPath": f"$.{state_name}.output",
            "Parameters": {
                "FunctionName": user_resource.get('resourceId', ''),
                "Payload": payload
            },
            "Retry": create_task_retry_configs(wait_for_callback),
            "Catch": create_catch_all_configs(next_state="WorkflowProcessingJobFailed")
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
            resource = states_integration_arn("sqs:sendMessage.waitForTaskToken", self.partition)
        else:
            resource = states_integration_arn("sqs:sendMessage", self.partition)

        state = {
            "Type": "Task",
            "Resource": resource,
            "ResultPath": f"$.{state_name}.output",
            "Parameters": {
                "QueueUrl": user_resource.get('resourceId', ''),
                "MessageBody": payload
            },
            "Retry": create_task_retry_configs(wait_for_callback),
            "Catch": create_catch_all_configs(next_state="WorkflowProcessingJobFailed")
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
            resource = states_integration_arn("events:putEvents.waitForTaskToken", self.partition)
        else:
            resource = states_integration_arn("events:putEvents", self.partition)

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
            "Retry": create_task_retry_configs(wait_for_callback),
            "Catch": create_catch_all_configs(next_state="WorkflowProcessingJobFailed")
        }

        state = self._apply_callback_timeout(state, pipeline)
        return state


# Reserved OpenJD job-parameter names the Deadline Cloud task state injects on createJob.
# Derived from the shared body envelope as "Vams" + capitalized field name; the job-callback
# lambda reads these back from GetJob, and a registered job template must declare every
# injected parameter (all STRING type).
DEADLINE_PARAMETER_PREFIX = "Vams"
DEADLINE_TASK_TOKEN_PARAMETER = "VamsTaskToken"
DEADLINE_PIPELINE_EXECUTION_ID_PARAMETER = "VamsPipelineExecutionId"
DEADLINE_WORKFLOW_EXECUTION_ID_PARAMETER = "VamsWorkflowExecutionId"


class DeadlineCloudTaskBuilder(TaskStateBuilder):
    """Builder for AWS Deadline Cloud createJob task states.

    Uses the Step Functions AWS SDK integration
    (aws-sdk:deadline:createJob.waitForTaskToken) to submit an OpenJD job to the
    pipeline's farm/queue. Deadline Cloud is async-only: createJob returns when the
    job is queued, not when it completes, so the task always waits on a task token.
    The deployment's Deadline job-callback lambda resolves the token from the
    job's EventBridge status events (default bus, source aws.deadline).

    The shared body envelope is flattened into reserved OpenJD job parameters
    (``Vams``-prefixed, string-typed). The registered job template must declare
    every one of these parameters. ``executingRequestContext`` is excluded: it is a
    multi-KB JSON object and Deadline caps a string job parameter at 1024 characters;
    it stays in the Step Functions state for the process-output step. The job reads
    everything else (resolved input files, output prefixes, aux locations) from the
    manifest at VamsInputManifestS3Location.

    The parsed userProvidedResource carries the Deadline-specific fields:
    deadlineFarmId, deadlineQueueId, deadlineTemplate (the template TEXT — callers
    resolve an S3-stored template to text before ASL generation), deadlineTemplateType
    (JSON|YAML), and optional deadlinePriority, deadlineMaxRetriesPerTask,
    deadlineMaxFailedTasksCount, deadlineStorageProfileId.
    """

    # Body fields not forwarded as job parameters (too large for the 1024-char
    # Deadline string-parameter cap; remain available in the SFN state).
    EXCLUDED_BODY_FIELDS = {"executingRequestContext"}

    DEFAULT_PRIORITY = 50

    # Hex digits of the state-name digest in the fallback client token. The Deadline
    # ClientToken caps at 64 characters and the execution name is a 32-char GUID.
    STATE_NAME_DIGEST_LENGTH = 16

    def build_payload(self, pipeline: Dict[str, Any], path_context: Dict[str, Any]) -> Dict[str, Any]:
        payload = super().build_payload(pipeline, path_context)
        # The job-callback lambda registers the Deadline job against this pipeline's
        # execution row and builds its orchestration event source from this id, so it
        # rides as a job parameter (only the Deadline body carries it).
        ref = path_context.get("pipelineExecutionIdRef")
        if ref:
            payload["body"]["pipelineExecutionId.$"] = ref
        return payload

    def _body_to_job_parameters(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """Flatten the shared body envelope into the createJob Parameters map. Each body
        field becomes a string-typed OpenJD job parameter named Vams{FieldName}. The
        JobParameter union member is 'String' (Step Functions SDK integrations PascalCase
        every shape member name)."""
        job_parameters = {}
        for key, ref in body.items():
            name = key[:-2] if key.endswith(".$") else key
            if name in self.EXCLUDED_BODY_FIELDS:
                continue
            param = DEADLINE_PARAMETER_PREFIX + name[0].upper() + name[1:]
            if key.endswith(".$"):
                job_parameters[param] = {"String.$": ref}
            else:
                job_parameters[param] = {"String": str(ref)}
        return job_parameters

    @staticmethod
    def _int_setting(field_name: str, value: Any) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            raise ValueError(f"DeadlineCloud pipeline setting {field_name} must be an integer")

    @classmethod
    def _client_token_expression(cls, state_name: str, payload: Dict[str, Any]) -> str:
        """createJob idempotency-token expression for the Parameters map (64-char Deadline cap).

        The token is identical across retries of one step — so a retried createJob cannot
        submit a duplicate job — and distinct for every other step, because a token shared
        between two steps makes Deadline return the first step's job, whose stored
        VamsTaskToken belongs to that first step, leaving the second step's token
        unresolved until its callback timeout. The step's pipelineExecutionId is a
        per-step, per-execution GUID and carries both properties; the state name does not
        (it is an ASL-build uuid1 prefix, which repeats within a build, plus the pipeline
        name, which repeats when one pipeline appears twice in a workflow). Without a
        pipelineExecutionId reference the token falls back to the execution name plus a
        digest of the full state name.
        """
        pipeline_execution_ref = payload.get("body", {}).get("pipelineExecutionId.$")
        if pipeline_execution_ref:
            return pipeline_execution_ref
        digest = hashlib.sha256(state_name.encode("utf-8")).hexdigest()[:cls.STATE_NAME_DIGEST_LENGTH]
        return f"States.Format('{{}}-{digest}', $$.Execution.Name)"

    def build_task_state(self, pipeline: Dict[str, Any], state_name: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        user_resource = self._parse_user_resource(pipeline)

        # createJob only reports that the job was QUEUED; completion arrives via the
        # job-callback lambda resolving the task token. A fire-and-forget Deadline task
        # would let the workflow proceed before any work ran, so callback is mandatory.
        if pipeline.get('waitForCallback') != 'Enabled':
            raise ValueError(
                "DeadlineCloud pipelines require waitForCallback=Enabled: createJob only "
                "queues the job; completion is reported via the task-token callback.")

        farm_id = user_resource.get('deadlineFarmId', '')
        queue_id = user_resource.get('deadlineQueueId', '')
        template = user_resource.get('deadlineTemplate', '')
        template_type = user_resource.get('deadlineTemplateType', 'YAML') or 'YAML'
        if not farm_id or not queue_id or not template:
            raise ValueError(
                "DeadlineCloud pipelines require deadlineFarmId, deadlineQueueId, and "
                "deadlineTemplate on the pipeline resource.")

        priority = user_resource.get('deadlinePriority')
        parameters = {
            "FarmId": farm_id,
            "QueueId": queue_id,
            "Template": template,
            "TemplateType": template_type,
            # 0 is a valid (lowest) priority — only fall back when unset/blank.
            "Priority": (self._int_setting('deadlinePriority', priority)
                         if priority not in (None, '') else self.DEFAULT_PRIORITY),
            # Identifies the VAMS pipeline step in the Deadline monitor.
            "NameOverride": state_name,
            # Step-scoped idempotency token so a retried createJob call cannot submit a
            # duplicate job and two steps cannot collapse onto one job.
            "ClientToken.$": self._client_token_expression(state_name, payload),
            "Parameters": self._body_to_job_parameters(payload.get("body", {})),
        }
        if user_resource.get('deadlineStorageProfileId'):
            parameters["StorageProfileId"] = user_resource['deadlineStorageProfileId']
        if user_resource.get('deadlineMaxRetriesPerTask') not in (None, ''):
            parameters["MaxRetriesPerTask"] = self._int_setting(
                'deadlineMaxRetriesPerTask', user_resource['deadlineMaxRetriesPerTask'])
        if user_resource.get('deadlineMaxFailedTasksCount') not in (None, ''):
            parameters["MaxFailedTasksCount"] = self._int_setting(
                'deadlineMaxFailedTasksCount', user_resource['deadlineMaxFailedTasksCount'])

        state = {
            "Type": "Task",
            "Resource": states_integration_arn(
                "aws-sdk:deadline:createJob.waitForTaskToken", self.partition),
            "ResultPath": f"$.{state_name}.output",
            "Parameters": parameters,
            # Retry only throttling, which rejects the request before a job exists, so the
            # replayed ClientToken creates the job for the first time and the surviving
            # attempt's fresh VamsTaskToken is the one the job carries. Every other error —
            # including a server error, a job-level failure, or a callback timeout — may
            # have created the job already: replaying the fixed ClientToken then returns
            # that job, whose stored VamsTaskToken belongs to the abandoned attempt, so the
            # retry attempt could never be completed. Those go straight to the Catch.
            "Retry": [create_retry_config(
                error_equals=["Deadline.ThrottlingException"])],
            "Catch": create_catch_all_configs(next_state="WorkflowProcessingJobFailed")
        }

        state = self._apply_callback_timeout(state, pipeline)
        return state


# ---------------------------------------------------------------------------
# Builder registry
# ---------------------------------------------------------------------------

# Builder class per execution type. A fresh instance is created per get_task_builder() call so
# each carries the deployment partition for its service-integration ARNs.
TASK_BUILDER_CLASSES: Dict[str, type] = {
    "Lambda": LambdaTaskBuilder,
    "SQS": SqsTaskBuilder,
    "EventBridge": EventBridgeTaskBuilder,
    "DeadlineCloud": DeadlineCloudTaskBuilder,
}


def get_task_builder(pipeline_execution_type: str,
                     partition: str = DEFAULT_PARTITION) -> TaskStateBuilder:
    """Return the appropriate TaskStateBuilder for the given execution type.

    Args:
        pipeline_execution_type: One of "Lambda", "SQS", "EventBridge", or "DeadlineCloud".
        partition: AWS partition for the service-integration ARNs (default "aws"). Pass the
            deployment partition (e.g. "aws-us-gov") so generated ASL is valid in that partition.

    Returns:
        A TaskStateBuilder instance bound to the given partition.

    Raises:
        ValueError: If the execution type is not supported.
    """
    builder_cls = TASK_BUILDER_CLASSES.get(pipeline_execution_type)
    if not builder_cls:
        raise ValueError(f"Unsupported pipeline execution type: {pipeline_execution_type}")
    return builder_cls(partition=partition or DEFAULT_PARTITION)
