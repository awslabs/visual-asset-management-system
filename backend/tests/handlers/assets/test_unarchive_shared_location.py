# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An archived asset is not unarchived while another live asset is stored at exactly its location (#1207 D7).

The troubleshooting steps for two live assets on one location archive one of them. Unarchiving it again would
make the location shared again, and with it the writes bucket sync cannot attribute. So, once the location
index is ready, the API unarchive refuses while another live asset is stored at exactly the asset's location,
before any file, preview or record is touched, with UNARCHIVE_LOCATION_IN_USE_REFUSAL, which names neither
asset and points to the troubleshooting section. Bucket sync does not restore such an archived asset for a file
written into its folder either: the record goes to the rejected-record queue (`archivedOwnerLocationInUse`),
with no restore, ledger row, tags or binding.

A nested pair (one location inside the other) is not refused, in either direction: with the nested-asset rule
each of its keys has one deepest owner, so an outer or inner asset of a layout earlier releases created is
unarchived and restored as before. An archived asset on the location does not count, and before the index is
ready both unarchive as before.
"""

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

from tests.common.fileAttributionFakes import AttributionAssetTable, asset_row  # noqa: E402
from tests.handlers.assets.test_assetService_unarchive_deleted_database import _request  # noqa: E402
from tests.handlers.assets.test_assetService_history import _load as _load_service  # noqa: E402
from tests.handlers.indexing import test_sqsBucketSync_location_attribution as bs  # noqa: E402

al = sys.modules['common.assetLocations']
# Read with a fallback so the behaviour tests also run (and fail) on code without the rule
REFUSAL = getattr(al, "UNARCHIVE_LOCATION_IN_USE_REFUSAL", "(no unarchive refusal)")
SECTION = "Bucket Sync Rejected-Record Queue Holds Messages"

BUCKET = "bucket-1"
LOCATION = "p/shared/"
ARCHIVED = {**asset_row("db1", "asset-1", "b1", LOCATION, BUCKET, archived=True), "assetName": "N1",
            "status": "archived"}


class _Table(AttributionAssetTable):
    """The attribution fake with recorded writes, as the unarchive's asset table."""

    def __init__(self, rows, page_size=None):
        super().__init__(rows, page_size=page_size)
        self.put_item = MagicMock()
        self.delete_item = MagicMock()


@pytest.fixture(autouse=True)
def _ready(monkeypatch):
    monkeypatch.setattr(al, "index_ready", lambda name: True)
    yield


@pytest.fixture
def svc(monkeypatch):
    m = _load_service()
    monkeypatch.setattr(m, "db_table", MagicMock())
    m.db_table.get_item.return_value = {"Item": {"databaseId": "db1"}}
    monkeypatch.setattr(m, "get_asset_bucket_details", MagicMock(return_value={"bucketName": BUCKET}))
    for name in ("unarchive_multi_assetFiles", "unarchive_file_preview", "write_asset_history_record",
                 "update_asset_count", "send_subscription_email"):
        monkeypatch.setattr(m, name, MagicMock(return_value=0))
    monkeypatch.setattr(m, "claims_and_roles", {"tokens": ["u1"]})

    def wire(*others, page_size=None):
        monkeypatch.setattr(m, "asset_table", _Table([ARCHIVED, *others], page_size=page_size))
        return m
    return wire


def _unarchive(m):
    return m.unarchive_asset("db1", "asset-1", _request(m, unarchiveFiles=True), {"tokens": ["u1"]})


def _nothing_written(m):
    m.unarchive_multi_assetFiles.assert_not_called()
    m.unarchive_file_preview.assert_not_called()
    m.asset_table.put_item.assert_not_called()
    m.asset_table.delete_item.assert_not_called()
    m.write_asset_history_record.assert_not_called()
    m.update_asset_count.assert_not_called()


@pytest.mark.unit
class TestTheMessage:
    def test_it_names_the_condition_and_the_section_and_no_asset(self):
        assert "shares its storage location with another asset" in REFUSAL and SECTION in REFUSAL
        assert "db2" not in REFUSAL and "other" not in REFUSAL.replace("another", "")

    def test_the_troubleshooting_section_names_the_refusal_and_the_reason(self):
        guide = os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", "documentation", "docusaurus-site",
                             "docs", "troubleshooting", "common-issues.md")
        text = open(guide, encoding="utf-8").read()
        section = text[text.index(f"### {SECTION}"):]
        section = section[:section.index("\n### ", 4)]
        assert REFUSAL.split(" See ")[0] in section and "archivedOwnerLocationInUse" in section


