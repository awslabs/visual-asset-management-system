"""
Cosmos Transfer 2.5 Inference Wrapper

Routes to the correct inference mode via the cosmos-transfer2.5 examples/inference.py
script using a JSON config file. Transfer 2B requires 65.4GB VRAM, so ALWAYS uses
torchrun with 8 GPUs (p4d.24xlarge).
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# CWD must be the repo root so the framework can find internal modules
COSMOS_REPO_DIR = "/opt/cosmos-transfer2.5"
COSMOS_INFERENCE_SCRIPT = "/opt/cosmos-transfer2.5/examples/inference.py"
CONFIG_PATH = "/tmp/transfer_config.json"

# Mapping from VAMS control type names to cosmos-transfer2.5 config keys and model flags
CONTROL_TYPE_MAP = {
    "edge": "edge",
    "depth": "depth",
    "seg": "seg",
    "segmentation": "seg",
    "blur": "vis",
    "vis": "vis",
}


def build_transfer_config(
    control_type: str,
    prompt: str,
    source_video_path: str,
    control_video_path: Optional[str] = None,
    control_weight: float = 1.0,
) -> dict:
    """
    Build the JSON config dict for cosmos-transfer2.5 inference.

    Args:
        control_type: Control signal type (edge, depth, seg, vis)
        prompt: Text prompt for generation
        source_video_path: Path to source video
        control_video_path: Path to control signal video (optional)
        control_weight: Control signal weight (default: 1.0)

    Returns:
        Config dict ready for JSON serialization
    """
    # Map to cosmos-transfer2.5 control type key
    cosmos_control_type = CONTROL_TYPE_MAP.get(control_type.lower(), "edge")

    config = {
        "name": "vams_transfer",
        "prompt": prompt,
        "video_path": source_video_path,
    }

    # Build the control signal entry
    control_entry = {
        "control_weight": control_weight,
    }

    # If control path is provided, include it; otherwise omit for on-the-fly computation
    if control_video_path:
        control_entry["control_path"] = control_video_path

    config[cosmos_control_type] = control_entry

    return config


def run_inference(
    control_type: str,
    prompt: str,
    source_video_path: str,
    control_video_path: Optional[str] = None,
    control_weight: float = 1.0,
    output_dir: str = "/tmp/output",
    hf_home: str = "/mnt/efs/cosmos-models/hf_cache",
    hf_token: Optional[str] = None,
    disable_guardrails: bool = True,
    num_gpus: int = 8,
) -> str:
    """
    Run Cosmos Transfer 2.5 inference.

    Transfer 2B needs 65.4GB VRAM, so ALWAYS uses torchrun with 8 GPUs.

    Args:
        control_type: Control signal type (edge, depth, seg, vis/blur)
        prompt: Text prompt for transfer
        source_video_path: Path to source video
        control_video_path: Path to control signal video (optional)
        control_weight: Control signal weight (default: 1.0)
        output_dir: Output directory for generated videos
        hf_home: HuggingFace cache directory (HF_HOME)
        hf_token: HuggingFace API token (optional)
        disable_guardrails: Whether to disable guardrails (default: True)
        num_gpus: Number of GPUs (default: 8, required for Transfer 2B)

    Returns:
        Path to output directory

    Raises:
        ValueError: If required inputs are missing
        RuntimeError: If inference fails
    """
    # Validate inputs
    if not source_video_path:
        raise ValueError("Transfer requires source_video_path")

    # Map control type to cosmos model flag
    cosmos_control_type = CONTROL_TYPE_MAP.get(control_type.lower(), "edge")

    # Build transfer config
    transfer_config = build_transfer_config(
        control_type=control_type,
        prompt=prompt,
        source_video_path=source_video_path,
        control_video_path=control_video_path,
        control_weight=control_weight,
    )

    # Write config to JSON file
    config_path = Path(CONFIG_PATH)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(transfer_config, f, indent=2)

    logger.info(f"Transfer config written to {config_path}:")
    logger.info(json.dumps(transfer_config, indent=2))

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Transfer 2B always needs multi-GPU (65.4GB VRAM)
    # --log-dir captures per-worker error files for debugging
    log_dir = "/tmp/torchrun_logs"
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    cmd = [
        "torchrun",
        f"--nproc_per_node={num_gpus}",
        f"--log-dir={log_dir}",
        COSMOS_INFERENCE_SCRIPT,
        "-i", str(config_path),
        "-o", output_dir,
        f"--model={cosmos_control_type}",
    ]

    if disable_guardrails:
        cmd.append("--disable-guardrails")
        logger.info("Guardrails DISABLED (--disable-guardrails)")
    else:
        logger.info("Guardrails enabled")

    # Set environment variables
    env = os.environ.copy()
    env["HF_HOME"] = hf_home
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    # NCCL debugging (set to WARN for production, INFO for troubleshooting)
    env["NCCL_DEBUG"] = "WARN"

    if hf_token:
        env["HF_TOKEN"] = hf_token

    # Set CUDA_VISIBLE_DEVICES if not already set
    if "CUDA_VISIBLE_DEVICES" not in env:
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(i) for i in range(num_gpus))

    # Log command
    logger.info("Running Cosmos Transfer 2.5 inference:")
    logger.info(f"  Control type: {control_type} (cosmos: {cosmos_control_type})")
    logger.info(f"  Multi-GPU: True (num_gpus={num_gpus})")
    logger.info(f"  Command: {' '.join(cmd)}")
    logger.info(f"  Output dir: {output_dir}")
    logger.info(f"  HF_HOME: {hf_home}")

    try:
        # Run inference — don't capture output so torchrun child processes
        # write directly to stdout/stderr (visible in CloudWatch)
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            cmd,
            env=env,
            check=True,
            text=True,
            cwd=COSMOS_REPO_DIR
        )  # nosemgrep: dangerous-subprocess-use-audit

        logger.info("Inference completed successfully")

        return output_dir

    except subprocess.CalledProcessError as e:
        logger.error(f"Inference failed with exit code {e.returncode}.")
        # Dump torchrun error files for worker-level tracebacks
        import glob
        for error_file in glob.glob(f"{log_dir}/**/*.log", recursive=True):
            try:
                content = Path(error_file).read_text()
                if content.strip():
                    logger.error(f"=== Torchrun log: {error_file} ===")
                    logger.error(content[-3000:])
            except Exception:
                pass
        raise RuntimeError(f"Inference failed with exit code {e.returncode}. Check CloudWatch logs for full error output.")
