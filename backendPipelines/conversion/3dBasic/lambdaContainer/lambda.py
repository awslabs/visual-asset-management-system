# Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import codecs
import json
import boto3
import os
import shutil
import stat
import struct
import subprocess
import sys
import tempfile
import threading
import time
import conversion_service
import expansion_bounds
import incomplete_result
import referenced_files
import size_check
import units_axes
from conversion_worker import (  # noqa: F401  (re-exported: the checks the child applies)
    MESH_ONLY_OUTPUT_FORMATS, USER_ERRORS, check_exportable, geometry_counts)
from common.logger import safeLogger
from botocore.exceptions import ClientError
from boto3.s3.transfer import TransferConfig
from botocore.config import Config

# Adaptive retry with client-side rate limiting, per backendPipelines/CLAUDE.md. A pipeline lambda
# runs against throttling-prone services (Step Functions, Amazon S3, EventBridge) for the length of
# a job, so a bare client leaves it on botocore's default mode with no rate limiting and a sustained
# burst surfaces as a throttling error on the caller instead of being smoothed.
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

logger = safeLogger(service="conversionTrimeshPipeline")

PR_SET_DUMPABLE = 4


def _disable_dumpable():
    """Mark this process non-dumpable (Linux), so the files under /proc/<pid>/ become root-owned and a
    process of the same user, such as the worker child, can neither read this process's environment
    nor attach to it. Returns False where the call is not available; nothing else depends on it."""
    if not sys.platform.startswith("linux"):
        return False
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
    except Exception:  # noqa: BLE001 - a platform without it keeps the other limits
        return False


_DUMPABLE_DISABLED = _disable_dumpable()

s3_client = boto3.client('s3', config=retry_config)
s3 = boto3.resource('s3', config=retry_config)

# An asset bucket may be in another Region than this function: the manifest names each bucket's
# Region, and a request for such a bucket is signed with a client for that Region. Buckets the
# manifest does not place (the manifest's own bucket, legacy payloads) use the clients above.
_bucket_regions = {}
_s3_clients_by_region = {}
# The regional us-east-1 endpoint rather than the global s3.amazonaws.com, which an interface
# endpoint's private DNS does not cover inside a VPC.
_regional_s3_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'},
                             s3={'us_east_1_regional_endpoint': 'regional'})


def _record_bucket_region(bucket_name, region):
    if bucket_name and region and region != os.environ.get("AWS_REGION", ""):
        _bucket_regions[bucket_name] = region


def _s3_client_for_bucket(bucket_name):
    region = _bucket_regions.get(bucket_name)
    if not region:
        return s3_client
    client = _s3_clients_by_region.get(region)
    if client is None:
        client = boto3.client('s3', region_name=region, config=_regional_s3_config)
        _s3_clients_by_region[region] = client
    return client


def download(bucket_name, object_key, file_path, version_id="", etag="", expected_size=None):
    """Download the input to ``file_path``; ``version_id`` reads that version, empty the current one.

    A read of the current object is made conditional on ``etag`` (the one the size check read), so an
    object replaced after that check is refused rather than read. ``expected_size`` is the size that
    check read; a file of any other size is refused."""
    logger.info(
        "Downloading Object from S3 Bucket. Bucket: {}, Object: {}, Version: {}, File Path: {}".format(
            bucket_name, object_key, version_id or "current", file_path
        )
    )
    client = _s3_client_for_bucket(bucket_name)
    try:
        with open(file_path, "wb") as data:
            if version_id:
                client.download_fileobj(bucket_name, object_key, data, ExtraArgs={"VersionId": version_id})
            elif etag:
                # download_fileobj accepts no IfMatch, so the object is read as one stream.
                body = client.get_object(Bucket=bucket_name, Key=object_key, IfMatch=etag)["Body"]
                try:
                    shutil.copyfileobj(body, data, 8 * 1024 ** 2)
                finally:
                    body.close()
            else:
                client.download_fileobj(bucket_name, object_key, data)
    except ClientError as e:
        logger.exception(e)
        code = str(e.response.get("Error", {}).get("Code", ""))
        if version_id and code in _MISSING_VERSION_CODES:
            raise Exception(f"The input file {object_key} at version {version_id} no longer exists; "
                            f"it was deleted after the run started.")
        if etag and not version_id and code in _CHANGED_OBJECT_CODES:
            raise Exception(f"The input file {object_key} was changed while the run was starting. "
                            f"Run the conversion again.")
        raise Exception("Could not download input file from S3 bucket")
    if isinstance(expected_size, int) and not isinstance(expected_size, bool) \
            and os.path.getsize(file_path) != expected_size:
        raise Exception(f"The input file {object_key} was changed while the run was starting. "
                        f"Run the conversion again.")
    return file_path


# Error codes Amazon S3 answers a conditional read of an object that no longer matches with.
_CHANGED_OBJECT_CODES = ("PreconditionFailed", "412")


# Error codes Amazon S3 answers a read of a version that no longer exists with: NoSuchVersion or
# NoSuchKey (GET), 404 (HEAD of a deleted version), 405 / MethodNotAllowed (a delete marker).
_MISSING_VERSION_CODES = ("NoSuchVersion", "NoSuchKey", "404", "405", "MethodNotAllowed")


def input_version_id(entry):
    """The S3 version id a manifest input file is read at, or "" to read the current object.

    Mirrors ``manifestHelper.input_version_id``: the workflow records the version a run reads on each
    input file. "null" is the version S3 reports for an object written while the bucket's versioning was
    off and is passed through; "" (a file in an unversioned bucket) reads the current object."""
    value = (entry or {}).get("versionId")
    if not isinstance(value, str):
        return ""
    return value


def head_input_size(bucket_name, object_key, version_id=""):
    """The input's size in bytes at its launch version, read with a HeadObject before anything is
    downloaded."""
    return head_input(bucket_name, object_key, version_id)[0]


def head_input(bucket_name, object_key, version_id=""):
    """``(size, etag)`` of the input at its launch version, read with a HeadObject before anything is
    downloaded; the ETag is "" when the answer carries none."""
    try:
        return size_check.head_object_info(_s3_client_for_bucket(bucket_name), bucket_name, object_key, version_id)
    except ClientError as e:
        logger.exception(e)
        if version_id and str(e.response.get("Error", {}).get("Code", "")) in _MISSING_VERSION_CODES:
            raise Exception(f"The input file {object_key} at version {version_id} no longer exists; "
                            f"it was deleted after the run started.")
        raise Exception("Could not read the input file's size from S3 bucket")


def uploadV2(bucket_name, object_key, file_path):
    logger.info(
        f"Uploading Object to S3 Bucket w/ auto chunking for multi-part.\nBucket:{bucket_name}.\n:Object: {object_key}"
    )

   # Multipart upload
    try:
        GB = 1024 ** 3
        MB = 1024 ** 2
        config = TransferConfig(multipart_threshold=1*GB, max_concurrency=10,
                                multipart_chunksize=100*MB, use_threads=True
                                )
        _s3_client_for_bucket(bucket_name).upload_file(file_path, bucket_name, object_key,
                                   ExtraArgs={},
                                   Config=config,
                                   Callback=ProgressPercentage(file_path)
                                   )
    except ClientError as e:
        logger.exception(e)
        raise Exception("Could not upload output file to S3 bucket")
    return object_key
    
class ManifestReadError(RuntimeError):
    """Raised when a referenced workflow input manifest cannot be read or is not a JSON object."""


