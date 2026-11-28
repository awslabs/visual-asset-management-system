# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A permanent delete removes the old versions a v2.6 to v2.7 ``move`` left outside the asset (#1888).

The upgrade step ``assetLocationFileToFolder`` moves a single-file asset's file ``K`` and its own previews
``K.previewFile.{ext}`` into the companion folder ``K/`` (``K/{name}``, ``K/{name}.previewFile.{ext}``): it
copies every version as a relocated version that names its source version, and puts a delete marker on each
source key. The sources lie outside the asset's new location ``K/``, so a delete of the location alone left
them. The asset's permanent delete, and the file delete of the moved file, now delete at the exact source keys
the versions the copies name, then the delete markers of a source key left with no version:

-   a version written at a source key after the move is not the asset's and stays, with the key's markers;
-   keys that only share the file key's characters (``K.bak``, ``Kx/...``, ``K.previewFile.`` with no
    extension, ``K.previewFile.png/x``) are never touched;
-   the old versions go before the location, so a delete S3 refuses keeps the asset (or the file) and the copies
    that name them, and a retry finishes the job;
-   a move the step has not finished (``state`` ``pending``) refuses the delete and changes nothing;
-   ``in-place`` and ``append-slash`` conversions, whose keys are all inside the location, delete as before.

The copies are written by the step's own ``move_object`` on a versioned moto bucket, so the delete reads what
the step writes. The harness is the permanent delete's S3 failure harness (``test_permanent_delete_s3_failures``).
"""

import importlib.util
import os
import pathlib
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")

from backend.backend.handlers.assets import assetFiles as af  # noqa: E402
from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)
from tests.handlers.assets.test_assetService_history import _asset, _load  # noqa: E402
from tests.handlers.assets.test_permanent_delete_s3_failures import (  # noqa: E402
    ASSET_MESSAGE, BUCKET, CLAIMS, FILE_MESSAGE, _FaultyS3, _access_denied,
)

al = sys.modules["common.assetLocations"]
fa = sys.modules["common.fileAttribution"]

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
STEP_MODULE = (REPO_ROOT / "infra" / "deploymentDataMigration" / "v2.6_to_v2.7" / "upgrade"
               / "asset_location_file_to_folder.py")

FILE_KEY = "proj/a/model.glb"
PREVIEW = FILE_KEY + ".previewFile.png"
TARGET = FILE_KEY + "/"
MOVED = TARGET + "model.glb"
MOVED_PREVIEW = MOVED + ".previewFile.png"
COMPANION = TARGET + "sub/c.obj"
OLD_KEYS = (FILE_KEY, PREVIEW)
# Keys that share the file key's characters but are not the file or one of its own previews
LOOKALIKES = ("proj/a/model.glb.bak", "proj/a/model.glbx/y.txt", "proj/a/model.glb.previewFile.",
              "proj/a/model.glb.previewFile.png/x", "proj/a/model.glb.previewFilepng")
NEIGHBOUR = "proj/a/other.glb"
PENDING_MESSAGE = ("This asset's file is still being moved into its folder by the v2.6 to v2.7 upgrade step "
                   "assetLocationFileToFolder. Run that step again to finish the move, then try again.")


def _step():
    spec = importlib.util.spec_from_file_location("asset_location_file_to_folder_for_delete_tests", STEP_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


STEP = _step()


def _record(mode="move", state="complete", source=FILE_KEY, target=TARGET):
    record = {"from": source, "storedFrom": source, "to": target, "mode": mode, "state": state,
              "startedAt": "2026-11-27T04:00:00+00:00", "archived": []}
    if state == "complete":
        record["completedAt"] = "2026-11-27T04:01:00+00:00"
    return record


class _Faulty(_FaultyS3):
    """The failure harness's client, which can also refuse every HeadObject."""

    def __init__(self, client):
        super().__init__(client)
        self.deny_heads = False
        # The prefixes listed with a '/' delimiter: the old-key reads; the location delete lists without one
        self.delimited = []
        # A page size for those reads, so a test can make them span pages
        self.delimited_page_size = None

    def list_object_versions(self, **kwargs):
        if kwargs.get("Delimiter"):
            self.delimited.append(kwargs.get("Prefix"))
            if self.delimited_page_size:
                kwargs["MaxKeys"] = self.delimited_page_size
        return super().list_object_versions(**kwargs)

    def head_object(self, **kwargs):
        if self.deny_heads:
            raise _access_denied("HeadObject")
        return self._client.head_object(**kwargs)

    def lift(self):
        super().lift()
        self.deny_heads = False


