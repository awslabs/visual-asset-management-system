"""Tests for sync engine and vamsignore utilities."""

import os
import time
from pathlib import Path

import pytest

from vamscli.utils.exceptions import InvalidSyncIgnoreFileError, SyncPlanError
from vamscli.utils.sync_engine import (
    LocalFileState, RemoteFileState, apply_conflict_checks, build_sync_plan,
    collect_local_files, is_syncable_key, map_remote_files, normalize_asset_location
)
from vamscli.utils.vamsignore import VamsIgnoreMatcher


def local_state(sync_path, size=100, mtime=1000.0, location='/'):
    return LocalFileState(
        relative_key=location + sync_path,
        sync_path=sync_path,
        local_path=Path('/local') / sync_path,
        size=size,
        mtime=mtime
    )


def remote_state(sync_path, size=100, mtime=1000.0, location='/'):
    return RemoteFileState(
        relative_key=location + sync_path,
        sync_path=sync_path,
        size=size,
        mtime=mtime
    )


class TestNormalizeAssetLocation:
    def test_root(self):
        assert normalize_asset_location('/') == '/'

    def test_empty_defaults_to_root(self):
        assert normalize_asset_location('') == '/'

    def test_adds_leading_and_trailing_slashes(self):
        assert normalize_asset_location('models') == '/models/'
        assert normalize_asset_location('/models') == '/models/'
        assert normalize_asset_location('models/') == '/models/'

    def test_backslashes_normalized(self):
        assert normalize_asset_location('models\\subdir') == '/models/subdir/'


class TestIsSyncableKey:
    def test_regular_file(self):
        assert is_syncable_key('model.glb') is True
        assert is_syncable_key('dir/model.glb') is True

    def test_preview_file_excluded(self):
        assert is_syncable_key('model.glb.previewFile.png') is False
        assert is_syncable_key('dir/model.glb.previewFile.gif') is False

    def test_no_extension_excluded(self):
        assert is_syncable_key('LICENSE') is False
        assert is_syncable_key('dir/README') is False

    def test_dot_in_directory_does_not_count(self):
        assert is_syncable_key('dir.v2/README') is False
        assert is_syncable_key('dir.v2/readme.txt') is True


class TestCollectLocalFiles:
    def test_collects_nested_files(self, tmp_path):
        (tmp_path / 'a.txt').write_text('aaa')
        (tmp_path / 'sub').mkdir()
        (tmp_path / 'sub' / 'b.txt').write_text('bb')

        files = collect_local_files(tmp_path)
        by_key = {f.sync_path: f for f in files}

        assert set(by_key) == {'a.txt', 'sub/b.txt'}
        assert by_key['a.txt'].relative_key == '/a.txt'
        assert by_key['sub/b.txt'].relative_key == '/sub/b.txt'
        assert by_key['a.txt'].size == 3
        assert by_key['sub/b.txt'].size == 2

    def test_asset_location_prefix(self, tmp_path):
        (tmp_path / 'a.txt').write_text('aaa')
        files = collect_local_files(tmp_path, 'models')
        assert files[0].relative_key == '/models/a.txt'
        assert files[0].sync_path == 'a.txt'

    def test_empty_directory_ok(self, tmp_path):
        assert collect_local_files(tmp_path) == []

    def test_missing_directory_raises(self, tmp_path):
        with pytest.raises(SyncPlanError):
            collect_local_files(tmp_path / 'missing')

    def test_file_instead_of_directory_raises(self, tmp_path):
        target = tmp_path / 'file.txt'
        target.write_text('x')
        with pytest.raises(SyncPlanError):
            collect_local_files(target)


class TestMapRemoteFiles:
    def test_maps_items(self):
        items = [
            {'relativePath': '/a.txt', 'isFolder': False, 'size': 10,
             'dateCreatedCurrentVersion': '2026-01-01T00:00:00+00:00',
             'versionId': 'v1', 'etag': 'abc'},
            {'relativePath': '/sub/', 'isFolder': True},
        ]
        remote = map_remote_files(items)
        assert len(remote) == 1
        assert remote[0].sync_path == 'a.txt'
        assert remote[0].size == 10
        assert remote[0].mtime is not None
        assert remote[0].version_id == 'v1'

    def test_filters_by_asset_location(self):
        items = [
            {'relativePath': '/models/a.txt', 'isFolder': False, 'size': 1},
            {'relativePath': '/other/b.txt', 'isFolder': False, 'size': 1},
        ]
        remote = map_remote_files(items, 'models')
        assert [r.sync_path for r in remote] == ['a.txt']

    def test_missing_timestamp_maps_to_none(self):
        remote = map_remote_files([{'relativePath': '/a.txt', 'isFolder': False, 'size': 1}])
        assert remote[0].mtime is None


