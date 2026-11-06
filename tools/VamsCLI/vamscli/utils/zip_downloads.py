"""Helpers of the 'zip-download' commands: selection parsing, waiting, local paths, formatting.

Kept out of commands/zip_download.py so the MCP server and tests can use them without Click.
"""

import re
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from ..constants import (
    ZIP_DOWNLOAD_MAX_SELECTION_ENTRIES,
    ZIP_DOWNLOAD_POLL_INTERVAL_SECONDS,
    ZIP_DOWNLOAD_TERMINAL_STATUSES,
)
from .download_manager import format_file_size
from .exceptions import (
    InvalidZipDownloadRequestError,
    ZipDownloadFailedError,
    ZipDownloadTimeoutError,
)

# The charset of an S3 VersionId (VERSION_ID_PATTERN in backend/backend/models/zipDownloads.py).
_VERSION_ID_RE = re.compile(r"^[A-Za-z0-9._\-]+$")


def _asset_relative(path: str) -> str:
    path = (path or "").strip()
    if not path:
        raise InvalidZipDownloadRequestError("A selection path must not be empty")
    return path if path.startswith("/") else f"/{path}"


def parse_file_spec(spec: str) -> Dict[str, Any]:
    """Parse one --file value, PATH or PATH@VERSIONID, into a file selection entry.

    The version is the text after the LAST '@' when it is a valid S3 version id. A path that itself
    contains '@' and takes no version ends with a bare '@' ("/a@b.glb@"), which leaves it unpinned.
    """
    path, version_id = spec, None
    if "@" in spec:
        head, tail = spec.rsplit("@", 1)
        if tail == "":
            path = head
        elif head and _VERSION_ID_RE.match(tail):
            path, version_id = head, tail
    path = _asset_relative(path)
    if path.endswith("/"):
        raise InvalidZipDownloadRequestError(
            f"--file '{path}' ends with '/'; use --folder for a folder")
    entry: Dict[str, Any] = {"type": "file", "relativePath": path}
    if version_id:
        entry["versionId"] = version_id
    return entry


def parse_folder(path: str) -> Dict[str, Any]:
    """A --folder value as a folder selection entry (asset-relative, ending with '/')."""
    path = _asset_relative(path)
    if path == "/":
        raise InvalidZipDownloadRequestError("Use --whole-asset to archive the whole asset")
    if not path.endswith("/"):
        path = f"{path}/"
    return {"type": "folder", "relativePath": path}


def build_selection(files: Iterable[str], folders: Iterable[str], whole_asset: bool) -> List[Dict[str, Any]]:
    """The create request's `selection` from the --file / --folder / --whole-asset options."""
    entries = [parse_file_spec(spec) for spec in files] + [parse_folder(path) for path in folders]
    if whole_asset:
        if entries:
            raise InvalidZipDownloadRequestError(
                "--whole-asset cannot be combined with --file or --folder")
        return [{"type": "asset", "relativePath": "/"}]
    if not entries:
        raise InvalidZipDownloadRequestError(
            "Select what to archive: --file, --folder, or --whole-asset")
    if len(entries) > ZIP_DOWNLOAD_MAX_SELECTION_ENTRIES:
        raise InvalidZipDownloadRequestError(
            f"A zip download takes at most {ZIP_DOWNLOAD_MAX_SELECTION_ENTRIES} selection entries; "
            "select a folder instead of many files")
    return entries


def build_create_request(selection: List[Dict[str, Any]], asset_version_id: Optional[str] = None,
                         asset_version_alias: Optional[str] = None, archive_format: str = "zip",
                         compression: Optional[str] = None,
                         archive_file_name: Optional[str] = None) -> Dict[str, Any]:
    """The CreateZipDownloadRequestModel body, refusing the combinations the API refuses."""
    if asset_version_id and asset_version_alias:
        raise InvalidZipDownloadRequestError(
            "--asset-version-id and --asset-version-alias are mutually exclusive")
    if (asset_version_id or asset_version_alias) and any(e.get("versionId") for e in selection):
        raise InvalidZipDownloadRequestError(
            "A per-file version (PATH@VERSIONID) cannot be combined with an asset version pin")
    if compression == "deflate" and archive_format != "zip":
        raise InvalidZipDownloadRequestError("--compression deflate applies to the zip format only")
    body: Dict[str, Any] = {"selection": selection, "archiveFormat": archive_format}
    body["compression"] = compression or "store"
    if asset_version_id:
        body["assetVersionId"] = asset_version_id
    if asset_version_alias:
        body["assetVersionIdAlias"] = asset_version_alias
    if archive_file_name:
        body["archiveFileName"] = archive_file_name
    return body


def is_terminal(job: Dict[str, Any]) -> bool:
    return isinstance(job, dict) and job.get("status") in ZIP_DOWNLOAD_TERMINAL_STATUSES


