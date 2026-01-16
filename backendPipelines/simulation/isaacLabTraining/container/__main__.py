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
import sys
import subprocess
import glob
import traceback
import boto3
from utils.aws.s3 import S3Client
from utils.training.config import PipelineConfig

# EFS mount path for checkpoints
EFS_CHECKPOINT_PATH = "/mnt/efs/checkpoints"
LOCAL_LOG_PATH = "/workspace/isaaclab/logs"

# Get region from environment (set by Batch job definition)
AWS_REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-west-2"))


def get_job_uuid_from_output_path(output_s3_path: str) -> str:
    """Extract job UUID from output S3 path.
    
    Output path format: .../output/{uuid}/files/
    Returns the UUID for organizing files under {uuid}/ prefix.
    """
    if not output_s3_path:
        return ""
    # Path ends with /files/, UUID is second-to-last segment
    parts = output_s3_path.rstrip("/").split("/")
    if len(parts) >= 2 and parts[-1] == "files":
        return parts[-2]
    return ""


def get_sfn_client():
    """Create Step Functions client with proper error handling."""
    return boto3.client("stepfunctions", region_name=AWS_REGION)


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
        print("Usage: python __main__.py '<job_config_json>'")
        sys.exit(1)

    job_config = json.loads(sys.argv[1])
    config = PipelineConfig.from_dict(job_config)

    print("=" * 60)
    print(f"IsaacLab Pipeline - {config.mode.upper()} Mode")
    print("=" * 60)
    print(f"Job Name: {config.job_name}")
    print(f"Task: {config.task}")
    print(f"Num Envs: {config.num_envs}")
    print(f"RL Library: {config.rl_library}")
    print(f"Input S3 Path: {config.input_s3_path}")
    print(f"Output S3 Path: {config.output_s3_path}")
    
    if config.mode == "train":
        print(f"Max Iterations: {config.max_iterations}")
        print(f"Num Nodes: {config.num_nodes}")
    else:
        print(f"Policy: {config.policy_s3_uri}")
        print(f"Num Episodes: {config.num_episodes}")
        print(f"Record Video: {config.record_video}")
    
    print("=" * 60)

    # Setup multi-node if needed (training only)
    node_info = setup_multi_node(config) if config.mode == "train" else None

    s3 = S3Client()

    # Download and install custom environment if provided
    if config.custom_environment_s3_uri:
        print(f"Downloading custom environment: {config.custom_environment_s3_uri}")
        download_and_install_custom_environment(s3, config.custom_environment_s3_uri)
    else:
        print("Using built-in Isaac Lab environment (no custom environment specified)")

    # Execute based on mode
    if config.mode == "train":
        run_training(config, s3, node_info, job_config)
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


def run_training(config: PipelineConfig, s3: S3Client, node_info: dict, job_config: dict):
    """Execute training workflow."""
    checkpoint_dir = setup_checkpoint_dir(config)
    
    cmd = build_training_command(config, checkpoint_dir, node_info)
    print(f"Executing: {' '.join(cmd)}")

    result = subprocess.run(cmd, cwd="/workspace/isaaclab", capture_output=False)

    if result.returncode != 0:
        print(f"ERROR: Training failed with exit code {result.returncode}")
        upload_logs(s3, config)
        sys.exit(result.returncode)

    if node_info["is_main"]:
        upload_training_results(s3, config, checkpoint_dir, job_config)

    print("Training complete")


def run_evaluation(config: PipelineConfig, s3: S3Client, job_config: dict):
    """Execute evaluation workflow."""
    # Find policy file - either from explicit URI or from input directory
    policy_path = download_policy(s3, config)
    
    cmd = build_evaluation_command(config, policy_path)
    print(f"Executing: {' '.join(cmd)}")

    result = subprocess.run(cmd, cwd="/workspace/isaaclab", capture_output=False)

    if result.returncode != 0:
        print(f"ERROR: Evaluation failed with exit code {result.returncode}")
        upload_logs(s3, config)
        sys.exit(result.returncode)

    upload_evaluation_results(s3, config, job_config)
    print("Evaluation complete")


