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


def discover_policy_file(s3_uri: str) -> str:
    """Discover .pt policy file in the same S3 directory as the input file.
    
    Args:
        s3_uri: S3 URI of the input config file
        
    Returns:
        S3 URI of the discovered .pt file, or empty string if not found
    """
    if not s3_uri:
        return ""
    
    try:
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        
        # Get parent directory prefix
        parent_prefix = "/".join(key.split("/")[:-1]) + "/"
        
        logger.info(f"Searching for .pt files in s3://{bucket}/{parent_prefix}")
        
        response = s3_client.list_objects_v2(Bucket=bucket, Prefix=parent_prefix)
        
        pt_files = [obj["Key"] for obj in response.get("Contents", []) 
                    if obj["Key"].endswith(".pt")]
        
        if pt_files:
            # Sort descending to get latest model (e.g., model_1500.pt > model_1000.pt)
            pt_files.sort(reverse=True)
            policy_key = pt_files[0]
            policy_uri = f"s3://{bucket}/{policy_key}"
            logger.info(f"Discovered policy file: {policy_uri}")
            return policy_uri
        
        logger.info("No .pt files found in input directory")
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


def build_training_config(event, training_config, compute_config, task, rl_library):
    """Build configuration for training mode."""
    num_envs = training_config.get("numEnvs", DEFAULT_NUM_ENVS_TRAIN)
    max_iterations = training_config.get("maxIterations", DEFAULT_MAX_ITERATIONS)
    num_nodes = compute_config.get("numNodes", 1)
    job_name = event.get("jobName") or "unknown"

    # Use VAMS standard output path (asset bucket)
    output_path = event.get("outputS3AssetFilesPath", "")

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
    input_s3_path = event.get("inputS3AssetFilePath", "")
    
    # 1. Check for relative checkpointPath (preferred method)
    checkpoint_path = training_config.get("checkpointPath")
    if checkpoint_path:
        # Build full S3 URI from relative path
        # Input path is the config file, get the asset directory (parent of config file's parent)
        parsed = urlparse(input_s3_path)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")
        # Get asset root directory (go up from config file location)
        # e.g., "assetId/evaluation/config.json" -> "assetId/"
        path_parts = key.split("/")
        if len(path_parts) >= 2:
            asset_root = "/".join(path_parts[:-2]) + "/" if len(path_parts) > 2 else ""
        else:
            asset_root = ""
        
        # Remove leading slash from checkpoint_path if present
        checkpoint_path = checkpoint_path.lstrip("/")
        policy_s3_uri = f"s3://{bucket}/{asset_root}{checkpoint_path}"
        logger.info(f"Using checkpointPath: {policy_s3_uri}")
    
    # 2. Check for explicit policyS3Uri
    if not policy_s3_uri:
        policy_s3_uri = training_config.get("policyS3Uri") or training_config.get("policyPath")
        if policy_s3_uri:
            logger.info(f"Using policyS3Uri: {policy_s3_uri}")
    
    # 3. Fall back to auto-discovery
    if not policy_s3_uri:
        policy_s3_uri = discover_policy_file(input_s3_path)
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
        "outputS3AssetFilesPath": output_path,
        "inputMetadata": event.get("inputMetadata", ""),
        "inputParameters": event.get("inputParameters", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }
