# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
import shutil
import tempfile
import threading
import os
import trimesh
from common.logger import safeLogger
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="conversionTrimeshPipeline")

s3_client = boto3.client('s3', config=retry_config)
s3 = boto3.resource('s3', config=retry_config)


def download(bucket_name, object_key, file_path):
    logger.info(
        "Downloading Object from S3 Bucket. Bucket: {}, Object: {}, File Path: {}".format(
            bucket_name, object_key, file_path
        )
    )
    try:
        with open(file_path, "wb") as data:
            s3_client.download_fileobj(bucket_name, object_key, data)
    except ClientError as e:
        logger.exception(e)
        raise Exception("Could not download input file from S3 bucket")
    return file_path


def uploadV2(bucket_name, object_key, file_path):
    logger.info(
        f"Uploading Object to S3 Bucket w/ auto chunking for multi-part.\nBucket:{bucket_name}.\n:Object: {object_key}"
    )

   # Multipart upload
    try:
        GB = 1024 ** 3
        MB = 1024 ** 2
        config = TransferConfig(multipart_threshold=1*GB, max_concurrency=10,
                                multipart_chunksize=100*MB, use_threads=True
                                )
        s3.meta.client.upload_file(file_path, bucket_name, object_key,
                                   ExtraArgs={},
                                   Config=config,
                                   Callback=ProgressPercentage(file_path)
                                   )
    except ClientError as e:
        logger.exception(e)
        raise Exception("Could not upload output file to S3 bucket")
    return object_key
    
class ManifestReadError(RuntimeError):
    """Raised when a referenced workflow input manifest cannot be read or is not a JSON object."""


def fetch_manifest(manifest_s3_location):
    """Fetch + parse the workflow input manifest from its S3 location.

    ``None`` when no location was supplied, so a legacy payload carrying its fields inline still
    resolves. A location that IS supplied but is malformed, unreadable, or does not parse to a JSON
    object raises ``ManifestReadError``.

    Mirrors ``manifestHelper.fetch_manifest``, which the pipelines with a ``lambda/`` code asset
    vendor: the manifest is the only carrier of the input file's asset identity and of the run's
    output paths, so answering a read failure with an empty manifest downgrades the run to the
    legacy body fields — writing output to whatever path those name, or failing later with an error
    that does not mention the manifest.
    """
    if not manifest_s3_location:
        return None
    if not manifest_s3_location.startswith("s3://"):
        raise ManifestReadError(
            f"The workflow supplied a malformed input manifest location: {manifest_s3_location}")
    bucket, _, key = manifest_s3_location[len("s3://"):].partition("/")
    if not bucket or not key:
        raise ManifestReadError(
            f"The workflow supplied a malformed input manifest location: {manifest_s3_location}")
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        manifest = json.loads(body)
    except Exception as e:
        raise ManifestReadError(
            f"Could not read the workflow input manifest at {manifest_s3_location}: {e}")
    if not isinstance(manifest, dict):
        raise ManifestReadError(
            f"The workflow input manifest at {manifest_s3_location} is not a JSON object")
    return manifest


class InputConfigurationError(RuntimeError):
    """Raised when an input-configuration file exists but cannot be parsed as a JSON object."""


def fetch_input_configuration(input_configuration_s3_location):
    """Fetch + parse the per-pipeline input configuration (inputParameters) from its S3 location.

    ``{}`` when no configuration was supplied or it could not be fetched. Raises
    ``InputConfigurationError`` when the file WAS fetched but its body is not a JSON object.

    Parsed here rather than through ``fetch_manifest`` because the two carry different weight: a
    configuration is optional, so an absent one legitimately leaves the pipeline reading its target
    format from the legacy inline body field, whereas a referenced manifest is the run's only
    statement of what to read and where to write it. An unparseable configuration still raises so the
    error names the configuration; a fetch failure yields ``{}`` and, with no inline field to fall
    back to, surfaces one step later as an empty output format the format check rejects.
    """
    if not input_configuration_s3_location:
        return {}
    if not input_configuration_s3_location.startswith("s3://"):
        return {}
    bucket, _, key = input_configuration_s3_location[len("s3://"):].partition("/")
    if not bucket or not key:
        return {}
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    except Exception as e:
        logger.warning(f"Could not read {input_configuration_s3_location}: {e}")
        return {}
    if not body or not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except ValueError as e:
        raise InputConfigurationError(
            f"The input configuration at {input_configuration_s3_location} is not valid JSON: {e}")
    if not isinstance(parsed, dict):
        raise InputConfigurationError(
            f"The input configuration at {input_configuration_s3_location} is not a JSON object")
    return parsed