def download_policy(s3: S3Client, config: PipelineConfig) -> str:
    """Download policy file from VAMS.
    
    Policy S3 URI is provided by the Lambda (openPipeline) which discovers
    the .pt file location in the VAMS asset bucket.
    """
    policy_path = "/tmp/policy.pt"
    
    if not config.policy_s3_uri:
        raise ValueError(
            "No policy S3 URI provided. The Lambda should have discovered and passed "
            "the policy file location. Check openPipeline Lambda logs."
        )
    
    print(f"Downloading policy from: {config.policy_s3_uri}")
    from urllib.parse import urlparse
    parsed = urlparse(config.policy_s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    s3.client.download_file(bucket, key, policy_path)
    print(f"Policy downloaded to {policy_path}")
    return policy_path


def setup_multi_node(config: PipelineConfig) -> dict:
    """Setup multi-node parallel communication."""
    node_index = int(os.environ.get("AWS_BATCH_JOB_NODE_INDEX", "0"))
    num_nodes = int(os.environ.get("AWS_BATCH_JOB_NUM_NODES", "1"))
    main_host = os.environ.get("AWS_BATCH_JOB_MAIN_NODE_PRIVATE_IPV4_ADDRESS", "")

    is_main = node_index == 0

    print(f"Node Index: {node_index}/{num_nodes - 1}")
    print(f"Is Main Node: {is_main}")
    if main_host:
        print(f"Main Node IP: {main_host}")

    return {
        "node_index": node_index,
        "num_nodes": num_nodes,
        "main_host": main_host,
        "is_main": is_main,
    }


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
        s3_uri: Full S3 URI to the environment package (.tar.gz, .zip, or .whl)
    """
    from urllib.parse import urlparse
    
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    filename = os.path.basename(key)
    local_path = f"/tmp/{filename}"
    
    print(f"Downloading {filename} from s3://{bucket}/{key}")
    s3.client.download_file(bucket, key, local_path)
    
    print(f"Installing custom environment: {local_path}")
    result = subprocess.run(
        ["/isaac-sim/python.sh", "-m", "pip", "install", "-e", local_path],
        capture_output=True,
        text=True
    )
    
    if result.returncode == 0:
        print(f"Successfully installed {filename}")
    else:
        raise RuntimeError(f"Failed to install custom environment {filename}: {result.stderr}")


def build_training_command(
    config: PipelineConfig, checkpoint_dir: str, node_info: dict
) -> list:
    """Build the IsaacLab training command."""
    script_map = {
        "rsl_rl": "scripts/reinforcement_learning/rsl_rl/train.py",
        "rl_games": "scripts/reinforcement_learning/rl_games/train.py",
        "skrl": "scripts/reinforcement_learning/skrl/train.py",
    }
    script = script_map.get(config.rl_library, script_map["rsl_rl"])

    cmd = [
        "./isaaclab.sh",
        "-p",
        script,
        "--task",
        config.task,
        "--num_envs",
        str(config.num_envs),
        "--max_iterations",
        str(config.max_iterations),
        "--headless",
    ]

    if config.seed is not None:
        cmd.extend(["--seed", str(config.seed)])

    if node_info["num_nodes"] > 1:
        cmd = build_multi_node_command(cmd, node_info)

    return cmd


def build_evaluation_command(config: PipelineConfig, policy_path: str) -> list:
    """Build the IsaacLab evaluation command.
    
    Note: Isaac Lab's play.py runs in an infinite loop by default.
    The --video flag with --video_length is REQUIRED to enable termination.
    See ISAACLAB_CLI_REFERENCE.md for details.
    """
    script_map = {
        "rsl_rl": "scripts/reinforcement_learning/rsl_rl/play.py",
        "rl_games": "scripts/reinforcement_learning/rl_games/play.py",
        "skrl": "scripts/reinforcement_learning/skrl/play.py",
    }
    script = script_map.get(config.rl_library, script_map["rsl_rl"])

    # Calculate total steps from user-configurable parameters
    total_steps = config.num_episodes * config.steps_per_episode

    cmd = [
        "./isaaclab.sh",
        "-p",
        script,
        "--task",
        config.task,
        "--num_envs",
        str(config.num_envs),
        "--checkpoint",
        policy_path,
        "--headless",
        # --video is REQUIRED for play.py to terminate (not just for recording)
        "--video",
        "--video_length",
        str(total_steps),
    ]

    return cmd


def build_multi_node_command(base_cmd: list, node_info: dict) -> list:
    """Wrap command with torchrun for multi-node training."""
    torchrun_cmd = [
        "torchrun",
        f"--nnodes={node_info['num_nodes']}",
        f"--node_rank={node_info['node_index']}",
        "--nproc_per_node=1",
    ]

    if node_info["main_host"]:
        torchrun_cmd.extend([
            f"--master_addr={node_info['main_host']}",
            "--master_port=29500",
        ])

    script_idx = base_cmd.index("-p") + 1
    script_and_args = base_cmd[script_idx:]

    return torchrun_cmd + script_and_args


def upload_training_results(s3: S3Client, config: PipelineConfig, checkpoint_dir: str, job_config: dict):
    """Upload trained policy and checkpoints to VAMS asset bucket.
    
    Output structure (files organized under job UUID for easy identification):
    - {output_s3_path}/{uuid}/checkpoints/*.pt (model checkpoints)
    - {output_s3_path}/{uuid}/metrics.csv (training metrics from TensorBoard)
    - {output_s3_path}/{uuid}/*.txt (converted .diff files)
    - {output_s3_path}/{uuid}/training-config.json (input configuration)
    """
    if not config.output_s3_path:
        print("No output S3 path configured, skipping upload")
        return

    # Get job UUID for organizing output files
    job_uuid = get_job_uuid_from_output_path(config.output_s3_path)
    base_output_path = f"{config.output_s3_path}{job_uuid}/" if job_uuid else config.output_s3_path
    
    print(f"Uploading training results to {base_output_path}")

    # Upload model checkpoints
    checkpoint_output_path = f"{base_output_path}checkpoints/"
    
    policy_patterns = [
        f"{LOCAL_LOG_PATH}/**/model_*.pt",
        f"{checkpoint_dir}/**/model_*.pt",
    ]

    for pattern in policy_patterns:
        for policy_path in glob.glob(pattern, recursive=True):
            filename = os.path.basename(policy_path)
            s3_path = f"{checkpoint_output_path}{filename}"
            print(f"Uploading {policy_path} -> {s3_path}")
            s3.upload_file(policy_path, s3_path)

    # Upload logs
    upload_logs(s3, config, job_uuid)
    
    # Upload input config
    upload_config(s3, config, job_config, job_uuid)


