"""
VAMS Cosmos 3 (omni) Container Wrapper

Orchestrates: load definition -> ensure models cached (HF_HOME on EFS + S3
backup) -> download input file (image2video) -> run inference via
cosmos-framework -> upload outputs to S3.

Container handles inference and S3 I/O only. SFN task callbacks are handled by
the pipelineEnd Lambda. Container exits 0 on success, non-zero on failure.
"""

import json
import logging
import math
import os
import re
import sys
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Set, Tuple
from urllib.parse import urlparse

from inference import generate_preview_gif, run_inference
from model_manager import S3_HF_CACHE_PREFIX, ensure_models_cached
import manifest_io

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

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
HF_CACHE_BASE = f"/mnt/efs/cosmos-models/hf_cache/{S3_HF_CACHE_PREFIX.split('/')[0]}"

# Task modes that consume an input file
INPUT_FILE_MODES = ("image2video", "video2video", "transfer")
# Task modes that emit a still image (not video)
IMAGE_OUTPUT_MODES = ("text2image",)
# Variants that can perform control-signal transfer (general-purpose omni models)
TRANSFER_CAPABLE_VARIANTS = ("nano", "super")
# Supported control-signal types for transfer
TRANSFER_CONTROL_TYPES = ("edge", "blur", "depth", "seg", "wsm")

# Buckets a control-signal path is allowed to name, as a comma-separated list of bucket names set on
# the Batch job definition. It carries the deployment's own asset buckets.
ALLOWED_INPUT_BUCKETS_ENV = "ALLOWED_INPUT_BUCKETS"
# Pipeline-definition keys whose values are S3 locations VAMS itself chose for this run.
RUN_S3_LOCATION_KEYS = (
    "inputS3AssetFilePath",
    "outputS3AssetFilesPath",
    "outputS3AssetPreviewPath",
    "outputS3AssetMetadataPath",
    "inputOutputS3AssetAuxiliaryFilesPath",
)
# Characters an output file name may keep when it is derived from a framework artifact's own path.
OUTPUT_NAME_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def parse_number_setting(raw, setting_name, default, integer=False, minimum=None):
    """Coerce one caller-supplied numeric setting, or raise ValueError naming the setting.

    A setting arrives as whatever its source carried: a typed template tag as a JSON number, an
    asset-metadata value as a string. Blank and absent both mean "not supplied" and yield the
    default; every other value must be a finite number. A boolean is rejected rather than read as
    1/0, and a fractional value for an integer setting is rejected rather than truncated -- a request
    for 3.9 frames silently generating 3 is what this exists to prevent.
    """
    if raw is None or (not isinstance(raw, bool) and str(raw).strip() == ""):
        return default
    if isinstance(raw, bool):
        raise ValueError(f"{setting_name} must be a number, but the boolean {raw} was supplied")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        raise ValueError(f"{setting_name} must be a number, but {raw!r} was supplied")
    if not math.isfinite(value):
        raise ValueError(f"{setting_name} must be a finite number, but {raw!r} was supplied")
    if integer:
        if not value.is_integer():
            raise ValueError(f"{setting_name} must be a whole number, but {raw!r} was supplied")
        value = int(value)
    if minimum is not None and value < minimum:
        raise ValueError(f"{setting_name} must be at least {minimum}, but {raw!r} was supplied")
    return value


def allowed_control_buckets(definition: Dict) -> Set[str]:
    """The S3 buckets a control-signal path may name.

    ALLOWED_INPUT_BUCKETS carries the deployment's own asset buckets. The buckets this run's own
    input and output locations name are added unconditionally: VAMS chose those, so they belong to
    the deployment by construction, and including them leaves the check effective on a job
    definition that does not set the variable.
    """
    buckets = set()
    for name in os.environ.get(ALLOWED_INPUT_BUCKETS_ENV, "").split(","):
        if name.strip():
            buckets.add(name.strip())
    for key in RUN_S3_LOCATION_KEYS:
        value = definition.get(key) or ""
        if isinstance(value, str) and value.startswith("s3://"):
            bucket = value[len("s3://"):].partition("/")[0]
            if bucket:
                buckets.add(bucket)
    return buckets


