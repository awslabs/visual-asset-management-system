#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
"""Miris upload pipeline container entry point.

Reads the pipeline definition (single JSON arg from constructPipeline), lists the
asset under its resolved root, chooses the root USD file (the selected file on a manual run;
otherwise the asset metadata miris_root_file, else the one layer no other layer in the asset
references; several such layers fail and are named), checks the root's dependencies, their size,
that every file it names opens and that it defines geometry, checks the formats of the textures it
uploads (preflight), writes the dependency manifest of the files the upload holds to the auxiliary
bucket (dependency_manifest), runs the Miris upload flow, writes the Miris stream metadata for that
root file to the run's metadata output path, and exits 0/non-0. A Miris asset the
run created is never deleted, whatever the outcome.

Every download is pinned to the ETag the listing at the start of the run reported (the selected file
to the version the run recorded at launch), so the run reads one consistent asset; a file that
changes after the listing fails the run.

Before pxr reads anything, the text USD in the download is measured against the task's memory
(memory_budget): a text layer can take several times its size to parse. Binary layers are not
bounded.

The run ends before its deadline (time_budget): it uploads nothing when too little time is left for
the upload, and its wait for Miris processing ends before the deadline does.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import dependency_manifest
import memory_budget
from asset_nesting import held_by_nested
import preflight
from miris_uploader import MirisClient, MirisError
from stream_metadata import (
    build_stream_metadata_body,
    metadata_object_key,
    metadata_output_location,
)
import time_budget
from upload_config import resolve_upload_config
from usd_packager import (
    UnreadableLayerError,
    check_geometry,
    choose_roots,
    compute_dependencies,
    dependency_tree_bytes,
    direct_references,
    local_download_plan,
    package_usdz,
    paths_outside,
    requested_root,
    root_candidates,
    scan_references,
    should_skip_packaging,
    tree_files,
)
from utils.secrets import get_miris_integration_key

retry_config = Config(retries={"max_attempts": 5, "mode": "adaptive"})

# Exit codes. The pipelineEnd lambda maps each to the failure reason the workflow shows
# (EXIT_CODE_REASONS); an uncaught exception, and a dependency manifest that cannot be written, exit 1.
EXIT_UNEXPECTED = 1
EXIT_USAGE = 2
EXIT_UNSUPPORTED_EXTENSION = 3
EXIT_TOO_LARGE = 4
EXIT_UNRESOLVED_REFERENCES = 5
EXIT_ROOT_NOT_FOUND = 6
EXIT_NO_METADATA_OUTPUT = 7
EXIT_MIRIS_FAILED = 8
EXIT_AMBIGUOUS_ROOT = 10
EXIT_UNREADABLE_LAYER = 11
EXIT_REFERENCE_OUTSIDE_ASSET = 12
EXIT_INPUT_VERSION_MISSING = 13
EXIT_INSUFFICIENT_TIME = 14
EXIT_ROOT_NOT_RENDERABLE = 15
EXIT_ROOT_FILE_INVALID = 16
EXIT_TEXT_USD_TOO_LARGE = 17
EXIT_UNSUPPORTED_TEXTURE_WEBP = 18
EXIT_ASSET_CHANGED_DURING_RUN = 19

# The asset metadata key naming the root of an automatic run, and how many files a failure's log
# entry names (tied roots, the largest text layers, WebP textures, unopenable files).
ROOT_FILE_METADATA_KEY = "miris_root_file"
LOGGED_FILES = 10

# Free disk kept beyond the download and the packaged copy of the root's dependency tree.
DISK_HEADROOM_BYTES = 256 * 1024 ** 2

WORKDIR = "/workdir"

_USD_MEDIA_TYPES = {
    ".usd": "model/vnd.usd",
    ".usda": "model/vnd.usda",
    ".usdc": "model/vnd.usdc",
    ".usdz": "model/vnd.usdz+zip",
}


def _log(msg, **fields):
    """One-line JSON-ish log so CloudWatch keeps it together."""
    payload = {"msg": msg, **fields}
    print(json.dumps(payload, default=str), flush=True)


def _sha256_of_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


_UUID_PATTERN = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def _stripped_upload_path(upload_path) -> tuple[str, str]:
    """Parse 's3://bucket/prefix' → ('bucket', 'prefix') (no trailing /)."""
    bucket, _, prefix = (upload_path[5:] if isinstance(upload_path, str) else "").partition("/")
    if not isinstance(upload_path, str) or not upload_path.startswith("s3://") or not bucket:
        raise MirisError("POST /v1/asset returned an upload path that is not an s3:// URI")
    return bucket, prefix.rstrip("/")


def _checked_asset_id(start: dict) -> str:
    """The Miris asset id from the POST /v1/asset response, which must be a UUID."""
    asset_id = start.get("id")
    if not isinstance(asset_id, str) or not _UUID_PATTERN.match(asset_id):
        raise MirisError("POST /v1/asset returned an asset id that is not a UUID")
    return asset_id


def _endpoint_host(start: dict):
    """The host of the response's upload endpoint, or None."""
    endpoint = start.get("endpoint_url")
    return urllib.parse.urlparse(endpoint).hostname if isinstance(endpoint, str) else None


