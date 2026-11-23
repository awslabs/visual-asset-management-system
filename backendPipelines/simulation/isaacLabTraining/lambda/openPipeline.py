#  Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""
ConstructPipeline Lambda
Builds the Batch job definition for IsaacLab training or evaluation.
The run configuration comes from the manifest-delivered input configuration; a JSON input file may
supply standing defaults for the fields it leaves blank.
"""

import json
import re
from datetime import datetime, timezone
import boto3
from urllib.parse import urlparse
from customLogging.logger import safeLogger
from botocore.config import Config
from botocore.exceptions import ClientError
import manifestHelper

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="OpenPipelineIsaacLabTraining")
s3_client = boto3.client("s3", config=retry_config)

# An asset bucket may be in another Region than this function. The event names the input and output
# buckets' Regions (resolved from the manifest by the vamsExecute Lambda); a read of such a bucket is
# signed with manifestHelper's client for that Region, and s3_client serves the rest.
_bucket_regions = {}


def _s3_client_for_bucket(bucket_name):
    region = _bucket_regions.get(bucket_name)
    if not region:
        return s3_client
    return manifestHelper.s3_client_for_region(region)
sfn_client = boto3.client("stepfunctions", config=retry_config)

# Sort floor for a checkpoint whose S3 object carries no LastModified.
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)

DEFAULT_TASK = "Isaac-Cartpole"
DEFAULT_RL_LIBRARY = "rsl_rl"
DEFAULT_NUM_ENVS_TRAIN = 4096
DEFAULT_NUM_ENVS_EVAL = 100
DEFAULT_MAX_ITERATIONS = 1500
DEFAULT_NUM_EPISODES = 50
DEFAULT_STEPS_PER_EPISODE = 1000

# Seconds the run's workflow step may take, per mode: the bound for the state machine's batch task and
# for the AWS Batch attempt (executeBatchJob.py). Each equals that mode's vamsSchema bundle taskTimeout
# (training/pipeline.json, evaluation/pipeline.json). The parent workflow's clock starts first (vamsExecute,
# this function and the Pass state run before the batch task is entered), so at the bound the parent's
# step timeout fires first and is what the execution records; the workflow's error handler then stops the
# registered sub-processes (this pipeline's state machine and its Batch job). infra/test/pipelines/
# isaacLabTimeoutChain.test.ts and lambda/tests/test_vams_schema_bundles.py assert the copies agree.
TASK_TIMEOUT_SECONDS = {"train": 28800, "evaluate": 32400}

# The RL libraries the container has an Isaac Lab script for. A third copy of the list held in
# container/utils/training/config.py (RL_LIBRARIES) and container/__main__.py (the train and play
# script maps): the lambda's code bundle is this directory alone, so the container package is not
# importable here. lambda/tests/test_config_precedence.py asserts the copies still agree.
SUPPORTED_RL_LIBRARIES = ("rsl_rl", "rl_games", "skrl")

# The physics engines the run may select (Isaac Lab's ``physics=<preset>`` override): Newton with the
# MuJoCo-Warp solver, the default, and PhysX on Isaac Sim, which reproduces what Isaac Lab 2.3.0 ran.
# Further copies live in container/utils/training/config.py (PHYSICS_ENGINES) and in the two vamsSchema
# templates (the PHYSICS tag's enumValues); lambda/tests/test_config_precedence.py asserts they agree.
SUPPORTED_PHYSICS = ("newton_mjwarp", "isaacsim_physx")
DEFAULT_PHYSICS = "newton_mjwarp"

# Isaac Lab 3.0 does not alias the 2.x ``-v0`` task ids, and some were renamed rather than only
# stripped, so the mapping is data. Only rows confirmed against an Isaac Lab 3.0.0 image are listed
# (``list_envs`` and a run); an id that ends in ``-v0`` and is not here is passed through unchanged
# (it may be a custom environment's own id, or a 3.0 id such as IsaacContrib-Stack-Cube-SO101-v0).
TASK_ID_MAP_2X_TO_3X = {
    "Isaac-Cartpole-v0": "Isaac-Cartpole",
    "Isaac-Cartpole-Direct-v0": "Isaac-Cartpole-Direct",
    "Isaac-Lift-Cube-Franka-v0": "Isaac-Lift-Franka",
    "Isaac-Velocity-Rough-Anymal-D-v0": "Isaac-Velocity-Rough-AnymalD",
}

# Task families the spike showed to be limited, as (id prefix, libraries the task offers). A deny
# list: a task no prefix names passes, so a valid combination is never blocked by a stale table. The
# AutoMate tasks register an agent configuration for rl_games only (``list_envs`` of Isaac Lab 3.0.0).
TASK_FAMILY_LIMITS = (
    ("IsaacContrib-AutoMate-", ("rl_games",)),
)

# The value the run records in place of an engine when the task has no physics presets to choose
# from. It is never accepted as input (``resolve_physics`` allows the two engines only).
TASK_DEFAULT_PHYSICS = "task-default"

# Tasks of Isaac Lab 3.0.0 that list no physics preset (``list_envs`` of the image, the spike's task
# table). Isaac Lab rejects any ``physics=<engine>`` override for them, with a "Typed preset
# selector(s)" error, so the run leaves the override out and the task runs on the physics it
# registers. A task this list does not name (a custom or ported environment) is handled in the
# container, which reads that same error and runs once more without the override.
TASKS_WITHOUT_PHYSICS_PRESETS = frozenset((
    "IsaacContrib-Velocity-Flat-AnymalC-Direct", "IsaacContrib-Velocity-Rough-AnymalC-Direct",
    "IsaacContrib-Assemble-Trocar-G129-Dex3", "IsaacContrib-Assemble-Trocar-G129-Dex3-Eval",
    "IsaacContrib-AutoMate-Assembly-Direct", "IsaacContrib-AutoMate-Disassembly-Direct",
    "IsaacContrib-Open-Drawer-OpenArm", "IsaacContrib-Cartpole-Camera-Showcase-Direct",
    "IsaacContrib-Deploy-GearAssembly-Rizon4s-Grav",
    "IsaacContrib-Deploy-GearAssembly-Rizon4s-Grav-ROS-Inference",
    "IsaacContrib-Deploy-GearAssembly-UR10e-2F140", "IsaacContrib-Deploy-GearAssembly-UR10e-2F85",
    "IsaacContrib-Deploy-GearAssembly-UR10e-2F140-ROS-Inference",
    "IsaacContrib-Deploy-GearAssembly-UR10e-2F85-ROS-Inference",
    "IsaacContrib-Deploy-Reach-Rizon4s", "IsaacContrib-Deploy-Reach-Rizon4s-ROS-Inference",
    "IsaacContrib-Deploy-Reach-UR10e", "IsaacContrib-Deploy-Reach-UR10e-ROS-Inference",
    "IsaacContrib-Navigation-3DObstacles-ARL-Robot-1",
    "IsaacContrib-TrackPositionNoObstacles-ARL-Robot-1", "IsaacContrib-Factory-PegInsert-Direct",
    "IsaacContrib-Factory-GearMesh-Direct", "IsaacContrib-Factory-NutThread-Direct",
    "IsaacContrib-Forge-PegInsert-Direct", "IsaacContrib-Forge-GearMesh-Direct",
    "IsaacContrib-Forge-NutThread-Direct", "IsaacContrib-Franka-Pour",
    "IsaacContrib-Humanoid-AMP-Dance-Direct", "IsaacContrib-Humanoid-AMP-Run-Direct",
    "IsaacContrib-Humanoid-AMP-Walk-Direct", "IsaacContrib-Lift-Cube-Franka",
    "IsaacContrib-Lift-Cube-Franka-IK-Abs", "IsaacContrib-Lift-Cube-Franka-IK-Rel",
    "IsaacContrib-Lift-Cube-OpenArm", "IsaacContrib-PickPlace-Locomanipulation-G1-Abs",
    "IsaacContrib-PickPlace-FixedBaseUpperBodyIK-G1-Abs", "IsaacContrib-Multitask-Manipulation",
    "IsaacContrib-Navigation-Flat-AnymalC", "IsaacContrib-PickPlace-GR1T2-Abs",
    "IsaacContrib-NutPour-GR1T2-Pink-IK-Abs", "IsaacContrib-ExhaustPipe-GR1T2-Pink-IK-Abs",
    "IsaacContrib-PickPlace-GR1T2-WaistEnabled-Abs", "IsaacContrib-PickPlace-G1-InspireFTP-Abs",
    "IsaacContrib-Reach-OpenArmBi", "IsaacContrib-Reach-OpenArm",
    "IsaacContrib-Stack-Cube-Instance-Randomize-Franka",
    "IsaacContrib-Stack-Cube-Instance-Randomize-Franka-IK-Rel", "IsaacContrib-UR10-Particle-Push",
))


# Ceilings for the operator-supplied counts, each far above any shipped default. They catch a value
# that cannot be what was meant — a mistyped extra digit — rather than describing capacity, which
# depends on the task and the GPU: a count inside its ceiling can still exhaust GPU memory. A second
# copy lives in container/utils/training/config.py, which covers a direct container invocation;
# rejecting the value here is what keeps a Batch GPU node from being provisioned for it at all.
# lambda/tests/test_isaaclab_numeric_bounds.py asserts the two copies agree.
MAX_NUM_ENVS = 65536
MAX_MAX_ITERATIONS = 100000
MAX_NUM_EPISODES = 10000
MAX_STEPS_PER_EPISODE = 100000

# Bounds for trainingConfig.stageAssetPaths, the executing asset's files and folders the container
# downloads before Isaac Lab starts. Counting them here rejects an oversized selection before a GPU
# node is provisioned; container/utils/aws/staging.py holds a second copy and re-checks what the
# asset holds when the job starts. lambda/tests/test_isaaclab_stage_assets.py asserts the two agree.
MAX_STAGE_ASSET_PATHS = 50
MAX_STAGE_ASSET_PATH_LENGTH = 1024
# The validated paths travel in the Batch job's command beside the task tokens and the run's S3
# paths, JSON-encoded (a character outside ASCII becomes a 6- or 12-character escape); their total
# as encoded keeps the container overrides well inside AWS Batch's size limits.
MAX_STAGE_ASSET_PATHS_TOTAL_LENGTH = 2048
MAX_STAGED_ASSET_FILES = 10000
MAX_STAGED_ASSET_BYTES = 50 * 1024 ** 3
# The longest file or folder name the container can stage, in bytes: NAME_MAX (255) less the
# 9-byte temporary suffix the boto3 downloader adds while a file downloads, which it cuts by
# characters rather than bytes.
MAX_STAGED_NAME_BYTES = 246

# A VAMS file preview (`<file>.previewFile.<ext>`) belongs to the file it previews, not to the task.
PREVIEW_FILE_MARKER = ".previewFile."

_STAGE_PATH_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_STAGE_PATH_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")


def abort_external_workflow(error, task_token):
    """Fail the VAMS workflow's waitForCallback task token so a failure here does not leave the
    pipeline task waiting for its full taskTimeout. This is the first state of the pipeline's state
    machine, so the container never starts and nothing downstream can report on the token.

    Never raises: the caller re-raises the original error, which is the one worth reading."""
    if not task_token:
        return
    try:
        sfn_client.send_task_failure(
            taskToken=task_token,
            error="IsaacLabPipelineError",
            cause=str(error)[:256]
        )
        logger.info("Sent task failure callback to Step Functions")
    except Exception as e:
        logger.error(f"Failed to send task failure callback: {e}")


def lambda_handler(event, context):
    logger.info("Event", event=event)

    try:
        return build_job_config_payload(event)
    except Exception as e:
        logger.exception(e)
        abort_external_workflow(e, event.get("externalSfnTaskToken", ""))
        raise


def bucket_regions(event) -> dict:
    """Bucket name -> Region for the asset buckets named by the event's input and output paths, from
    the Regions the vamsExecute Lambda resolved from the manifest. A bucket in the deployment Region
    (an empty Region, or an event from an older Lambda) is left out: requests for it use the default
    client."""
    regions = {}
    for path_key, region_key in (("inputS3AssetFilePath", "inputBucketRegion"),
                                 ("outputS3AssetFilesPath", "outputBucketRegion")):
        uri, region = event.get(path_key) or "", event.get(region_key) or ""
        if uri.startswith("s3://") and region:
            regions[uri[len("s3://"):].split("/", 1)[0]] = region
    return regions


def build_job_config_payload(event):
    """The state machine payload carrying the Batch job's run configuration."""

    _bucket_regions.update(bucket_regions(event))

    # Standing defaults a JSON input file may carry, read at the version the run was launched on; the
    # manifest configuration outranks them.
    file_config = load_config_from_s3(event.get("inputS3AssetFilePath"), event.get("inputVersionId") or "")

    training_config = merge_configs(
        file_config.get("trainingConfig", {}),
        event.get("trainingConfig", {})
    )

    mode = training_config.get("mode", "train")
    requested_task = require_task(training_config.get("task") or DEFAULT_TASK)
    task = map_task_id(requested_task)
    rl_library = resolve_rl_library(training_config.get("rlLibrary") or DEFAULT_RL_LIBRARY)
    physics = resolve_physics(training_config.get("physics") or DEFAULT_PHYSICS)
    require_supported_task(task, rl_library)
    if task in TASKS_WITHOUT_PHYSICS_PRESETS:
        physics = TASK_DEFAULT_PHYSICS

    if mode == "train":
        job_config = build_training_config(event, training_config, task, rl_library, physics)
    elif mode == "evaluate":
        job_config = build_evaluation_config(event, training_config, task, rl_library, physics)
    else:
        raise ValueError(f"Invalid mode: {mode}. Must be 'train' or 'evaluate'")

    if task != requested_task:
        job_config["trainingConfig"]["taskIdRequested"] = requested_task

    # Bucket name -> Region for the asset buckets this job reads and writes, so the container signs
    # each bucket's requests with a client for that bucket's Region (see utils/aws/s3.py).
    job_config["bucketRegions"] = bucket_regions(event)

    logger.info(f"Job config: {job_config}")

    return {
        "jobName": event.get("jobName"),
        "definition": json.dumps(job_config),
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath"),
        "outputS3AssetFilesPath": job_config.get("outputS3AssetFilesPath", ""),
        "taskTimeoutSeconds": TASK_TIMEOUT_SECONDS[mode],
        "status": "STARTING",
    }