def relative_subdir_from_manifest_path(relative_path):
    """The input file's subdirectory within the asset, derived from its asset-relative manifest
    path ('/parts/housing/model.obj' -> 'parts/housing'). A file at the asset root yields ''."""
    trimmed = (relative_path or "").strip("/")
    if "/" not in trimmed:
        return ""
    return trimmed.rsplit("/", 1)[0]


# The mesh formats this pipeline accepts as input: every one is a format trimesh loads with the
# container's pinned dependencies (requirements.txt / poetry.lock). COLLADA (`.dae`) needs pycollada
# and `.3mf` / `.xaml` / `.3dxml` need lxml and networkx; with none of those installed trimesh
# registers an exception wrapper in place of each loader. The registered inputFileFilters in
# vamsSchema/pipeline.json declare this same set to VAMS.
SUPPORTED_INPUT_FORMATS = {'.stl', '.obj', '.ply', '.gltf', '.glb', '.xyz'}

# The formats it can produce as output. Loading a format is not the same as writing it: `.xyz` is a
# point-cloud text format trimesh reads into a PointCloud, and its xyz exporter accepts a PointCloud
# only and raises even for one, so nothing this pipeline loads can be written as `.xyz`. `.xaml` and
# `.3dxml` have no exporter at any dependency set, and the `.3mf` exporter is registered behind the
# same lxml and networkx pair as its loader, which is why the requested output type is checked against
# this set rather than against the accept list.
SUPPORTED_OUTPUT_FORMATS = {'.stl', '.obj', '.ply', '.gltf', '.glb'}


# The folder a converted file is written under when the conversion does not change the file
# extension, so a same-format re-export is a sibling of its source rather than a new version of it.
# The name matches rapidPipeline's, so every conversion pipeline places a same-format output alike.
SAME_FORMAT_OUTPUT_SUBDIR = "optimized"


def output_relative_subdir(relative_subdir, input_extension, output_extension):
    """The subdirectory the converted file is written under, relative to the output-files prefix:
    the input file's own subdirectory within the asset, plus a trailing `optimized` folder when the
    conversion does not change the file extension.

    Every format this pipeline exports is also a format it accepts, and each built-in template pins
    one target format, so a template whose target is the input file's own format is a same-extension
    run (`.glb` with Convert to GLB, `.gltf` with Convert to GLTF, `.obj` with Convert to OBJ,
    `.stl` with Convert to STL). The output keeps both the input's subdirectory and its file name, so
    in that case the output's ASSET-RELATIVE path equals the input's; the workflow's process-output
    step writes each staged output back to the output asset at exactly that relative path, so the
    write-back would land a new version of the operator's source object rather than a sibling file.
    The extra folder is what keeps the two apart, and it is a folder rather than a changed file name
    because the name is what identifies the converted model.

    The two extensions decide it on their own: the output file name is the input's stem plus the
    output extension, so equal extensions is exactly the case where the two names — and therefore the
    two relative paths — coincide. Both are compared in the lower case this pipeline normalizes them
    to, so a `.STL` source re-exported as `.stl` is also treated as same-format: its output would
    otherwise differ from its source by extension case alone. A format-changing conversion still
    lands directly beside its source, and the folder is constant per format, so it separates the
    output from the input rather than separating runs from each other (the workflow's own output path
    extension does that).
    """
    subdir = (relative_subdir or "").strip("/")
    if input_extension != output_extension:
        return subdir
    return f"{subdir}/{SAME_FORMAT_OUTPUT_SUBDIR}" if subdir else SAME_FORMAT_OUTPUT_SUBDIR


