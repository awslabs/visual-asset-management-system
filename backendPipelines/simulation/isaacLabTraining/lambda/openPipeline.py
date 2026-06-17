#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
ConstructPipeline Lambda
Builds the Batch job definition for IsaacLab training or evaluation.
Downloads and parses config file from S3 if provided.
"""

import json
import boto3
from urllib.parse import urlparse
from customLogging.logger import safeLogger

logger = safeLogger(service="OpenPipelineIsaacLabTraining")
s3_client = boto3.client("s3")

DEFAULT_TASK = "Isaac-Cartpole-v0"
DEFAULT_NUM_ENVS_TRAIN = 4096
DEFAULT_NUM_ENVS_EVAL = 100
DEFAULT_MAX_ITERATIONS = 1500
DEFAULT_NUM_EPISODES = 50


def lambda_handler(event, context):
    logger.info(f"Event: {event}")

    # Load config from S3 file if provided
    file_config = load_config_from_s3(event.get("inputS3AssetFilePath"))
    
    # Merge configs: inputParameters (defaults) < file_config (user's specific config takes priority)
    training_config = merge_configs(
        event.get("trainingConfig", {}),
        file_config.get("trainingConfig", {})
    )
    compute_config = merge_configs(
        event.get("computeConfig", {}),
        file_config.get("computeConfig", {})
    )
    
    mode = training_config.get("mode", "train")
    task = training_config.get("task", DEFAULT_TASK)
    rl_library = training_config.get("rlLibrary", "rsl_rl")

    if mode == "train":
        job_config = build_training_config(event, training_config, compute_config, task, rl_library)
    elif mode == "evaluate":
        job_config = build_evaluation_config(event, training_config, task, rl_library)
    else:
        raise ValueError(f"Invalid mode: {mode}. Must be 'train' or 'evaluate'")

    logger.info(f"Job config: {job_config}")

    return {
        "jobName": event.get("jobName"),
        "definition": json.dumps(job_config),
        "numNodes": job_config.get("computeConfig", {}).get("numNodes", 1),
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath"),
        "outputS3AssetFilesPath": job_config.get("outputS3AssetFilesPath", ""),
        "status": "STARTING",
    }


def load_config_from_s3(s3_uri: str) -> dict:
    """Download and parse JSON config file from S3."""
    if not s3_uri:
        return {}
    
    try:
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        
        # Only parse JSON files
        if not key.endswith(".json"):
            logger.info(f"Input file is not JSON, skipping config parsing: {key}")
            return {}
        
        response = s3_client.get_object(Bucket=bucket, Key=key)
        content = response["Body"].read().decode("utf-8")
        config = json.loads(content)
        logger.info(f"Loaded config from S3: {config}")
        return config
    except Exception as e:
        logger.warning(f"Failed to load config from S3: {e}")
        return {}


def discover_policy_file(bucket: str, asset_location_key: str) -> str:
    """Discover .pt policy file anywhere under the asset root in S3.

    Searches under bucketAsset + inputAssetLocationKey (the authoritative asset
    root), not under the input file's parent directory. The asset root is the
    only reliable starting point — input files may live at arbitrary depths
    beneath it, and deriving the root from the input file path is unsafe.

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

        pt_files = []
        paginator = s3_client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                if obj["Key"].endswith(".pt"):
                    pt_files.append(obj["Key"])

        if pt_files:
            # Sort descending to get latest model (e.g., model_1500.pt > model_1000.pt)
            pt_files.sort(reverse=True)
            policy_key = pt_files[0]
            policy_uri = f"s3://{bucket}/{policy_key}"
            logger.info(f"Discovered policy file: {policy_uri}")
            return policy_uri

        logger.info("No .pt files found under asset root")
        return ""
    except Exception as e:
        logger.warning(f"Failed to discover policy file: {e}")
        return ""


def merge_configs(base: dict, override: dict) -> dict:
    """Merge two config dicts, with override taking priority."""
    result = base.copy()
    for key, value in override.items():
        if value is not None:
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


def resolve_custom_environment_path(event, training_config) -> str:
    """Resolve custom environment path - supports relative or absolute S3 paths.

    Priority order:
    1. customEnvironmentPath - relative path within asset (e.g., "environments/my_env.tar.gz")
    2. customEnvironmentS3Uri - explicit full S3 URI

    Returns:
        Full S3 URI to the custom environment package, or empty string if not specified
    """
    bucket = event.get("bucketAsset", "")
    asset_location_key = event.get("inputAssetLocationKey", "")

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
        return resolved

    # 2. Check for explicit customEnvironmentS3Uri
    custom_env_uri = training_config.get("customEnvironmentS3Uri")
    if custom_env_uri:
        logger.info(f"Using customEnvironmentS3Uri: {custom_env_uri}")
        return custom_env_uri

    return ""


def build_training_config(event, training_config, compute_config, task, rl_library):
    """Build configuration for training mode."""
    num_envs = training_config.get("numEnvs", DEFAULT_NUM_ENVS_TRAIN)
    max_iterations = training_config.get("maxIterations", DEFAULT_MAX_ITERATIONS)
    num_nodes = compute_config.get("numNodes", 1)
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
            "seed": training_config.get("seed"),
        },
        "computeConfig": {
            "numNodes": num_nodes,
        },
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath"),
        "customEnvironmentS3Uri": custom_env_s3_uri,
        "outputS3AssetFilesPath": output_path,
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }


def build_evaluation_config(event, training_config, task, rl_library):
    """Build configuration for evaluation mode.
    
    Policy file discovery (in priority order):
    1. checkpointPath - relative path within asset directory (e.g., "checkpoints/model_300.pt")
    2. policyS3Uri - explicit full S3 URI
    3. Auto-discover .pt files in input directory (backward compatibility)
    """
    num_envs = training_config.get("numEnvs", DEFAULT_NUM_ENVS_EVAL)
    num_episodes = training_config.get("numEpisodes", DEFAULT_NUM_EPISODES)
    steps_per_episode = training_config.get("stepsPerEpisode", 1000)
    record_video = training_config.get("recordVideo", False)
    
    # Policy discovery with priority: checkpointPath > policyS3Uri > auto-discover
    policy_s3_uri = None
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
        logger.info(f"Using checkpointPath: {policy_s3_uri}")

    # 2. Check for explicit policyS3Uri
    if not policy_s3_uri:
        policy_s3_uri = training_config.get("policyS3Uri") or training_config.get("policyPath")
        if policy_s3_uri:
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
        "computeConfig": {
            "numNodes": 1,  # Evaluation always single node
        },
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath"),
        "customEnvironmentS3Uri": custom_env_s3_uri,
        "outputS3AssetFilesPath": output_path,
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }
