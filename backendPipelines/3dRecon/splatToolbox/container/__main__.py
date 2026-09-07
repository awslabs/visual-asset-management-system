# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import os
import shutil
import sys
import subprocess
import boto3
from vams_utils import manifest_io
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

def resolve_output_env(bucket_name: str, object_dir: str, job_name: str) -> tuple:
    """The (S3_OUTPUT, UUID) pair for an output-files prefix, as `main.py` expects them.

    `main.py` writes every output to "{S3_OUTPUT}/{UUID}/..." and rejects an empty UUID, so the pair
    is split to recompose to exactly the given prefix: UUID takes its last segment and S3_OUTPUT
    everything above. Outputs then land at the prefix root, leaving the workflow's output path prefix
    as the only thing that nests them — an execution's output folder is the workflow's choice, not
    the container's. `main.py` interpolates the pair at ~15 sites and is upstream-synced and
    gitignored, so this is the only durable place to fix the layout. UUID is read nowhere else here:
    the DynamoDB metrics writes it keys are all gated on DDB_TABLE_NAME, which VAMS does not set.
    """
    trimmed = str(object_dir or "").strip("/")
    parent, _, leaf = trimmed.rpartition("/")
    return f"s3://{bucket_name}/{parent}", (leaf or job_name)


def output_listing_prefix(s3_output: str, job_uuid: str) -> tuple:
    """The (bucket, key prefix) an (S3_OUTPUT, UUID) pair addresses, as `main.py` joins them.

    Derived from the pair the run was actually given rather than recomputed from the pipeline
    definition, so it cannot name a different place than the one the outputs were written to.
    """
    without_scheme = s3_output[len("s3://"):] if s3_output.startswith("s3://") else s3_output
    bucket_name, _, parent = without_scheme.partition("/")
    key_prefix = "/".join(segment for segment in (parent.strip("/"), job_uuid.strip("/")) if segment)
    return bucket_name, f"{key_prefix}/" if key_prefix else ""


def missing_output_cause(bucket_name: str, key_prefix: str, s3_client):
    """The reason a run that exited 0 wrote nothing, or None when it wrote something or cannot be read.

    Asks for a single key, since the only question is whether the prefix is empty. `main.py` exits 0
    having uploaded nothing whenever its upload steps are skipped, and the workflow's process-output
    step finds no files and records the execution as complete, so exit status alone does not tell an
    operator whether a job that ran for hours produced anything. A listing that FAILS returns None
    rather than a cause: the job role holds `s3:ListBucket` on the asset buckets, so a failure here is
    something other than an empty result, and discarding a finished GPU run over it would cost more
    than the check saves.
    """
    try:
        response = s3_client.list_objects_v2(Bucket=bucket_name, Prefix=key_prefix, MaxKeys=1)
    except Exception as error:
        print(f"Could not list s3://{bucket_name}/{key_prefix} to confirm the run wrote output: "
              f"{error}")
        return None
    if response.get('Contents'):
        return None
    return f"Pipeline exited 0 but wrote no output under s3://{bucket_name}/{key_prefix}"


# Models baked into the image by `build_models_tar.py --bake`, and where the image holds them. The
# image's own MODEL_PATH is where upstream expects a model channel to be mounted; this launch
# sequence repoints MODEL_PATH at the volume it unpacks inputs on, so the baked files are linked
# across into it.
BAKED_MODEL_DIR = '/opt/ml/input/data/model'
BAKED_MODEL_FILES = ('sam2.1_hiera_large.pt',)


def stage_baked_models(model_path: str, baked_dir: str = BAKED_MODEL_DIR) -> list:
    """The baked model files made reachable under the run's MODEL_PATH, by name.

    `remove_background_sam2.py` composes its checkpoint path as `{MODEL_PATH}/sam2.1_hiera_large.pt`,
    which is the image's MODEL_PATH only when nothing has repointed it. The u2net weights need no
    step here: `backgroundremover` reads them from `U2NET_PATH`, which the image sets to where they
    are baked. Linking rather than copying keeps a several-hundred-megabyte checkpoint out of every
    job's startup; the two paths sit on different mounts, so a copy is the fallback for a filesystem
    that refuses the link. A model absent from the image is reported and skipped — only the options
    that consume it are affected, and the rest of the run is unrelated to them.
    """
    staged = []
    for name in BAKED_MODEL_FILES:
        source = os.path.join(baked_dir, name)
        target = os.path.join(model_path, name)
        if not os.path.exists(source):
            print(f"Baked model {name} is not in the image at {source}; the options that use it "
                  f"will fail if they are enabled")
            continue
        if os.path.exists(target):
            print(f"Baked model {name} is already present at {target}")
            staged.append(name)
            continue
        try:
            os.symlink(source, target)
        except OSError as error:
            print(f"Could not link {source} to {target} ({error}); copying instead")
            shutil.copyfile(source, target)
        print(f"Staged baked model {name} at {target}")
        staged.append(name)
    return staged


METADATA_SCHEMA_VERSION_GROUPED = 2


def resolve_asset_metadata(metadata_obj: dict) -> dict:
    """The asset-level metadata of an input-metadata envelope, as a flat {key: value} config map.

    The envelope is grouped by asset (`{"schemaVersion": 2, "assets": [...]}`) and holds asset-level
    metadata as each group's `fileKey` "/" record, with database metadata in its own top-level
    section; the legacy `{"VAMS": {...}}` view carries the same values under `assetMetadata`. This
    mirrors `manifestHelper`'s projection rule: the asset scope resolves only from an envelope naming
    exactly ONE asset, since several assets leave no way to tell which one a setting belongs to.
    Anything the envelope cannot supply is reported rather than left to look like an empty asset.
    """
    if not isinstance(metadata_obj, dict):
        return {}

    if metadata_obj.get('schemaVersion') == METADATA_SCHEMA_VERSION_GROUPED and 'assets' in metadata_obj:
        assets = metadata_obj.get('assets') or []
        if len(assets) != 1:
            print(f"No asset metadata applied: the input metadata names {len(assets)} assets, "
                  f"so no single asset's settings can be selected")
            return {}
        for record in (assets[0] or {}).get('files') or []:
            if (record or {}).get('fileKey') == '/':
                return record.get('metadata') or {}
        print("No asset metadata applied: the input metadata carries no asset-level record")
        return {}

    if 'VAMS' in metadata_obj:
        return (metadata_obj.get('VAMS') or {}).get('assetMetadata') or {}

    return metadata_obj


# Ceilings on what a single input archive may extract to. The Batch instance this container runs on has
# one 200 GiB gp3 root volume, and the container's `/tmp` — where `main.py` unpacks every archive — is a
# bind mount of a directory on that volume, shared with the operating system, the container image, the
# models and the run's own outputs. The byte ceiling is therefore the volume itself: an archive that
# expands past it cannot fit whatever this code does, so the ceiling refuses no input that could have
# finished, while a ceiling above the volume would bound nothing. Splat and photogrammetry inputs are
# image sets and videos in the tens to hundreds of gigabytes and are admitted up to what the volume
# holds — a capture larger than that needs a larger volume (the launch template block device in
# `batch-gpu-pipeline.ts`), not a larger ceiling. Entry count is a separate failure mode: inode and
# directory-metadata exhaustion, which an archive of millions of near-empty files reaches while
# occupying almost no space. A COLMAP bundle of the largest capture the volume holds — on the order of
# 6,000 frames with masks and per-image sparse records — stays well inside it.
MAX_ARCHIVE_EXTRACTED_BYTES = 200 * 1024 ** 3
MAX_ARCHIVE_ENTRY_COUNT = 1000000


