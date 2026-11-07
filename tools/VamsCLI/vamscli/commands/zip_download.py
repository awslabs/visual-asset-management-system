"""Download as Zip commands for VamsCLI.

A zip download is a server-side job that archives a selection of one asset's files (zip, tar or
tar.gz). The job and its archive can be downloaded until the job's expiresAt (the deployment's
retention period after it was created); the archive file is then removed by an S3 lifecycle rule. Jobs belong to the user who
created them. The archive is fetched through a short-lived presigned URL, which these commands
stream straight to a local file and never print.
"""

from builtins import list as builtin_list  # Avoid namespace collision with the 'list' command
from typing import Any, Dict, Optional, Tuple

import click

from ..constants import (
    ZIP_DOWNLOAD_COMPRESSIONS,
    ZIP_DOWNLOAD_DEFAULT_WAIT_TIMEOUT_SECONDS,
    ZIP_DOWNLOAD_FORMATS,
    ZIP_DOWNLOAD_PROGRESS_STEP_BYTES,
)
from ..utils.api_client import APIClient
from ..utils.confirmation import confirm_destructive_action
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.download_manager import format_file_size
from ..utils.exceptions import (
    APIError,
    AssetNotFoundError,
    DatabaseNotFoundError,
    InvalidZipDownloadRequestError,
    ZipDownloadDisabledError,
    ZipDownloadError,
    ZipDownloadExpiredError,
    ZipDownloadFailedError,
    ZipDownloadLimitError,
    ZipDownloadNotFoundError,
    ZipDownloadNotReadyError,
    ZipDownloadStateError,
    ZipDownloadTimeoutError,
    ZipDownloadTransferError,
)
from ..utils.json_output import output_error, output_result, output_status, output_warning
from ..utils.zip_downloads import (
    build_create_request,
    build_selection,
    format_job,
    format_job_list,
    is_terminal,
    progress_text,
    resolve_local_path,
    wait_for_zip_download,
)

# The domain errors every command reports, most specific first: (class, heading, hint).
_ERROR_PRESENTATION = (
    (ZipDownloadDisabledError, "Zip Downloads Unavailable",
     "Ask your VAMS administrator whether zip downloads (app.zipDownloads) are enabled. If they "
     "were enabled after you signed in, run 'vamscli auth login' to refresh the feature switches."),
    (ZipDownloadNotFoundError, "Zip Download Not Found",
     "Use 'vamscli zip-download list' to see your zip downloads. A job expires at its "
     "expiresAt, the deployment's retention period after it was created."),
    (ZipDownloadLimitError, "Zip Download Limit Reached",
     "Wait for one of your active zip downloads to finish, or cancel one with "
     "'vamscli zip-download cancel'."),
    (ZipDownloadNotReadyError, "Zip Download Not Ready",
     "Add --wait to wait for the archive, or check it with 'vamscli zip-download get'."),
    (ZipDownloadExpiredError, "Zip Download Expired",
     "Create a new zip download with 'vamscli zip-download create'."),
    (ZipDownloadStateError, "Zip Download State", None),
    (ZipDownloadFailedError, "Zip Download Failed", None),
    (ZipDownloadTimeoutError, "Zip Download Still Running", None),
    (ZipDownloadTransferError, "Zip Download Transfer Failed", None),
    (InvalidZipDownloadRequestError, "Invalid Zip Download Request", None),
    (ZipDownloadError, "Zip Download Error", None),
    (AssetNotFoundError, "Asset Not Found", "Use 'vamscli assets list' to see available assets."),
    (DatabaseNotFoundError, "Database Not Found",
     "Use 'vamscli database list' to see available databases."),
    (APIError, "API Error", None),
)

_HANDLED_ERRORS = (ZipDownloadError, AssetNotFoundError, DatabaseNotFoundError, APIError)


def _report(error: Exception, json_output: bool) -> None:
    """Print a handled business error (JSON mode exits 1 here) and fail the command."""
    for error_class, heading, hint in _ERROR_PRESENTATION:
        if isinstance(error, error_class):
            output_error(error, json_output, error_type=heading, helpful_message=hint)
            break
    else:
        output_error(error, json_output)
    raise click.ClickException(str(error))


def _api_client(ctx: click.Context) -> APIClient:
    profile_manager = get_profile_manager_from_context(ctx)
    config = profile_manager.load_config()
    return APIClient(config['api_gateway_url'], profile_manager)


def _status_printer(json_output: bool):
    """An on_update callback printing a job's state whenever it changes (text mode only)."""
    last = {"text": None}

    def _update(job: Dict[str, Any]) -> None:
        text = progress_text(job)
        if text != last["text"]:
            last["text"] = text
            output_status(f"  {text}", json_output)

    return _update


