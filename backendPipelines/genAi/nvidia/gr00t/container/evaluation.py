"""Gr00t policy evaluation wrapper.

Wraps the upstream Isaac-GR00T `scripts/eval_policy.py`, which replays a recorded LeRobot dataset
against a checkpoint and reports the mean squared error between predicted and recorded actions. It runs
OFFLINE: no simulator and no robot are involved, only a local checkpoint and a local dataset.

Two things the upstream script does not do, which this wrapper adds:
  - it prints MSE to stdout and writes NO result file, so a VAMS execution would be recorded as
    successful with zero outputs. The MSE is parsed out and serialized to a metrics JSON.
  - it evaluates against whatever dataset it is given. When that is the same dataset the checkpoint
    trained on, the number measures FIT, not generalization, so the emitted metrics are explicitly
    labelled as a sanity check rather than a held-out score.
"""

import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

GROOT_REPO_DIR = "/workspace"
EVAL_SCRIPT = "/workspace/scripts/eval_policy.py"

# "MSE: 0.0123" per trajectory, then "Average MSE across all trajs: 0.0119".
_TRAJ_MSE = re.compile(
    # ONE spelling only. eval_policy.py prints BOTH forms for the SAME trajectory —
    #   Unnormalized Action MSE across single traj: 2.1068...
    #   MSE: 2.1068...
    # — so matching both counted every value twice: a 5-trajectory run reported 10 per-trajectory
    # entries, each duplicated. Verified against a real run's log. The bare "MSE:" form is the one
    # matched because it is the stable label; the descriptive line is the same number restated.
    r"^MSE:\s*([0-9.eE+-]+)\s*$",
    re.MULTILINE)
# Upstream DOES print this summary; it is preferred over averaging locally.
_AVG_MSE = re.compile(r"Average MSE across all trajs:\s*([0-9.eE+-]+)")

# Emitted on every run regardless of outcome, so it crowds out the failure when tailing the log.
_NOISE = re.compile(
    r"Unable to register (cuDNN|cuFFT|cuBLAS) factory"
    r"|TF-TRT Warning"
    r"|tensorflow/core/platform/cpu_feature_guard"
    r"|To enable the following instructions"
    r"|A new version of Albumentations"
    r"|check_for_updates\(\)"
    r"|tyro/_parsers\.py.*UserWarning"
    r"|^\s*warnings\.warn\("
    r"|`use_fast` is set to `True`"
)


# The language modality every data config requests, mapped to the column a LeRobot export always
# carries. experiment_cfg/metadata.json records no annotation group, so a modality.json derived from
# a checkpoint alone omits it and the dataset fails integrity before a single trajectory is replayed.
# This is the same mapping the fine-tuning path writes (finetune_gr00t.py), which is what makes the
# evaluation read the dataset the way training did.
ANNOTATION_MODALITY_DEFAULT = {"human.task_description": {"original_key": "task_index"}}


def _as_float(text: str) -> Optional[float]:
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def parse_mse(stdout: str) -> Dict[str, Any]:
    """Pull the per-trajectory and average MSE out of the eval script's stdout.

    Parsing stdout is the only option: the upstream script reports its metric with print() and writes
    no machine-readable output. A parse miss is reported as null rather than guessed at, so a silent
    upstream format change surfaces as a missing metric instead of a plausible wrong number.
    """
    per_traj = [v for v in (_as_float(m) for m in _TRAJ_MSE.findall(stdout)) if v is not None]
    avg_match = _AVG_MSE.search(stdout)
    average = _as_float(avg_match.group(1)) if avg_match else None
    if average is None and per_traj:
        # Fall back to computing it ourselves; the per-trajectory lines are the primary evidence.
        average = sum(per_traj) / len(per_traj)
    return {"averageMse": average, "perTrajectoryMse": per_traj}


