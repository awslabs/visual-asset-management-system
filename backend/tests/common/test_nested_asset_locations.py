# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A file belongs to the deepest stored location that contains it. The rule for one asset location and the
locations of other assets nested in it (pure), and the lookup of those locations: the location index once it
is ready, the walk of the bucket's registrations before it for a destructive operation, none otherwise."""

import sys

import pytest

from tests.common.fileAttributionFakes import AttributionAssetTable, AttributionBucketsTable, asset_row, registration

al = sys.modules['common.assetLocations']
fa = sys.modules['common.fileAttribution']

BUCKET = 'asset-bucket'


@pytest.fixture(autouse=True)
def _fresh():
    al.clear_nested_locations_cache()
    fa.clear_registration_cache()
    yield
    al.clear_nested_locations_cache()
    fa.clear_registration_cache()


@pytest.mark.unit
class TestTheRule:
    @pytest.mark.parametrize('location,nested,key,holder', [
        # A folder asset with a folder asset inside it
        ('outer/', {'outer/inner/'}, 'outer/inner/a.glb', 'outer/inner/'),
        ('outer/', {'outer/inner/'}, 'outer/inner/', 'outer/inner/'),
        ('outer/', {'outer/inner/'}, 'outer/innerfile.glb', None),
        ('outer/', {'outer/inner/'}, 'outer/a.glb', None),
        # A folder asset with a single-file asset inside it: its object, companion folder and previews
        ('outer/', {'outer/model.glb'}, 'outer/model.glb', 'outer/model.glb'),
        ('outer/', {'outer/model.glb'}, 'outer/model.glb/notes.txt', 'outer/model.glb'),
        ('outer/', {'outer/model.glb'}, 'outer/model.glb.previewFile.png', 'outer/model.glb'),
        ('outer/', {'outer/model.glb'}, 'outer/model.glb.bak', None),
        # A single-file asset with an asset in its companion folder
        ('outer/model.glb', {'outer/model.glb/sub/'}, 'outer/model.glb/sub/x.obj', 'outer/model.glb/sub/'),
        ('outer/model.glb', {'outer/model.glb/sub/'}, 'outer/model.glb/other.obj', None),
        ('outer/model.glb', {'outer/model.glb/sub/'}, 'outer/model.glb', None),
        # The deepest of several nested locations holds the key
        ('outer/', {'outer/inner/', 'outer/inner/deeper/'}, 'outer/inner/deeper/x', 'outer/inner/deeper/'),
        # A shallower location is never nested: the asset keeps the keys it shares with it
        ('outer/inner/', {'outer/'}, 'outer/inner/a.glb', None),
    ])
    def test_the_deepest_location_holds_the_key(self, location, nested, key, holder):
        assert al.deeper_holder(location, frozenset(nested), key) == holder
        assert al.held_by(location, frozenset(nested), key) is (al.contains(location, key) and holder is None)

    def test_a_preview_partner_is_nested_in_a_single_file(self):
        # x.glb.previewFile and x.glb both hold x.glb.previewFile.previewFile.png; the longer one owns it
        assert al.is_nested_in('a/x.glb', 'a/x.glb.previewFile')
        assert not al.is_nested_in('a/x.glb.previewFile', 'a/x.glb')
        assert al.deeper_holder('a/x.glb', {'a/x.glb.previewFile'}, 'a/x.glb.previewFile.previewFile.png') \
            == 'a/x.glb.previewFile'

    @pytest.mark.parametrize('other', ['outer/', 'outer', 'other/', 'outerx/', 'out/'])
    def test_what_is_not_nested(self, other):
        assert not al.is_nested_in('outer/', other)

    def test_nested_locations_keep_only_nested_entries_and_answer_keep(self):
        entries = [al.LocationEntry('db2', 'inner', 'b1', 'outer/inner/'),
                   al.LocationEntry('db1', 'outer', 'b1', 'outer/'),
                   al.LocationEntry('db1', 'other', 'b1', 'other/')]
        nested = al.NestedLocations('outer/', entries)
        assert nested.locations == frozenset({'outer/inner/'})
        assert [nested.keep(k) for k in ('outer/a', 'outer/inner/a')] == [True, False]
        assert [nested.holds(k) for k in ('outer/a', 'outer/inner/a', 'other/a')] == [True, False, False]

    def test_the_auxiliary_data_of_a_nested_asset_of_the_same_database_is_kept(self):
        nested = al.NestedLocations('outer/', [al.LocationEntry('db1', 'inner', 'b1', 'outer/inner/'),
                                               al.LocationEntry('db2', 'other', 'b1', 'outer/other/')])
        assert nested.keep_aux('db1', 'db1/outer/a.glb/preview/x.png') is True
        assert nested.keep_aux('db1', 'db1/outer/inner/a.glb/preview/x.png') is False
        # The rule is ownership, not databases: the viewer data under a nested location is never the outer's
        assert nested.keep_aux('db1', 'db1/outer/other/a.glb/preview/x.png') is False