def fetch_manifest(manifest_s3_location):
    """Fetch + parse the workflow input manifest from its S3 location.

    ``None`` when no location was supplied, so a legacy payload carrying its fields inline still
    resolves. A location that IS supplied but is malformed, unreadable, or does not parse to a JSON
    object raises ``ManifestReadError``.

    Mirrors ``manifestHelper.fetch_manifest``, which the pipelines with a ``lambda/`` code asset
    vendor: the manifest is the only carrier of the input file's asset identity and of the run's
    output paths, so answering a read failure with an empty manifest downgrades the run to the
    legacy body fields — writing output to whatever path those name, or failing later with an error
    that does not mention the manifest.
    """
    if not manifest_s3_location:
        return None
    if not manifest_s3_location.startswith("s3://"):
        raise ManifestReadError(
            f"The workflow supplied a malformed input manifest location: {manifest_s3_location}")
    bucket, _, key = manifest_s3_location[len("s3://"):].partition("/")
    if not bucket or not key:
        raise ManifestReadError(
            f"The workflow supplied a malformed input manifest location: {manifest_s3_location}")
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        manifest = json.loads(body)
    except Exception as e:
        raise ManifestReadError(
            f"Could not read the workflow input manifest at {manifest_s3_location}: {e}")
    if not isinstance(manifest, dict):
        raise ManifestReadError(
            f"The workflow input manifest at {manifest_s3_location} is not a JSON object")
    return manifest


class InputConfigurationError(RuntimeError):
    """Raised when an input-configuration file exists but cannot be parsed as a JSON object."""


def fetch_input_configuration(input_configuration_s3_location):
    """Fetch + parse the per-pipeline input configuration (inputParameters) from its S3 location.

    ``{}`` when no configuration was supplied or it could not be fetched. Raises
    ``InputConfigurationError`` when the file WAS fetched but its body is not a JSON object.

    Parsed here rather than through ``fetch_manifest`` because the two carry different weight: a
    configuration is optional, so an absent one legitimately leaves the pipeline reading its target
    format from the legacy inline body field, whereas a referenced manifest is the run's only
    statement of what to read and where to write it. An unparseable configuration still raises so the
    error names the configuration; a fetch failure yields ``{}`` and, with no inline field to fall
    back to, surfaces one step later as an empty output format the format check rejects.
    """
    if not input_configuration_s3_location:
        return {}
    if not input_configuration_s3_location.startswith("s3://"):
        return {}
    bucket, _, key = input_configuration_s3_location[len("s3://"):].partition("/")
    if not bucket or not key:
        return {}
    try:
        body = s3_client.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
    except Exception as e:
        logger.warning(f"Could not read {input_configuration_s3_location}: {e}")
        return {}
    if not body or not body.strip():
        return {}
    try:
        parsed = json.loads(body)
    except ValueError as e:
        raise InputConfigurationError(
            f"The input configuration at {input_configuration_s3_location} is not valid JSON: {e}")
    if not isinstance(parsed, dict):
        raise InputConfigurationError(
            f"The input configuration at {input_configuration_s3_location} is not a JSON object")
    return parsed


def relative_subdir_from_manifest_path(relative_path):
    """The input file's subdirectory within the asset, derived from its asset-relative manifest
    path ('/parts/housing/model.obj' -> 'parts/housing'). A file at the asset root yields ''."""
    trimmed = (relative_path or "").strip("/")
    if "/" not in trimmed:
        return ""
    return trimmed.rsplit("/", 1)[0]


# The mesh formats this pipeline accepts as input: every one is a format trimesh loads with the
# container's pinned dependencies (requirements.txt / poetry.lock). COLLADA (`.dae`) needs pycollada
# and `.3mf` / `.xaml` / `.3dxml` need lxml and networkx; with none of those installed trimesh
# registers an exception wrapper in place of each loader. The registered inputFileFilters in
# vamsSchema/pipeline.json declare this same set to VAMS.
SUPPORTED_INPUT_FORMATS = {'.stl', '.obj', '.ply', '.gltf', '.glb', '.xyz', '.usd', '.usda', '.usdc', '.usdz',
                           '.dae', '.3mf', '.off', '.fbx', '.step', '.stp'}

# STEP input is opt-in (app.pipelines.useConversion3dBasic.enableStepInput): the image built without it
# holds no Open CASCADE code, and a STEP run is refused before anything is downloaded.
STEP_INPUT_FORMATS = {'.step', '.stp'}
STEP_CONFIG_KEY = "app.pipelines.useConversion3dBasic.enableStepInput"

# The formats it can produce as output. Loading a format is not the same as writing it: `.xyz` is a
# point-cloud text format trimesh reads into a PointCloud, and its xyz exporter accepts a PointCloud
# only and raises even for one, so nothing this pipeline loads can be written as `.xyz`. `.xaml` and
# `.3dxml` have no exporter at any dependency set, and the `.3mf` exporter is registered behind the
# same lxml and networkx pair as its loader, which is why the requested output type is checked against
# this set rather than against the accept list.
SUPPORTED_OUTPUT_FORMATS = {'.stl', '.obj', '.ply', '.gltf', '.glb', '.usda', '.usdc', '.usdz', '.off', '.dae',
                            '.3mf'}

class InputTextError(ValueError):
    """The text a model input is read from is not UTF-8, so the loader cannot read it."""


# trimesh reads the text of an OBJ, ASCII STL, XYZ or glTF file, and a GLB's JSON chunk, as UTF-8. When
# that fails it guesses another encoding with charset_normalizer, which this image does not install, so
# the run would end with a raw "No module named 'charset_normalizer'" (or, for a .gltf, a message about
# a file named model.gltf). That text is therefore read as UTF-8 here first, before any referenced file
# is fetched or the model is loaded, and a file whose text is not UTF-8 fails with a message saying it
# could not be read. A text file saved in another encoding fails the same way rather than being guessed
# at: with no detector installed there is no reliable way to tell its encoding, and a wrong guess would
# change the names it carries (objects, groups, materials) in the converted model while the run reported
# success. ASCII is UTF-8, so a file without accented or other non-ASCII characters is unaffected.
_NOT_UTF8 = "byte 0x{byte:02X} at offset {offset} is not UTF-8"
INPUT_TEXT_ERRORS = {
    '.obj': ("{name} could not be read as OBJ text: " + _NOT_UTF8 + ". The file is binary data or text "
             "saved in another encoding. Save it as UTF-8 OBJ text and run the conversion again."),
    '.xyz': ("{name} could not be read as XYZ text: " + _NOT_UTF8 + ". The file is binary data or text "
             "saved in another encoding. Save it as UTF-8 text and run the conversion again."),
    '.gltf': ("{name} could not be read as glTF JSON: " + _NOT_UTF8 + ". glTF JSON must be UTF-8 text. "
              "Export the model again and run the conversion again."),
    '.stl': ("{name} could not be read as STL: it is not a binary STL (its size does not match the "
             "triangle count in its header), and as ASCII STL text, " + _NOT_UTF8 + ". Export it as "
             "binary STL or as UTF-8 ASCII STL and run the conversion again."),
    '.off': ("{name} could not be read as OFF text: " + _NOT_UTF8 + ". The file is binary data or text "
             "saved in another encoding. Save it as UTF-8 OFF text and run the conversion again."),
    '.glb': ("{name} could not be read as GLB: in its JSON chunk, " + _NOT_UTF8 + ". glTF JSON must be "
             "UTF-8 text. Export the model again and run the conversion again."),
}

# Bytes read per step while checking text, so a large input is checked without a second copy of it
# in memory.
_TEXT_CHECK_CHUNK = 1024 ** 2
# A binary STL: an 80-byte header, a little-endian 32-bit triangle count, then 50 bytes per triangle.
_STL_HEADER_BYTES = 84
_STL_TRIANGLE_BYTES = 50
# A GLB starts with a 12-byte header (magic, version, length) and the 8-byte header of its first chunk.
_GLB_HEAD = struct.Struct("<4sIII4s")


