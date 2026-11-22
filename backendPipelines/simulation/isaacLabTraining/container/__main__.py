#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
IsaacLab Pipeline - Main Entry Point

Supports both training and evaluation modes:
- Training: Train new RL policies from scratch
- Evaluation: Evaluate pre-trained policies
"""

import json
import os
import re
import sys
import subprocess
import glob
import traceback
import boto3
from utils.aws.s3 import S3Client
from utils.aws.staging import STAGED_ASSET_FILES_ENV, stage_asset_files
from utils.training.config import (
    PipelineConfig, PHYSICS_ENGINES, RL_LIBRARIES, TASK_DEFAULT_PHYSICS)
from utils.training.checkpoints import find_checkpoints, old_rsl_rl_checkpoint_message
from utils.training.task_physics import (
    NO_PRESETS_NOTE, is_typed_preset_rejection, isaac_lab_version, preset_error_message,
    run_isaac_lab, unknown_preset_message)
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

# EFS mount path for checkpoints
EFS_CHECKPOINT_PATH = "/mnt/efs/checkpoints"
LOCAL_LOG_PATH = "/workspace/isaaclab/logs"

# Where the evaluation policy is downloaded. Isaac Lab's play.py derives its run folder from the
# --checkpoint path (the checkpoint's folder for rsl_rl, the folder above it for rl_games and skrl)
# and records the video under <run folder>/videos/play; rsl_rl also exports policy.pt and
# policy.onnx to <checkpoint folder>/exported. Two levels below LOCAL_LOG_PATH, which the image
# gives the non-root user, keeps all of those writes in a folder that user owns and under the
# video upload's search root, LOCAL_LOG_PATH/**/videos/** (issue #1312).
EVALUATION_POLICY_PATH = f"{LOCAL_LOG_PATH}/evaluation/checkpoints/policy.pt"

# Package formats pip can install from a downloaded archive
ENVIRONMENT_PACKAGE_SUFFIXES = (".tar.gz", ".tgz", ".zip", ".whl")

# Isaac Lab 3.0.0 runs in /opt/isaaclab-venv on Python 3.12 (Isaac Lab 2.3.0 ran Python 3.11). Custom
# environments are installed with that interpreter, and a wheel built for Python 3.11 cannot import.
ISAAC_LAB_PYTHON = "/opt/isaaclab-venv/bin/python"
ISAAC_LAB_PYTHON_TAG = "cp312"
_WHEEL_PYTHON_TAG = re.compile(r"-(cp3\d+)-cp3\d+[a-z]*-[^-]+\.whl$")

# The visualizer playback records with. Newton state is not visible to a Kit visualizer without
# extra work and Isaac Lab warns that Kit frames can come out black under Newton, so evaluation on
# Newton records through Newton's own GL viewer (issue #1669, owner decision D10). PhysX keeps the
# default Kit visualizer. Training passes ``--viz none`` on both engines: the container has no
# display, and ``--headless`` no longer switches visualizers off in Isaac Lab 3.0.
NEWTON_VIDEO_VISUALIZER = "newton_gl"

# Wall-clock bound on the play process (see evaluation_time_limit).
EVALUATION_TIME_LIMIT_FLOOR_SECONDS = 1800
EVALUATION_SECONDS_PER_STEP = 0.1
# 7.5 hours: the evaluation workflow step allows 9 hours from its start, and the 1.5 hour margin covers
# the wait for a GPU instance and the image pull, so the watchdog's readable failure comes first.
EVALUATION_TIME_LIMIT_CAP_SECONDS = 27000

# Get region from environment (set by Batch job definition)
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))


def get_job_uuid_from_output_path(output_s3_path: str) -> str:
    """The execution id encoded in the output S3 path (``.../output/{executionId}/files/``), or ``""``."""
    if not output_s3_path:
        return ""
    # Path ends with /files/, execution id is the second-to-last segment
    parts = output_s3_path.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-1] == "files":
        return parts[-2]
    return ""


def resolve_output_base_path(output_s3_path: str) -> str:
    """Output prefix for all written files, with a trailing slash."""
    if not output_s3_path:
        return ""
    return output_s3_path if output_s3_path.endswith("/") else output_s3_path + "/"


def get_sfn_client():
    """Create Step Functions client with proper error handling."""
    return boto3.client("stepfunctions", region_name=AWS_REGION, config=retry_config)


def send_task_failure(task_token: str, error: str, cause: str):
    """Send failure callback to Step Functions."""
    try:
        sfn_client = get_sfn_client()
        sfn_client.send_task_failure(
            taskToken=task_token,
            error=error,
            cause=cause[:256]
        )
        print("Step Functions callback sent: FAILURE")
    except Exception as callback_error:
        print(f"ERROR: Failed to send Step Functions failure callback: {callback_error}")


def send_task_success(task_token: str, output: dict):
    """Send success callback to Step Functions."""
    try:
        sfn_client = get_sfn_client()
        sfn_client.send_task_success(taskToken=task_token, output=json.dumps(output))
        print("Step Functions callback sent: SUCCESS")
    except Exception as callback_error:
        print(f"ERROR: Failed to send Step Functions success callback: {callback_error}")


def send_task_heartbeat(task_token: str):
    """Send heartbeat to Step Functions to prevent timeout."""
    try:
        sfn_client = get_sfn_client()
        sfn_client.send_task_heartbeat(taskToken=task_token)
        print("Step Functions heartbeat sent")
    except Exception as heartbeat_error:
        print(f"WARNING: Failed to send Step Functions heartbeat: {heartbeat_error}")


class HeartbeatThread:
    """Background thread to send periodic heartbeats to Step Functions."""
    
    def __init__(self, internal_token: str = None, external_token: str = None, interval_seconds: int = 300):
        """
        Initialize heartbeat thread.
        
        Args:
            internal_token: Task token for internal Step Function (isaaclab-pipeline-internal)
            external_token: Task token for external Step Function (vams-isaaclab-training)
            interval_seconds: Interval between heartbeats (default 5 minutes)
        """
        import threading
        self.internal_token = internal_token
        self.external_token = external_token
        self.interval = interval_seconds
        self._stop_event = threading.Event()
        self._thread = None
    
    def _heartbeat_loop(self):
        """Send heartbeats at regular intervals until stopped."""
        while not self._stop_event.wait(self.interval):
            if self.internal_token:
                send_task_heartbeat(self.internal_token)
            if self.external_token:
                send_task_heartbeat(self.external_token)
    
    def start(self):
        """Start the heartbeat thread."""
        import threading
        if self.internal_token or self.external_token:
            self._thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
            self._thread.start()
            print(f"Heartbeat thread started (interval: {self.interval}s)")
    
    def stop(self):
        """Stop the heartbeat thread."""
        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=5)
            print("Heartbeat thread stopped")


def main():
    # Internal SFN task token (from isaaclab-pipeline-internal)
    internal_task_token = os.environ.get("SFN_TASK_TOKEN")
    # External SFN task token will be extracted from job config
    external_task_token = None
    heartbeat = None
    
    try:
        # Parse job config to get external task token
        if len(sys.argv) >= 2:
            try:
                job_config = json.loads(sys.argv[1])
                external_task_token = job_config.get("externalSfnTaskToken")
            except json.JSONDecodeError:
                pass
        
        # Start heartbeat thread to prevent Step Function timeout
        # Send heartbeat every 5 minutes (well under the 30-minute heartbeat timeout)
        heartbeat = HeartbeatThread(internal_task_token, external_task_token, interval_seconds=300)
        heartbeat.start()
        
        result = run_pipeline()
        
        # Stop heartbeat thread before sending final callback
        if heartbeat:
            heartbeat.stop()
        
        # Send success callback to internal Step Functions (isaaclab-pipeline-internal)
        if internal_task_token:
            send_task_success(internal_task_token, result)
        
        # Send success callback to external Step Functions (vams-isaaclab-training)
        if external_task_token:
            send_task_success(external_task_token, result)
            
    except Exception as e:
        print(f"ERROR: Pipeline failed with exception: {e}")
        traceback.print_exc()
        
        # Stop heartbeat thread
        if heartbeat:
            heartbeat.stop()
        
        # Send failure callback to internal Step Functions
        if internal_task_token:
            send_task_failure(internal_task_token, "PipelineError", str(e))
        
        # Send failure callback to external Step Functions (vams-isaaclab-training)
        if external_task_token:
            send_task_failure(external_task_token, "PipelineError", str(e))
            
        sys.exit(1)


def run_pipeline():
    if len(sys.argv) < 2:
        raise ValueError("Usage: python __main__.py '<job_config_json>'")

    job_config = json.loads(sys.argv[1])
    config = PipelineConfig.from_dict(job_config)

    print("=" * 60)
    print(f"IsaacLab Pipeline - {config.mode.upper()} Mode")
    print("=" * 60)
    print(f"Job Name: {config.job_name}")
    print(f"Task: {config.task}")
    print(f"Num Envs: {config.num_envs}")
    print(f"RL Library: {config.rl_library}")
    print(f"Physics: {config.physics}")
    if config.physics == TASK_DEFAULT_PHYSICS:
        print(NO_PRESETS_NOTE.format(task=config.task))
    print(f"Isaac Lab: {isaac_lab_version()}")
    if config.task_id_requested:
        print(f"Task id {config.task_id_requested} was mapped to {config.task} "
              f"for Isaac Lab {isaac_lab_version()}")
    print(f"NVIDIA driver: {gpu_driver_version() or 'unknown'}")
    print(f"Input S3 Path: {config.input_s3_path}")
    print(f"Output S3 Path: {config.output_s3_path}")
    
    if config.mode == "train":
        print(f"Max Iterations: {config.max_iterations}")
    else:
        print(f"Policy: {config.policy_s3_uri}")
        print(f"Num Episodes: {config.num_episodes}")
        print(f"Record Video: {config.record_video}")
        if config.physics_note:
            print(f"NOTE: {config.physics_note}")
    
    print("=" * 60)

    s3 = S3Client()
    # Sign each asset bucket's requests for its own Region before any S3 call (see utils/aws/s3.py).
    s3.register_bucket_regions(job_config.get("bucketRegions"))

    # Stage the asset files the task reads from disk, before any package code runs in the container
    if config.stage_asset_files:
        stage_assets(s3, config)

    # Download and install custom environment if provided
    if config.custom_environment_s3_uri:
        print(f"Downloading custom environment: {config.custom_environment_s3_uri}")
        download_and_install_custom_environment(s3, config.custom_environment_s3_uri)
    else:
        print("Using built-in Isaac Lab environment (no custom environment specified)")

    # Execute based on mode
    if config.mode == "train":
        run_training(config, s3, job_config)
    elif config.mode == "evaluate":
        run_evaluation(config, s3, job_config)
    else:
        raise ValueError(f"Invalid mode: {config.mode}. Must be 'train' or 'evaluate'")

    # Return result for Step Functions callback
    return {
        "jobName": config.job_name,
        "status": "SUCCEEDED",
        "outputS3AssetFilesPath": config.output_s3_path,
    }


def run_training(config: PipelineConfig, s3: S3Client, job_config: dict):
    """Execute training workflow."""
    checkpoint_dir = setup_checkpoint_dir(config)

    result = run_with_preset_fallback(
        config, lambda: build_training_command(config, checkpoint_dir), cwd="/workspace/isaaclab")

    if result.returncode != 0:
        print(f"ERROR: Training failed with exit code {result.returncode}")
        upload_logs(s3, config)
        refusal = preset_failure_message(config, result.tail)
        if refusal:
            raise RuntimeError(refusal)
        # Raise rather than exit: main() owns the Step Functions failure callbacks, and
        # SystemExit would bypass them and leave the workflow task waiting for its timeout.
        raise RuntimeError(f"Training failed with exit code {result.returncode}")

    upload_training_results(s3, config, checkpoint_dir, job_config)

    print("Training complete")


def run_evaluation(config: PipelineConfig, s3: S3Client, job_config: dict):
    """Execute evaluation workflow."""
    # Find policy file - either from explicit URI or from input directory
    policy_path = download_policy(s3, config)

    if config.rl_library == "rsl_rl":
        problem = old_rsl_rl_checkpoint_message(policy_path)
        if problem:
            print(f"ERROR: {problem}")
            upload_logs(s3, config)
            raise RuntimeError(problem)

    limit = evaluation_time_limit(config)
    result = run_with_preset_fallback(
        config, lambda: build_evaluation_command(config, policy_path), cwd="/workspace/isaaclab",
        timeout_seconds=limit)

    if result.timed_out:
        print(f"ERROR: Evaluation did not finish within {limit} seconds")
        upload_logs(s3, config)
        raise RuntimeError(
            f"Evaluation on physics={config.physics} did not finish within {limit} seconds and was "
            f"stopped. The playback of {config.num_episodes * config.steps_per_episode} steps "
            "should end well before that; check the run log for where it stopped.")

    if result.returncode != 0:
        print(f"ERROR: Evaluation failed with exit code {result.returncode}")
        upload_logs(s3, config)
        refusal = preset_failure_message(config, result.tail)
        if refusal:
            raise RuntimeError(refusal)
        raise RuntimeError(f"Evaluation failed with exit code {result.returncode}")

    upload_evaluation_results(s3, config, job_config)
    print("Evaluation complete")


def run_with_preset_fallback(config: PipelineConfig, build_command, cwd: str, **kwargs):
    """Run the Isaac Lab command; when the task has no physics presets, run it again without one.

    A task that lists no preset of the physics type rejects ``physics=<engine>`` during
    configuration validation, before Isaac Sim starts. The Lambda leaves the override out for the
    tasks it knows; for any other (a custom or ported environment) this reads the rejection and
    runs once more with no selector, so the task runs on the physics it registers. The run is
    recorded as ``task-default``. Any other failure is returned as it is.
    """
    cmd = build_command()
    print(f"Executing: {' '.join(cmd)}")
    result = run_isaac_lab(cmd, cwd=cwd, **kwargs)
    if (result.returncode != 0 and not getattr(result, "timed_out", False)
            and config.physics != TASK_DEFAULT_PHYSICS and is_typed_preset_rejection(result.tail)):
        print(NO_PRESETS_NOTE.format(task=config.task))
        config.physics = TASK_DEFAULT_PHYSICS
        cmd = build_command()
        print(f"Executing: {' '.join(cmd)}")
        result = run_isaac_lab(cmd, cwd=cwd, **kwargs)
    return result


def preset_failure_message(config: PipelineConfig, output: str):
    """The readable message for a failed run whose output shows a preset error, or ``None``."""
    return (unknown_preset_message(config.task, config.physics, output, PHYSICS_ENGINES)
            or preset_error_message(config.task, output))


def evaluation_time_limit(config: PipelineConfig) -> int:
    """Seconds the play process may run before it is stopped.

    Play did not terminate in some runs (a Kit visualizer under Newton; a Newton checkpoint played
    on PhysX), and a hung run would otherwise hold the GPU until the workflow's timeout. The limit
    is a floor plus a per-step allowance for the bounded playback (``--video_length``), capped below
    the workflow's own 32,400 second (9 hour) bound. The constants are first estimates, to be tuned from the
    live smoke runs.
    """
    steps = config.num_episodes * config.steps_per_episode
    return int(min(EVALUATION_TIME_LIMIT_CAP_SECONDS,
                   EVALUATION_TIME_LIMIT_FLOOR_SECONDS + steps * EVALUATION_SECONDS_PER_STEP))


def gpu_driver_version() -> str:
    """The NVIDIA driver version, or ``""`` when it cannot be read (never an error)."""
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30)  # nosemgrep: dangerous-subprocess-use-audit
        lines = (result.stdout or "").strip().splitlines()
        return lines[0].strip() if result.returncode == 0 and lines else ""
    except Exception:
        return ""


def download_policy(s3: S3Client, config: PipelineConfig) -> str:
    """Download policy file from VAMS.

    The policy S3 URI reaches here from the Lambda (openPipeline), which resolves it from a relative
    checkpointPath, an operator-supplied policyS3Uri, or by discovering the newest .pt in the asset.
    The Lambda constrains it to the executing asset's own bucket in every case, because the file is
    deserialized by torch.load in this container. The file lands at EVALUATION_POLICY_PATH, whose
    folders decide where play.py writes its video and exported policy.
    """
    policy_path = EVALUATION_POLICY_PATH

    if not config.policy_s3_uri:
        raise ValueError(
            "No policy S3 URI provided. The Lambda should have discovered and passed "
            "the policy file location. Check openPipeline Lambda logs."
        )

    print(f"Downloading policy from: {config.policy_s3_uri}")
    from urllib.parse import urlparse
    parsed = urlparse(config.policy_s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Policy must be an s3:// URI: {config.policy_s3_uri}")
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    os.makedirs(os.path.dirname(policy_path), exist_ok=True)
    s3.client_for_bucket(bucket).download_file(bucket, key, policy_path)
    print(f"Policy downloaded to {policy_path}")
    return policy_path


def stage_assets(s3: S3Client, config: PipelineConfig) -> dict:
    """Stage the run's asset files (trainingConfig.stageAssetPaths) and export their folder.

    The files keep their asset-relative paths under the staging folder, which the Isaac Lab process
    inherits as STAGED_ASSET_FILES_ENV; see utils/aws/staging.py.
    """
    paths = config.stage_asset_files.get("paths")
    shown = ", ".join(str(p) or "/" for p in paths) if isinstance(paths, list) else repr(paths)
    print(f"Staging asset files: {shown}")
    staged = stage_asset_files(s3, config.stage_asset_files)
    os.environ[STAGED_ASSET_FILES_ENV] = staged["path"]
    print(f"Staged {staged['files']} asset files ({staged['bytes']} bytes) in {staged['seconds']} s; "
          f"{STAGED_ASSET_FILES_ENV}={staged['path']}")
    return staged


def setup_checkpoint_dir(config: PipelineConfig) -> str:
    """Setup checkpoint directory on EFS or local."""
    if os.path.exists("/mnt/efs"):
        checkpoint_dir = f"{EFS_CHECKPOINT_PATH}/{config.job_name}"
    else:
        checkpoint_dir = f"/tmp/checkpoints/{config.job_name}"

    os.makedirs(checkpoint_dir, exist_ok=True)
    print(f"Checkpoint directory: {checkpoint_dir}")
    return checkpoint_dir


def download_and_install_custom_environment(s3: S3Client, s3_uri: str):
    """Download a custom environment package from S3 and install it.

    Args:
        s3: S3Client instance
        s3_uri: Full S3 URI to the environment package (.tar.gz, .tgz, .zip, or .whl)

    The install runs with build isolation disabled so it builds against the backend already
    present in the image rather than fetching one. A package whose dependencies are not already
    installed fails here rather than mid-run.
    """
    from urllib.parse import urlparse

    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3" or not parsed.netloc:
        raise ValueError(f"Custom environment must be an s3:// URI: {s3_uri}")

    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    filename = os.path.basename(key)
    if not filename.endswith(ENVIRONMENT_PACKAGE_SUFFIXES):
        raise ValueError(
            f"Unsupported custom environment package '{filename}'. Supported formats: "
            f"{', '.join(ENVIRONMENT_PACKAGE_SUFFIXES)}"
        )
    wheel_tag = _WHEEL_PYTHON_TAG.search(filename)
    if wheel_tag and wheel_tag.group(1) != ISAAC_LAB_PYTHON_TAG:
        raise ValueError(
            f"Custom environment wheel '{filename}' is built for Python "
            f"{wheel_tag.group(1)[2]}.{wheel_tag.group(1)[3:]}, but Isaac Lab {isaac_lab_version()} "
            f"runs Python 3.12. Rebuild the package for Python 3.12 ({ISAAC_LAB_PYTHON_TAG})."
        )
    local_path = f"/tmp/{filename}"

    print(f"Downloading {filename} from s3://{bucket}/{key}")
    s3.client_for_bucket(bucket).download_file(bucket, key, local_path)

    print(f"Installing custom environment: {local_path}")
    result = subprocess.run(
        [ISAAC_LAB_PYTHON, "-m", "pip", "install", "--no-build-isolation", local_path],
        capture_output=True,
        text=True
    )

    if result.returncode == 0:
        print(f"Successfully installed {filename}")
    else:
        raise RuntimeError(f"Failed to install custom environment {filename}: {result.stderr}")


def resolve_rl_library(rl_library: str) -> str:
    """The RL library for ``--rl_library``, checked against the libraries the pipeline supports.

    An unrecognized library is rejected rather than substituted: falling back would run the whole
    job with a different library than asked for and report success, leaving checkpoints the
    requested library cannot load.
    """
    if rl_library not in RL_LIBRARIES:
        raise ValueError(
            f"Unsupported rlLibrary '{rl_library}'. Supported libraries: "
            f"{', '.join(sorted(RL_LIBRARIES))}"
        )
    return rl_library


def resolve_physics(physics: str) -> str:
    """The physics engine for the ``physics=`` override, checked against the allowed engines.

    The value reaches the command line as one argument of a list (no shell), and only a value of
    the allow list gets there.
    """
    if physics not in PHYSICS_ENGINES:
        raise ValueError(
            f"Unsupported physics '{physics}'. Supported engines: {', '.join(PHYSICS_ENGINES)}"
        )
    return physics


def build_training_command(config: PipelineConfig, checkpoint_dir: str) -> list:
    """Build the Isaac Lab training command (Isaac Lab 3.0: ``isaaclab.sh train``).

    ``--headless`` no longer exists in Isaac Lab 3.0; ``--viz none`` keeps visualizers off on both
    engines. The ``physics=<engine>`` Hydra override comes last, as Isaac Lab expects overrides.
    """
    cmd = [
        "./isaaclab.sh",
        "train",
        "--rl_library",
        resolve_rl_library(config.rl_library),
        "--task",
        config.task,
        "--num_envs",
        str(config.num_envs),
        "--max_iterations",
        str(config.max_iterations),
        "--viz",
        "none",
    ]

    if config.seed is not None:
        cmd.extend(["--seed", str(config.seed)])

    if config.physics != TASK_DEFAULT_PHYSICS:
        cmd.append(f"physics={resolve_physics(config.physics)}")
    return cmd


def build_evaluation_command(config: PipelineConfig, policy_path: str) -> list:
    """Build the Isaac Lab evaluation command (Isaac Lab 3.0: ``isaaclab.sh play``).

    Note: ``play`` runs until it is stopped unless a video length bounds it. The --video flag with
    --video_length is REQUIRED to enable termination. See ISAACLAB_CLI_REFERENCE.md for details.

    On Newton the video is recorded through Newton's GL viewer (``--viz newton_gl``); PhysX keeps
    the default Kit visualizer. The ``physics=<engine>`` override comes last and is the same engine
    the training run used, or a transfer test the operator asked for.
    """
    # Calculate total steps from user-configurable parameters
    total_steps = config.num_episodes * config.steps_per_episode

    cmd = [
        "./isaaclab.sh",
        "play",
        "--rl_library",
        resolve_rl_library(config.rl_library),
        "--task",
        config.task,
        "--num_envs",
        str(config.num_envs),
        "--checkpoint",
        policy_path,
        # --video is REQUIRED for play to terminate (not just for recording)
        "--video",
        "--video_length",
        str(total_steps),
    ]

    if config.physics == TASK_DEFAULT_PHYSICS:
        return cmd  # the task lists no physics presets: no selector and no Newton viewer

    physics = resolve_physics(config.physics)
    if physics == "newton_mjwarp":
        cmd.extend(["--viz", NEWTON_VIDEO_VISUALIZER])

    cmd.append(f"physics={physics}")
    return cmd


def upload_training_results(s3: S3Client, config: PipelineConfig, checkpoint_dir: str, job_config: dict):
    """Upload trained policy and checkpoints to VAMS asset bucket.

    Output structure:
    - {output_s3_path}checkpoints/* (model checkpoints: model_*.pt for rsl_rl, *.pth for rl_games,
      agent_*.pt and best_agent.pt for skrl)
    - {output_s3_path}metrics.csv (training metrics from TensorBoard)
    - {output_s3_path}*.txt (converted .diff files)
    - {output_s3_path}train-config.json (input configuration)
    """
    if not config.output_s3_path:
        print("No output S3 path configured, skipping upload")
        return

    base_output_path = resolve_output_base_path(config.output_s3_path)
    job_uuid = get_job_uuid_from_output_path(config.output_s3_path)

    print(f"Uploading training results for execution {job_uuid} to {base_output_path}")

    # Upload model checkpoints. Each library lays its run folder out differently, so the files come
    # from the per-library table in utils/training/checkpoints.py. They are stored flat under
    # checkpoints/ by file name, as the rsl_rl ones always were.
    checkpoint_output_path = f"{base_output_path}checkpoints/"

    uploaded = {}
    for policy_path in find_checkpoints([LOCAL_LOG_PATH, checkpoint_dir], config.rl_library):
        filename = os.path.basename(policy_path)
        if filename in uploaded:
            print(f"WARNING: {policy_path} has the same file name as {uploaded[filename]}; "
                  f"the newer file replaces it in {checkpoint_output_path}")
        s3_path = f"{checkpoint_output_path}{filename}"
        print(f"Uploading {policy_path} -> {s3_path}")
        s3.upload_file(policy_path, s3_path)
        uploaded[filename] = policy_path

    if not uploaded:
        print(f"ERROR: no {config.rl_library} checkpoint found under {LOCAL_LOG_PATH} or "
              f"{checkpoint_dir}; nothing was uploaded to {checkpoint_output_path}")

    # Upload logs and the input config first, so a failed run keeps its diagnostics
    upload_logs(s3, config)
    upload_config(s3, config, job_config)

    if not uploaded:
        # A training run that produced no policy is a failed run (issue #1315). Raise rather than
        # exit: main() owns the Step Functions failure callbacks.
        raise RuntimeError(
            f"Training finished but no {config.rl_library} checkpoint was found to upload")


def upload_evaluation_results(s3: S3Client, config: PipelineConfig, job_config: dict):
    """Upload evaluation results to VAMS asset bucket.

    Output structure:
    - {output_s3_path}metrics.csv (evaluation metrics from TensorBoard)
    - {output_s3_path}*.txt (converted .diff files)
    - {output_s3_path}videos/*.mp4 (evaluation videos, when recordVideo is set)
    - {output_s3_path}evaluate-config.json (input configuration)

    Note: Videos are always generated because the --video flag is required for play.py to
    terminate, so recordVideo decides whether the recording is uploaded rather than whether it
    is produced. See ISAACLAB_CLI_REFERENCE.md for details.
    """
    if not config.output_s3_path:
        print("No output S3 path configured, skipping upload")
        return

    base_output_path = resolve_output_base_path(config.output_s3_path)
    job_uuid = get_job_uuid_from_output_path(config.output_s3_path)

    print(f"Uploading evaluation results for execution {job_uuid} to {base_output_path}")

    # Convert TensorBoard events to CSV for VAMS compatibility
    export_tensorboard_to_csv(LOCAL_LOG_PATH)
    
    # Convert .diff files to .txt for VAMS compatibility
    convert_diff_to_txt(LOCAL_LOG_PATH)

    # Upload evaluation logs
    log_patterns = [
        f"{LOCAL_LOG_PATH}/**/*.log",
        f"{LOCAL_LOG_PATH}/**/*.csv",
        f"{LOCAL_LOG_PATH}/**/*.json",
        f"{LOCAL_LOG_PATH}/**/*.txt",
    ]

    for pattern in log_patterns:
        for log_path in glob.glob(pattern, recursive=True):
            filename = os.path.basename(log_path)
            s3_path = f"{base_output_path}{filename}"
            print(f"Uploading {log_path} -> {s3_path}")
            try:
                s3.upload_file(log_path, s3_path)
            except Exception as e:
                print(f"Warning: Failed to upload {log_path}: {e}")

    # Upload videos - recorded on every run since --video is required for termination. play.py
    # records under the policy's folders (EVALUATION_POLICY_PATH), which sit below LOCAL_LOG_PATH.
    if config.record_video:
        videos_output_path = f"{base_output_path}videos/"
        video_search_paths = [
            f"{LOCAL_LOG_PATH}/**/videos/**/*.mp4",
            f"{LOCAL_LOG_PATH}/**/videos/**/*.avi",
        ]

        for pattern in video_search_paths:
            for video_path in glob.glob(pattern, recursive=True):
                filename = os.path.basename(video_path)
                s3_path = f"{videos_output_path}{filename}"
                print(f"Uploading video {video_path} -> {s3_path}")
                try:
                    s3.upload_file(video_path, s3_path)
                except Exception as e:
                    print(f"Warning: Failed to upload video {video_path}: {e}")
    else:
        print("Record Video disabled, skipping video upload")

    # Upload input config
    upload_config(s3, config, job_config)