def wait_for_zip_download(api_client, database_id: str, asset_id: str, request_id: str,
                          timeout: float, on_update: Optional[Callable[[Dict[str, Any]], None]] = None,
                          poll_interval: float = ZIP_DOWNLOAD_POLL_INTERVAL_SECONDS,
                          sleep: Callable[[float], None] = time.sleep,
                          clock: Callable[[], float] = time.monotonic,
                          require_success: bool = True) -> Dict[str, Any]:
    """Poll a job until it finishes and return it.

    Raises:
        ZipDownloadTimeoutError: When `timeout` seconds pass first (the job keeps running)
        ZipDownloadFailedError: When `require_success` and the job ends FAILED or CANCELLED
    """
    deadline = clock() + max(0.0, float(timeout))
    while True:
        job = api_client.get_zip_download(database_id, asset_id, request_id)
        if on_update:
            on_update(job)
        if is_terminal(job):
            if require_success and job.get("status") != "SUCCEEDED":
                reason = job.get("errorMessage") or "no reason was recorded"
                raise ZipDownloadFailedError(
                    f"Zip download '{request_id}' ended {job.get('status')}: {reason}", job)
            return job
        remaining = deadline - clock()
        if remaining <= 0:
            raise ZipDownloadTimeoutError(
                f"Zip download '{request_id}' is still {job.get('status')} after {int(timeout)} "
                "seconds; it keeps running. Check it with 'vamscli zip-download get' or wait again "
                "with 'vamscli zip-download download --wait'", job)
        sleep(min(poll_interval, remaining))


def resolve_local_path(output: str, archive_file_name: str, overwrite: bool = False) -> Path:
    """Where to write an archive: into `output` when it is a directory, else to `output` itself.

    The archive's own name is reduced to its last path component, so a stored name can never
    steer the write outside the chosen directory.
    """
    target = Path(output).expanduser()
    if target.is_dir():
        name = Path(archive_file_name or "").name or "archive.zip"
        target = target / name
    if target.exists() and not overwrite:
        raise InvalidZipDownloadRequestError(
            f"'{target}' already exists; pass --overwrite to replace it")
    if not target.parent.exists():
        raise InvalidZipDownloadRequestError(f"The directory '{target.parent}' does not exist")
    return target


def progress_text(job: Dict[str, Any]) -> str:
    """One line describing a job's state and progress."""
    status = job.get("status", "UNKNOWN")
    progress = job.get("progress") or {}
    files_total = progress.get("filesTotal") or 0
    bytes_total = progress.get("bytesTotal") or 0
    parts = [status]
    if files_total:
        parts.append(f"{progress.get('filesDone') or 0}/{files_total} files")
    if bytes_total:
        parts.append(f"{format_file_size(progress.get('bytesDone') or 0)} of {format_file_size(bytes_total)}")
    return ", ".join(parts)


def format_job(job: Dict[str, Any]) -> str:
    """Text-mode rendering of one job."""
    if not isinstance(job, dict):
        return str(job)
    summary = job.get("selectionSummary") or {}
    if summary.get("wholeAsset"):
        selection = "whole asset"
    else:
        selection = f"{summary.get('files', 0)} file(s), {summary.get('folders', 0)} folder(s)"
    lines = [
        f"Request ID: {job.get('requestId', 'N/A')}",
        f"Asset: {job.get('databaseId', 'N/A')}/{job.get('assetId', 'N/A')}"
        + (f" ({job['assetName']})" if job.get("assetName") else ""),
        f"Status: {progress_text(job)}",
        f"Archive: {job.get('archiveFileName', 'N/A')} ({job.get('archiveFormat', 'zip')}, "
        f"{job.get('compression', 'store')})",
        f"Selection: {selection}",
    ]
    if job.get("assetVersionId"):
        lines.append(f"Asset version: {job['assetVersionId']}")
    if job.get("archiveSizeBytes") is not None:
        lines.append(f"Size: {format_file_size(job['archiveSizeBytes'])}")
    if job.get("errorMessage"):
        lines.append(f"Error: {job['errorMessage']}")
    if job.get("cancelledBy"):
        lines.append(f"Cancelled by: {job['cancelledBy']}")
    lines.append(f"Created: {job.get('createdAt', 'N/A')}")
    if job.get("completedAt"):
        lines.append(f"Completed: {job['completedAt']}")
    if job.get("expiresAt"):
        lines.append(f"Expires: {job['expiresAt']}")
    return "\n".join(lines)


def format_job_list(result: Dict[str, Any]) -> str:
    """Text-mode rendering of a job listing."""
    items = result.get("Items", []) if isinstance(result, dict) else []
    if not items:
        return "No zip downloads found."
    lines = [f"Found {len(items)} zip download(s):", "=" * 80]
    for job in items:
        lines.append(format_job(job))
        lines.append("-" * 80)
    if isinstance(result, dict) and result.get("NextToken"):
        lines.append(f"\nNext token: {result['NextToken']}")
        lines.append("Use --starting-token to get the next page")
    return "\n".join(lines)