def resolve_rl_library(rl_library) -> str:
    """The RL library the run asked for, checked against the set the container has a script for.

    An unrecognised value is rejected rather than substituted, so a run cannot complete against a
    library the operator did not ask for and leave checkpoints the requested library cannot load.
    The container rejects it too; doing it here is what makes the rejection free — this is the first
    state of the pipeline's state machine, so no GPU node is provisioned and no container image is
    pulled.
    """
    value = rl_library.strip() if isinstance(rl_library, str) else rl_library
    if value not in SUPPORTED_RL_LIBRARIES:
        raise ValueError(
            f"Unsupported rlLibrary '{rl_library}'. Supported libraries: "
            f"{', '.join(sorted(SUPPORTED_RL_LIBRARIES))}"
        )
    return value


def resolve_physics(physics) -> str:
    """The physics engine the run asked for, checked against the two the container can select.

    An unrecognised value is rejected rather than substituted, so a run cannot complete on an engine
    the operator did not ask for. Like ``resolve_rl_library``, this is the free check: it fails in the
    first state of the state machine, before any GPU node is provisioned. The value is not echoed
    beyond the message of the ValueError.
    """
    value = physics.strip() if isinstance(physics, str) else physics
    if value not in SUPPORTED_PHYSICS:
        raise ValueError(
            f"Unsupported physics '{physics}'. Supported engines: {', '.join(SUPPORTED_PHYSICS)}"
        )
    return value