def diagnostic_tail(text: Optional[str], limit: int = 120) -> List[str]:
    """The last `limit` genuinely-informative lines of a captured stream.

    Two things make a naive `splitlines()[-limit:]` useless here, and both were observed swallowing the
    real failure:

      - **Progress bars.** tqdm redraws with a carriage return, and `splitlines()` splits on `\\r` as
        well as `\\n`. One "Loading checkpoint shards" bar therefore becomes hundreds of "lines", which
        on its own overran the whole tail budget — the log ended mid-progress-bar with no traceback.
        Only the final state of each `\\r`-updated line carries information, so the rest is dropped.
      - **Import noise.** cuDNN/cuFFT/cuBLAS factory registration, TensorFlow CPU-feature notices,
        TF-TRT warnings and tyro field warnings are emitted on every run whether it succeeds or fails.

    The traceback is last, so the tail is the right window — it just has to be a tail of the lines that
    mean something.
    """
    if not text:
        return []
    lines = []
    # Split on NEWLINES only. str.splitlines() also breaks on \r, which would turn a single
    # carriage-return-redrawn progress bar into one "line" per redraw before it could be collapsed.
    for raw in text.split("\n"):
        # Keep only the last redraw of a \r-updated line: a progress bar's final state.
        line = raw.split("\r")[-1].rstrip()
        if not line:
            continue
        if _NOISE.search(line):
            continue
        lines.append(line)
    return lines[-limit:]


def _to_lerobot_modality_schema(modalities: Dict[str, Any]) -> Dict[str, Any]:
    """Convert the checkpoint's modality block into the shape modality.json is validated against.

    The two carry the SAME information in different forms. experiment_cfg describes each state/action
    group by its `shape` (single_arm [5], gripper [1]); LeRobotModalityMetadata instead requires the
    COLUMN RANGE that group occupies in the dataset's flat state/action vector — `start` and `end`.
    Passing the raw block through fails pydantic validation with "Field required: state.single_arm.start"
    for every group.

    The ranges are recovered by accumulating the shapes in declaration order, which is the order the
    columns are laid out: for this dataset single_arm [5] then gripper [1] tiles exactly onto the 6
    columns that meta/info.json reports for observation.state and action. A group whose shape is missing
    or not 1-D is left as-is rather than guessed at, so a malformed block surfaces as a validation error
    instead of a silently wrong column mapping.

    `video` groups are passed through untouched: they are keyed by camera name and carry no ranges.

    An `annotation` group is SYNTHESIZED when the checkpoint does not carry one. experiment_cfg
    records only video/state/action, but every data config's modality_config() also asks for
    `annotation.human.task_description` as its language modality — so a mapping derived purely from
    the checkpoint loads without an annotation section and the dataset then fails integrity with
    "Trying to get annotation metadata for a dataset with no annotations". A LeRobot export always
    carries the task index (`meta/tasks.jsonl` plus a `task_index` column), which is the column the
    fine-tuning path maps this key to, so the same mapping is written here. See
    ANNOTATION_MODALITY_DEFAULT.
    """
    converted: Dict[str, Any] = {}
    for group_name, group in (modalities or {}).items():
        if not isinstance(group, dict):
            continue
        if group_name == "video":
            # LeRobot expects each camera to name the dataset column holding its frames.
            converted[group_name] = {
                camera: ({"original_key": f"observation.images.{camera}"}
                         if isinstance(spec, dict) else spec)
                for camera, spec in group.items()
            }
            continue

        cursor = 0
        entries: Dict[str, Any] = {}
        for key, spec in group.items():
            if not isinstance(spec, dict):
                entries[key] = spec
                continue
            shape = spec.get("shape")
            if not (isinstance(shape, list) and len(shape) == 1 and isinstance(shape[0], int)):
                entries[key] = spec
                continue
            width = shape[0]
            entry = {"start": cursor, "end": cursor + width}
            cursor += width
            # Carry through the descriptive fields LeRobot also understands; drop `shape`, which it
            # does not accept and which start/end now express.
            for passthrough in ("absolute", "rotation_type", "continuous"):
                if spec.get(passthrough) is not None:
                    entry[passthrough] = spec[passthrough]
            entries[key] = entry
        converted[group_name] = entries

    if not converted.get("annotation"):
        converted["annotation"] = dict(ANNOTATION_MODALITY_DEFAULT)
    return converted