def _entries(client, key):
    """Every version and delete marker id at exactly ``key``, newest first."""
    listing = client.list_object_versions(Bucket=BUCKET, Prefix=key)
    return [e["VersionId"] for e in listing.get("Versions", []) + listing.get("DeleteMarkers", [])
            if e["Key"] == key]


def _version_ids(client, key):
    listing = client.list_object_versions(Bucket=BUCKET, Prefix=key)
    return [e["VersionId"] for e in listing.get("Versions", []) if e["Key"] == key]


def _keys_under(client, prefix):
    listing = client.list_object_versions(Bucket=BUCKET, Prefix=prefix)
    return sorted({e["Key"] for e in listing.get("Versions", []) + listing.get("DeleteMarkers", [])})


def _move(client):
    """Write the file, its preview and a companion file, then move them as the upgrade step does."""
    client.put_object(Bucket=BUCKET, Key=FILE_KEY, Body=b"v1")
    client.put_object(Bucket=BUCKET, Key=FILE_KEY, Body=b"v2")
    client.put_object(Bucket=BUCKET, Key=PREVIEW, Body=b"p1")
    client.put_object(Bucket=BUCKET, Key=COMPANION, Body=b"c1")
    for key in LOOKALIKES + (NEIGHBOUR,):
        client.put_object(Bucket=BUCKET, Key=key, Body=b"x")
    for source, destination, path in ((FILE_KEY, MOVED, "/model.glb"),
                                      (PREVIEW, MOVED_PREVIEW, "/model.glb.previewFile.png")):
        outcome, mapping = STEP.move_object(client, BUCKET, source, destination, "db1", "a1", path, False)
        assert outcome == "moved" and mapping


@pytest.fixture
def s3(monkeypatch):
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        faulty = _Faulty(client)
        monkeypatch.setattr(af, "s3_client", faulty)
        al.clear_nested_locations_cache()
        fa.clear_registration_cache()
        monkeypatch.setattr(al, "index_ready", lambda name: True)
        monkeypatch.setattr(af, "MAX_PARALLEL_S3_WORKERS", 1)
        monkeypatch.setattr(af, "delete_assetAuxiliary_files", MagicMock(name="delete_assetAuxiliary_files"))
        yield SimpleNamespace(client=client, faulty=faulty)


