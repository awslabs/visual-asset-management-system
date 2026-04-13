"""
Cosmos Predict Inference Wrapper

Routes to correct Cosmos inference script (text2world.py or video2world.py)
and handles preview generation.
"""

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# CWD must be the repo root so the framework can find config files via relative paths
# (e.g., cosmos_predict1/diffusion/config/config.py)
COSMOS_REPO_DIR = "/opt/cosmos-predict1"
COSMOS_INFERENCE_DIR = "/opt/cosmos-predict1/cosmos_predict1/diffusion/inference"


def run_inference(
    model_type: str,
    model_size: str,
    prompt: Optional[str],
    input_file_path: Optional[str],
    output_dir: str,
    efs_model_dir: str,
    num_gpus: int = 4,
    disable_prompt_upsampler: bool = False,
    disable_guardrails: bool = False,
) -> str:
    """
    Run Cosmos inference using torchrun.

    Args:
        model_type: "text2world" or "video2world"
        model_size: "7B" or "13B"
        prompt: Text prompt (required for text2world, optional for video2world)
        input_file_path: Input video path (required for video2world)
        output_dir: Output directory for generated videos
        efs_model_dir: Base directory containing all models on EFS
        num_gpus: Number of GPUs to use (default: 4 for g5.12xlarge)
        disable_prompt_upsampler: Whether to disable prompt upsampler (for explicit video2world prompts)
        disable_guardrails: If True, use --disable_guardrail instead of --offload_guardrail_models

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

    # Determine inference script
    if model_type == "text2world":
        script_name = "text2world.py"
    else:
        script_name = "video2world.py"

    script_path = Path(COSMOS_INFERENCE_DIR) / script_name

    if not script_path.exists():
        raise RuntimeError(f"Inference script not found: {script_path}")

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Determine diffusion transformer directory name
    if model_type == "text2world":
        diffusion_dir = f"Cosmos-Predict1-{model_size}-Text2World"
    else:
        diffusion_dir = f"Cosmos-Predict1-{model_size}-Video2World"

    # Build command using the correct Cosmos CLI arguments
    # Reference: text2world.py --help / video2world.py --help
    cmd = [
        "torchrun",
        f"--nproc_per_node={num_gpus}",
        str(script_path),
        "--checkpoint_dir", efs_model_dir,
        "--diffusion_transformer_dir", diffusion_dir,
        "--video_save_folder", output_dir,
        "--num_gpus", str(num_gpus),
        "--offload_diffusion_transformer",
        "--offload_tokenizer",
        "--offload_text_encoder_model",
        # Disable prompt upsampler by default (matching reference implementation)
        # The prompt is already handled by VAMS metadata/inputParameters
        "--disable_prompt_upsampler",
        "--disable_guardrail" if disable_guardrails else "--offload_guardrail_models",
    ]

    if disable_guardrails:
        logger.info("Guardrails DISABLED (--disable_guardrail)")
    else:
        logger.info("Guardrails enabled with CPU offloading (--offload_guardrail_models)")

    # Add model type specific arguments
    if model_type == "text2world":
        if not prompt or not prompt.strip():
            raise ValueError("Text2World requires a text prompt")
        cmd.extend(["--prompt", prompt])
    else:  # video2world
        if not input_file_path:
            raise ValueError("Video2World requires an input video/image file")
        cmd.extend(["--input_image_or_video_path", input_file_path])

        # Auto-detect num_input_frames based on file type:
        # - Images (.jpg, .jpeg, .png, .webp, .gif): 1 frame
        # - Videos (.mp4, .mov, .avi, .mkv): 9 frames (better temporal continuity)
        image_extensions = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp", ".tiff"}
        file_ext = Path(input_file_path).suffix.lower()
        num_input_frames = "1" if file_ext in image_extensions else "9"
        cmd.extend(["--num_input_frames", num_input_frames])
        logger.info(f"Input file type: {file_ext} → num_input_frames={num_input_frames}")

        # Cosmos video2world.py REQUIRES --prompt (assertion in the code).
        # If no user prompt, use a generic one that lets the model generate freely.
        v2w_prompt = prompt.strip() if prompt and prompt.strip() else "Continue the scene from the input video"
        cmd.extend(["--prompt", v2w_prompt])
        # Disable prompt upsampler since we're providing an explicit prompt
        cmd.append("--disable_prompt_upsampler")

    # Set environment variables
    env = os.environ.copy()
    env["PYTHONPATH"] = "/opt/cosmos-predict1"
    env["PYTORCH_CUDA_ALLOC_CONF"] = "expandable_segments:True"

    # Log command
    logger.info(f"Running Cosmos inference:")
    logger.info(f"  Model: {model_type} ({model_size})")
    logger.info(f"  Command: {' '.join(cmd)}")
    logger.info(f"  Output dir: {output_dir}")

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
        logger.info(f"stdout: {result.stdout}")

        return output_dir

    except subprocess.CalledProcessError as e:
        logger.error(f"Inference failed with exit code {e.returncode}")
        logger.error(f"stdout: {e.stdout}")
        logger.error(f"stderr: {e.stderr}")
        raise RuntimeError(f"Inference failed: {e.stderr}")


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

        logger.info(f"Preview GIF generated successfully")
        return str(output_path)

    except subprocess.CalledProcessError as e:
        logger.error(f"GIF generation failed with exit code {e.returncode}")
        logger.error(f"stderr: {e.stderr}")
        raise RuntimeError(f"GIF generation failed: {e.stderr}")
    except Exception as e:
        logger.error(f"GIF generation failed: {e}")
        raise RuntimeError(f"GIF generation failed: {e}")
