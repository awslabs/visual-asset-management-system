#  Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import shlex
import boto3
from customLogging.logger import safeLogger
import manifestHelper

logger = safeLogger(service="ConstructPipelineRapidPipeline")
s3 = boto3.client('s3')

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

    # construct different pipeline definition
    definition = construct_rapidPipeline_definition(event)

    logger.info(f"Definition: {definition}")
    
    return {
        "jobName": event.get("jobName"),
        "commands": definition,
        # Forward the metadata + input-configuration S3 locations, not their content.
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "status": "STARTING"
    }


def relative_subdir_from_manifest_path(relative_path):
    """The input file's subdirectory within the asset, derived from its asset-relative manifest
    path ('/parts/housing/model.obj' -> 'parts/housing'). A file at the asset root yields ''."""
    trimmed = (relative_path or "").strip("/")
    if "/" not in trimmed:
        return ""
    return trimmed.rsplit("/", 1)[0]


def relative_subdir_from_asset_id(input_s3_asset_file_key, asset_id):
    """The input file's subdirectory within the asset, located by the threaded assetId within the
    file's full S3 key ('base/xidM/parts/housing/model.obj' + 'xidM' -> 'parts/housing'). Yields ''
    when the assetId names no segment of the key, so an asset whose base location key does not
    contain it writes at the output root rather than at a guessed depth."""
    if not asset_id:
        return ""
    segments = (input_s3_asset_file_key or "").split("/")
    if asset_id not in segments:
        return ""
    return "/".join(segments[segments.index(asset_id) + 1:-1])


def input_relative_subdir(event, input_s3_asset_file_key):
    """The subdirectory the converted file is written under, relative to the output-files prefix.

    The preferred source is the workflow manifest's first input file relativePath, which is
    asset-relative already; the threaded assetId is the fallback. The subdirectory is never derived
    from the S3 key on its own: an asset's root prefix within its bucket is configurable per bucket
    (baseAssetsPrefix) and per asset (assetLocation), so the asset-relative part of a key is not
    recoverable from the key alone.

    The manifest read is best-effort. The vamsExecute lambda already read the same manifest at
    launch, and this state carries no catch, so a transient read failure writes at the output root
    rather than failing the state machine and leaving the workflow's task token unreported.
    """
    manifest_s3_location = manifestHelper.manifest_location(event)
    if manifest_s3_location:
        try:
            manifest = manifestHelper.fetch_manifest(s3, manifest_s3_location)
        except Exception as e:
            logger.warning(f"Could not read the input manifest at {manifest_s3_location}: {e}")
            manifest = None
        input_files = (manifest or {}).get('inputFiles') or []
        if input_files:
            return relative_subdir_from_manifest_path((input_files[0] or {}).get('relativePath'))
    return relative_subdir_from_asset_id(input_s3_asset_file_key, event.get('assetId', ''))


# The folder a converted file is written under when the conversion does not change the file
# extension, so an optimize-in-place run is a sibling of its source rather than a new version of it.
SAME_FORMAT_OUTPUT_SUBDIR = "optimized"


def output_relative_subdir(relative_subdir, input_extension, output_extension):
    """The subdirectory the converted file is written under, relative to the output-files prefix:
    the input file's own subdirectory within the asset, plus a trailing `optimized` folder when the
    conversion does not change the file extension.

    rpdx optimizes as well as converts, so the output extension can equal the input's — a template
    that targets the input's own format, or a run that names no output type and falls back to it.
    The output keeps both the input's subdirectory and its file name, so in that case the output's
    ASSET-RELATIVE path equals the input's; the workflow's process-output step writes each staged
    output back to the output asset at exactly that relative path, so the write-back would land a new
    version of the operator's source object rather than a sibling file. The extra folder is what
    keeps the two apart, and it is a folder rather than a changed file name because the name is what
    identifies the converted model.

    The two extensions decide it on their own: the output file name is the input's stem plus the
    output extension, so equal extensions is exactly the case where the two names — and therefore the
    two relative paths — coincide. A format-changing conversion still lands directly beside its
    source, and the folder is constant per format, so it separates the output from the input rather
    than separating runs from each other (the workflow's own output path extension does that).
    """
    subdir = (relative_subdir or "").strip("/")
    # Compared without the leading dot and case-folded: the input's extension is derived from the S3
    # key while the output's is whatever `outputType` carries, and that is caller data - the shipped
    # templates write ".glb", a caller may write "glb". A raw comparison reads those as a format
    # CHANGE, skips the folder, and lets the write-back resolve onto the operator's own source
    # object, which is the outcome this function exists to prevent.
    if (input_extension or "").lstrip(".").lower() != (output_extension or "").lstrip(".").lower():
        return subdir
    return f"{subdir}/{SAME_FORMAT_OUTPUT_SUBDIR}" if subdir else SAME_FORMAT_OUTPUT_SUBDIR


