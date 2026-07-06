"""Sync commands for VamsCLI."""

import asyncio
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import click

from ..constants import (
    DEFAULT_PARALLEL_UPLOADS, DEFAULT_RETRY_ATTEMPTS, DEFAULT_PARALLEL_DOWNLOADS,
    DEFAULT_DOWNLOAD_RETRY_ATTEMPTS, DEFAULT_DOWNLOAD_TIMEOUT, DEFAULT_IGNORE_FILE_NAME
)
from ..utils.decorators import requires_setup_and_auth, get_profile_manager_from_context
from ..utils.api_client import APIClient
from ..utils.json_output import output_status, output_result, output_error, output_warning
from ..utils.logging import log_debug
from ..utils.exceptions import (
    SyncError, SyncPlanError, SyncPushError, SyncPullError,
    SyncConfirmationRequiredError, InvalidSyncIgnoreFileError,
    InvalidFileError, FileTooLargeError, UploadSequenceError, FileUploadError,
    AssetNotFoundError, DatabaseNotFoundError, AssetVersionError, APIError
)
from ..utils.file_processor import (
    FileInfo, create_upload_sequences, validate_file_for_upload,
    validate_file_extensions, format_file_size
)
from ..utils.upload_manager import UploadManager, format_duration
from ..utils.download_manager import (
    DownloadManager, DownloadFileInfo, StreamingDownloadProgress, parse_remote_timestamp,
    generate_presigned_urls
)
from ..utils.sync_engine import (
    apply_conflict_checks, build_sync_plan, collect_local_files, map_remote_files,
    normalize_asset_location, SyncPlan
)
from ..utils.vamsignore import VamsIgnoreMatcher
from .file import parse_json_input, ProgressDisplay
from .assets import list_all_asset_files, DownloadProgressDisplay


@click.group()
def sync():
    """Synchronization commands."""
    pass


@sync.group('file')
def sync_file():
    """Synchronize files between a local directory and an asset.

    Compares local files against the asset's current files using size and
    modified timestamps (like S3 sync), then transfers only the differences.
    Push uploads local changes to VAMS; pull downloads remote changes locally.
    """
    pass


def _merge_json_input(json_data: Dict[str, Any], values: Dict[str, Any]) -> Dict[str, Any]:
    """Override command values with matching keys from --json-input data.

    Flag values must be JSON booleans — truthy strings like "no" or "false"
    must never enable a destructive flag.
    """
    for key in values:
        if key not in json_data:
            continue
        new_value = json_data[key]
        if isinstance(values[key], bool) and not isinstance(new_value, bool):
            raise click.ClickException(
                f"JSON input key '{key}' must be a boolean (true/false), "
                f"got: {new_value!r}"
            )
        values[key] = new_value
    return values


def _validate_sync_args(local_directory: Optional[str], database_id: str, asset_id: str,
                        allow_delete: bool, permanent_delete: bool, confirm: bool,
                        dryrun: bool, direction: str,
                        version_comment: Optional[str] = None) -> Path:
    """Validate common sync arguments and safeguard flags."""
    if not local_directory:
        raise click.ClickException("Local directory is required")
    if not database_id:
        raise click.ClickException("Database ID is required (-d/--database)")
    if not asset_id:
        raise click.ClickException("Asset ID is required (-a/--asset)")

    directory = Path(local_directory)
    if not directory.exists():
        raise click.ClickException(f"Local directory not found: {directory}")
    if not directory.is_dir():
        raise click.ClickException(f"Local path is not a directory: {directory}")

    if permanent_delete and not allow_delete:
        raise click.ClickException("--permanent-delete requires --allow-delete")

    if version_comment is not None and not 1 <= len(version_comment.strip()) <= 256:
        raise click.ClickException("--version-comment must be 1-256 characters")

    if not dryrun:
        if direction == 'push' and permanent_delete and not confirm:
            raise SyncConfirmationRequiredError(
                "Permanently deleting files in VAMS requires the --confirm flag. "
                "Without --permanent-delete, deleted files are archived and recoverable."
            )
        if direction == 'pull' and allow_delete and not confirm:
            raise SyncConfirmationRequiredError(
                "Deleting local files requires the --confirm flag. "
                "Local file deletion cannot be undone."
            )

    return directory


