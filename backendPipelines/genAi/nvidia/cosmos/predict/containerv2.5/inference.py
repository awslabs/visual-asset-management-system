"""
Cosmos Predict 2.5 Inference Wrapper

Routes to the correct inference mode via the cosmos-predict2.5 examples/inference.py
script using a JSON config file. Handles both 2B (single-node python) and 14B
(multi-GPU torchrun) execution.
"""

import json
import logging
import os
import collections
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

# CWD must be the repo root so the framework can find internal modules
COSMOS_REPO_DIR = "/opt/cosmos-predict2.5"
COSMOS_INFERENCE_SCRIPT = "/opt/cosmos-predict2.5/examples/inference.py"
CONFIG_PATH = "/tmp/inference_config.json"

# The name of the single inference sample this container submits. The framework writes each sample's
# artifacts as `{output_dir}/{name}.mp4` alongside a `{name}.json`, so this also names the video the
# container looks for afterwards -- one constant rather than the same literal in two files.
INFERENCE_SAMPLE_NAME = "vams_inference"


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
        "name": INFERENCE_SAMPLE_NAME,
        "prompt": prompt or "",
        "num_output_frames": num_output_frames,
        "seed": seed,
        "guidance": guidance,
    }

    if inference_type == "video2world" and input_file_path:
        config["input_path"] = input_file_path

    return config


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
        # 2B: direct python (-u = unbuffered stdio so progress streams to CloudWatch)
        cmd = [
            "python",
            "-u",
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

    returncode, output_tail = _run_streaming(cmd, env=env, cwd=COSMOS_REPO_DIR)
    if returncode != 0:
        # The tail goes in the RAISED message, not only the log: this exception's text is
        # what the workflow records as the execution's error, and that record was
        # previously the one place the cause did not appear.
        logger.error(
            f"Inference failed with exit code {returncode}. Last output:\n{output_tail}"
        )
        raise RuntimeError(
            f"Inference failed with exit code {returncode}. Last output:\n{output_tail}"
        )

    logger.info("Inference completed successfully")

    return output_dir



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
