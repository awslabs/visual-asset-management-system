"""
Gr00t Fine-Tuning Inference Wrapper

Wraps the gr00t FinetuneWorkflow for single-GPU and multi-GPU (torchrun) execution.
Based on the sample finetune_gr00t.py from the NVIDIA embodied AI platform.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

GROOT_REPO_DIR = "/workspace"
FINETUNE_SCRIPT = "/workspace/scripts/finetune_gr00t.py"


def run_training(
    config: Dict,
    dataset_path: str,
    output_dir: str,
    hf_home: str,
    hf_token: Optional[str] = None,
) -> str:
    """
    Run Gr00t fine-tuning.

    For single GPU: direct python execution
    For multi-GPU: torchrun --nproc_per_node={num_gpus}

    Args:
        config: Merged training configuration dict
        dataset_path: Path to local dataset directory
        output_dir: Path to output checkpoint directory
        hf_home: HuggingFace cache directory
        hf_token: HuggingFace API token

    Returns:
        Path to output directory

    Raises:
        RuntimeError: If training fails
    """
    num_gpus = int(config.get("numGpus", 1))

    # Set environment variables for the training script
    env = os.environ.copy()
    env["HF_HOME"] = hf_home
    env["PYTHONPATH"] = GROOT_REPO_DIR

    if hf_token:
        env["HF_TOKEN"] = hf_token

    # Map config to environment variables expected by finetune_gr00t.py
    env_mappings = {
        "DATASET_LOCAL_DIR": dataset_path,
        "OUTPUT_DIR": output_dir,
        "BASE_MODEL_PATH": config.get("baseModelPath", "nvidia/GR00T-N1.5-3B"),
        "DATA_CONFIG": config.get("dataConfig", "so100_dualcam"),
        "MAX_STEPS": str(config.get("maxSteps", 6000)),
        "BATCH_SIZE": str(config.get("batchSize", 32)),
        "LEARNING_RATE": str(config.get("learningRate", "1e-4")),
        "WEIGHT_DECAY": str(config.get("weightDecay", "1e-5")),
        "WARMUP_RATIO": str(config.get("warmupRatio", "0.05")),
        "SAVE_STEPS": str(config.get("saveSteps", 2000)),
        "NUM_GPUS": str(num_gpus),
        "LORA_RANK": str(config.get("loraRank", 0)),
        "LORA_ALPHA": str(config.get("loraAlpha", 16)),
        "LORA_DROPOUT": str(config.get("loraDropout", "0.1")),
        "TUNE_LLM": str(config.get("tuneLlm", "false")).lower(),
        "TUNE_VISUAL": str(config.get("tuneVisual", "false")).lower(),
        "TUNE_PROJECTOR": str(config.get("tuneProjector", "true")).lower(),
        "TUNE_DIFFUSION_MODEL": str(config.get("tuneDiffusionModel", "true")).lower(),
        "EMBODIMENT_TAG": config.get("embodimentTag", "new_embodiment"),
        "VIDEO_BACKEND": config.get("videoBackend", "torchvision_av"),
        "REPORT_TO": "tensorboard",
        "UPLOAD_TARGET": "none",
    }

    for key, value in env_mappings.items():
        env[key] = value

    # Build command
    if num_gpus > 1:
        cmd = [
            "python", "-m", "torch.distributed.run",
            f"--nproc_per_node={num_gpus}",
            FINETUNE_SCRIPT,
        ]
    else:
        cmd = [
            "python",
            FINETUNE_SCRIPT,
        ]

    logger.info("Running Gr00t fine-tuning:")
    logger.info(f"  Dataset: {dataset_path}")
    logger.info(f"  Output: {output_dir}")
    logger.info(f"  Model: {env_mappings['BASE_MODEL_PATH']}")
    logger.info(f"  Data Config: {env_mappings['DATA_CONFIG']}")
    logger.info(f"  Max Steps: {env_mappings['MAX_STEPS']}")
    logger.info(f"  Batch Size: {env_mappings['BATCH_SIZE']}")
    logger.info(f"  LoRA Rank: {env_mappings['LORA_RANK']}")
    logger.info(f"  Num GPUs: {num_gpus}")
    logger.info(f"  Command: {' '.join(cmd)}")

    try:
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            cmd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            cwd=GROOT_REPO_DIR
        )  # nosemgrep: dangerous-subprocess-use-audit

        logger.info("Training completed successfully")
        logger.info(f"stdout (last 2000 chars): {result.stdout[-2000:]}")

        return output_dir

    except subprocess.CalledProcessError as e:
        logger.error(f"Training failed with exit code {e.returncode}")
        logger.error(f"stdout: {e.stdout[-2000:]}")
        logger.error(f"stderr: {e.stderr[-2000:]}")
        raise RuntimeError(f"Training failed: {e.stderr[-500:]}")