def map_task_id(task: str) -> str:
    """The Isaac Lab 3.0 id for a saved 2.x task id, or ``task`` unchanged.

    The mapping is logged once; the original id travels in the run configuration (``taskIdRequested``).
    """
    mapped = TASK_ID_MAP_2X_TO_3X.get(task)
    if not mapped:
        return task
    logger.info(f"Task id {task} mapped to {mapped}")
    return mapped


def require_supported_task(task: str, rl_library: str) -> None:
    """Refuse a task and library the spike showed cannot run, before any GPU starts."""
    for prefix, libraries in TASK_FAMILY_LIMITS:
        if task.startswith(prefix) and rl_library not in libraries:
            raise ValueError(
                f"Task {task} offers the RL library {', '.join(libraries)} in Isaac Lab 3.0. "
                f"Set rlLibrary to {libraries[0]}."
            )


_EXECUTION_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def train_config_keys(key: str) -> list:
    """The keys a checkpoint's ``train-config.json`` may sit at, in the order to try them.

    A training run writes its checkpoints to ``<asset>/checkpoints/<executionId>/<file>`` and its
    run record to ``<asset>/<executionId>/train-config.json``. A checkpoint copied straight into a
    ``checkpoints`` folder has the record beside that folder.
    """
    parts = key.split("/")
    folders = parts[:-1]
    keys = []
    if len(folders) >= 2 and folders[-2] == "checkpoints" and _EXECUTION_ID.match(folders[-1]):
        keys.append("/".join(folders[:-2] + [folders[-1], "train-config.json"]))
    if folders and folders[-1] == "checkpoints":
        keys.append("/".join(folders[:-1] + ["train-config.json"]))
    return keys