def resolve_inputs_from_manifest(data):
    """Resolve the input file path, output-files path and the input's asset-relative subdirectory
    from the workflow manifest (inputManifestS3Location), falling back to the legacy top-level body
    fields for direct/local invocations. Locations are carried as bucket + relative keys, so s3://
    URIs are reconstructed here. Returns (input_s3_asset_file_path, output_s3_asset_files_path,
    relative_subdir).

    Mirrors ``manifestHelper.resolve_inputs`` + ``enforce_single_input_file``, which the pipelines
    with a ``lambda/`` code asset vendor; this pipeline is a container image, so it reads the same
    envelope fields directly. Any change to the envelope applies to both."""
    manifest = fetch_manifest(data.get("inputManifestS3Location", ""))
    input_files = (manifest or {}).get("inputFiles") or []
    # The pipeline is registered with inputFileArity 'one' and converts a single mesh per
    # execution; more than one resolved input would be silently dropped.
    if len(input_files) > 1:
        raise ValueError(
            f"This pipeline processes a single input file per execution, but the workflow "
            f"manifest supplied {len(input_files)} input files. Multi-file input is not yet "
            f"supported for this pipeline."
        )
    input_path = ""
    relative_subdir = ""
    if input_files:
        first = input_files[0]
        if first.get("bucket") and first.get("key"):
            input_path = f"s3://{first['bucket']}/{first['key']}"
        relative_subdir = relative_subdir_from_manifest_path(first.get("relativePath"))
    input_path = input_path or data.get("inputS3AssetFilePath", "")
    # Output-files path reconstructed from the outputs bucket + bucket-relative files prefix.
    outputs = (manifest or {}).get("outputs", {})
    output_path = ""
    if outputs.get("bucket") and outputs.get("files"):
        output_path = f"s3://{outputs['bucket']}/{outputs['files']}"
    output_path = output_path or data.get("outputS3AssetFilesPath", "")
    return input_path, output_path, relative_subdir


def exported_files(export_dir):
    """Every file the export wrote, as paths relative to ``export_dir`` with forward slashes, sorted.

    The export is read back from disk rather than assumed to be the one file it was asked for,
    because what an exporter writes depends on the model as well as the format: a `.gltf` export
    always writes its vertex data to companion `gltf_buffer_N.bin` files, and an `.obj` export writes
    `material.mtl` plus a texture image only when the mesh carries one. Uploading a fixed name
    instead leaves the model referencing companions that were never stored, which for `.gltf` means
    it cannot be opened at all.
    """
    found = []
    for root, _, file_names in os.walk(export_dir):
        for file_name in file_names:
            relative = os.path.relpath(os.path.join(root, file_name), export_dir)
            found.append(relative.replace(os.sep, "/"))
    return sorted(found)