def ensure_dataset_modality_file(dataset_path: str, checkpoint_path: str) -> Optional[str]:
    """Make sure the dataset has a `meta/modality.json`, deriving it from the checkpoint if absent.

    eval_policy.py loads the dataset through LeRobotSingleDataset, which ASSERTS on that file:
        AssertionError: Please provide a meta/modality.json file in <dataset>
    A LeRobot export does not include it — the fine-tuning path builds the mapping from the named
    dataConfig at run time and never writes it back to the dataset. So a dataset that trains
    successfully still cannot be evaluated as-is, which is a data-contract gap rather than a user error.

    The checkpoint already carries the same mapping in `experiment_cfg/metadata.json`
    ({<embodiment>: {modalities: {video/state/action: {...}}}}), written when the model was trained.
    Reusing it guarantees the evaluation reads the dataset exactly the way training did, instead of
    re-deriving a mapping that might disagree.

    The repair is deliberately LOCAL-ONLY. It writes into the container's downloaded copy under
    /tmp/input, never back to the asset, so the user's dataset is left exactly as they uploaded it.
    Only OUTPUT_DIR is synced to S3, so nothing here can reach the input asset. The consequence is that
    every evaluation re-derives the file, which is cheap and keeps the dataset authoritative.

    Returns the path written, or None when the file already existed (the normal case for a dataset
    exported with one). Raises when neither source is available, so the run fails with an explanation
    rather than the upstream assertion.
    """
    meta_dir = Path(dataset_path) / "meta"
    target = meta_dir / "modality.json"
    if target.exists():
        logger.info(f"Dataset already provides {target}")
        return None

    source = Path(checkpoint_path) / "experiment_cfg" / "metadata.json"
    if not source.exists():
        raise RuntimeError(
            f"The dataset has no meta/modality.json and the checkpoint has no "
            f"experiment_cfg/metadata.json to derive it from ({source}). Evaluation cannot read the "
            "dataset without a modality mapping.")

    payload = json.loads(source.read_text(encoding="utf-8"))
    # One entry per embodiment tag; a fine-tune writes exactly one.
    modalities = None
    for entry in payload.values():
        if isinstance(entry, dict) and entry.get("modalities"):
            modalities = entry["modalities"]
            break
    if not modalities:
        raise RuntimeError(
            f"{source} carries no 'modalities' block, so a modality mapping cannot be derived.")

    meta_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(_to_lerobot_modality_schema(modalities), indent=2), encoding="utf-8")
    logger.info(
        f"Wrote {target} from the checkpoint's experiment_cfg (keys: {sorted(modalities)}) — the "
        "dataset did not ship one.")
    return str(target)