def validate_control_s3_uri(s3_uri, allowed_buckets, setting_name="COSMOS3_CONTROL_PATH") -> str:
    """Return the control-signal S3 URI unchanged, or raise ValueError explaining the rejection.

    The value is a complete s3://bucket/key URI supplied by whoever authored the execution or the
    asset's metadata, and it reaches `aws s3 cp` under the Batch job role -- which can read every
    asset bucket in the deployment. Restricting the bucket to the deployment's own is what keeps a
    metadata author from naming an unrelated bucket and having its object pulled into the run.
    """
    value = (s3_uri or "").strip()
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in value):
        raise ValueError(f"{setting_name} must not contain control characters: {s3_uri!r}")
    if not value.startswith("s3://"):
        raise ValueError(
            f"{setting_name} must be a complete S3 URI of the form s3://bucket/key, "
            f"but {s3_uri!r} was supplied")
    remainder = value[len("s3://"):]
    if "?" in remainder or "#" in remainder:
        # urlparse splits a query or fragment off the key while `aws s3 cp` keeps it, so the object
        # checked here would not be the object downloaded.
        raise ValueError(f"{setting_name} must not contain '?' or '#': {s3_uri!r}")
    bucket, _, key = remainder.partition("/")
    if not bucket or not key or key.endswith("/"):
        raise ValueError(
            f"{setting_name} must name an object, not a bucket or a prefix: {s3_uri!r}")
    # A leading or doubled slash is a real difference: urlparse strips it while `aws s3 cp` keeps it,
    # so the two would address different objects.
    if key.startswith("/") or "//" in key or any(part in (".", "..") for part in key.split("/")):
        raise ValueError(f"{setting_name} contains an ambiguous key path: {s3_uri!r}")
    if bucket not in allowed_buckets:
        raise ValueError(
            f"{setting_name} names bucket '{bucket}', which is not one of this deployment's "
            f"buckets ({', '.join(sorted(allowed_buckets)) or 'none resolved'})")
    return value


def build_control_blocks(control_types_raw, control_paths_raw, control_weights_raw, allowed_buckets):
    """Build the per-control-type block mapping for control-signal transfer.

    Each of the raw inputs is a comma-separated string (multi-control), aligned
    by position. control_paths entries may be blank (auto-compute from source).
    Returns ``(blocks, control_sources)``: blocks is {control_type: {"weight": float}} as the
    framework consumes it, and control_sources maps a control type to the validated s3:// URI of its
    pre-computed control video, for the types that named one.
    """
    types = [t.strip().lower() for t in str(control_types_raw or "").split(",") if t.strip()]
    if not types:
        types = ["edge"]
    paths = [p.strip() for p in str(control_paths_raw or "").split(",")]
    weights = [w.strip() for w in str(control_weights_raw or "").split(",")]

    blocks = {}
    control_sources = {}
    for i, ctype in enumerate(types):
        if ctype not in TRANSFER_CONTROL_TYPES:
            logger.warning(f"Unknown control type '{ctype}' ignored (supported: {TRANSFER_CONTROL_TYPES})")
            continue
        # Weight: the aligned entry when present and non-blank, else the default 1.0. A weight that
        # is not a number is rejected rather than quietly becoming 1.0 -- the run would otherwise
        # apply a conditioning strength nobody asked for and report success.
        blocks[ctype] = {
            "weight": parse_number_setting(
                weights[i] if i < len(weights) else "",
                f"COSMOS3_CONTROL_WEIGHT ({ctype})", 1.0),
        }
        path = paths[i] if i < len(paths) else ""
        if path:
            control_sources[ctype] = validate_control_s3_uri(path, allowed_buckets)
    return blocks, control_sources


def load_pipeline_definition() -> Dict:
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
    definition_path = Path(definition_source)
    if definition_path.exists():
        with open(definition_path, "r") as f:
            return json.load(f)
    raise ValueError(f"Could not parse pipeline definition: {definition_source}")


def parse_s3_uri(s3_uri: str) -> Tuple[str, str]:
    parsed = urlparse(s3_uri)
    return parsed.netloc, parsed.path.lstrip("/")


def download_from_s3(s3_uri: str, local_path: Path) -> None:
    local_path.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Downloading from S3: {s3_uri} -> {local_path}")
    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "cp", s3_uri, str(local_path)], capture_output=True, text=True
    )  # nosemgrep: dangerous-subprocess-use-audit
    if result.returncode != 0:
        raise RuntimeError(f"S3 download failed: {result.stderr}")