def physics_note(policy_s3_uri: str, physics: str) -> str:
    """One line saying the checkpoint was trained on another engine, or ``""``.

    Advisory (it never fails a run: evaluating on the other engine is a legitimate transfer test).
    Reads the ``train-config.json`` the training run wrote (see ``train_config_keys`` for where), in the
    same bucket as the checkpoint, whose bucket scope has already been checked. Every error gives
    ``""``. A run configuration with no ``physics`` and no ``isaacLabVersion`` was written before the
    upgrade, which ran PhysX. Only a value of the allow list is repeated, never operator text.
    """
    if physics == TASK_DEFAULT_PHYSICS:
        return ""  # the task has no engine to choose, so there is nothing to compare
    try:
        parsed = urlparse(policy_s3_uri)
        keys = train_config_keys(parsed.path.lstrip("/"))
        if not parsed.netloc or not keys:
            return ""
        response = None
        for candidate in keys:
            try:
                response = _s3_client_for_bucket(parsed.netloc).get_object(
                    Bucket=parsed.netloc, Key=candidate, Range="bytes=0-1048575")
                break
            except Exception as error:
                logger.info(f"No run record at the candidate key: {type(error).__name__}")
        if response is None:
            return ""
        recorded = json.loads(response["Body"].read())
        training = recorded.get("trainingConfig") or {}
        trained = training.get("physics")
        if trained is None and not recorded.get("isaacLabVersion"):
            trained = "isaacsim_physx"
            suffix = " (a run made before the upgrade)"
        else:
            suffix = ""
        if trained not in SUPPORTED_PHYSICS or trained == physics:
            return ""
        return (f"This checkpoint was trained with physics={trained}{suffix}; this evaluation runs "
                f"physics={physics}, so the result is a transfer test, not a like-for-like evaluation.")
    except Exception as error:
        logger.info(f"No physics note: {type(error).__name__}")
        return ""


def _parse_whole(value):
    """``value`` as an int when it names one exactly, else ``None``.

    Rejects a fraction rather than truncating it, and rejects ``nan``/``inf``, which are integral to
    neither ``int()`` nor ``is_integer()``.
    """
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            pass
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else None


def whole_number(field: str, value, default, minimum=1, maximum=None):
    """The whole number ``field`` carries, or ``default`` when the run does not set it.

    A quoted number is accepted because a hand-edited config body or a JSON input file easily carries
    one, and because a string reaching the container multiplies instead of adding up: ``numEpisodes``
    of ``"50"`` times ``stepsPerEpisode`` of 1000 is a 2000-character ``--video_length`` argument
    rather than 50000. A boolean, a fraction, a blank value, or a count outside
    ``minimum``..``maximum`` is rejected naming the field, before any Batch job is submitted.
    """
    if value is None:
        return default

    if isinstance(value, bool):
        raise ValueError(f"{field} must be a whole number, not a boolean (received {value!r})")

    number = value if isinstance(value, int) else None
    if number is None and isinstance(value, (float, str)):
        number = _parse_whole(value)
    if number is None:
        raise ValueError(f"{field} must be a whole number (received {value!r})")

    if minimum is not None and number < minimum:
        raise ValueError(f"{field} must be {minimum} or greater (received {number})")
    if maximum is not None and number > maximum:
        raise ValueError(f"{field} must be {maximum} or less (received {number})")
    return number


