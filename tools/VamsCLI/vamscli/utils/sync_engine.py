"""Sync planning engine for comparing local directories against VAMS asset files."""

from pathlib import Path
from typing import Any, Callable, Dict, List, NamedTuple, Optional

from ..constants import SYNC_MTIME_TOLERANCE_SECONDS
from .download_manager import parse_remote_timestamp
from .exceptions import GlobalInfrastructureError, SyncPlanError
from .vamsignore import VamsIgnoreMatcher


class LocalFileState(NamedTuple):
    """A local file participating in a sync comparison."""
    relative_key: str  # Full asset-relative key including the asset location prefix
    sync_path: str     # Path relative to the sync root (no leading slash)
    local_path: Path
    size: int
    mtime: float       # Epoch seconds


class RemoteFileState(NamedTuple):
    """A remote asset file participating in a sync comparison."""
    relative_key: str
    sync_path: str
    size: Optional[int]
    mtime: Optional[float]  # Epoch seconds from dateCreatedCurrentVersion
    version_id: Optional[str] = None
    etag: Optional[str] = None


# Reasons attached to planned sync actions
REASON_MISSING = "missing"
REASON_SIZE_MISMATCH = "size-mismatch"
REASON_NEWER = "newer"

# Conflict classifications from revision-history checking
CONFLICT_LOCAL_MODIFIED = "local-modified"    # Pull would overwrite local-only edits
CONFLICT_REMOTE_NEWER = "remote-newer"        # Push would revert newer remote work with an outdated copy
CONFLICT_BOTH_MODIFIED = "both-modified"      # Both sides changed independently


def normalize_asset_location(asset_location: str) -> str:
    """Normalize an asset location to have leading and trailing slashes."""
    location = (asset_location or '/').replace('\\', '/')
    if not location.startswith('/'):
        location = '/' + location
    if not location.endswith('/'):
        location += '/'
    return location


def is_syncable_key(sync_path: str) -> bool:
    """Whether a file can participate in sync push/pull.

    Preview companion files (.previewFile.*) never appear as items in the
    file listing API, and file names without an extension are rejected at
    upload initialization, so both are excluded from sync comparisons.
    """
    if '.previewFile.' in sync_path:
        return False
    file_name = sync_path.rsplit('/', 1)[-1]
    return '.' in file_name


def collect_local_files(directory: Path, asset_location: str = '/') -> List[LocalFileState]:
    """Collect all files under a local directory for sync comparison."""
    if not directory.exists():
        raise SyncPlanError(f"Local directory not found: {directory}")
    if not directory.is_dir():
        raise SyncPlanError(f"Local path is not a directory: {directory}")

    location = normalize_asset_location(asset_location)
    files = []
    for file_path in sorted(directory.glob('**/*')):
        if not file_path.is_file():
            continue
        sync_path = str(file_path.relative_to(directory)).replace('\\', '/')
        try:
            stat = file_path.stat()
        except OSError:
            # File removed between directory scan and stat
            continue
        files.append(LocalFileState(
            relative_key=location + sync_path,
            sync_path=sync_path,
            local_path=file_path,
            size=stat.st_size,
            mtime=stat.st_mtime
        ))
    return files


def map_remote_files(items: List[Dict[str, Any]], asset_location: str = '/') -> List[RemoteFileState]:
    """Map file listing API items under an asset location into sync states.

    Items must come from a listing WITHOUT the server-side prefix parameter
    (which rebases relativePath); filtering happens here instead.
    """
    location = normalize_asset_location(asset_location)
    remote = []
    for item in items:
        if item.get('isFolder'):
            continue
        relative_path = item.get('relativePath') or ''
        if not relative_path.startswith('/'):
            relative_path = '/' + relative_path
        if not relative_path.startswith(location):
            continue
        sync_path = relative_path[len(location):]
        if not sync_path:
            continue
        remote.append(RemoteFileState(
            relative_key=relative_path,
            sync_path=sync_path,
            size=item.get('size'),
            mtime=parse_remote_timestamp(item.get('dateCreatedCurrentVersion')),
            version_id=item.get('versionId'),
            etag=item.get('etag')
        ))
    return remote


