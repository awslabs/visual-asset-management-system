# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Lambda to execute the zipArchive system pipeline (utility-zip-archive) from VAMS workflows.
Note: Lambda function name must start with "vams" to allow invoke permissioning.

Two modes, decided from the run manifest:
- zipDownload: the manifest carries the server-only system execution context of a Download as Zip
  job (VAMS writes it only for a run it launched under the Zip-Download system trigger). The job's
  request manifest in the auxiliary bucket says what to archive and where.
- archiveRun: any other run (an ordinary workflow run of this pipeline). The run's input files are
  archived into its outputs files prefix and ingested into the asset as a new file version.
"""
import json
import os
import time

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import manifestHelper
from customLogging.logger import safeLogger

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="VamsExecuteZipArchivePipeline")
# The open-pipeline invoke is synchronous and not safe to repeat: each call starts a new archive state
# machine run (and Batch job) on the same task token. So botocore neither retries it nor gives up on
# it while it still runs (the #1205 rule): the read timeout is above openPipeline's 300 s (5 minute)
# timeout, and only a throttle the function never ran for is retried (invoke_open_pipeline).
OPEN_PIPELINE_READ_TIMEOUT_SECONDS = 310
OPEN_PIPELINE_THROTTLE_ATTEMPTS = 4
OPEN_PIPELINE_THROTTLE_BACKOFF_SECONDS = 1
invoke_config = Config(retries={'total_max_attempts': 1}, read_timeout=OPEN_PIPELINE_READ_TIMEOUT_SECONDS,
                       connect_timeout=10)
lambda_client = boto3.client('lambda', config=invoke_config)
s3_client = boto3.client('s3', config=retry_config)
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'),
                          config=retry_config)
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]

# The manifest key P2a writes the system execution context under (backend
# common/workflows/systemPipelines.SYSTEM_EXECUTION_CONTEXT_MANIFEST_KEY).
SYSTEM_EXECUTION_CONTEXT_MANIFEST_KEY = "systemExecutionContext"
ZIP_CONTEXT_KEYS = ("requestId", "userId", "databaseId", "assetId", "manifestKey")
MODE_ZIP_DOWNLOAD = "zipDownload"
MODE_ARCHIVE_RUN = "archiveRun"


def workflow_execution_id_from_event_prefix(prefix):
    """The workflow execution id in ``<source>.execution.<executionId>.pipeline.<pipelineExecutionId>``."""
    if not prefix:
        return ""
    start = prefix.find(".execution.")
    end = prefix.rfind(".pipeline.")
    if start < 0 or end <= start:
        return ""
    return prefix[start + len(".execution."):end]


def zip_download_context(manifest):
    """The Download as Zip context of a run, or None for an ordinary run."""
    context = (manifest or {}).get(SYSTEM_EXECUTION_CONTEXT_MANIFEST_KEY)
    if not isinstance(context, dict) or not context:
        return None
    if not all(isinstance(context.get(k), str) and context.get(k) for k in ZIP_CONTEXT_KEYS):
        raise ValueError("The system execution context does not describe a zip download")
    if not context["manifestKey"].startswith("vams.preparedDownloads/"):
        raise ValueError("The zip download manifest is outside the prepared downloads prefix")
    return {k: context[k] for k in ZIP_CONTEXT_KEYS}


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token so the pipeline task does not wait for the
    full taskTimeout when this lambda cannot start the pipeline."""
    if not task_token:
        return
    try:
        sfn_client.send_task_failure(taskToken=task_token, error="ZipArchivePipelineError",
                                     cause=str(error)[:256])
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def build_open_payload(data, manifest, resolved, external_task_token):
    context = zip_download_context(manifest)
    prefix = resolved.get('orchestrationEventPrefix', '')
    payload = {
        "mode": MODE_ZIP_DOWNLOAD if context else MODE_ARCHIVE_RUN,
        "runManifestS3Location": manifestHelper.manifest_location(data),
        "inputConfigurationS3Location": resolved.get('inputConfigurationS3Location', ''),
        "outputS3AssetFilesPath": resolved.get('outputS3AssetFilesPath', ''),
        "outputBucketRegion": resolved['outputBucketRegion'],
        "inputBucketRegion": resolved['inputBucketRegion'],
        "assetId": resolved.get('assetId', ''),
        "databaseId": resolved.get('databaseId', ''),
        "workflowExecutionId": workflow_execution_id_from_event_prefix(prefix),
        "sfnExternalTaskToken": external_task_token,
        "orchestrationEventPrefix": prefix,
    }
    if context:
        aux_bucket = (manifest or {}).get("auxBucket", "")
        if not aux_bucket:
            raise ValueError("The run manifest names no auxiliary bucket")
        payload["zipDownload"] = dict(context, manifestBucket=aux_bucket)
    return payload


def invoke_open_pipeline(payload):
    """Invoke openPipeline synchronously, once. Lambda refuses a throttled invoke before the function
    runs (TooManyRequestsException), so that one is retried with a short backoff; any other error,
    including a read timeout, is raised and never repeated, since the call may have started a run."""
    body = json.dumps(payload).encode('utf-8')
    for attempt in range(OPEN_PIPELINE_THROTTLE_ATTEMPTS):
        try:
            return lambda_client.invoke(FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
                                        InvocationType='RequestResponse', Payload=body)
        except ClientError as e:
            throttled = e.response.get('Error', {}).get('Code') == 'TooManyRequestsException'
            if not throttled or attempt == OPEN_PIPELINE_THROTTLE_ATTEMPTS - 1:
                raise
            logger.warning(f"Invoke of openPipeline throttled; retrying (attempt {attempt + 2})")
            time.sleep(OPEN_PIPELINE_THROTTLE_BACKOFF_SECONDS * (2 ** attempt))


def lambda_handler(event, context):
    logger.info(event)
    external_task_token = None
    try:
        if not event.get('body'):
            raise ValueError('Request body is required')
        data = json.loads(event['body']) if isinstance(event['body'], str) else event['body']
        if 'TaskToken' not in data:
            raise Exception("VAMS Workflow TaskToken not found in pipeline input. "
                            "Register this pipeline as needing a task token callback.")
        external_task_token = data['TaskToken']

        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        if not resolved['manifestUsed']:
            raise ValueError("The zipArchive pipeline needs the workflow run manifest")
        # The archive's AWS Batch compute runs in the VPC's isolated subnets, which reach Amazon S3 in
        # the deployment Region and the Regions served by the VPC's cross-Region S3 endpoints.
        manifestHelper.enforce_inputs_in_region(resolved)
        # The run manifest is read again for the server-only system execution context, which the
        # shared resolver does not expose.
        manifest = manifestHelper.fetch_manifest(s3_client, manifestHelper.manifest_location(data))
        payload = build_open_payload(data, manifest, resolved, external_task_token)
        logger.info(f"Starting the zipArchive pipeline in mode {payload['mode']}")

        response = invoke_open_pipeline(payload)
        if response.get('StatusCode') != 200 or response.get('FunctionError'):
            raise Exception("Invoke Open Pipeline Lambda Failed: " + str(response.get('FunctionError')))
        return {'statusCode': 200, 'body': 'Success'}
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, external_task_token)
        return {'statusCode': 500, 'body': json.dumps({"message": "Internal Server Error"})}
