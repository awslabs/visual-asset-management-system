# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
ConstructPipeline - GenAI CAD STEP Agent
Builds the definition document the agent container runs from, validating the run's settings and
resolving the output file name BEFORE any compute is provisioned.
"""

import json
import os

import boto3
from botocore.config import Config

import cad_step_naming
import manifestHelper
from customLogging.logger import safeLogger

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="ConstructPipeline-CadStepAgent")
s3 = boto3.client('s3', config=retry_config)
sfn = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'), config=retry_config)

# Deployment-level settings the CDK passes through; per-run values narrow them, never widen them.
MAX_RUN_SECONDS = int(os.environ.get("MAX_RUN_SECONDS", "3600"))
ALLOW_INTERNET_RESEARCH = os.environ.get("ALLOW_INTERNET_RESEARCH", "true").strip().lower() == "true"
OPENAI_ENABLED = os.environ.get("OPENAI_ENABLED", "false").strip().lower() == "true"
DEFAULT_MODEL_PROVIDER = "bedrock"
MODEL_PROVIDERS = ("bedrock", "openai")
DEFAULT_MAX_ATTEMPTS = 4
MAX_ATTEMPTS_CEILING = 10
DEFAULT_SCRIPT_TIMEOUT_SECONDS = 300
MAX_PROMPT_CHARS = 20000
STAGE_TYPE = "CAD_STEP_AGENT"

_TRUE_SPELLINGS = {"true", "1", "yes", "on"}
_FALSE_SPELLINGS = {"false", "0", "no", "off"}


def _as_bool(value, default):
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in _TRUE_SPELLINGS:
            return True
        if lowered in _FALSE_SPELLINGS:
            return False
    return default


def _as_int(value, default):
    if isinstance(value, bool):
        return default
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return default


def _split_s3_uri(field_name, s3_uri):
    """The bucket and key of an 's3://bucket/key' location, named by the payload field it came from.
    Raises rather than unpacking short, so a payload whose path field is absent or carries no key
    reports which field was empty instead of a bare 'not enough values to unpack'."""
    bucket, _, key = (s3_uri or "").replace("s3://", "").partition("/")
    if not bucket or not key:
        raise ValueError(f"{field_name} is not an s3://bucket/key location: '{s3_uri}'")
    return bucket, key


def agent_settings(input_configuration):
    """The validated agent settings of a run, from the resolved template configuration.

    The configuration is the template's json body with its tags substituted (spec section 6), so the
    keys here are the body's keys. Values are clamped to the deployment's bounds and unknown provider
    or mode values are refused: a wrong provider would otherwise be discovered only after the
    container has started.
    """
    cfg = input_configuration if isinstance(input_configuration, dict) else {}

    mode = str(cfg.get("mode", cad_step_naming.MODE_MODIFY) or cad_step_naming.MODE_MODIFY).strip().lower()
    if mode not in cad_step_naming.MODES:
        raise ValueError(f"mode must be one of {', '.join(cad_step_naming.MODES)}")

    prompt = str(cfg.get("prompt", "") or "").strip()
    if not prompt:
        raise ValueError("A prompt describing the CAD change or design is required")
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError(f"prompt exceeds {MAX_PROMPT_CHARS} characters")

    provider = str(cfg.get("modelProvider", DEFAULT_MODEL_PROVIDER) or DEFAULT_MODEL_PROVIDER).strip().lower()
    if provider not in MODEL_PROVIDERS:
        raise ValueError(f"modelProvider must be one of {', '.join(MODEL_PROVIDERS)}")
    if provider == "openai" and not OPENAI_ENABLED:
        raise ValueError("The openai model provider is not configured for this deployment")

    max_attempts = _as_int(cfg.get("maxAttempts"), DEFAULT_MAX_ATTEMPTS)
    max_attempts = max(1, min(MAX_ATTEMPTS_CEILING, max_attempts))

    return {
        "mode": mode,
        "prompt": prompt,
        "outputFilename": str(cfg.get("outputFilename", "") or ""),
        "outputFilenamePrefix": str(cfg.get("outputFilenamePrefix", "") or ""),
        "designName": str(cfg.get("designName", "") or ""),
        # The deployment master switch is ANDed with the run's choice.
        "allowInternetResearch": ALLOW_INTERNET_RESEARCH and _as_bool(cfg.get("allowInternetResearch"), True),
        "modelProvider": provider,
        "modelId": str(cfg.get("modelId", "") or "").strip(),
        "maxAttempts": max_attempts,
        "maxRunSeconds": MAX_RUN_SECONDS,
        "scriptTimeoutSeconds": DEFAULT_SCRIPT_TIMEOUT_SECONDS,
    }


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token so a failure here does not leave the
    pipeline task waiting for its full taskTimeout. This task is the first state of the pipeline's
    state machine, so nothing further downstream can report on the token.
    Never raises: the caller re-raises the original error, which is the one worth reading."""
    if not task_token:
        return
    try:
        sfn.send_task_failure(
            taskToken=task_token,
            error="CadStepAgentPipelineError",
            cause=str(error)[:256]
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    logger.info("Event", event=event)
    try:
        return _build_execution_params(event)
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, event.get('externalSfnTaskToken', ''))
        raise


