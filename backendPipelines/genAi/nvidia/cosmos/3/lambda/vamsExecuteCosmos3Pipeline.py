#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution.
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams.
Extracts COSMOS3_* metadata and MODEL_VARIANT/TASK_MODE inputParameters, then invokes openPipeline.
"""
import os
import boto3
import json
from customLogging.logger import safeLogger
import manifestHelper

logger = safeLogger(service="VamsExecuteCosmos3Pipeline")
lambda_client = boto3.client('lambda')
s3_client = boto3.client('s3')
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]

INPUT_FILE_MODES = ("image2video", "video2video", "transfer")
# Variants whose pipeline can honor a metadata-driven transfer mode
TRANSFER_CAPABLE_VARIANTS = ("nano", "super")


def _extract_metadata_value(input_metadata, key, scopes):
    """Look up a metadata key across the given VAMS metadata scopes (assetMetadata/fileMetadata)."""
    if not input_metadata:
        return ""
    try:
        metadata_obj = json.loads(input_metadata) if isinstance(input_metadata, str) else input_metadata
        vams_metadata = metadata_obj.get("VAMS", {})
        for scope in scopes:
            scope_md = vams_metadata.get(scope, {})
            if isinstance(scope_md, dict) and scope_md.get(key):
                return scope_md.get(key)
    except Exception as e:
        logger.warning(f"Failed to extract {key} from metadata: {e}")
    return ""


def execute_pipeline(payload):
    logger.info("Invoking openPipeline Lambda .........")
    lambda_response = lambda_client.invoke(
        FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=json.dumps(payload).encode('utf-8'),
    )
    logger.info(lambda_response)
    if 'StatusCode' not in lambda_response or lambda_response['StatusCode'] != 200:
        message = lambda_response.get("body", {}).get("message", "")
        raise Exception("Invoke Open Pipeline Lambda Failed. " + message)


def lambda_handler(event, context):
    logger.info(event)
    external_task_token = None
    try:
        if not event.get('body'):
            return {'statusCode': 400, 'body': json.dumps({"message": 'Request body is required'})}
        data = json.loads(event['body']) if isinstance(event['body'], str) else event['body']

        if 'TaskToken' not in data:
            raise Exception("VAMS Workflow TaskToken not found in pipeline input. Register this pipeline as needing a task token callback.")
        external_task_token = data['TaskToken']

        # Resolve input/output locations from the workflow manifest (legacy-payload fallback).
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        # Single input file per execution today (SFN/manifest layer is multi-file-ready).
        manifestHelper.enforce_single_input_file(resolved)
        logger.info(f"Resolved pipeline inputs (manifestUsed={resolved['manifestUsed']}): {resolved}")

        # Read metadata + input-configuration CONTENT from S3 (locations travel, content does not);
        # inline fields remain a fallback for direct/local invocations. The COSMOS3_* extraction
        # below stays at this boundary; only the S3 locations are forwarded to the container.
        # The metadata file is the grouped-by-asset envelope, projected onto the legacy
        # {"VAMS": {...}} view for this run's subject that the scopes below read: its input file, or —
        # in the no-input-file modes — the envelope's first metadata-source asset.
        input_metadata = manifestHelper.run_vams_view(
            manifestHelper.fetch_metadata(s3_client, resolved['inputMetadataS3Location']), resolved) \
            or data.get('inputMetadata', '')
        input_parameters = manifestHelper.fetch_input_configuration(s3_client, resolved['inputConfigurationS3Location']) \
            or data.get('inputParameters', '')

        # Variant + task mode come from inputParameters (set at registration)
        model_variant = "nano"
        task_mode = ""
        try:
            params = json.loads(input_parameters) if isinstance(input_parameters, str) else (input_parameters or {})
            model_variant = params.get("MODEL_VARIANT", "nano") or "nano"
            task_mode = params.get("TASK_MODE", "") or ""
        except Exception as e:
            logger.warning(f"Failed to parse inputParameters: {e}")

        # A COSMOS3_TASK_MODE metadata value can switch the pipeline into a
        # different mode per run (notably "transfer"). It is read from both
        # scopes since it may be set before we know the final scope. Transfer is
        # only honored on the general-purpose omni variants (nano, super).
        mode_override = _extract_metadata_value(
            input_metadata, "COSMOS3_TASK_MODE", ("fileMetadata", "assetMetadata")
        )
        if mode_override:
            if mode_override == "transfer" and model_variant not in TRANSFER_CAPABLE_VARIANTS:
                logger.warning(
                    f"COSMOS3_TASK_MODE=transfer ignored for variant '{model_variant}' "
                    f"(only {TRANSFER_CAPABLE_VARIANTS} support transfer)"
                )
            else:
                task_mode = mode_override
                logger.info(f"COSMOS3_TASK_MODE metadata overrode task mode to: {task_mode}")

        # Every generation setting resolves CONFIG-FIRST with an asset-metadata fallback (see
        # manifestHelper.resolve_input_setting): what the operator supplied on the execute screen — a
        # template's dynamic tag — wins, and a blank field falls back to a standing value on the asset.
        # Only assetMetadata is consulted: these settings describe the RUN, not one file, and a workflow
        # may select many files. An input-file mode additionally honours per-FILE metadata, since there
        # the setting can legitimately belong to the file being converted.
        needs_input = task_mode in INPUT_FILE_MODES or model_variant == "super-image2video"
        scopes = ("fileMetadata", "assetMetadata") if needs_input else ("assetMetadata",)

        def _setting(config_keys, metadata_key):
            return manifestHelper.resolve_input_setting(
                input_parameters, input_metadata, config_keys, metadata_key,
                metadata_scopes=scopes)

        cosmos_prompt = _setting(("PROMPT", "prompt"), "COSMOS3_PROMPT")
        cosmos_negative_prompt = _setting(
            ("NEGATIVE_PROMPT", "negativePrompt"), "COSMOS3_NEGATIVE_PROMPT")
        cosmos_seed = _setting(("SEED", "seed"), "COSMOS3_SEED")
        cosmos_guidance = _setting(("GUIDANCE", "guidance"), "COSMOS3_GUIDANCE")
        cosmos_num_frames = _setting(("NUM_FRAMES", "numFrames"), "COSMOS3_NUM_FRAMES")

        # Control-signal transfer fields (only meaningful when task_mode == "transfer")
        cosmos_control_type = _setting(("CONTROL_TYPE", "controlType"), "COSMOS3_CONTROL_TYPE")
        cosmos_control_path = _setting(("CONTROL_PATH", "controlPath"), "COSMOS3_CONTROL_PATH")
        cosmos_control_weight = _setting(
            ("CONTROL_WEIGHT", "controlWeight"), "COSMOS3_CONTROL_WEIGHT")
        cosmos_control_guidance = _setting(
            ("CONTROL_GUIDANCE", "controlGuidance"), "COSMOS3_CONTROL_GUIDANCE")

        payload = {
            "modelVariant": model_variant,
            "taskMode": task_mode,
            "inputS3AssetFilePath": resolved['inputS3AssetFilePath'],
            "outputS3AssetFilesPath": resolved['outputS3AssetFilesPath'],
            "outputS3AssetPreviewPath": resolved['outputS3AssetPreviewPath'],
            "outputS3AssetMetadataPath": resolved['outputS3AssetMetadataPath'],
            "inputOutputS3AssetAuxiliaryFilesPath": resolved['inputOutputS3AssetAuxiliaryFilesPath'],
            # Metadata + input-configuration S3 LOCATIONS travel onward (never the inline content);
            # the container reads them from S3 as needed.
            "inputMetadataS3Location": resolved['inputMetadataS3Location'],
            "inputConfigurationS3Location": resolved['inputConfigurationS3Location'],
            "orchestrationEventPrefix": resolved['orchestrationEventPrefix'],
            "sfnExternalTaskToken": external_task_token,
            "executingUserName": data.get('executingUserName', ''),
            "executingRequestContext": data.get('executingRequestContext', ''),
            "assetId": resolved['assetId'],
            "databaseId": resolved['databaseId'],
            "cosmosPrompt": cosmos_prompt,
            "cosmosNegativePrompt": cosmos_negative_prompt,
            "cosmosSeed": cosmos_seed,
            "cosmosGuidance": cosmos_guidance,
            "cosmosNumFrames": cosmos_num_frames,
            "cosmosControlType": cosmos_control_type,
            "cosmosControlPath": cosmos_control_path,
            "cosmosControlWeight": cosmos_control_weight,
            "cosmosControlGuidance": cosmos_control_guidance,
        }

        execute_pipeline(payload)
        return {'statusCode': 200, 'body': 'Success'}
    except Exception as e:
        logger.exception(e)
        if external_task_token:
            try:
                sfn_client.send_task_failure(
                    taskToken=external_task_token,
                    error="Cosmos3PipelineError",
                    cause=str(e)[:256],
                )
            except Exception as sfn_err:
                logger.error(f"Failed to send task failure callback: {sfn_err}")
        return {'statusCode': 500, 'body': json.dumps({"message": str(e)})}