class TestBuildSyncPlanPush:
    def test_missing_remote_file_transfers(self):
        plan = build_sync_plan('push', [local_state('a.txt')], [])
        assert [t['syncPath'] for t in plan.transfers] == ['a.txt']
        assert plan.transfers[0]['reason'] == 'missing'
        assert plan.transfers[0]['localPath'] == str(Path('/local/a.txt'))

    def test_identical_files_unchanged(self):
        plan = build_sync_plan('push', [local_state('a.txt')], [remote_state('a.txt')])
        assert plan.transfers == []
        assert len(plan.unchanged) == 1

    def test_size_mismatch_requires_allow_modify(self):
        local = [local_state('a.txt', size=100)]
        remote = [remote_state('a.txt', size=50)]

        plan = build_sync_plan('push', local, remote)
        assert plan.transfers == []
        assert [s['syncPath'] for s in plan.skipped_modify] == ['a.txt']

        plan = build_sync_plan('push', local, remote, allow_modify=True)
        assert [t['syncPath'] for t in plan.transfers] == ['a.txt']
        assert plan.transfers[0]['reason'] == 'size-mismatch'

    def test_newer_source_mtime_is_modify(self):
        local = [local_state('a.txt', mtime=2000.0)]
        remote = [remote_state('a.txt', mtime=1000.0)]
        plan = build_sync_plan('push', local, remote, allow_modify=True)
        assert [t['reason'] for t in plan.transfers] == ['newer']

    def test_older_source_mtime_unchanged(self):
        local = [local_state('a.txt', mtime=1000.0)]
        remote = [remote_state('a.txt', mtime=2000.0)]
        plan = build_sync_plan('push', local, remote, allow_modify=True)
        assert plan.transfers == []
        assert len(plan.unchanged) == 1

    def test_mtime_within_tolerance_unchanged(self):
        local = [local_state('a.txt', mtime=1001.0)]
        remote = [remote_state('a.txt', mtime=1000.0)]
        plan = build_sync_plan('push', local, remote, allow_modify=True)
        assert plan.transfers == []

    def test_size_only_ignores_mtime(self):
        local = [local_state('a.txt', mtime=9000.0)]
        remote = [remote_state('a.txt', mtime=1000.0)]
        plan = build_sync_plan('push', local, remote, allow_modify=True, size_only=True)
        assert plan.transfers == []
        assert len(plan.unchanged) == 1

    def test_unknown_remote_mtime_treated_as_changed(self):
        local = [local_state('a.txt')]
        remote = [remote_state('a.txt', mtime=None)]
        plan = build_sync_plan('push', local, remote, allow_modify=True)
        assert [t['reason'] for t in plan.transfers] == ['newer']

    def test_remote_only_file_requires_allow_delete(self):
        plan = build_sync_plan('push', [], [remote_state('gone.txt')])
        assert plan.deletes == []
        assert [s['syncPath'] for s in plan.skipped_delete] == ['gone.txt']

        plan = build_sync_plan('push', [], [remote_state('gone.txt')], allow_delete=True)
        assert [d['syncPath'] for d in plan.deletes] == ['gone.txt']

    def test_ignored_patterns_excluded_both_sides(self):
        matcher = VamsIgnoreMatcher(['*.log'])
        local = [local_state('keep.txt'), local_state('debug.log')]
        remote = [remote_state('old.log')]
        plan = build_sync_plan('push', local, remote, ignore_matcher=matcher,
                               allow_delete=True)
        assert [t['syncPath'] for t in plan.transfers] == ['keep.txt']
        assert plan.deletes == []
        assert {i['syncPath'] for i in plan.ignored} == {'debug.log', 'old.log'}

    def test_unsupported_files_excluded(self):
        local = [local_state('model.glb.previewFile.png'), local_state('LICENSE')]
        plan = build_sync_plan('push', local, [])
        assert plan.transfers == []
        assert {u['syncPath'] for u in plan.unsupported} == {'model.glb.previewFile.png', 'LICENSE'}

    def test_invalid_direction_raises(self):
        with pytest.raises(SyncPlanError):
            build_sync_plan('sideways', [], [])


