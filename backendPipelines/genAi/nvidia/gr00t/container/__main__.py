"""
VAMS Gr00t Fine-Tuning Container Wrapper

Orchestrates the full pipeline:
1. Load pipeline definition from sys.argv[1]
2. Ensure base model cached (EFS -> S3 -> HuggingFace)
3. Download asset files from S3 (excluding gr00tOutput_* folders)
4. Resolve config: the asset's own gr00t_config.json (1st) > the template's configuration body (2nd)
   > GROOT_* asset metadata (3rd) > defaults
5. Run fine-tuning via gr00t FinetuneWorkflow
6. Upload checkpoint outputs to S3

Container exits with code 0 on success or non-zero on failure.
SFN task callbacks handled by pipelineEnd Lambda.
"""

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple
from urllib.parse import unquote, urlparse

import manifest_io
from evaluation import run_evaluation
from inference import run_training
from model_manager import S3_HF_CACHE_PREFIX, ensure_models_cached, backup_cache_to_s3

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

INPUT_DIR = Path("/tmp/input")
OUTPUT_DIR = Path("/tmp/output")
# One EFS filesystem is mounted at one path by ALL FOUR Cosmos pipelines, so a cache directory
# that does not name the pipeline is shared by all of them -- and the cache check asks only
# whether the directory holds any weights at all. The first pipeline to run would populate it
# and every other one would read a hit, skip its own S3 restore, and download its weights during
# inference instead, while the backup uploaded the combined directory to each pipeline's own
# prefix. This lay dormant only because the mount never worked -- an unmounted path is an empty
# local directory, so the check was correctly a miss -- so fixing the mount is what activates it.
# The segment comes from the S3 prefix that already identifies this pipeline, so the filesystem
# layout and the backup layout cannot drift apart.
HF_CACHE_BASE = f"/mnt/efs/gr00t-models/hf_cache/{S3_HF_CACHE_PREFIX.split('/')[0]}"

# Default training config values
DEFAULTS = {
    "datasetPath": "dataset",
    "dataConfig": "so100_dualcam",
    "baseModelPath": "nvidia/GR00T-N1.5-3B",
    "maxSteps": 6000,
    "batchSize": 32,
    "learningRate": "1e-4",
    "weightDecay": "1e-5",
    "warmupRatio": "0.05",
    "saveSteps": 2000,
    "numGpus": 1,
    "loraRank": 0,
    "loraAlpha": 16,
    "loraDropout": "0.1",
    "tuneLlm": "false",
    "tuneVisual": "false",
    "tuneProjector": "true",
    "tuneDiffusionModel": "true",
    "embodimentTag": "new_embodiment",
    "videoBackend": "torchvision_av",
    # Evaluation-only. checkpointFolder empty means "the newest gr00tOutput_* folder on the asset",
    # which is what makes an evaluation step usable straight after a training step.
    "checkpointFolder": "",
    "evalTrajectories": 5,
    "evalSteps": 150,
    "evalStartTrajectory": 0,
}

# Container mode. finetune trains; evaluate scores an existing checkpoint. Set on the Batch job
# definition, so one image and one compute environment serve both pipelines.
MODE_FINETUNE = "finetune"
MODE_EVALUATE = "evaluate"
CHECKPOINT_DIR = Path("/tmp/checkpoint")


def load_pipeline_definition() -> Dict:
    """Load pipeline definition from command line argument or environment variable."""
    definition_source = None

    if len(sys.argv) > 1:
        definition_source = sys.argv[1]
    elif "PIPELINE_DEFINITION" in os.environ:
        definition_source = os.environ["PIPELINE_DEFINITION"]
    else:
        raise ValueError("No pipeline definition provided via command line or PIPELINE_DEFINITION env var")

    logger.info(f"Loading pipeline definition from: {definition_source[:100]}...")

    try:
        return json.loads(definition_source)
    except json.JSONDecodeError:
        pass

    try:
        definition_path = Path(definition_source)
        if definition_path.exists():
            with open(definition_path, "r") as f:
                return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to load as file: {e}")

    raise ValueError(f"Could not parse pipeline definition as JSON or load from file: {definition_source}")


