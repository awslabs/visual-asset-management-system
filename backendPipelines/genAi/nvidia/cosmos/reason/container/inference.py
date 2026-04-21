"""
Cosmos Reason 2 Inference Wrapper

Runs the cosmos-reason2-inference CLI tool in offline mode to analyze
video/image files and produce text output (captions, descriptions, reasoning).

The CLI command:
  cosmos-reason2-inference offline --model nvidia/Cosmos-Reason2-{size} \
      --videos /tmp/input/file.mp4 -i /tmp/reason_config.yaml \
      --max-model-len 16384 -o /tmp/output

For images:
  cosmos-reason2-inference offline --model nvidia/Cosmos-Reason2-{size} \
      --images /tmp/input/file.jpg -i /tmp/reason_config.yaml \
      --max-model-len 16384 -o /tmp/output
"""

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

# CWD must be the repo root so the framework can find internal modules
COSMOS_REPO_DIR = "/opt/cosmos-reason2"
CONFIG_PATH = "/tmp/reason_config.yaml"

# File extensions classified as video vs image
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".tif", ".gif"}


def is_video_file(file_path: str) -> bool:
    """
    Determine if a file is a video based on its extension.

    Args:
        file_path: Path to the file

    Returns:
        True if video, False if image
    """
    ext = Path(file_path).suffix.lower()
    return ext in VIDEO_EXTENSIONS


def build_reason_config(prompt: str) -> dict:
    """
    Build the YAML config dict for cosmos-reason2 inference.

    The config follows the InputConfig pydantic model which accepts:
    - user_prompt: The question/instruction for the model
    - system_prompt: System instruction (default: "You are a helpful assistant.")
    - sampling_params: Empty dict for defaults

    Args:
        prompt: The question/instruction prompt for the model

    Returns:
        Config dict ready for YAML serialization
    """
    return {
        "user_prompt": prompt,
        "system_prompt": "You are a helpful assistant.",
        "sampling_params": {},
    }


def run_inference(
    model_name: str,
    model_size: str,
    prompt: str,
    input_file_path: str,
    output_dir: str,
    hf_home: str,
    hf_token: Optional[str] = None,
    max_model_len: int = 16384,
) -> str:
    """
    Run Cosmos Reason 2 inference using the CLI tool.

    Args:
        model_name: Full model name (e.g., "nvidia/Cosmos-Reason2-2B")
        model_size: Model size ("2B" or "8B")
        prompt: Text prompt/question for analysis
        input_file_path: Path to input video or image file
        output_dir: Output directory for results
        hf_home: HuggingFace cache directory (HF_HOME)
        hf_token: HuggingFace API token (optional)
        max_model_len: Maximum model context length (default: 16384)

    Returns:
        The text output from the model

    Raises:
        ValueError: If required inputs are missing
        RuntimeError: If inference fails
    """
    # Validate inputs
    if not input_file_path:
        raise ValueError("Cosmos Reason requires an input file (video or image)")

    if not prompt or prompt.strip() == "":
        prompt = "Caption the video in detail."
        logger.info(f"No prompt provided, using default: {prompt}")

    # Build config
    reason_config = build_reason_config(prompt=prompt)

    # Write config to YAML file
    config_path = Path(CONFIG_PATH)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        if HAS_YAML:
            yaml.dump(reason_config, f, default_flow_style=False)
        else:
            # Simple YAML serialization fallback
            for key, value in reason_config.items():
                f.write(f"{key}: \"{value}\"\n")

    logger.info(f"Reason config written to {config_path}:")
    logger.info(json.dumps(reason_config, indent=2))

    # Create output directory
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Determine if input is video or image
    is_video = is_video_file(input_file_path)
    input_flag = "--videos" if is_video else "--images"
    input_type_label = "video" if is_video else "image"

    # Build command
    cmd = [
        "cosmos-reason2-inference",
        "offline",
        "--model", model_name,
        input_flag, input_file_path,
        "-i", str(config_path),
        "--max-model-len", str(max_model_len),
        "-o", output_dir,
    ]

    # Set environment variables
    env = os.environ.copy()
    env["HF_HOME"] = hf_home

    if hf_token:
        env["HF_TOKEN"] = hf_token

    # Set CUDA_VISIBLE_DEVICES if not already set
    if "CUDA_VISIBLE_DEVICES" not in env:
        env["CUDA_VISIBLE_DEVICES"] = "0"

    # Use vLLM v0 engine for better compatibility with diverse GPU architectures
    # The v1 engine has stricter requirements around Triton JIT compilation
    env["VLLM_USE_V1"] = "0"

    # Log command
    logger.info("Running Cosmos Reason 2 inference:")
    logger.info(f"  Model: {model_name} ({model_size})")
    logger.info(f"  Input type: {input_type_label}")
    logger.info(f"  Input file: {input_file_path}")
    logger.info(f"  Prompt: {prompt}")
    logger.info(f"  Command: {' '.join(cmd)}")
    logger.info(f"  Output dir: {output_dir}")
    logger.info(f"  HF_HOME: {hf_home}")
    logger.info(f"  Max model len: {max_model_len}")

    try:
        # Run inference — merge stderr into stdout so CloudWatch captures full output
        # Also tee to a file so we can parse the model's text output
        stdout_file = Path(output_dir) / "_inference_stdout.txt"
        with open(stdout_file, "w") as stdout_fh:
            process = subprocess.Popen(  # nosemgrep: dangerous-subprocess-use-audit
                cmd,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,  # merge stderr into stdout for CloudWatch visibility
                text=True,
                cwd=COSMOS_REPO_DIR,
                bufsize=1,
            )  # nosemgrep: dangerous-subprocess-use-audit
            # Read combined output line by line, write to file and print to console
            for line in process.stdout:
                print(line, end="", flush=True)
                stdout_fh.write(line)
            process.wait()

        if process.returncode != 0:
            raise subprocess.CalledProcessError(process.returncode, cmd)

        logger.info("Inference completed successfully")

        # Parse output - try output files first, then stdout
        stdout_text = stdout_file.read_text() if stdout_file.exists() else ""
        output_text = _extract_output(output_dir, stdout_text)

        return output_text

    except subprocess.CalledProcessError as e:
        logger.error(f"Inference failed with exit code {e.returncode}")
        raise RuntimeError(f"Inference failed with exit code {e.returncode}. Check CloudWatch logs for full error output.")