def enforce_archive_extraction_limits(entries,
                                     max_entry_count=MAX_ARCHIVE_ENTRY_COUNT,
                                     max_declared_bytes=MAX_ARCHIVE_EXTRACTED_BYTES):
    """The (entry count, total declared bytes) of an archive, refusing one that exceeds a ceiling.

    Reads only the central-directory metadata `zipfile` has already parsed, so the decision costs no
    disk and no extraction. Entry count is enforced here and only here: an entry has to be listed to be
    extracted at all, and inode exhaustion is reached by entries that each declare almost nothing. The
    declared byte total is an ADVISORY early reject rather than the protection — the sizes are the
    archive's own account of itself — and the ceiling it is measured against is the one
    `ExtractionByteBudget` enforces on the bytes extraction actually produces. Both counters are tested
    as the entries are walked and raise on the entry that crosses a ceiling, so an archive declaring
    millions of entries is refused without the whole list being summed. A `ValueError` is the same
    failure shape as the surrounding zip-slip check.
    """
    total_declared_bytes = 0
    entry_count = 0
    for entry in entries:
        entry_count += 1
        if entry_count > max_entry_count:
            raise ValueError(
                f"Archive entry count exceeds the extraction limit of {max_entry_count} entries")
        total_declared_bytes += _declared_size(entry, 'file_size')
        if total_declared_bytes > max_declared_bytes:
            entry_name = getattr(entry, 'filename', '')
            raise ValueError(
                f"Archive declares more than the extraction limit of {max_declared_bytes} "
                f"bytes, reached at entry '{entry_name}'")
    return entry_count, total_declared_bytes


def _declared_size(entry, attribute: str) -> int:
    """The non-negative byte count an archive entry declares, treating anything unusable as zero."""
    try:
        return max(int(getattr(entry, attribute, 0) or 0), 0)
    except (TypeError, ValueError):
        return 0


class ExtractionByteBudget:
    """The bytes one extraction has actually produced, refusing to let it pass a ceiling.

    Bounds what an archive writes rather than what it says it will write: `metered` wraps the member
    handle the extraction copies from, so every byte counted is a byte on its way into a file on the
    volume, whatever the central directory declared for that entry. The total runs across the whole
    extraction, so a payload split over many entries reaches the same ceiling, and the refusal lands on
    the read that crosses it — part way through a member, with the rest still unwritten.
    """

    def __init__(self, max_bytes=MAX_ARCHIVE_EXTRACTED_BYTES):
        self.max_bytes = max_bytes
        self.extracted_bytes = 0

    def metered(self, member, name=''):
        """The member handle, charging every byte read out of it against this budget."""
        return _MeteredArchiveMember(member, self, name)

    def charge(self, byte_count, name=''):
        self.extracted_bytes += byte_count
        if self.extracted_bytes > self.max_bytes:
            raise ValueError(
                f"Archive extraction exceeds the extraction limit of {self.max_bytes} bytes, "
                f"reached while writing entry '{name}'")


class _MeteredArchiveMember:
    """An archive member handle that charges what it reads to an `ExtractionByteBudget`.

    Covers the read methods an extraction copies with (`read`, `read1`, `readinto`); everything else is
    the member's own.
    """

    def __init__(self, member, budget, name):
        self._member = member
        self._budget = budget
        self._name = name

    def read(self, *args, **kwargs):
        data = self._member.read(*args, **kwargs)
        self._budget.charge(len(data), self._name)
        return data

    def read1(self, *args, **kwargs):
        data = self._member.read1(*args, **kwargs)
        self._budget.charge(len(data), self._name)
        return data

    def readinto(self, buffer):
        byte_count = self._member.readinto(buffer)
        self._budget.charge(byte_count or 0, self._name)
        return byte_count

    def __enter__(self):
        self._member.__enter__()
        return self

    def __exit__(self, *exception):
        return self._member.__exit__(*exception)

    def __getattr__(self, attribute):
        return getattr(self._member, attribute)


_MEMBER_OPEN_UNSHADOWED = object()


def extract_within_limits(archive, extractall, *args, **kwargs):
    """One archive extraction, bounded by the entry ceiling and by the bytes it actually writes.

    The byte ceiling is charged against the member handles the extraction copies from, by shadowing
    `open` on the archive being extracted for the duration of the call: `zipfile` reads a member through
    it and writes what it reads to the target file, so the running total is the extraction's real
    footprint on the volume rather than the central directory's account of what that footprint will be.
    Shadowing the one archive rather than `ZipFile` leaves every other archive and every read that is
    not this extraction unmetered. Each extraction carries its own budget: `main.py` unpacks into a
    single temp directory under `DATASET_PATH`, so what the volume has to hold is the largest single
    extraction rather than the sum of them.
    """
    enforce_archive_extraction_limits(archive.infolist())
    budget = ExtractionByteBudget(MAX_ARCHIVE_EXTRACTED_BYTES)
    unmetered_open = archive.open
    shadowed_open = archive.__dict__.get('open', _MEMBER_OPEN_UNSHADOWED)

    def metered_open(name, *open_args, **open_kwargs):
        return budget.metered(unmetered_open(name, *open_args, **open_kwargs),
                              getattr(name, 'filename', name))

    archive.open = metered_open
    try:
        return extractall(*args, **kwargs)
    finally:
        if shadowed_open is _MEMBER_OPEN_UNSHADOWED:
            archive.__dict__.pop('open', None)
        else:
            archive.open = shadowed_open