@pytest.mark.unit
class TestTheApiUnarchive:
    def test_another_live_asset_at_the_same_location_refuses_it(self, svc):
        m = svc(asset_row("db2", "other", "b1", LOCATION, BUCKET))
        with pytest.raises(m.VAMSGeneralErrorResponse) as refused:
            _unarchive(m)
        assert str(refused.value).endswith(": " + REFUSAL)
        assert "db2" not in str(refused.value) and "other" not in str(refused.value).replace("another", "")
        _nothing_written(m)

    @pytest.mark.parametrize("other_location", ["p/shared/inner/", "p/"],
                             ids=["outer-unarchived-inner-live", "inner-unarchived-outer-live"])
    def test_a_nested_pair_is_unarchived_in_either_direction(self, svc, other_location):
        m = svc(asset_row("db2", "other", "b1", other_location, BUCKET))
        assert _unarchive(m).success is True
        m.asset_table.put_item.assert_called_once()

    def test_an_archived_asset_or_a_neighbour_does_not_refuse_it(self, svc):
        archived_other = asset_row("db2", "other", "b1", LOCATION, BUCKET, archived=True)
        neighbour = asset_row("db2", "next", "b1", "p/shared-old/", BUCKET)
        m = svc(archived_other, neighbour)
        assert _unarchive(m).success is True
        m.asset_table.put_item.assert_called_once()

    def test_before_the_index_is_ready_it_unarchives_as_before(self, svc, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: False)
        m = svc(asset_row("db2", "other", "b1", LOCATION, BUCKET))
        assert _unarchive(m).success is True

    def test_a_location_the_index_cannot_answer_for_unarchives_as_before(self, svc):
        # One row a page and more rows at the location than the lookup's page limit
        others = [asset_row("db3", f"old{i}", "b1", LOCATION, BUCKET, archived=True)
                  for i in range(al.MAX_PAGES_PER_LOOKUP + 1)]
        m = svc(*others, asset_row("db2", "other", "b1", LOCATION, BUCKET), page_size=1)
        assert _unarchive(m).success is True

    def test_the_lookup_pages_past_the_assets_own_and_archived_rows_to_a_live_one(self):
        # 30 archived rows and the asset's own at the location, then a live one; nested live rows never count
        rows = [ARCHIVED] + [asset_row("db3", f"old{i:02}", "b1", LOCATION, BUCKET, archived=True)
                             for i in range(30)] + [asset_row("db2", "zz-live", "b1", LOCATION, BUCKET),
                                                    asset_row("db4", "inner", "b1", "p/shared/inner/", BUCKET)]
        found = al.unarchive_conflicts(AttributionAssetTable(rows, page_size=10), ARCHIVED, BUCKET)
        assert [(e.database_id, e.asset_id) for e in found] == [("db2", "zz-live")]
        assert al.unarchive_conflicts(AttributionAssetTable(rows[:-2] + rows[-1:], page_size=10),
                                      ARCHIVED, BUCKET) == []


class _SyncTable(AttributionAssetTable):
    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        mock = MagicMock(name=name)
        setattr(self, name, mock)
        return mock


def _rejected(m):
    return [json.loads(c.kwargs["MessageBody"]) for c in m.sqs_client.send_message.call_args_list]


@pytest.mark.unit
class TestBucketSyncDoesNotRestoreIt:
    ARCHIVED_ENTRY = bs._entry(database_id="db1#deleted", asset_id="owner-1", location="projects/alpha/")

    def _run(self, rows, ready=True):
        m = bs._load()
        bs._wire(m, resolve=[bs.NOT_FOUND, bs._found(self.ARCHIVED_ENTRY)], ready=ready)
        archived_row = asset_row("db1", "owner-1", "bucket-1", "projects/alpha/", bs.BUCKET, archived=True)
        m.dynamodb.Table.return_value = _SyncTable([archived_row, *rows])
        response = m.lambda_handler_created({"Records": [bs._sqs("msg-1", bs._record("projects/alpha/x.glb"))]},
                                            MagicMock())
        return m, response

    def test_a_live_asset_at_the_same_location_sends_the_record_to_the_rejected_queue(self):
        other = asset_row("db2", "other", "bucket-1", "projects/alpha/", bs.BUCKET)
        m, response = self._run([other])
        assert response["batchItemFailures"] == []
        m.restore_archived_asset.assert_not_called()
        [message] = _rejected(m)
        assert message["reason"] == "archivedOwnerLocationInUse" and message["key"] == "projects/alpha/x.glb"
        assert message["candidateAssets"] == ["db1#deleted:owner-1", "db2:other"]
        assert bs._history_rows(m) == [] and bs._tagged(m) == [] and bs._sns_records(m) == []
        m.create_new_asset.assert_not_called()

    def test_a_live_asset_nested_in_its_location_does_not_stop_the_restore(self):
        inner = asset_row("db2", "inner", "bucket-1", "projects/alpha/inner/", bs.BUCKET)
        m, _ = self._run([inner])
        assert m.restore_archived_asset.call_args.args[:2] == ("bucket-1", "owner-1")
        assert _rejected(m) == []

    def test_control_an_archived_asset_at_the_same_location_does_not_either(self):
        archived_other = asset_row("db2", "other", "bucket-1", "projects/alpha/", bs.BUCKET, archived=True)
        m, _ = self._run([archived_other])
        assert m.restore_archived_asset.call_args.args[:2] == ("bucket-1", "owner-1")
        assert _rejected(m) == []

    def test_before_the_index_is_ready_the_asset_is_restored_as_before(self, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: False)
        other = asset_row("db2", "other", "bucket-1", "projects/alpha/", bs.BUCKET)
        m, _ = self._run([other], ready=False)
        m.restore_archived_asset.assert_called_once()
        assert _rejected(m) == []