def first_non_utf8_byte(handle, length=None, chunk_size=_TEXT_CHECK_CHUNK):
    """``(offset, byte)`` of the first byte that is not valid UTF-8 in the next ``length`` bytes of
    ``handle`` (to its end when ``length`` is None), the offset counted from where the read starts; or
    None when all of them are UTF-8. A character split across two reads is decoded whole."""
    decoder = codecs.getincrementaldecoder("utf-8")()
    consumed = 0
    while True:
        size = chunk_size if length is None else min(chunk_size, length - consumed)
        chunk = handle.read(size) if size > 0 else b""
        final = not chunk
        # The decoder keeps the start of a character that the previous read cut off, and reports an
        # error's position within those bytes plus this read.
        held = len(decoder.getstate()[0])
        try:
            decoder.decode(chunk, final=final)
        except UnicodeDecodeError as e:
            return consumed - held + e.start, e.object[e.start]
        consumed += len(chunk)
        if final:
            return None


def _is_binary_stl(path):
    """True when the file is laid out as a binary STL, the test trimesh applies: its size is the
    header plus 50 bytes for each triangle the header counts. trimesh reads any other STL as ASCII
    text. A binary STL's header is decoded only as metadata, and one that is not UTF-8 is dropped, so
    the header is not checked."""
    size = os.path.getsize(path)
    if size < _STL_HEADER_BYTES:
        return False
    with open(path, "rb") as handle:
        handle.seek(_STL_HEADER_BYTES - 4)
        (triangles,) = struct.unpack("<I", handle.read(4))
    return size - _STL_HEADER_BYTES == triangles * _STL_TRIANGLE_BYTES


def _glb_json_chunk(path):
    """``(offset, length)`` of a GLB's JSON chunk, or None when the file does not start with a glTF 2
    header and a JSON chunk; the loader rejects such a file with its own message."""
    with open(path, "rb") as handle:
        head = handle.read(_GLB_HEAD.size)
    if len(head) < _GLB_HEAD.size:
        return None
    magic, version, _, chunk_length, chunk_type = _GLB_HEAD.unpack(head)
    if magic != b"glTF" or version != 2 or chunk_type != b"JSON":
        return None
    return _GLB_HEAD.size, chunk_length


def check_input_text(local_path, input_extension, input_name):
    """Raise ``InputTextError`` when the text trimesh would decode from this input is not UTF-8: the
    whole of an OBJ, XYZ or glTF file, an STL that is not laid out as binary, and a GLB's JSON chunk.
    Any other input is left to the loader."""
    start, length = 0, None
    if input_extension == '.stl':
        if _is_binary_stl(local_path):
            return
    elif input_extension == '.glb':
        chunk = _glb_json_chunk(local_path)
        if chunk is None:
            return
        start, length = chunk
    elif input_extension not in INPUT_TEXT_ERRORS:
        return
    with open(local_path, "rb") as handle:
        handle.seek(start)
        found = first_non_utf8_byte(handle, length)
    if found is None:
        return
    offset, byte = found
    raise InputTextError(INPUT_TEXT_ERRORS[input_extension].format(
        name=input_name, byte=byte, offset=start + offset))


# The folder a converted file is written under when the conversion does not change the file
# extension, so a same-format re-export is a sibling of its source rather than a new version of it.
# The name matches rapidPipeline's, so every conversion pipeline places a same-format output alike.
SAME_FORMAT_OUTPUT_SUBDIR = "optimized"


def output_relative_subdir(relative_subdir, input_extension, output_extension):
    """The subdirectory the converted file is written under, relative to the output-files prefix:
    the input file's own subdirectory within the asset, plus a trailing `optimized` folder when the
    conversion does not change the file extension.

    Every format this pipeline exports is also a format it accepts, and each built-in template pins
    one target format, so a template whose target is the input file's own format is a same-extension
    run (`.glb` with Convert to GLB, `.gltf` with Convert to GLTF, `.obj` with Convert to OBJ,
    `.ply` with Convert to PLY, `.stl` with Convert to STL). The output keeps both the input's
    subdirectory and its file name, so
    in that case the output's ASSET-RELATIVE path equals the input's; the workflow's process-output
    step writes each staged output back to the output asset at exactly that relative path, so the
    write-back would land a new version of the operator's source object rather than a sibling file.
    The extra folder is what keeps the two apart, and it is a folder rather than a changed file name
    because the name is what identifies the converted model.

    The two extensions decide it on their own: the output file name is the input's stem plus the
    output extension, so equal extensions is exactly the case where the two names — and therefore the
    two relative paths — coincide. Both are compared in the lower case this pipeline normalizes them
    to, so a `.STL` source re-exported as `.stl` is also treated as same-format: its output would
    otherwise differ from its source by extension case alone. A format-changing conversion still
    lands directly beside its source, and the folder is constant per format, so it separates the
    output from the input rather than separating runs from each other (the workflow's own output path
    extension does that).
    """
    subdir = (relative_subdir or "").strip("/")
    if input_extension != output_extension:
        return subdir
    return f"{subdir}/{SAME_FORMAT_OUTPUT_SUBDIR}" if subdir else SAME_FORMAT_OUTPUT_SUBDIR


def resolve_inputs_from_manifest(data):
    """Resolve the input file path, output-files path and the input's asset-relative subdirectory
    from the workflow manifest (inputManifestS3Location), falling back to the legacy top-level body
    fields for direct/local invocations. Locations are carried as bucket + relative keys, so s3://
    URIs are reconstructed here. Returns (input_s3_asset_file_path, output_s3_asset_files_path,
    relative_subdir, input_version_id), the last being the S3 version the run reads the input at
    ("" reads the current object).

    Mirrors ``manifestHelper.resolve_inputs`` + ``enforce_single_input_file``, which the pipelines
    with a ``lambda/`` code asset vendor; this pipeline is a container image, so it reads the same
    envelope fields directly. Any change to the envelope applies to both."""
    manifest = fetch_manifest(data.get("inputManifestS3Location", ""))
    input_files = (manifest or {}).get("inputFiles") or []
    # The pipeline is registered with inputFileArity 'one' and converts a single mesh per
    # execution; more than one resolved input would be silently dropped.
    if len(input_files) > 1:
        raise ValueError(
            f"This pipeline processes a single input file per execution, but the workflow "
            f"manifest supplied {len(input_files)} input files. Multi-file input is not yet "
            f"supported for this pipeline."
        )
    input_path = ""
    relative_subdir = ""
    version_id = ""
    if input_files:
        first = input_files[0]
        if first.get("bucket") and first.get("key"):
            input_path = f"s3://{first['bucket']}/{first['key']}"
            _record_bucket_region(first["bucket"], first.get("bucketRegion", ""))
            version_id = input_version_id(first)
        relative_subdir = relative_subdir_from_manifest_path(first.get("relativePath"))
    input_path = input_path or data.get("inputS3AssetFilePath", "")
    # Output-files path reconstructed from the outputs bucket + bucket-relative files prefix.
    outputs = (manifest or {}).get("outputs", {})
    output_path = ""
    if outputs.get("bucket") and outputs.get("files"):
        output_path = f"s3://{outputs['bucket']}/{outputs['files']}"
        _record_bucket_region(outputs["bucket"], outputs.get("bucketRegion", ""))
    output_path = output_path or data.get("outputS3AssetFilesPath", "")
    # Where a run that left referenced files out writes conversion-incomplete.json: the execution's
    # results folder (the outputs bucket + its bucket-relative results prefix).
    global _results_location, _output_path_extension
    _results_location = None
    # The prefix the write-back inserts before each output file's own name (the launch's or the
    # workflow's defaultOutputFileBaseExecutionPathExtension), resolved by the execute handler. A loose
    # USD output names its files by their placed paths (usd_io.placed_reference).
    target = (manifest or {}).get("outputTarget") or {}
    extension = target.get("fileBaseExecutionPathExtension") if isinstance(target, dict) else None
    _output_path_extension = extension if isinstance(extension, str) else None
    if outputs.get("bucket") and outputs.get("results"):
        _results_location = (outputs["bucket"], outputs["results"].strip("/") + "/")
    return input_path, output_path, relative_subdir, version_id