def export_tensorboard_to_csv(log_dir: str) -> str | None:
    """Export TensorBoard event files to CSV format.
    
    Args:
        log_dir: Directory containing TensorBoard event files
    
    Returns path to generated CSV file, or None if no events found.
    """
    try:
        from tensorboard.backend.event_processing.event_accumulator import EventAccumulator
        import csv
        
        # Find event files
        event_files = glob.glob(f"{log_dir}/**/events.out.*", recursive=True)
        if not event_files:
            print("No TensorBoard event files found")
            return None
        
        # Use the first event file's directory
        event_dir = os.path.dirname(event_files[0])
        
        # Load events
        ea = EventAccumulator(event_dir)
        ea.Reload()
        
        # Get all scalar tags
        scalar_tags = ea.Tags().get('scalars', [])
        if not scalar_tags:
            print("No scalar metrics found in TensorBoard events")
            return None
        
        # Collect all data points
        all_data = {}
        all_steps = set()
        
        for tag in scalar_tags:
            events = ea.Scalars(tag)
            for event in events:
                step = event.step
                all_steps.add(step)
                if step not in all_data:
                    all_data[step] = {'step': step, 'wall_time': event.wall_time}
                # Clean tag name for CSV header
                clean_tag = tag.replace('/', '_')
                all_data[step][clean_tag] = event.value
        
        if not all_data:
            print("No data points found in TensorBoard events")
            return None
        
        # Write CSV
        csv_path = os.path.join(log_dir, "metrics.csv")
        sorted_steps = sorted(all_steps)
        
        # Get all column names
        all_columns = set()
        for data in all_data.values():
            all_columns.update(data.keys())
        columns = ['step', 'wall_time'] + sorted([c for c in all_columns if c not in ['step', 'wall_time']])
        
        with open(csv_path, 'w', newline='') as f:
            writer = csv.DictWriter(f, fieldnames=columns)
            writer.writeheader()
            for step in sorted_steps:
                writer.writerow(all_data[step])
        
        print(f"Exported TensorBoard metrics to {csv_path}")
        return csv_path
        
    except ImportError:
        print("Warning: tensorboard not available for CSV export")
        return None
    except Exception as e:
        print(f"Warning: Failed to export TensorBoard to CSV: {e}")
        return None


