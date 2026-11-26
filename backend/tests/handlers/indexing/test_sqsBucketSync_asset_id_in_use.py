# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync creates no second asset with an id its target database already holds (#1207, #1860).

An asset is addressed by its database and asset ids, so the same asset id may be held by assets of several
databases. A file no stored location holds, written under a first-level folder `{assetId}/`, becomes a new asset
with that id in the database bucket sync creates assets in for the bucket. When that database already holds the id
(live or archived, stored elsewhere: a `bucketExistingKey` asset, a single-file asset, an asset nested in another
asset's folder by an earlier release), nothing is created or attributed and the record goes to the rejected-record
queue with the reason `assetIdInUse`. An asset with the id in another database, or in another bucket, does not stop
the create, and an archived asset stored elsewhere is not restored for the key.

The first folder names the asset of this registration whose stored location holds the key: when assets of several
databases share the id in one registration, the one whose location holds the key is used (the deepest when several
do; two at one location are rejected `ambiguousLocation`), before the location index is ready as after, so such a
pair is not received again and again. An asset of this registration that holds the key but that the lookups missed
is used instead of a new one.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.common.fileAttributionFakes import AttributionAssetTable, asset_row  # noqa: E402
from tests.handlers.indexing.test_sqsBucketSync_location_attribution import (  # noqa: E402,F401
    BUCKET, REJECTED_URL, _deleted, _history_rows, _load, _record, _restore_module_attrs, _sns_records, _sqs,
    _tagged, _wire,
)

al = sys.modules['common.assetLocations']

ID = "x29cf13a1-75b0-4752-9367-1a60074dcfa1"
CUSTOM_ROOT = asset_row("db1", ID, "bucket-1", "proj/root/", BUCKET)
SINGLE_FILE = asset_row("db1", ID, "bucket-1", "proj/model.glb", BUCKET)
OUTER = asset_row("db1", "outer", "bucket-1", "outer/", BUCKET)
NESTED = asset_row("db2", ID, "bucket-1", "outer/inb/", BUCKET)
OTHER_BUCKET = asset_row("db3", ID, "bucket-2", f"{ID}/", "other-bucket")
ARCHIVED_ELSEWHERE = asset_row("db2", ID, "bucket-1", "proj/old/", BUCKET, archived=True)
TROUBLESHOOTING = (Path(__file__).resolve().parents[4] / "documentation" / "docusaurus-site" / "docs"
                   / "troubleshooting" / "common-issues.md")
SECTION = "Bucket Sync Rejected-Record Queue Holds Messages"


def _held(database_id):
    """A `candidateAssets` entry for the asset with the test's id in `database_id`."""
    return ":".join((database_id, ID))


def _file_of(database_id, path):
    """A version-history row's `databaseId:assetId:filePath` for the asset with the test's id."""
    return ":".join((database_id, ID, path))


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    # Load the shared module before patching the rule module's readiness (see D8)
    m = _load()
    monkeypatch.setattr(al, "index_ready", lambda name: False)
    al.clear_location_cache()
    al.clear_nested_locations_cache()
    m.asset_cache.clear()
    yield
    al.clear_location_cache()
    al.clear_nested_locations_cache()
    m.asset_cache.clear()


def _wire_rows(m, rows, first_folder=None, ready=False, target_database="db1"):
    """The real owner and nested-location lookups and the asset-id lookups over in-memory asset rows;
    `first_folder` is the live row the first-folder lookup returns, `target_database` the database bucket sync
    creates the bucket's assets in."""
    _wire(m, live=dict(first_folder) if first_folder else None, ready=ready)
    table = AttributionAssetTable(rows)
    m.dynamodb.Table.return_value = table
    m.resolve_owner = MagicMock(wraps=al.resolve_owner)
    m.get_or_create_database_for_bucket = MagicMock(return_value=target_database)
    return table


def _real_create(m, monkeypatch, restored):
    """The real create_new_asset over the in-memory rows, with createAsset's own create mocked."""
    m.create_new_asset = restored["create_new_asset"]
    create_asset = MagicMock(side_effect=lambda request, claims, system: MagicMock(assetId=request.assetId))
    monkeypatch.setattr(m, "create_asset", create_asset)
    return create_asset


def _handle(m, key, receive_count=1):
    return m.lambda_handler_created({"Records": [_sqs("msg-1", _record(key), receive_count)]}, MagicMock())


def _rejections(m):
    return [json.loads(c.kwargs["MessageBody"]) for c in m.sqs_client.send_message.call_args_list
            if c.kwargs.get("QueueUrl") == REJECTED_URL]


def _created(create_asset):
    return {(c.args[0].databaseId, c.args[0].assetId) for c in create_asset.call_args_list}


def _assert_refused(m, key, candidates, reason="assetIdInUse"):
    [body] = _rejections(m)
    assert (body["reason"], body["bucket"], body["key"]) == (reason, BUCKET, key)
    assert sorted(body["candidateAssets"]) == sorted(candidates)
    m.restore_archived_asset.assert_not_called()
    assert _history_rows(m) == [] and _tagged(m) == [] and _sns_records(m) == []


@pytest.mark.unit
class TestAnIdAnotherDatabaseHolds:
    """The folder becomes an asset of the bucket's database, as for any new folder."""

    @pytest.mark.parametrize("ready", [True, False], ids=["index-ready", "before-readiness"])
    @pytest.mark.parametrize("holder", [CUSTOM_ROOT, SINGLE_FILE], ids=["custom-root", "single-file"])
    def test_a_direct_write_under_its_id_folder_creates_the_asset_in_the_target_database(
            self, holder, ready, monkeypatch, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [holder], first_folder=holder, ready=ready, target_database="db-target")
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        key = f"{ID}/direct.txt"
        response = _handle(m, key)
        assert response["batchItemFailures"] == []
        assert _created(create_asset) == {("db-target", ID)}
        assert _rejections(m) == []
        assert {r["databaseId:assetId:filePath"] for r in _history_rows(m)} == {_file_of("db-target", "/direct.txt")}

    @pytest.mark.parametrize("key", [f"{ID}/", f"{ID}/sub/inb-1.txt"], ids=["folder-marker", "file"])
    def test_a_nested_asset_of_another_database_does_not_stop_the_create(self, key, monkeypatch,
                                                                         _restore_module_attrs):  # noqa: F811
        """The R3 seed: an asset with the id whose row an earlier release left inside another asset's folder.
        Its database is not the bucket's, so the folder becomes the bucket's database's asset."""
        m = _load()
        _wire_rows(m, [OUTER, NESTED], first_folder=NESTED, ready=True, target_database="db-target")
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        _handle(m, key)
        assert _created(create_asset) == {("db-target", ID)}
        assert _rejections(m) == []

    def test_an_id_held_in_another_bucket_does_not_stop_the_create(self, monkeypatch,
                                                                   _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [OTHER_BUCKET], target_database="db-target")
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        _handle(m, f"{ID}/copied.txt")
        assert _created(create_asset) == {("db-target", ID)}
        assert _rejections(m) == []

    @pytest.mark.parametrize("ready", [True, False], ids=["index-ready", "before-readiness"])
    def test_an_archived_asset_of_another_database_is_not_restored_and_does_not_block(
            self, ready, monkeypatch, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [ARCHIVED_ELSEWHERE], ready=ready, target_database="db-target")
        m.lookup_archived_asset = MagicMock(return_value=dict(ARCHIVED_ELSEWHERE))
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        _handle(m, f"{ID}/x.txt")
        m.restore_archived_asset.assert_not_called()
        assert _created(create_asset) == {("db-target", ID)}
        assert _rejections(m) == []


@pytest.mark.unit
class TestAnIdTheTargetDatabaseHolds:
    """A second asset with one id in one database is never created."""

    @pytest.mark.parametrize("ready", [True, False], ids=["index-ready", "before-readiness"])
    @pytest.mark.parametrize("holder", [CUSTOM_ROOT, SINGLE_FILE], ids=["custom-root", "single-file"])
    def test_a_live_asset_stored_elsewhere_refuses_the_key(self, holder, ready, monkeypatch,
                                                           _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [holder], first_folder=holder, ready=ready, target_database="db1")
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        key = f"{ID}/direct.txt"
        response = _handle(m, key)
        assert response["batchItemFailures"] == []
        _assert_refused(m, key, [_held("db1")])
        create_asset.assert_not_called()

    @pytest.mark.parametrize("ready", [True, False], ids=["index-ready", "before-readiness"])
    def test_an_archived_asset_stored_elsewhere_refuses_the_key_and_is_not_restored(
            self, ready, monkeypatch, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [ARCHIVED_ELSEWHERE], ready=ready, target_database="db2")
        m.lookup_archived_asset = MagicMock(return_value=dict(ARCHIVED_ELSEWHERE))
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        key = f"{ID}/x.txt"
        _handle(m, key)
        _assert_refused(m, key, [_held("db2#deleted")])
        create_asset.assert_not_called()

    def test_a_create_that_finds_the_id_in_use_is_refused(self):
        m = _load()
        _wire_rows(m, [])
        holder = dict(CUSTOM_ROOT)
        m.create_new_asset = MagicMock(side_effect=m.AssetIdInUseError(ID, [holder]))
        key = f"{ID}/late.txt"
        _handle(m, key)
        [body] = _rejections(m)
        assert (body["reason"], body["candidateAssets"]) == ("assetIdInUse", [_held("db1")])
        assert _history_rows(m) == [] and _sns_records(m) == []

    def test_an_id_lookup_that_fails_returns_the_record_to_its_queue(self):
        m = _load()
        table = _wire_rows(m, [])
        real_query = table.query

        def query(**kwargs):
            if kwargs.get("IndexName") == "assetIdGSI":
                raise RuntimeError("throttled")
            return real_query(**kwargs)
        table.query = query
        response = _handle(m, f"{ID}/x.txt")
        assert response["batchItemFailures"] == [{"itemIdentifier": "msg-1"}]
        m.create_new_asset.assert_not_called()
        assert _rejections(m) == []


@pytest.mark.unit
class TestTheFirstFolderLookup:
    """Assets of several databases with one id in one registration: the one whose location holds the key.

    The rows carry no location index attributes yet (the backfill has not reached them), so only the first-folder
    lookup can find the asset. When it returned the first live row of the pair and that row was stored elsewhere,
    the record went back to its queue on every receive until it reached the dead-letter queue.
    """

    @pytest.mark.parametrize("ready", [True, False], ids=["index-ready", "before-readiness"])
    def test_the_asset_whose_location_holds_the_key_is_used(self, ready):
        m = _load()
        first = asset_row("db1", ID, "bucket-1", "proj/root/", BUCKET, indexed=False)
        holder = asset_row("db2", ID, "bucket-1", f"{ID}/", BUCKET, indexed=False)
        _wire_rows(m, [first, holder], first_folder=first, ready=ready)
        response = _handle(m, f"{ID}/a.txt")
        assert response["batchItemFailures"] == []
        assert {r["databaseId:assetId:filePath"] for r in _history_rows(m)} == {_file_of("db2", "/a.txt")}
        assert _rejections(m) == [] and not m.create_new_asset.called

    def test_the_deepest_location_that_holds_the_key_wins(self):
        m = _load()
        first = asset_row("db1", ID, "bucket-1", "proj/root/", BUCKET, indexed=False)
        outer = asset_row("db2", ID, "bucket-1", f"{ID}/", BUCKET, indexed=False)
        inner = asset_row("db3", ID, "bucket-1", f"{ID}/sub/", BUCKET, indexed=False)
        _wire_rows(m, [first, outer, inner], first_folder=first)
        response = _handle(m, f"{ID}/sub/a.txt")
        assert response["batchItemFailures"] == []
        assert {r["databaseId:assetId:filePath"] for r in _history_rows(m)} == {_file_of("db3", "/a.txt")}

    def test_two_at_one_location_are_rejected_as_ambiguous(self):
        m = _load()
        first = asset_row("db1", ID, "bucket-1", "proj/root/", BUCKET, indexed=False)
        one = asset_row("db2", ID, "bucket-1", f"{ID}/", BUCKET, indexed=False)
        two = asset_row("db3", ID, "bucket-1", f"{ID}/", BUCKET, indexed=False)
        _wire_rows(m, [first, one, two], first_folder=first)
        key = f"{ID}/a.txt"
        response = _handle(m, key)
        assert response["batchItemFailures"] == []
        _assert_refused(m, key, [_held("db2"), _held("db3")], reason="ambiguousLocation")
        m.create_new_asset.assert_not_called()

    def test_a_delete_cleans_up_under_the_asset_whose_location_holds_the_key(self):
        m = _load()
        first = asset_row("db1", ID, "bucket-1", "proj/root/", BUCKET, indexed=False)
        holder = asset_row("db2", ID, "bucket-1", f"{ID}/", BUCKET, indexed=False)
        _wire_rows(m, [first, holder], first_folder=first)
        m.is_object_permanently_deleted = MagicMock(return_value=True)
        m.delete_file_metadata_on_s3_delete = MagicMock()
        m.lambda_handler_deleted({"Records": [_sqs("d1", _deleted(f"{ID}/model.glb"))]}, MagicMock())
        m.delete_file_metadata_on_s3_delete.assert_any_call("db2", ID, "/model.glb")

    def test_control_a_first_row_that_holds_the_key_is_used_without_another_read(self):
        m = _load()
        normal = asset_row("db1", ID, "bucket-1", f"{ID}/", BUCKET)
        table = _wire_rows(m, [normal, asset_row("db2", ID, "bucket-1", "proj/other/", BUCKET)],
                           first_folder=normal, ready=True)
        _handle(m, f"{ID}/a.txt")
        assert {r["databaseId:assetId:filePath"] for r in _history_rows(m)} == {_file_of("db1", "/a.txt")}
        assert table.reads, "no asset table read was recorded, so the check below would be vacuous"
        assert ("query", "assetIdGSI") not in {read[:2] for read in table.reads}


@pytest.mark.unit
class TestControls:
    def test_a_new_folder_whose_id_no_asset_has_is_auto_created(self):
        m = _load()
        _wire_rows(m, [OUTER], ready=True)
        _handle(m, f"{ID}/new.txt")
        assert m.create_new_asset.call_args.args == ("bucket-1", "db1", ID)
        assert _rejections(m) == []

    def test_a_normal_asset_keeps_its_id_folder(self):
        m = _load()
        normal = asset_row("db1", ID, "bucket-1", f"{ID}/", BUCKET)
        _wire_rows(m, [normal], first_folder=normal, ready=True)
        _handle(m, f"{ID}/a.txt")
        assert [r["resolvedBy"] for r in _history_rows(m)] == ["fastPath"]
        assert _rejections(m) == [] and not m.create_new_asset.called

    def test_another_asset_stored_at_the_id_folder_owns_its_keys(self):
        """An asset created on the existing key `{assetId}/` of an asset stored elsewhere holds that folder."""
        m = _load()
        on_folder = asset_row("db4", "x-onfolder", "bucket-1", f"{ID}/", BUCKET)
        _wire_rows(m, [CUSTOM_ROOT, on_folder], first_folder=CUSTOM_ROOT, ready=True)
        _handle(m, f"{ID}/a.txt")
        assert [r["databaseId:assetId:filePath"] for r in _history_rows(m)] == ["db4:x-onfolder:/a.txt"]
        assert _rejections(m) == []

    def test_an_asset_that_holds_the_key_but_was_not_found_is_looked_up_again(self):
        """Its row is in the asset-id index but not yet in the others (or their read failed)."""
        m = _load()
        unindexed = asset_row("db1", ID, "bucket-1", f"{ID}/", BUCKET, indexed=False)
        _wire_rows(m, [unindexed])
        response = _handle(m, f"{ID}/a.txt")
        assert response["batchItemFailures"] == [{"itemIdentifier": "msg-1"}]
        m.create_new_asset.assert_not_called()
        assert _rejections(m) == []

    def test_an_archived_asset_of_its_own_folder_the_index_does_not_list_is_restored(self):
        m = _load()
        legacy = asset_row("db1", ID, "bucket-1", f"{ID}/", BUCKET, archived=True, indexed=False)
        _wire_rows(m, [legacy], ready=True)
        _handle(m, f"{ID}/a.txt")
        assert m.restore_archived_asset.call_args.args[:2] == ("bucket-1", ID)
        assert [r["databaseId:assetId:filePath"] for r in _history_rows(m)] == [_file_of("db1", "/a.txt")]
        assert _rejections(m) == [] and not m.create_new_asset.called


@pytest.mark.unit
class TestCreateNewAsset:
    def test_it_refuses_an_id_the_target_database_holds(self, monkeypatch, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [CUSTOM_ROOT])
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        with pytest.raises(m.AssetIdInUseError) as refused:
            m.create_new_asset("bucket-1", "db1", ID)
        assert {(r["databaseId"], r["assetId"]) for r in refused.value.holders} == {("db1", ID)}
        create_asset.assert_not_called()

    def test_it_refuses_an_id_an_archived_asset_of_the_target_database_holds(
            self, monkeypatch, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [ARCHIVED_ELSEWHERE])
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        with pytest.raises(m.AssetIdInUseError) as refused:
            m.create_new_asset("bucket-1", "db2", ID)
        assert {(r["databaseId"], r["assetId"]) for r in refused.value.holders} == {("db2#deleted", ID)}
        create_asset.assert_not_called()

    @pytest.mark.parametrize("holder", [CUSTOM_ROOT, ARCHIVED_ELSEWHERE, OTHER_BUCKET],
                             ids=["live", "archived", "other-bucket"])
    def test_it_creates_an_id_only_another_database_holds(self, holder, monkeypatch,
                                                          _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [holder])
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        assert m.create_new_asset("bucket-1", "default-db", ID) == ID
        assert _created(create_asset) == {("default-db", ID)}

    def test_it_reads_both_partitions_of_the_target_database_consistently(
            self, monkeypatch, _restore_module_attrs):  # noqa: F811
        m = _load()
        table = _wire_rows(m, [OUTER])
        _real_create(m, monkeypatch, _restore_module_attrs)
        m.create_new_asset("bucket-1", "default-db", ID)
        reads = {(read[1]["databaseId"], read[1]["assetId"], read[2].get("ConsistentRead"))
                 for read in table.reads if read[0] == "get_item"}
        assert {("default-db", ID, True), ("default-db#deleted", ID, True)} <= reads

    def test_a_read_that_fails_returns_the_record_to_its_queue(
            self, monkeypatch, _restore_module_attrs):  # noqa: F811
        m = _load()
        table = _wire_rows(m, [])
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        table.get_item = MagicMock(side_effect=RuntimeError("throttled"))
        with pytest.raises(m.RecordRedrive):
            m.create_new_asset("bucket-1", "db1", ID)
        create_asset.assert_not_called()

    def test_control_a_free_id_is_created(self, monkeypatch, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [OUTER])
        create_asset = _real_create(m, monkeypatch, _restore_module_attrs)
        assert m.create_new_asset("bucket-1", "default-db", ID) == ID
        assert create_asset.called


@pytest.mark.unit
class TestTheTroubleshootingGuide:
    def test_it_explains_the_reason_and_how_to_resolve_it(self):
        text = TROUBLESHOOTING.read_text(encoding="utf-8")
        section = text.split(f"### {SECTION}", 1)[1].split("\n### ", 1)[0]
        assert "`assetIdInUse`" in section
        assert "bucketExistingKey" in section

    def test_it_limits_the_reason_to_the_target_database(self):
        text = TROUBLESHOOTING.read_text(encoding="utf-8")
        section = text.split(f"### {SECTION}", 1)[1].split("\n### ", 1)[0]
        assert "in any database or bucket" not in section
        assert "same database" in section


@pytest.mark.unit
class TestANewAssetIdWhoseLastCharacterIsNotAdmitted:
    """The folder name is the new asset id; its last character follows the body rule."""

    @pytest.mark.parametrize("asset_id", ["part%", "part)", "part!", "part™"])
    def test_no_asset_is_created(self, asset_id, monkeypatch):
        m = _load()
        monkeypatch.setattr(m, "database_asset_id_rows", MagicMock(side_effect=AssertionError("must not read")))
        monkeypatch.setattr(m, "create_asset", MagicMock(side_effect=AssertionError("must not create")))

        assert m.create_new_asset("bucket-1", "db1", asset_id) is None
        m.create_asset.assert_not_called()