class TestBuildSyncPlanPull:
    def test_missing_local_file_transfers(self):
        plan = build_sync_plan('pull', [], [remote_state('a.txt')])
        assert [t['syncPath'] for t in plan.transfers] == ['a.txt']
        assert plan.transfers[0]['reason'] == 'missing'

    def test_local_only_file_requires_allow_delete(self):
        plan = build_sync_plan('pull', [local_state('extra.txt')], [])
        assert plan.deletes == []
        assert [s['syncPath'] for s in plan.skipped_delete] == ['extra.txt']

        plan = build_sync_plan('pull', [local_state('extra.txt')], [], allow_delete=True)
        assert [d['syncPath'] for d in plan.deletes] == ['extra.txt']
        assert plan.deletes[0]['localPath'] == str(Path('/local/extra.txt'))

    def test_newer_remote_requires_allow_modify(self):
        local = [local_state('a.txt', mtime=1000.0)]
        remote = [remote_state('a.txt', mtime=2000.0)]

        plan = build_sync_plan('pull', local, remote)
        assert plan.transfers == []
        assert [s['reason'] for s in plan.skipped_modify] == ['newer']

        plan = build_sync_plan('pull', local, remote, allow_modify=True)
        assert [t['syncPath'] for t in plan.transfers] == ['a.txt']

    def test_plan_dict_summary(self):
        plan = build_sync_plan('pull', [local_state('extra.txt')],
                               [remote_state('a.txt', size=7)])
        data = plan.to_dict()
        assert data['direction'] == 'pull'
        assert data['summary']['transfer_count'] == 1
        assert data['summary']['transfer_size'] == 7
        assert data['summary']['skipped_delete_count'] == 1


def version(size, last_modified, is_latest=False):
    return {'size': size, 'lastModified': last_modified, 'isLatest': is_latest,
            'versionId': f'v-{last_modified}'}


# Epoch-aligned ISO timestamps for history entries
T1 = '2026-01-01T00:00:00+00:00'  # 1767225600
T2 = '2026-01-02T00:00:00+00:00'  # 1767312000
T3 = '2026-01-03T00:00:00+00:00'  # 1767398400
E1, E2, E3 = 1767225600.0, 1767312000.0, 1767398400.0


class TestApplyConflictChecks:
    def test_pull_outdated_local_copy_is_safe(self):
        # Local file matches an older version exactly -> plain outdated copy, download proceeds
        local = [local_state('a.txt', size=10, mtime=E1)]
        remote = [remote_state('a.txt', size=20, mtime=E2)]
        plan = build_sync_plan('pull', local, remote, allow_modify=True)
        history = {'/a.txt': [version(20, T2, is_latest=True), version(10, T1)]}

        apply_conflict_checks(plan, local, remote, lambda key: history[key])

        assert [t['syncPath'] for t in plan.transfers] == ['a.txt']
        assert plan.conflicts == []

    def test_pull_local_edit_is_conflict(self):
        # Local file matches no known version -> local-only edits would be overwritten
        local = [local_state('a.txt', size=15, mtime=E3)]
        remote = [remote_state('a.txt', size=20, mtime=E2)]
        plan = build_sync_plan('pull', local, remote, allow_modify=True)
        history = {'/a.txt': [version(20, T2, is_latest=True), version(10, T1)]}

        apply_conflict_checks(plan, local, remote, lambda key: history[key])

        assert plan.transfers == []
        assert [c['conflict'] for c in plan.conflicts] == ['local-modified']

    def test_push_outdated_local_copy_is_conflict(self):
        # Local file matches an OLDER version -> pushing would revert newer remote work
        local = [local_state('a.txt', size=10, mtime=E1)]
        remote = [remote_state('a.txt', size=20, mtime=E2)]
        plan = build_sync_plan('push', local, remote, allow_modify=True)
        history = {'/a.txt': [version(20, T2, is_latest=True), version(10, T1)]}

        apply_conflict_checks(plan, local, remote, lambda key: history[key])

        assert plan.transfers == []
        assert [c['conflict'] for c in plan.conflicts] == ['remote-newer']

    def test_push_local_edit_of_current_version_is_safe(self):
        # Local edit made after the remote current version -> normal push
        local = [local_state('a.txt', size=15, mtime=E3)]
        remote = [remote_state('a.txt', size=20, mtime=E2)]
        plan = build_sync_plan('push', local, remote, allow_modify=True)
        history = {'/a.txt': [version(20, T2, is_latest=True), version(10, T1)]}

        apply_conflict_checks(plan, local, remote, lambda key: history[key])

        assert [t['syncPath'] for t in plan.transfers] == ['a.txt']
        assert plan.conflicts == []

    def test_push_local_edit_older_than_remote_is_conflict(self):
        # Local edit matches no version AND remote current is newer -> both modified
        local = [local_state('a.txt', size=15, mtime=E1)]
        remote = [remote_state('a.txt', size=20, mtime=E2)]
        plan = build_sync_plan('push', local, remote, allow_modify=True)
        history = {'/a.txt': [version(20, T2, is_latest=True)]}

        apply_conflict_checks(plan, local, remote, lambda key: history[key])

        assert plan.transfers == []
        assert [c['conflict'] for c in plan.conflicts] == ['both-modified']

    def test_missing_files_never_checked(self):
        local = []
        remote = [remote_state('new.txt')]
        plan = build_sync_plan('pull', local, remote)
        calls = []

        apply_conflict_checks(plan, local, remote, lambda key: calls.append(key) or [])

        assert calls == []
        assert [t['syncPath'] for t in plan.transfers] == ['new.txt']

    def test_history_lookup_failure_fails_open(self):
        local = [local_state('a.txt', size=15, mtime=E3)]
        remote = [remote_state('a.txt', size=20, mtime=E2)]
        plan = build_sync_plan('pull', local, remote, allow_modify=True)

        def failing_lookup(key):
            raise RuntimeError('API error')

        apply_conflict_checks(plan, local, remote, failing_lookup)

        assert [t['syncPath'] for t in plan.transfers] == ['a.txt']
        assert plan.conflicts == []

    def test_infrastructure_errors_propagate(self):
        from vamscli.utils.exceptions import AuthenticationError
        local = [local_state('a.txt', size=15, mtime=E3)]
        remote = [remote_state('a.txt', size=20, mtime=E2)]
        plan = build_sync_plan('pull', local, remote, allow_modify=True)

        def auth_failing_lookup(key):
            raise AuthenticationError('token expired')

        with pytest.raises(AuthenticationError):
            apply_conflict_checks(plan, local, remote, auth_failing_lookup)

    def test_conflicts_in_plan_dict(self):
        local = [local_state('a.txt', size=15, mtime=E3)]
        remote = [remote_state('a.txt', size=20, mtime=E2)]
        plan = build_sync_plan('pull', local, remote, allow_modify=True)
        apply_conflict_checks(plan, local, remote, lambda key: [version(20, T2, is_latest=True)])

        data = plan.to_dict()
        assert data['summary']['conflict_count'] == 1
        assert data['conflicts'][0]['conflict'] == 'local-modified'
        assert data['summary']['transfer_count'] == 0