def _build_ignore_matcher(directory: Path, ignore_file: Optional[str],
                          no_ignore: bool) -> VamsIgnoreMatcher:
    """Build the ignore matcher for a sync directory."""
    if no_ignore:
        return VamsIgnoreMatcher()
    return VamsIgnoreMatcher.for_directory(directory, ignore_file)


def _fetch_remote_states(api_client: APIClient, database_id: str, asset_id: str,
                         asset_location: str) -> List:
    """List all current asset files and map those under the asset location.

    Lists without the server-side prefix parameter (which rebases relativePath
    to be prefix-relative); filtering happens client-side to keep keys
    asset-root-relative.
    """
    items = list_all_asset_files(api_client, database_id, asset_id, {
        'includeArchived': 'false',
        'basic': 'true'
    })
    return map_remote_files(items, asset_location)


def _safe_local_path(root: Path, sync_path: str) -> Path:
    """Resolve a sync path under the local root, rejecting path traversal."""
    candidate = (root / sync_path.lstrip('/')).resolve()
    root_resolved = root.resolve()
    if root_resolved != candidate and root_resolved not in candidate.parents:
        raise SyncPullError(f"Remote file path escapes the local directory: {sync_path}")
    return candidate


def _clean_upload_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Strip non-serializable progress objects from an upload result."""
    clean = {k: v for k, v in result.items() if k != 'progress'}
    if 'sequence_results' in clean:
        clean['sequence_results'] = [
            {k: v for k, v in seq.items() if k != 'progress'}
            for seq in clean['sequence_results']
        ]
    return clean


def _apply_conflict_checks(api_client: APIClient, database_id: str, asset_id: str,
                           plan: SyncPlan, local_states: List, remote_states: List,
                           json_output: bool) -> None:
    """Run revision-history conflict checking against planned modify transfers."""
    modify_count = sum(1 for t in plan.transfers if t['reason'] != 'missing')
    if not modify_count:
        return
    output_status(f"Checking {modify_count} changed file(s) against revision history...",
                  json_output)

    def history_lookup(relative_key: str) -> List[Dict[str, Any]]:
        file_info = api_client.get_file_info(database_id, asset_id, {
            'filePath': relative_key,
            'includeVersions': 'true'
        })
        return file_info.get('versions') or []

    apply_conflict_checks(plan, local_states, remote_states, history_lookup)
    for entry in plan.conflicts:
        output_warning(f"⚠️  Conflict ({entry['conflict']}): {entry['syncPath']} - skipped",
                       json_output)


def _format_plan_lines(plan_data: Dict[str, Any], direction: str) -> List[str]:
    """Format a sync plan for CLI display."""
    summary = plan_data['summary']
    transfer_verb = 'upload' if direction == 'push' else 'download'
    delete_noun = 'remote file(s)' if direction == 'push' else 'local file(s)'

    lines = []
    lines.append(f"Sync plan ({direction}):")
    lines.append(f"  To {transfer_verb}: {summary['transfer_count']} file(s), "
                 f"{format_file_size(summary['transfer_size'])}")
    for entry in plan_data['transfers']:
        lines.append(f"    + {entry['syncPath']} ({entry['reason']})")

    lines.append(f"  To delete: {summary['delete_count']} {delete_noun}")
    for entry in plan_data['deletes']:
        lines.append(f"    - {entry['syncPath']}")

    lines.append(f"  Unchanged: {summary['unchanged_count']} file(s)")

    if summary.get('conflict_count'):
        lines.append(f"  Conflicts (skipped, resolve manually): {summary['conflict_count']} file(s)")
        for entry in plan_data['conflicts']:
            lines.append(f"    ! {entry['syncPath']} ({entry['conflict']})")

    if summary['skipped_modify_count']:
        lines.append(f"  Skipped (modified, use --allow-modify): {summary['skipped_modify_count']} file(s)")
        for entry in plan_data['skipped_modify']:
            lines.append(f"    ~ {entry['syncPath']} ({entry['reason']})")
    if summary['skipped_delete_count']:
        lines.append(f"  Skipped (delete candidates, use --allow-delete): {summary['skipped_delete_count']} file(s)")
        for entry in plan_data['skipped_delete']:
            lines.append(f"    ~ {entry['syncPath']}")
    if summary['ignored_count']:
        lines.append(f"  Ignored by patterns: {summary['ignored_count']} file(s)")
    if summary['unsupported_count']:
        lines.append(f"  Unsupported (previews or no file extension): {summary['unsupported_count']} file(s)")

    return lines


@sync_file.command('push')
@click.argument('local_directory', required=False, type=click.Path())
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--asset-location', default='/',
              help='Asset directory to sync against (default: "/", the whole asset)')
@click.option('--dryrun', is_flag=True,
              help='Report what would change without transferring or deleting anything')
@click.option('--allow-modify', is_flag=True,
              help='Upload files that exist in VAMS but differ locally (default: only add missing files)')
@click.option('--allow-delete', is_flag=True,
              help='Archive VAMS files that no longer exist locally')
@click.option('--permanent-delete', is_flag=True,
              help='Permanently delete instead of archive (requires --allow-delete and --confirm)')
@click.option('--confirm', is_flag=True,
              help='Confirm permanent deletion of VAMS files')
@click.option('--size-only', is_flag=True,
              help='Compare files by size only, ignoring modified timestamps')
@click.option('--conflict-check', is_flag=True,
              help='Check changed files against remote revision history and skip files whose '
                   'push would revert newer remote work (slower: one API call per changed file)')
@click.option('--ignore-file', type=click.Path(),
              help=f'Ignore-pattern file to use instead of {DEFAULT_IGNORE_FILE_NAME} in the sync directory')
@click.option('--no-ignore', is_flag=True,
              help='Disable ignore-pattern processing entirely')
@click.option('--version-comment',
              help='Create an asset version with this comment after a successful push (1-256 characters)')
@click.option('--parallel-uploads', type=int, default=DEFAULT_PARALLEL_UPLOADS,
              help=f'Max parallel uploads (default: {DEFAULT_PARALLEL_UPLOADS})')
@click.option('--retry-attempts', type=int, default=DEFAULT_RETRY_ATTEMPTS,
              help=f'Retry attempts per part (default: {DEFAULT_RETRY_ATTEMPTS})')
@click.option('--json-input',
              help='JSON input with all parameters (file path with @ prefix or JSON string)')
@click.option('--json-output', is_flag=True,
              help='Output raw JSON response')
@click.option('--hide-progress', is_flag=True,
              help='Hide upload progress display')
@click.pass_context
@requires_setup_and_auth
def push(ctx: click.Context, local_directory, database_id, asset_id, asset_location,
         dryrun, allow_modify, allow_delete, permanent_delete, confirm, size_only,
         conflict_check, ignore_file, no_ignore, version_comment, parallel_uploads,
         retry_attempts, json_input, json_output, hide_progress):
    """
    Push local file changes up to an asset.

    Compares the local directory against the asset's current files and uploads
    new files. With --allow-modify, changed files are also uploaded; with
    --allow-delete, VAMS files missing locally are archived (or permanently
    deleted with --permanent-delete --confirm). Files matching patterns in
    .vamsignore (gitignore syntax) are excluded from the comparison.

    Examples:
        vamscli sync file push ./models -d db1 -a asset1 --dryrun
        vamscli sync file push ./models -d db1 -a asset1 --allow-modify
        vamscli sync file push ./models -d db1 -a asset1 --allow-modify --allow-delete
        vamscli sync file push ./models -d db1 -a asset1 --allow-delete --permanent-delete --confirm
    """
    try:
        json_data = parse_json_input(json_input) if json_input else {}
        if json_data:
            values = _merge_json_input(json_data, {
                'local_directory': local_directory, 'database_id': database_id,
                'asset_id': asset_id, 'asset_location': asset_location, 'dryrun': dryrun,
                'allow_modify': allow_modify, 'allow_delete': allow_delete,
                'permanent_delete': permanent_delete, 'confirm': confirm,
                'size_only': size_only, 'conflict_check': conflict_check,
                'ignore_file': ignore_file, 'no_ignore': no_ignore,
                'version_comment': version_comment, 'parallel_uploads': parallel_uploads,
                'retry_attempts': retry_attempts, 'hide_progress': hide_progress,
            })
            (local_directory, database_id, asset_id, asset_location, dryrun,
             allow_modify, allow_delete, permanent_delete, confirm, size_only,
             conflict_check, ignore_file, no_ignore, version_comment, parallel_uploads,
             retry_attempts, hide_progress) = (
                values['local_directory'], values['database_id'], values['asset_id'],
                values['asset_location'], values['dryrun'], values['allow_modify'],
                values['allow_delete'], values['permanent_delete'], values['confirm'],
                values['size_only'], values['conflict_check'], values['ignore_file'],
                values['no_ignore'], values['version_comment'], values['parallel_uploads'],
                values['retry_attempts'], values['hide_progress'])

        if json_output:
            hide_progress = True

        directory = _validate_sync_args(local_directory, database_id, asset_id,
                                        allow_delete, permanent_delete, confirm,
                                        dryrun, 'push', version_comment)

        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)

        output_status(f"Verifying asset '{asset_id}'...", json_output)
        api_client.get_asset(database_id, asset_id)

        ignore_matcher = _build_ignore_matcher(directory, ignore_file, no_ignore)

        output_status("Collecting local files...", json_output)
        local_states = collect_local_files(directory, asset_location)
        log_debug(f"Collected {len(local_states)} local files for sync push")

        output_status("Listing asset files...", json_output)
        remote_states = _fetch_remote_states(api_client, database_id, asset_id, asset_location)
        log_debug(f"Found {len(remote_states)} remote files under '{asset_location}'")

        plan = build_sync_plan('push', local_states, remote_states,
                               ignore_matcher=ignore_matcher,
                               allow_modify=allow_modify,
                               allow_delete=allow_delete,
                               size_only=size_only)
        if conflict_check:
            _apply_conflict_checks(api_client, database_id, asset_id, plan,
                                   local_states, remote_states, json_output)
        plan_data = plan.to_dict()

        result: Dict[str, Any] = {
            'direction': 'push',
            'databaseId': database_id,
            'assetId': asset_id,
            'assetLocation': normalize_asset_location(asset_location),
            'localDirectory': str(directory),
            'dryrun': dryrun,
            'plan': plan_data,
        }

        if dryrun or not plan.has_changes:
            message = ("Dry run - no changes applied" if dryrun
                       else "Already in sync - nothing to push")
            output_result(result, json_output, success_message=f"✅ {message}",
                          cli_formatter=lambda r: '\n'.join(_format_plan_lines(r['plan'], 'push')))
            return result

        # Confirm permanent deletions interactively (flag-only gate in JSON mode)
        if plan.deletes and permanent_delete and not json_output:
            output_warning(f"⚠️  {len(plan.deletes)} VAMS file(s) will be PERMANENTLY deleted. "
                           "This cannot be undone!", json_output)
            if not click.confirm("Are you sure you want to proceed?"):
                raise click.ClickException("Sync push aborted")

        if not json_output and not hide_progress:
            for line in _format_plan_lines(plan_data, 'push'):
                click.echo(line)
            click.echo()

        execution: Dict[str, Any] = {}

        # Upload planned transfers
        if plan.transfers:
            upload_files = [FileInfo(entry['localPath'], entry['relativeKey'])
                            for entry in plan.transfers]

            for file_info in upload_files:
                validate_file_for_upload(file_info.local_path, 'assetFile', file_info.relative_key)

            database_config = api_client.get_database(database_id)
            restricted_extensions = database_config.get('restrictFileUploadsToExtensions', '')
            if restricted_extensions and restricted_extensions.strip():
                validate_file_extensions(upload_files, restricted_extensions, 'assetFile')

            sequences = create_upload_sequences(upload_files)
            output_status(f"Uploading {len(upload_files)} file(s) in {len(sequences)} sequence(s)...",
                          json_output)

            progress_display = ProgressDisplay(hide_progress, json_output,
                                               total_sequences=len(sequences))

            async def run_upload():
                async with UploadManager(
                    api_client=api_client,
                    max_parallel=parallel_uploads,
                    max_retries=retry_attempts,
                    progress_callback=progress_display.update
                ) as upload_manager:
                    return await upload_manager.upload_all_sequences(
                        sequences, database_id, asset_id, 'assetFile'
                    )

            upload_result = asyncio.run(run_upload())

            if not json_output and not hide_progress and progress_display._lines_printed > 0:
                click.echo('\033[2K\033[1A' * progress_display._lines_printed, nl=False)
                click.echo('\033[2K', nl=False)

            execution['uploads'] = _clean_upload_result(upload_result)

        # Archive or permanently delete remote files missing locally
        if plan.deletes:
            action = 'delete' if permanent_delete else 'archive'
            output_status(f"{'Deleting' if permanent_delete else 'Archiving'} "
                          f"{len(plan.deletes)} remote file(s)...", json_output)

            delete_results = {'action': action, 'succeeded': [], 'failed': []}
            for entry in plan.deletes:
                try:
                    if permanent_delete:
                        api_client.delete_file(database_id, asset_id, {
                            'filePath': entry['relativeKey'],
                            'isPrefix': False,
                            'confirmPermanentDelete': True
                        })
                    else:
                        api_client.archive_file(database_id, asset_id, {
                            'filePath': entry['relativeKey'],
                            'isPrefix': False
                        })
                    delete_results['succeeded'].append(entry['relativeKey'])
                except (APIError, AssetNotFoundError) as e:
                    delete_results['failed'].append({
                        'relativeKey': entry['relativeKey'],
                        'error': str(e)
                    })
            execution['deletes'] = delete_results

        # Determine overall outcome before optional version creation
        uploads_ok = execution.get('uploads', {}).get('overall_success', True)
        deletes_ok = not execution.get('deletes', {}).get('failed')
        overall_success = uploads_ok and deletes_ok

        if version_comment and overall_success:
            output_status("Creating asset version...", json_output)
            try:
                version_result = api_client.create_asset_version(database_id, asset_id, {
                    'useLatestFiles': True,
                    'comment': version_comment
                })
                execution['version'] = version_result
            except (APIError, AssetVersionError) as e:
                execution['version'] = {'error': str(e)}
                output_warning(f"Push succeeded but version creation failed: {e}", json_output)

        result['execution'] = execution
        result['overall_success'] = overall_success

        def format_push_result(data):
            lines = _format_plan_lines(data['plan'], 'push')
            uploads = data['execution'].get('uploads')
            if uploads:
                lines.append("\nUpload results:")
                lines.append(f"  Successful files: {uploads['successful_files']}/{uploads['total_files']}")
                lines.append(f"  Total size: {uploads['total_size_formatted']}")
                lines.append(f"  Duration: {format_duration(uploads['upload_duration'])}")
            deletes = data['execution'].get('deletes')
            if deletes:
                verb = 'Deleted' if deletes['action'] == 'delete' else 'Archived'
                lines.append(f"\n{verb} remote files: {len(deletes['succeeded'])}")
                for failure in deletes['failed']:
                    lines.append(f"  ✗ {failure['relativeKey']}: {failure['error']}")
            version = data['execution'].get('version')
            if version:
                if version.get('error'):
                    lines.append(f"\nVersion creation failed: {version['error']}")
                else:
                    lines.append(f"\nCreated asset version: {version.get('assetVersionId', 'N/A')}")
            return '\n'.join(lines)

        success_msg = ("✅ Sync push completed successfully!" if overall_success
                       else "⚠️  Sync push completed with some failures")
        output_result(result, json_output, success_message=success_msg,
                      cli_formatter=format_push_result)
        return result

    except (SyncError, InvalidFileError, FileTooLargeError, UploadSequenceError,
            FileUploadError, AssetNotFoundError, DatabaseNotFoundError) as e:
        output_error(e, json_output, error_type="Sync Push Error")
        raise click.ClickException(str(e))


@sync_file.command('pull')
@click.argument('local_directory', required=False, type=click.Path())
@click.option('-d', '--database', 'database_id', required=True, help='Database ID')
@click.option('-a', '--asset', 'asset_id', required=True, help='Asset ID')
@click.option('--asset-location', default='/',
              help='Asset directory to sync against (default: "/", the whole asset)')
@click.option('--dryrun', is_flag=True,
              help='Report what would change without transferring or deleting anything')
@click.option('--allow-modify', is_flag=True,
              help='Download files that exist locally but differ in VAMS (default: only add missing files)')
@click.option('--allow-delete', is_flag=True,
              help='Delete local files that no longer exist in VAMS (requires --confirm)')
@click.option('--confirm', is_flag=True,
              help='Confirm deletion of local files')
@click.option('--size-only', is_flag=True,
              help='Compare files by size only, ignoring modified timestamps')
@click.option('--conflict-check', is_flag=True,
              help='Check changed files against remote revision history and skip files with '
                   'local-only modifications (slower: one API call per changed file)')
@click.option('--ignore-file', type=click.Path(),
              help=f'Ignore-pattern file to use instead of {DEFAULT_IGNORE_FILE_NAME} in the sync directory')
@click.option('--no-ignore', is_flag=True,
              help='Disable ignore-pattern processing entirely')
@click.option('--parallel-downloads', type=int, default=DEFAULT_PARALLEL_DOWNLOADS,
              help=f'Max parallel downloads (default: {DEFAULT_PARALLEL_DOWNLOADS})')
@click.option('--retry-attempts', type=int, default=DEFAULT_DOWNLOAD_RETRY_ATTEMPTS,
              help=f'Retry attempts per file (default: {DEFAULT_DOWNLOAD_RETRY_ATTEMPTS})')
@click.option('--timeout', type=int, default=DEFAULT_DOWNLOAD_TIMEOUT,
              help=f'Download timeout per file in seconds (default: {DEFAULT_DOWNLOAD_TIMEOUT})')
@click.option('--json-input',
              help='JSON input with all parameters (file path with @ prefix or JSON string)')
@click.option('--json-output', is_flag=True,
              help='Output raw JSON response')
@click.option('--hide-progress', is_flag=True,
              help='Hide download progress display')
@click.pass_context
@requires_setup_and_auth
def pull(ctx: click.Context, local_directory, database_id, asset_id, asset_location,
         dryrun, allow_modify, allow_delete, confirm, size_only, conflict_check,
         ignore_file, no_ignore, parallel_downloads, retry_attempts, timeout,
         json_input, json_output, hide_progress):
    """
    Pull asset file changes down to a local directory.

    Compares the asset's current files against the local directory and
    downloads new files. With --allow-modify, changed files are also
    downloaded; with --allow-delete --confirm, local files missing from VAMS
    are deleted. Downloaded files keep the remote modified timestamp so later
    syncs can detect changes. Files matching patterns in .vamsignore
    (gitignore syntax) are excluded from the comparison.

    Examples:
        vamscli sync file pull ./models -d db1 -a asset1 --dryrun
        vamscli sync file pull ./models -d db1 -a asset1 --allow-modify
        vamscli sync file pull ./models -d db1 -a asset1 --allow-modify --allow-delete --confirm
    """
    try:
        json_data = parse_json_input(json_input) if json_input else {}
        if json_data:
            values = _merge_json_input(json_data, {
                'local_directory': local_directory, 'database_id': database_id,
                'asset_id': asset_id, 'asset_location': asset_location, 'dryrun': dryrun,
                'allow_modify': allow_modify, 'allow_delete': allow_delete,
                'confirm': confirm, 'size_only': size_only,
                'conflict_check': conflict_check, 'ignore_file': ignore_file,
                'no_ignore': no_ignore, 'parallel_downloads': parallel_downloads,
                'retry_attempts': retry_attempts, 'timeout': timeout,
                'hide_progress': hide_progress,
            })
            (local_directory, database_id, asset_id, asset_location, dryrun,
             allow_modify, allow_delete, confirm, size_only, conflict_check,
             ignore_file, no_ignore, parallel_downloads, retry_attempts, timeout,
             hide_progress) = (
                values['local_directory'], values['database_id'], values['asset_id'],
                values['asset_location'], values['dryrun'], values['allow_modify'],
                values['allow_delete'], values['confirm'], values['size_only'],
                values['conflict_check'], values['ignore_file'], values['no_ignore'],
                values['parallel_downloads'], values['retry_attempts'], values['timeout'],
                values['hide_progress'])

        if json_output:
            hide_progress = True

        directory = _validate_sync_args(local_directory, database_id, asset_id,
                                        allow_delete, False, confirm, dryrun, 'pull')

        # Setup/auth already validated by decorator
        profile_manager = get_profile_manager_from_context(ctx)
        config = profile_manager.load_config()
        api_client = APIClient(config['api_gateway_url'], profile_manager)

        output_status(f"Verifying asset '{asset_id}'...", json_output)
        asset_data = api_client.get_asset(database_id, asset_id)
        if not asset_data.get('isDistributable', True):
            raise SyncPullError(
                f"Asset '{asset_id}' is not distributable - files cannot be downloaded"
            )

        ignore_matcher = _build_ignore_matcher(directory, ignore_file, no_ignore)

        output_status("Listing asset files...", json_output)
        remote_states = _fetch_remote_states(api_client, database_id, asset_id, asset_location)
        log_debug(f"Found {len(remote_states)} remote files under '{asset_location}'")

        output_status("Collecting local files...", json_output)
        local_states = collect_local_files(directory, asset_location)
        log_debug(f"Collected {len(local_states)} local files for sync pull")

        plan = build_sync_plan('pull', local_states, remote_states,
                               ignore_matcher=ignore_matcher,
                               allow_modify=allow_modify,
                               allow_delete=allow_delete,
                               size_only=size_only)
        if conflict_check:
            _apply_conflict_checks(api_client, database_id, asset_id, plan,
                                   local_states, remote_states, json_output)
        plan_data = plan.to_dict()

        result: Dict[str, Any] = {
            'direction': 'pull',
            'databaseId': database_id,
            'assetId': asset_id,
            'assetLocation': normalize_asset_location(asset_location),
            'localDirectory': str(directory),
            'dryrun': dryrun,
            'plan': plan_data,
        }

        if dryrun or not plan.has_changes:
            message = ("Dry run - no changes applied" if dryrun
                       else "Already in sync - nothing to pull")
            output_result(result, json_output, success_message=f"✅ {message}",
                          cli_formatter=lambda r: '\n'.join(_format_plan_lines(r['plan'], 'pull')))
            return result

        # Confirm local deletions interactively (flag-only gate in JSON mode)
        if plan.deletes and not json_output:
            output_warning(f"⚠️  {len(plan.deletes)} local file(s) will be deleted. "
                           "This cannot be undone!", json_output)
            if not click.confirm("Are you sure you want to proceed?"):
                raise click.ClickException("Sync pull aborted")

        if not json_output and not hide_progress:
            for line in _format_plan_lines(plan_data, 'pull'):
                click.echo(line)
            click.echo()

        execution: Dict[str, Any] = {}
        remote_by_sync_path = {state.sync_path: state for state in remote_states}

        # Download planned transfers, generating presigned URLs while downloading
        if plan.transfers:
            output_status(f"Downloading {len(plan.transfers)} file(s)...", json_output)
            progress_display = DownloadProgressDisplay(hide_progress=hide_progress)
            url_failures: List[Dict[str, Any]] = []

            async def run_download():
                queue: asyncio.Queue = asyncio.Queue(maxsize=parallel_downloads * 2)
                streaming_progress = StreamingDownloadProgress()

                async def _produce():  # nosemgrep: useless-inner-function
                    # The None sentinel must reach the queue on every exit path
                    # or the consumer blocks forever
                    try:
                        # Generate presigned URLs in bulk (chunked server-side calls)
                        url_map = generate_presigned_urls(
                            api_client, database_id, asset_id,
                            [entry['relativeKey'] for entry in plan.transfers]
                        )
                        for entry in plan.transfers:
                            remote_state = remote_by_sync_path.get(entry['syncPath'])
                            url_entry = url_map.get(entry['relativeKey'], {})
                            try:
                                local_path = _safe_local_path(directory, entry['syncPath'])
                                if not url_entry.get('downloadUrl'):
                                    raise SyncPullError(
                                        url_entry.get('error') or 'URL generation failed'
                                    )
                                await queue.put(DownloadFileInfo(
                                    relative_key=entry['relativeKey'],
                                    local_path=local_path,
                                    download_url=url_entry['downloadUrl'],
                                    file_size=remote_state.size if remote_state else None,
                                    last_modified=remote_state.mtime if remote_state else None
                                ))
                            except Exception as e:
                                url_failures.append({
                                    'relativeKey': entry['relativeKey'],
                                    'error': str(e)
                                })
                    finally:
                        await queue.put(None)

                async with DownloadManager(
                    api_client, max_parallel=parallel_downloads,
                    max_retries=retry_attempts, timeout=timeout,
                    progress_callback=progress_display.update
                ) as manager:
                    producer = asyncio.create_task(_produce())
                    download_result = await manager.download_files_streamed(queue, streaming_progress)
                    await producer
                    return download_result

            download_result = asyncio.run(run_download())

            if not json_output and not hide_progress and progress_display._lines_printed > 0:
                click.echo('\033[2K\033[1A' * progress_display._lines_printed, nl=False)
                click.echo('\033[2K', nl=False)

            if url_failures:
                download_result['failed_downloads'] = (
                    download_result.get('failed_downloads', []) +
                    [{'relative_key': f['relativeKey'], 'local_path': '', 'error': f['error']}
                     for f in url_failures]
                )
                download_result['failed_files'] = len(download_result['failed_downloads'])
                download_result['overall_success'] = False
            execution['downloads'] = download_result

        # Delete local files missing from VAMS
        if plan.deletes:
            output_status(f"Deleting {len(plan.deletes)} local file(s)...", json_output)
            delete_results = {'action': 'delete', 'succeeded': [], 'failed': []}
            for entry in plan.deletes:
                try:
                    local_path = _safe_local_path(directory, entry['syncPath'])
                    local_path.unlink()
                    delete_results['succeeded'].append(str(local_path))
                except (OSError, SyncPullError) as e:
                    delete_results['failed'].append({
                        'localPath': entry.get('localPath', entry['syncPath']),
                        'error': str(e)
                    })
            execution['deletes'] = delete_results

        downloads_ok = execution.get('downloads', {}).get('overall_success', True)
        deletes_ok = not execution.get('deletes', {}).get('failed')
        overall_success = downloads_ok and deletes_ok

        result['execution'] = execution
        result['overall_success'] = overall_success

        def format_pull_result(data):
            lines = _format_plan_lines(data['plan'], 'pull')
            downloads = data['execution'].get('downloads')
            if downloads:
                lines.append("\nDownload results:")
                lines.append(f"  Successful files: {downloads['successful_files']}/{downloads['total_files']}")
                lines.append(f"  Total size: {downloads['total_size_formatted']}")
                lines.append(f"  Duration: {format_duration(downloads['download_duration'])}")
                for failure in downloads.get('failed_downloads', []):
                    lines.append(f"  ✗ {failure['relative_key']}: {failure['error']}")
            deletes = data['execution'].get('deletes')
            if deletes:
                lines.append(f"\nDeleted local files: {len(deletes['succeeded'])}")
                for failure in deletes['failed']:
                    lines.append(f"  ✗ {failure['localPath']}: {failure['error']}")
            return '\n'.join(lines)

        success_msg = ("✅ Sync pull completed successfully!" if overall_success
                       else "⚠️  Sync pull completed with some failures")
        output_result(result, json_output, success_message=success_msg,
                      cli_formatter=format_pull_result)
        return result

    except (SyncError, AssetNotFoundError, DatabaseNotFoundError) as e:
        output_error(e, json_output, error_type="Sync Pull Error")
        raise click.ClickException(str(e))
