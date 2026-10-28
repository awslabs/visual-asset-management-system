#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
Lambda Function to Call from within VAMS Pipeline and Workflows for Manual Execution.
Note: Lambda function name must start with "vams" to allow invoke permissioning from vams.
Resolves the input image + output locations from the workflow manifest, selects the image-to-3D
model, then invokes openPipeline.
"""
import os
import boto3
import json
from botocore.config import Config
from customLogging.logger import safeLogger
import manifestHelper

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="VamsExecuteImageTo3dPipeline")
lambda_client = boto3.client('lambda', config=retry_config)
s3_client = boto3.client('s3', config=retry_config)
# The region comes from the execution environment, with no fallback: AWS_REGION is always set by
# the Lambda and Batch runtimes, and a literal default is a commercial region that does not exist
# in GovCloud or the EU Sovereign Cloud, so a fallback can only ever point somewhere wrong.
sfn_client = boto3.client('stepfunctions', config=retry_config)
OPEN_PIPELINE_FUNCTION_NAME = os.environ["OPEN_PIPELINE_FUNCTION_NAME"]

# Model keys the pipeline can dispatch to. The value is the SFN Choice discriminator, the Dockerfile
# suffix and the per-model Batch job definition, so an unrecognized value cannot be forwarded.
SUPPORTED_MODELS = ("sam3d", "stable3dgen", "trellis2", "triposr", "step1x3d", "hunyuan3d", "blenderagent")
DEFAULT_MODEL = "stable3dgen"

# Models whose upstream pipeline accepts SEVERAL views of one subject in a single reconstruction, and the
# most it can use. Everything else takes one image, so a manifest carrying more is rejected here rather
# than silently reconstructing from whichever file happened to be first.
#
# Hunyuan3D-2 is the only one: its image processor maps named views onto view indices and batches them into
# one conditioning tensor. Stable3DGen has a multi-image entry point upstream that is not wired into its
# adapter, so it is deliberately absent until it is.
MULTI_VIEW_MODELS = {"hunyuan3d": 4, "blenderagent": 8}

# Models that may take an EXISTING MESH among their input files, as a starting point to refine. Every other
# model reconstructs from pixels and is refused a mesh here rather than failing inside the container.
MESH_INPUT_MODELS = ("blenderagent",)
MESH_INPUT_EXTENSIONS = (".glb", ".gltf", ".obj", ".stl", ".ply")

# The Blender agent's run modes, read from BLENDERAGENT_MODE, and those that fit an existing mesh to its
# photograph. The refit's other settings are checked by the container's adapter.
BLENDERAGENT_MODES = ("agent", "refit", "refit-agent")
BLENDERAGENT_REFIT_MODES = ("refit", "refit-agent")


def select_starting_meshes(keys):
    """The meshes that count as DISTINCT starting points: one per stem, preferring the glTF.

    A prior workflow step hands its whole output on -- the same model as .glb, .obj and .stl -- and
    those are one model, not three starting meshes; the glTF carries the texture, so it is the one
    kept. Meshes with different stems are different models and all stay. The container narrows the
    same way (``__main__.select_starting_meshes``), but the reconstruction instance is provisioned
    only after this check, so the collapse has to happen here too or a chained refine is refused
    before it starts.
    """
    preference = {".glb": 0, ".gltf": 1, ".obj": 2, ".ply": 3, ".stl": 4}
    by_stem = {}
    for key in keys:
        stem = os.path.splitext(os.path.basename(key))[0]
        ext = os.path.splitext(key)[1].lower()
        current = by_stem.get(stem)
        if current is None or preference.get(ext, 9) < preference.get(os.path.splitext(current)[1].lower(), 9):
            by_stem[stem] = key
    return [by_stem[stem] for stem in sorted(by_stem)]


def execute_pipeline(payload):
    logger.info("Invoking openPipeline Lambda .........")
    lambda_response = lambda_client.invoke(
        FunctionName=OPEN_PIPELINE_FUNCTION_NAME,
        InvocationType='RequestResponse',
        Payload=json.dumps(payload).encode('utf-8'),
    )
    logger.info("Open pipeline response", response=lambda_response)
    if 'StatusCode' not in lambda_response or lambda_response['StatusCode'] != 200:
        message = lambda_response.get("body", {}).get("message", "")
        raise Exception("Invoke Open Pipeline Lambda Failed. " + message)

    # A handled invocation still returns StatusCode 200 when the invoked function raised: the
    # failure is reported via FunctionError. Without this check an unhandled error in
    # openPipeline reads as success here, so no task-token failure is ever sent and the
    # workflow's callback task blocks until taskTimeout.
    if lambda_response.get('FunctionError'):
        raise Exception(
            "Invoke Open Pipeline Lambda Failed: " + str(lambda_response.get('FunctionError')))


def lambda_handler(event, context):
    logger.info("Event", event=event)
    external_task_token = None
    try:
        if not event.get('body'):
            return {'statusCode': 400, 'body': json.dumps({"message": 'Request body is required'})}
        data = json.loads(event['body']) if isinstance(event['body'], str) else event['body']

        if 'TaskToken' not in data:
            raise Exception("VAMS Workflow TaskToken not found in pipeline input. Register this pipeline as needing a task token callback.")
        external_task_token = data['TaskToken']

        # Input files, output paths, assetId and databaseId all come from the workflow manifest;
        # the Step Functions body carries only locations and identifiers.
        resolved = manifestHelper.resolve_pipeline_inputs(data, s3_client)
        logger.info(f"Resolved pipeline inputs (manifestUsed={resolved['manifestUsed']}): {resolved}")

        # A malformed manifest first entry leaves both of these empty, which the container cannot
        # recover from: it slices the input key on assetId to rebuild the relative output subdir.
        if not resolved['inputS3AssetFilePath']:
            raise Exception("No input image resolved for this execution. The workflow manifest carried no usable input file and the pipeline input had no inputS3AssetFilePath.")
        if not resolved['assetId']:
            raise Exception("No assetId resolved for this execution. The workflow manifest's first input file must carry an assetId.")

        # The input-configuration CONTENT is read from S3 here; only the LOCATION travels onward to
        # the container.
        input_cfg = manifestHelper.fetch_input_configuration(
            s3_client, resolved['inputConfigurationS3Location'])
        # A malformed configuration raises, but an unfetchable one returns {} like an absent one, so
        # the empty case is logged to tell the two apart.
        if not input_cfg:
            logger.warning(
                f"Input configuration resolved empty for location "
                f"'{resolved['inputConfigurationS3Location']}'. The run falls back to the template's "
                f"own defaults; an unfetchable configuration file looks identical to an empty one.")

        # The template's configuration body is the only source for the model. Asset and database
        # metadata are deliberately not consulted: the key resolved here picks the Batch job definition
        # and therefore the container IMAGE, while the container separately picks its adapter, so any
        # second input channel has to agree in both places or a run reconstructs with one model inside
        # another model's image -- which is exactly what happened when the two read different scopes.
        model = (input_cfg or {}).get("MODEL") or (input_cfg or {}).get("model") or DEFAULT_MODEL
        model = str(model).strip().lower()
        if model not in SUPPORTED_MODELS:
            raise Exception(
                f"Unsupported image-to-3D model '{model}'. Supported models: "
                f"{', '.join(SUPPORTED_MODELS)}.")
        logger.info(f"Resolved image-to-3D model: {model}")

        # How many input files this model may take is a property of the MODEL, so the check waits until
        # the model is known. enforce_single_input_file is the shared helper every other pipeline uses;
        # a multi-view model needs its own limit instead, and exceeding that limit is rejected rather
        # than truncated -- a reconstruction from three of four supplied views is a wrong answer that
        # looks like a right one.
        view_limit = MULTI_VIEW_MODELS.get(model)
        input_files = resolved.get('inputFiles') or []
        if view_limit is None:
            manifestHelper.enforce_single_input_file(resolved)
        elif len(input_files) > view_limit:
            raise Exception(
                f"Model '{model}' accepts at most {view_limit} views of one subject, but the workflow "
                f"manifest supplied {len(input_files)} input files.")

        # A mesh among the inputs is a starting point for a model that refines one, and nothing for a
        # model that does not; refused here, before a GPU instance is provisioned to discover it.
        mesh_inputs = [entry.get('key', '') for entry in input_files
                       if os.path.splitext(entry.get('key', ''))[1].lower() in MESH_INPUT_EXTENSIONS]
        if mesh_inputs and model not in MESH_INPUT_MODELS:
            raise Exception(
                f"Model '{model}' reconstructs from photographs and cannot take a mesh as input, but the "
                f"workflow manifest supplied {', '.join(os.path.basename(k) for k in mesh_inputs)}. "
                f"Choose the Blender agent to refine an existing model.")
        # A prior workflow step hands the same model on as .glb, .obj and .stl; those are ONE
        # starting point, so they are collapsed per stem (the glTF preferred) before the limit is
        # applied, exactly as the container does. Only genuinely distinct models exceed it.
        starting_meshes = select_starting_meshes(mesh_inputs)
        if len(starting_meshes) > 1:
            raise Exception(
                f"At most one mesh may be supplied as a starting point; got "
                f"{', '.join(os.path.basename(k) for k in starting_meshes)}.")

        # A refit fits an existing mesh to its photograph, so a refit run missing either is refused here,
        # before a GPU instance is provisioned to discover it. Photographs are told from texture sidecars
        # (material*.png) exactly as the container tells them apart. A blank mode is the default, as there.
        if model == "blenderagent":
            raw_mode = (input_cfg or {}).get("BLENDERAGENT_MODE")
            mode = ("" if raw_mode is None else str(raw_mode)).strip().lower() or "agent"
            if mode not in BLENDERAGENT_MODES:
                raise Exception(
                    f"BLENDERAGENT_MODE must be one of {', '.join(BLENDERAGENT_MODES)}, not '{mode}'.")
            photographs = [entry.get('key', '') for entry in input_files
                           if os.path.splitext(entry.get('key', ''))[1].lower() not in MESH_INPUT_EXTENSIONS
                           and not os.path.basename(entry.get('key', '')).lower().startswith("material")]
            missing = [what for what, supplied in (("no mesh", starting_meshes), ("no photograph", photographs))
                       if not supplied]
            if mode in BLENDERAGENT_REFIT_MODES and missing:
                raise Exception(
                    f"BLENDERAGENT_MODE {mode} fits an existing mesh to its photograph, but the workflow "
                    f"manifest supplied {' and '.join(missing)}. Select another model's output alongside the "
                    f"photograph it was made from.")

        # Every input file as an s3:// URI, in manifest order. The FIRST stays the primary: it is what
        # asset identity, the relative output subdirectory and every single-view model read, so this list
        # is additive and an existing single-image run is unchanged by it.
        input_paths = [f"s3://{entry.get('bucket', '')}/{entry.get('key', '')}"
                       for entry in input_files
                       if entry.get('bucket') and entry.get('key')]
        if len(input_paths) > 1:
            logger.info(f"Multi-view run: {len(input_paths)} views for model '{model}'")

        payload = {
            "inputS3AssetFilePath": resolved['inputS3AssetFilePath'],
            # Additive: the full view list for a multi-view model. Empty or single-entry for every
            # other run, whose behaviour is driven by inputS3AssetFilePath exactly as before.
            "inputS3AssetFilePaths": input_paths,
            "outputS3AssetFilesPath": resolved['outputS3AssetFilesPath'],
            "outputS3AssetPreviewPath": resolved['outputS3AssetPreviewPath'],
            "outputS3AssetMetadataPath": resolved['outputS3AssetMetadataPath'],
            # The prefix whose contents the workflow reads onto the EXECUTION rather than into
            # the asset, so the gate report and the benchmark are visible against the run.
            "outputS3AssetResultsPath": resolved['outputS3AssetResultsPath'],
            "inputOutputS3AssetAuxiliaryFilesPath": resolved['inputOutputS3AssetAuxiliaryFilesPath'],
            # Metadata + input-configuration S3 LOCATIONS travel onward (never the inline content);
            # the container reads them from S3 as needed.
            "inputMetadataS3Location": resolved['inputMetadataS3Location'],
            "inputConfigurationS3Location": resolved['inputConfigurationS3Location'],
            "assetId": resolved['assetId'],
            "databaseId": resolved['databaseId'],
            # The Regions of the input files' bucket and of the output bucket ("" is the deployment
            # Region). The container signs each bucket's requests for its own Region.
            "inputBucketRegion": resolved['inputBucketRegion'],
            "outputBucketRegion": resolved['outputBucketRegion'],
            "model": model,
            "sfnExternalTaskToken": external_task_token,
            "executingUserName": data.get('executingUserName', ''),
            "executingRequestContext": data.get('executingRequestContext', ''),
            "orchestrationEventPrefix": resolved['orchestrationEventPrefix'],
        }

        execute_pipeline(payload)
        return {'statusCode': 200, 'body': 'Success'}
    except Exception as e:
        logger.exception(e)
        if external_task_token:
            try:
                sfn_client.send_task_failure(
                    taskToken=external_task_token,
                    error="ImageTo3dPipelineError",
                    cause=str(e)[:256],
                )
            except Exception as sfn_err:
                logger.error(f"Failed to send task failure callback: {sfn_err}")
        return {'statusCode': 500, 'body': json.dumps({"message": str(e)})}