FAILURE_CAUSE_MAX_LENGTH = 256


def failure_cause(error) -> str:
    """The `SendTaskFailure` cause for a failed `main.py` run, within the length the peer pipelines use.

    Carries the exit status rather than the launched command: the command is the launcher source, which
    would fill the whole cause and leave the status out of it.
    """
    cause = ("Pipeline execution failed with exit status "
             f"{getattr(error, 'returncode', 'unknown')}")
    return cause[:FAILURE_CAUSE_MAX_LENGTH]


# `main.py` extracts the input archive through `zipfile.ZipFile.extractall`, at more than one site and
# entirely inside the upstream-synced source tree. It runs as a child process, so the child installs
# the limits around that method before executing the script: the wrapper is what applies
# extract_within_limits to every extraction, wherever upstream performs it, so the entry ceiling and the
# running total of extracted bytes cover each one. `run_path` with `run_name='__main__'` reproduces
# `python main.py` — it sets `sys.argv[0]` and `__file__` to the script and puts its directory first on
# `sys.path`, which is what `main.py` resolves `config.json` and its sibling modules against. The entry
# module loads under its own name, so `main()` does not re-run.
_GUARDED_MAIN_LAUNCHER = """
import importlib.util
import runpy
import zipfile

_spec = importlib.util.spec_from_file_location('vams_container_entry', {entry_path!r})
_entry = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_entry)

_extractall = zipfile.ZipFile.extractall


def _limited_extractall(self, *args, **kwargs):
    return _entry.extract_within_limits(self, _extractall.__get__(self), *args, **kwargs)


zipfile.ZipFile.extractall = _limited_extractall
print('Archive extraction limits active: %d extracted bytes / %d entries'
      % (_entry.MAX_ARCHIVE_EXTRACTED_BYTES, _entry.MAX_ARCHIVE_ENTRY_COUNT))
runpy.run_path({script_path!r}, run_name='__main__')
"""


# Config keys this launch sequence owns. They are members of the runtime allowlist, but the values
# offered for them arrive from an asset's own metadata and from a template's config body, both of
# which an operator edits: `LOCAL_DEBUG` turns off the input download and every output upload while
# the run still exits 0, `CODE_PATH` moves the directory `main.py` resolves its scripts and
# checkpoints against, and `TASK_TOKEN` is the workflow callback. The input and output pair is set
# further down from the pipeline definition. A value offered for any of these is dropped rather than
# applied, so the allowlist being upstream-synced does not decide what an asset can reach.
RESERVED_CONFIG_KEYS = frozenset({
    'CODE_PATH',
    'DATASET_PATH',
    'MODEL_PATH',
    'LOCAL_DEBUG',
    'TASK_TOKEN',
    'S3_INPUT',
    'S3_OUTPUT',
    'UUID',
    'FILENAME',
})


def set_config_parameters(params: dict, metadata: dict):
    """
    Set environment variables for valid config parameters.
    Metadata takes priority over parameters if both exist.
    """
    # Load valid config parameters
    try:
        with open('config.json', 'r') as f:
            config_keys = set(json.load(f).keys())
    except:
        print("Warning: Could not load config.json")
        return

    params = params if isinstance(params, dict) else {}
    metadata = metadata if isinstance(metadata, dict) else {}
    print(f"Input parameters: {params}")
    print(f"Input metadata: {metadata}")

    # Combine with metadata priority
    combined = {**params, **metadata}
    print(f"Combined parameters and metadata: {combined}")
    
    # Set environment variables for valid config keys only
    for key, value in combined.items():
        if key in RESERVED_CONFIG_KEYS:
            # The key without its value: one of these is the workflow callback token
            print(f"Skipping {key} (set by the container, not by an input)")
        elif key in config_keys:
            os.environ[key] = str(value)
            source = "metadata" if key in metadata else "parameters"
            print(f"Set config {key}={value} (from {source})")
        else:
            print(f"Skipping {key}={value} (not in config.json)")