def _check_upload_location(start: dict) -> None:
    """The upload endpoint must be an https URL and the upload path an s3:// URI."""
    endpoint = start.get("endpoint_url")
    if not isinstance(endpoint, str) or urllib.parse.urlparse(endpoint).scheme != "https" \
            or not _endpoint_host(start):
        raise MirisError("POST /v1/asset returned an upload endpoint that is not an https URL")
    _stripped_upload_path(start.get("upload_path"))


def _download_version(s3, bucket: str, key: str, local: str, version_id: str) -> None:
    """Download one version of an object (parallel ranged reads)."""
    s3.download_file(bucket, key, local, ExtraArgs={"VersionId": version_id})


def _stream_download(s3, bucket: str, key: str, local: str, etag: str) -> None:
    """Download an object in one read that S3 refuses when its ETag is no longer `etag`."""
    body = s3.get_object(Bucket=bucket, Key=key, IfMatch=etag)["Body"]
    with open(local, "wb") as f:
        for chunk in iter(lambda: body.read(1024 * 1024), b""):
            f.write(chunk)


def _download_at_etag(s3, bucket: str, key: str, local: str, etag: str) -> str:
    """Download the object as it was when it had `etag` and return its VersionId ('' for none).

    The HEAD is conditional on the ETag and names the version that has it, and that version is what
    is downloaded; a version cannot change. Without a version id ('' or 'null', which a later write
    can replace) the read itself carries the condition."""
    version = s3.head_object(Bucket=bucket, Key=key, IfMatch=etag).get("VersionId") or ""
    if version and version != "null":
        _download_version(s3, bucket, key, local, version)
    else:
        _stream_download(s3, bucket, key, local, etag)
    return version


def asset_root_location(stage) -> tuple[str, str]:
    """(bucket, prefix ending in '/') of the asset root; raises ValueError when there is none."""
    location = stage.get("assetRoot") or {}
    bucket = location.get("bucketName") or ""
    prefix = (location.get("objectDir") or "").lstrip("/")
    if not bucket or not prefix.strip("/"):
        raise ValueError("Pipeline definition has no asset root (assetRoot)")
    if not prefix.endswith("/"):
        prefix += "/"
    return bucket, prefix


def list_asset_files(s3, bucket: str, root_prefix: str, nested_locations=()):
    """The run's one listing of the asset: ([(key, asset-relative path, size)] of every file under
    the asset root except the files of the assets stored in `nested_locations` inside it, {key:
    ETag}). Every download is pinned to the ETag listed here."""
    paginator = s3.get_paginator("list_objects_v2")
    sizes, etags = {}, {}
    for page in paginator.paginate(Bucket=bucket, Prefix=root_prefix):
        for obj in page.get("Contents", []):
            if held_by_nested(nested_locations, obj["Key"]):
                continue
            sizes[obj["Key"]] = int(obj.get("Size", 0))
            etags[obj["Key"]] = obj.get("ETag", "")
    files = [(key, rel, sizes[key]) for key, rel in local_download_plan(sizes, root_prefix)]
    return files, etags


class InputVersionMissingError(Exception):
    """The selected file's launch version no longer exists."""

    def __init__(self, rel: str, version_id: str, error: Exception):
        super().__init__(f"{rel} has no version {version_id}: {error}")
        self.rel, self.version_id, self.error = rel, version_id, error


