# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""`maxFiles=N` must return exactly N files.

The page's file budget was spent on LISTED ROWS (`max_objects=file_budget`, and the
`listed_file_count > file_budget` stop), but **two kinds of row are listed and then never returned to
the caller as a file**, so each one silently consumed a unit of budget and gave nothing back:

1. **Folder markers.** Removed afterwards by `apply_file_filters` "Filter 1: Exclude folders if not
   requested". Every asset prefix carries at least its own root marker.
2. **`.previewFile.` companions.** Folded into their base file's entry as a `previewFile` attribute
   rather than returned as files of their own.

Measured on a 4,100-file asset: `maxFiles=10` returned 9, `100` returned 99, `500` returned 499 — a
shortfall of exactly 1 at every size. The two causes had to be separated by measurement, and the
first fix alone did not close it: after the folder-marker fix deployed, the SAME asset still returned
9 for `maxFiles=10`, because it carries exactly one companion. `maxFiles=1` changing from 0 to 1 is
what showed the first fix had landed at all.

The budget is therefore now counted in files the caller RECEIVES (`spends_file_budget`) rather than in
rows listed, and the row/file translation (`row_count_for_budget`) pulls each base file's trailing
companions in with it. Folder markers are dropped from the listing outright; companions are NOT —
`preview_lookup` is built from them, so excluding them would strip every `previewFile` from the export.
That asymmetry is the reason for two mechanisms instead of one, and
`test_the_companion_is_still_attached_to_its_base_file` is what holds it.

No data was lost at any point — the continuation carried the remainder — so this is an off-by-one in
the contract rather than a truncation. That is also why the whole-set arms matter as much as the count
arms: a "fix" that returned N by dropping a file would satisfy every count assertion here.

