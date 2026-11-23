# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync files a key under the deepest stored location that contains it (#1207 D10).

The first-folder fast path finds the asset named by the key's first folder. A layout earlier releases allowed
stores another asset inside that asset's folder (`a1/inner/` inside `a1/`); a key there belongs to the inner
asset, as the file list, file info, downloads and the reindex already decide. So the fast path counts only when
no other stored location holds the key: one location-index query on the found asset's location names the
locations nested in it (live or archived) and any other live asset at the same location. When one holds the
key, the location index names the owner: a live nested asset gets the ledger row, the VAMS tags and the
forwarded binding; an archived one is restored, as any archived owner is; two live assets on one location
attribute nothing. The index is read at any time (a hit counts before the backfill marker exists too); a miss
before it proves nothing, so a nested asset the index does not list yet is decided by the first folder, as
before. A nested row the index lists but the table no longer has leaves the decision to the first folder.
"""

import sys
from unittest.mock import MagicMock

import pytest

from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)
from tests.handlers.indexing.test_sqsBucketSync_location_attribution import (  # noqa: E402,F401
    BUCKET, _deleted, _history_rows, _load, _record, _restore_module_attrs, _sns_records, _sqs, _tagged, _wire,
)

al = sys.modules['common.assetLocations']
fa = sys.modules['common.fileAttribution']

OUTER = asset_row("db1", "a1", "bucket-1", "a1/", BUCKET)
INNER = asset_row("db2", "inner", "bucket-1", "a1/inner/", BUCKET)
SAME_DB = asset_row("db1", "kit", "bucket-1", "a1/kit/", BUCKET)
ARCHIVED_INNER = asset_row("db2", "old", "bucket-1", "a1/old/", BUCKET, archived=True)


class _Table(AttributionAssetTable):
    """The attribution fake whose `gone` rows are listed by the index but no longer in the table."""

    def __init__(self, rows, gone=()):
        super().__init__(rows)
        self.gone = set(gone)

    def get_item(self, Key, **kwargs):
        if Key["assetId"] in self.gone:
            with self._lock:
                self.reads.append(('get_item', Key, kwargs))
            return {}
        return super().get_item(Key, **kwargs)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    # Load the shared module before patching the rule module's readiness (see D8)
    _load()
    monkeypatch.setattr(al, "index_ready", lambda name: False)
    al.clear_location_cache()
    al.clear_nested_locations_cache()
    yield
    al.clear_location_cache()
    al.clear_nested_locations_cache()


def _wire_index(m, rows, ready=False, gone=(), first_folder=OUTER):
    """The real owner lookup and nested-location lookup over an in-memory location index; the first folder
    `a1` names OUTER."""
    _wire(m, live=dict(first_folder) if first_folder else None, ready=ready)
    table = _Table(rows, gone=gone)
    m.dynamodb.Table.return_value = table
    m.resolve_owner = MagicMock(wraps=al.resolve_owner)
    m.is_object_permanently_deleted = MagicMock(return_value=True)
    m.delete_file_metadata_on_s3_delete = MagicMock()
    return table


def _created(m, key, receive_count=1):
    response = m.lambda_handler_created({"Records": [_sqs("msg-1", _record(key), receive_count)]}, MagicMock())
    assert response["batchItemFailures"] == []


def _filed(m):
    """(ledger row key, resolvedBy) of each history row, and (databaseId, assetId) of each forwarded binding."""
    rows = [(r["databaseId:assetId:filePath"], r["resolvedBy"]) for r in _history_rows(m)]
    bindings = [(r["vamsBinding"]["databaseId"], r["vamsBinding"]["assetId"]) for r in _sns_records(m)
                if "vamsBinding" in r]
    return rows, bindings


def _nested_queries(table):
    return [r for r in table.reads if r[0] == 'index' and r[1] == al.LOCATION_INDEX_NAME
            and 'begins_with' in r[2]['KeyConditionExpression']]


@pytest.mark.unit
class TestAWriteInsideANestedLocation:
    @pytest.mark.parametrize("ready", [True, False], ids=["index-ready", "before-readiness"])
    @pytest.mark.parametrize("nested", [INNER, SAME_DB], ids=["another-database", "same-database"])
    def test_it_is_filed_under_the_nested_asset(self, nested, ready):
        m = _load()
        _wire_index(m, [OUTER, nested], ready=ready)
        folder = nested["assetLocation"]["Key"]
        db, asset = nested["databaseId"], nested["assetId"]
        _created(m, folder + "x.txt")
        assert _filed(m) == ([(f"{db}:{asset}:/x.txt", "locationIndex")], [(db, asset)])
        assert _tagged(m) == [(BUCKET, folder + "x.txt", db, asset)]
        m.restore_archived_asset.assert_not_called()
        m.create_new_asset.assert_not_called()

    def test_the_binding_it_forwards_is_the_one_every_consumer_accepts(self):
        m = _load()
        table = _wire_index(m, [OUTER, INNER])
        _created(m, "a1/inner/sub/x.txt")
        [forwarded] = _sns_records(m)
        attribution = fa.attribute_object(BUCKET, "a1/inner/sub/x.txt", asset_table=table,
                                          buckets_table=AttributionBucketsTable([registration("bucket-1", BUCKET)]),
                                          binding=fa.binding_of(forwarded), asset_table_name=table.name)
        assert (attribution.database_id, attribution.asset_id, attribution.relative_path,
                attribution.resolved_by) == ("db2", "inner", "/sub/x.txt", "binding")

    def test_an_archived_nested_asset_is_restored_and_gets_the_file(self):
        m = _load()
        _wire_index(m, [OUTER, ARCHIVED_INNER])
        m.restore_archived_asset = MagicMock(return_value="db2")
        _created(m, "a1/old/x.txt")
        assert m.restore_archived_asset.call_args.args[:2] == ("bucket-1", "old")
        assert _filed(m) == ([("db2:old:/x.txt", "locationIndex")], [("db2", "old")])

    def test_an_archived_asset_nested_in_a_custom_root_asset_is_restored_and_gets_the_file(self):
        """No first-folder asset: the location index names the owner, the deepest stored location live or
        archived, not the live asset around it."""
        m = _load()
        outer = asset_row("db1", "owner-1", "bucket-1", "projects/alpha/", BUCKET)
        archived = asset_row("db2", "old2", "bucket-1", "projects/alpha/old/", BUCKET, archived=True)
        _wire_index(m, [outer, archived], first_folder=None)
        m.restore_archived_asset = MagicMock(return_value="db2")
        _created(m, "projects/alpha/old/x.txt")
        assert m.restore_archived_asset.call_args.args[:2] == ("bucket-1", "old2")
        assert _filed(m) == ([("db2:old2:/x.txt", "locationIndex")], [("db2", "old2")])
        # Control: a key of the custom-root asset's own folder is its file
        al.clear_location_cache()
        m.asset_file_version_history_table.reset_mock()
        m.sns_client.reset_mock()
        _created(m, "projects/alpha/y.txt")
        assert _filed(m) == ([("db1:owner-1:/y.txt", "locationIndex")], [("db1", "owner-1")])

    def test_a_live_asset_at_the_same_location_attributes_nothing(self):
        m = _load()
        twin = asset_row("db3", "twin", "bucket-1", "a1/", BUCKET)
        _wire_index(m, [OUTER, twin])
        _created(m, "a1/x.txt")
        [sent] = m.sqs_client.send_message.call_args_list
        assert '"ambiguousLocation"' in sent.kwargs["MessageBody"]
        assert _filed(m) == ([], []) and _tagged(m) == []

    def test_a_redelivered_record_reads_the_nested_locations_fresh(self):
        m = _load()
        table = _wire_index(m, [OUTER])
        _created(m, "a1/inner/a.txt")
        table.rows.append(INNER)
        m.asset_file_version_history_table.reset_mock()
        m.sns_client.reset_mock()
        _created(m, "a1/inner/a.txt", receive_count=2)
        assert _filed(m) == ([("db2:inner:/a.txt", "locationIndex")], [("db2", "inner")])


@pytest.mark.unit
class TestTheContainingAssetKeepsItsOwnKeys:
    def test_a_write_in_its_own_folder_stays_on_the_fast_path_with_one_index_query(self):
        m = _load()
        table = _wire_index(m, [OUTER, INNER])
        _created(m, "a1/sub/a.txt")
        _created(m, "a1/b.txt")
        rows, bindings = _filed(m)
        assert rows == [("db1:a1:/sub/a.txt", "fastPath"), ("db1:a1:/b.txt", "fastPath")]
        assert bindings == [("db1", "a1"), ("db1", "a1")]
        m.resolve_owner.assert_not_called()
        # The nested locations of the asset are read once and reused for the next write
        assert len(_nested_queries(table)) <= 1

    def test_a_nested_asset_the_index_does_not_list_yet_leaves_the_first_folder_decision(self):
        """Before the backfill a nested asset created by an earlier release has no index entry: a miss
        proves nothing then, and the first folder decides as before."""
        m = _load()
        unindexed = asset_row("db2", "inner", "bucket-1", "a1/inner/", BUCKET, indexed=False)
        _wire_index(m, [OUTER, unindexed])
        _created(m, "a1/inner/x.txt")
        assert _filed(m) == ([("db1:a1:/inner/x.txt", "fastPath")], [("db1", "a1")])

    def test_a_nested_row_gone_from_the_table_leaves_the_first_folder_decision(self):
        m = _load()
        _wire_index(m, [OUTER, INNER], gone={"inner"})
        _created(m, "a1/inner/x.txt")
        assert _filed(m) == ([("db1:a1:/inner/x.txt", "fastPath")], [("db1", "a1")])


@pytest.mark.unit
class TestADeleteInsideANestedLocation:
    def test_its_metadata_is_cleaned_up_under_the_nested_asset(self):
        m = _load()
        _wire_index(m, [OUTER, INNER])
        m.lambda_handler_deleted({"Records": [_sqs("d1", _deleted("a1/inner/x.txt"))]}, MagicMock())
        m.delete_file_metadata_on_s3_delete.assert_called_once_with("db2", "inner", "/x.txt")
        [forwarded] = _sns_records(m)
        assert (forwarded["vamsBinding"]["databaseId"], forwarded["vamsBinding"]["assetId"]) == ("db2", "inner")

    def test_control_a_delete_in_the_containing_assets_own_folder(self):
        m = _load()
        _wire_index(m, [OUTER, INNER])
        m.lambda_handler_deleted({"Records": [_sqs("d1", _deleted("a1/sub/x.txt"))]}, MagicMock())
        m.delete_file_metadata_on_s3_delete.assert_called_once_with("db1", "a1", "/sub/x.txt")