def parse_s3_uri(s3_uri: str) -> Tuple[str, str]:
    """Parse S3 URI into bucket and key."""
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    return bucket, key


def resolve_checkpoint_folder(s3_asset_path: str, requested: str) -> str:
    """The gr00tOutput_* folder to evaluate. An explicit name wins; otherwise the NEWEST one is used.

    Newest-by-default is what lets an evaluation step follow a training step without the operator
    copying a folder name across: training names its output with a UTC timestamp
    (gr00tOutput_{model}_trainingjob_{YYYYmmddTHHMMSS}_{job}), so lexical ordering is chronological.
    """
    if requested:
        return requested.strip().strip("/")

    bucket, prefix = parse_s3_uri(s3_asset_path)
    prefix = prefix.rstrip("/") + "/" if prefix else ""
    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3api", "list-objects-v2", "--bucket", bucket, "--prefix", prefix,
         "--delimiter", "/", "--query", "CommonPrefixes[].Prefix", "--output", "json"],
        capture_output=True, text=True,
    )  # nosemgrep: dangerous-subprocess-use-audit
    if result.returncode != 0:
        raise RuntimeError(f"Could not list asset folders to find a checkpoint: {result.stderr}")

    try:
        prefixes = json.loads(result.stdout or "[]") or []
    except json.JSONDecodeError:
        prefixes = []
    folders = sorted(
        p[len(prefix):].rstrip("/") for p in prefixes
        if p[len(prefix):].startswith("gr00tOutput_")
    )
    if not folders:
        raise ValueError(
            "No gr00tOutput_* folder found on the asset. Run the fine-tuning pipeline first, or set "
            "checkpointFolder to the checkpoint you want evaluated.")
    logger.info(f"Checkpoint folders found: {folders}")
    return folders[-1]


def download_checkpoint_from_s3(s3_asset_path: str, checkpoint_folder: str, local_dir: Path) -> str:
    """Download ONE gr00tOutput_* folder. Deliberately separate from the asset sync, which excludes
    these folders so a training run does not re-download every previous checkpoint.

    Prefers the folder ROOT (where training writes the final model) over its checkpoint-N
    subdirectories, which are intermediate saves.
    """
    local_dir.mkdir(parents=True, exist_ok=True)
    source = f"{s3_asset_path.rstrip('/')}/{checkpoint_folder}/"
    logger.info(f"Downloading checkpoint: {source} -> {local_dir}")

    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "sync", source, str(local_dir)],
        capture_output=True, text=True,
    )  # nosemgrep: dangerous-subprocess-use-audit
    if result.returncode != 0:
        raise RuntimeError(f"Checkpoint download failed: {result.stderr}")

    # The final model sits at the folder root; fall back to the highest-numbered checkpoint-N when a
    # run was interrupted before the final save.
    if (local_dir / "config.json").exists():
        return str(local_dir)
    numbered = sorted(
        (d for d in local_dir.glob("checkpoint-*") if d.is_dir()),
        key=lambda d: int(d.name.split("-")[-1]) if d.name.split("-")[-1].isdigit() else -1,
    )
    if numbered:
        logger.info(f"No model at the checkpoint root; using {numbered[-1].name}")
        return str(numbered[-1])
    raise ValueError(
        f"Downloaded checkpoint folder '{checkpoint_folder}' contains no model (no config.json at its "
        "root and no checkpoint-N subdirectory).")


def download_asset_from_s3(s3_asset_path: str, local_dir: Path) -> None:
    """
    Download asset files from S3, excluding previous gr00tOutput_* folders.
    Only excludes locally -- never deletes from S3.
    """
    local_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"Downloading asset from S3: {s3_asset_path} -> {local_dir}")
    logger.info("Excluding gr00tOutput_* folders from download")

    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        [
            "aws", "s3", "sync",
            s3_asset_path, str(local_dir),
            "--exclude", "gr00tOutput_*/*",
            "--exclude", "gr00tOutput_*",
        ],
        capture_output=True, text=True
    )  # nosemgrep: dangerous-subprocess-use-audit

    if result.returncode != 0:
        raise RuntimeError(f"S3 asset download failed: {result.stderr}")

    logger.info(f"Asset downloaded to {local_dir}")