def convert_input_output(input_path, output_path, output_filetype, relative_subdir=""):
    input_bucket, input_key = input_path.replace("s3://", "").split("/", 1)
    output_bucket, output_key = output_path.replace("s3://", "").split("/", 1)
    logger.info(input_key)
    logger.info(output_key)

    #Folder check
    if (input_key.endswith("/")):
        raise ValueError("Input S3 URI cannot be a folder")

    # Check input and output formats. Extensions are compared case-insensitively to match the
    # registered inputFileFilters, which match a file's extension regardless of case.
    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_key)
    input_s3_asset_extension = input_s3_asset_extension.lower()
    output_filetype = (output_filetype or "").lower()
    if (not input_s3_asset_extension or input_s3_asset_extension == '' or input_s3_asset_extension not in SUPPORTED_INPUT_FORMATS):
        raise ValueError(f"Input format {input_s3_asset_extension} not supported by Trimesh pipeline")
    if output_filetype not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(f"Output format {output_filetype} not supported by Trimesh pipeline")

    # Download, convert and upload inside a working directory of this invocation's own, removed
    # before returning either way. The Lambda execution environment is reused between invocations
    # and its /tmp is a fixed budget set by the function's ephemeralStorageSize (see
    # conversion3dBasicFunctions.ts), so files left behind reduce what the next conversion has to
    # work with until one fails on space rather than on its own content.
    #
    # The download and the export get separate subdirectories because everything the export
    # directory holds is uploaded as output: the input has to be somewhere the upload cannot see it.
    work_dir = tempfile.mkdtemp()
    try:
        input_dir = os.path.join(work_dir, 'input')
        export_dir = os.path.join(work_dir, 'export')
        os.makedirs(input_dir)
        os.makedirs(export_dir)

        # Download input file from S3
        temp_file = os.path.join(input_dir, f'input{input_s3_asset_extension}')
        download(input_bucket, input_key, temp_file)

        # Load mesh using trimesh
        mesh = trimesh.load(temp_file)

        # Export mesh to output format, under the name it will carry in the asset: the input file's
        # own name with the output extension.
        # NOTE: SUPPORTED_OUTPUT_FORMATS (and therefore output_filetype) carry a leading dot, e.g. ".stl",
        # but trimesh's exporter registry is keyed without it ("stl"). Passing the dotted form raises
        # ValueError("%s exporter not available!", ".stl") and fails every conversion, so strip it here.
        outputFileName, _ = os.path.splitext(os.path.basename(input_key)) #get the original file name without extension
        outputFileName = f"{outputFileName}{output_filetype}" #add final output extension
        output_file = os.path.join(export_dir, outputFileName)
        mesh.export(output_file, file_type=output_filetype.lstrip('.'))

        # Upload output file to S3. The converted file keeps the input file's subdirectory within the
        # asset so the write-back step places it beside the input rather than at the asset root. A
        # conversion that does not change the file extension gains one further folder, so the write-back
        # cannot resolve to the input's own key.
        output_subdir = output_relative_subdir(
            relative_subdir, input_s3_asset_extension, output_filetype)
        if not output_key.endswith("/"):
            output_key += "/"
        if output_subdir:
            output_key = f"{output_key}{output_subdir}/"

        exported = exported_files(export_dir)
        if outputFileName not in exported:
            raise RuntimeError(
                f"The Trimesh export of {outputFileName} produced no such file "
                f"(the export directory holds {exported or 'nothing'})")

        # An export that emitted companion files is placed in a folder of its own, named after the
        # converted model. Companions are referenced by the model RELATIVE to itself and their names
        # come from the exporter rather than from the model — a `.gltf`'s buffers are always
        # `gltf_buffer_N.bin` and a textured `.obj`'s material is always `material.mtl` — so two
        # conversions landing in one asset directory would otherwise overwrite each other's
        # companions and leave both models pointing at one set. Grouping keeps each set with its own
        # model, and keeps the references resolving, since the companions stay beside it.
        # A single-file export keeps its place directly beside the source.
        if len(exported) > 1:
            output_key = f"{output_key}{os.path.splitext(outputFileName)[0]}/"
        for relative_name in exported:
            uploadV2(output_bucket, f"{output_key}{relative_name}",
                     os.path.join(export_dir, relative_name)) #upload to storage
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    logger.info("Conversion complete")


def lambda_handler(event, context):

    logger.info(event)
    response = {
        'statusCode': 200,
        'body': '',
        'headers': {
            'Content-Type': 'application/json'
        }
    }

    # Parse request body
    if not event.get('body'):
        message = 'Request body is required'
        logger.error(message)
        raise ValueError(message)

    if isinstance(event['body'], str):
        data = json.loads(event['body'])
    else:
        data = event['body']

    # Check external task token if passed (Synchronous Pipeline so no task token should be passed)
    if 'TaskToken' in data:
        raise Exception("VAMS Workflow TaskToken found in pipeline input. Make sure to register this pipeline in VAMS as NOT needing a task token callback.")
        
    # Read the input configuration from its S3 location (inline fallback for transition).
    input_configuration = fetch_input_configuration(data.get('inputConfigurationS3Location', ''))
    if not input_configuration and data.get('inputParameters'):
        inline = data['inputParameters']
        input_configuration = json.loads(inline) if isinstance(inline, str) else inline

    # The target output format comes from the input configuration (outputType). Fall back to the
    # legacy inline body field for executions whose ASL predates this change.
    output_filetype = (input_configuration or {}).get('outputType') or data.get('outputType', '')

    #Get Executing username
    if 'executingUserName' in data:
        executing_userName = data['executingUserName']
    else:
        executing_userName = ''

    #Get Executing requestContext
    if 'executingRequestContext' in data:
        executing_requestContext = data['executingRequestContext']
    else:
        executing_requestContext = ''

    # Resolve the input file + output-files paths and the input's asset-relative subdirectory from
    # the workflow manifest (legacy body fields are the fallback for direct/local invocations).
    input_path, output_path, relative_subdir = resolve_inputs_from_manifest(data)

    convert_input_output(input_path, output_path, output_filetype, relative_subdir)

    return {
        'statusCode': 200, 
        'body': 'Success'
    }



# Class for multipart upload
class ProgressPercentage(object):
    def __init__(self, filename):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount):
        # To simplify we'll assume this is hooked up
        # to a single filename.
        with self._lock:
            self._seen_so_far += bytes_amount
            percentage = (self._seen_so_far / self._size) * 100
