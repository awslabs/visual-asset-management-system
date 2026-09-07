#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
ConstructPipeline Lambda
Builds the Batch job definition for IsaacLab training or evaluation.
The run configuration comes from the manifest-delivered input configuration; a JSON input file may
supply standing defaults for the fields it leaves blank.
"""

import json
import re
from datetime import datetime, timezone
import boto3
from urllib.parse import urlparse
from customLogging.logger import safeLogger
from botocore.config import Config
from botocore.exceptions import ClientError

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="OpenPipelineIsaacLabTraining")
s3_client = boto3.client("s3", config=retry_config)
sfn_client = boto3.client("stepfunctions", config=retry_config)

# Sort floor for a checkpoint whose S3 object carries no LastModified.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

DEFAULT_TASK = "Isaac-Cartpole-v0"
DEFAULT_RL_LIBRARY = "rsl_rl"
DEFAULT_NUM_ENVS_TRAIN = 4096
DEFAULT_NUM_ENVS_EVAL = 100
DEFAULT_MAX_ITERATIONS = 1500
DEFAULT_NUM_EPISODES = 50
DEFAULT_STEPS_PER_EPISODE = 1000

# The RL libraries the container has an Isaac Lab script for. A third copy of the list held in
# container/utils/training/config.py (RL_LIBRARIES) and container/__main__.py (the train and play
# script maps): the lambda's code bundle is this directory alone, so the container package is not
# importable here. lambda/tests/test_config_precedence.py asserts the copies still agree.
SUPPORTED_RL_LIBRARIES = ("rsl_rl", "rl_games", "skrl")

# Ceilings for the operator-supplied counts, each far above any shipped default. They catch a value
# that cannot be what was meant — a mistyped extra digit — rather than describing capacity, which
# depends on the task and the GPU: a count inside its ceiling can still exhaust GPU memory. A second
# copy lives in container/utils/training/config.py, which covers a direct container invocation;
# rejecting the value here is what keeps a Batch GPU node from being provisioned for it at all.
# lambda/tests/test_isaaclab_numeric_bounds.py asserts the two copies agree.
MAX_NUM_ENVS = 65536
MAX_MAX_ITERATIONS = 100000
MAX_NUM_EPISODES = 10000
MAX_STEPS_PER_EPISODE = 100000


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token so a failure here does not leave the
    pipeline task waiting for its full taskTimeout. This is the first state of the pipeline's state
    machine, so the container never starts and nothing downstream can report on the token.

    Never raises: the caller re-raises the original error, which is the one worth reading."""
    if not task_token:
        return
    try:
        sfn_client.send_task_failure(
            taskToken=task_token,
            error="IsaacLabPipelineError",
            cause=str(error)[:256]
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    logger.info(f"Event: {event}")

    try:
        return build_job_config_payload(event)
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, event.get("externalSfnTaskToken", ""))
        raise


def build_job_config_payload(event):
    """The state machine payload carrying the Batch job's run configuration."""

    # Standing defaults a JSON input file may carry; the manifest configuration outranks them.
    file_config = load_config_from_s3(event.get("inputS3AssetFilePath"))

    training_config = merge_configs(
        file_config.get("trainingConfig", {}),
        event.get("trainingConfig", {})
    )

    mode = training_config.get("mode", "train")
    task = require_task(training_config.get("task") or DEFAULT_TASK)
    rl_library = resolve_rl_library(training_config.get("rlLibrary") or DEFAULT_RL_LIBRARY)

    if mode == "train":
        job_config = build_training_config(event, training_config, task, rl_library)
    elif mode == "evaluate":
        job_config = build_evaluation_config(event, training_config, task, rl_library)
    else:
        raise ValueError(f"Invalid mode: {mode}. Must be 'train' or 'evaluate'")

    logger.info(f"Job config: {job_config}")

    return {
        "jobName": event.get("jobName"),
        "definition": json.dumps(job_config),
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath"),
        "outputS3AssetFilesPath": job_config.get("outputS3AssetFilesPath", ""),
        "status": "STARTING",
    }