def upload_output_to_s3(local_dir: Path, s3_output_path: str, output_folder_name: str) -> str:
    """Upload training output folder to S3."""
    s3_dest = f"{s3_output_path.rstrip('/')}/{output_folder_name}/"

    logger.info(f"Uploading output to S3: {local_dir} -> {s3_dest}")

    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "sync", str(local_dir), s3_dest],
        capture_output=True, text=True
    )  # nosemgrep: dangerous-subprocess-use-audit

    if result.returncode != 0:
        raise RuntimeError(f"S3 output upload failed: {result.stderr}")

    logger.info(f"Output uploaded to {s3_dest}")
    return s3_dest


def log_free_space(path: Path) -> Optional[int]:
    """Log (and return) the free space in MiB on the filesystem holding `path`; None when unreadable.

    Checkpoints accumulate on the container's own volume while training runs, and a container the
    kernel kills for filling it leaves NO Python-level error: the process is gone, so neither a
    traceback nor main()'s failure handler runs. Recording the number as it changes is what turns an
    out-of-disk kill into a one-line diagnosis. Never raises -- this is diagnostics.
    """
    try:
        free_mib = shutil.disk_usage(str(path)).free // (1024 * 1024)
    except Exception as e:
        logger.warning(f"Could not read free space for {path} (non-fatal): {e}")
        return None
    logger.info(f"Free space on the volume holding {path}: {free_mib} MiB")
    return free_mib


# How often the output folder is synced to S3 while training runs. Training writes every checkpoint to
# a container-local directory, so a single upload after it returns means an attempt that dies partway
# -- the 8-hour Batch attempt timeout, an instance failure -- loses every hour of GPU work it had
# already saved. The sync goes to the SAME destination the final upload uses, so whatever reached S3
# before the interruption stays there; `aws s3 sync` transfers only what changed, so repeating it costs
# a listing.
#
# WHERE THAT IS, precisely, because it is NOT where a later run looks for a checkpoint:
# outputS3AssetFilesPath is the execution's own STAGING prefix
# (pipelines/{pipelineName}/{jobName}/output/{executionId}/files/, executionRecords
# .pipeline_output_prefixes), which the workflow's process-output step promotes onto the asset when the
# run SUCCEEDS. resolve_checkpoint_folder and download_checkpoint_from_s3 read the ASSET prefix
# (inputS3AssetPath), and a failed or timed-out attempt never reaches process-output, so an interrupted
# run's checkpoints are durable in S3 but invisible to a following evaluation run: recovering them is a
# copy from the staging prefix onto the asset. __enter__ logs the exact URI for that reason.
CHECKPOINT_UPLOAD_INTERVAL_SECONDS = 300
# How long the final upload waits for a sync already in flight. Bounded rather than unbounded: a
# stalled sync must not hold the container past the work it has already done.
CHECKPOINT_UPLOAD_JOIN_SECONDS = 600