class AssetChangedError(Exception):
    """A file changed or was removed after the run listed the asset."""

    def __init__(self, rel: str, error: Exception):
        super().__init__(f"{rel} changed after the run listed the asset: {error}")
        self.rel, self.error = rel, error


_MISSING_VERSION_CODES = {"NoSuchVersion", "NoSuchKey", "404", "405", "InvalidArgument"}
# A conditional read refused because the ETag moved (412), or the object is gone.
_CHANGED_CODES = {"PreconditionFailed", "412", "NoSuchKey", "404"}


def download_files(s3, bucket: str, files, dest_dir: str, etags, launch_versions=None):
    """Download `files` into dest_dir, preserving the relative layout so USD relative references
    resolve. A relative path in `launch_versions` is read at that version (the selected file, at
    the version the run recorded at launch); every other file is read as it was at its listed ETag
    (`etags`, by key), and one that has changed since raises AssetChangedError. Returns
    ({relative path: VersionId or ''}, {relative path: ETag of the bytes downloaded})."""
    launch_versions = launch_versions or {}
    versions, downloaded = {}, {}
    for key, rel, _size in files:
        local = os.path.join(dest_dir, rel)
        os.makedirs(os.path.dirname(local), exist_ok=True)
        launch = launch_versions.get(rel)
        try:
            if launch:
                head = s3.head_object(Bucket=bucket, Key=key, VersionId=launch)
                _download_version(s3, bucket, key, local, launch)
                versions[rel], downloaded[rel] = launch, head.get("ETag", "")
            else:
                versions[rel] = _download_at_etag(s3, bucket, key, local, etags[key])
                downloaded[rel] = etags[key]
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if launch and code in _MISSING_VERSION_CODES:
                raise InputVersionMissingError(rel, launch, e) from e
            if not launch and code in _CHANGED_CODES:
                raise AssetChangedError(rel, e) from e
            raise
    return versions, downloaded


def free_disk_bytes(path: str) -> int:
    return shutil.disk_usage(path).free


def check_text_usd_or_exit(paths, asset_dir: str, environ) -> None:
    """Exit with EXIT_TEXT_USD_TOO_LARGE, before pxr reads anything, when the text USD among `paths`
    needs more memory than the task has (memory_budget). The log names the largest text files."""
    sizes = {path: memory_budget.text_bytes(path) for path in paths}
    total = sum(sizes.values())
    memory = memory_budget.memory_limit_bytes(environ)
    limit = memory_budget.text_usd_budget_bytes(memory)
    if total <= limit:
        return
    largest = sorted((p for p in sizes if sizes[p]), key=lambda p: -sizes[p])[:LOGGED_FILES]
    _log("text_usd_too_large", reason="text_usd_too_large", text_bytes=total, limit_bytes=limit,
         memory_bytes=memory,
         files=[{"file": os.path.relpath(p, asset_dir).replace(os.sep, "/"), "bytes": sizes[p]}
                for p in largest])
    sys.exit(EXIT_TEXT_USD_TOO_LARGE)


def exit_unreadable_layer(error: UnreadableLayerError, asset_dir: str):
    """Log the unreadable layer by its asset-relative path and exit with EXIT_UNREADABLE_LAYER."""
    base = os.path.realpath(asset_dir)
    relative = os.path.relpath(os.path.realpath(error.path), base).replace(os.sep, "/")
    detail = str(error.error).replace(base + os.sep, "").replace(asset_dir.rstrip("/") + "/", "")
    _log("unreadable_layer", file=relative, error=detail[:500])
    sys.exit(EXIT_UNREADABLE_LAYER)


def exit_unopenable(files, root_rel):
    """Log the asset-relative files pxr could not open and exit with EXIT_UNREADABLE_LAYER."""
    error = ("pxr could not open the root or a file inside it" if files == [root_rel]
             else "pxr could not open a file the root names")
    _log("unreadable_layer", file=files[0], files=files[:LOGGED_FILES], error=error)
    sys.exit(EXIT_UNREADABLE_LAYER)


def report_location(stage, field) -> tuple[str, str]:
    """(bucket, key) of the run report the definition names in `field` (ambiguousRootReport,
    preflightWarningsReport), or ('', '') when the run has none."""
    location = stage.get(field) or {}
    return location.get("bucketName") or "", location.get("objectKey") or ""