def convert_diff_to_txt(log_dir: str) -> list[str]:
    """Convert .diff files to .txt for VAMS compatibility.
    
    Args:
        log_dir: Directory containing .diff files
    """
    txt_files = []
    for diff_path in glob.glob(f"{log_dir}/**/*.diff", recursive=True):
        # Get original filename without .diff extension
        base_name = os.path.basename(diff_path).replace('.diff', '')
        new_filename = f"{base_name}_git_diff.txt"
        txt_path = os.path.join(os.path.dirname(diff_path), new_filename)
        try:
            import shutil
            shutil.copy(diff_path, txt_path)
            txt_files.append(txt_path)
            print(f"Converted {diff_path} -> {txt_path}")
        except Exception as e:
            print(f"Warning: Failed to convert {diff_path}: {e}")
    return txt_files


def upload_logs(s3: S3Client, config: PipelineConfig):
    """Upload logs and metrics to VAMS asset bucket."""
    if not config.output_s3_path:
        return

    base_output_path = resolve_output_base_path(config.output_s3_path)

    # Convert TensorBoard events to CSV for VAMS compatibility
    export_tensorboard_to_csv(LOCAL_LOG_PATH)
    
    # Convert .diff files to .txt for VAMS compatibility
    convert_diff_to_txt(LOCAL_LOG_PATH)
    
    # Upload log and metric files
    log_patterns = [
        f"{LOCAL_LOG_PATH}/**/*.log",
        f"{LOCAL_LOG_PATH}/**/*.txt",
        f"{LOCAL_LOG_PATH}/**/*.csv",
        f"{LOCAL_LOG_PATH}/**/*.json",
    ]
    
    for pattern in log_patterns:
        for log_path in glob.glob(pattern, recursive=True):
            filename = os.path.basename(log_path)
            s3_path = f"{base_output_path}{filename}"
            print(f"Uploading {log_path} -> {s3_path}")
            try:
                s3.upload_file(log_path, s3_path)
            except Exception as e:
                print(f"Warning: Failed to upload {log_path}: {e}")