@pytest.fixture
def svc(s3, monkeypatch):
    """assetService's permanent delete of a moved asset, on the real S3 deletes and the moto bucket."""
    _move(s3.client)
    m = _load()
    asset = _asset(db="db1", aid="a1", assetLocation={"Key": TARGET}, assetLocationConversion=_record())
    state = SimpleNamespace(m=m, asset=asset, client=s3.client, faulty=s3.faulty)
    real_lookup = fa.nested_locations_of_asset

    def nested_lookup(_table, _buckets, row, bucket_name, *args, **kwargs):
        rows = [asset_row("db1", "a1", "b1", state.asset["assetLocation"]["Key"], BUCKET)]
        return real_lookup(AttributionAssetTable(rows), AttributionBucketsTable([registration("b1", BUCKET, "/")]),
                           row, bucket_name, *args, **kwargs)
    stubs = {
        "s3": s3.faulty,
        "get_asset_details": MagicMock(side_effect=lambda *args, **kwargs: dict(state.asset)),
        "authorize_single_asset": MagicMock(return_value=True),
        "get_asset_bucket_details": MagicMock(return_value={"bucketName": BUCKET}),
        "delete_s3_prefix_all_versions": af.delete_s3_prefix_all_versions,
        "delete_files_metadata": MagicMock(name="delete_files_metadata"),
        "delete_file_version_records": MagicMock(),
        "delete_auxiliary_files_of_keys": MagicMock(),
        "delete_assetAuxiliary_files": MagicMock(),
        "delete_asset_metadata_for_permanent_deletion": MagicMock(),
        "write_asset_history_record": MagicMock(),
        "update_asset_count": MagicMock(),
        "asset_table": MagicMock(),
        "sns_client": MagicMock(),
        "subscription_table": MagicMock(),
        "asset_links_table": None,
        "asset_upload_table": None,
        "comment_table": None,
        "versions_table": None,
        "asset_versions_files_table": None,
        "asset_file_metadata_versions_table": None,
        "nested_locations_of_asset": nested_lookup,
        "S3PrefixDeleteIncomplete": af.S3PrefixDeleteIncomplete,
    }
    for name, value in stubs.items():
        monkeypatch.setattr(m, name, value, raising=False)
    yield state


def _delete_asset(svc):
    request = MagicMock()
    request.confirmPermanentDelete = True
    return svc.m.delete_asset_permanent(svc.asset["databaseId"], "a1", request, {"tokens": ["u1"]})


def _lookalikes_and_neighbour_kept(client):
    for key in LOOKALIKES + (NEIGHBOUR,):
        assert len(_entries(client, key)) == 1, key