def check_textures_or_exit(paths, asset_dir: str) -> list:
    """Exit with EXIT_UNSUPPORTED_TEXTURE_WEBP when a texture the upload holds is WebP, naming the
    first LOGGED_FILES; otherwise return the warnings for the other textures (preflight)."""
    webp, warnings = preflight.classify(preflight.upload_files(paths, asset_dir))
    if webp:
        _log("unsupported_texture_webp", reason="unsupported_texture_webp", count=len(webp),
             files=webp[:LOGGED_FILES], remedy="convert them to PNG or JPEG")
        sys.exit(EXIT_UNSUPPORTED_TEXTURE_WEBP)
    return warnings


def log_preflight_warnings(root_rel, warnings) -> None:
    """One preflight_warning entry per warning, up to preflight.REPORT_MAX_WARNINGS, and their count."""
    if not warnings:
        return
    for warning in warnings[:preflight.REPORT_MAX_WARNINGS]:
        _log("preflight_warning", warning=warning[:500])
    _log("preflight_warnings", count=len(warnings), root=root_rel)


def write_preflight_warnings(s3, stage, root_rel, warnings) -> None:
    """Write the pre-flight warnings to the run's preflight_warnings report, which the execution
    records as a result. Nothing is written without warnings, and a report that cannot be written
    leaves the run's outcome alone."""
    if not warnings:
        return
    bucket, key = report_location(stage, "preflightWarningsReport")
    if not (bucket and key):
        return
    try:
        s3.put_object(Bucket=bucket, Key=key,
                      Body=preflight.report_text(root_rel, warnings).encode("utf-8"),
                      ContentType="text/plain; charset=utf-8")
    except Exception as e:
        _log("preflight_warnings_report_not_written", bucket=bucket, key=key, error=str(e)[:500])


def write_dependency_manifest_or_exit(s3, bucket, database_id, asset_id, root_rel, etags) -> str:
    """Write the dependency manifest of `root_rel` and its tree (`etags`: {asset-relative path: ETag
    downloaded}) to the auxiliary bucket and return its id. A manifest that cannot be written exits
    with EXIT_UNEXPECTED before anything reaches Miris, so no stream metadata names a missing one."""
    data = dependency_manifest.canonical_bytes(dependency_manifest.build_manifest(root_rel, etags))
    manifest_sha = dependency_manifest.manifest_id(data)
    key = dependency_manifest.manifest_key(database_id, asset_id, manifest_sha)
    try:
        s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType="application/json")
    except Exception as e:
        _log("dependency_manifest_not_written", bucket=bucket, key=key, error=str(e)[:500])
        sys.exit(EXIT_UNEXPECTED)
    _log("dependency_manifest_written", bucket=bucket, key=key, files=len(etags))
    return manifest_sha


def exit_ambiguous_root(s3, stage, roots):
    """Record the tied roots (the first LOGGED_FILES and their count) in the log and in
    the run's ambiguous_root report, which the end Lambda puts in the failure reason, and exit with
    EXIT_AMBIGUOUS_ROOT. A report that cannot be written leaves the log entry alone."""
    report = {"roots": roots[:LOGGED_FILES], "count": len(roots)}
    _log("ambiguous_root", reason="ambiguous_root", metadata_key=ROOT_FILE_METADATA_KEY, **report)
    bucket, key = report_location(stage, "ambiguousRootReport")
    if bucket and key:
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=json.dumps(report).encode("utf-8"),
                          ContentType="application/json")
        except Exception as e:
            _log("ambiguous_root_report_not_written", bucket=bucket, key=key, error=str(e)[:500])
    sys.exit(EXIT_AMBIGUOUS_ROOT)


def _put_artifact(start, upload_local, upload_filename, content_type):
    """SigV4 PUT of the artifact to the temporary upload location POST /v1/asset returned.

    The object key matches the declared content_path verbatim, so a name with spaces is uploaded
    as "Coral House.usdz", not "Coral%20House.usdz". boto3 handles wire-level encoding; the
    logical Key stays raw.
    """
    temp_bucket, temp_prefix = _stripped_upload_path(start["upload_path"])
    temp_key = f"{temp_prefix}/{upload_filename}"
    temp_s3 = boto3.client(
        "s3",
        endpoint_url=start["endpoint_url"],
        region_name=start["region"],
        aws_access_key_id=start["access_key_id"],
        aws_secret_access_key=start["secret_key"],
        aws_session_token=start["session_token"],
        config=retry_config,
    )
    with open(upload_local, "rb") as f:
        temp_s3.put_object(
            Bucket=temp_bucket,
            Key=temp_key,
            Body=f,
            ContentType=content_type,
        )
    _log("sigv4_put_complete", bucket=temp_bucket, key=temp_key)