class PeriodicOutputUpload:
    """Sync the output folder to S3 on an interval for the duration of a `with` block.

    Best-effort by design. A cycle that fails is logged and the next one retries, because the FINAL
    upload after training is what decides the run's outcome (it raises), and failing the run here would
    discard hours of GPU work for a transient S3 error. What it buys is recoverability: whatever
    reached S3 before an interruption stays there.

    A cycle can copy a checkpoint the trainer is still writing, so an interrupted run's newest
    checkpoint may be incomplete -- the final sync re-transfers anything whose size or timestamp moved,
    so a run that finishes is consistent either way. The older checkpoints, which are the ones worth
    recovering, are complete by the time a later cycle sees them.

    The sync runs on its own thread rather than between output lines of the training subprocess: an
    upload of a multi-GB checkpoint takes minutes, and a reader that stops draining the child's pipe
    for minutes leaves the child blocked on write() for that long -- pausing training for every upload.
    """

    def __init__(self, s3_output_path: str, output_folder_name: str,
                 interval_seconds: int = None):
        self._s3_output_path = s3_output_path
        self._output_folder_name = output_folder_name
        self._interval = (CHECKPOINT_UPLOAD_INTERVAL_SECONDS if interval_seconds is None
                          else interval_seconds)
        self._stop = threading.Event()
        self._thread = None
        self.cycles_uploaded = 0
        self.cycles_failed = 0

    def __enter__(self):
        logger.info(
            f"Syncing intermediate output to S3 every {self._interval}s while training runs")
        # The one line an operator needs after an interrupted attempt. It names the staging URI the
        # checkpoints are recoverable FROM, which is not the asset prefix a following evaluation run
        # searches, so without it recovery starts by working out where the bytes went.
        logger.info(
            "Intermediate checkpoints are recoverable from "
            f"{self._s3_output_path.rstrip('/')}/{self._output_folder_name}/ -- this is the "
            "execution's staging prefix, which is promoted onto the asset only when the run "
            "succeeds. After an interrupted attempt, copy that folder onto the asset to evaluate or "
            "resume from it.")
        self._thread = threading.Thread(
            target=self._run, name="gr00t-output-upload", daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback_obj):
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=CHECKPOINT_UPLOAD_JOIN_SECONDS)
            if self._thread.is_alive():
                logger.warning(
                    "An intermediate output sync is still running; the final upload proceeds anyway.")
        logger.info(
            f"Intermediate output syncs: {self.cycles_uploaded} succeeded, {self.cycles_failed} failed")
        return False

    def _run(self):
        # Waits BEFORE the first cycle: a run that finishes inside one interval has nothing to recover
        # and the final upload covers it.
        while not self._stop.wait(self._interval):
            self.sync_once()

    def sync_once(self):
        """One incremental sync of the output folder. Records the outcome; never raises."""
        log_free_space(OUTPUT_DIR)
        try:
            upload_output_to_s3(OUTPUT_DIR, self._s3_output_path, self._output_folder_name)
            self.cycles_uploaded += 1
        except Exception as e:
            self.cycles_failed += 1
            logger.warning(f"Intermediate output sync failed (non-fatal, will retry): {e}")


# The HuggingFace owners a base model may be pulled from. baseModelPath reaches from_pretrained, which
# downloads the named repository into the shared EFS HuggingFace cache that every later run restores
# from, so the owner set is the trust boundary: NVIDIA's own GR00T releases. A deployment with its own
# mirror or internal base adds owners through GR00T_ADDITIONAL_BASE_MODEL_OWNERS (comma-separated); the
# list is additive, so 'nvidia' and locally available models are always usable.
#
# Twin of the same four names in ../lambda/vamsExecuteGr00tFinetunePipeline.py, which validates the
# sources it can see: asset metadata and the execute-time input configuration. The check is repeated
# here because resolve_config merges the asset's own gr00t_config.json OVER that value, making this
# container the point of use. The image carries only the files this directory's Dockerfile COPYs, so the
# lambda module is not importable here -- the two copies are kept identical by hand and change together.
ALLOWED_BASE_MODEL_OWNERS = ("nvidia",)
ADDITIONAL_BASE_MODEL_OWNERS_ENV = "GR00T_ADDITIONAL_BASE_MODEL_OWNERS"

# One segment of a repository id or of a local model path, matched in full: '..', an empty segment,
# whitespace and URL/shell punctuation are all outside it, so a value cannot traverse out of the path
# it names.
_MODEL_PATH_SEGMENT = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")


def allowed_base_model_owners():
    """The allowlisted HuggingFace owners, lowercased, including any the deployment adds by
    environment. Malformed entries are ignored rather than widening the list to an unusable value."""
    owners = [owner.lower() for owner in ALLOWED_BASE_MODEL_OWNERS]
    for entry in os.environ.get(ADDITIONAL_BASE_MODEL_OWNERS_ENV, "").split(","):
        owner = entry.strip().lower()
        if owner and _MODEL_PATH_SEGMENT.fullmatch(owner) and owner not in owners:
            owners.append(owner)
    return tuple(owners)