def resolve_rl_library(rl_library) -> str:
    """The RL library the run asked for, checked against the set the container has a script for.

    An unrecognised value is rejected rather than substituted, so a run cannot complete against a
    library the operator did not ask for and leave checkpoints the requested library cannot load.
    The container rejects it too; doing it here is what makes the rejection free — this is the first
    state of the pipeline's state machine, so no GPU node is provisioned and no container image is
    pulled.
    """
    value = rl_library.strip() if isinstance(rl_library, str) else rl_library
    if value not in SUPPORTED_RL_LIBRARIES:
        raise ValueError(
            f"Unsupported rlLibrary '{rl_library}'. Supported libraries: "
            f"{', '.join(sorted(SUPPORTED_RL_LIBRARIES))}"
        )
    return value


def _parse_whole(value):
    """``value`` as an int when it names one exactly, else ``None``.

    Rejects a fraction rather than truncating it, and rejects ``nan``/``inf``, which are integral to
    neither ``int()`` nor ``is_integer()``.
    """
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


def whole_number(field: str, value, default, minimum=1, maximum=None):
    """The whole number ``field`` carries, or ``default`` when the run does not set it.

    A quoted number is accepted because a hand-edited config body or a JSON input file easily carries
    one, and because a string reaching the container multiplies instead of adding up: ``numEpisodes``
    of ``"50"`` times ``stepsPerEpisode`` of 1000 is a 2000-character ``--video_length`` argument
    rather than 50000. A boolean, a fraction, a blank value, or a count outside
    ``minimum``..``maximum`` is rejected naming the field, before any Batch job is submitted.
    """
    if value is None:
        return default

    if isinstance(value, bool):
        raise ValueError(f"{field} must be a whole number, not a boolean (received {value!r})")

    number = value if isinstance(value, int) else None
    if number is None and isinstance(value, (float, str)):
        number = _parse_whole(value)
    if number is None:
        raise ValueError(f"{field} must be a whole number (received {value!r})")

    if minimum is not None and number < minimum:
        raise ValueError(f"{field} must be {minimum} or greater (received {number})")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field} must be {maximum} or less (received {number})")
    return number


