#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution.
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams.
This handler executes the Gr00t Fine-Tuning pipeline by extracting training configuration
from asset metadata and invoking the openPipeline Lambda.

Operates at the asset level (not file level). Downloads the entire asset for training.
"""
import os
import boto3
import json
from customLogging.logger import safeLogger
import manifestHelper


logger = safeLogger(service="VamsExecuteGr00tFinetunePipeline")
lambda_client = boto3.client('lambda')
s3_client = boto3.client('s3')
sfn_client = boto3.client('stepfunctions', region_name=os.environ.get('AWS_REGION', 'us-east-1'))
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]


def resolve_input_asset_prefix(resolved):
    """The S3 PREFIX this pipeline trains on.

    Gr00t fine-tuning consumes the WHOLE asset, so the container needs a prefix, not an object key. A
    whole-asset selection already resolves to a prefix and is used as-is. A single-FILE selection
    resolves to an object key, and appending "/" to that would produce a prefix matching nothing — so
    the asset root recorded on the manifest's first input file (``assetRootS3Key``) is used instead.
    With no manifest entry to read, fall back to the file's parent prefix.
    """
    path = (resolved or {}).get("inputS3AssetFilePath") or ""
    if not path:
        return ""
    if path.endswith("/"):
        return path
    input_files = (resolved or {}).get("inputFiles") or []
    first = input_files[0] if input_files else {}
    bucket = first.get("bucket") or ""
    asset_root = first.get("assetRootS3Key") or ""
    if bucket and asset_root:
        return f"s3://{bucket}/{asset_root.lstrip('/')}"
    # No manifest root: the parent "folder" of the object key is the closest usable prefix.
    return path.rsplit("/", 1)[0] + "/"


def execute_pipeline(input_s3_asset_file_path, output_s3_asset_files_path, output_s3_asset_preview_path, output_s3_asset_metadata_path,
                      inputOutput_s3_assetAuxiliary_files_path, input_metadata_s3_location, input_configuration_s3_location,
                      orchestration_event_prefix, external_task_token,
                      executing_userName, executing_requestContext, asset_id, database_id, groot_config,
                      mode="finetune"):
    """
    Execute the Gr00t Fine-Tuning pipeline by invoking the openPipeline Lambda.
    Asset-level pipeline: downloads entire asset, not a single file.
    """

    messagePayload = {
        "inputS3AssetPath": input_s3_asset_file_path.rstrip("/") + "/",
        "outputS3AssetFilesPath": output_s3_asset_files_path,
        "outputS3AssetPreviewPath": output_s3_asset_preview_path,
        "outputS3AssetMetadataPath": output_s3_asset_metadata_path,
        "inputOutputS3AssetAuxiliaryFilesPath": inputOutput_s3_assetAuxiliary_files_path,
        "inputMetadataS3Location": input_metadata_s3_location,
        "inputConfigurationS3Location": input_configuration_s3_location,
        "orchestrationEventPrefix": orchestration_event_prefix,
        "sfnExternalTaskToken": external_task_token,
        "executingUserName": executing_userName,
        "executingRequestContext": executing_requestContext,
        "assetId": asset_id,
        "databaseId": database_id,
        "gr00tConfig": json.dumps(groot_config) if isinstance(groot_config, dict) else groot_config,
        # Lifted out of the resolved config so constructPipeline can name the Batch job and the
        # container can branch. 'finetune' unless a template asked for 'evaluate'.
        "mode": mode
    }

    logger.info("Invoking openPipeline Lambda .........")
    lambda_response = lambda_client.invoke(FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
                                        InvocationType='RequestResponse',
                                        Payload=json.dumps(messagePayload).encode('utf-8'))
    logger.info("lambda response")
    logger.info(lambda_response)
    logger.info("Invoke Open Pipeline Lambda Successfully.")

    if 'StatusCode' not in lambda_response or lambda_response['StatusCode'] != 200:
        message = lambda_response.get("body", {}).get("message", "")
        raise Exception("Invoke Open Pipeline Lambda Failed. " + message)


def lambda_handler(event, context):
    logger.info(event)
    external_task_token = ""

    try:
        if not event.get('body'):
            message = 'Request body is required'
            logger.error(message)
            return {
                'statusCode': 400,
                'body': json.dumps({"message": message})
            }

        if isinstance(event['body'], str):
            data = json.loads(event['body'])
        else:
            data = event['body']

        # Get external task token
        if 'TaskToken' in data:
            external_task_token = data['TaskToken']
        else:
            raise Exception("VAMS Workflow TaskToken not found in pipeline input. Make sure to register this pipeline in VAMS as needing a task token callback.")

        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        # Single input file per execution today (SFN/manifest layer is multi-file-ready).
        manifestHelper.enforce_single_input_file(resolved)

        # Source input configuration (3rd priority) and asset metadata (2nd priority) from S3,
        # falling back to inline payload fields for the legacy/transition path.
        input_parameters = manifestHelper.fetch_input_configuration(s3_client, resolved['inputConfigurationS3Location']) \
            or data.get('inputParameters', '')
        # The metadata file is the grouped-by-asset envelope, projected onto the legacy
        # {"VAMS": {...}} view for this run's subject that the scope below reads
        # (manifestHelper.run_vams_view).
        input_metadata = manifestHelper.run_vams_view(
            manifestHelper.fetch_metadata(s3_client, resolved['inputMetadataS3Location']), resolved) \
            or data.get('inputMetadata', '')

        # Merge order, lowest first so later sources override: asset metadata, then the
        # execute-time input configuration. The asset's own gr00t_config.json is applied last of
        # all by the container after download, so it stays the highest priority.
        groot_config = {}

        # Extract from ASSET metadata -- applied FIRST so it is the fallback layer the input
        # configuration below can override.
        if input_metadata:
            try:
                metadata_obj = json.loads(input_metadata) if isinstance(input_metadata, str) else input_metadata
                vams_metadata = metadata_obj.get("VAMS", {})
                asset_metadata = vams_metadata.get("assetMetadata", {})

                metadata_mappings = {
                    "GROOT_DATASET_PATH": "datasetPath",
                    "GROOT_DATA_CONFIG": "dataConfig",
                    "GROOT_BASE_MODEL_PATH": "baseModelPath",
                    "GROOT_MAX_STEPS": "maxSteps",
                    "GROOT_BATCH_SIZE": "batchSize",
                    "GROOT_LEARNING_RATE": "learningRate",
                    "GROOT_WEIGHT_DECAY": "weightDecay",
                    "GROOT_WARMUP_RATIO": "warmupRatio",
                    "GROOT_SAVE_STEPS": "saveSteps",
                    "GROOT_NUM_GPUS": "numGpus",
                    "GROOT_LORA_RANK": "loraRank",
                    "GROOT_LORA_ALPHA": "loraAlpha",
                    "GROOT_LORA_DROPOUT": "loraDropout",
                    "GROOT_TUNE_LLM": "tuneLlm",
                    "GROOT_TUNE_VISUAL": "tuneVisual",
                    "GROOT_TUNE_PROJECTOR": "tuneProjector",
                    "GROOT_TUNE_DIFFUSION_MODEL": "tuneDiffusionModel",
                    "GROOT_EMBODIMENT_TAG": "embodimentTag",
                    "GROOT_VIDEO_BACKEND": "videoBackend",
                }
                for metadata_key, config_key in metadata_mappings.items():
                    val = asset_metadata.get(metadata_key, "")
                    if val:
                        groot_config[config_key] = val
                        logger.info(f"Extracted {metadata_key} from asset metadata: {val}")
            except Exception as e:
                logger.warning(f"Failed to extract config from asset metadata: {e}")
        # Extract from the input CONFIGURATION -- applied SECOND so it OVERRIDES asset metadata.
        # This is what the operator supplied on the execute screen (a template's dynamic tags), so
        # it must win over a standing value saved on the asset. A blank field is simply absent here,
        # leaving the metadata value applied above in place.
        if input_parameters:
            try:
                params_obj = json.loads(input_parameters) if isinstance(input_parameters, str) else input_parameters
                param_mappings = {
                    "datasetPath": "datasetPath",
                    "dataConfig": "dataConfig",
                    "baseModelPath": "baseModelPath",
                    "maxSteps": "maxSteps",
                    "batchSize": "batchSize",
                    "learningRate": "learningRate",
                    "weightDecay": "weightDecay",
                    "warmupRatio": "warmupRatio",
                    "saveSteps": "saveSteps",
                    "numGpus": "numGpus",
                    "loraRank": "loraRank",
                    "loraAlpha": "loraAlpha",
                    "loraDropout": "loraDropout",
                    "tuneLlm": "tuneLlm",
                    "tuneVisual": "tuneVisual",
                    "tuneProjector": "tuneProjector",
                    "tuneDiffusionModel": "tuneDiffusionModel",
                    "embodimentTag": "embodimentTag",
                    "videoBackend": "videoBackend",
                    # Evaluation-only. checkpointFolder empty means "the newest gr00tOutput_* folder on
                    # the asset", so an evaluation can follow a training run without naming it.
                    "checkpointFolder": "checkpointFolder",
                    "evalTrajectories": "evalTrajectories",
                    "evalSteps": "evalSteps",
                    "evalStartTrajectory": "evalStartTrajectory",
                    "evalModalityKeys": "evalModalityKeys",
                    "evaluationKind": "evaluationKind",
                    # Which job to run. Supplied by the template's config body, so the SAME pipeline
                    # can host a training template and an evaluation template.
                    "mode": "mode",
                }
                for param_key, config_key in param_mappings.items():
                    if param_key in params_obj:
                        groot_config[config_key] = params_obj[param_key]
            except Exception as e:
                logger.warning(f"Failed to parse input parameters: {e}")


        # The template decides which job this is. Popped so it does not also appear inside
        # gr00tConfig, where it is not a training/eval parameter.
        mode = str(groot_config.pop("mode", "") or "finetune").strip().lower()
        if mode not in ("finetune", "evaluate"):
            raise Exception(
                f"Gr00t pipeline mode must be 'finetune' or 'evaluate', got '{mode}'.")
        logger.info(f"Gr00t pipeline mode: {mode}")

        executing_userName = data.get('executingUserName', '')
        executing_requestContext = data.get('executingRequestContext', '')

        execute_pipeline(
            resolve_input_asset_prefix(resolved),
            resolved['outputS3AssetFilesPath'],
            resolved['outputS3AssetPreviewPath'],
            resolved['outputS3AssetMetadataPath'],
            resolved['inputOutputS3AssetAuxiliaryFilesPath'],
            resolved['inputMetadataS3Location'],
            resolved['inputConfigurationS3Location'],
            resolved['orchestrationEventPrefix'],
            external_task_token,
            executing_userName,
            executing_requestContext,
            resolved['assetId'],
            resolved['databaseId'],
            groot_config,
            mode
        )

        return {
            'statusCode': 200,
            'body': 'Success'
        }
    except Exception as e:
        logger.exception(e)
        if external_task_token:
            try:
                sfn_client.send_task_failure(
                    taskToken=external_task_token,
                    error="Gr00tFinetunePipelineError",
                    cause=str(e)[:256]
                )
                logger.info("Sent task failure callback to Step Functions")
            except Exception as sfn_err:
                logger.error(f"Failed to send task failure callback: {sfn_err}")
        return {
            'statusCode': 500,
            'body': json.dumps({"message": str(e)})
        }