def upload_to_s3(local_path: Path, s3_uri: str) -> None:
    logger.info(f"Uploading to S3: {local_path} -> {s3_uri}")
    result = subprocess.run(  # nosemgrep: dangerous-subprocess-use-audit
        ["aws", "s3", "cp", str(local_path), s3_uri], capture_output=True, text=True
    )  # nosemgrep: dangerous-subprocess-use-audit
    if result.returncode != 0:
        raise RuntimeError(f"S3 upload failed: {result.stderr}")


def compute_relative_subdir(input_s3_path: str, asset_id: str) -> str:
    _, key = parse_s3_uri(input_s3_path)
    parts = key.split("/")
    try:
        asset_id_idx = parts.index(asset_id)
    except ValueError:
        logger.warning(f"Asset ID {asset_id} not found in S3 path {input_s3_path}")
        return ""
    subdir_parts = parts[asset_id_idx + 1:-1]
    return "/".join(subdir_parts) + "/" if subdir_parts else ""


def find_output_files(output_dir: Path, extensions) -> List[Path]:
    """Every artifact under output_dir carrying one of `extensions`, most recently written first.

    `rglob` yields directory order, which is filesystem-dependent rather than sorted, so taking its
    first hit picks an arbitrary file whenever a run leaves more than one candidate -- and a transfer
    run does, since the framework writes the control video it computed alongside the generated one.
    The order here is explicit (newest, then largest, then path) and every candidate is returned, so
    the caller uploads all of them instead of discarding the rest.
    """
    wanted = {ext.lower() for ext in extensions}
    candidates = [f for f in output_dir.rglob("*") if f.is_file() and f.suffix.lower() in wanted]
    candidates.sort(key=lambda f: (-f.stat().st_mtime, -f.stat().st_size, str(f)))
    if not candidates:
        logger.warning(f"No {extensions} files found in {output_dir}")
        return []
    logger.info(f"Found {len(candidates)} output artifact(s) in {output_dir}:")
    for f in candidates:
        stat = f.stat()
        logger.info(f"  {f.relative_to(output_dir)} ({stat.st_size} bytes, mtime {stat.st_mtime})")
    return candidates