def main():
    # Debug: Print all available inputs
    print(f"Command line arguments: {sys.argv}")
    print(f"Environment variables:")
    for key, value in os.environ.items():
        if key.startswith(('INPUT_', 'VAMS_', 'AWS_', 'TASK_')):
            print(f"  {key}={value}")
    
    # Try to get pipeline definition from command line or environment
    pipeline_json = None
    if len(sys.argv) >= 2 and sys.argv[1].strip():
        arg = sys.argv[1].strip()
        # Check if it's a file path
        if arg.startswith('/') and arg.endswith('.json'):
            print(f"Reading pipeline definition from file: {arg}")
            try:
                with open(arg, 'r') as f:
                    pipeline_json = f.read()
                print(f"Successfully read pipeline definition from file")
            except Exception as e:
                print(f"Error reading pipeline file {arg}: {e}")
                sys.exit(1)
        else:
            pipeline_json = arg
            print(f"Using pipeline definition from command line argument")
    elif os.environ.get('PIPELINE_DEFINITION'):
        pipeline_json = os.environ['PIPELINE_DEFINITION']
        print(f"Using pipeline definition from PIPELINE_DEFINITION environment variable")
    
    if not pipeline_json:
        print("Error: No pipeline definition provided in arguments or environment")
        sys.exit(1)
    
    # Parse the VAMS pipeline JSON
    try:
        pipeline_def = json.loads(pipeline_json)
        print(f"Successfully parsed pipeline definition")
    except json.JSONDecodeError as e:
        print(f"Failed to parse pipeline definition as JSON: {e}")
        print(f"Raw content (first 200 chars): '{pipeline_json[:200]}'")
        sys.exit(1)
    
    # The pipeline definition carries the metadata + input-configuration S3 locations; read each from S3
    input_metadata_s3_location = pipeline_def.get('inputMetadataS3Location', '')
    input_configuration_s3_location = pipeline_def.get('inputConfigurationS3Location', '')
    print(f"Input metadata S3 location: {input_metadata_s3_location}")
    print(f"Input configuration S3 location: {input_configuration_s3_location}")

    metadata_obj = manifest_io.fetch_metadata(input_metadata_s3_location)
    input_parameters_obj = manifest_io.fetch_input_configuration(input_configuration_s3_location)

    # Config settings come from the envelope's asset-level metadata (see resolve_asset_metadata).
    metadata_config = resolve_asset_metadata(metadata_obj)
    print(f"Asset metadata settings: {metadata_config}")

    # Store for main.py access
    if metadata_obj:
        os.environ['VAMS_INPUT_METADATA'] = json.dumps(metadata_obj)
    if input_parameters_obj:
        os.environ['VAMS_INPUT_PARAMETERS'] = json.dumps(input_parameters_obj)

    # Set config parameters from metadata and parameters (metadata takes priority)
    set_config_parameters(input_parameters_obj, metadata_config)
    
    # Extract the input file information from the first stage
    if not pipeline_def.get('stages') or len(pipeline_def['stages']) == 0:
        print("Error: No stages found in pipeline definition")
        sys.exit(1)
    
    stage = pipeline_def['stages'][0]
    input_file = stage.get('inputFile', {})
    output_files = stage.get('outputFiles', {})
    
    if not input_file or not output_files:
        print("Error: Missing inputFile or outputFiles in stage")
        sys.exit(1)
    
    # Set environment variables that main.py expects
    os.environ['S3_INPUT'] = f"s3://{input_file['bucketName']}/{input_file['objectKey']}"
    os.environ['FILENAME'] = input_file['objectKey'].split('/')[-1]

    # S3_OUTPUT + UUID recompose to the output-files prefix (see resolve_output_env), so outputs land
    # at its root and only the workflow's output path prefix nests them.
    os.environ['S3_OUTPUT'], os.environ['UUID'] = resolve_output_env(
        output_files['bucketName'], output_files['objectDir'],
        pipeline_def.get('jobName', 'pipeline-job'))

    # Force the correct paths for Batch environment
    os.environ['AWS_BATCH_JOB_ID'] = 'vams-batch-job'
    os.environ['DATASET_PATH'] = '/tmp/input/train'
    os.environ['MODEL_PATH'] = '/tmp/input/model'
    
    # Don't set MODEL_INPUT - this will skip model download in main.py
    # The container has pre-built models that should work

    # Create required directories
    os.makedirs('/tmp/input/train', exist_ok=True)
    os.makedirs('/tmp/input/model', exist_ok=True)

    # Make the models baked into the image reachable under this run's MODEL_PATH
    stage_baked_models(os.environ['MODEL_PATH'])

    # Create empty models.tar.gz so untar_gz doesn't fail
    import tarfile
    models_path = '/tmp/input/model/models.tar.gz'
    # Check if the file already exists to avoid creating it twice
    if not os.path.exists(models_path):
        with tarfile.open(models_path, 'w:gz') as tar:
            pass  # Create empty tar.gz file
        print(f"Created empty models.tar.gz at {models_path}")
    else:
        print(f"models.tar.gz already exists at {models_path}, skipping creation")
    
    print(f"Starting Splat Toolbox pipeline for: {os.environ['FILENAME']}")
    print(f"Model path: {os.environ['MODEL_PATH']}")
    print(f"Dataset path: {os.environ['DATASET_PATH']}")
    print(f"S3_INPUT: {os.environ['S3_INPUT']}")
    print(f"S3_OUTPUT: {os.environ['S3_OUTPUT']}")
    print(f"UUID: {os.environ['UUID']}")
    
    # Get task token for callback
    task_token = pipeline_def.get('externalSfnTaskToken', '')
    
    # Add the code path to Python path so main.py can import pipeline
    env = os.environ.copy()
    env['PYTHONPATH'] = '/opt/ml/code'
    
    # Call the existing main.py from the directory, under the archive extraction limits
    try:
        print("Starting main.py with real-time output...")
        launcher = _GUARDED_MAIN_LAUNCHER.format(
            entry_path=os.path.abspath(__file__), script_path='main.py')
        result = subprocess.run([sys.executable, '-c', launcher], # nosemgrep: dangerous-subprocess-use-audit
                              cwd='/opt/ml/code',
                              env=env,
                              check=True)
        print("Pipeline completed successfully")

        # A zero exit status does not mean the run uploaded anything (see missing_output_cause)
        region = os.environ.get('AWS_REGION', 'us-east-1')
        output_bucket, output_prefix = output_listing_prefix(
            os.environ['S3_OUTPUT'], os.environ['UUID'])
        no_output_cause = missing_output_cause(
            output_bucket, output_prefix, boto3.client('s3', region_name=region, config=retry_config))
        if no_output_cause:
            print(no_output_cause)
            if task_token:
                print("Sending failure callback with task token")
                sfn_client = boto3.client('stepfunctions', region_name=region, config=retry_config)
                sfn_client.send_task_failure(
                    taskToken=task_token,
                    error='Pipeline Failure',
                    cause=no_output_cause[:FAILURE_CAUSE_MAX_LENGTH]
                )
                print("Failure callback sent")
            sys.exit(1)

        # Send success callback if task token exists
        if task_token:
            print(f"Sending success callback with task token")
            sfn_client = boto3.client('stepfunctions', region_name=region, config=retry_config)
            sfn_client.send_task_success(
                taskToken=task_token,
                output=json.dumps({'status': 'Pipeline Success'})
            )
            print("Success callback sent")
        
        sys.exit(0)
    except subprocess.CalledProcessError as e:
        print(f"Pipeline failed: {e}")
        
        # Send failure callback if task token exists
        if task_token:
            print(f"Sending failure callback with task token")
            region = os.environ.get('AWS_REGION', 'us-east-1')
            sfn_client = boto3.client('stepfunctions', region_name=region, config=retry_config)
            sfn_client.send_task_failure(
                taskToken=task_token,
                error='Pipeline Failure',
                cause=failure_cause(e)
            )
            print("Failure callback sent")
        
        sys.exit(1)


if __name__ == "__main__":
    main()
