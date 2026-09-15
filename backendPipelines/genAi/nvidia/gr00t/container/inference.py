"""
Gr00t Fine-Tuning Inference Wrapper

Wraps the gr00t FinetuneWorkflow for single-GPU and multi-GPU (torchrun) execution.
Based on the sample finetune_gr00t.py from the NVIDIA embodied AI platform.
"""

import json
import logging
import os
import collections
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

GROOT_REPO_DIR = "/workspace"
FINETUNE_SCRIPT = "/workspace/scripts/finetune_gr00t.py"


# Enough lines to carry a Python traceback plus the output that preceded it. Bounded because a job that
# prints a progress line per step would otherwise grow this process's memory for the whole run, and the
# failure message needs the END of the output, not all of it.
_TAIL_LINES = 80
# A single line can be arbitrarily long (a full command echo, a serialized tensor shape), so the line
# bound above is only a real memory bound with a per-line one beside it.
_TAIL_LINE_CHARS = 2000


def _run_streaming(cmd, env=None, cwd=None, tail_lines=_TAIL_LINES):
    """Run `cmd`, streaming its output onward while keeping a bounded copy of the tail.

    Returns `(returncode, tail_text)`.

    `subprocess.run(check=True)` with no capture leaves the child writing straight to the inherited
    stdout, so its output reaches CloudWatch but this process never sees it -- and the exception raised
    on failure can then only report the exit code. That message is what lands in the VAMS execution
    record, so an operator reading the record was told "exit code 1" for a cause the child had already
    printed in full.

    `capture_output=True` would hand this process the text, but only once the child has exited: `run()`
    returns nothing before then, so a multi-hour job would log NOTHING while it ran and a hang would be
    undiagnosable. It does NOT deadlock -- `run()` drains through `communicate()`, which reads both pipes
    concurrently; measured at 1.2 MB with no stall. What deadlocks is `Popen(stdout=PIPE)` followed by
    `wait()` with no reader, which is why the loop below reads before it waits.

    So: one pipe, drained incrementally as the child writes, which keeps the live log and still leaves
    this process holding the tail. stderr is merged into stdout because with two pipes and a single
    reader the unread pipe is exactly the one that fills.
    """
    tail = collections.deque(maxlen=tail_lines)
    proc = subprocess.Popen(  # nosemgrep: dangerous-subprocess-use-audit
        cmd, env=env, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )  # nosemgrep: dangerous-subprocess-use-audit
    try:
        for raw in iter(lambda: proc.stdout.readline(), b""):
            # errors="replace": container output can carry non-UTF-8 bytes, and a decode error must not
            # abort a run whose real work already succeeded.
            line = raw.decode("utf-8", "replace").rstrip("\r\n")
            # Forwarded to this process's stdout so CloudWatch still receives the child's output
            # unchanged -- the whole point is to ADD an in-process copy, not to reroute the log.
            print(line, flush=True)
            if line.strip():
                tail.append(line[:_TAIL_LINE_CHARS])
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait()
    return proc.returncode, "\n".join(tail)


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

    # Build command (-u = unbuffered stdio so training progress streams live to CloudWatch)
    if num_gpus > 1:
        cmd = [
            "python", "-u", "-m", "torch.distributed.run",
            f"--nproc_per_node={num_gpus}",
            FINETUNE_SCRIPT,
        ]
    else:
        cmd = [
            "python",
            "-u",
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

    # Don't capture output so the child process writes directly to
    # stdout/stderr (visible in CloudWatch). Capturing with
    # capture_output=True fills the OS pipe buffer (~64KB) during a
    # multi-hour fine-tune run (HF model download, torch init, training
    # metrics) and deadlocks the child on write() while the parent
    # blocks in wait().
    returncode, output_tail = _run_streaming(cmd, env=env, cwd=GROOT_REPO_DIR)
    if returncode != 0:
        # The tail goes in the RAISED message, not only the log: this exception's text is
        # what the workflow records as the execution's error, and that record was
        # previously the one place the cause did not appear.
        logger.error(
            f"Training failed with exit code {returncode}. Last output:\n{output_tail}"
        )
        raise RuntimeError(
            f"Training failed with exit code {returncode}. Last output:\n{output_tail}"
        )

    logger.info("Training completed successfully")

    return output_dir