# (bucket, prefix) of the run's results folder, set by resolve_inputs_from_manifest; None without one.
_results_location = None
# The run's output path extension from the manifest's outputTarget; None without one.
_output_path_extension = None


def exported_files(export_dir):
    """Every file the export wrote, as paths relative to ``export_dir`` with forward slashes, sorted.

    The export is read back from disk rather than assumed to be the one file it was asked for,
    because what an exporter writes depends on the model as well as the format: a `.gltf` export
    always writes its vertex data to companion `gltf_buffer_N.bin` files, and an `.obj` export writes
    `material.mtl` plus a texture image only when the mesh carries one. Uploading a fixed name
    instead leaves the model referencing companions that were never stored, which for `.gltf` means
    it cannot be opened at all.
    """
    found = []
    for root, dir_names, file_names in os.walk(export_dir):
        for name in dir_names + file_names:
            path = os.path.join(root, name)
            info = os.lstat(path)
            if stat.S_ISDIR(info.st_mode):
                continue
            relative = os.path.relpath(path, export_dir).replace(os.sep, "/")
            # Only a regular file that has no other name is uploaded: a link would put the content of
            # whatever it points at into the asset.
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError(
                    f"The converter wrote {relative}, which is not a regular file, so the conversion "
                    f"was stopped.")
            found.append(relative)
    return sorted(found)