class InsufficientTimeError(Exception):
    """Too little of the run's time is left to upload the artifact; nothing was sent to Miris."""

    def __init__(self, seconds_left: float, seconds_needed: float):
        super().__init__(f"{int(seconds_left)}s left, {int(seconds_needed)}s needed for the upload")
        self.seconds_left = seconds_left
        self.seconds_needed = seconds_needed


def run_miris_upload(client, *, name, tags, upload_local, upload_filename, content_type,
                     size, task_timeout, write_metadata, deadline=None, clock=time.time):
    """Create the Miris asset, upload the artifact, record it on the file and wait for processing.

    The stream metadata is written as soon as Miris has the upload, so the file is linked to the
    asset whether or not processing finishes within the wait; a wait that runs out ends the run
    with the asset still processing and its streamable promotion skipped. A failure after the asset
    exists keeps the asset in Miris and logs its id (miris_asset_kept). Raises
    InsufficientTimeError, before anything reaches Miris, when the time left before `deadline`
    does not cover the upload. Returns the Miris asset id.
    """
    left = time_budget.seconds_left(deadline, clock())
    needed = time_budget.upload_seconds_needed(size)
    if left is not None and left < needed:
        raise InsufficientTimeError(left, needed)

    start = client.start_upload(
        name=name,
        content_path=upload_filename,
        total_bytes=size,
        tags=tags,
    )
    asset_uuid = _checked_asset_id(start)
    # Named fields only: the response also carries temporary upload credentials.
    _log("start_upload", asset_uuid=asset_uuid, region=start.get("region"),
         endpoint_host=_endpoint_host(start))
    try:
        _check_upload_location(start)
        _put_artifact(start, upload_local, upload_filename, content_type)

        client.mark_upload_complete(asset_uuid)
        _log("upload_marked_complete", asset_uuid=asset_uuid)

        write_metadata(asset_uuid)

        wait = task_timeout
        left = time_budget.seconds_left(deadline, clock())
        if left is not None:
            wait = max(0, min(task_timeout, int(left)))
        try:
            ready = client.poll_until_terminal(asset_uuid, timeout_seconds=wait)
        except TimeoutError as e:
            _log("processing_unfinished", asset_uuid=asset_uuid, error=str(e))
            _log("generate_skipped", reason="processing_unfinished")
            return asset_uuid
        _log("terminal_state_reached", state=ready.get("state"))

        # Streamable promotion is best effort; manual promotion in the Miris Portal is the fallback.
        gen = client.trigger_generate_best_effort(asset_uuid)
        if gen is None:
            _log("generate_skipped", reason="endpoint_unavailable_or_failed")
        else:
            _log("generate_triggered", state=gen.get("state"))
    except BaseException as e:
        # A MirisError's text is the request and status only; another error can carry request details.
        _log("miris_asset_kept", asset_uuid=asset_uuid,
             error=str(e) if isinstance(e, MirisError) else type(e).__name__)
        raise
    return asset_uuid


def upload_or_exit(client, **upload_args):
    """Run the upload, exiting with the code for a Miris failure or for too little time left to
    upload. Any other failure propagates and exits 1."""
    try:
        return run_miris_upload(client, **upload_args)
    except InsufficientTimeError as e:
        _log("insufficient_time", seconds_left=int(e.seconds_left),
             seconds_needed=int(e.seconds_needed))
        sys.exit(EXIT_INSUFFICIENT_TIME)
    except MirisError as e:
        _log("miris_upload_failed", error=str(e))
        sys.exit(EXIT_MIRIS_FAILED)