def _transfer_printer(json_output: bool):
    """A progress callback printing a line every ZIP_DOWNLOAD_PROGRESS_STEP_BYTES (text mode only)."""
    state = {"next": ZIP_DOWNLOAD_PROGRESS_STEP_BYTES}

    def _progress(done: int, total: Optional[int]) -> None:
        if done >= state["next"] or (total and done >= total):
            state["next"] = done + ZIP_DOWNLOAD_PROGRESS_STEP_BYTES
            suffix = f" of {format_file_size(total)} ({done * 100 // total}%)" if total else ""
            output_status(f"  Downloaded {format_file_size(done)}{suffix}", json_output)

    return _progress


def _fetch_archive(api_client: APIClient, database_id: str, asset_id: str, request_id: str,
                   output: str, overwrite: bool, json_output: bool) -> Dict[str, Any]:
    """Issue the URL for a finished archive and stream it to `output`; never prints the URL."""
    issued = api_client.get_zip_download_url(database_id, asset_id, request_id)
    archive_file_name = issued.get('archiveFileName') or 'archive.zip'
    local_path = resolve_local_path(output, archive_file_name, overwrite)
    expected = issued.get('archiveSizeBytes')
    output_status(f"Downloading '{archive_file_name}' to '{local_path}'...", json_output)
    written = api_client.download_zip_archive(
        issued['url'], str(local_path), expected_size=expected,
        progress_callback=_transfer_printer(json_output))
    return {
        "requestId": request_id,
        "archiveFileName": archive_file_name,
        "localPath": str(local_path),
        "bytesWritten": written,
        "bytesWrittenFormatted": format_file_size(written),
    }


def _require_confirm(confirm: bool, json_output: bool, verb: str) -> None:
    if not confirm:
        output_warning(f"⚠️  {verb} requires explicit confirmation! Use the --confirm flag.",
                       json_output)
        _report(InvalidZipDownloadRequestError(f"{verb} requires the --confirm flag"), json_output)


@click.group(name='zip-download')
def zip_download():
    """Download as Zip: archive an asset's files on the server and download the archive.

    A job archives files, folders or the whole asset at their current versions, at per-file
    versions (PATH@VERSIONID), or at an asset version. Jobs belong to you, appear in
    'zip-download list', and expire at their expiresAt (the deployment's retention period after
    creation); an expired job cannot be downloaded. The whole job is
    refused when a selected file is archived, deleted, in an archive storage tier or of a blocked
    type.
    """
    pass


@zip_download.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID')
@click.option('--file', 'files', multiple=True,
              help='File to include, as PATH or PATH@VERSIONID (repeat for several). A path that '
                   'contains "@" and takes no version ends with a bare "@".')
@click.option('--folder', 'folders', multiple=True,
              help='Folder to include, with everything under it (repeat for several)')
@click.option('--whole-asset', is_flag=True, help='Archive every file of the asset')
@click.option('--asset-version-id', default=None,
              help='Archive the selection as recorded in this asset version')
@click.option('--asset-version-alias', default=None,
              help='Archive the selection as recorded in the asset version with this alias')
@click.option('--format', 'archive_format', type=click.Choice(ZIP_DOWNLOAD_FORMATS),
              default='zip', show_default=True, help='Archive format')
@click.option('--compression', type=click.Choice(ZIP_DOWNLOAD_COMPRESSIONS), default=None,
              help='Compression of a zip archive (default store)')
@click.option('--name', 'archive_file_name', default=None,
              help='Archive file name (the extension is added when missing)')
@click.option('--wait', is_flag=True, help='Wait until the archive is ready')
@click.option('--timeout', type=click.IntRange(min=1), default=None,
              help=f'Seconds to wait with --wait or --download-to '
                   f'(default {ZIP_DOWNLOAD_DEFAULT_WAIT_TIMEOUT_SECONDS})')
@click.option('--download-to', default=None,
              help='Wait, then download the archive to this file or directory')