def require_task(value) -> str:
    """The Isaac Lab task id the run names, checked non-blank.

    The task reaches ``--task`` on the container's command line, where a blank value fails only after
    Isaac Sim has started.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"task must be a non-blank Isaac Lab task id (received {value!r})")
    return value.strip()


# Error codes Amazon S3 answers a read of a version that no longer exists with: NoSuchVersion or
# NoSuchKey (GET), 404 (HEAD of a deleted version), 405 / MethodNotAllowed (a delete marker).
MISSING_VERSION_ERROR_CODES = ("NoSuchVersion", "NoSuchKey", "404", "405", "MethodNotAllowed")


def load_config_from_s3(s3_uri: str, version_id: str = "") -> dict:
    """Standing defaults from a JSON input file, or {} when the file carries none.

    An input file is an ASSET file the operator selected, not the run's configuration, so anything
    it holds is a fallback only. A file that is not JSON, is not a JSON object, or does not parse
    yields no defaults rather than an error. ``version_id`` reads the version the run was launched on;
    when that version no longer exists the run fails naming it, rather than taking defaults from
    whatever the file holds now.
    """
    if not s3_uri:
        return {}

    try:
        parsed = urlparse(s3_uri)
        bucket = parsed.netloc
        key = parsed.path.lstrip("/")

        if not key.endswith(".json"):
            logger.info(f"Input file is not JSON, skipping config parsing: {key}")
            return {}

        version = {"VersionId": version_id} if version_id else {}
        try:
            response = _s3_client_for_bucket(bucket).get_object(Bucket=bucket, Key=key, **version)
        except ClientError as e:
            if version_id and str(e.response.get("Error", {}).get("Code", "")) in MISSING_VERSION_ERROR_CODES:
                raise InputVersionMissingError(
                    f"The input file {key} at version {version_id} no longer exists; "
                    f"it was deleted after the run started.") from None
            raise
        content = response["Body"].read().decode("utf-8")
        config = json.loads(content)
        if not isinstance(config, dict):
            logger.info(f"Input JSON file is not a config object, skipping: {key}")
            return {}
        logger.info(f"Loaded config defaults from S3: {config}")
        return config
    except InputVersionMissingError:
        raise
    except Exception as e:
        logger.warning(f"Failed to load config from S3: {e}")
        return {}


class InputVersionMissingError(Exception):
    """The input file version the run was launched on no longer exists."""


def _as_utc(last_modified) -> datetime:
    """A comparable timezone-aware timestamp for an S3 object's LastModified. A missing value or a
    naive datetime is normalized so run folders always compare against each other."""
    if not isinstance(last_modified, datetime):
        return _EPOCH
    if last_modified.tzinfo is None:
        return last_modified.replace(tzinfo=timezone.utc)
    return last_modified


# What each RL library names the checkpoints it writes (issue #1315): the file names the evaluation
# may pick (``name``) and the number that orders them (``iteration``, group 1). The container uploads
# every library's checkpoints flat into the run's checkpoints/ folder; the layouts and the matching
# table of paths are in container/utils/training/checkpoints.py (CHECKPOINT_PATTERNS), and
# tests/test_policy_discovery.py asserts that the two agree. Isaac Lab 3.0.0 layouts:
#   rsl_rl:   model_<iteration>.pt
#   rl_games: nn/last_<name>_ep_<epoch>_rew__<reward>_.pth (and <name>.pth, best reward, no number, when written)
#   skrl:     agent_<timestep>.pt, best_agent.pt (no number)
CHECKPOINT_RANKING = {
    "rsl_rl": {"name": r"^model_\d+\.pt$", "iteration": r"^model_(\d+)\.pt$"},
    "rl_games": {"name": r"\.pth$", "iteration": r"^last_.*?_(?:ep|frame)_(\d+)(?:_.*)?\.pth$"},
    "skrl": {"name": r"^(?:agent_\d+|best_agent)\.pt$", "iteration": r"^agent_(\d+)\.pt$"},
}

# File suffixes a policy can carry. rl_games writes .pth; every other library and any hand-made
# policy uses .pt.
POLICY_SUFFIXES = (".pt", ".pth")


def checkpoint_iteration(key: str, rl_library: str = None) -> int:
    """The training iteration encoded in a checkpoint file name (``model_1500.pt`` -> 1500).

    With ``rl_library`` the file name is read the way that library writes it (``agent_30000.pt`` for
    skrl, ``last_Cartpole_ep_150_rew__12.5_.pth`` for rl_games), and a name that carries no iteration
    (``best_agent.pt``, ``Cartpole.pth``) yields -1. Without it, or for a library not in the table,
    the last run of digits in the file name is read, and a name carrying no digits yields -1, so it
    sorts below every numbered checkpoint.
    """
    file_name = key.rsplit("/", 1)[-1]
    ranking = CHECKPOINT_RANKING.get(rl_library)
    if ranking:
        match = re.search(ranking["iteration"], file_name)
        return int(match.group(1)) if match else -1
    digits = re.findall(r"\d+", file_name)
    return int(digits[-1]) if digits else -1


def discover_policy_file(bucket: str, asset_location_key: str, rl_library: str = None) -> str:
    """Discover a .pt or .pth policy file anywhere under the asset root in S3.

    Searches under bucketAsset + inputAssetLocationKey (the authoritative asset
    root), not under the input file's parent directory. The asset root is the
    only reliable starting point — input files may live at arbitrary depths
    beneath it, and deriving the root from the input file path is unsafe.

    Each training run writes its checkpoints under its own execution folder, so selection is
    per-run: the run folder holding the most recently written checkpoint wins, and within it the
    highest training iteration (numeric, not lexicographic).

    When ``rl_library`` is given, files named the way that library names its checkpoints are
    preferred (CHECKPOINT_RANKING), so a newer run of another library on the same asset is not
    picked and then fails to load. A file of any other name is still chosen when none matches.

    Args:
        bucket: The asset S3 bucket name (bucketAsset).
        asset_location_key: The asset root prefix within the bucket (inputAssetLocationKey).
        rl_library: The RL library the evaluation runs, or None to consider every .pt/.pth file alike.

    Returns:
        S3 URI of the discovered .pt file, or empty string if not found.
    """
    if not bucket or not asset_location_key:
        return ""

    try:
        prefix = asset_location_key if asset_location_key.endswith("/") else asset_location_key + "/"

        logger.info(f"Searching for .pt and .pth files under asset root s3://{bucket}/{prefix}")

        # The candidate files, with their write times, in listing order.
        candidates = []
        paginator = _s3_client_for_bucket(bucket).get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith(POLICY_SUFFIXES):
                    candidates.append((key, _as_utc(obj.get("LastModified"))))

        ranking = CHECKPOINT_RANKING.get(rl_library)
        if ranking:
            named = [c for c in candidates if re.search(ranking["name"], c[0].rsplit("/", 1)[-1])]
            candidates = named or candidates

        # Per run folder (the checkpoint's parent prefix): its checkpoints and its newest write time.
        runs = {}
        for key, last_modified in candidates:
            run_prefix = key.rsplit("/", 1)[0]
            run = runs.setdefault(run_prefix, {"keys": [], "lastModified": _EPOCH})
            run["keys"].append(key)
            if last_modified > run["lastModified"]:
                run["lastModified"] = last_modified

        if runs:
            newest_run_prefix = max(runs, key=lambda p: (runs[p]["lastModified"], p))
            policy_key = max(runs[newest_run_prefix]["keys"],
                             key=lambda k: checkpoint_iteration(k, rl_library))
            policy_uri = f"s3://{bucket}/{policy_key}"
            logger.info(f"Discovered policy file: {policy_uri}")
            return policy_uri

        logger.info("No .pt or .pth files found under asset root")
        return ""
    except Exception as e:
        logger.warning(f"Failed to discover policy file: {e}")
        return ""


def merge_configs(base: dict, override: dict) -> dict:
    """Merge two config sections, with override taking priority per key.

    A key the override leaves absent, null, or blank falls through to the base, which is what lets an
    input file hold a standing default for a field the run's configuration does not set.
    """
    result = base.copy() if isinstance(base, dict) else {}
    if not isinstance(override, dict):
        return result
    for key, value in override.items():
        if value is not None and str(value).strip() != "":
            result[key] = value
    return result


def resolve_relative_path(bucket: str, asset_location_key: str, relative_path: str) -> str:
    """Resolve a relative path to a full S3 URI based on the asset root.

    Uses bucketAsset + inputAssetLocationKey as the authoritative asset root.
    Do not derive the asset root from inputS3AssetFilePath — the input file may
    be nested arbitrarily deep under the asset root, so path-segment math on it
    cannot correctly locate the asset root.

    Args:
        bucket: The asset S3 bucket name (bucketAsset).
        asset_location_key: The asset root prefix within the bucket (inputAssetLocationKey).
        relative_path: Relative path within the asset (e.g., "environments/my_env.tar.gz").

    Returns:
        Full S3 URI to the file.
    """
    prefix = asset_location_key if asset_location_key.endswith("/") else asset_location_key + "/"
    return f"s3://{bucket}/{prefix}{relative_path.lstrip('/')}"


def require_asset_bucket(field: str, s3_uri: str, event) -> str:
    """Confirm an operator-supplied S3 URI names the executing asset's own bucket.

    Both `customEnvironmentS3Uri` and `policyS3Uri` reach the container as objects it downloads and
    then executes: the environment package is installed with pip, which runs its setup code, and the
    policy is deserialized by torch. Their values arrive from a template config body or the content of
    a JSON input file, so the bucket is constrained here rather than left to the job role, whose S3
    read spans every registered VAMS asset bucket plus the auxiliary bucket.

    Fails closed on a missing `bucketAsset`, which is the same condition the relative-path routes
    already reject: without it there is nothing to compare against.
    """
    bucket = event.get("bucketAsset", "")
    if not bucket:
        raise ValueError(
            f"Cannot validate {field}: pipeline is missing bucketAsset. Ensure vamsExecute "
            "passes it through from the workflow."
        )

    parsed = urlparse(s3_uri)
    if parsed.scheme != "s3":
        raise ValueError(f"{field} must be an s3:// URI (received scheme '{parsed.scheme}')")
    if parsed.netloc != bucket:
        raise ValueError(
            f"{field} must reference the asset's own bucket '{bucket}', not '{parsed.netloc}'"
        )
    return s3_uri


def require_existing_object(field: str, s3_uri: str) -> str:
    """Confirm the object an operator-supplied S3 URI names is there.

    A mistyped `checkpointPath` or `customEnvironmentPath` otherwise fails inside the container, after
    a GPU node has started and the image has been pulled. Only a definite not-found is rejected: any
    other outcome — a denied HeadObject, a transient error — leaves the run to proceed, so a
    permissions gap cannot turn a working run into a rejected one.
    """
    parsed = urlparse(s3_uri)
    bucket = parsed.netloc
    key = parsed.path.lstrip("/")
    if not bucket or not key:
        return s3_uri

    try:
        _s3_client_for_bucket(bucket).head_object(Bucket=bucket, Key=key)
    except ClientError as e:
        code = str(e.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey"):
            raise ValueError(f"{field} names no object in the asset bucket: {s3_uri}")
        logger.warning(f"Could not confirm {field} exists ({code}), continuing: {s3_uri}")
    except Exception as e:
        logger.warning(f"Could not confirm {field} exists, continuing: {e}")
    return s3_uri


def resolve_custom_environment_path(event, training_config) -> str:
    """Resolve custom environment path - supports relative or absolute S3 paths.

    Priority order:
    1. customEnvironmentPath - relative path within asset (e.g., "environments/my_env.tar.gz")
    2. customEnvironmentS3Uri - explicit full S3 URI

    Either route must resolve inside the executing asset's own bucket; the check sits at the single
    exit so both routes and any future one pass through it.

    Returns:
        Full S3 URI to the custom environment package, or empty string if not specified
    """
    bucket = event.get("bucketAsset", "")
    asset_location_key = event.get("inputAssetLocationKey", "")
    resolved = ""

    # 1. Check for relative customEnvironmentPath (preferred)
    custom_env_path = training_config.get("customEnvironmentPath")
    if custom_env_path:
        if not bucket or not asset_location_key:
            raise ValueError(
                "Cannot resolve customEnvironmentPath: pipeline is missing bucketAsset "
                "or inputAssetLocationKey. Ensure vamsExecute passes these through from the workflow."
            )
        resolved = resolve_relative_path(bucket, asset_location_key, custom_env_path)
        logger.info(f"Using customEnvironmentPath: {resolved}")

    # 2. Check for explicit customEnvironmentS3Uri
    if not resolved:
        custom_env_uri = training_config.get("customEnvironmentS3Uri")
        if custom_env_uri:
            resolved = custom_env_uri
            logger.info(f"Using customEnvironmentS3Uri: {resolved}")

    if not resolved:
        return ""

    return require_existing_object(
        "customEnvironmentS3Uri",
        require_asset_bucket("customEnvironmentS3Uri", resolved, event))


def _shown(value) -> str:
    """``value`` quoted for an error message, cut short so a long value cannot fill the message."""
    text = repr(value)
    return text if len(text) <= 120 else text[:117] + "..."


def normalize_stage_asset_path(value) -> str:
    """One ``stageAssetPaths`` entry as a path relative to the asset root.

    Returns ``""`` for the whole asset (``/``), the folder path with a trailing ``/`` for a folder,
    and the bare path otherwise, which names that file or the folder of that name. The value is
    operator-supplied (a template body or a JSON input file), so it is accepted only as a relative
    path that stays inside the asset: no URI scheme, no backslash, no control character, no ``.``,
    ``..`` or empty segment, and at most ``MAX_STAGE_ASSET_PATH_LENGTH`` characters.
    """
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"stageAssetPaths entries must be non-blank strings (received {_shown(value)})")
    path = value.strip()
    if len(path) > MAX_STAGE_ASSET_PATH_LENGTH:
        raise ValueError(
            f"stageAssetPaths entries must be at most {MAX_STAGE_ASSET_PATH_LENGTH} characters "
            f"(received one of {len(path)})")
    if _STAGE_PATH_CONTROL_CHARACTERS.search(path) or "\\" in path or _STAGE_PATH_SCHEME.match(path):
        raise ValueError(
            f"stageAssetPaths entries must be paths within the asset, not URIs, and must not hold "
            f"backslashes or control characters (received {_shown(value)})")
    path = path.lstrip("/")
    if not path:
        return ""
    segments = path.rstrip("/").split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        raise ValueError(
            f"stageAssetPaths entries must stay inside the asset: no '.', '..' or empty path "
            f"segment (received {_shown(value)})")
    return "/".join(segments) + ("/" if path.endswith("/") else "")


def stage_asset_paths(training_config) -> list:
    """The validated, de-duplicated ``stageAssetPaths`` of a run, in the order given.

    A list of paths, or one path as a string; absent, null or blank stages nothing.
    """
    value = training_config.get("stageAssetPaths")
    if value is None or (isinstance(value, str) and not value.strip()):
        return []
    entries = [value] if isinstance(value, str) else value
    if not isinstance(entries, list):
        raise ValueError(
            f"stageAssetPaths must be a list of paths within the asset (received {_shown(value)})")
    if len(entries) > MAX_STAGE_ASSET_PATHS:
        raise ValueError(
            f"stageAssetPaths names {len(entries)} paths; at most {MAX_STAGE_ASSET_PATHS} are allowed")
    paths = []
    for entry in entries:
        path = normalize_stage_asset_path(entry)
        if path not in paths:
            paths.append(path)
    total_length = encoded_length(paths)
    if total_length > MAX_STAGE_ASSET_PATHS_TOTAL_LENGTH:
        raise ValueError(
            f"stageAssetPaths entries take {total_length} characters in the job's command (a character "
            f"outside ASCII takes 6 or 12); at most {MAX_STAGE_ASSET_PATHS_TOTAL_LENGTH} are allowed")
    return paths


def encoded_length(paths) -> int:
    """Characters the paths take in the Batch job's command, which carries them JSON-encoded."""
    return sum(len(json.dumps(path)) - 2 for path in paths)