def validate_base_model_path(base_model_path):
    """The base model this run may load, normalized; "" when the run names none.

    Two shapes are usable. An ``owner/name`` HuggingFace repository id is fetched from HuggingFace,
    so its owner must be allowlisted. An absolute path is read from the container's own filesystem —
    the EFS cache, or a checkpoint a previous run wrote — and needs no allowlist, but every segment
    must be a plain name so the value cannot traverse elsewhere. Anything else raises: the value also
    names the output folder the run writes, and an unallowlisted repository would be downloaded into
    the shared cache.
    """
    value = "" if base_model_path is None else str(base_model_path).strip()
    if not value:
        return ""

    if value.startswith("/"):
        segments = value.rstrip("/").split("/")[1:]
        if segments and all(_MODEL_PATH_SEGMENT.fullmatch(segment) for segment in segments):
            return value
        raise Exception(
            f"Gr00t baseModelPath '{value}' is not a usable local model path. An absolute path may "
            "not contain empty or relative segments.")

    owners = allowed_base_model_owners()
    segments = value.split("/")
    if (len(segments) == 2
            and all(_MODEL_PATH_SEGMENT.fullmatch(segment) for segment in segments)
            and segments[0].lower() in owners):
        return value

    raise Exception(
        f"Gr00t baseModelPath '{value}' is not an allowed base model. Supply a HuggingFace "
        f"repository owned by one of: {', '.join(owners)} (for example 'nvidia/GR00T-N1.5-3B'), or "
        "an absolute path to a model already available to the container.")


class AssetConfigurationError(RuntimeError):
    """Raised when the asset's gr00t_config.json exists but cannot be read as a JSON object."""


def load_asset_config_file(config_file: Path) -> Dict:
    """The asset's own gr00t_config.json, parsed. ``{}`` for an empty file.

    Raises ``AssetConfigurationError`` when the file exists but is unreadable, is not valid JSON, or is
    not a JSON object. This is the HIGHEST-priority configuration source, so tolerating a parse failure
    runs the job on the lower-priority values -- a trailing comma trains 6000 default steps instead of
    the 20000 the file asked for, writes a checkpoint, and records the execution as a success with none
    of the requested parameters applied. Same reasoning, and the same shape, as
    ``manifest_io.fetch_input_configuration``.
    """
    try:
        body = config_file.read_text(encoding="utf-8")
    except OSError as e:
        raise AssetConfigurationError(f"Could not read {config_file}: {e}")
    if not body.strip():
        logger.info(f"{config_file} is empty; no overrides applied")
        return {}
    try:
        parsed = json.loads(body)
    except ValueError as e:
        raise AssetConfigurationError(f"{config_file} is not valid JSON: {e}")
    if not isinstance(parsed, dict):
        raise AssetConfigurationError(
            f"{config_file} is not a JSON object (found {type(parsed).__name__})")
    return parsed


def _reject_unusable_relative_path(text: str, field_name: str) -> None:
    """Raise on a value that cannot name a folder inside the asset. Message-quality only -- the
    containment check below is what actually decides."""
    if "\x00" in text:
        raise ValueError(f"{field_name} contains a NUL byte.")
    if "://" in text:
        raise ValueError(
            f"{field_name} '{text}' is a URI. It must name a folder within the asset.")
    if "\\" in text:
        raise ValueError(
            f"{field_name} '{text}' contains a backslash. Separate folders with '/'.")
    if text.startswith("/"):
        raise ValueError(
            f"{field_name} '{text}' is an absolute path. It must name a folder within the asset.")