def output_object_prefix(output_s3_asset_files_key, relative_subdir):
    """The output destination prefix: the workflow's output-files prefix followed by the output
    file's own subdirectory within the asset, so the converted file lands beside its source instead
    of at the asset root.

    `aws s3 cp` takes the source file's own name for a destination ending in '/', which is what
    keeps the output name unchanged; a destination NOT ending in '/' would write a single object
    named after the prefix instead. Exactly one separator joins the two parts, so a prefix that
    already ends in '/' and a file at the asset root both compose without an empty segment.
    """
    prefix = output_s3_asset_files_key or ""
    if prefix and not prefix.endswith("/"):
        prefix += "/"
    subdir = (relative_subdir or "").strip("/")
    return f"{prefix}{subdir}/" if subdir else prefix


def construct_rapidPipeline_definition(event) -> dict:
    input_s3_asset_file_uri = event['inputS3AssetFilePath']
    output_s3_asset_files_uri = event['outputS3AssetFilesPath'] 
    input_s3_asset_file_bucket, input_s3_asset_file_key = input_s3_asset_file_uri.replace("s3://", "").split("/", 1)
    output_s3_asset_files_bucket, output_s3_asset_files_key = output_s3_asset_files_uri.replace("s3://", "").split("/", 1)
    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_s3_asset_file_key)
    input_s3_asset_file_filename = input_s3_asset_file_root.split("/")[-1]
    inputOutput_s3_assetAuxiliary_files_uri = event['inputOutputS3AssetAuxiliaryFilesPath']
    inputOutput_s3_assetAuxiliary_files_bucket, inputOutput_s3_assetAuxiliary_files_key = inputOutput_s3_assetAuxiliary_files_uri .replace("s3://", "").split("/", 1)

    # Read the input configuration (rp_config) from its S3 location.
    config = manifestHelper.fetch_input_configuration(s3, event.get('inputConfigurationS3Location', '')) or {}

    # outputType is a VAMS-reserved key in the input configuration: it selects the output file
    # extension and is removed before the remainder is written as the rpdx rp_config.json. Fall
    # back to the threaded outputFileType, then to the input file's own extension so the written
    # object always carries one — rpdx then optimizes the model without changing its format.
    output_s3_asset_extension = (config.pop('outputType', None)
                                 or event.get('outputFileType', '')
                                 or input_s3_asset_extension)

    # The converted file keeps the input file's subdirectory within the asset, so the workflow's
    # process-output step places it beside its source. A conversion that does not change the file
    # extension gains one further folder, so the write-back cannot resolve to the input's own key.
    # The workflow's own output path extension is inserted immediately before the file name at
    # write-back, giving {subdir}/{extension}/{name}.
    relative_subdir = output_relative_subdir(
        input_relative_subdir(event, input_s3_asset_file_key),
        input_s3_asset_extension, output_s3_asset_extension)

    # Every value interpolated into the shell command below originates from asset
    # filenames / S3 keys / caller-supplied parameters, so each is shell-quoted with
    # shlex.quote(). The command still runs under a shell (it chains steps with &&),
    # so untrusted values must be inert single-quoted literals to prevent command
    # injection (e.g. a filename containing $(...), backticks, or ';').
    input_file = f"{input_s3_asset_file_filename}{input_s3_asset_extension}"
    output_s3_asset_file_filename = input_s3_asset_file_filename + output_s3_asset_extension
    input_object = f"s3://{input_s3_asset_file_bucket}/{input_s3_asset_file_key}"
    output_object = (f"s3://{output_s3_asset_files_bucket}/"
                     f"{output_object_prefix(output_s3_asset_files_key, relative_subdir)}")

    q_input_object = shlex.quote(input_object)
    q_input_file = shlex.quote(input_file)
    q_output_file = shlex.quote(output_s3_asset_file_filename)
    q_output_object = shlex.quote(output_object)

    # format standard RapidPipeline command string
    standard_command_with_config = f"aws s3 cp {q_input_object} . && /rpdx/rpdx --read_config rp_config.json -i {q_input_file} -c -e {q_output_file} && aws s3 cp {q_output_file} {q_output_object}"
    standard_command_no_config = f"aws s3 cp {q_input_object} . && /rpdx/rpdx -i {q_input_file} -c -e {q_output_file} && aws s3 cp {q_output_file} {q_output_object}"

    # Handle custom configurations using the input configuration read from S3 above.
    if config:
        # Namespace the config object per execution so concurrent runs cannot read each other's
        # config (L13). jobName carries a millisecond stamp and a random suffix, so it stays distinct
        # for runs launched in the same second — one upload can fan out to several simultaneous runs
        # of this pipeline.
        config_key = f"rp_config_{event.get('jobName', 'default')}.json"
        # write config json file to S3
        s3.put_object(
            Body=json.dumps(config),
            Bucket=inputOutput_s3_assetAuxiliary_files_bucket,
            Key=config_key
        )
        q_config_object = shlex.quote(f"s3://{inputOutput_s3_assetAuxiliary_files_bucket}/{config_key}")
        # download config file from S3, read config file, then execute standard command
        command = f"aws s3 cp {q_config_object} rp_config.json && " + standard_command_with_config
    else:
        # if no input configuration is found, execute standard command
        command = standard_command_no_config


    commands = [
        "/bin/sh",
        "-c",
        command
    ]

    return commands

