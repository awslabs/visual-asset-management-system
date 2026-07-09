"""
Cosmos 3 Inference Wrapper

Builds a cosmos-framework sample-argument JSON file and invokes the framework
inference entrypoint. Handles single-GPU (Nano) via `python -m
cosmos_framework.scripts.inference` and multi-GPU (Super) via `torchrun`.

The five Cosmos 3 checkpoints are all served from the one cosmos-framework
repo; the variant is selected by --checkpoint-path and the task by the
sample-arg `model_mode`.
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
COSMOS_REPO_DIR = "/opt/cosmos-framework"
SAMPLE_PATH = "/tmp/cosmos3_sample.json"

# Map VAMS variant -> cosmos-framework checkpoint name + default model_mode
VARIANT_CHECKPOINT = {
    "nano": "Cosmos3-Nano",
    "super": "Cosmos3-Super",
    "super-text2image": "Cosmos3-Super-Text2Image",
    "super-image2video": "Cosmos3-Super-Image2Video",
}
VARIANT_DEFAULT_MODE = {
    "nano": "text2video",
    "super": "text2video",
    "super-text2image": "text2image",
    "super-image2video": "image2video",
}
# Variants that require a single-GPU launch (python) vs multi-GPU (torchrun)
SINGLE_GPU_VARIANTS = {"nano"}

# Only the general-purpose omni checkpoints can perform control-signal transfer.
# The task-specialized Super checkpoints (text2image, image2video) do not.
TRANSFER_CAPABLE_VARIANTS = {"nano", "super"}

# Control-signal transfer runs on the framework's video2video model_mode.
TRANSFER_MODEL_MODE = "video2video"
# Supported control-signal types (cosmos-framework transfer controls).
TRANSFER_CONTROL_TYPES = ("edge", "blur", "depth", "seg", "wsm")


def build_sample(
    model_mode: str,
    prompt: Optional[str],
    negative_prompt: str,
    num_frames: int,
    guidance: Optional[float],
    seed: int,
    input_file_path: Optional[str],
    control_blocks: Optional[dict] = None,
    control_guidance: Optional[float] = None,
) -> dict:
    """Build the cosmos-framework sample-argument dict.

    control_blocks, when provided, is a mapping of control type ->
    {"weight": float[, "control_path": str]} for control-signal transfer
    (one entry = single control, multiple = multi-control blend).
    """
    sample = {
        "model_mode": model_mode,
        "prompt": prompt or "",
        "num_frames": num_frames,
        "seed": seed,
    }
    if negative_prompt:
        sample["negative_prompt"] = negative_prompt
    if guidance is not None:
        sample["guidance"] = guidance
    # Modes that consume an input image/video
    if input_file_path and model_mode in ("image2video", "video2video"):
        sample["vision_path"] = input_file_path
    # Control-signal transfer: attach one block per control type. A block with
    # a control_path uses a pre-computed control video; without one the
    # framework auto-computes the signal from vision_path.
    if control_blocks:
        for control_type, block in control_blocks.items():
            sample[control_type] = block
        if control_guidance is not None:
            sample["control_guidance"] = control_guidance
    return sample


def run_inference(
    variant: str,
    task_mode: str,
    prompt: Optional[str],
    negative_prompt: str,
    num_frames: int,
    guidance: Optional[float],
    seed: int,
    input_file_path: Optional[str],
    output_dir: str,
    hf_home: str,
    hf_token: Optional[str],
    num_gpus: int,
    disable_guardrails: bool = True,
    control_blocks: Optional[dict] = None,
    control_guidance: Optional[float] = None,
) -> str:
    """Run Cosmos 3 inference for the given variant/task.

    When task_mode == "transfer", control_blocks carries the control-signal
    conditioning (single or multi-control) and inference runs on the
    video2video model_mode.
    """
    checkpoint = VARIANT_CHECKPOINT.get(variant)
    if not checkpoint:
        raise ValueError(f"Unknown Cosmos 3 variant: {variant}")

    is_transfer = task_mode == "transfer"
    if is_transfer:
        model_mode = TRANSFER_MODEL_MODE
    else:
        model_mode = task_mode or VARIANT_DEFAULT_MODE.get(variant, "text2video")

    # Validate inputs by mode
    if model_mode in ("text2image", "text2video") and (not prompt or not prompt.strip()):
        raise ValueError(f"{model_mode} requires a non-empty prompt")
    if model_mode in ("image2video", "video2video") and not input_file_path:
        raise ValueError(f"{model_mode} requires an input file")
    if is_transfer and not control_blocks:
        raise ValueError("transfer requires at least one control block")
    # For input-file modes without a prompt, use a generic continuation prompt
    effective_prompt = prompt
    if model_mode in ("image2video", "video2video") and (not prompt or not prompt.strip()):
        effective_prompt = "Continue the scene from the input"

    sample = build_sample(
        model_mode=model_mode,
        prompt=effective_prompt,
        negative_prompt=negative_prompt,
        num_frames=num_frames,
        guidance=guidance,
        seed=seed,
        input_file_path=input_file_path,
        control_blocks=control_blocks if is_transfer else None,
        control_guidance=control_guidance if is_transfer else None,
    )

    sample_path = Path(SAMPLE_PATH)
    sample_path.parent.mkdir(parents=True, exist_ok=True)
    with open(sample_path, "w") as f:
        json.dump(sample, f, indent=2)

    logger.info(f"Cosmos 3 sample written to {sample_path}:")
    logger.info(json.dumps(sample, indent=2))

    Path(output_dir).mkdir(parents=True, exist_ok=True)

    is_single_gpu = variant in SINGLE_GPU_VARIANTS or num_gpus <= 1

    if is_single_gpu:
        cmd = [
            "python", "-u", "-m", "cosmos_framework.scripts.inference",
            "--parallelism-preset=latency",
            "-i", str(sample_path),
            "-o", output_dir,
            "--checkpoint-path", checkpoint,
            "--seed", str(seed),
        ]
    else:
        cmd = [
            "torchrun", f"--nproc-per-node={num_gpus}",
            "-m", "cosmos_framework.scripts.inference",
            "--parallelism-preset=throughput",
            f"--dp-shard-size={num_gpus}",
            "--dp-replicate-size=1",
            "--cp-size=1",
            "--cfgp-size=1",
            "-i", str(sample_path),
            "-o", output_dir,
            "--checkpoint-path", checkpoint,
            "--seed", str(seed),
        ]

    if disable_guardrails:
        cmd.append("--no-guardrails")
        logger.info("Guardrails DISABLED (--no-guardrails)")

    env = os.environ.copy()
    env["HF_HOME"] = hf_home
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"
    if hf_token:
        env["HF_TOKEN"] = hf_token
    # Control-signal transfer partitions the sequence into control ranges whose
    # lengths become unbacked symints under torch.compile; on Ada (L40S / sm_89)
    # NATTEN's cutlass-fmha backend cannot resolve the resulting data-dependent
    # shape guard and aborts. Running the transfer path eagerly sidesteps the
    # torch._dynamo guard. This is applied on ALL GPUs (not just Ada) so transfer
    # works regardless of the instance type the operator selects: eager execution
    # is always correct, and on H100/H200 it only forgoes a compile optimization
    # the FNA backend would otherwise use.
    if is_transfer:
        env["TORCH_COMPILE_DISABLE"] = "1"
        env["TORCHDYNAMO_DISABLE"] = "1"
        logger.info("Transfer mode: torch.compile disabled (eager) for cross-GPU compatibility (avoids the NATTEN cutlass symint guard on Ada GPUs)")

    logger.info("Running Cosmos 3 inference:")
    logger.info(f"  Variant: {variant} (checkpoint={checkpoint}, mode={model_mode})")
    if is_transfer:
        logger.info(f"  Transfer controls: {list(control_blocks.keys())}")
    logger.info(f"  Single-GPU: {is_single_gpu} (num_gpus={num_gpus})")
    logger.info(f"  Command: {' '.join(cmd)}")

    try:
        subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            cmd, env=env, check=True, text=True, cwd=COSMOS_REPO_DIR,
        )  # nosemgrep: dangerous-subprocess-use-audit
        logger.info("Inference completed successfully")
        return output_dir
    except subprocess.CalledProcessError as e:
        logger.error(f"Inference failed with exit code {e.returncode}. Check CloudWatch logs.")
        raise RuntimeError(f"Inference failed with exit code {e.returncode}.")


def generate_preview_gif(video_path: str, output_path: str, duration: int = 2, fps: int = 10, width: int = 320) -> str:
    """Generate preview GIF from video using ffmpeg."""
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y", "-i", video_path,
        "-t", str(duration),
        "-vf", f"fps={fps},scale={width}:-1",
        "-loop", "0",
        str(output_path),
    ]
    logger.info(f"Generating preview GIF: {video_path} -> {output_path}")
    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        cmd, check=True, capture_output=True, text=True
    )
    logger.info("Preview GIF generated successfully")
    return str(output_path)
