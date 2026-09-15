"""
Cosmos Transfer 2.5 Inference Wrapper

Routes to the correct inference mode via the cosmos-transfer2.5 examples/inference.py
script using a JSON config file. Transfer 2B requires 65.4GB VRAM, so ALWAYS uses
torchrun with 8 GPUs (p4d.24xlarge).
"""

import collections
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


def resolve_control_type(control_type: str) -> str:
    """The cosmos-transfer2.5 config key and model flag for a VAMS control type.

    An unrecognised value is rejected rather than substituted. Falling back would run the edge model
    for a control signal the operator did not ask for, name the output file after the type they DID
    ask for, and report success -- so nothing about the run would prompt a re-check.
    """
    key = control_type.strip().lower() if isinstance(control_type, str) else control_type
    if key not in CONTROL_TYPE_MAP:
        raise ValueError(
            f"Unsupported controlType '{control_type}'. Supported control types: "
            f"{', '.join(sorted(CONTROL_TYPE_MAP))}"
        )
    return CONTROL_TYPE_MAP[key]


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
    cosmos_control_type = resolve_control_type(control_type)

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

    torchrun's per-worker tracebacks land in its own log directory rather than on this stream, so the
    caller still dumps those on failure — this tail carries the launcher's output, which is what names
    the failing worker and the reason it was reaped.
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
        ValueError: If required inputs are missing or control_type is not a supported signal
        RuntimeError: If inference fails
    """
    # Validate inputs
    if not source_video_path:
        raise ValueError("Transfer requires source_video_path")

    # Map control type to cosmos model flag
    cosmos_control_type = resolve_control_type(control_type)

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

    returncode, output_tail = _run_streaming(cmd, env=env, cwd=COSMOS_REPO_DIR)
    if returncode != 0:
        logger.error(f"Inference failed with exit code {returncode}.")
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
        # The tail goes in the RAISED message, not only the log: this exception's text is what the
        # workflow records as the execution's error, and that record was previously the one place the
        # cause did not appear. The torchrun per-worker logs above stay in CloudWatch, where they can
        # be read in full without bounding them into an error field.
        raise RuntimeError(
            f"Inference failed with exit code {returncode}. Last output:\n{output_tail}"
        )

    logger.info("Inference completed successfully")

    return output_dir