@click.option('--overwrite', is_flag=True, help='Replace an existing local file with --download-to')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def create(ctx: click.Context, database_id: str, asset_id: str, files: Tuple[str, ...],
           folders: Tuple[str, ...], whole_asset: bool, asset_version_id: Optional[str],
           asset_version_alias: Optional[str], archive_format: str, compression: Optional[str],
           archive_file_name: Optional[str], wait: bool, timeout: Optional[int],
           download_to: Optional[str], overwrite: bool, json_output: bool):
    """Create a zip download job for files, folders or the whole asset.

    Examples:
        vamscli zip-download create -d my-db -a my-asset --whole-asset
        vamscli zip-download create -d my-db -a my-asset --folder /textures --file /model.glb
        vamscli zip-download create -d my-db -a my-asset --file /model.glb@3HL4kqtJlcpXroDTDmJ
        vamscli zip-download create -d my-db -a my-asset --whole-asset --asset-version-id 2
        vamscli zip-download create -d my-db -a my-asset --whole-asset --format tar.gz --wait
        vamscli zip-download create -d my-db -a my-asset --whole-asset --download-to ./downloads
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    try:
        if timeout is not None and not (wait or download_to):
            raise InvalidZipDownloadRequestError("--timeout applies with --wait or --download-to")
        selection = build_selection(files, folders, whole_asset)
        body = build_create_request(selection, asset_version_id, asset_version_alias,
                                    archive_format, compression, archive_file_name)
        if download_to:
            # Fail before the job starts when the destination can never be written.
            resolve_local_path(download_to, archive_file_name or "archive", overwrite)

        output_status(f"Creating a zip download of asset '{asset_id}'...", json_output)
        job = api_client.create_zip_download(database_id, asset_id, body)

        if wait or download_to:
            output_status(f"Waiting for zip download '{job.get('requestId')}'...", json_output)
            job = wait_for_zip_download(
                api_client, database_id, asset_id, job['requestId'],
                timeout or ZIP_DOWNLOAD_DEFAULT_WAIT_TIMEOUT_SECONDS,
                on_update=_status_printer(json_output))

        if download_to:
            download = _fetch_archive(api_client, database_id, asset_id, job['requestId'],
                                      download_to, overwrite, json_output)
            result = {**job, "download": download}
            output_result(
                result, json_output,
                success_message="✓ Zip download created and downloaded!",
                cli_formatter=lambda r: format_job(r) + f"\nSaved to: {r['download']['localPath']}")
            return result

        output_result(job, json_output,
                      success_message=("✓ Zip download ready!" if is_terminal(job)
                                       else "✓ Zip download created!"),
                      cli_formatter=format_job)
        return job
    except _HANDLED_ERRORS as e:
        _report(e, json_output)


@zip_download.command()
@click.option('-d', '--database-id', default=None, help='Database ID (with -a: one asset only)')
@click.option('-a', '--asset-id', default=None, help='Asset ID (with -d: one asset only)')
@click.option('--max-items', type=click.IntRange(1, 1000), default=None,
              help='Maximum jobs to return (1-1000)')
@click.option('--page-size', type=click.IntRange(1, 1000), default=None,
              help='Jobs per page (1-1000)')
@click.option('--starting-token', default=None, help='Token for pagination')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def list(ctx: click.Context, database_id: Optional[str], asset_id: Optional[str],
         max_items: Optional[int], page_size: Optional[int], starting_token: Optional[str],
         json_output: bool):
    """List your zip downloads, newest first: for one asset, or across assets.

    Examples:
        vamscli zip-download list
        vamscli zip-download list -d my-db -a my-asset
        vamscli zip-download list --max-items 20 --starting-token <token>
        vamscli zip-download list --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status("Retrieving zip downloads...", json_output)
    try:
        result = api_client.list_zip_downloads(
            database_id=database_id, asset_id=asset_id, max_items=max_items,
            page_size=page_size, starting_token=starting_token)
        items = builtin_list(result.get('Items') or []) if isinstance(result, dict) else []
        output_result(result, json_output,
                      success_message=f"Found {len(items)} zip download(s)",
                      cli_formatter=format_job_list)
        return result
    except _HANDLED_ERRORS as e:
        _report(e, json_output)