def resolve_asset_relative_path(base_dir: Path, value, field_name: str) -> Path:
    """`value` resolved beneath `base_dir`, or raise.

    Joining a caller-supplied path onto a base directory does not confine it: an absolute value
    REPLACES the base (``PurePosixPath('/tmp/input') / '/mnt/efs/x'`` is ``/mnt/efs/x``) and ``..``
    segments are not normalized away. Every source of this value -- GROOT_* asset metadata, the
    template's configuration body, the asset's own gr00t_config.json -- is writable by anyone who can
    edit the asset, and the resolved path is both read (training, evaluation) and WRITTEN (evaluation's
    modality repair) while the shared EFS model cache is mounted read-write, so existence is not
    enough: it has to be inside the per-job download directory.

    The percent-decoded form is held to the same test, so an encoded traversal is judged on what it
    denotes; the path returned is the one the asset actually names, so a folder whose name contains a
    '%' is not rewritten.
    """
    text = "" if value is None else str(value).strip()
    if not text:
        raise ValueError(
            f"{field_name} is empty. Name the folder within the asset that holds it.")

    base = Path(os.path.realpath(str(base_dir)))
    for candidate in dict.fromkeys((text, unquote(text))):
        _reject_unusable_relative_path(candidate, field_name)
        resolved = Path(os.path.realpath(str(base / candidate)))
        if resolved != base and base not in resolved.parents:
            raise ValueError(
                f"{field_name} '{text}' resolves to {resolved}, outside the asset directory {base}. "
                "It must name a folder within the asset.")
    return Path(os.path.realpath(str(base / text)))


def resolve_config(definition: Dict, asset_dir: Path) -> Dict:
    """
    Resolve training config using 3-tier priority:
    1. gr00t_config.json in the asset (highest)
    2. The merged gr00tConfig from the Lambda -- the template's configuration body over GROOT_* asset
       metadata, in that order, so the value the operator supplied on the execute screen wins over a
       standing value saved on the asset
    3. Defaults (lowest)

    Returns merged config dict. Raises when gr00t_config.json cannot be read as a JSON object, and when
    the merged baseModelPath is not an allowed base model.
    """
    config = dict(DEFAULTS)

    # Apply gr00tConfig from Lambda (already merged: inputParameters < asset metadata)
    groot_config_str = definition.get("gr00tConfig", "{}")
    if groot_config_str:
        try:
            lambda_config = json.loads(groot_config_str) if isinstance(groot_config_str, str) else groot_config_str
            for key, value in lambda_config.items():
                if value is not None and value != "":
                    config[key] = value
            logger.info(f"Applied Lambda gr00tConfig overrides: {list(lambda_config.keys())}")
        except Exception as e:
            logger.warning(f"Failed to parse gr00tConfig from Lambda: {e}")

    # Check for gr00t_config.json in asset (1st priority -- overrides everything)
    config_file = asset_dir / "gr00t_config.json"
    if config_file.exists():
        file_config = load_asset_config_file(config_file)
        for key, value in file_config.items():
            # An empty value means the field was left unset, which is how every other source reads it:
            # the Lambda skips a blank input-configuration field and a blank GROOT_* metadata value. A
            # blank here replacing a real one is how "datasetPath": "" collapsed the dataset path onto
            # the asset root.
            if value is not None and value != "":
                config[key] = value
        logger.info(f"Applied gr00t_config.json overrides: {list(file_config.keys())}")

    # The MERGED value is the one this container loads, whichever source supplied it, so the allowlist
    # is applied here at the point of use -- AFTER the gr00t_config.json merge above and OUTSIDE both
    # parse handlers, inside which a rejection would be logged as a warning and the run would continue
    # on the rejected value. A blank value leaves the container on its own default rather than handing
    # from_pretrained an empty path.
    config["baseModelPath"] = (validate_base_model_path(config.get("baseModelPath"))
                               or DEFAULTS["baseModelPath"])
    logger.info(f"Gr00t base model: {config['baseModelPath']}")

    return config


def find_latest_checkpoint(output_dir: Path) -> Optional[Path]:
    """Find the latest checkpoint directory in the output."""
    checkpoints = sorted(output_dir.glob("checkpoint-*"), key=lambda p: p.name)
    if checkpoints:
        return checkpoints[-1]
    return None