def main():
    start_time = time.time()
    try:
        logger.info("=" * 80)
        logger.info("VAMS Cosmos 3 Pipeline Starting")
        logger.info("=" * 80)

        definition = load_pipeline_definition()
        logger.info(f"Pipeline definition loaded: {json.dumps(definition, indent=2)}")

        variant = definition.get("modelVariant") or os.environ.get("MODEL_VARIANT", "nano")
        task_mode = definition.get("taskMode") or os.environ.get("TASK_MODE", "")
        cosmos_prompt = definition.get("cosmosPrompt", "")
        negative_prompt = definition.get("cosmosNegativePrompt", "")
        # Numeric settings are coerced here, at the first point they are readable, so a value that
        # cannot be used costs only the container start rather than the model restore behind it.
        seed = parse_number_setting(definition.get("cosmosSeed"), "COSMOS3_SEED", 0, integer=True)
        num_frames = parse_number_setting(
            definition.get("cosmosNumFrames"), "COSMOS3_NUM_FRAMES", 189, integer=True, minimum=1)
        guidance = parse_number_setting(definition.get("cosmosGuidance"), "COSMOS3_GUIDANCE", None)
        # Control-signal transfer fields (only used when task_mode == "transfer")
        control_type_raw = definition.get("cosmosControlType", "")
        control_path_raw = definition.get("cosmosControlPath", "")
        control_weight_raw = definition.get("cosmosControlWeight", "")
        control_guidance_raw = definition.get("cosmosControlGuidance", "")
        input_s3_asset_file_path = definition.get("inputS3AssetFilePath")
        output_s3_asset_files_path = definition.get("outputS3AssetFilesPath")
        asset_id = definition.get("assetId")

        hf_token = os.environ.get("HF_TOKEN")
        s3_model_bucket = os.environ.get("S3_MODEL_BUCKET")
        num_gpus = int(os.environ.get("NUM_GPUS", "1"))

        hf_home = HF_CACHE_BASE
        os.environ["HF_HOME"] = hf_home
        if hf_token:
            os.environ["HF_TOKEN"] = hf_token

        # Optional flags from the input configuration. Only the S3 LOCATION travels in the pipeline
        # definition; the container reads the configuration file from S3 here (inline fallback for
        # local-test invocations that pass a raw JSON string instead of an s3:// location).
        invalidate_models = False
        disable_guardrails = True
        generate_preview_gif_flag = False
        try:
            params = manifest_io.fetch_input_configuration(
                definition.get("inputConfigurationS3Location", ""))
            if params:
                invalidate_models = str(params.get("INVALIDATE_COSMOS_MODELS", "")).lower() == "true"
                disable_guardrails = str(params.get("DISABLE_GUARDRAILS", "true")).lower() != "false"
                generate_preview_gif_flag = str(params.get("GENERATE_PREVIEW_GIF", "")).lower() == "true"
                if not task_mode:
                    task_mode = params.get("TASK_MODE", "") or task_mode
                if not variant or variant == "nano":
                    variant = params.get("MODEL_VARIANT", variant) or variant
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
        if not asset_id:
            raise ValueError("assetId is required in pipeline definition")
        if not output_s3_asset_files_path:
            raise ValueError("outputS3AssetFilesPath is required in pipeline definition")

        # Control-signal transfer is only supported on the general-purpose omni
        # checkpoints (nano, super). Ignore the transfer request for the
        # task-specialized Super variants and fall back to their normal mode.
        if task_mode == "transfer" and variant not in TRANSFER_CAPABLE_VARIANTS:
            logger.warning(
                f"task_mode=transfer is not supported for variant '{variant}'; "
                f"ignoring transfer and using the variant's default mode"
            )
            task_mode = ""

        is_transfer = task_mode == "transfer"
        effective_mode = task_mode or ""

        # The control-signal settings are validated here rather than where they are used below:
        # everything in between is expensive -- the model restore alone moves tens of gigabytes --
        # and a rejected control weight or control path is knowable now.
        control_blocks = None
        control_guidance = None
        control_sources = {}
        if is_transfer:
            control_blocks, control_sources = build_control_blocks(
                control_type_raw, control_path_raw, control_weight_raw,
                allowed_control_buckets(definition)
            )
            if not control_blocks:
                raise ValueError(
                    f"transfer requires at least one valid COSMOS3_CONTROL_TYPE "
                    f"(supported: {', '.join(TRANSFER_CONTROL_TYPES)})"
                )
            control_guidance = parse_number_setting(
                control_guidance_raw, "COSMOS3_CONTROL_GUIDANCE", 1.5)

        logger.info(f"Variant: {variant}, task_mode: {task_mode}, seed: {seed}, num_frames: {num_frames}")
        # The guardrail state is the one generation setting with a safety consequence, and it is
        # decided by the input configuration rather than by anything visible in the job definition,
        # so the run records which way it resolved.
        logger.info(f"Guardrails: {'disabled' if disable_guardrails else 'enabled'}")
        logger.info(f"Prompt: {cosmos_prompt}")

        # Step 1: Ensure models cached
        logger.info("Step 1: Ensuring models are cached")
        ensure_models_cached(hf_home=hf_home, s3_bucket=s3_model_bucket, invalidate=invalidate_models)

        # Step 2: Download input file for input-file modes
        input_file_path = None
        # Determine effective mode for input decision: fall back to variant default handled in inference
        needs_input = effective_mode in INPUT_FILE_MODES or variant == "super-image2video"
        if needs_input:
            if not input_s3_asset_file_path:
                raise ValueError(f"Mode {effective_mode or variant} requires inputS3AssetFilePath")
            logger.info("Step 2: Downloading input file")
            input_filename = Path(parse_s3_uri(input_s3_asset_file_path)[1]).name
            input_file_path = INPUT_DIR / input_filename
            download_from_s3(input_s3_asset_file_path, input_file_path)

        # Step 2b: For transfer, download the pre-computed control videos named above. A control type
        # with no path leaves the framework to auto-compute the signal from the source video
        # (vision_path).
        if is_transfer:
            logger.info("Step 2b: Preparing transfer control signals")
            for ctype in control_blocks:
                s3_path = control_sources.get(ctype)
                if s3_path:
                    control_local = INPUT_DIR / f"control_{ctype}_{Path(parse_s3_uri(s3_path)[1]).name}"
                    download_from_s3(s3_path, control_local)
                    control_blocks[ctype]["control_path"] = str(control_local)
                    logger.info(f"  {ctype}: using pre-computed control {s3_path}")
                else:
                    logger.info(f"  {ctype}: auto-computed from source video")

        # Step 3: Run inference
        logger.info("Step 3: Running inference")
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        run_inference(
            variant=variant,
            task_mode=task_mode,
            prompt=cosmos_prompt,
            negative_prompt=negative_prompt,
            num_frames=num_frames,
            guidance=guidance,
            seed=seed,
            input_file_path=str(input_file_path) if input_file_path else None,
            output_dir=str(OUTPUT_DIR),
            hf_home=hf_home,
            hf_token=hf_token,
            num_gpus=num_gpus,
            disable_guardrails=disable_guardrails,
            control_blocks=control_blocks,
            control_guidance=control_guidance,
        )

        # Step 4: Find output (image for text2image, else video)
        logger.info("Step 4: Finding output file")
        is_image_output = (task_mode in IMAGE_OUTPUT_MODES) or variant == "super-text2image"
        extensions = (".png", ".jpg", ".jpeg", ".webp") if is_image_output else (".mp4",)
        output_files = find_output_files(OUTPUT_DIR, extensions)
        if not output_files:
            raise RuntimeError("No output file generated")
        output_file, extra_files = output_files[0], output_files[1:]
        out_ext = output_file.suffix
        if extra_files:
            # Which artifact a generative run leaves is not fixed, so uploading one and dropping the
            # rest would report success for a partial result. All of them travel; the primary is the
            # one that carries the asset-facing name and the preview.
            logger.warning(
                f"{len(output_files)} output artifacts matched {extensions}; primary: "
                f"{output_file.name}; also uploading: {', '.join(f.name for f in extra_files)}")

        # Step 5: Upload to S3, preserving relative path for input-file modes
        logger.info("Step 5: Uploading output to S3")
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_bucket, output_base = parse_s3_uri(output_s3_asset_files_path)
        output_base = output_base.rstrip("/") + "/"
        relative_subdir = ""
        if needs_input and input_s3_asset_file_path:
            relative_subdir = compute_relative_subdir(input_s3_asset_file_path, asset_id)
            stem = Path(parse_s3_uri(input_s3_asset_file_path)[1]).stem
            output_basename = f"{stem}_Cosmos3_{variant}_{timestamp}"
        else:
            output_basename = f"cosmos3-{variant}-{timestamp}"
        output_key = f"{output_base}{relative_subdir}{output_basename}{out_ext}"
        output_s3_uri = f"s3://{output_bucket}/{output_key}"
        upload_to_s3(output_file, output_s3_uri)
        logger.info(f"Uploaded output: {output_s3_uri}")

        # Additional artifacts are named after their own path inside the output directory so several
        # from one run stay distinguishable, and stay flat beside the primary because the workflow's
        # own path extension is what separates runs.
        for extra in extra_files:
            suffix = OUTPUT_NAME_UNSAFE.sub(
                "_", str(extra.relative_to(OUTPUT_DIR).with_suffix("")))
            extra_uri = (f"s3://{output_bucket}/{output_base}{relative_subdir}"
                         f"{output_basename}_{suffix}{extra.suffix}")
            upload_to_s3(extra, extra_uri)
            logger.info(f"Uploaded additional output: {extra_uri}")

        # Step 6: Optional preview GIF for video outputs
        if generate_preview_gif_flag and out_ext == ".mp4":
            logger.info("Step 6: Generating and uploading preview GIF")
            try:
                preview_gif_path = OUTPUT_DIR / "preview.gif"
                generate_preview_gif(video_path=str(output_file), output_path=str(preview_gif_path))
                preview_s3_uri = f"s3://{output_bucket}/{output_key}.previewFile.gif"
                upload_to_s3(preview_gif_path, preview_s3_uri)
                logger.info(f"Preview GIF uploaded: {preview_s3_uri}")
            except Exception as gif_err:
                logger.warning(f"Failed to generate/upload preview GIF (non-fatal): {gif_err}")

        # Step 7: Backup HF cache (non-fatal)
        logger.info("Step 7: Backing up HF cache to S3")
        try:
            from model_manager import backup_cache_to_s3
            backup_cache_to_s3(hf_home=hf_home, s3_bucket=s3_model_bucket)
        except Exception as backup_err:
            logger.warning(f"Failed to backup HF cache to S3 (non-fatal): {backup_err}")

        elapsed = time.time() - start_time
        logger.info(f"Pipeline completed successfully in {elapsed:.1f}s. Output: {output_s3_uri}")

    except Exception as e:
        logger.error("Pipeline failed with error:")
        logger.error(str(e))
        import traceback
        logger.error(traceback.format_exc())
        sys.stdout.flush()
        sys.stderr.flush()
        raise


if __name__ == "__main__":
    main()