def _extract_output(output_dir: str, stdout: str) -> dict:
    """
    Extract the model's text output from output directory or stdout.

    Returns a dict with: systemPrompt, userPrompt, result, jobLogs
    """
    output_path = Path(output_dir)

    # Check for output files (JSON, TXT, or JSONL) — skip our internal stdout file
    for pattern in ["*.json", "*.jsonl", "*.txt"]:
        for output_file in output_path.glob(pattern):
            if output_file.name.startswith("_"):
                continue
            try:
                content = output_file.read_text().strip()
                if content:
                    logger.info(f"Found output in file: {output_file}")
                    return {
                        "systemPrompt": "",
                        "userPrompt": "",
                        "result": content,
                        "jobLogs": "",
                    }
            except Exception as e:
                logger.warning(f"Failed to read output file {output_file}: {e}")

    # Fall back to stdout — parse structured sections from cosmos-reason2 CLI output
    if stdout and stdout.strip():
        logger.info("Using stdout as output, extracting structured response")
        return _parse_structured_output(stdout)

    return {
        "systemPrompt": "",
        "userPrompt": "",
        "result": "No output generated",
        "jobLogs": "",
    }


def _parse_structured_output(stdout: str) -> dict:
    """
    Parse the cosmos-reason2-inference CLI stdout into structured fields.

    The CLI prints output in this format:
        --------------------
        System:
          ...
        --------------------
        User:
          ...
        --------------------
        Reasoning:       (optional)
          ...
        --------------------
        Assistant:
          ...
        --------------------
        Inference time: X.XX seconds

    Returns a dict with: systemPrompt, userPrompt, result, reasoning (optional), jobLogs
    """
    import re

    # Strip ANSI escape codes
    ansi_pattern = re.compile(r'\x1b\[[0-9;]*m')
    clean = ansi_pattern.sub('', stdout)

    separator = "--------------------"

    # Split into sections by the separator
    sections = clean.split(separator)

    system_prompt = ""
    user_prompt = ""
    reasoning = ""
    assistant = ""
    job_logs_parts = []

    for i, section in enumerate(sections):
        stripped = section.strip()
        if not stripped:
            continue

        if stripped.startswith("System:"):
            system_prompt = stripped[len("System:"):].strip()
        elif stripped.startswith("User:"):
            user_prompt = stripped[len("User:"):].strip()
        elif stripped.startswith("Reasoning:"):
            reasoning = _clean_section(stripped[len("Reasoning:"):])
        elif stripped.startswith("Assistant:"):
            assistant = _clean_section(stripped[len("Assistant:"):])
        else:
            # Everything else is job logs (model loading, progress bars, etc.)
            job_logs_parts.append(stripped)

    result = {
        "systemPrompt": system_prompt,
        "userPrompt": user_prompt,
        "result": assistant if assistant else reasoning,
        "jobLogs": "\n".join(job_logs_parts),
    }

    if reasoning and assistant:
        result["reasoning"] = reasoning

    if not result["result"]:
        logger.warning("Could not extract Assistant or Reasoning section")
        result["result"] = stdout.strip()

    return result


def _clean_section(text: str) -> str:
    """Clean a parsed section: strip whitespace, remove progress bar artifacts."""
    lines = []
    for line in text.strip().split('\n'):
        stripped = line.strip()
        # Skip progress bar lines, empty EngineCore prefixes, and inference time
        if stripped and not stripped.startswith('(EngineCore') and '|' not in stripped[:5] and not stripped.startswith('Inference time:'):
            lines.append(stripped)
    return '\n'.join(lines).strip()