def _build_execution_params(event):
    """The state machine payload for the agent run."""
    output_bucket, output_key = _split_s3_uri(
        'outputS3AssetFilesPath', event.get('outputS3AssetFilesPath'))
    output_s3_asset_metadata_uri = event.get('outputS3AssetMetadataPath', '') or ''
    aux_uri = event.get('inputOutputS3AssetAuxiliaryFilesPath', '') or ''
    asset_id = event.get('assetId', '') or ''

    input_configuration = manifestHelper.fetch_input_configuration(
        s3, event.get('inputConfigurationS3Location', '')) or {}
    settings = agent_settings(input_configuration)

    input_uri = event.get('inputS3AssetFilePath', '') or ''
    input_file = None
    input_file_name = ""
    relative_subdir = ""
    if input_uri:
        input_bucket, input_key = _split_s3_uri('inputS3AssetFilePath', input_uri)
        _, extension = os.path.splitext(input_key)
        input_file = {
            "bucketName": input_bucket,
            "objectKey": input_key,
            "fileExtension": extension,
        }
        input_file_name = input_key.rsplit("/", 1)[-1]
        relative_subdir = cad_step_naming.relative_subdir_of(input_key, asset_id)

    # The modify template raises the pipeline's arity to one; a modify run with no file, or a
    # generate run handed one, is a template/workflow mismatch worth refusing here.
    if settings["mode"] == cad_step_naming.MODE_MODIFY and not input_file:
        raise ValueError("The modify mode requires one input STEP file")
    if settings["mode"] == cad_step_naming.MODE_GENERATE and input_file:
        raise ValueError("The generate mode takes no input file")

    # Resolving the name here is what turns a bad override into a pre-invoke rejection.
    file_name = cad_step_naming.resolve_output_filename(
        settings["mode"], input_file_name, settings["outputFilename"],
        settings["outputFilenamePrefix"], settings["designName"], settings["prompt"])

    def _dir_of(uri):
        if not uri:
            return {"bucketName": "", "objectDir": ""}
        bucket, key = _split_s3_uri('s3 path', uri)
        return {"bucketName": bucket, "objectDir": key}

    definition = {
        "jobName": event.get("jobName"),
        "stageType": STAGE_TYPE,
        "mode": settings["mode"],
        "inputFile": input_file,
        "outputFiles": {
            "bucketName": output_bucket,
            "objectDir": output_key,
            "relativeSubdir": relative_subdir,
            "fileName": file_name,
        },
        "outputMetadata": _dir_of(output_s3_asset_metadata_uri),
        "auxiliary": _dir_of(aux_uri),
        "assetId": asset_id,
        "databaseId": event.get("databaseId", "") or "",
        "agent": {
            "prompt": settings["prompt"],
            "allowInternetResearch": settings["allowInternetResearch"],
            "modelProvider": settings["modelProvider"],
            "modelId": settings["modelId"],
            "maxAttempts": settings["maxAttempts"],
            "maxRunSeconds": settings["maxRunSeconds"],
            "scriptTimeoutSeconds": settings["scriptTimeoutSeconds"],
        },
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }
    # The prompt is caller-authored content; the log line names the run and its shape, not the text.
    logger.info(
        f"Definition built: job={definition['jobName']} mode={settings['mode']} "
        f"provider={settings['modelProvider']} output={file_name} attempts={settings['maxAttempts']}")

    return {
        "jobName": event.get("jobName"),
        "currentStageType": STAGE_TYPE,
        "definition": [json.dumps(definition)],
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "orchestrationEventPrefix": event.get("orchestrationEventPrefix", ""),
    }