OUTER = asset_row('db1', 'outer', 'reg-1', 'outer/', BUCKET)
INNER = asset_row('db2', 'inner', 'reg-1', 'outer/inner/', BUCKET)
INNER_FILE = asset_row('db1', 'innerfile', 'reg-1', 'outer/model.glb', BUCKET)
ARCHIVED_INNER = asset_row('db3', 'gone', 'reg-2', 'outer/gone/', BUCKET, archived=True)
NEIGHBOUR = asset_row('db1', 'neighbour', 'reg-1', 'outerx/', BUCKET)
REGISTRATIONS = [registration('reg-1', BUCKET, '/'), registration('reg-2', BUCKET, 'outer/')]


def _nested(monkeypatch, rows, ready, destructive=False, page_size=None, location='outer/'):
    monkeypatch.setattr(al, 'index_ready', lambda name: ready)
    table = AttributionAssetTable(rows, page_size=page_size)
    buckets = AttributionBucketsTable(REGISTRATIONS)
    nested = fa.nested_locations_of_asset(table, buckets, OUTER, BUCKET, location, destructive=destructive)
    return nested, table


@pytest.mark.unit
class TestTheLookup:
    ROWS = [OUTER, INNER, INNER_FILE, ARCHIVED_INNER, NEIGHBOUR]

    def test_once_the_index_is_ready_it_reads_the_index_live_and_archived(self, monkeypatch):
        nested, table = _nested(monkeypatch, self.ROWS, ready=True, page_size=1)
        assert nested.locations == frozenset({'outer/inner/', 'outer/model.glb', 'outer/gone/'})
        assert nested.source == 'index'
        assert {r[1] for r in table.reads} == {al.LOCATION_INDEX_NAME}

    def test_a_single_file_location_reads_its_companion_folder_and_preview_stem(self, monkeypatch):
        rows = [asset_row('db1', 'one', 'reg-1', 'a/x.glb', BUCKET),
                asset_row('db1', 'sub', 'reg-1', 'a/x.glb/sub/', BUCKET),
                asset_row('db1', 'partner', 'reg-1', 'a/x.glb.previewFile', BUCKET),
                asset_row('db1', 'bak', 'reg-1', 'a/x.glb.bak', BUCKET)]
        nested, table = _nested(monkeypatch, rows, ready=True, location='a/x.glb')
        assert nested.locations == frozenset({'a/x.glb/sub/', 'a/x.glb.previewFile'})
        assert [r[2]['ExpressionAttributeValues'][':p'] for r in table.reads] == ['a/x.glb']

    def test_before_the_index_a_read_finds_none_and_reads_nothing(self, monkeypatch):
        nested, table = _nested(monkeypatch, self.ROWS, ready=False)
        assert not nested and table.reads == []

    def test_before_the_index_a_destructive_operation_walks_every_registration_of_the_bucket(self, monkeypatch):
        nested, table = _nested(monkeypatch, self.ROWS, ready=False, destructive=True)
        assert nested.locations == frozenset({'outer/inner/', 'outer/model.glb', 'outer/gone/'})
        assert nested.source == 'registrationWalk'
        walked = [r[2]['ExpressionAttributeValues'][':b'] for r in table.reads if r[1] == al.BUCKET_ID_INDEX_NAME]
        assert walked == ['reg-1', 'reg-2']

    def test_an_index_that_does_not_finish_keeps_the_pre_index_rule(self, monkeypatch):
        monkeypatch.setattr(al, 'MAX_NESTED_LOCATION_PAGES', 1)
        read, _ = _nested(monkeypatch, self.ROWS, ready=True, page_size=1)
        assert not read
        destructive, _ = _nested(monkeypatch, self.ROWS, ready=True, destructive=True, page_size=1)
        assert destructive.source == 'registrationWalk'
        assert 'outer/inner/' in destructive.locations

    def test_a_read_reuses_its_answer_for_a_short_time_and_a_destructive_one_never(self, monkeypatch):
        monkeypatch.setattr(al, 'index_ready', lambda name: True)
        table = AttributionAssetTable(self.ROWS)
        buckets = AttributionBucketsTable(REGISTRATIONS)
        for _ in range(3):
            fa.nested_locations_of_asset(table, buckets, OUTER, BUCKET, 'outer/')
        reads_after_reads = len(table.reads)
        fa.nested_locations_of_asset(table, buckets, OUTER, BUCKET, 'outer/', destructive=True)
        assert reads_after_reads == 1
        assert len(table.reads) == 2