def _is_staged_key(key: str, root_prefix: str) -> bool:
    """Whether an object under the asset root is an asset file the run stages: not the root or a
    folder marker (a key ending in ``/``) and not a VAMS file preview."""
    if key.endswith("/") or len(key) <= len(root_prefix):
        return False
    return PREVIEW_FILE_MARKER not in key.rsplit("/", 1)[-1]


def list_stage_asset_files(bucket: str, root_prefix: str, paths: list) -> dict:
    """``{key: size}`` of the files the ``stageAssetPaths`` selections name under the asset root.

    A path without a trailing ``/`` names the file of that name when one exists, and the folder of
    that name otherwise or as well; its siblings that merely share the prefix (``Isaac2/`` for
    ``Isaac``) are not listed. A selection that names no file is rejected, and the listing stops as
    soon as the total passes ``MAX_STAGED_ASSET_FILES`` or ``MAX_STAGED_ASSET_BYTES``, so a whole
    bucket is never walked to find out it is too large.
    """
    client = _s3_client_for_bucket(bucket)
    paginator = client.get_paginator("list_objects_v2")
    files = {}
    total_bytes = 0

    def add(key, size):
        nonlocal total_bytes
        if key in files or not _is_staged_key(key, root_prefix):
            return False
        files[key] = size
        total_bytes += size
        if len(files) > MAX_STAGED_ASSET_FILES:
            raise ValueError(
                f"stageAssetPaths selects more than {MAX_STAGED_ASSET_FILES} files; at most "
                f"{MAX_STAGED_ASSET_FILES} are staged")
        if total_bytes > MAX_STAGED_ASSET_BYTES:
            raise ValueError(
                f"stageAssetPaths selects more than {MAX_STAGED_ASSET_BYTES // 1024 ** 3} GiB of files; "
                f"at most {MAX_STAGED_ASSET_BYTES // 1024 ** 3} GiB are staged")
        return True

    for path in paths:
        matched = False
        if path and not path.endswith("/"):
            exact = root_prefix + path
            # The exact key, when it exists, is the first key S3 lists under itself as a prefix.
            response = client.list_objects_v2(Bucket=bucket, Prefix=exact, MaxKeys=1)
            first = (response.get("Contents") or [{}])[0]
            if first.get("Key") == exact:
                add(exact, int(first.get("Size") or 0))
                matched = _is_staged_key(exact, root_prefix)
            folder = exact + "/"
        else:
            folder = root_prefix + path
        for page in paginator.paginate(Bucket=bucket, Prefix=folder):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if _is_staged_key(key, root_prefix):
                    matched = True
                    add(key, int(obj.get("Size") or 0))
        if not matched:
            raise ValueError(f"stageAssetPaths entry {_shown(path or '/')} names no file in the asset")
    return files