def log_hardware_context() -> Dict[str, Any]:
    """Log (and return) the GPU/RAM the run actually got, before anything large is loaded.

    A container killed by the kernel or the GPU driver leaves NO Python-level error: the process is
    gone, so neither a traceback nor this module's own failure handler ever runs. The log then ends
    mid-model-load with an exit code and nothing that says why — which is exactly the state the
    evaluation reached, and it is unresolvable after the fact because the evidence was never written.

    The fix is to record the constraints BEFORE the load. This matters here specifically because the
    compute environment offers instance types with very different GPU memory (an L40S has 48 GB, an
    A10G 24 GB) and the job asks only for "1 GPU", so placement — not configuration — decides whether
    a given model fits. Knowing which one ran turns a silent kill into a one-line diagnosis.

    Best-effort and never raises: this is diagnostics, and failing to collect them must not fail a run.
    """
    ctx: Dict[str, Any] = {}
    try:
        out = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
            ["nvidia-smi",
             "--query-gpu=name,memory.total,memory.used,driver_version",
             "--format=csv,noheader"],
            capture_output=True, text=True, timeout=30,
        )  # nosemgrep: dangerous-subprocess-use-audit
        gpus = [ln.strip() for ln in (out.stdout or "").splitlines() if ln.strip()]
        ctx["gpus"] = gpus
        for gpu in gpus:
            logger.info(f"GPU: {gpu}")
        if not gpus and out.stderr:
            logger.warning(f"nvidia-smi reported no GPUs: {out.stderr.strip()[:200]}")
    except Exception as e:
        logger.warning(f"Could not read GPU info (non-fatal): {e}")

    try:
        # MemAvailable is the number that matters for an OOM kill; MemTotal alone hides pressure from
        # everything already resident (the checkpoint download, the dataset, page cache).
        meminfo = {}
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            key, _, rest = line.partition(":")
            parts = rest.split()
            if parts:
                meminfo[key] = int(parts[0]) // 1024  # kB -> MiB
        ctx["hostMemTotalMiB"] = meminfo.get("MemTotal")
        ctx["hostMemAvailableMiB"] = meminfo.get("MemAvailable")
        logger.info(f"Host RAM: total={ctx['hostMemTotalMiB']} MiB "
                    f"available={ctx['hostMemAvailableMiB']} MiB")
    except Exception as e:
        logger.warning(f"Could not read host memory info (non-fatal): {e}")

    try:
        ctx["cpuCount"] = os.cpu_count()
        logger.info(f"vCPUs visible: {ctx['cpuCount']}")
    except Exception:
        pass
    return ctx


def _last_redraw(line: str) -> str:
    """The final state of a carriage-return-redrawn line, with its terminator removed.

    The trailing CR of a CRLF terminator has to come off FIRST: splitting on \\r before stripping it
    would discard the entire line on any platform that ends lines with \\r\\n, leaving an empty string.
    After that, the segment following the last remaining \\r is the newest redraw of a progress bar.
    """
    return line.rstrip("\r\n").split("\r")[-1].rstrip()


def _run_streaming(cmd, env, cwd=GROOT_REPO_DIR, echo=None):
    """Run `cmd`, streaming its output to the log while accumulating it. Returns (returncode, text).

    Deliberately NOT `subprocess.run(capture_output=True)`. That fills an OS pipe buffer (typically
    64 KB) and then blocks the child until the parent reads — but a synchronous `run()` does not read
    until the child exits, so a child that outdraws the buffer stalls and is eventually killed. The
    signature is distinctive and was observed here: exit code 1, stdout truncated mid-progress-bar,
    and NO traceback, because the child never got to print one. Loading a multi-billion-parameter
    checkpoint emits far more than 64 KB of progress output, so evaluation reliably hit it.

    Reading incrementally also means the job log fills in as the evaluation proceeds rather than only
    at the end, so a run that hangs can be diagnosed while it is still hanging.

    stderr is merged into stdout so one read loop drains both — with two pipes and one reader, the
    unread pipe is exactly the one that fills. Merging costs interleaving fidelity, which does not
    matter here: the metric is parsed by line pattern, not by stream.
    """
    emit = echo or (lambda line: logger.info(f"  {line}"))
    # The NORMALIZED lines — the same text that was logged, one entry per real line. Returning these
    # rather than the raw bytes means what gets parsed is what the operator sees in CloudWatch, and it
    # also recovers a metric printed as the final redraw of a progress line: in the raw stream that
    # value sits after a \r and a line-anchored regex never sees it.
    lines: List[str] = []
    # Read BYTES, not text. In text mode Python's universal newlines translate a lone \r into \n
    # before the reader sees it, so a tqdm bar arrives as one indistinguishable "line" per redraw and
    # no amount of post-processing can collapse it — the information needed to tell a redraw from a
    # finished line has already been destroyed. Decoding here keeps \r intact.
    proc = subprocess.Popen(  # nosemgrep: dangerous-subprocess-use-audit
        cmd, env=env, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )  # nosemgrep: dangerous-subprocess-use-audit
    try:
        buffer = ""
        for raw in iter(lambda: proc.stdout.readline(), b""):
            # errors="replace": container output can carry non-UTF-8 bytes, and a decode error must
            # not abort a run whose real work already succeeded.
            text = raw.decode("utf-8", "replace")
            buffer += text
            # A \n terminates a real line; everything before the last \r WITHIN it is redraw history.
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                out = _last_redraw(line)
                if not out:
                    continue
                # Kept even when it is noise: the returned text has to be complete enough to parse
                # and to tail, and only the LOG is filtered.
                lines.append(out)
                if not _NOISE.search(out):
                    emit(out)
        # Trailing output with no final newline (a bar the child exited on).
        tail = _last_redraw(buffer)
        if tail:
            lines.append(tail)
            if not _NOISE.search(tail):
                emit(tail)
    finally:
        if proc.stdout:
            proc.stdout.close()
        proc.wait()
    return proc.returncode, "\n".join(lines)