def main():
    deadline = time_budget.run_deadline(os.environ, time.time())
    if len(sys.argv) < 2:
        _log("usage: __main__.py <definition-json>")
        sys.exit(EXIT_USAGE)
    definition = json.loads(sys.argv[1])
    stage = definition["stages"][0]
    asset_id = stage.get("assetId", "")
    database_id = stage.get("databaseId", "")
    trigger = stage["triggerInput"]
    miris_base = stage["mirisApiBaseUrl"]
    secret_arn = stage["mirisApiKeySecretArn"]
    task_timeout = int(stage["taskTimeoutSeconds"])
    max_bytes = int(stage["maxAssetSizeBytes"])

    # Both identities become the Miris asset's binding tags.
    if not asset_id or not database_id:
        _log("asset_identity_missing", asset_id=asset_id, database_id=database_id)
        sys.exit(EXIT_USAGE)
    try:
        asset_bucket, asset_root = asset_root_location(stage)
    except ValueError as e:
        _log("asset_root_missing", error=str(e))
        sys.exit(EXIT_USAGE)

    trigger_key = trigger["objectKey"]
    extension = trigger["fileExtension"].lower()
    if extension not in _USD_MEDIA_TYPES:
        _log("unsupported_extension", extension=extension)
        sys.exit(EXIT_UNSUPPORTED_EXTENSION)

    # The stream metadata is the pipeline's only output, so a run without somewhere
    # to write it fails before anything is uploaded to Miris.
    try:
        metadata_bucket, metadata_prefix = metadata_output_location(stage)
    except ValueError as e:
        _log("metadata_output_path_missing", error=str(e))
        sys.exit(EXIT_NO_METADATA_OUTPUT)
    # The dependency manifest the stream metadata names is written to the auxiliary bucket.
    auxiliary_bucket = stage.get("auxiliaryBucketName") or ""
    if not auxiliary_bucket:
        _log("auxiliary_bucket_missing")
        sys.exit(EXIT_USAGE)

    s3 = boto3.client("s3", config=retry_config)
    os.makedirs(WORKDIR, exist_ok=True)
    asset_dir = os.path.join(WORKDIR, "asset")

    # A manual run's root is the selected file; an automatic run's is the asset metadata
    # miris_root_file when it is set, else it is chosen from the asset's USD files.
    trigger_rel = trigger_key[len(asset_root):] if trigger_key.startswith(asset_root) else ""
    manual = stage.get("manualRun") is True
    override = "" if manual else stage.get("mirisRootFile")
    override = override.strip() if isinstance(override, str) else ""
    requested = requested_root(trigger_rel if manual else override)
    extensions = [
        e for e in (stage.get("rootExtensions") or _USD_MEDIA_TYPES) if e in _USD_MEDIA_TYPES]
    # Files of another asset stored inside this one's location are that asset's.
    nested = stage.get("nestedLocations")
    nested = [n for n in nested if isinstance(n, str)] if isinstance(nested, list) else []
    files, listed_etags = list_asset_files(s3, asset_bucket, asset_root, nested)
    candidates = root_candidates([rel for _, rel, _ in files], extensions)
    if not candidates or (manual and requested not in candidates):
        _log("root_usd_not_found", asset_root=asset_root, trigger_key=trigger_key,
             listed_files=len(files), manual=manual)
        sys.exit(EXIT_ROOT_NOT_FOUND)
    if override and requested not in candidates:
        _log("root_file_invalid", metadata_key=ROOT_FILE_METADATA_KEY, value=override[:500],
             candidates=candidates[:LOGGED_FILES])
        sys.exit(EXIT_ROOT_FILE_INVALID)

    # A .usdz references no other file in the asset, so several of them and no other layer are
    # several roots; that is known before anything downloads.
    if not requested and len(candidates) > 1 and all(c.lower().endswith(".usdz") for c in candidates):
        exit_ambiguous_root(s3, stage, candidates)

    # A lone .usdz is self-contained and is the whole upload, so maxAssetSizeBytes applies to it
    # now. Anything else needs the whole asset on disk, and the limit applies to the root's
    # dependency tree once it is known; the disk must hold the download and the packaged tree.
    lone = requested or (candidates[0] if len(candidates) == 1 else "")
    if lone.lower().endswith(".usdz"):
        to_download = [f for f in files if f[1] == lone]
    else:
        to_download = files
    total = sum(size for _, _, size in to_download)
    if lone.lower().endswith(".usdz") and total > max_bytes:
        _log("file_too_large", size=total, max_bytes=max_bytes, scope="usdz")
        sys.exit(EXIT_TOO_LARGE)
    packaged = 0 if lone.lower().endswith(".usdz") else min(total, max_bytes)
    needed, free = total + packaged + DISK_HEADROOM_BYTES, free_disk_bytes(WORKDIR)
    if needed > free:
        _log("file_too_large", scope="disk", download_bytes=total, needed_bytes=needed,
             free_bytes=free)
        sys.exit(EXIT_TOO_LARGE)

    # The selected file is read at the version the run recorded at launch; every other file as it
    # was when listed above.
    launch_versions = {trigger_rel: trigger.get("versionId") or ""} if trigger_rel else {}
    try:
        versions, downloaded_etags = download_files(s3, asset_bucket, to_download, asset_dir,
                                                    listed_etags, launch_versions)
    except InputVersionMissingError as e:
        _log("input_version_missing", file=e.rel, version_id=e.version_id, error=str(e.error))
        sys.exit(EXIT_INPUT_VERSION_MISSING)
    except AssetChangedError as e:
        _log("asset_changed_during_run", reason="asset_changed_during_run", file=e.rel,
             error=str(e.error)[:500], remedy="start the upload again")
        sys.exit(EXIT_ASSET_CHANGED_DURING_RUN)

    # Prim-heavy text USD can cost up to about 8x its size to parse, so it is measured before any parse.
    check_text_usd_or_exit([os.path.join(asset_dir, rel) for _, rel, _ in to_download],
                           asset_dir, os.environ)

    def references_of(rel):
        return direct_references(os.path.join(asset_dir, rel), asset_dir)

    # Never a guess: several unreferenced candidates fail and are named.
    if requested:
        roots = [requested]
    else:
        try:
            roots = choose_roots(candidates, references_of)
        except UnreadableLayerError as e:
            exit_unreadable_layer(e, asset_dir)
        if len(roots) > 1:
            exit_ambiguous_root(s3, stage, roots)
        if not roots:
            _log("root_usd_not_found", reason="every_layer_is_referenced", candidates=candidates[:20])
            sys.exit(EXIT_ROOT_NOT_FOUND)
    root_rel = roots[0]
    root_local = os.path.join(asset_dir, root_rel)
    source_version_id = versions.get(root_rel, "")
    filename = os.path.basename(root_rel)
    root_extension = os.path.splitext(filename)[1].lower()
    name_no_ext = os.path.splitext(filename)[0]
    root_source = "selected_file" if manual else ("metadata" if requested else "asset")
    _log("root_chosen", root=root_rel, source=root_source, candidates=len(candidates),
         trigger_key=trigger_key)

    # Its dependencies are checked first, so a missing layer is reported as missing rather than as a
    # root without geometry; packaging comes after both checks.
    layers = assets = []
    if root_extension != ".usdz":
        try:
            layers, assets, unresolved, unopenable = compute_dependencies(root_local)
        except UnreadableLayerError as e:
            # Name the layer itself when the root's own references lead to it.
            try:
                scan_references([root_rel], references_of)
            except UnreadableLayerError as unreadable:
                exit_unreadable_layer(unreadable, asset_dir)
            exit_unreadable_layer(e, asset_dir)
        if unresolved:
            _log("unresolved_references", count=len(unresolved), paths=unresolved[:20])
            sys.exit(EXIT_UNRESOLVED_REFERENCES)
        # Only files the asset holds are packaged, never one a reference resolves to elsewhere.
        outside = paths_outside(layers + assets + unopenable, asset_dir)
        if outside:
            _log("reference_outside_asset", count=len(outside), paths=outside[:20])
            sys.exit(EXIT_REFERENCE_OUTSIDE_ASSET)
        # A file pxr cannot open would be missing from the stream, whatever else the root defines.
        if unopenable:
            exit_unopenable([os.path.relpath(path, os.path.realpath(asset_dir)).replace(os.sep, "/")
                             for path in unopenable], root_rel)
        # The upload holds the root's dependency tree, so the size limit applies to that tree.
        tree_bytes = dependency_tree_bytes(layers + assets)
        if tree_bytes > max_bytes:
            _log("file_too_large", size=tree_bytes, max_bytes=max_bytes, scope="dependency_tree",
                 root=root_rel)
            sys.exit(EXIT_TOO_LARGE)
    # A .usdz root is its own tree; its members are the textures it uploads.
    warnings = check_textures_or_exit(layers + assets if layers else [root_local], asset_dir)
    try:
        has_geometry, unopenable, unset_metadata = check_geometry(root_local, asset_dir)
    except UnreadableLayerError as e:
        exit_unreadable_layer(e, asset_dir)
    if not has_geometry:
        # Without geometry, a file pxr cannot open inside a .usdz root may hide it; name it.
        if unopenable:
            exit_unopenable(unopenable, root_rel)
        _log("root_not_renderable", root=root_rel, source=root_source)
        sys.exit(EXIT_ROOT_NOT_RENDERABLE)
    warnings += [f"{root_rel}: the stage does not set {key}, so the asset may stream at the wrong "
                 f"{'orientation' if key == 'upAxis' else 'scale'}" for key in unset_metadata]
    log_preflight_warnings(root_rel, warnings)

    # Resolve the single self-contained artifact to upload to Miris.
    if root_extension == ".usdz":
        upload_local = root_local
        upload_filename = filename
        upload_content_type = _USD_MEDIA_TYPES[".usdz"]
    elif should_skip_packaging(len(layers), len(assets)):
        _log("packaging_skipped", reason="no_external_dependencies")
        upload_local = root_local
        upload_filename = filename
        upload_content_type = _USD_MEDIA_TYPES[root_extension]
    else:
        upload_local = os.path.join(WORKDIR, f"{name_no_ext}.usdz")
        package_usdz(root_local, upload_local)
        upload_filename = f"{name_no_ext}.usdz"
        upload_content_type = _USD_MEDIA_TYPES[".usdz"]
        _log(
            "packaged_usdz",
            file=upload_filename,
            layers=len(layers),
            assets=len(assets),
        )

    size = os.path.getsize(upload_local)
    sha = _sha256_of_file(upload_local)
    _log("artifact_ready", path=upload_local, size=size, sha256=sha[:16])

    # The root and its dependency tree, at the ETags downloaded: what the stream is built from.
    tree = tree_files([root_local] + layers + assets, asset_dir)
    manifest_sha = write_dependency_manifest_or_exit(
        s3, auxiliary_bucket, database_id, asset_id, root_rel,
        {rel: downloaded_etags[rel] for rel in tree})

    # Read the already-rendered per-run configuration (template values substituted by the
    # workflow before this container started). Never fatal: every field falls back to a
    # VAMS-derived default.
    cfg = resolve_upload_config(stage, upload_filename, s3)
    _log("upload_config_resolved", asset_name=cfg.asset_name, tags=cfg.tags)

    # Secrets Manager
    key = get_miris_integration_key(secret_arn, region_name=os.environ.get("AWS_REGION"))
    client = MirisClient(miris_base, key)

    miris_tags = ["vams", f"vams-asset-{asset_id}", f"vams-database-{database_id}"] + cfg.tags

    def write_metadata(asset_uuid):
        """Write the stream metadata for the root USD file."""
        body = build_stream_metadata_body(
            asset_uuid=asset_uuid,
            source_version_id=source_version_id,
            display_name=cfg.asset_name,
            uploaded_at=datetime.datetime.now(datetime.timezone.utc),
            dependency_manifest=manifest_sha,
        )
        metadata_key = metadata_object_key(metadata_prefix, root_rel)
        s3.put_object(
            Bucket=metadata_bucket,
            Key=metadata_key,
            Body=json.dumps(body, indent=2).encode("utf-8"),
            ContentType="application/json",
        )
        _log(
            "stream_metadata_written",
            bucket=metadata_bucket,
            key=metadata_key,
            asset_uuid=asset_uuid,
            source_version_id=source_version_id,
            dependency_manifest=manifest_sha,
        )

    upload_or_exit(
        client,
        name=cfg.asset_name,
        tags=miris_tags,
        upload_local=upload_local,
        upload_filename=upload_filename,
        content_type=upload_content_type,
        size=size,
        task_timeout=task_timeout,
        write_metadata=write_metadata,
        deadline=deadline,
    )
    write_preflight_warnings(s3, stage, root_rel, warnings)


if __name__ == "__main__":
    main()
