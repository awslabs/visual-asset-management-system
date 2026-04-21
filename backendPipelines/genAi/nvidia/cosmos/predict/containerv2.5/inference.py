"""
Cosmos Predict 2.5 Inference Wrapper

Routes to the correct inference mode via the cosmos-predict2.5 examples/inference.py
script using a JSON config file. Handles both 2B (single-node python) and 14B
(multi-GPU torchrun) execution.
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
COSMOS_REPO_DIR = "/opt/cosmos-predict2.5"
COSMOS_INFERENCE_SCRIPT = "/opt/cosmos-predict2.5/examples/inference.py"
CONFIG_PATH = "/tmp/inference_config.json"


def build_inference_config(
    inference_type: str,
    prompt: Optional[str],
    input_file_path: Optional[str] = None,
    num_output_frames: int = 61,
    seed: int = 0,
    guidance: int = 3,
) -> dict:
    """
    Build the JSON config dict for cosmos-predict2.5 inference.

    Args:
        inference_type: "text2world" or "video2world"
        prompt: Text prompt for generation
        input_file_path: Input video/image path (required for video2world)
        num_output_frames: Number of output frames (default: 61, ~4s at 16fps)
        seed: Random seed (default: 0)
        guidance: Guidance scale (default: 3)

    Returns:
        Config dict ready for JSON serialization
    """
    config = {
        "inference_type": inference_type,
        "name": "vams_inference",
        "prompt": prompt or "",
        "num_output_frames": num_output_frames,
        "seed": seed,
        "guidance": guidance,
    }

    if inference_type == "video2world" and input_file_path:
        config["input_path"] = input_file_path

    return config


def run_inference(
    model_type: str,
    model_size: str,
    model_subpath: str,
    prompt: Optional[str],
    input_file_path: Optional[str],
    output_dir: str,
    hf_home: str,
    hf_token: Optional[str] = None,
    disable_guardrails: bool = True,
    num_gpus: int = 8,
    offload_text_encoder: bool = True,
    offload_tokenizer: bool = True,
    offload_diffusion_model: bool = True,
) -> str:
    """
    Run Cosmos Predict 2.5 inference.

    For 2B models: uses direct `python examples/inference.py`
    For 14B models: uses `torchrun --nproc_per_node=8 examples/inference.py`

    Args:
        model_type: "text2world" or "video2world"
        model_size: "2B", "14B", or "2B-distilled"
        model_subpath: Model subpath (e.g., "2B/post-trained", "14B/post-trained", "2B/distilled")
        prompt: Text prompt (required for text2world, optional for video2world)
        input_file_path: Input video path (required for video2world)
        output_dir: Output directory for generated videos
        hf_home: HuggingFace cache directory (HF_HOME)
        hf_token: HuggingFace API token (optional)
        disable_guardrails: Whether to disable guardrails (default: True)
        num_gpus: Number of GPUs for 14B multi-GPU mode (default: 8)

    Returns:
        Path to output directory

    Raises:
        ValueError: If required inputs are missing
        RuntimeError: If inference fails
    """
    # Validate inputs
    if model_type == "text2world":
        if not prompt or prompt.strip() == "":
            raise ValueError("Text2World requires a non-empty prompt")
    elif model_type == "video2world":
        if not input_file_path:
            raise ValueError("Video2World requires input_file_path")
    else:
        raise ValueError(f"Invalid model_type: {model_type}. Must be 'text2world' or 'video2world'")

    # For video2world without an explicit prompt, use a generic continuation prompt
    effective_prompt = prompt
    if model_type == "video2world" and (not prompt or not prompt.strip()):
        effective_prompt = "Continue the scene from the input video"

    # Build inference config
    inference_config = build_inference_config(
        inference_type=model_type,
        prompt=effective_prompt,
        input_file_path=input_file_path,
    )

    # Write config to JSON file
    config_path = Path(CONFIG_PATH)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(inference_config, f, indent=2)

    logger.info(f"Inference config written to {config_path}:")
    logger.info(json.dumps(inference_config, indent=2))

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Determine if multi-GPU (14B) or single-node (2B)
    is_multi_gpu = model_size.startswith("14B")

    # tyro CLI with OmitArgPrefixes flattens the namespace:
    # --setup.model becomes --model, --setup.disable_guardrails becomes --disable-guardrails
    # (tyro also converts underscores to dashes in flag names)
    if is_multi_gpu:
        # 14B: torchrun multi-GPU
        cmd = [
            "torchrun",
            f"--nproc_per_node={num_gpus}",
            COSMOS_INFERENCE_SCRIPT,
            "-i", str(config_path),
            "-o", output_dir,
            f"--model={model_subpath}",
        ]
    else:
        # 2B: direct python
        cmd = [
            "python",
            COSMOS_INFERENCE_SCRIPT,
            "-i", str(config_path),
            "-o", output_dir,
            f"--model={model_subpath}",
        ]

    if disable_guardrails:
        cmd.append("--disable-guardrails")
        logger.info("Guardrails DISABLED (--disable-guardrails)")
    else:
        logger.info("Guardrails enabled")

    # Offload flags: move model components to CPU RAM to reduce GPU VRAM usage
    # Enabled by default for g5/g6e instances (24GB/GPU). Disable for larger GPUs (A100/H100).
    offload_flags = []
    if offload_text_encoder:
        offload_flags.append("--offload-text-encoder")
    if offload_tokenizer:
        offload_flags.append("--offload-tokenizer")
    if offload_diffusion_model:
        offload_flags.append("--offload-diffusion-model")

    if offload_flags:
        cmd.extend(offload_flags)
        logger.info(f"Offloading enabled: {', '.join(offload_flags)}")
    else:
        logger.info("No offloading -- all models loaded directly to GPU")

    # Set environment variables
    env = os.environ.copy()
    env["HF_HOME"] = hf_home
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    if hf_token:
        env["HF_TOKEN"] = hf_token

    # Set CUDA_VISIBLE_DEVICES if not already set
    if "CUDA_VISIBLE_DEVICES" not in env:
        if is_multi_gpu:
            env["CUDA_VISIBLE_DEVICES"] = ",".join(str(i) for i in range(num_gpus))
        else:
            env["CUDA_VISIBLE_DEVICES"] = "0"

    # Log command
    logger.info("Running Cosmos Predict 2.5 inference:")
    logger.info(f"  Model: {model_type} ({model_size}, subpath={model_subpath})")
    logger.info(f"  Multi-GPU: {is_multi_gpu} (num_gpus={num_gpus})")
    logger.info(f"  Command: {' '.join(cmd)}")
    logger.info(f"  Output dir: {output_dir}")
    logger.info(f"  HF_HOME: {hf_home}")

    try:
        # Run inference
        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            cmd,
            env=env,
            check=True,
            capture_output=True,
            text=True,
            cwd=COSMOS_REPO_DIR
        ) # nosemgrep: dangerous-subprocess-use-audit

        logger.info("Inference completed successfully")
        logger.info(f"stdout: {result.stdout[-2000:]}")  # Last 2000 chars to avoid log overflow

        return output_dir

    except subprocess.CalledProcessError as e:
        logger.error(f"Inference failed with exit code {e.returncode}")
        logger.error(f"stdout: {e.stdout[-2000:]}")
        logger.error(f"stderr: {e.stderr[-2000:]}")
        raise RuntimeError(f"Inference failed: {e.stderr[-500:]}")


def generate_preview_gif(video_path: str, output_path: str, duration: int = 2, fps: int = 10, width: int = 320) -> str:
    """
    Generate preview GIF from video using ffmpeg.

    Args:
        video_path: Path to input video file
        output_path: Path to output GIF file
        duration: Duration of GIF in seconds (default: 2)
        fps: Frames per second for GIF (default: 10)
        width: Width of GIF in pixels, height auto-scaled (default: 320)

    Returns:
        Path to generated GIF

    Raises:
        RuntimeError: If GIF generation fails
    """
    try:
        # Create output directory if needed
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        # Build ffmpeg command
        cmd = [
            "ffmpeg",
            "-y",  # Overwrite output file
            "-i", video_path,
            "-t", str(duration),
            "-vf", f"fps={fps},scale={width}:-1",
            "-loop", "0",
            str(output_path)
        ]

        logger.info(f"Generating preview GIF: {video_path} -> {output_path}")

        result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            cmd,
            check=True,
            capture_output=True,
            text=True
        )

        logger.info("Preview GIF generated successfully")
        return str(output_path)

    except subprocess.CalledProcessError as e:
        logger.error(f"GIF generation failed with exit code {e.returncode}")
        logger.error(f"stderr: {e.stderr}")
        raise RuntimeError(f"GIF generation failed: {e.stderr}")
    except Exception as e:
        logger.error(f"GIF generation failed: {e}")
        raise RuntimeError(f"GIF generation failed: {e}")