def upload_evaluation_results(s3: S3Client, config: PipelineConfig, job_config: dict):
    """Upload evaluation results to VAMS asset bucket.
    
    Output structure (files organized under job UUID for easy identification):
    - {output_s3_path}/{uuid}/metrics.csv (evaluation metrics from TensorBoard)
    - {output_s3_path}/{uuid}/*.txt (converted .diff files)
    - {output_s3_path}/{uuid}/videos/*.mp4 (evaluation videos)
    - {output_s3_path}/{uuid}/evaluation-config.json (input configuration)
    
    Note: Videos are always generated because --video flag is required for
    play.py to terminate. See ISAACLAB_CLI_REFERENCE.md for details.
    """
    if not config.output_s3_path:
        print("No output S3 path configured, skipping upload")
        return

    # Get job UUID for organizing output files
    job_uuid = get_job_uuid_from_output_path(config.output_s3_path)
    base_output_path = f"{config.output_s3_path}{job_uuid}/" if job_uuid else config.output_s3_path
    
    print(f"Uploading evaluation results to {base_output_path}")

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

    # Upload videos - always generated since --video is required for termination
    videos_output_path = f"{base_output_path}videos/"
    video_search_paths = [
        "/tmp/videos/**/*.mp4",
        "/tmp/videos/**/*.avi",
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

    # Upload input config
    upload_config(s3, config, job_config, job_uuid)


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


def upload_logs(s3: S3Client, config: PipelineConfig, job_uuid: str = ""):
    """Upload logs and metrics to VAMS asset bucket."""
    if not config.output_s3_path:
        return
    
    # Determine base output path with job UUID prefix
    base_output_path = f"{config.output_s3_path}{job_uuid}/" if job_uuid else config.output_s3_path
    
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


def upload_config(s3: S3Client, config: PipelineConfig, job_config: dict, job_uuid: str = ""):
    """Upload the input configuration to the output directory for reference."""
    # Determine base output path with job UUID prefix
    base_output_path = f"{config.output_s3_path}{job_uuid}/" if job_uuid else config.output_s3_path
    
    # Determine config filename based on mode
    config_filename = f"{config.mode}-config.json"
    config_s3_path = f"{base_output_path}{config_filename}"
    
    # Write config to temp file
    temp_config_path = f"/tmp/{config_filename}"
    with open(temp_config_path, "w") as f:
        json.dump(job_config, f, indent=2)
    
    print(f"Uploading config {temp_config_path} -> {config_s3_path}")
    try:
        s3.upload_file(temp_config_path, config_s3_path)
    except Exception as e:
        print(f"Warning: Failed to upload config: {e}")


if __name__ == "__main__":
    main()