def require_task(value) -> str:
    """The Isaac Lab task id the run names, checked non-blank.

    The task reaches ``--task`` on the container's command line, where a blank value fails only after
    Isaac Sim has started.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"task must be a non-blank Isaac Lab task id (received {value!r})")
    return value.strip()


def load_config_from_s3(s3_uri: str) -> dict:
    """Standing defaults from a JSON input file, or {} when the file carries none.

    An input file is an ASSET file the operator selected, not the run's configuration, so anything
    it holds is a fallback only. A file that is not JSON, is not a JSON object, or does not parse
    yields no defaults rather than an error.
    """
    if not s3_uri:
        return {}

    try:
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")

        if not key.endswith(".json"):
            logger.info(f"Input file is not JSON, skipping config parsing: {key}")
            return {}

        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read().decode("utf-8")
        config = json.loads(content)
        if not isinstance(config, dict):
            logger.info(f"Input JSON file is not a config object, skipping: {key}")
            return {}
        logger.info(f"Loaded config defaults from S3: {config}")
        return config
    except Exception as e:
        logger.warning(f"Failed to load config from S3: {e}")
        return {}


def _as_utc(last_modified) -> datetime:
    """A comparable timezone-aware timestamp for an S3 object's LastModified. A missing value or a
    naive datetime is normalized so run folders always compare against each other."""
    if not isinstance(last_modified, datetime):
        return _EPOCH
    if last_modified.tzinfo is None:
        return last_modified.replace(tzinfo=timezone.utc)
    return last_modified


def checkpoint_iteration(key: str) -> int:
    """The training iteration encoded in a checkpoint file name (``model_1500.pt`` -> 1500).

    Reads the last run of digits in the file name; a name carrying no digits yields -1 so it sorts
    below every numbered checkpoint.
    """
    file_name = key.rsplit("/", 1)[-1]
    digits = re.findall(r"\d+", file_name)
    return int(digits[-1]) if digits else -1


def discover_policy_file(bucket: str, asset_location_key: str) -> str:
    """Discover .pt policy file anywhere under the asset root in S3.

    Searches under bucketAsset + inputAssetLocationKey (the authoritative asset
    root), not under the input file's parent directory. The asset root is the
    only reliable starting point — input files may live at arbitrary depths
    beneath it, and deriving the root from the input file path is unsafe.

    Each training run writes its checkpoints under its own execution folder, so selection is
    per-run: the run folder holding the most recently written checkpoint wins, and within it the
    highest training iteration (numeric, not lexicographic).

    Args:
        bucket: The asset S3 bucket name (bucketAsset).
        asset_location_key: The asset root prefix within the bucket (inputAssetLocationKey).

    Returns:
        S3 URI of the discovered .pt file, or empty string if not found.
    """
    if not bucket or not asset_location_key:
        return ""

    try:
        prefix = asset_location_key if asset_location_key.endswith("/") else asset_location_key + "/"

        logger.info(f"Searching for .pt files under asset root s3://{bucket}/{prefix}")

        # Per run folder (the checkpoint's parent prefix): its checkpoints and its newest write time.
        runs = {}
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if not key.endswith(".pt"):
                    continue
                run_prefix = key.rsplit("/", 1)[0]
                run = runs.setdefault(run_prefix, {"keys": [], "lastModified": _EPOCH})
                run["keys"].append(key)
                last_modified = _as_utc(obj.get("LastModified"))
                if last_modified > run["lastModified"]:
                    run["lastModified"] = last_modified

        if runs:
            newest_run_prefix = max(runs, key=lambda p: (runs[p]["lastModified"], p))
            policy_key = max(runs[newest_run_prefix]["keys"], key=checkpoint_iteration)
            policy_uri = f"s3://{bucket}/{policy_key}"
            logger.info(f"Discovered policy file: {policy_uri}")
            return policy_uri

        logger.info("No .pt files found under asset root")
        return ""
    except Exception as e:
        logger.warning(f"Failed to discover policy file: {e}")
        return ""


def merge_configs(base: dict, override: dict) -> dict:
    """Merge two config sections, with override taking priority per key.

    A key the override leaves absent, null, or blank falls through to the base, which is what lets an
    input file hold a standing default for a field the run's configuration does not set.
    """
    result = base.copy() if isinstance(base, dict) else {}
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if value is not None and str(value).strip() != "":
            result[key] = value
    return result


def resolve_relative_path(bucket: str, asset_location_key: str, relative_path: str) -> str:
    """Resolve a relative path to a full S3 URI based on the asset root.

    Uses bucketAsset + inputAssetLocationKey as the authoritative asset root.
    Do not derive the asset root from inputS3AssetFilePath — the input file may
    be nested arbitrarily deep under the asset root, so path-segment math on it
    cannot correctly locate the asset root.

    Args:
        bucket: The asset S3 bucket name (bucketAsset).
        asset_location_key: The asset root prefix within the bucket (inputAssetLocationKey).
        relative_path: Relative path within the asset (e.g., "environments/my_env.tar.gz").

    Returns:
        Full S3 URI to the file.
    """
    prefix = asset_location_key if asset_location_key.endswith("/") else asset_location_key + "/"
    return f"s3://{bucket}/{prefix}{relative_path.lstrip('/')}"


def require_asset_bucket(field: str, s3_uri: str, event) -> str:
    """Confirm an operator-supplied S3 URI names the executing asset's own bucket.

    Both `customEnvironmentS3Uri` and `policyS3Uri` reach the container as objects it downloads and
    then executes: the environment package is installed with pip, which runs its setup code, and the
    policy is deserialized by torch. Their values arrive from a template config body or the content of
    a JSON input file, so the bucket is constrained here rather than left to the job role, whose S3
    read spans every registered VAMS asset bucket plus the auxiliary bucket.

    Fails closed on a missing `bucketAsset`, which is the same condition the relative-path routes
    already reject: without it there is nothing to compare against.
    """
    bucket = event.get("bucketAsset", "")
    if not bucket:
        raise ValueError(
            f"Cannot validate {field}: pipeline is missing bucketAsset. Ensure vamsExecute "
            "passes it through from the workflow."
        )

    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3":
        raise ValueError(f"{field} must be an s3:// URI (received scheme '{parsed.scheme}')")
    if parsed.netloc != bucket:
        raise ValueError(
            f"{field} must reference the asset's own bucket '{bucket}', not '{parsed.netloc}'"
        )
    return s3_uri


def require_existing_object(field: str, s3_uri: str) -> str:
    """Confirm the object an operator-supplied S3 URI names is there.

    A mistyped `checkpointPath` or `customEnvironmentPath` otherwise fails inside the container, after
    a GPU node has started and the image has been pulled. Only a definite not-found is rejected: any
    other outcome — a denied HeadObject, a transient error — leaves the run to proceed, so a
    permissions gap cannot turn a working run into a rejected one.
    """
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        return s3_uri

    try:
        s3_client.head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey"):
            raise ValueError(f"{field} names no object in the asset bucket: {s3_uri}")
        logger.warning(f"Could not confirm {field} exists ({code}), continuing: {s3_uri}")
    except Exception as e:
        logger.warning(f"Could not confirm {field} exists, continuing: {e}")
    return s3_uri


def resolve_custom_environment_path(event, training_config) -> str:
    """Resolve custom environment path - supports relative or absolute S3 paths.

    Priority order:
    1. customEnvironmentPath - relative path within asset (e.g., "environments/my_env.tar.gz")
    2. customEnvironmentS3Uri - explicit full S3 URI

    Either route must resolve inside the executing asset's own bucket; the check sits at the single
    exit so both routes and any future one pass through it.

    Returns:
        Full S3 URI to the custom environment package, or empty string if not specified
    """
    bucket = event.get("bucketAsset", "")
    asset_location_key = event.get("inputAssetLocationKey", "")
    resolved = ""

    # 1. Check for relative customEnvironmentPath (preferred)
    custom_env_path = training_config.get("customEnvironmentPath")
    if custom_env_path:
        if not bucket or not asset_location_key:
            raise ValueError(
                "Cannot resolve customEnvironmentPath: pipeline is missing bucketAsset "
                "or inputAssetLocationKey. Ensure vamsExecute passes these through from the workflow."
            )
        resolved = resolve_relative_path(bucket, asset_location_key, custom_env_path)
        logger.info(f"Using customEnvironmentPath: {resolved}")

    # 2. Check for explicit customEnvironmentS3Uri
    if not resolved:
        custom_env_uri = training_config.get("customEnvironmentS3Uri")
        if custom_env_uri:
            resolved = custom_env_uri
            logger.info(f"Using customEnvironmentS3Uri: {resolved}")

    if not resolved:
        return ""

    return require_existing_object(
        "customEnvironmentS3Uri",
        require_asset_bucket("customEnvironmentS3Uri", resolved, event))


def build_training_config(event, training_config, task, rl_library):
    """Build configuration for training mode."""
    num_envs = whole_number("numEnvs", training_config.get("numEnvs"),
                            DEFAULT_NUM_ENVS_TRAIN, maximum=MAX_NUM_ENVS)
    max_iterations = whole_number("maxIterations", training_config.get("maxIterations"),
                                  DEFAULT_MAX_ITERATIONS, maximum=MAX_MAX_ITERATIONS)
    seed = whole_number("seed", training_config.get("seed"), None, minimum=None)
    job_name = event.get("jobName") or "unknown"

    # Use VAMS standard output path (asset bucket)
    output_path = event.get("outputS3AssetFilesPath", "")
    
    # Resolve custom environment path (relative or absolute)
    custom_env_s3_uri = resolve_custom_environment_path(event, training_config)

    return {
        "jobName": job_name,
        "trainingConfig": {
            "mode": "train",
            "task": task,
            "numEnvs": num_envs,
            "maxIterations": max_iterations,
            "rlLibrary": rl_library,
            "seed": seed,
        },
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath"),
        "customEnvironmentS3Uri": custom_env_s3_uri,
        "outputS3AssetFilesPath": output_path,
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }


def build_evaluation_config(event, training_config, task, rl_library):
    """Build configuration for evaluation mode.
    
    Policy file discovery (in priority order):
    1. checkpointPath - relative path within asset directory (e.g., "checkpoints/model_300.pt")
    2. policyS3Uri - explicit full S3 URI
    3. Auto-discover .pt files in input directory (backward compatibility)
    """
    num_envs = whole_number("numEnvs", training_config.get("numEnvs"),
                            DEFAULT_NUM_ENVS_EVAL, maximum=MAX_NUM_ENVS)
    num_episodes = whole_number("numEpisodes", training_config.get("numEpisodes"),
                                DEFAULT_NUM_EPISODES, maximum=MAX_NUM_EPISODES)
    steps_per_episode = whole_number("stepsPerEpisode", training_config.get("stepsPerEpisode"),
                                     DEFAULT_STEPS_PER_EPISODE, maximum=MAX_STEPS_PER_EPISODE)
    record_video = training_config.get("recordVideo", False)

    # Policy discovery with priority: checkpointPath > policyS3Uri > auto-discover
    policy_s3_uri = None
    # The field the checkpoint came from, so a not-found names what the operator wrote. Auto-discovery
    # leaves it None: that URI came from a listing, so the object is known to be there.
    policy_field = None
    bucket = event.get("bucketAsset", "")
    asset_location_key = event.get("inputAssetLocationKey", "")

    # 1. Check for relative checkpointPath (preferred method)
    checkpoint_path = training_config.get("checkpointPath")
    if checkpoint_path:
        if not bucket or not asset_location_key:
            raise ValueError(
                "Cannot resolve checkpointPath: pipeline is missing bucketAsset or "
                "inputAssetLocationKey. Ensure vamsExecute passes these through from the workflow."
            )
        policy_s3_uri = resolve_relative_path(bucket, asset_location_key, checkpoint_path)
        policy_field = "checkpointPath"
        logger.info(f"Using checkpointPath: {policy_s3_uri}")

    # 2. Check for explicit policyS3Uri
    if not policy_s3_uri:
        policy_s3_uri = training_config.get("policyS3Uri") or training_config.get("policyPath")
        if policy_s3_uri:
            policy_field = "policyS3Uri"
            logger.info(f"Using policyS3Uri: {policy_s3_uri}")

    # 3. Fall back to auto-discovery across the entire asset root
    if not policy_s3_uri:
        policy_s3_uri = discover_policy_file(bucket, asset_location_key)
        if policy_s3_uri:
            logger.info(f"Auto-discovered policy: {policy_s3_uri}")
    
    if not policy_s3_uri:
        raise ValueError(
            "No policy file found for evaluation. Provide one of: "
            "'checkpointPath' (relative path like 'checkpoints/model_300.pt'), "
            "'policyS3Uri' (full S3 URI), or place a .pt file in the config directory."
        )

    # The checkpoint reaches torch.load in the container, so the same bucket scope applies as to the
    # custom environment package. Routes 1 and 3 resolve inside the asset bucket already; route 2 is
    # operator-supplied and is what this constrains.
    policy_s3_uri = require_asset_bucket("policyS3Uri", policy_s3_uri, event)

    # An operator-named checkpoint is confirmed present here rather than in the container, where a
    # mistyped path fails only once a GPU node is running.
    if policy_field:
        policy_s3_uri = require_existing_object(policy_field, policy_s3_uri)

    job_name = event.get("jobName") or "unknown"
    output_path = event.get("outputS3AssetFilesPath", "")
    
    # Resolve custom environment path (relative or absolute)
    custom_env_s3_uri = resolve_custom_environment_path(event, training_config)

    return {
        "jobName": job_name,
        "trainingConfig": {
            "mode": "evaluate",
            "task": task,
            "numEnvs": num_envs,
            "numEpisodes": num_episodes,
            "stepsPerEpisode": steps_per_episode,
            "policyS3Uri": policy_s3_uri,
            "recordVideo": record_video,
            "rlLibrary": rl_library,
        },
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath"),
        "customEnvironmentS3Uri": custom_env_s3_uri,
        "outputS3AssetFilesPath": output_path,
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }
