#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import shlex
import boto3
from customLogging.logger import safeLogger
import manifestHelper
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="ConstructPipelineModelOps")
s3 = boto3.client('s3', config=retry_config)
sfn = boto3.client(
    'stepfunctions',
    region_name=os.environ["AWS_REGION"],
    config=retry_config
)


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token. The state machine carries no catch on
    this task, and the container command the ECS task runs is resolved from this lambda's own
    output - so reporting here is what ends the waiting task rather than leaving it for the full
    four-hour taskTimeout. A direct invoke carries no token and reports nothing."""
    if not task_token:
        return
    try:
        sfn.send_task_failure(
            taskToken=task_token,
            error="ModelOpsError",
            cause=str(error)[:256]
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    """
    ConstructPipeline
    Builds pipeline input definition to run the ECS task
    """

    ##################
    #Valid Test Input Parameters Definition to this Pipeline
    # {"includeAllAssetFileHierarchyFiles": "True", "seedMetadataGenerationWithInputMetadata": "True" }
    #################

    logger.info(f"Event: {event}")
    logger.info(f"Context: {context}")

    try:
        # construct different pipeline definition
        definition = construct_modelops_definition(event)

        logger.info(f"Definition: {definition}")

        return {
            "jobName": event.get("jobName"),
            "commands": definition,
            "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
            "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
            "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
            "status": "STARTING"
        }
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, event.get("externalSfnTaskToken", ""))
        raise


def input_object_prefix(input_s3_asset_file_key):
    """The directory portion of the input file's S3 key
    ('xidM/parts/housing/model.obj' -> 'xidM/parts/housing').

    The state block addresses an object as prefix + name + extension, so the prefix carries every
    segment between the bucket and the file name. Keeping the file's own subdirectory is what lets
    the handler read a source below the asset root and write its result beside that source, so two
    sources sharing a basename in different folders stay distinct.
    """
    key = input_s3_asset_file_key or ""
    if "/" not in key:
        return ""
    return key.rsplit("/", 1)[0]


def relative_subdir_from_asset_id(input_s3_asset_file_key, asset_id):
    """The input file's subdirectory within the asset, located by the threaded assetId within the
    file's full S3 key ('base/xidM/parts/housing/model.obj' + 'xidM' -> 'parts/housing'). Yields ''
    when the assetId names no segment of the key, so an asset whose base location key does not
    contain it writes at the output root rather than at a guessed depth.

    The asset-relative part of a key is not recoverable from the key alone: an asset's root prefix
    within its bucket is configurable per bucket (baseAssetsPrefix) and per asset (assetLocation).
    The assetId is a workflow state variable, threaded from the manifest through vamsExecute and
    openPipeline, and is empty for a direct invoke that carries no workflow context.
    """
    if not asset_id:
        return ""
    segments = (input_s3_asset_file_key or "").split("/")
    if asset_id not in segments:
        return ""
    return "/".join(segments[segments.index(asset_id) + 1:-1])


# The folder a converted file is written under when the conversion does not change the file
# extension, so an optimize-in-place run is a sibling of its source rather than a new version of it.
SAME_FORMAT_OUTPUT_SUBDIR = "optimized"


def output_relative_subdir(relative_subdir, input_extension, output_extension):
    """The subdirectory the converted file is written under, relative to the output-files prefix:
    the input file's own subdirectory within the asset, plus a trailing `optimized` folder when the
    output cannot be told apart from the input by name.

    ModelOps optimizes as well as converts, so the output extension can equal the input's — a
    template that targets a format the pipeline also accepts as input, or a run that names no output
    type and falls back to it. The output keeps both the input's subdirectory and its file name, so
    in that case the output's ASSET-RELATIVE path equals the input's; the workflow's process-output
    step writes each staged output back to the output asset at exactly that relative path, so the
    write-back would land a new version of the operator's source object rather than a sibling file.
    The extra folder is what keeps the two apart, and it is a folder rather than a changed file name
    because the name is what identifies the converted model.

    A format-changing conversion still lands directly beside its source, and the folder is constant
    per format, so it separates the output from the input rather than separating runs from each other
    (the workflow's own output path extension does that).
    """
    subdir = (relative_subdir or "").strip("/")
    # Compared without the leading dot and case-folded, because the two extensions arrive from
    # different places and in different shapes: the input's is derived from the S3 key, while the
    # output's is whatever `outputType` carries - the shipped templates write ".glb", a caller may
    # write "glb", and the emitted state/output blocks strip the dot themselves. A raw string
    # comparison reads "glb" and ".glb" as a format CHANGE, skips the folder, and lets the
    # write-back resolve onto the operator's own source object, which is the one outcome this
    # function exists to prevent.
    if (input_extension or "").lstrip(".").lower() != (output_extension or "").lstrip(".").lower():
        return subdir
    return f"{subdir}/{SAME_FORMAT_OUTPUT_SUBDIR}" if subdir else SAME_FORMAT_OUTPUT_SUBDIR


def output_object_prefix(output_s3_asset_files_key, relative_subdir):
    """The output destination prefix: the workflow's output-files prefix followed by the output
    file's own subdirectory within the asset, so a converted file lands beside its source instead of
    at the asset root. Exactly one separator joins the two parts, so a prefix that already ends in
    '/' and a file at the asset root both compose without an empty segment."""
    prefix = output_s3_asset_files_key or ""
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    subdir = (relative_subdir or "").strip("/")
    return f"{prefix}{subdir}/" if subdir else prefix


def construct_modelops_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event['outputS3AssetFilesPath']
    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri.replace("s3://", "").split("/", 1)
    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)
    input_s3_asset_file_filename = input_s3_asset_file_root.split("/")[-1]
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri .replace("s3://", "").split("/", 1)

    # Read the input configuration from S3 and update parameters based on event data
    config = manifestHelper.fetch_input_configuration(s3, event.get('inputConfigurationS3Location', ''))

    if config:
        # The target format the run produces: outputType is a VAMS-reserved key in the input
        # configuration, then the threaded outputFileType, then the input file's own extension so the
        # named object always carries one - ModelOps then optimizes the model without changing its
        # format. It is READ rather than removed: it is also what selects the target format for the
        # handler, and it is the only value distinguishing the three shipped templates.
        output_s3_asset_extension = (config.get('outputType')
                                     or event.get('outputFileType', '')
                                     or input_s3_asset_extension)

        # The converted file keeps the input file's subdirectory within the asset, so the workflow's
        # process-output step places it beside its source rather than at the asset root - where two
        # sources sharing a basename in different folders would overwrite each other. A conversion
        # that does not change the file extension gains one further folder, so the write-back cannot
        # resolve to the input's own key.
        relative_subdir = output_relative_subdir(
            relative_subdir_from_asset_id(input_s3_asset_file_key, event.get('assetId', '')),
            input_s3_asset_extension, output_s3_asset_extension)

        # The destination the produced file is written to. The workflow's output-files prefix is the
        # staging location its process-output step reads; the auxiliary working path is the fallback
        # for a direct invoke that carries no workflow output location.
        if output_s3_asset_files_key:
            output_bucket = output_s3_asset_files_bucket
            output_key = output_s3_asset_files_key
        else:
            output_bucket = inputOutput_s3_assetAuxiliary_files_bucket
            output_key = inputOutput_s3_assetAuxiliary_files_key

        # The state block carries the asset identity injected below. A template config body need
        # not declare it (the shipped per-format templates do not), so create it when absent
        # rather than raising KeyError on a valid configuration.
        state = config.setdefault("state", {})
        state["name"] = input_s3_asset_file_filename
        state["bucket"] = input_s3_asset_file_bucket
        state["prefix"] = input_object_prefix(input_s3_asset_file_key)
        state["extension"] = input_s3_asset_extension.replace(".", "")

        # The state block above addresses the INPUT object, so the output block is what names where
        # the result goes. Same four fields, same shape: bucket, the prefix carrying every segment
        # between the bucket and the file name, the file name, and the extension.
        output = config.setdefault("output", {})
        output["name"] = input_s3_asset_file_filename
        output["bucket"] = output_bucket
        output["prefix"] = output_object_prefix(output_key, relative_subdir).rstrip("/")
        output["extension"] = output_s3_asset_extension.replace(".", "")

        command_string = json.dumps(config)
        # The config JSON is derived from the asset filename/key and caller parameters.
        # Pass it as a single shell-quoted literal to `printf '%s'` so its contents are
        # never parsed by the shell; this prevents command injection via a value
        # containing a single quote (which json.dumps does not escape) or other shell
        # metacharacters.
        command = "printf '%s' " + shlex.quote(command_string) + " | /home/app/apps/handler/dist/index.js -i yaml --debug"

    else:
        # No configuration means no target format and no command to build. Raising is what reaches
        # the task-token callback: the ECS task resolves its container command from this lambda's
        # output, so a value returned in that field is carried forward as the command instead of
        # ending the run.
        raise manifestHelper.InputConfigurationError(
            "No input configuration file detected for the ModelOps pipeline.")

    commands = [
        "/bin/bash",
         "-c",
         command
    ]

    return commands