@pytest.mark.unit
class TestTheAssetPermanentDeleteOfAMovedAsset:
    def test_the_old_versions_and_markers_at_the_file_and_preview_keys_go(self, svc):
        assert len(_version_ids(svc.client, FILE_KEY)) == 2 and len(_entries(svc.client, FILE_KEY)) == 3

        response = _delete_asset(svc)

        assert response.success is True
        for key in OLD_KEYS:
            assert _entries(svc.client, key) == [], key
        assert _keys_under(svc.client, TARGET) == []
        _lookalikes_and_neighbour_kept(svc.client)
        svc.m.asset_table.delete_item.assert_called()

    def test_an_archived_moved_asset_loses_its_old_versions_too(self, svc):
        # Archive puts a delete marker on each key in the location; the copies' versions stay behind it
        for key in (MOVED, MOVED_PREVIEW, COMPANION):
            svc.client.delete_object(Bucket=BUCKET, Key=key)
        svc.asset.update(databaseId="db1#deleted", status="archived")

        response = _delete_asset(svc)

        assert response.success is True
        for key in OLD_KEYS:
            assert _entries(svc.client, key) == [], key
        assert _keys_under(svc.client, TARGET) == []
        _lookalikes_and_neighbour_kept(svc.client)

    def test_a_version_written_at_the_old_key_after_the_move_stays_with_its_markers(self, svc):
        foreign = svc.client.put_object(Bucket=BUCKET, Key=FILE_KEY, Body=b"later")["VersionId"]
        markers_before = set(_entries(svc.client, FILE_KEY)) - set(_version_ids(svc.client, FILE_KEY))

        _delete_asset(svc)

        assert _version_ids(svc.client, FILE_KEY) == [foreign]
        assert set(_entries(svc.client, FILE_KEY)) == {foreign} | markers_before
        assert _entries(svc.client, PREVIEW) == []

    @pytest.mark.parametrize("metadata", [
        {"vams-changesource": "fileRevert", "vams-changeassetidfrom": "a1"},
        {"vams-changesource": "fileRelocate", "vams-changeassetidfrom": "another-asset"},
    ], ids=["another-change", "another-asset"])
    def test_a_copy_the_step_did_not_write_makes_no_old_version_the_assets(self, svc, metadata):
        foreign = svc.client.put_object(Bucket=BUCKET, Key=FILE_KEY, Body=b"later")["VersionId"]
        svc.client.put_object(Bucket=BUCKET, Key=MOVED, Body=b"copy", Metadata=dict(
            metadata, **{"vams-changeassetfileversionfrom": foreign}))

        _delete_asset(svc)

        assert _version_ids(svc.client, FILE_KEY) == [foreign]

    def test_the_old_keys_are_read_a_page_at_a_time(self, svc):
        svc.faulty.delimited_page_size = 1

        _delete_asset(svc)

        for key in OLD_KEYS:
            assert _entries(svc.client, key) == [], key
        _lookalikes_and_neighbour_kept(svc.client)
        assert svc.faulty.delimited.count(FILE_KEY) > 1

    def test_a_refused_old_version_delete_keeps_the_asset_and_its_location_and_a_retry_finishes(self, svc):
        oldest = _version_ids(svc.client, FILE_KEY)[-1]
        copies = {key: _entries(svc.client, key) for key in (MOVED, MOVED_PREVIEW, COMPANION)}
        svc.faulty.deny_versions.add((FILE_KEY, oldest))

        with pytest.raises(svc.m.VAMSGeneralErrorResponse) as raised:
            _delete_asset(svc)

        assert str(raised.value).endswith(ASSET_MESSAGE)
        svc.m.asset_table.delete_item.assert_not_called()
        svc.m.write_asset_history_record.assert_not_called()
        assert {key: _entries(svc.client, key) for key in copies} == copies
        assert oldest in _version_ids(svc.client, FILE_KEY)

        svc.faulty.lift()
        response = _delete_asset(svc)

        assert response.success is True
        for key in OLD_KEYS:
            assert _entries(svc.client, key) == [], key
        assert _keys_under(svc.client, TARGET) == []

    def test_a_refused_head_of_a_copy_deletes_nothing(self, svc):
        before = {key: _entries(svc.client, key) for key in OLD_KEYS + (MOVED, MOVED_PREVIEW, COMPANION)}
        svc.faulty.deny_heads = True

        with pytest.raises(svc.m.VAMSGeneralErrorResponse) as raised:
            _delete_asset(svc)

        assert str(raised.value).endswith(ASSET_MESSAGE)
        assert {key: _entries(svc.client, key) for key in before} == before
        svc.m.asset_table.delete_item.assert_not_called()

    def test_a_pending_move_refuses_the_delete_and_changes_nothing(self, svc):
        svc.asset["assetLocationConversion"] = _record(state="pending")
        before = {key: _entries(svc.client, key) for key in OLD_KEYS + (MOVED, MOVED_PREVIEW, COMPANION)}

        with pytest.raises(svc.m.VAMSGeneralErrorResponse) as raised:
            _delete_asset(svc)

        assert str(raised.value).endswith(PENDING_MESSAGE)
        assert {key: _entries(svc.client, key) for key in before} == before
        svc.m.asset_table.delete_item.assert_not_called()
        svc.m.write_asset_history_record.assert_not_called()

    def test_a_record_that_does_not_match_the_location_deletes_nothing_outside_it(self, svc):
        svc.asset["assetLocationConversion"] = _record(target="proj/b/")

        _delete_asset(svc)

        assert len(_version_ids(svc.client, FILE_KEY)) == 2
        assert _keys_under(svc.client, TARGET) == []


@pytest.mark.unit
class TestOtherConversionsDeleteAsBefore:
    def test_an_in_place_conversion_deletes_its_folder_and_nothing_beside_it(self, svc):
        # In place the file stays where it is and its folder becomes the location
        svc.asset.update(assetLocation={"Key": "proj/a/"},
                         assetLocationConversion=_record(mode="in-place", target="proj/a/"))
        svc.client.put_object(Bucket=BUCKET, Key="proj/a-other/keep.txt", Body=b"k")

        _delete_asset(svc)

        assert _keys_under(svc.client, "proj/a/") == []
        assert _keys_under(svc.client, "proj/a-other/") == ["proj/a-other/keep.txt"]
        assert svc.faulty.delimited == []

    def test_an_append_slash_conversion_leaves_a_file_written_later_at_the_old_key(self, svc):
        # Nothing was stored at the key when the step appended the slash: a file there now is not the asset's
        svc.asset["assetLocationConversion"] = _record(mode="append-slash")

        _delete_asset(svc)

        assert len(_version_ids(svc.client, FILE_KEY)) == 2
        assert _keys_under(svc.client, TARGET) == []
        assert svc.faulty.delimited == []