:::note[Two filters still spend budget, deliberately]
`fileExtensions` reads only the key so it COULD be applied during the listing, but that makes the
walk unbounded — an asset whose keys all fail the filter would be paged end to end to fill one page.
`includeOnlyPrimaryTypeFiles` cannot be known from a listing at all. Both are pinned below as
current behaviour so the limitation is recorded rather than rediscovered.
:::
"""

import pytest

from tests.handlers.assets.test_assetExportService_authz_fail_closed import (  # noqa: E402
    _load_asset_export_service,
    _DB,
    _ASSET,
)
from tests.handlers.assets.test_assetExportService_file_paging import (  # noqa: E402
    _FakeS3,
    _CountingQueryClient,
    _asset_item,
    _run_batch,
    _run_export,
    _PREFIX,
)


def _tree(file_count, folder_count=1, prefix=_PREFIX):
    """Keys for one asset: `folder_count` folder markers plus `file_count` files.

    The root marker (`prefix` itself) is always first, which is what S3 returns for a real asset —
    `file upload` creates it, and a 13-file control upload was measured storing 14 objects. Extra
    markers are nested folders, so an asset with a directory tree is covered too.
    """
    keys = [prefix]
    for index in range(1, folder_count):
        keys.append(f"{prefix}sub{index:02d}/")
    keys.extend(f"{prefix}file{index:04d}.glb" for index in range(file_count))
    return keys


def _exported_files(entries):
    """Every exported file across the batch, in order."""
    return [file for entry in entries for file in entry.get('files', [])]


@pytest.mark.unit
class TestMaxFilesIsExact:
    """The count arm. Each of these returned N-1 before the fix."""

    @pytest.mark.parametrize("budget", [1, 2, 10, 100])
    def test_a_root_folder_marker_does_not_consume_budget(self, budget):
        m = _load_asset_export_service()
        # Twice the budget of files available, so a shortfall cannot be "that is all there was".
        s3 = _FakeS3({_PREFIX: _tree(budget * 2 + 5)})
        entries, _state = _run_batch(
            m, s3, _CountingQueryClient(), [_asset_item()], maxFiles=budget)

        files = _exported_files(entries)
        assert len(files) == budget, (
            f"maxFiles={budget} returned {len(files)} files; the asset's own root marker is being "
            f"counted against the budget and then filtered out: {[f['relativePath'] for f in files]}")
        assert all(not f['isFolder'] for f in files), (
            f"a folder was exported without includeFolderFiles: {files}")

    @pytest.mark.parametrize("folder_count", [1, 3, 12])
    def test_a_whole_directory_tree_does_not_consume_budget(self, folder_count):
        # A nested tree makes the shortfall scale with the folder count, not stay at one, so a fix
        # that merely subtracted a constant would fail here.
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _tree(40, folder_count=folder_count)})
        entries, _state = _run_batch(
            m, s3, _CountingQueryClient(), [_asset_item()], maxFiles=10)

        assert len(_exported_files(entries)) == 10, _exported_files(entries)

    def test_an_asset_with_no_folder_marker_is_unaffected(self):
        # The control that keeps the arms above honest. This case returned exactly N BEFORE the fix
        # too, so if it now returned N+1 the change would be over-counting rather than correcting.
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: [f"{_PREFIX}file{i:04d}.glb" for i in range(30)]})
        entries, _state = _run_batch(
            m, s3, _CountingQueryClient(), [_asset_item()], maxFiles=10)

        assert len(_exported_files(entries)) == 10, _exported_files(entries)

    def test_each_asset_in_a_batch_costs_only_its_files(self):
        # The shortfall was per ASSET, so a multi-asset page lost one file per asset. The budget is
        # shared across the batch, so this asserts the total rather than a per-asset count.
        m = _load_asset_export_service()
        assets = [_asset_item(asset_id=f"x-asset-{i}") for i in range(3)]
        keys = {}
        for asset in assets:
            prefix = asset['assetLocation']['Key']
            keys[prefix] = _tree(4, prefix=prefix)
        entries, _state = _run_batch(
            m, s3_for(keys), _CountingQueryClient(), assets, maxFiles=12)

        assert len(_exported_files(entries)) == 12, [
            f['relativePath'] for f in _exported_files(entries)]


def s3_for(keys_by_prefix):
    return _FakeS3(keys_by_prefix)


@pytest.mark.unit
class TestFoldersStillExportWhenRequested:
    """The opposite arm: the fix must not make folders unreachable.

    `exclude_folders` is wired to `not includeFolderFiles`, so a request that asks for folders must
    still receive them — and they must still cost budget, because they are exported.
    """

    def test_include_folder_files_returns_folders(self):
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _tree(40, folder_count=4)})
        entries, _state = _run_batch(
            m, s3, _CountingQueryClient(), [_asset_item()],
            maxFiles=10, includeFolderFiles=True)

        files = _exported_files(entries)
        assert len(files) == 10, files
        assert any(f['isFolder'] for f in files), (
            f"includeFolderFiles=True returned no folder rows: "
            f"{[f['relativePath'] for f in files]}")

    def test_folders_count_toward_the_budget_when_requested(self):
        # 4 markers + 6 files == 10, so a budget of 10 must be filled exactly and stop. If folders
        # were excluded from the count even here, more than 10 rows would come back.
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _tree(6, folder_count=4)})
        entries, _state = _run_batch(
            m, s3, _CountingQueryClient(), [_asset_item()],
            maxFiles=10, includeFolderFiles=True)

        files = _exported_files(entries)
        assert len(files) == 10, files
        assert sum(1 for f in files if f['isFolder']) == 4, files


@pytest.mark.unit
class TestListS3FilesExcludeFolders:
    """`list_s3_files` directly, because the caller-level arms cannot show WHERE the row was dropped.

    A fix applied after the listing would satisfy every count assertion above while the marker still
    consumed a unit of the listing's own `max_objects` ceiling — the ceiling being what starves the
    budget in the first place.
    """

    def test_folder_rows_are_neither_returned_nor_counted(self):
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _tree(20, folder_count=5)})

        from unittest.mock import patch
        with patch.object(m, "s3_client", s3):
            files = m.list_s3_files("bucket-name", _PREFIX, max_objects=10,
                                    exclude_folders=True)

        assert all(not f['isFolder'] for f in files), files
        # max_objects returns one row beyond the ceiling so the caller can tell a full page from the
        # last one, hence 11 rather than 10 — and all 11 must be FILES.
        assert len(files) == 11, [f['relativePath'] for f in files]

    def test_folder_rows_are_returned_and_counted_by_default(self):
        # The default must not change: `exclude_folders` defaults False, and the one caller opts in.
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _tree(20, folder_count=5)})

        from unittest.mock import patch
        with patch.object(m, "s3_client", s3):
            files = m.list_s3_files("bucket-name", _PREFIX, max_objects=10)

        assert len(files) == 11, [f['relativePath'] for f in files]
        assert any(f['isFolder'] for f in files), (
            f"the default listing dropped folder rows: {[f['relativePath'] for f in files]}")


@pytest.mark.unit
class TestNoFileIsLostOrDuplicated:
    """The arm that makes the count arms safe. Returning N by dropping a real file would satisfy
    every assertion above; only walking the continuation to exhaustion catches it."""

    def test_paging_a_folder_bearing_asset_yields_every_file_exactly_once(self):
        m = _load_asset_export_service()
        file_count = 25
        s3 = _FakeS3({_PREFIX: _tree(file_count, folder_count=3)})
        query_client = _CountingQueryClient()
        assets = [_asset_item()]

        seen = []
        token = None
        for _ in range(file_count + 5):        # generous bound; a non-terminating loop is a failure
            page = _run_export(m, s3, query_client, assets,
                               starting_token=token, maxFiles=7)
            seen.extend(file['relativePath']
                        for asset in page['assets'] for file in asset['files'])
            # 'NextToken', capitalised — a lowercase read returns None, the loop exits after page
            # one, and the walk silently covers a single page. Caught here only because the
            # assertion compares the whole set rather than counting the first page.
            token = page.get('NextToken')
            if not token:
                break
        else:
            pytest.fail(f"paging did not terminate; collected {len(seen)} paths")

        expected = [f"/file{index:04d}.glb" for index in range(file_count)]
        assert sorted(seen) == expected, (
            f"expected {len(expected)} distinct files, collected {len(seen)}: "
            f"missing={sorted(set(expected) - set(seen))} "
            f"duplicated={sorted({p for p in seen if seen.count(p) > 1})}")


@pytest.mark.unit
class TestKnownRemainingLooseness:
    """Pins the two filters that still spend budget, so the limitation is recorded behaviour.

    These are NOT temporary tests: both constructs remain writable, and a future change that made
    either filter exact should update these deliberately rather than discover them failing.
    """

    def test_an_extension_filter_may_return_fewer_than_maxfiles(self):
        m = _load_asset_export_service()
        # Alternating extensions: half the listed rows fail the filter and are dropped after the
        # budget has already been spent on them.
        keys = [_PREFIX] + [
            f"{_PREFIX}file{index:04d}{'.glb' if index % 2 == 0 else '.txt'}"
            for index in range(40)
        ]
        entries, _state = _run_batch(
            m, _FakeS3({_PREFIX: keys}), _CountingQueryClient(), [_asset_item()],
            maxFiles=10, fileExtensions=['.glb'])

        files = _exported_files(entries)
        assert all(f['relativePath'].endswith('.glb') for f in files), files
        # Documented shortfall, not a target. Asserted as "at most" with a nonzero floor so the arm
        # fails if extension filtering ever stops working altogether.
        assert 0 < len(files) <= 10, files


def _tree_with_companions(base_count, companion_every=1, prefix=_PREFIX):
    """Keys for one asset whose base files carry `.previewFile.` companions.

    S3 lists a companion immediately after its base file, because the companion's key is the base
    key plus the suffix. That adjacency is what the export relies on to attach the companion.
    """
    keys = [prefix]
    for index in range(base_count):
        base = f"{prefix}part{index:05d}.glb"
        keys.append(base)
        if companion_every and index % companion_every == 0:
            keys.append(f"{base}.previewFile.png")
    return keys


@pytest.mark.unit
class TestACompanionDoesNotConsumeBudget:
    """A `.previewFile.` companion is LISTED but folded into its base file's entry, so it must not
    cost the caller one of their `maxFiles`.

    This is the second of the two rows that spent budget and returned no file. It was found live on
    prod5 AFTER the folder-marker fix had already deployed: `maxFiles=3` returned 2 and `maxFiles=10`
    returned 9 on a 4,100-file asset carrying exactly one companion, and the companion appeared only
    as `previewFile: /group000/sub0/part00000.glb.previewFile.png` on its base file's entry — never as
    a file of its own.
    """

    @pytest.mark.parametrize("budget", [1, 2, 3, 10])
    def test_maxfiles_counts_only_returned_files(self, budget):
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _tree_with_companions(budget * 2 + 5)})
        entries, _state = _run_batch(
            m, s3, _CountingQueryClient(), [_asset_item()], maxFiles=budget)

        files = _exported_files(entries)
        assert len(files) == budget, (
            f"maxFiles={budget} returned {len(files)}; a companion is consuming budget and being "
            f"folded into its base entry: {[f['relativePath'] for f in files]}")
        # And no companion is returned as a file in its own right, which is the premise above.
        assert all('.previewFile.' not in f['relativePath'] for f in files), files

    def test_the_companion_is_still_attached_to_its_base_file(self):
        # The load-bearing control. The companion must NOT be dropped from the listing to solve this —
        # `preview_lookup` is built from the listed companions, so excluding them the way folder
        # markers are excluded would silently strip every previewFile from the export.
        m = _load_asset_export_service()
        s3 = _FakeS3({_PREFIX: _tree_with_companions(6)})
        entries, _state = _run_batch(
            m, s3, _CountingQueryClient(), [_asset_item()], maxFiles=3)

        files = _exported_files(entries)
        attached = [f for f in files if f.get('previewFile')]
        assert attached, (
            f"no exported file carries a previewFile, so the companions were dropped rather than "
            f"folded: {[(f['relativePath'], f.get('previewFile')) for f in files]}")
        for f in attached:
            assert f['previewFile'].startswith(f['relativePath']), (
                f"{f['relativePath']} was attached the wrong companion: {f['previewFile']}")

    def test_no_file_is_lost_across_pages_of_a_companion_bearing_asset(self):
        # Paging is where a row-versus-file budget mismatch does real damage: an off-by-one in the
        # resume point drops or repeats a file rather than just shortening a page.
        m = _load_asset_export_service()
        base_count = 17
        s3 = _FakeS3({_PREFIX: _tree_with_companions(base_count)})
        query_client = _CountingQueryClient()
        assets = [_asset_item()]

        seen, token = [], None
        for _ in range(base_count + 5):
            page = _run_export(m, s3, query_client, assets,
                               starting_token=token, maxFiles=4)
            seen.extend(file['relativePath']
                        for asset in page['assets'] for file in asset['files'])
            token = page.get('NextToken')
            if not token:
                break
        else:
            pytest.fail(f"paging did not terminate; collected {len(seen)}")

        expected = [f"/part{index:05d}.glb" for index in range(base_count)]
        assert sorted(seen) == expected, (
            f"missing={sorted(set(expected) - set(seen))} "
            f"duplicated={sorted({p for p in seen if seen.count(p) > 1})}")


@pytest.mark.unit
class TestSpendsFileBudgetPredicate:
    """The predicate directly. It decides what `maxFiles` means, and it is wrong in BOTH directions
    if `include_folders` is not threaded from the request."""

    def test_a_plain_file_spends(self):
        m = _load_asset_export_service()
        assert m.spends_file_budget({'isFolder': False, 'key': 'db/a/model.glb'}) is True

    def test_a_companion_never_spends(self):
        m = _load_asset_export_service()
        companion = {'isFolder': False, 'key': 'db/a/model.glb.previewFile.png'}
        assert m.spends_file_budget(companion) is False
        # Not even when folders are requested — the flag governs folders only.
        assert m.spends_file_budget(companion, include_folders=True) is False

    def test_a_folder_spends_only_when_requested(self):
        # Both directions. Returning False unconditionally let an includeFolderFiles=True page return
        # MORE rows than maxFiles; returning True unconditionally is the original defect.
        m = _load_asset_export_service()
        folder = {'isFolder': True, 'key': 'db/a/sub/'}
        assert m.spends_file_budget(folder, include_folders=False) is False
        assert m.spends_file_budget(folder, include_folders=True) is True


@pytest.mark.unit
class TestRowCountForBudget:
    """The row/file translation. A base file's trailing companions must travel with it."""

    def test_trailing_companions_come_with_their_base(self):
        m = _load_asset_export_service()
        files = [
            {'isFolder': False, 'key': 'a.glb'},
            {'isFolder': False, 'key': 'a.glb.previewFile.png'},
            {'isFolder': False, 'key': 'b.glb'},
            {'isFolder': False, 'key': 'b.glb.previewFile.png'},
        ]
        # One file of budget must take 2 rows: the base AND its companion. Taking 1 row would leave
        # the companion for a page that holds no base file to attach it to, dropping it entirely.
        assert m.row_count_for_budget(files, 1) == 2
        assert m.row_count_for_budget(files, 2) == 4

    def test_a_zero_budget_takes_no_rows(self):
        m = _load_asset_export_service()
        assert m.row_count_for_budget([{'isFolder': False, 'key': 'a.glb'}], 0) == 0

    def test_a_budget_beyond_the_list_takes_everything(self):
        m = _load_asset_export_service()
        files = [{'isFolder': False, 'key': 'a.glb'}, {'isFolder': False, 'key': 'b.glb'}]
        assert m.row_count_for_budget(files, 99) == 2