def upload_config(s3: S3Client, config: PipelineConfig, job_config: dict):
    """Upload the input configuration to the output directory for reference."""
    base_output_path = resolve_output_base_path(config.output_s3_path)

    # Determine config filename based on mode
    config_filename = f"{config.mode}-config.json"
    config_s3_path = f"{base_output_path}{config_filename}"
    
    # Write config to temp file. The record carries the physics engine the run used and the Isaac
    # Lab release it ran on, so a run made before the upgrade (PhysX, Isaac Lab 2.3.0) is told
    # apart from one made after it, and the resolved engine is there even if the caller left it out.
    recorded = dict(job_config)
    recorded_training = dict(recorded.get("trainingConfig") or {})
    recorded_training["physics"] = config.physics
    recorded["trainingConfig"] = recorded_training
    recorded["isaacLabVersion"] = isaac_lab_version()
    driver = gpu_driver_version()
    if driver:
        recorded["gpuDriverVersion"] = driver
    temp_config_path = f"/tmp/{config_filename}"
    with open(temp_config_path, "w") as f:
        json.dump(recorded, f, indent=2)
    
    print(f"Uploading config {temp_config_path} -> {config_s3_path}")
    try:
        s3.upload_file(temp_config_path, config_s3_path)
    except Exception as e:
        print(f"Warning: Failed to upload config: {e}")


if __name__ == "__main__":
    main()