def _proc_status(proc_root, pid):
    """``(real uid, parent pid, has an address space)`` from /proc/<pid>/status, or None."""
    try:
        with open(os.path.join(proc_root, str(pid), "status"), encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError:
        return None
    uid = ppid = None
    for line in text.splitlines():
        if line.startswith("Uid:"):
            uid = int(line.split()[1])
        elif line.startswith("PPid:"):
            ppid = int(line.split()[1])
    # Kernel threads and zombies have no address space.
    return uid, ppid, "VmSize:" in text


def _proc_start_ticks(proc_root, pid):
    try:
        with open(os.path.join(proc_root, str(pid), "stat"), encoding="utf-8", errors="replace") as handle:
            text = handle.read()
        return int(text[text.rindex(")") + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def reap_other_processes(proc_root="/proc", uid=None, own_pid=None, kill=os.kill, only=None, passes=5):
    """Kill every process of this user other than this one, its ancestors and kernel threads, and return
    the pids killed.

    Killing the worker's process group does not reach a descendant that started its own session or
    double-forked, and in a reused execution environment such a process would outlive the invocation
    next to the following one's files. Processes that started before this one (the runtime's own, an
    extension) are left alone. ``only`` limits the candidates to the pids it accepts (tests)."""
    uid = os.getuid() if uid is None else uid
    own_pid = os.getpid() if own_pid is None else own_pid
    own_start = _proc_start_ticks(proc_root, own_pid)
    protected = {own_pid}
    pid = own_pid
    while True:
        status = _proc_status(proc_root, pid)
        if status is None or not status[1] or status[1] in protected:
            break
        pid = status[1]
        protected.add(pid)
    killed = []
    for _ in range(passes):
        found = []
        try:
            names = os.listdir(proc_root)
        except OSError:
            break
        for name in names:
            if not name.isdigit() or int(name) in protected:
                continue
            candidate = int(name)
            if only is not None and not only(candidate):
                continue
            status = _proc_status(proc_root, candidate)
            if status is None or status[0] != uid or not status[2]:
                continue
            started = _proc_start_ticks(proc_root, candidate)
            if own_start is not None and started is not None and started < own_start:
                continue
            found.append(candidate)
        if not found:
            break
        for candidate in found:
            try:
                kill(candidate, 9)
                killed.append(candidate)
            except (ProcessLookupError, PermissionError, OSError):
                pass
        time.sleep(0.05)
    return killed


# Only inside a Lambda execution environment, where this process is the function's own and the other
# processes of its user are the worker's descendants; elsewhere (a developer's machine) the user's
# other processes are not the function's to stop.
def _in_lambda_environment():
    return bool(os.environ.get("AWS_LAMBDA_FUNCTION_NAME")) or bool(size_check.task_memory_bytes())


_STRAY_PROCESS_FILTER = None


def reap_stray_processes():
    if not _in_lambda_environment():
        return []
    try:
        return reap_other_processes(only=_STRAY_PROCESS_FILTER)
    except Exception as e:  # noqa: BLE001 - never replace the run's own outcome
        logger.warning(f"Could not check for leftover processes: {e}")
        return []


def remove_work_dir(path):
    """Remove the invocation's working directory, including entries the worker made unreadable, and
    fail the run when anything is left."""
    for root, dir_names, _ in os.walk(path):
        for name in dir_names:
            full = os.path.join(root, name)
            if not os.path.islink(full):
                try:
                    os.chmod(full, 0o700)
                except OSError:
                    pass
    try:
        os.chmod(path, 0o700)
    except OSError:
        pass
    shutil.rmtree(path, ignore_errors=True)
    if os.path.lexists(path):
        logger.error("The working directory could not be removed")
        raise RuntimeError("The conversion's working files could not be removed, so the run was stopped.")


# The environment the worker child starts with: nothing from the function's own, so no AWS credential
# or session token (AWS_*) and no other variable reaches the parsers. These are only what the
# interpreter and its shared libraries need to start. (The child shares the user and the network
# namespace with this process; this process is non-dumpable, which closes its /proc files.)
WORKER_ENV_PASSTHROUGH = ("PATH", "LD_LIBRARY_PATH", "PYTHONPATH", "LANG", "LC_ALL", "TZ")
# Seconds of the invocation kept for uploading the output and answering after the worker, so a
# worker that uses its whole allowance still leaves the parent time to report the failure.
WORKER_RESERVE_SECONDS = 90
# Used when the invocation's remaining time is not known (outside Lambda).
WORKER_DEFAULT_TIMEOUT_SECONDS = 900
# The most of the child's stderr kept in the log when it fails.
WORKER_STDERR_LOG_CHARS = 4000

# The monotonic time the invocation ends, set by the handler from the Lambda context.
_invocation_deadline = None

_WORKER_SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "conversion_worker.py")


def worker_environment(environ=None, work_dir="/tmp"):
    """The scrubbed environment of the worker child."""
    environ = os.environ if environ is None else environ
    # The passthrough list names what the interpreter needs; the scrub list is checked as well, so a
    # name added to the first by mistake (an AWS_* or ECS_* variable, which on an AWS Batch task carry
    # the job role's credential endpoint) still never reaches the child.
    env = {name: environ[name] for name in WORKER_ENV_PASSTHROUGH
           if name in environ and not conversion_service.is_scrubbed(name)}
    env.update({"HOME": "/tmp", "TMPDIR": work_dir, "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONNOUSERSITE": "1"})
    return env


def worker_timeout_seconds(now=None):
    """Seconds the worker may run: the invocation's remaining time less the reserve."""
    if _invocation_deadline is None:
        return WORKER_DEFAULT_TIMEOUT_SECONDS
    now = time.monotonic() if now is None else now
    return max(_invocation_deadline - now - WORKER_RESERVE_SECONDS, 1)


def run_worker(job, work_dir, timeout_seconds=None):
    """Run the load and the export in a child process (conversion_worker.py) and return when it has
    written ``job["output_file"]``; the return value is the child's result (``report`` for a
    conversion, ``references`` for a ``mode: scan`` job).

    The child gets a scrubbed environment, an address-space limit (``job["address_space_bytes"]``)
    and a wall-clock limit; on a timeout its whole process group is killed. A failure it reports is
    raised here: a ``ValueError`` as a ``ValueError`` (the text is the model's author's to act on),
    anything else as a ``RuntimeError`` with the same text."""
    timeout = worker_timeout_seconds() if timeout_seconds is None else timeout_seconds
    job_path = os.path.join(work_dir, "job.json")
    result_path = os.path.join(work_dir, "result.json")
    stderr_path = os.path.join(work_dir, "worker.stderr")
    # A result left by an earlier job of this run (the reference scan) is never read as this one's.
    if os.path.lexists(result_path):
        os.remove(result_path)
    with open(job_path, "w", encoding="utf-8") as handle:
        json.dump(job, handle)
    with open(stderr_path, "wb") as stderr:
        process = subprocess.Popen(
            [sys.executable, _WORKER_SCRIPT, job_path, result_path],
            env=worker_environment(work_dir=work_dir), cwd=work_dir, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=stderr, start_new_session=True)
        try:
            process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_group(process)
            raise conversion_service.WorkerOutOfTime(
                f"The conversion of {job['input_name']} did not finish within {int(timeout)} seconds "
                f"and was stopped. Reduce the model's size and run the conversion again.")
        finally:
            # Nothing the worker started outlives it, including what left its process group.
            _kill_group(process)
            reap_stray_processes()

    result = None
    try:
        with open(result_path, encoding="utf-8") as handle:
            result = json.load(handle)
    except (OSError, ValueError):
        pass
    if result is not None and result.get("ok"):
        global _peak_worker_memory_mib
        peak = result.get("peakMemoryMiB")
        if isinstance(peak, (int, float)) and not isinstance(peak, bool):
            _peak_worker_memory_mib = max(peak, _peak_worker_memory_mib or 0)
        return result
    if result is None:
        _log_worker_stderr(stderr_path)
        code = process.returncode
        detail = f"was killed by signal {-code}" if code is not None and code < 0 else f"exited with status {code}"
        # SIGKILL is how a container that reaches its memory limit loses the child (the limit is hit
        # before the child's own address-space limit), on Lambda and on an AWS Batch task alike.
        error_type = conversion_service.WorkerKilledForMemory if code == -9 else RuntimeError
        raise error_type(
            f"The conversion of {job['input_name']} stopped unexpectedly: the converter {detail}. "
            f"A model larger than the {_memory_owner()}'s memory is the usual cause.")
    if result.get("type") == "MemoryError":
        memory = job.get("address_space_bytes")
        limit = f" ({memory / 1024 ** 2:,.0f} MB)" if memory else ""
        raise conversion_service.WorkerKilledForMemory(
            f"{job['input_name']} needs more memory to convert than this {_memory_owner()} has{limit}. "
            f"Reduce the model's size and run the conversion again.")
    message = _without_work_dir(str(result.get("message") or "The conversion failed"), work_dir)
    if result.get("type") not in USER_ERRORS:
        # The child printed the traceback to its stderr; the message alone does not say where it failed.
        # The detail stays in the log; the run's error does not carry it.
        logger.error(f"Conversion worker failed with {str(result.get('type'))[:100]}: {message[:2000]}")
        _log_worker_stderr(stderr_path)
        raise RuntimeError(f"The conversion of {job['input_name']} failed unexpectedly. "
                           f"The {_memory_owner()}'s log has the detail.")
    raise ValueError(message)


def _without_work_dir(message, work_dir):
    """``message`` with the invocation's working directory removed from any path in it."""
    for prefix in {work_dir, os.path.realpath(work_dir)}:
        message = message.replace(prefix.rstrip("/") + "/", "").replace(prefix, "")
    return message


def _kill_group(process):
    try:
        os.killpg(process.pid, 9)
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _log_worker_stderr(path):
    try:
        with open(path, "rb") as handle:
            tail = handle.read()[-WORKER_STDERR_LOG_CHARS:].decode("utf-8", errors="replace")
    except OSError:
        return
    if tail.strip():
        logger.error(f"Conversion worker stderr: {tail}")


# The phase timer of the run in progress (read by convert() for conversion-run.json).
_phase_timer = conversion_service.PhaseTimer()
# The largest peak resident memory (MiB) a worker of the run in progress reported; None before one has.
_peak_worker_memory_mib = None


def _memory_owner():
    """What owns the memory a message names: the job on an AWS Batch task, the function on Lambda."""
    return "job" if size_check.compute_tier() == size_check.TIER_BATCH else "function"


def _escalation(reason, message, name, input_bytes, input_extension, output_filetype, resource=None):
    """The named result a run returns instead of an error when it should continue on AWS Batch."""
    required = None
    if input_bytes is not None:
        required = -(-size_check.required_memory_bytes(input_extension, input_bytes, output_filetype) // size_check.MB)
    memory = size_check.memory_limit_bytes()
    return conversion_service.escalation_result(
        reason, message, input_name=name, input_bytes=input_bytes,
        pair=f"{input_extension} to {output_filetype}", required_memory_mib=required,
        limit_bytes_by_task_mib=size_check.limits_by_task_memory(input_extension, output_filetype),
        min_task_memory_mib=(memory // size_check.MB) if memory else 0, resource=resource)


def _uncompressed_escalation(message, name, uncompressed_bytes, input_extension, output_filetype):
    """The named result of a compressed package (3MF) whose declared uncompressed size is over the
    function's memory: ``inputBytes`` is the declared uncompressed size and the limits are per
    uncompressed byte, so the router compares like with like."""
    required = -(-size_check.required_memory_bytes_uncompressed(
        input_extension, uncompressed_bytes, output_filetype) // size_check.MB)
    memory = size_check.memory_limit_bytes()
    return conversion_service.escalation_result(
        conversion_service.REASON_SIZE, message, input_name=name, input_bytes=uncompressed_bytes,
        pair=f"{input_extension} to {output_filetype}", required_memory_mib=required,
        limit_bytes_by_task_mib=size_check.uncompressed_limits_by_task_memory(input_extension, output_filetype),
        min_task_memory_mib=(memory // size_check.MB) if memory else 0, resource=size_check.MEMORY_RESOURCE)


def convert_input_output(input_path, output_path, output_filetype, relative_subdir="", input_version="", options=None,
                         allow_escalation=False):
    """Convert the input and upload the output; returns the completion message of a run that left
    referenced files out (None otherwise). With ``allow_escalation`` on the Lambda tier, a run the
    function cannot finish (an input over its limits, a worker that ran out of memory or time, a
    referenced file over its limits) raises ``EscalationRequired`` carrying the named result instead of
    the error."""
    escalation_allowed = bool(allow_escalation) and size_check.compute_tier() == size_check.TIER_LAMBDA
    global _peak_worker_memory_mib
    _peak_worker_memory_mib = None
    _phase_timer.reset()
    _phase_timer.phase("head")
    input_bucket, input_key = input_path.replace("s3://", "").split("/", 1)
    output_bucket, output_key = output_path.replace("s3://", "").split("/", 1)
    logger.info(f"Converting s3://{input_bucket}/{input_key} to {output_filetype}")
    logger.info(f"Output files prefix: s3://{output_bucket}/{output_key}")

    #Folder check
    if (input_key.endswith("/")):
        raise ValueError("Input S3 URI cannot be a folder")

    # Check input and output formats. Extensions are compared case-insensitively to match the
    # registered inputFileFilters, which match a file's extension regardless of case.
    input_s3_asset_file_root, input_s3_asset_extension = os.path.splitext(input_key)
    input_s3_asset_extension = input_s3_asset_extension.lower()
    output_filetype = (output_filetype or "").lower()
    if (not input_s3_asset_extension or input_s3_asset_extension == '' or input_s3_asset_extension not in SUPPORTED_INPUT_FORMATS):
        raise ValueError(f"Input format {input_s3_asset_extension} not supported by Trimesh pipeline")
    if output_filetype not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(f"Output format {output_filetype} not supported by Trimesh pipeline")
    if input_s3_asset_extension in STEP_INPUT_FORMATS and \
            os.environ.get("ENABLE_STEP_INPUT", "").strip().lower() != "true":
        raise ValueError(
            f"STEP input is not enabled in this deployment, so {os.path.basename(input_key)} was not converted. "
            f"An administrator can set {STEP_CONFIG_KEY} to true in the deployment configuration and deploy "
            f"again; STEP is read with Open CASCADE, which the default image does not include.")
    # The unit and up-axis options are checked here, before anything is downloaded, so an illegal
    # combination (a target glTF cannot have, a target with no known source) fails naming the option.
    options = options if isinstance(options, units_axes.Options) else units_axes.Options.from_config(options)
    units_axes.validate_request(input_s3_asset_extension, output_filetype, options)

    # The input's size is checked before anything is downloaded: the function's memory and working
    # disk set what it can convert (see size_check.py), and an input over them fails here naming the
    # size, the limit and the resource, instead of ending in an out-of-memory kill or a full disk. The
    # same limit, less the input, bounds the files the input references.
    input_size, input_etag = head_input(input_bucket, input_key, input_version)
    input_name = os.path.basename(input_key)
    # A limit of the input's own format that no memory lifts is refused here on both tiers, and never
    # escalated.
    conversion_service.check_format_limit(input_name, input_s3_asset_extension, input_size)
    try:
        size_limit = size_check.check_size(
            input_name, input_s3_asset_extension, input_size,
            memory_bytes=size_check.memory_limit_bytes(), disk_free_bytes=size_check.free_disk_bytes(tempfile.gettempdir()),
            output_extension=output_filetype)
    except size_check.InputTooLargeError as error:
        if not escalation_allowed:
            raise
        raise conversion_service.EscalationRequired(_escalation(
            conversion_service.REASON_SIZE, str(error), input_name, error.total_bytes or input_size,
            input_s3_asset_extension, output_filetype, error.resource)) from error

    # Download, convert and upload inside a working directory of this invocation's own, removed
    # before returning either way. The Lambda execution environment is reused between invocations
    # and its /tmp is a fixed budget set by the function's ephemeralStorageSize (see
    # conversion3dBasicFunctions.ts), so files left behind reduce what the next conversion has to
    # work with until one fails on space rather than on its own content.
    #
    # The download and the export get separate subdirectories because everything the export
    # directory holds is uploaded as output: the input has to be somewhere the upload cannot see it.
    work_dir = tempfile.mkdtemp()
    try:
        _phase_timer.phase("download")
        input_dir = os.path.join(work_dir, 'input')
        export_dir = os.path.join(work_dir, 'export')
        os.makedirs(input_dir)
        os.makedirs(export_dir)

        # Download the input at its own path within its asset, then the files it references beside
        # it (glTF/GLB buffers and images, an OBJ's material library and the textures it names, a
        # PLY's texture), so its relative references resolve locally as they do in the asset. Every
        # reference is validated against the input's asset prefix before anything is fetched; a buffer
        # that cannot be fetched fails the run, and a material library or texture that cannot is
        # skipped with a warning. The loader is given a resolver that serves only the fetched files,
        # so no reference reaches anything else on the local disk.
        root_key, input_relative_path = referenced_files.confinement_root(input_key, relative_subdir)
        temp_file = referenced_files.local_path_for(input_dir, input_relative_path)
        if temp_file is None:
            raise ValueError(f"Input S3 key {input_key} cannot be staged locally")
        os.makedirs(os.path.dirname(temp_file), exist_ok=True)
        # The input is read at the run's launch version; the files it references as they are now.
        download(input_bucket, input_key, temp_file, **({"version_id": input_version} if input_version else {}),
                 **({"etag": input_etag} if input_etag and not input_version else {}),
                 expected_size=input_size)
        # An input whose text is not UTF-8 fails here, before its references are read or fetched.
        check_input_text(temp_file, input_s3_asset_extension, os.path.basename(input_key))
        # A package compressed on disk (3MF) needs memory for what it holds, not for its zip: its
        # declared uncompressed size is read from the central directory and checked before any part
        # is parsed.
        if size_check.uncompressed_expansion_factor(input_s3_asset_extension) is not None:
            try:
                size_check.check_uncompressed_size(
                    input_name, input_s3_asset_extension, size_check.declared_uncompressed_bytes(temp_file),
                    memory_bytes=size_check.memory_limit_bytes(), output_extension=output_filetype)
            except size_check.InputTooLargeError as error:
                # Memory-driven like the size check before the download, so it escalates the same way,
                # but only when the largest AWS Batch job size would admit the declared size; otherwise
                # it stays the refusal it is on every tier.
                largest = max(size_check.uncompressed_limits_by_task_memory(
                    input_s3_asset_extension, output_filetype).values(), default=None)
                if not escalation_allowed or largest is None or error.total_bytes > largest:
                    raise
                raise conversion_service.EscalationRequired(_uncompressed_escalation(
                    str(error), input_name, error.total_bytes, input_s3_asset_extension,
                    output_filetype)) from error
        memory_bytes = size_check.memory_limit_bytes()
        handling = options.resolved_reference_handling(output_filetype)
        scene_input = input_s3_asset_extension in referenced_files.USD_EXTENSIONS or input_s3_asset_extension == ".dae"
        _phase_timer.phase("references")

        def scan(batch):
            # The scanned formats' references are read in a worker child, like the conversion itself.
            result = run_worker({"mode": "scan", "files": [list(item) for item in batch],
                                 "input_name": os.path.basename(input_key),
                                 "address_space_bytes": size_check.address_space_limit_bytes()}, work_dir)
            return result.get("references") or {}

        fetched = referenced_files.fetch_referenced_files(
            _s3_client_for_bucket(input_bucket), input_bucket, root_key, input_relative_path,
            temp_file, input_dir, limits=referenced_files.FetchLimits(
                # Files that carry geometry (buffers, material libraries) are bounded by what is left
                # of the input's limit; texture images by their decoded pixels against the memory the
                # geometry leaves, not by their file size.
                max_geometry_bytes=(size_limit - input_size) if size_limit is not None else None,
                image_budget=(lambda geometry_bytes: size_check.texture_budget_bytes(
                    input_s3_asset_extension, geometry_bytes, memory_bytes, output_filetype)) if memory_bytes else None,
                # selected-only follows no reference: each is reported, and a glTF buffer the model
                # cannot load without still fails the run.
                max_depth=0 if handling == "selected-only" else (
                    referenced_files.SCENE_MAX_DEPTH if scene_input else referenced_files.DEFAULT_MAX_DEPTH),
                not_followed_reason=("not followed: REFERENCE_HANDLING is selected-only"
                                     if handling == "selected-only" else None)),
            logger=logger, scan=scan)
        skipped_entries = list(getattr(fetched, "skipped_entries", None) or [])
        if escalation_allowed and getattr(fetched, "skipped_for_size_count", 0):
            # Files the model references were left out for size: a larger job can include them. The job is
            # sized from the input and the declared sizes of every referenced file found (fetched or left
            # out), so the larger run does not skip them again.
            raise conversion_service.EscalationRequired(_escalation(
                conversion_service.REASON_SIZE,
                f"{input_name} references files the function's size limits leave out.", input_name,
                input_size + max(getattr(fetched, "declared_bytes", 0) or 0, 0), input_s3_asset_extension,
                output_filetype, size_check.MEMORY_RESOURCE))
        # MISSING_REFERENCES fail stops here, before the conversion, naming the files.
        if options.missing_references == "fail" and skipped_entries:
            raise ValueError(incomplete_result.refusal(os.path.basename(input_key), skipped_entries))

        # Export mesh to output format, under the name it will carry in the asset: the input file's
        # own name with the output extension. The load and the export run in a child process (see
        # run_worker), which writes the file to the export directory.
        outputFileName, _ = os.path.splitext(os.path.basename(input_key)) #get the original file name without extension
        outputFileName = f"{outputFileName}{output_filetype}" #add final output extension
        output_file = os.path.join(export_dir, outputFileName)
        _phase_timer.phase("convert")
        worker_result = run_worker({
            "input_file": temp_file,
            "input_dir": input_dir,
            "input_relative_path": input_relative_path,
            "by_reference": fetched.by_reference,
            "output_file": output_file,
            "output_filetype": output_filetype,
            "options": {key: getattr(options, field) for key, (field, _) in units_axes.OPTION_KEYS.items()},
            "output_path_extension": _output_path_extension,
            "input_name": os.path.basename(input_key),
            "address_space_bytes": size_check.address_space_limit_bytes(),
            "tool_timeout_seconds": int(worker_timeout_seconds()),
            "max_scene_nodes": expansion_bounds.max_scene_nodes(memory_bytes),
            "file_size_bytes": size_check.file_size_limit_bytes(size_check.free_disk_bytes(work_dir)),
            # The triangle budget of the scene walks: the constant one on Lambda, the task's own on Batch.
            "max_triangles": size_check.triangle_budget(output_extension=output_filetype),
        }, work_dir) or {}
        _phase_timer.phase("upload")
        removed = ((worker_result.get("report") or {}).get("removed")) or []
        if removed:
            logger.info(f"{len(removed)} name(s) a USD layer holds were removed before it was opened: "
                        + "; ".join(f"{str(name)[:100]!r} ({reason})" for name, reason, *_ in removed[:10]))

        # Upload output file to S3. The converted file keeps the input file's subdirectory within the
        # asset so the write-back step places it beside the input rather than at the asset root. A
        # conversion that does not change the file extension gains one further folder, so the write-back
        # cannot resolve to the input's own key.
        output_subdir = output_relative_subdir(
            relative_subdir, input_s3_asset_extension, output_filetype)
        if not output_key.endswith("/"):
            output_key += "/"
        if output_subdir:
            output_key = f"{output_key}{output_subdir}/"

        exported = exported_files(export_dir)
        if outputFileName not in exported:
            raise RuntimeError(
                f"The Trimesh export of {outputFileName} produced no such file "
                f"(the export directory holds {exported or 'nothing'})")

        # An export that emitted companion files is placed in a folder of its own, named after the
        # converted model. Companions are referenced by the model RELATIVE to itself and their names
        # come from the exporter rather than from the model — a `.gltf`'s buffers are always
        # `gltf_buffer_N.bin` and a textured `.obj`'s material is always `material.mtl` — so two
        # conversions landing in one asset directory would otherwise overwrite each other's
        # companions and leave both models pointing at one set. Grouping keeps each set with its own
        # model, and keeps the references resolving, since the companions stay beside it.
        # A single-file export keeps its place directly beside the source.
        if len(exported) > 1:
            output_key = f"{output_key}{os.path.splitext(outputFileName)[0]}/"
        for relative_name in exported:
            uploadV2(output_bucket, f"{output_key}{relative_name}",
                     os.path.join(export_dir, relative_name)) #upload to storage
        _phase_timer.stop()
    except (conversion_service.WorkerKilledForMemory, conversion_service.WorkerOutOfTime) as error:
        _phase_timer.stop()
        if not escalation_allowed:
            if size_check.compute_tier() == size_check.TIER_LAMBDA:
                # The function reports the error type by name to the workflow, and it has always been
                # RuntimeError: the named subclass is for the escalation and the Batch task's log line.
                raise RuntimeError(str(error)) from error
            raise
        killed = isinstance(error, conversion_service.WorkerKilledForMemory)
        raise conversion_service.EscalationRequired(_escalation(
            conversion_service.REASON_MEMORY if killed else conversion_service.REASON_TIME, str(error),
            input_name, input_size, input_s3_asset_extension, output_filetype,
            size_check.MEMORY_RESOURCE if killed else None)) from error
    except referenced_files.ReferencedFileError as error:
        _phase_timer.stop()
        if not (escalation_allowed and error.for_size):
            raise
        raise conversion_service.EscalationRequired(_escalation(
            conversion_service.REASON_SIZE, str(error), input_name,
            input_size + (error.needed_bytes or 0), input_s3_asset_extension, output_filetype,
            size_check.MEMORY_RESOURCE)) from error
    finally:
        reap_stray_processes()
        remove_work_dir(work_dir)

    logger.info("Conversion complete")
    if skipped_entries and any(not entry.get("forSize") for entry in skipped_entries):
        name = os.path.basename(input_key)
        incomplete_result.log(logger, skipped_entries)
        write_incomplete_result(name, input_relative_path, skipped_entries)
        return incomplete_result.message(name, skipped_entries)
    if skipped_entries:
        write_incomplete_result(os.path.basename(input_key), input_relative_path, skipped_entries)
    return incomplete_message(os.path.basename(input_key), fetched)


def write_incomplete_result(input_name, input_relative_path, entries):
    """Write conversion-incomplete.json under the run's results folder. Best effort: without a results
    location, or when the write fails, the run keeps its output and its message; the log has the list."""
    if not _results_location:
        logger.info("No results location for this run; the incomplete-conversion list is in the log only")
        return
    bucket, prefix = _results_location
    try:
        _s3_client_for_bucket(bucket).put_object(
            Bucket=bucket, Key=prefix + incomplete_result.INCOMPLETE_RESULT_FILENAME,
            Body=incomplete_result.document(input_name, input_relative_path, entries),
            ContentType="application/json")
        logger.info(f"Incomplete-conversion result written: {incomplete_result.INCOMPLETE_RESULT_FILENAME}")
    except Exception as e:  # noqa: BLE001 - the conversion's outcome stands
        logger.warning(f"Could not write the incomplete-conversion result ({type(e).__name__}); the list is "
                       f"in the run log")


# Files named in the completion message; the rest are counted.
INCOMPLETE_MESSAGE_FILES = 5


def incomplete_message(input_name, fetched):
    """The completion message of a run that left referenced files out because of a size limit, or
    None when none was left out. The run still succeeds, so a model that lost its textures is not
    reported as a plain success."""
    count = getattr(fetched, "skipped_for_size_count", 0)
    names = getattr(fetched, "skipped_for_size", None) or []
    if not count or not isinstance(count, int):
        return None
    shown = "; ".join(names[:INCOMPLETE_MESSAGE_FILES])
    more = f"; and {count - INCOMPLETE_MESSAGE_FILES} more" if count > INCOMPLETE_MESSAGE_FILES else ""
    noun = "file" if count == 1 else "files"
    return (f"Converted {input_name}, but {count} referenced {noun} could not be included because of "
            f"the function's size limits, so the converted model is incomplete: {shown}{more}. "
            f"Reduce the size of the referenced files and run the conversion again.")


def remember_attempt_deadline_epoch(epoch_seconds, now_wall=None, now_monotonic=None):
    """Record when an AWS Batch attempt ends (wall-clock epoch seconds), for the worker's time limit."""
    global _invocation_deadline
    now_wall = time.time() if now_wall is None else now_wall
    now_monotonic = time.monotonic() if now_monotonic is None else now_monotonic
    _invocation_deadline = now_monotonic + (float(epoch_seconds) - now_wall)


def _remember_deadline(context):
    """Record when this invocation ends, from the Lambda context, for the worker's time limit."""
    global _invocation_deadline
    try:
        millis = context.get_remaining_time_in_millis()
    except AttributeError:
        millis = None
    if isinstance(millis, bool) or not isinstance(millis, (int, float)):
        _invocation_deadline = None
        return
    _invocation_deadline = time.monotonic() + millis / 1000.0


def lambda_handler(event, context):
    _remember_deadline(context)
    # The logger keeps its keys in a reused execution environment, so the previous invocation's
    # workflow execution id is removed before anything is logged.
    logger.remove_keys(['workflowExecutionId'])

    # Nothing from the event is logged until the task-token check has passed, and the event itself
    # is never logged: a run whose pipeline was registered with a callback by mistake carries a
    # Step Functions task token in its body, and this container's logger does not redact it. Only
    # identifiers are logged below.
    body = event.get('body') if isinstance(event, dict) else None
    if not body:
        message = 'Request body is required'
        logger.error(message)
        raise ValueError(message)

    if isinstance(body, str):
        data = json.loads(body)
    else:
        data = body
    if not isinstance(data, dict):
        message = 'Request body must be a JSON object'
        logger.error(message)
        raise ValueError(message)

    # Check external task token if passed (Synchronous Pipeline so no task token should be passed)
    if 'TaskToken' in data:
        raise Exception("VAMS Workflow TaskToken found in pipeline input. Make sure to register this pipeline in VAMS as NOT needing a task token callback.")

    return convert(data)


def _task_vcpu():
    try:
        value = int(os.environ.get("VAMS_TASK_VCPU", ""))
    except ValueError:
        return None
    return value if value > 0 else None


def write_run_document(document):
    """Write conversion-run.json under the run's results folder. Best effort, like the incomplete
    result: the conversion's outcome stands without it."""
    if not _results_location:
        logger.info("No results location for this run; the tier is in the log only")
        return
    bucket, prefix = _results_location
    try:
        _s3_client_for_bucket(bucket).put_object(
            Bucket=bucket, Key=prefix + conversion_service.RUN_DOCUMENT_FILENAME,
            Body=json.dumps(document, indent=2).encode("utf-8"), ContentType="application/json")
    except Exception as e:  # noqa: BLE001 - the conversion's outcome stands
        logger.warning(f"Could not write {conversion_service.RUN_DOCUMENT_FILENAME} ({type(e).__name__})")


def convert(data):
    """The whole conversion of one request, the same on both compute tiers (the Lambda handler and
    ``batch_entry.py`` both call it): read the input configuration, resolve the run's locations from
    the manifest, convert, upload, and report.

    ``data`` is the request body (the workflow's task body without its envelope). With
    ``allowEscalation`` true on the Lambda tier, a run the function cannot finish returns the named
    result (``escalate`` true, with the reason and what the router needs) instead of raising. Returns
    ``{'statusCode': 200, 'body': message or 'Success'}``, or that result."""
    # VAMS shows this step's log by reading the function's log group, which every run shares, for the
    # lines that contain the run's workflow execution id. A warning about a skipped referenced file
    # names no S3 path that would carry it, so the id goes on every line from here on. A direct
    # invocation without one logs without it.
    workflow_execution_id = data.get('workflowExecutionId')
    if isinstance(workflow_execution_id, str) and workflow_execution_id:
        logger.append_keys(workflowExecutionId=workflow_execution_id)

    logger.info("Conversion request received", extra={
        "inputManifestS3Location": data.get('inputManifestS3Location', ''),
        "inputConfigurationS3Location": data.get('inputConfigurationS3Location', ''),
    })

    # Read the input configuration from its S3 location (inline fallback for transition).
    input_configuration = fetch_input_configuration(data.get('inputConfigurationS3Location', ''))
    if not input_configuration and data.get('inputParameters'):
        inline = data['inputParameters']
        input_configuration = json.loads(inline) if isinstance(inline, str) else inline

    # The target output format comes from the input configuration (outputType). Fall back to the
    # legacy inline body field for executions whose ASL predates this change.
    output_filetype = (input_configuration or {}).get('outputType') or data.get('outputType', '')
    # A value without its leading dot is the same format.
    if isinstance(output_filetype, str) and output_filetype and not output_filetype.startswith('.'):
        output_filetype = '.' + output_filetype
    options = units_axes.Options.from_config(input_configuration)

    # Resolve the input file + output-files paths and the input's asset-relative subdirectory from
    # the workflow manifest (legacy body fields are the fallback for direct/local invocations).
    input_path, output_path, relative_subdir, version_id = resolve_inputs_from_manifest(data)

    # conversion-run.json is written only by a deployment that has the Batch tier: its state machine
    # sends allowEscalation on the Lambda attempt (true or false), and the Batch task always runs it.
    tier = size_check.compute_tier()
    run_document_enabled = "allowEscalation" in data or tier == size_check.TIER_BATCH
    memory = size_check.memory_limit_bytes()
    task_memory_mib = (memory // size_check.MB) if memory else None
    try:
        # The flag is passed only when the state machine set it, so a deployment without the Batch
        # tier calls the conversion exactly as before.
        extra = {"allow_escalation": True} if conversion_service.allow_escalation(data) else {}
        message = convert_input_output(input_path, output_path, output_filetype, relative_subdir, version_id,
                                       options, **extra)
    except conversion_service.EscalationRequired as escalation:
        result = escalation.result
        logger.info(f"The conversion continues on AWS Batch: {conversion_service.REASON_TEXT[result['reason']]}")
        return result
    except Exception as e:  # noqa: BLE001 - recorded, then raised unchanged
        if tier == size_check.TIER_BATCH:
            # The end of the line: say which tier ran and how it failed, then fail as before. The error text
            # is the one the job's log and the workflow carry.
            reason = data.get("escalationReason")
            reason = reason if reason in conversion_service.ESCALATION_REASONS else None
            _phase_timer.stop()
            write_run_document(conversion_service.run_document(
                tier, task_memory_mib, _task_vcpu(), _phase_timer.seconds, reason=reason,
                escalated_from=size_check.TIER_LAMBDA if reason else None,
                message=f"{type(e).__name__}: {e}", failed=True))
        raise

    if run_document_enabled:
        reason = data.get("escalationReason")
        reason = reason if reason in conversion_service.ESCALATION_REASONS else None
        required = data.get("requiredMemoryMiB")
        write_run_document(conversion_service.run_document(
            tier, task_memory_mib, _task_vcpu(), _phase_timer.seconds, reason=reason,
            escalated_from=size_check.TIER_LAMBDA if reason else None,
            required_memory_mib=required if isinstance(required, int) and not isinstance(required, bool) else None,
            message=message, peak_memory_mib=_peak_worker_memory_mib))
    if message:
        logger.warning(message)
    return {
        'statusCode': 200,
        'body': message or 'Success'
    }



# Class for multipart upload
class ProgressPercentage(object):
    def __init__(self, filename):
        self._filename = filename
        self._size = float(os.path.getsize(filename))
        self._seen_so_far = 0
        self._lock = threading.Lock()

    def __call__(self, bytes_amount):
        # To simplify we'll assume this is hooked up
        # to a single filename.
        with self._lock:
            self._seen_so_far += bytes_amount
