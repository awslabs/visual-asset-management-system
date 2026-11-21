# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.fileAttribution: which asset an S3 object belongs to.

A file belongs to the asset whose stored location contains it, in the same bucket. The record's binding
is used when it checks out, then the location index; before the index is ready, the stamp and the first
folder, each with the bucket and containment checks. The module is the REAL one, registered by
tests/conftest.py.
"""

import sys

import pytest

from tests.common.fileAttributionFakes import (
    AttributionAssetTable,
    AttributionBucketsTable,
    asset_row,
    registration,
)

fa = sys.modules['common.fileAttribution']
al = sys.modules['common.assetLocations']

BUCKET = 'asset-bucket'
OTHER_BUCKET = 'other-bucket'
REGISTRATIONS = [
    registration('reg-root', BUCKET, '/'),
    registration('reg-other', OTHER_BUCKET, 'assets/'),
]


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    fa.clear_registration_cache()
    al.clear_location_cache()
    yield
    fa.clear_registration_cache()
    al.clear_location_cache()


def _ready(monkeypatch, ready):
    monkeypatch.setattr(fa, 'index_ready', lambda table_name: ready)


def _attribute(rows, key, *, ready=False, monkeypatch=None, binding=None, stamp=None, include_archived=False,
               page_size=None, bucket=BUCKET):
    _ready(monkeypatch, ready)
    asset_table = AttributionAssetTable(rows, page_size=page_size)
    buckets_table = AttributionBucketsTable(REGISTRATIONS, page_size=page_size)
    stamp_reads = []

    def read_stamp():
        stamp_reads.append(key)
        return stamp

    result = fa.attribute_object(bucket, key, asset_table=asset_table, buckets_table=buckets_table,
                                 binding=binding, include_archived=include_archived, read_stamp=read_stamp)
    return result, asset_table, stamp_reads


def _binding(database_id='db1', asset_id='a1', bucket_id='reg-root', relative_path='/x', resolved_by='fastPath'):
    return {'databaseId': database_id, 'assetId': asset_id, 'bucketId': bucket_id,
            'relativePath': relative_path, 'resolvedBy': resolved_by}


CUSTOM = asset_row('db1', 'custom', 'reg-root', 'projects/building-a/', BUCKET)
NORMAL = asset_row('db1', 'a1', 'reg-root', 'a1/', BUCKET, indexed=False)
SINGLE = asset_row('db2', 'one', 'reg-root', 'projects/model.glb', BUCKET)


@pytest.mark.unit
class TestBinding:
    def test_a_binding_that_checks_out_attributes_without_the_index_or_the_stamp(self, monkeypatch):
        result, table, stamp_reads = _attribute([NORMAL], 'a1/sub/model.glb', monkeypatch=monkeypatch,
                                                binding=_binding())
        assert result.attributed
        assert (result.database_id, result.asset_id, result.relative_path) == ('db1', 'a1', '/sub/model.glb')
        assert result.resolved_by == fa.RESOLVED_BY_BINDING
        # The binding was confirmed with a read of the asset row, and the index was never queried.
        assert ('get_item', {'databaseId': 'db1', 'assetId': 'a1'}, {'ConsistentRead': True}) in table.reads
        assert table.reads and not [r for r in table.reads if r[0] == 'index']
        assert stamp_reads == []

    def test_a_binding_to_a_registration_of_another_bucket_is_not_used(self, monkeypatch):
        foreign = asset_row('db9', 'a1', 'reg-other', 'assets/a1/', OTHER_BUCKET)
        result, _, _ = _attribute([foreign], 'a1/sub/model.glb', ready=True, monkeypatch=monkeypatch,
                                  binding=_binding(database_id='db9', bucket_id='reg-other'))
        assert not result.attributed
        assert result.asset_id != 'a1' or result.database_id != 'db9'

    def test_a_binding_whose_asset_does_not_hold_the_key_is_not_used(self, monkeypatch):
        result, _, _ = _attribute([NORMAL, CUSTOM], 'projects/building-a/x.obj', ready=True,
                                  monkeypatch=monkeypatch, binding=_binding())
        # Not filed under the bound asset a1; the location index answers instead.
        assert (result.database_id, result.asset_id) == ('db1', 'custom')
        assert result.resolved_by == fa.RESOLVED_BY_LOCATION_INDEX

    def test_a_binding_to_a_gone_asset_is_not_used(self, monkeypatch):
        result, _, _ = _attribute([], 'a1/sub/model.glb', ready=True, monkeypatch=monkeypatch, binding=_binding())
        assert not result.attributed

    def test_a_binding_to_an_asset_archived_since_attributes_it_archived_when_asked(self, monkeypatch):
        archived = asset_row('db1', 'a1', 'reg-root', 'a1/', BUCKET, archived=True)
        result, _, _ = _attribute([archived], 'a1/m.glb', monkeypatch=monkeypatch, binding=_binding(),
                                  include_archived=True)
        assert result.attributed and result.archived
        assert result.database_id == 'db1'

    @pytest.mark.parametrize('binding', [None, {}, {'databaseId': 'db1'}, 'db1', {'databaseId': 'db1',
                                                                               'assetId': '', 'bucketId': 'r'}])
    def test_binding_of_accepts_only_a_complete_binding(self, binding):
        assert fa.binding_of({'vamsBinding': binding}) is None

    def test_binding_of_returns_a_complete_binding(self):
        assert fa.binding_of({'vamsBinding': _binding()}) == _binding()


@pytest.mark.unit
class TestLocationIndex:
    def test_a_custom_root_file_is_attributed_by_the_index(self, monkeypatch):
        result, _, stamp_reads = _attribute([CUSTOM], 'projects/building-a/floor/plan.e57', monkeypatch=monkeypatch)
        assert (result.database_id, result.asset_id, result.relative_path) == ('db1', 'custom', '/floor/plan.e57')
        assert result.resolved_by == fa.RESOLVED_BY_LOCATION_INDEX
        assert stamp_reads == []

    def test_a_miss_once_the_index_is_ready_is_final_and_reads_no_stamp(self, monkeypatch):
        stamp = {'databaseid': 'db1', 'assetid': 'a1'}
        result, _, stamp_reads = _attribute([NORMAL], 'a1/m.glb', ready=True, monkeypatch=monkeypatch, stamp=stamp)
        assert not result.attributed
        assert stamp_reads == []

    def test_two_live_assets_on_one_location_attribute_nothing(self, monkeypatch):
        twin = asset_row('db2', 'custom2', 'reg-root', 'projects/building-a/', BUCKET)
        result, _, _ = _attribute([CUSTOM, twin], 'projects/building-a/x.obj', monkeypatch=monkeypatch)
        assert result.status == fa.AMBIGUOUS

    def test_an_archived_custom_root_asset_is_found_only_when_asked(self, monkeypatch):
        archived = asset_row('db1', 'custom', 'reg-root', 'projects/building-a/', BUCKET, archived=True)
        live_only, _, _ = _attribute([archived], 'projects/building-a/x.obj', ready=True, monkeypatch=monkeypatch)
        assert not live_only.attributed
        with_archived, _, _ = _attribute([archived], 'projects/building-a/x.obj', ready=True,
                                         monkeypatch=monkeypatch, include_archived=True)
        assert with_archived.attributed and with_archived.archived
        assert with_archived.database_id == 'db1'


@pytest.mark.unit
class TestAKeyTheIndexCannotAnswerFor:
    """A key with more candidate locations than the index resolves, or one whose lookup does not finish
    within its page limit, is attributed as before the index (stamp, then first folder), also once the
    index is ready."""

    DEEP_KEY = 'a1/' + 'd/' * 40 + 'scan.e57'

    def test_a_key_with_too_many_candidate_locations_is_attributed_by_its_first_folder(self, monkeypatch):
        assert len(al.candidate_locations(self.DEEP_KEY)) > al.MAX_CANDIDATE_LOCATIONS
        result, _, stamp_reads = _attribute([NORMAL], self.DEEP_KEY, ready=True, monkeypatch=monkeypatch)
        assert (result.database_id, result.asset_id) == ('db1', 'a1')
        assert result.resolved_by == fa.RESOLVED_BY_FIRST_FOLDER
        assert stamp_reads == [self.DEEP_KEY]

    def test_a_lookup_past_its_page_limit_is_attributed_by_its_first_folder(self, monkeypatch):
        def unfinished(*args, **kwargs):
            raise RuntimeError('Location index query did not finish within 5 pages')
        monkeypatch.setattr(fa, 'resolve_owner', unfinished)
        result, _, _ = _attribute([NORMAL], 'a1/m.glb', ready=True, monkeypatch=monkeypatch)
        assert (result.database_id, result.asset_id, result.relative_path) == ('db1', 'a1', '/m.glb')
        assert result.resolved_by == fa.RESOLVED_BY_FIRST_FOLDER


@pytest.mark.unit
class TestSingleFileLocation:
    @pytest.mark.parametrize('key,relative', [
        ('projects/model.glb', '/model.glb'),
        ('projects/model.glb/textures/a.png', '/textures/a.png'),
        ('projects/model.glb.previewFile.png', '/model.glb.previewFile.png'),
    ])
    def test_the_object_its_companion_folder_and_its_previews_belong_to_it(self, monkeypatch, key, relative):
        result, _, _ = _attribute([SINGLE], key, monkeypatch=monkeypatch)
        assert (result.asset_id, result.relative_path) == ('one', relative)

    @pytest.mark.parametrize('key', ['projects/model.glb.bak', 'projects/model.glbx/a.png'])
    def test_a_neighbour_that_only_shares_characters_is_not_attributed(self, monkeypatch, key):
        result, _, _ = _attribute([SINGLE], key, ready=True, monkeypatch=monkeypatch)
        assert not result.attributed


@pytest.mark.unit
class TestBeforeTheIndexIsReady:
    def test_the_stamp_attributes_a_file_its_asset_holds(self, monkeypatch):
        result, _, stamp_reads = _attribute([NORMAL], 'a1/m.glb', monkeypatch=monkeypatch,
                                            stamp={'databaseid': 'db1', 'assetid': 'a1'})
        assert (result.database_id, result.asset_id, result.resolved_by) == ('db1', 'a1', fa.RESOLVED_BY_STAMP)
        assert stamp_reads == ['a1/m.glb']

    def test_a_file_copied_into_another_assets_folder_belongs_to_that_asset(self, monkeypatch):
        # The copy keeps the source's stamp (a1); the key is in b1's folder.
        other = asset_row('db2', 'b1', 'reg-root', 'b1/', BUCKET, indexed=False)
        result, _, _ = _attribute([NORMAL, other], 'b1/m.glb', monkeypatch=monkeypatch,
                                  stamp={'databaseid': 'db1', 'assetid': 'a1'})
        assert (result.database_id, result.asset_id) == ('db2', 'b1')
        assert result.resolved_by == fa.RESOLVED_BY_FIRST_FOLDER

    def test_a_stamp_naming_an_asset_of_another_bucket_is_not_used(self, monkeypatch):
        foreign = asset_row('db9', 'z9', 'reg-other', 'assets/z9/', OTHER_BUCKET, indexed=False)
        result, _, _ = _attribute([foreign], 'loose/m.glb', monkeypatch=monkeypatch,
                                  stamp={'databaseid': 'db9', 'assetid': 'z9'})
        assert not result.attributed

    def test_the_first_folder_attributes_a_normal_asset_without_a_stamp(self, monkeypatch):
        result, _, _ = _attribute([NORMAL], 'a1/sub/m.glb', monkeypatch=monkeypatch)
        assert (result.database_id, result.asset_id, result.relative_path) == ('db1', 'a1', '/sub/m.glb')

    def test_the_first_folder_reads_every_page_of_the_asset_id_lookup(self, monkeypatch):
        rows = [asset_row(f'db{i}', 'a1', 'reg-other', 'assets/a1/', OTHER_BUCKET, indexed=False) for i in range(3)]
        rows.append(asset_row('dbz', 'a1', 'reg-root', 'a1/', BUCKET, indexed=False))
        result, _, _ = _attribute(rows, 'a1/sub/m.glb', monkeypatch=monkeypatch, page_size=1)
        assert (result.database_id, result.asset_id) == ('dbz', 'a1')

    def test_one_asset_id_in_two_databases_of_one_registration_attributes_nothing(self, monkeypatch):
        twin = asset_row('db2', 'a1', 'reg-root', 'a1/', BUCKET, indexed=False)
        result, _, _ = _attribute([NORMAL, twin], 'a1/m.glb', monkeypatch=monkeypatch)
        assert result.status == fa.AMBIGUOUS

    def test_a_live_and_an_archived_asset_with_one_id_are_ambiguous_only_with_archived_rows(self, monkeypatch):
        archived_twin = asset_row('db2', 'a1', 'reg-root', 'a1/', BUCKET, archived=True, indexed=False)
        live_only, _, _ = _attribute([NORMAL, archived_twin], 'a1/m.glb', monkeypatch=monkeypatch)
        assert (live_only.database_id, live_only.asset_id) == ('db1', 'a1')
        with_archived, _, _ = _attribute([NORMAL, archived_twin], 'a1/m.glb', monkeypatch=monkeypatch,
                                         include_archived=True)
        assert with_archived.status == fa.AMBIGUOUS

    def test_a_registration_prefix_comes_off_before_the_first_folder(self, monkeypatch):
        prefixed = asset_row('db9', 'z9', 'reg-other', 'assets/z9/', OTHER_BUCKET, indexed=False)
        result, _, _ = _attribute([prefixed], 'assets/z9/m.glb', monkeypatch=monkeypatch, bucket=OTHER_BUCKET)
        assert (result.asset_id, result.relative_path) == ('z9', '/m.glb')

    def test_a_custom_root_file_without_index_rows_is_not_attributed_to_its_first_folder(self, monkeypatch):
        unindexed = asset_row('db1', 'custom', 'reg-root', 'projects/building-a/', BUCKET, indexed=False)
        lookalike = asset_row('db1', 'projects', 'reg-root', 'elsewhere/', BUCKET, indexed=False)
        result, _, _ = _attribute([unindexed, lookalike], 'projects/building-a/x.obj', monkeypatch=monkeypatch)
        assert not result.attributed