class SyncPlan:
    """A computed set of sync actions plus everything intentionally not acted on."""

    def __init__(self, direction: str):
        self.direction = direction  # 'push' or 'pull'
        self.transfers: List[Dict[str, Any]] = []          # Files to upload (push) or download (pull)
        self.deletes: List[Dict[str, Any]] = []            # Remote files to archive/delete (push) or local files to delete (pull)
        self.unchanged: List[Dict[str, Any]] = []
        self.skipped_modify: List[Dict[str, Any]] = []     # Differ but modifications not allowed
        self.skipped_delete: List[Dict[str, Any]] = []     # Delete candidates but deletions not allowed
        self.ignored: List[Dict[str, Any]] = []            # Matched ignore patterns
        self.unsupported: List[Dict[str, Any]] = []        # Cannot participate in sync (previews, no extension)
        self.conflicts: List[Dict[str, Any]] = []          # Revision-history conflicts (skipped, warned)

    @property
    def has_changes(self) -> bool:
        return bool(self.transfers or self.deletes)

    @property
    def transfer_size(self) -> int:
        return sum(entry.get('size') or 0 for entry in self.transfers)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'direction': self.direction,
            'transfers': self.transfers,
            'deletes': self.deletes,
            'unchanged': len(self.unchanged),
            'skipped_modify': self.skipped_modify,
            'skipped_delete': self.skipped_delete,
            'ignored': self.ignored,
            'unsupported': self.unsupported,
            'conflicts': self.conflicts,
            'summary': {
                'transfer_count': len(self.transfers),
                'transfer_size': self.transfer_size,
                'delete_count': len(self.deletes),
                'unchanged_count': len(self.unchanged),
                'skipped_modify_count': len(self.skipped_modify),
                'skipped_delete_count': len(self.skipped_delete),
                'ignored_count': len(self.ignored),
                'unsupported_count': len(self.unsupported),
                'conflict_count': len(self.conflicts),
            }
        }


def _source_is_newer(source_mtime: Optional[float], dest_mtime: Optional[float],
                     tolerance: float = SYNC_MTIME_TOLERANCE_SECONDS) -> bool:
    """Whether the source timestamp is newer than the destination's.

    Unknown timestamps on either side are treated as newer so the file is
    synced rather than silently skipped.
    """
    if source_mtime is None or dest_mtime is None:
        return True
    return source_mtime > dest_mtime + tolerance


def _transfer_entry(relative_key: str, sync_path: str, size: Optional[int], reason: str,
                    local_path: Optional[Path] = None) -> Dict[str, Any]:
    entry = {'relativeKey': relative_key, 'syncPath': sync_path, 'size': size, 'reason': reason}
    if local_path is not None:
        entry['localPath'] = str(local_path)
    return entry


def build_sync_plan(direction: str,
                    local_files: List[LocalFileState],
                    remote_files: List[RemoteFileState],
                    ignore_matcher: Optional[VamsIgnoreMatcher] = None,
                    allow_modify: bool = False,
                    allow_delete: bool = False,
                    size_only: bool = False) -> SyncPlan:
    """Compare local and remote file states and produce a sync plan.

    direction 'push' treats local as source and remote as destination;
    'pull' is the reverse. Files matching the ignore matcher are excluded
    from all comparisons on both sides. Without allow_modify only missing
    files transfer; without allow_delete no delete actions are planned.
    """
    if direction not in ('push', 'pull'):
        raise SyncPlanError(f"Invalid sync direction: {direction}")

    matcher = ignore_matcher or VamsIgnoreMatcher()
    plan = SyncPlan(direction)

    local_by_path: Dict[str, LocalFileState] = {}
    for state in local_files:
        if matcher.is_ignored(state.sync_path):
            plan.ignored.append(_transfer_entry(state.relative_key, state.sync_path,
                                                state.size, 'local', state.local_path))
        elif not is_syncable_key(state.sync_path):
            plan.unsupported.append(_transfer_entry(state.relative_key, state.sync_path,
                                                    state.size, 'local', state.local_path))
        else:
            local_by_path[state.sync_path] = state

    remote_by_path: Dict[str, RemoteFileState] = {}
    for remote_state in remote_files:
        if matcher.is_ignored(remote_state.sync_path):
            plan.ignored.append(_transfer_entry(remote_state.relative_key, remote_state.sync_path,
                                                remote_state.size, 'remote'))
        elif not is_syncable_key(remote_state.sync_path):
            plan.unsupported.append(_transfer_entry(remote_state.relative_key, remote_state.sync_path,
                                                    remote_state.size, 'remote'))
        else:
            remote_by_path[remote_state.sync_path] = remote_state

    if direction == 'push':
        source_by_path: Dict[str, Any] = local_by_path
        dest_by_path: Dict[str, Any] = remote_by_path
    else:
        source_by_path = remote_by_path
        dest_by_path = local_by_path

    for sync_path, source in source_by_path.items():
        local_state = local_by_path.get(sync_path)
        local_path = local_state.local_path if local_state else None
        dest = dest_by_path.get(sync_path)

        if dest is None:
            plan.transfers.append(_transfer_entry(source.relative_key, sync_path,
                                                  source.size, REASON_MISSING, local_path))
            continue

        if source.size is None or dest.size is None or source.size != dest.size:
            reason = REASON_SIZE_MISMATCH
        elif not size_only and _source_is_newer(source.mtime, dest.mtime):
            reason = REASON_NEWER
        else:
            plan.unchanged.append(_transfer_entry(source.relative_key, sync_path,
                                                  source.size, 'unchanged', local_path))
            continue

        entry = _transfer_entry(source.relative_key, sync_path, source.size, reason, local_path)
        if allow_modify:
            plan.transfers.append(entry)
        else:
            plan.skipped_modify.append(entry)

    # Destination files with no source counterpart are delete candidates
    for sync_path, dest in dest_by_path.items():
        if sync_path in source_by_path:
            continue
        local_state = local_by_path.get(sync_path)
        local_path = local_state.local_path if local_state else None
        entry = _transfer_entry(dest.relative_key, sync_path, dest.size, REASON_MISSING, local_path)
        if allow_delete:
            plan.deletes.append(entry)
        else:
            plan.skipped_delete.append(entry)

    return plan