def unstageable_reason(relative: str):
    """Why an asset file at ``relative``, its path under the asset root, cannot be written under the
    staging folder, or ``None`` when it can. S3 keys are opaque, so a key may hold a ``.``, ``..``
    or empty segment, a NUL character, or a name longer than the container can write."""
    segments = relative.split("/")
    if any(segment in ("", ".", "..") for segment in segments):
        return "its path has a '.', '..' or empty segment"
    if "\x00" in relative:
        return "its path holds a NUL character"
    if any(len(segment.encode("utf-8")) > MAX_STAGED_NAME_BYTES for segment in segments):
        return f"a name in its path is longer than {MAX_STAGED_NAME_BYTES} bytes"
    return None


def require_stageable(files, root_prefix: str) -> None:
    """Reject a selection holding a file the container cannot write to disk: one ``unstageable_reason``
    names, or one at the same path as a folder that holds other selected files."""
    relatives = sorted(key[len(root_prefix):] for key in files)
    folders = set()
    for relative in relatives:
        reason = unstageable_reason(relative)
        if reason:
            raise ValueError(f"stageAssetPaths selects asset file {_shown(relative)}, which cannot be "
                             f"staged: {reason}")
        parts = relative.split("/")
        folders.update("/".join(parts[:i]) for i in range(1, len(parts)))
    for relative in relatives:
        if relative in folders:
            raise ValueError(f"stageAssetPaths selects asset file {_shown(relative)}, which cannot be "
                             f"staged: a folder of other selected files has the same path")


def resolve_stage_asset_files(event, training_config) -> dict:
    """The executing asset's files the container stages before the run, or ``{}`` for none.

    Paths resolve against the asset root the workflow manifest names (``bucketAsset`` +
    ``inputAssetLocationKey``), never against a location the run configuration supplies, so a run
    stages only files of the asset it executes against. They are counted and checked here, before a
    GPU node is provisioned. The container receives the root and the validated paths, whose total
    length is bounded, rather than the file list, which could pass the Batch command's size limit;
    it lists them again and re-applies the limits and checks.
    """
    paths = stage_asset_paths(training_config)
    if not paths:
        return {}

    bucket = event.get("bucketAsset", "")
    asset_location_key = event.get("inputAssetLocationKey", "")
    if not bucket or not asset_location_key:
        raise ValueError(
            "Cannot resolve stageAssetPaths: pipeline is missing bucketAsset or inputAssetLocationKey. "
            "Ensure vamsExecute passes these through from the workflow.")

    root_prefix = asset_location_key if asset_location_key.endswith("/") else asset_location_key + "/"
    files = list_stage_asset_files(bucket, root_prefix, paths)
    require_stageable(files, root_prefix)
    total_bytes = sum(files.values())
    logger.info(f"stageAssetPaths {paths}: {len(files)} files, {total_bytes} bytes")
    return {
        "rootS3Uri": f"s3://{bucket}/{root_prefix}",
        "paths": paths,
        "fileCount": len(files),
        "totalBytes": total_bytes,
    }


def with_stage_asset_files(job_config, event, training_config):
    """``job_config`` with its ``stageAssetFiles`` block when the run stages asset files; a run that
    stages none keeps the job configuration it had without the setting."""
    stage = resolve_stage_asset_files(event, training_config)
    if stage:
        job_config["stageAssetFiles"] = stage
    return job_config