@zip_download.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID')
@click.option('-r', '--request-id', required=True, help='[REQUIRED] Zip download request ID')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def get(ctx: click.Context, database_id: str, asset_id: str, request_id: str, json_output: bool):
    """Show one zip download, with its status and progress.

    Examples:
        vamscli zip-download get -d my-db -a my-asset -r <request-id>
        vamscli zip-download get -d my-db -a my-asset -r <request-id> --json-output
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    output_status(f"Retrieving zip download '{request_id}'...", json_output)
    try:
        job = api_client.get_zip_download(database_id, asset_id, request_id)
        output_result(job, json_output, cli_formatter=format_job)
        return job
    except _HANDLED_ERRORS as e:
        _report(e, json_output)


@zip_download.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID')
@click.option('-r', '--request-id', required=True, help='[REQUIRED] Zip download request ID')
@click.option('-o', '--output', required=True,
              help='[REQUIRED] Local file, or an existing directory to save the archive into '
                   'under its own name')
@click.option('--wait', is_flag=True, help='Wait until the archive is ready first')
@click.option('--timeout', type=click.IntRange(min=1), default=None,
              help=f'Seconds to wait with --wait (default {ZIP_DOWNLOAD_DEFAULT_WAIT_TIMEOUT_SECONDS})')
@click.option('--overwrite', is_flag=True, help='Replace an existing local file')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def download(ctx: click.Context, database_id: str, asset_id: str, request_id: str, output: str,
             wait: bool, timeout: Optional[int], overwrite: bool, json_output: bool):
    """Download a finished archive to a local file.

    The archive streams from a short-lived presigned URL that is never printed. A partial
    transfer leaves no file behind.

    Examples:
        vamscli zip-download download -d my-db -a my-asset -r <request-id> -o ./downloads
        vamscli zip-download download -d my-db -a my-asset -r <request-id> -o model.zip --wait
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    try:
        if timeout is not None and not wait:
            raise InvalidZipDownloadRequestError("--timeout applies with --wait")
        if wait:
            output_status(f"Waiting for zip download '{request_id}'...", json_output)
            wait_for_zip_download(api_client, database_id, asset_id, request_id,
                                  timeout or ZIP_DOWNLOAD_DEFAULT_WAIT_TIMEOUT_SECONDS,
                                  on_update=_status_printer(json_output))
        result = _fetch_archive(api_client, database_id, asset_id, request_id, output,
                                overwrite, json_output)
        output_result(result, json_output, success_message="✓ Archive downloaded!",
                      cli_formatter=lambda r: f"Saved to: {r['localPath']} "
                                              f"({r['bytesWrittenFormatted']})")
        return result
    except _HANDLED_ERRORS as e:
        _report(e, json_output)


@zip_download.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID')
@click.option('-r', '--request-id', required=True, help='[REQUIRED] Zip download request ID')
@click.option('--confirm', is_flag=True, help='Confirm cancelling the job')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def cancel(ctx: click.Context, database_id: str, asset_id: str, request_id: str, confirm: bool,
           json_output: bool):
    """Cancel a queued or running zip download.

    The Zip Archive run stops and its partial archive is discarded; the job stays listed as
    CANCELLED. A job that has already finished is refused: delete it instead. The --confirm
    flag is required.

    Examples:
        vamscli zip-download cancel -d my-db -a my-asset -r <request-id> --confirm
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    try:
        _require_confirm(confirm, json_output, "Cancelling a zip download")
        if not json_output and not confirm_destructive_action():
            output_status("Cancellation aborted.", json_output)
            return None
        output_status(f"Cancelling zip download '{request_id}'...", json_output)
        result = api_client.cancel_zip_download(database_id, asset_id, request_id)
        if isinstance(result, dict) and result.get('action') == 'deleted':
            output_warning("The job finished before it could be cancelled, so it was deleted.",
                           json_output)
        output_result(result, json_output, success_message="✓ Zip download cancelled!")
        return result
    except _HANDLED_ERRORS as e:
        _report(e, json_output)


@zip_download.command()
@click.option('-d', '--database-id', required=True, help='[REQUIRED] Database ID')
@click.option('-a', '--asset-id', required=True, help='[REQUIRED] Asset ID')
@click.option('-r', '--request-id', required=True, help='[REQUIRED] Zip download request ID')
@click.option('--confirm', is_flag=True, help='Confirm deleting the job and its archive')
@click.option('--json-output', is_flag=True, help='Output raw JSON response')
@click.pass_context
@requires_setup_and_auth
def delete(ctx: click.Context, database_id: str, asset_id: str, request_id: str, confirm: bool,
           json_output: bool):
    """Delete a finished zip download and its stored archive.

    ⚠️  Not reversible. A job that is still queued or running is refused: cancel it first. The
    --confirm flag is required.

    Examples:
        vamscli zip-download delete -d my-db -a my-asset -r <request-id> --confirm
    """
    # Setup/auth already validated by decorator
    api_client = _api_client(ctx)

    try:
        _require_confirm(confirm, json_output, "Deleting a zip download")
        if not json_output:
            output_warning(f"⚠  You are about to delete zip download '{request_id}' and its archive",
                           json_output)
            if not confirm_destructive_action():
                output_status("Deletion cancelled.", json_output)
                return None
        output_status(f"Deleting zip download '{request_id}'...", json_output)
        result = api_client.delete_zip_download(database_id, asset_id, request_id)
        output_result(result, json_output, success_message="✓ Zip download deleted!")
        return result
    except _HANDLED_ERRORS as e:
        _report(e, json_output)