def main():
    """Main pipeline execution."""
    start_time = time.time()

    try:
        logger.info("=" * 80)
        logger.info("VAMS Gr00t Fine-Tuning Pipeline Starting")
        logger.info("=" * 80)

        definition = load_pipeline_definition()
        logger.info(f"Pipeline definition loaded: {json.dumps(definition, indent=2)}")

        # Extract required fields
        input_s3_asset_path = definition.get("inputS3AssetPath")
        output_s3_asset_files_path = definition.get("outputS3AssetFilesPath")
        asset_id = definition.get("assetId")

        # Get environment variables
        hf_token = os.environ.get("HF_TOKEN")
        s3_model_bucket = os.environ.get("S3_MODEL_BUCKET")
        batch_job_id = os.environ.get("AWS_BATCH_JOB_ID", "unknown")
        # Which job this image is running. Carried on the pipeline DEFINITION (which constructPipeline
        # passes as the container argv) rather than on the Batch job definition, so training and
        # evaluation share one job definition, one queue, and one state machine — the env var is kept
        # as an override for running the image by hand. Defaults to finetune, so an older definition
        # with no mode behaves exactly as before.
        mode = (definition.get("mode") or os.environ.get("GROOT_MODE")
                or MODE_FINETUNE)
        mode = str(mode).strip().lower()
        if mode not in (MODE_FINETUNE, MODE_EVALUATE):
            raise ValueError(
                f"GROOT_MODE must be '{MODE_FINETUNE}' or '{MODE_EVALUATE}', got '{mode}'.")
        logger.info(f"Container mode: {mode}")

        # Set HF_HOME for native HuggingFace cache on EFS
        hf_home = HF_CACHE_BASE
        os.environ["HF_HOME"] = hf_home

        if hf_token:
            os.environ["HF_TOKEN"] = hf_token

        # Check for model invalidation flag (input configuration read from S3)
        invalidate_models = False
        try:
            params = manifest_io.fetch_input_configuration(definition.get("inputConfigurationS3Location", ""))
            invalidate_models = str(params.get("INVALIDATE_GROOT_MODELS", "")).lower() == "true"
            if invalidate_models:
                logger.info("INVALIDATE_GROOT_MODELS=true: will clear EFS/S3 cache")
        # A configuration that EXISTS but cannot be parsed is not something to tolerate: the
        # broad handler below would leave the run on its defaults and still report success,
        # with every caller-supplied parameter silently dropped. Placed ABOVE that handler --
        # below it this arm would be dead code.
        except manifest_io.InputConfigurationError:
            raise
        except Exception:
            pass

        if not s3_model_bucket:
            raise ValueError("S3_MODEL_BUCKET environment variable is required")
        if not input_s3_asset_path:
            raise ValueError("inputS3AssetPath is required in pipeline definition")
        if not asset_id:
            raise ValueError("assetId is required in pipeline definition")
        if not output_s3_asset_files_path:
            raise ValueError("outputS3AssetFilesPath is required in pipeline definition")

        logger.info(f"Asset path: {input_s3_asset_path}")
        logger.info(f"Output path: {output_s3_asset_files_path}")
        logger.info(f"Asset ID: {asset_id}")
        logger.info(f"Batch Job ID: {batch_job_id}")
        logger.info(f"HF_HOME: {hf_home}")

        # Step 1: Ensure base model cached
        logger.info("=" * 80)
        logger.info("Step 1: Ensuring base model is cached")
        logger.info("=" * 80)

        ensure_models_cached(
            hf_home=hf_home,
            s3_bucket=s3_model_bucket,
            invalidate=invalidate_models
        )

        # Step 2: Download asset files from S3
        logger.info("=" * 80)
        logger.info("Step 2: Downloading asset files from S3")
        logger.info("=" * 80)

        download_asset_from_s3(input_s3_asset_path, INPUT_DIR)

        # Step 3: Resolve training config (3-tier priority)
        logger.info("=" * 80)
        logger.info("Step 3: Resolving training configuration")
        logger.info("=" * 80)

        config = resolve_config(definition, INPUT_DIR)
        logger.info(f"Resolved config: {json.dumps(config, indent=2)}")

        # Resolve dataset path, confined to the asset the run was given
        dataset_dir = resolve_asset_relative_path(INPUT_DIR, config["datasetPath"], "datasetPath")
        dataset_path = str(dataset_dir)
        if not dataset_dir.exists():
            raise ValueError(f"Dataset directory not found at {dataset_path}. "
                           f"Expected LeRobot dataset at '{config['datasetPath']}' within asset.")

        logger.info(f"Dataset path resolved: {dataset_path}")

        # Extract short model name from path (e.g., "nvidia/GR00T-N1.5-3B" -> "N1.5-3B")
        model_path = config.get("baseModelPath", "nvidia/GR00T-N1.5-3B")
        model_short = model_path.split("/")[-1].replace("GR00T-", "") if "/" in model_path else model_path
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        job_id_short = batch_job_id.split("-")[0] if "-" in batch_job_id else batch_job_id[:8]

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

        if mode == MODE_EVALUATE:
            # Step 4: score an existing checkpoint. The asset sync above excluded gr00tOutput_*, so the
            # checkpoint is fetched separately -- that exclusion exists to stop a TRAINING run
            # re-downloading every past checkpoint, and evaluation should not regress it.
            logger.info("=" * 80)
            logger.info("Step 4: Running evaluation")
            logger.info("=" * 80)

            checkpoint_folder = resolve_checkpoint_folder(
                input_s3_asset_path, str(config.get("checkpointFolder", "") or ""))
            logger.info(f"Evaluating checkpoint folder: {checkpoint_folder}")
            checkpoint_path = download_checkpoint_from_s3(
                input_s3_asset_path, checkpoint_folder, CHECKPOINT_DIR)

            metrics = run_evaluation(
                config=config,
                checkpoint_path=checkpoint_path,
                dataset_path=dataset_path,
                output_dir=str(OUTPUT_DIR),
                hf_home=hf_home,
                hf_token=hf_token,
            )
            logger.info(f"Evaluation average MSE: {metrics.get('averageMse')}")
            output_folder_name = (
                f"gr00tEval_{model_short}_evaljob_{timestamp}_{job_id_short}")
        else:
            # Step 4: Run fine-tuning
            logger.info("=" * 80)
            logger.info("Step 4: Running fine-tuning")
            logger.info("=" * 80)

            # Named before training so each checkpoint can be synced to its final destination as it is
            # written, rather than only after the whole run returns.
            output_folder_name = (
                f"gr00tOutput_{model_short}_trainingjob_{timestamp}_{job_id_short}")
            log_free_space(OUTPUT_DIR)
            with PeriodicOutputUpload(output_s3_asset_files_path, output_folder_name):
                run_training(
                    config=config,
                    dataset_path=dataset_path,
                    output_dir=str(OUTPUT_DIR),
                    hf_home=hf_home,
                    hf_token=hf_token,
                )

        # Step 5: Upload the output
        logger.info("=" * 80)
        logger.info("Step 5: Uploading output to S3")
        logger.info("=" * 80)
        logger.info(f"Output folder name: {output_folder_name}")

        # A sync of an empty directory succeeds, so without this check a run whose work produced
        # nothing uploads nothing and is still recorded as a success.
        if not any(path.is_file() for path in OUTPUT_DIR.rglob("*")):
            raise RuntimeError(
                f"The {mode} step produced no files in {OUTPUT_DIR}, so there is nothing to upload.")

        s3_dest = upload_output_to_s3(OUTPUT_DIR, output_s3_asset_files_path, output_folder_name)

        # Step 6: Backup HF cache to S3 (non-fatal)
        logger.info("=" * 80)
        logger.info("Step 6: Backing up HF cache to S3")
        logger.info("=" * 80)
        try:
            backup_cache_to_s3(hf_home=hf_home, s3_bucket=s3_model_bucket)
        except Exception as backup_err:
            logger.warning(f"Failed to backup HF cache to S3 (non-fatal): {backup_err}")

        elapsed_time = time.time() - start_time

        logger.info("=" * 80)
        logger.info(f"Pipeline completed successfully in {elapsed_time:.1f}s")
        logger.info(f"Output: {s3_dest}")
        logger.info("=" * 80)

    except Exception as e:
        logger.error("=" * 80)
        logger.error("Pipeline failed with error:")
        logger.error(str(e))
        logger.error("=" * 80)
        import traceback
        logger.error(traceback.format_exc())
        sys.stdout.flush()
        sys.stderr.flush()
        raise


if __name__ == "__main__":
    main()