def action_modality_keys(dataset_path: str) -> List[str]:
    """The dataset's own action group names, in declaration order.

    `eval_policy.py` defaults `--modality-keys` to `["right_arm"]`, which is a different robot's
    layout. Against an so100/so101 dataset — whose action groups are `single_arm` and `gripper` — that
    default fails deep inside the metric computation with:

        KeyError: 'action.right_arm'

    and it fails only AFTER the model is loaded and the first inference step has run, so it looks like
    an inference bug rather than a wrong argument.

    Reading the groups from the dataset's `meta/modality.json` makes the evaluation self-configuring
    for whatever robot the dataset describes, instead of correct for exactly one. Returns [] when the
    file is unreadable or declares no action groups, so the caller falls back to the upstream default
    rather than passing something invalid.
    """
    try:
        payload = json.loads(
            (Path(dataset_path) / "meta" / "modality.json").read_text(encoding="utf-8"))
    except Exception as e:  # nosec B110 - best effort; caller falls back to the upstream default
        logger.warning(f"Could not read the dataset's modality mapping ({e})")
        return []
    action = payload.get("action")
    if not isinstance(action, dict):
        return []
    return [str(k) for k in action]


def run_evaluation(
    config: Dict[str, Any],
    checkpoint_path: str,
    dataset_path: str,
    output_dir: str,
    hf_home: str,
    hf_token: Optional[str] = None,
) -> Dict[str, Any]:
    """Evaluate a checkpoint against a dataset. Returns the metrics dict that is also written to disk.

    Raises RuntimeError when the eval script fails, so the Batch job exits non-zero and the VAMS
    execution is recorded as FAILED rather than as a success with no metrics.
    """
    # The dataset must carry a modality mapping before LeRobotSingleDataset will load it.
    ensure_dataset_modality_file(dataset_path, checkpoint_path)

    env = os.environ.copy()
    env["HF_HOME"] = hf_home
    env["PYTHONPATH"] = GROOT_REPO_DIR
    if hf_token:
        env["HF_TOKEN"] = hf_token

    trajectories = str(config.get("evalTrajectories", 5))
    steps = str(config.get("evalSteps", 150))
    plot_dir = Path(output_dir) / "plots"
    plot_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python", "-u", EVAL_SCRIPT,
        "--model-path", checkpoint_path,
        "--dataset-path", dataset_path,
        "--data-config", str(config.get("dataConfig", "so100_dualcam")),
        "--embodiment-tag", str(config.get("embodimentTag", "new_embodiment")),
        "--video-backend", str(config.get("videoBackend", "torchvision_av")),
        "--trajs", trajectories,
        "--steps", steps,
        "--start-traj", str(config.get("evalStartTrajectory", 0)),
        # Without --model-path the script expects an inference SERVER on --host/--port; it is always
        # passed above, so the local-policy path is taken.
    ]
    # Plots are OFF by default because upstream's plot_trajectory() raises
    #   TypeError: 'Axes' object is not iterable
    # whenever a plotted group has a single dimension — plt.subplots(1) returns a bare Axes rather
    # than an array, and it iterates unconditionally. That happens AFTER the metric is computed and
    # printed, so the run had done all of its real work and still exited 1 with no metrics recorded.
    # The plots are a diagnostic extra; the MSE is the deliverable. Opt back in with plots: true once
    # the upstream helper handles the single-axis case.
    if str(config.get("plots", "")).strip().lower() in ("1", "true", "yes"):
        cmd += ["--plot", "--save_plot_path", str(plot_dir)]
    # An explicit template value wins; otherwise derive the keys from the dataset. Falling through to
    # the upstream default is wrong for any robot other than the one it was written for.
    modality_keys = config.get("evalModalityKeys")
    if modality_keys:
        keys: List[str] = (modality_keys if isinstance(modality_keys, list)
                           else [k.strip() for k in str(modality_keys).split(",") if k.strip()])
    else:
        keys = action_modality_keys(dataset_path)
        if keys:
            logger.info(f"Using the dataset's own action modality keys: {keys}")
    for key in keys:
        cmd += ["--modality-keys", key]

    # Recorded BEFORE the child loads the model: a kernel/driver kill leaves no traceback, so this is
    # the only place the run's actual GPU and RAM can be captured.
    log_hardware_context()

    logger.info("Running Gr00t evaluation:")
    logger.info(f"  Checkpoint: {checkpoint_path}")
    logger.info(f"  Dataset: {dataset_path}")
    logger.info(f"  Trajectories: {trajectories} | Steps: {steps}")
    logger.info(f"  Command: {' '.join(cmd)}")

    returncode, stdout_text = _run_streaming(cmd, env)
    if returncode != 0:
        # Echo the child's output before failing; otherwise the reason is invisible in CloudWatch.
        logger.error(f"Evaluation failed with exit code {returncode}")
        for line in diagnostic_tail(stdout_text):
            logger.error(f"  out: {line}")
        raise RuntimeError(f"Evaluation failed with exit code {returncode}")

    metrics = parse_mse(stdout_text)
    if metrics["averageMse"] is None:
        raise RuntimeError(
            "Evaluation produced no MSE metric. The upstream eval_policy.py output format may have "
            "changed; check the job log for its stdout.")

    metrics.update({
        "metric": "mse",
        "metricDescription": "Mean squared error between predicted and recorded actions.",
        "checkpointPath": checkpoint_path,
        "datasetPath": dataset_path,
        "trajectoriesEvaluated": len(metrics["perTrajectoryMse"]) or int(trajectories),
        "stepsPerTrajectory": int(steps),
        "dataConfig": config.get("dataConfig", "so100_dualcam"),
        "embodimentTag": config.get("embodimentTag", "new_embodiment"),
        # Load-bearing: when the eval dataset is the training dataset, this number reflects fit and
        # will look optimistically low. Recording the caveat next to the value keeps it from being
        # read as a generalization score later.
        "evaluationKind": config.get("evaluationKind", "sanity-check-on-training-data"),
        "caveat": (
            "Evaluated against the dataset the checkpoint was trained on unless a separate held-out "
            "dataset was supplied. Treat the MSE as a sanity check that the policy learned the "
            "demonstrated actions, not as a measure of generalization."
        ),
    })

    metrics_path = Path(output_dir) / "gr00tEvalMetrics.json"
    metrics_path.write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    logger.info(f"Wrote metrics to {metrics_path}: averageMse={metrics['averageMse']}")
    return metrics
