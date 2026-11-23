# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bucket sync never creates a second asset with an id the asset table already holds (#1207 D12).

The first folder under a registration's prefix names an asset only when that asset's stored location holds the
key. An asset stored elsewhere in the bucket (a `bucketExistingKey` asset, a single-file asset, an asset nested
in another asset's folder by an earlier release) keeps its id, so a file written directly under `{assetId}/`
is no file of it, and no other location may hold it either. Auto-creating an asset for that folder would give
two assets one id, and every handler that finds an asset by its id alone (comments, subscriptions) then refuses
the real one. So the key is attributed to nobody: the record goes to the rejected-record queue with the reason
`assetIdInUse`, before and after the location index is ready, whichever database or bucket holds the id, and
an archived asset stored elsewhere is not restored for it either. `create_new_asset` refuses such an id on its
own too. An asset of this registration that holds the key but that the lookups missed is used instead.
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from tests.common.fileAttributionFakes import AttributionAssetTable, asset_row  # noqa: E402
from tests.handlers.indexing.test_sqsBucketSync_location_attribution import (  # noqa: E402,F401
    BUCKET, REJECTED_URL, _history_rows, _load, _record, _restore_module_attrs, _sns_records, _sqs, _tagged,
    _wire,
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


def _wire_rows(m, rows, first_folder=None, ready=False):
    """The real owner and nested-location lookups and the asset-id lookup over in-memory asset rows;
    `first_folder` is the live row the first-folder lookup returns."""
    _wire(m, live=dict(first_folder) if first_folder else None, ready=ready)
    table = AttributionAssetTable(rows)
    m.dynamodb.Table.return_value = table
    m.resolve_owner = MagicMock(wraps=al.resolve_owner)
    return table


def _handle(m, key, receive_count=1):
    return m.lambda_handler_created({"Records": [_sqs("msg-1", _record(key), receive_count)]}, MagicMock())


def _rejections(m):
    return [json.loads(c.kwargs["MessageBody"]) for c in m.sqs_client.send_message.call_args_list
            if c.kwargs.get("QueueUrl") == REJECTED_URL]


def _assert_refused(m, key, candidates):
    [body] = _rejections(m)
    assert (body["reason"], body["bucket"], body["key"]) == ("assetIdInUse", BUCKET, key)
    assert sorted(body["candidateAssets"]) == sorted(candidates)
    m.create_new_asset.assert_not_called()
    m.get_or_create_database_for_bucket.assert_not_called()
    m.restore_archived_asset.assert_not_called()
    assert _history_rows(m) == [] and _tagged(m) == [] and _sns_records(m) == []


@pytest.mark.unit
class TestAFolderNamedByAnAssetStoredElsewhere:
    @pytest.mark.parametrize("ready", [True, False], ids=["index-ready", "before-readiness"])
    @pytest.mark.parametrize("holder", [CUSTOM_ROOT, SINGLE_FILE], ids=["custom-root", "single-file"])
    def test_a_direct_write_under_its_id_folder_creates_no_second_asset(self, holder, ready):
        m = _load()
        _wire_rows(m, [holder], first_folder=holder, ready=ready)
        key = f"{ID}/d12-direct.txt"
        response = _handle(m, key)
        assert response["batchItemFailures"] == []
        _assert_refused(m, key, [f"db1:{ID}"])

    @pytest.mark.parametrize("key", [f"{ID}/", f"{ID}/sub/inb-1.txt"], ids=["folder-marker", "file"])
    def test_a_nested_asset_from_an_earlier_release_keeps_its_id(self, key):
        """The R3 seed: an asset created at `{assetId}/` whose row an earlier release left inside another
        asset's folder. The marker createAsset wrote at `{assetId}/` belongs to nobody."""
        m = _load()
        _wire_rows(m, [OUTER, NESTED], first_folder=NESTED, ready=True)
        _handle(m, key)
        _assert_refused(m, key, [f"db2:{ID}"])

    def test_an_id_held_in_another_bucket_is_refused_too(self):
        m = _load()
        _wire_rows(m, [OTHER_BUCKET])
        key = f"{ID}/copied.txt"
        _handle(m, key)
        _assert_refused(m, key, [f"db3:{ID}"])

    @pytest.mark.parametrize("ready", [True, False], ids=["index-ready", "before-readiness"])
    def test_an_archived_asset_stored_elsewhere_is_not_restored_for_it(self, ready):
        m = _load()
        _wire_rows(m, [ARCHIVED_ELSEWHERE], ready=ready)
        m.lookup_archived_asset = MagicMock(return_value=dict(ARCHIVED_ELSEWHERE))
        key = f"{ID}/x.txt"
        _handle(m, key)
        _assert_refused(m, key, [f"db2#deleted:{ID}"])

    def test_a_create_that_finds_the_id_in_use_is_refused_the_same_way(self):
        """The id came into use between the check and the create (or the create path skipped the check)."""
        m = _load()
        _wire_rows(m, [])
        holder = dict(CUSTOM_ROOT)
        m.create_new_asset = MagicMock(side_effect=m.AssetIdInUseError(ID, [holder]))
        key = f"{ID}/late.txt"
        _handle(m, key)
        [body] = _rejections(m)
        assert (body["reason"], body["candidateAssets"]) == ("assetIdInUse", [f"db1:{ID}"])
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
        assert [r["databaseId:assetId:filePath"] for r in _history_rows(m)] == [f"db1:{ID}:/a.txt"]
        assert _rejections(m) == [] and not m.create_new_asset.called


@pytest.mark.unit
class TestCreateNewAsset:
    def test_it_refuses_an_id_another_database_holds(self, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [CUSTOM_ROOT])
        m.create_new_asset = _restore_module_attrs["create_new_asset"]
        m.create_asset = MagicMock()
        with pytest.raises(m.AssetIdInUseError) as refused:
            m.create_new_asset("bucket-1", "default-db", ID)
        assert [(r["databaseId"], r["assetId"]) for r in refused.value.holders] == [("db1", ID)]
        m.create_asset.assert_not_called()

    def test_it_refuses_an_archived_id(self, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [ARCHIVED_ELSEWHERE])
        m.create_new_asset = _restore_module_attrs["create_new_asset"]
        m.create_asset = MagicMock()
        with pytest.raises(m.AssetIdInUseError):
            m.create_new_asset("bucket-1", "default-db", ID)
        m.create_asset.assert_not_called()

    def test_control_a_free_id_is_created(self, _restore_module_attrs):  # noqa: F811
        m = _load()
        _wire_rows(m, [OUTER])
        m.create_new_asset = _restore_module_attrs["create_new_asset"]
        m.create_asset = MagicMock(return_value=MagicMock(assetId=ID))
        assert m.create_new_asset("bucket-1", "default-db", ID) == ID
        assert m.create_asset.call_count == 1


@pytest.mark.unit
class TestTheTroubleshootingGuide:
    def test_it_explains_the_reason_and_how_to_resolve_it(self):
        text = TROUBLESHOOTING.read_text(encoding="utf-8")
        section = text.split(f"### {SECTION}", 1)[1].split("\n### ", 1)[0]
        assert "`assetIdInUse`" in section
        assert "bucketExistingKey" in section