def build_training_config(event, training_config, task, rl_library, physics=DEFAULT_PHYSICS):
    """Build configuration for training mode."""
    num_envs = whole_number("numEnvs", training_config.get("numEnvs"),
                            DEFAULT_NUM_ENVS_TRAIN, maximum=MAX_NUM_ENVS)
    max_iterations = whole_number("maxIterations", training_config.get("maxIterations"),
                                  DEFAULT_MAX_ITERATIONS, maximum=MAX_MAX_ITERATIONS)
    seed = whole_number("seed", training_config.get("seed"), None, minimum=None)
    job_name = event.get("jobName") or "unknown"

    # Use VAMS standard output path (asset bucket)
    output_path = event.get("outputS3AssetFilesPath", "")
    
    # Resolve custom environment path (relative or absolute)
    custom_env_s3_uri = resolve_custom_environment_path(event, training_config)

    job_config = {
        "jobName": job_name,
        "trainingConfig": {
            "mode": "train",
            "task": task,
            "numEnvs": num_envs,
            "maxIterations": max_iterations,
            "rlLibrary": rl_library,
            "physics": physics,
            "seed": seed,
        },
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath"),
        "customEnvironmentS3Uri": custom_env_s3_uri,
        "outputS3AssetFilesPath": output_path,
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }
    return with_stage_asset_files(job_config, event, training_config)


def build_evaluation_config(event, training_config, task, rl_library, physics=DEFAULT_PHYSICS):
    """Build configuration for evaluation mode.
    
    Policy file discovery (in priority order):
    1. checkpointPath - relative path within asset directory (e.g., "checkpoints/model_300.pt")
    2. policyS3Uri - explicit full S3 URI
    3. Auto-discover .pt and .pth files under the asset root (backward compatibility)
    """
    num_envs = whole_number("numEnvs", training_config.get("numEnvs"),
                            DEFAULT_NUM_ENVS_EVAL, maximum=MAX_NUM_ENVS)
    num_episodes = whole_number("numEpisodes", training_config.get("numEpisodes"),
                                DEFAULT_NUM_EPISODES, maximum=MAX_NUM_EPISODES)
    steps_per_episode = whole_number("stepsPerEpisode", training_config.get("stepsPerEpisode"),
                                     DEFAULT_STEPS_PER_EPISODE, maximum=MAX_STEPS_PER_EPISODE)
    record_video = training_config.get("recordVideo", False)

    # Policy discovery with priority: checkpointPath > policyS3Uri > auto-discover
    policy_s3_uri = None
    # The field the checkpoint came from, so a not-found names what the operator wrote. Auto-discovery
    # leaves it None: that URI came from a listing, so the object is known to be there.
    policy_field = None
    bucket = event.get("bucketAsset", "")
    asset_location_key = event.get("inputAssetLocationKey", "")

    # 1. Check for relative checkpointPath (preferred method)
    checkpoint_path = training_config.get("checkpointPath")
    if checkpoint_path:
        if not bucket or not asset_location_key:
            raise ValueError(
                "Cannot resolve checkpointPath: pipeline is missing bucketAsset or "
                "inputAssetLocationKey. Ensure vamsExecute passes these through from the workflow."
            )
        policy_s3_uri = resolve_relative_path(bucket, asset_location_key, checkpoint_path)
        policy_field = "checkpointPath"
        logger.info(f"Using checkpointPath: {policy_s3_uri}")

    # 2. Check for explicit policyS3Uri
    if not policy_s3_uri:
        policy_s3_uri = training_config.get("policyS3Uri") or training_config.get("policyPath")
        if policy_s3_uri:
            policy_field = "policyS3Uri"
            logger.info(f"Using policyS3Uri: {policy_s3_uri}")

    # 3. Fall back to auto-discovery across the entire asset root
    if not policy_s3_uri:
        policy_s3_uri = discover_policy_file(bucket, asset_location_key, rl_library)
        if policy_s3_uri:
            logger.info(f"Auto-discovered policy: {policy_s3_uri}")
    
    if not policy_s3_uri:
        raise ValueError(
            "No policy file found for evaluation. Provide one of: "
            "'checkpointPath' (relative path like 'checkpoints/model_300.pt'), "
            "'policyS3Uri' (full S3 URI), or place a .pt or .pth file in the asset."
        )

    # The checkpoint reaches torch.load in the container, so the same bucket scope applies as to the
    # custom environment package. Routes 1 and 3 resolve inside the asset bucket already; route 2 is
    # operator-supplied and is what this constrains.
    policy_s3_uri = require_asset_bucket("policyS3Uri", policy_s3_uri, event)

    # An operator-named checkpoint is confirmed present here rather than in the container, where a
    # mistyped path fails only once a GPU node is running.
    if policy_field:
        policy_s3_uri = require_existing_object(policy_field, policy_s3_uri)

    job_name = event.get("jobName") or "unknown"
    output_path = event.get("outputS3AssetFilesPath", "")
    
    # Resolve custom environment path (relative or absolute)
    custom_env_s3_uri = resolve_custom_environment_path(event, training_config)

    job_config = {
        "jobName": job_name,
        "trainingConfig": {
            "mode": "evaluate",
            "task": task,
            "numEnvs": num_envs,
            "numEpisodes": num_episodes,
            "stepsPerEpisode": steps_per_episode,
            "policyS3Uri": policy_s3_uri,
            "recordVideo": record_video,
            "rlLibrary": rl_library,
            "physics": physics,
        },
        "inputS3AssetFilePath": event.get("inputS3AssetFilePath"),
        "customEnvironmentS3Uri": custom_env_s3_uri,
        "outputS3AssetFilesPath": output_path,
        "inputMetadataS3Location": event.get("inputMetadataS3Location", ""),
        "inputConfigurationS3Location": event.get("inputConfigurationS3Location", ""),
        "externalSfnTaskToken": event.get("externalSfnTaskToken", ""),
    }
    note = physics_note(policy_s3_uri, physics)
    if note:
        job_config["trainingConfig"]["physicsNote"] = note
    return with_stage_asset_files(job_config, event, training_config)