@pytest.fixture
def files(s3, monkeypatch):
    """assetFiles' file route on the moved asset, with its record helpers observed."""
    _move(s3.client)
    asset = {"databaseId": "db1", "assetId": "a1", "bucketId": "b1", "assetLocation": {"Key": TARGET},
             "assetLocationConversion": _record()}
    table = AttributionAssetTable([asset_row("db1", "a1", "b1", TARGET, BUCKET)])
    monkeypatch.setattr(af, "asset_table", table)
    monkeypatch.setattr(af, "buckets_table", AttributionBucketsTable([registration("b1", BUCKET, "/")]))
    monkeypatch.setattr(af, "get_asset_with_permissions", lambda *args, **kwargs: asset)
    monkeypatch.setattr(af, "get_default_bucket_details",
                        lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": ""})
    for name in ("delete_file_metadata", "delete_files_metadata", "delete_file_version_records",
                 "send_subscription_email"):
        monkeypatch.setattr(af, name, MagicMock(name=name), raising=False)
    yield SimpleNamespace(client=s3.client, faulty=s3.faulty, asset=asset)


@pytest.mark.unit
class TestTheFileDeleteOfTheMovedFile:
    def test_the_moved_file_takes_its_old_versions_and_its_previews_old_versions(self, files):
        response = af.delete_file("db1", "a1", "/model.glb", False, True, CLAIMS)

        assert response.success is True
        for key in OLD_KEYS + (MOVED, MOVED_PREVIEW):
            assert _entries(files.client, key) == [], key
        assert len(_entries(files.client, COMPANION)) == 1
        _lookalikes_and_neighbour_kept(files.client)

    def test_another_file_leaves_the_old_versions(self, files):
        af.delete_file("db1", "a1", "/sub/c.obj", False, True, CLAIMS)

        assert len(_version_ids(files.client, FILE_KEY)) == 2
        assert len(_version_ids(files.client, PREVIEW)) == 1
        assert _entries(files.client, COMPANION) == []

    def test_a_refused_old_version_delete_keeps_the_file_and_a_retry_finishes(self, files):
        moved = _entries(files.client, MOVED)
        files.faulty.deny_keys.add(PREVIEW)

        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", "a1", "/model.glb", False, True, CLAIMS)

        assert str(raised.value).endswith(FILE_MESSAGE)
        assert _entries(files.client, MOVED) == moved
        assert len(_entries(files.client, MOVED_PREVIEW)) == 1
        af.delete_file_metadata.assert_not_called()

        files.faulty.lift()
        af.delete_file("db1", "a1", "/model.glb", False, True, CLAIMS)

        for key in OLD_KEYS + (MOVED, MOVED_PREVIEW):
            assert _entries(files.client, key) == [], key

    def test_a_pending_move_keeps_the_moved_file(self, files):
        files.asset["assetLocationConversion"] = _record(state="pending")
        before = {key: _entries(files.client, key) for key in OLD_KEYS + (MOVED, MOVED_PREVIEW)}

        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            af.delete_file("db1", "a1", "/model.glb", False, True, CLAIMS)

        assert str(raised.value).endswith(PENDING_MESSAGE)
        assert {key: _entries(files.client, key) for key in before} == before

    def test_a_pending_move_still_deletes_a_companion_file(self, files):
        files.asset["assetLocationConversion"] = _record(state="pending")

        af.delete_file("db1", "a1", "/sub/c.obj", False, True, CLAIMS)

        assert _entries(files.client, COMPANION) == []
        assert len(_version_ids(files.client, FILE_KEY)) == 2