class TestVamsIgnoreMatcher:
    def test_no_patterns_matches_nothing(self):
        matcher = VamsIgnoreMatcher()
        assert matcher.is_ignored('anything.txt') is False
        assert matcher.has_patterns is False

    def test_wildcard_patterns(self):
        matcher = VamsIgnoreMatcher(['*.log', 'temp/'])
        assert matcher.is_ignored('debug.log') is True
        assert matcher.is_ignored('sub/debug.log') is True
        assert matcher.is_ignored('temp/file.txt') is True
        assert matcher.is_ignored('keep.txt') is False

    def test_negation_pattern(self):
        matcher = VamsIgnoreMatcher(['*.log', '!important.log'])
        assert matcher.is_ignored('debug.log') is True
        assert matcher.is_ignored('important.log') is False

    def test_double_star_pattern(self):
        matcher = VamsIgnoreMatcher(['build/**'])
        assert matcher.is_ignored('build/out/a.bin') is True
        assert matcher.is_ignored('src/a.bin') is False

    def test_comments_and_blanks_ignored(self):
        matcher = VamsIgnoreMatcher(['# comment', '', '*.tmp'])
        assert matcher.is_ignored('x.tmp') is True
        assert matcher.has_patterns is True

    def test_leading_slash_and_backslash_normalized(self):
        matcher = VamsIgnoreMatcher(['*.log'])
        assert matcher.is_ignored('/debug.log') is True
        assert matcher.is_ignored('sub\\debug.log') is True

    def test_from_file(self, tmp_path):
        ignore_file = tmp_path / '.vamsignore'
        ignore_file.write_text('*.log\n# comment\n')
        matcher = VamsIgnoreMatcher.from_file(ignore_file)
        assert matcher.is_ignored('a.log') is True

    def test_from_missing_file_raises(self, tmp_path):
        with pytest.raises(InvalidSyncIgnoreFileError):
            VamsIgnoreMatcher.from_file(tmp_path / 'missing')

    def test_for_directory_uses_default_file(self, tmp_path):
        (tmp_path / '.vamsignore').write_text('*.log\n')
        matcher = VamsIgnoreMatcher.for_directory(tmp_path)
        assert matcher.is_ignored('a.log') is True

    def test_for_directory_without_default_file(self, tmp_path):
        matcher = VamsIgnoreMatcher.for_directory(tmp_path)
        assert matcher.has_patterns is False

    def test_for_directory_with_override(self, tmp_path):
        override = tmp_path / 'custom-ignore.txt'
        override.write_text('*.bin\n')
        (tmp_path / '.vamsignore').write_text('*.log\n')
        matcher = VamsIgnoreMatcher.for_directory(tmp_path, str(override))
        assert matcher.is_ignored('a.bin') is True
        assert matcher.is_ignored('a.log') is False

    def test_for_directory_with_missing_override_raises(self, tmp_path):
        with pytest.raises(InvalidSyncIgnoreFileError):
            VamsIgnoreMatcher.for_directory(tmp_path, str(tmp_path / 'missing'))
