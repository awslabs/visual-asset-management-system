# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Two live assets on one stored location: a destructive operation of either leaves every shared key alone.

Archiving or permanently deleting one asset, and deleting, archiving or moving one of its files, must not
archive or delete the files the other asset's location holds too, so the other asset keeps its files and
every version of them. The lookup is the nested-location one (`fileAttribution.nested_locations_of_asset`,
destructive), where an equal location of another live asset counts like a nested one. An archived asset at
the same location does not count (the live asset owns the keys), and reads are unchanged.
"""

import os
import sys
from unittest.mock import MagicMock

import boto3
import pytest
from moto import mock_aws

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")

from backend.backend.handlers.assets import assetFiles  # noqa: F401,E402
from tests.common.fileAttributionFakes import (  # noqa: E402
    AttributionAssetTable, AttributionBucketsTable, asset_row, registration,
)
from tests.handlers.assets.test_assetService_history import _load as _load_service_for_delete  # noqa: E402
from tests.handlers.assets.test_assetService_permanent_delete_notification import _prepare  # noqa: E402
from tests.handlers.assets.test_assetService_single_file_location import _load as _load_service  # noqa: E402

al = sys.modules['common.assetLocations']
fa = sys.modules['common.fileAttribution']

BUCKET = "vams-shared"
LOCATION = "p/shared/"
A = asset_row("db1", "a", "reg-1", LOCATION, BUCKET)
B = asset_row("db2", "b", "reg-1", LOCATION, BUCKET)
FILES = ("p/shared/x.txt", "p/shared/sub/y.txt")
CLAIMS = {"tokens": ["tester"], "roles": []}


def _versions(client):
    listed = client.list_object_versions(Bucket=BUCKET)
    return sorted((v["Key"], v["VersionId"]) for v in listed.get("Versions", [])), listed.get("DeleteMarkers", [])


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    al.clear_nested_locations_cache()
    fa.clear_registration_cache()
    monkeypatch.setattr(al, "index_ready", lambda name: True)
    yield
    al.clear_nested_locations_cache()


@pytest.fixture
def bucket():
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in FILES:
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v1")
            client.put_object(Bucket=BUCKET, Key=key, Body=b"v2")
        yield client


def _nested(rows, asset=A, destructive=True):
    return fa.nested_locations_of_asset(AttributionAssetTable(rows), AttributionBucketsTable(
        [registration("reg-1", BUCKET, "/")]), asset, BUCKET, destructive=destructive)


@pytest.mark.unit
class TestTheLookup:
    def test_a_destructive_lookup_names_the_other_live_asset_on_the_location(self):
        nested = _nested([A, B])
        assert [(e.database_id, e.asset_id) for e in nested.shared] == [("db2", "b")]
        assert nested.holder("p/shared/x.txt") == LOCATION and nested.is_shared_key("p/shared/x.txt")

    def test_reads_and_an_archived_co_owner_are_unchanged(self):
        assert not _nested([A, B], destructive=False)
        archived_b = asset_row("db2", "b", "reg-1", LOCATION, BUCKET, archived=True)
        assert not _nested([A, archived_b])

    def test_the_asset_itself_is_never_its_own_co_owner_archived_or_live(self):
        assert not _nested([A])
        archived_a = asset_row("db1", "a", "reg-1", LOCATION, BUCKET, archived=True)
        assert not _nested([archived_a, asset_row("db1", "a", "reg-1", LOCATION, BUCKET)], asset=archived_a)

    def test_before_the_index_the_registration_walk_finds_the_co_owner(self, monkeypatch):
        monkeypatch.setattr(al, "index_ready", lambda name: False)
        nested = _nested([A, B])
        assert nested.source == "registrationWalk" and len(nested.shared) == 1

    def test_the_auxiliary_data_of_a_same_database_co_owner_is_kept(self):
        same_db = _nested([A, asset_row("db1", "c", "reg-1", LOCATION, BUCKET)])
        assert same_db.keep_aux("db1", "db1/p/shared/x.txt/preview/t.png") is False
        other_db = _nested([A, B])
        assert other_db.keep_aux("db1", "db1/p/shared/x.txt/preview/t.png") is True


@pytest.mark.unit
class TestTheOtherAssetKeepsItsFiles:
    def test_a_permanent_delete_of_the_location_keeps_every_version(self, bucket, monkeypatch):
        monkeypatch.setattr(assetFiles, "s3_client", bucket)
        before = _versions(bucket)
        deleted = assetFiles.delete_s3_prefix_all_versions(BUCKET, LOCATION, location=LOCATION, nested=_nested([A, B]))
        assert deleted == []
        assert _versions(bucket) == before

    def test_an_archive_of_the_location_puts_no_delete_marker(self, bucket, monkeypatch):
        monkeypatch.setattr(assetFiles, "s3_client", bucket)
        archived = assetFiles.archive_s3_prefix(BUCKET, LOCATION, "db1", "a", location=LOCATION, nested=_nested([A, B]))
        assert archived == {}
        assert _versions(bucket)[1] == []

    def test_the_asset_archive_marks_no_shared_file(self, bucket, monkeypatch):
        svc = _load_service()
        monkeypatch.setattr(svc, "s3", bucket)
        monkeypatch.setattr(svc, "asset_file_version_history_table", MagicMock())
        monkeypatch.setattr(svc, "MAX_PARALLEL_S3_WORKERS", 1)
        svc.archive_multi_assetFiles({"Key": LOCATION}, BUCKET, "db1", "a", "u1", nested=_nested([A, B]))
        assert _versions(bucket)[1] == []
        # Control: without a co-owner every file of the location is archived
        svc.archive_multi_assetFiles({"Key": LOCATION}, BUCKET, "db1", "a", "u1", nested=_nested([A]))
        assert sorted(m["Key"] for m in _versions(bucket)[1]) == sorted(FILES)

    def test_the_asset_permanent_delete_passes_the_co_owner_to_every_delete(self, monkeypatch):
        m = _load_service_for_delete()
        archived_a = asset_row("db1", "a1", "reg-1", LOCATION, BUCKET, archived=True)
        _prepare(m, monkeypatch, {**archived_a, "status": "archived"})
        table, buckets = AttributionAssetTable([archived_a, B]), AttributionBucketsTable([])
        real = fa.nested_locations_of_asset
        monkeypatch.setattr(m, "nested_locations_of_asset",
                            lambda _t, _b, asset, bucket_name, *args, **kwargs: real(table, buckets, asset, BUCKET,
                                                                                    *args, **kwargs))
        request = MagicMock()
        request.confirmPermanentDelete = True
        m.delete_asset_permanent("db1#deleted", "a1", request, CLAIMS)
        nested = m.delete_s3_prefix_all_versions.call_args_list[0].kwargs["nested"]
        assert [e.asset_id for e in nested.shared] == ["b"]
        assert m.delete_assetAuxiliary_files.call_args.kwargs["nested"] is nested

    @pytest.mark.parametrize("operation", ["delete", "archive", "folderDelete", "folderArchive", "move"])
    def test_a_file_operation_on_a_shared_key_is_refused_and_changes_nothing(self, bucket, monkeypatch, operation):
        af = assetFiles
        asset = dict(A)
        monkeypatch.setattr(af, "asset_table", AttributionAssetTable([A, B]))
        monkeypatch.setattr(af, "buckets_table", AttributionBucketsTable([registration("reg-1", BUCKET, "/")]))
        monkeypatch.setattr(af, "s3_client", bucket)
        monkeypatch.setattr(af, "get_asset_with_permissions", lambda *args, **kwargs: asset)
        monkeypatch.setattr(af, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": "/"})
        before = _versions(bucket)
        calls = {
            "delete": lambda: af.delete_file("db1", "a", "/x.txt", False, True, CLAIMS),
            "archive": lambda: af.archive_file("db1", "a", "/x.txt", False, CLAIMS),
            "folderDelete": lambda: af.delete_file("db1", "a", "/sub/", True, True, CLAIMS),
            "folderArchive": lambda: af.archive_file("db1", "a", "/sub/", True, CLAIMS),
            "move": lambda: af.move_file("db1", "a", "/x.txt", "/moved.txt", CLAIMS),
        }
        with pytest.raises(af.VAMSGeneralErrorResponse) as raised:
            calls[operation]()
        assert str(raised.value).endswith(": " + al.SHARED_PATH_REFUSAL)
        assert "db2" not in str(raised.value) and " b " not in str(raised.value)
        assert _versions(bucket) == before