def _matching_version(local_size: int, local_mtime: float, versions: List[Dict[str, Any]],
                      tolerance: float = SYNC_MTIME_TOLERANCE_SECONDS) -> Optional[Dict[str, Any]]:
    """Find a revision-history version whose size and timestamp match a local file.

    Pulled files carry the remote version's timestamp as their mtime, so an
    unmodified local copy of any historical version matches exactly.
    """
    for version in versions:
        if version.get('size') != local_size:
            continue
        version_mtime = parse_remote_timestamp(version.get('lastModified'))
        if version_mtime is None:
            continue
        if abs(local_mtime - version_mtime) <= tolerance:
            return version
    return None


def apply_conflict_checks(plan: SyncPlan,
                          local_files: List[LocalFileState],
                          remote_files: List[RemoteFileState],
                          history_lookup: Callable[[str], List[Dict[str, Any]]]) -> None:
    """Move conflicting modify transfers out of a plan using revision history.

    For each transfer that overwrites an existing file, the destination file
    is compared against the remote file's revision history (per-version size
    and lastModified from the file info API):

    - Pull: a local file matching any known version is just an outdated copy
      and is safe to overwrite. A local file matching no version has
      independent local modifications, so pulling over it is a conflict.
    - Push: a local file matching an older (non-current) version is an
      outdated copy whose push would revert newer remote work. A locally
      edited file (matching no version) conflicts when the remote current
      version is newer than the local edit, meaning someone else pushed
      after the local change was made.

    Conflicting entries are removed from plan.transfers and appended to
    plan.conflicts with a 'conflict' classification. History lookup failures
    leave the transfer in place (fail open).
    """
    local_by_path = {state.sync_path: state for state in local_files}
    remote_by_path = {state.sync_path: state for state in remote_files}

    kept: List[Dict[str, Any]] = []
    for entry in plan.transfers:
        if entry['reason'] == REASON_MISSING:
            kept.append(entry)
            continue

        local_state = local_by_path.get(entry['syncPath'])
        remote_state = remote_by_path.get(entry['syncPath'])
        if local_state is None or remote_state is None:
            kept.append(entry)
            continue

        try:
            versions = history_lookup(entry['relativeKey']) or []
        except GlobalInfrastructureError:
            raise
        except Exception:
            kept.append(entry)
            continue

        matched = _matching_version(local_state.size, local_state.mtime, versions)
        local_matches_current = bool(matched and matched.get('isLatest'))
        conflict = None

        if plan.direction == 'pull':
            if matched is None:
                conflict = CONFLICT_LOCAL_MODIFIED
        else:
            if matched is not None and not local_matches_current:
                conflict = CONFLICT_REMOTE_NEWER
            elif matched is None and not _source_is_newer(local_state.mtime, remote_state.mtime):
                conflict = CONFLICT_BOTH_MODIFIED

        if conflict:
            plan.conflicts.append({**entry, 'conflict': conflict})
        else:
            kept.append(entry)

    plan.transfers = kept
