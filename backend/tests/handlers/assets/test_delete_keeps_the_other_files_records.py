# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Deleting one of two files named alike leaves the other file's version and metadata rows (#1977).

In an asset stored at ``a1/``, ``a1/x.txt`` is listed at ``/x.txt`` (version rows ``x.txt``, a legacy ``/x.txt``)
and ``a1//x.txt`` at ``//x.txt`` (version rows ``//x.txt``). The version-row cleanup of a permanent delete and the
metadata cleanup of a folder delete compared paths with every leading ``/`` removed, so deleting either file
also deleted the other's rows. They now compare by the one-slash rule, and the delete route passes each
deleted file's listed path instead of the stripped path or the form the request named the file by.
"""

import boto3
import pytest
from unittest.mock import MagicMock, patch

from backend.backend.handlers.assets import assetFiles as af  # noqa: E402
from tests.handlers.assets.test_asset_version_entry_keys import (  # noqa: E402,F401
    ASSET, DB, _versions_table, aws,
)

ROWS = [(DB, ASSET, "1", "x.txt", "v-plain"), (DB, ASSET, "2", "/x.txt", "v-legacy"),
        (DB, ASSET, "1", "//x.txt", "v-empty-segment"), (DB, ASSET, "1", "y.txt", "v-other")]


def _remaining(table):
    return sorted((item["fileKey"], item["versionId"]) for item in table.scan()["Items"])


@pytest.mark.unit
class TestTheVersionRows:
    @pytest.mark.parametrize("path", ["//x.txt"])
    def test_deleting_the_empty_segment_file_keeps_the_root_files_rows(self, aws, monkeypatch, path):
        table = _versions_table(ROWS)
        monkeypatch.setattr(af, "asset_version_files_table", table)
        assert af.delete_file_version_records(DB, ASSET, [path]) == 1
        assert _remaining(table) == [("/x.txt", "v-legacy"), ("x.txt", "v-plain"), ("y.txt", "v-other")]

    @pytest.mark.parametrize("path", ["/x.txt", "x.txt"])
    def test_deleting_the_root_file_keeps_the_empty_segment_files_rows(self, aws, monkeypatch, path):
        table = _versions_table(ROWS)
        monkeypatch.setattr(af, "asset_version_files_table", table)
        assert af.delete_file_version_records(DB, ASSET, [path]) == 2
        assert _remaining(table) == [("//x.txt", "v-empty-segment"), ("y.txt", "v-other")]

    @pytest.mark.parametrize("path", ["/", "//", "/sub/"])
    def test_a_folder_or_slash_only_path_deletes_nothing(self, aws, monkeypatch, path):
        table = _versions_table(ROWS)
        monkeypatch.setattr(af, "asset_version_files_table", table)
        assert af.delete_file_version_records(DB, ASSET, [path]) == 0
        assert len(_remaining(table)) == len(ROWS)


@pytest.mark.unit
class TestTheMetadataRows:
    @pytest.mark.parametrize("paths, expected", [
        (["//x.txt"], {f"{DB}:{ASSET}://x.txt"}),
        (["/x.txt", "x.txt"], {f"{DB}:{ASSET}:/x.txt"}),
        (["/", "//", "/sub/", ""], set()),
    ])
    def test_each_path_names_its_own_rows(self, monkeypatch, paths, expected):
        seen = []

        def _rows(table, database_id, asset_id, composite_keys):
            seen.append(set(composite_keys))
            return []

        monkeypatch.setattr(af, "_file_record_rows", _rows)
        monkeypatch.setattr(af, "asset_file_metadata_table", MagicMock())
        monkeypatch.setattr(af, "file_attribute_table", None)
        af.delete_files_metadata(DB, ASSET, paths)
        assert (seen[0] if seen else set()) == expected


@pytest.fixture
def route(monkeypatch):
    from moto import mock_aws
    BUCKET = "vams-test-assets"
    with mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=BUCKET)
        client.put_bucket_versioning(Bucket=BUCKET, VersioningConfiguration={"Status": "Enabled"})
        for key in ("A1/x.txt", "A1//x.txt", "A1//sub/y.txt"):
            client.put_object(Bucket=BUCKET, Key=key, Body=b"content")
        asset = {"databaseId": DB, "assetId": "A1", "bucketId": "b1", "assetLocation": {"Key": "A1/"}}
        from tests.common.fileAttributionFakes import AttributionAssetTable, AttributionBucketsTable, registration
        monkeypatch.setattr(af, "asset_table", AttributionAssetTable([asset]))
        monkeypatch.setattr(af, "buckets_table", AttributionBucketsTable([registration("b1", BUCKET, "")]))
        monkeypatch.setattr(af, "s3_client", client)
        monkeypatch.setattr(af, "s3_resource", boto3.resource("s3", region_name="us-east-1"))
        monkeypatch.setattr(af, "get_asset_with_permissions", lambda *args, **kwargs: asset)
        monkeypatch.setattr(af, "get_default_bucket_details",
                            lambda bucket_id: {"bucketId": bucket_id, "bucketName": BUCKET, "baseAssetsPrefix": ""})
        monkeypatch.setattr(af, "asset_file_version_history_table", None)
        monkeypatch.setattr(af, "MAX_PARALLEL_S3_WORKERS", 1)
        for name in ("send_subscription_email", "delete_assetAuxiliary_files", "delete_auxiliary_files_of_keys",
                     "delete_file_metadata"):
            monkeypatch.setattr(af, name, lambda *args, **kwargs: None)
        calls = {"versions": [], "metadata": []}
        monkeypatch.setattr(af, "delete_file_version_records",
                            lambda db, a, paths: calls["versions"].append(sorted(paths)))
        monkeypatch.setattr(af, "delete_files_metadata",
                            lambda db, a, paths: calls["metadata"].append(sorted(paths)))
        yield calls


@pytest.mark.unit
class TestTheDeleteRoute:
    """The route hands the cleanups each deleted file's listed path."""

    @pytest.mark.parametrize("path", ["//x.txt", "A1//x.txt"])
    def test_a_file_delete_names_the_file_by_its_listed_path(self, route, path):
        af.delete_file(DB, "A1", path, False, True, {"tokens": ["alice"], "roles": []})
        assert route["versions"] == [["//x.txt"]]

    def test_a_folder_delete_names_each_file_by_its_listed_path(self, route):
        af.delete_file(DB, "A1", "//", True, True, {"tokens": ["alice"], "roles": []})
        assert route["metadata"] == [["//sub/y.txt", "//x.txt"]]
        assert route["versions"] == [["//sub/y.txt", "//x.txt"]]


@pytest.mark.unit
class TestTheArchiveHistoryPath:
    """#1977: an archive records the delete marker's history under the archived file's listed path."""

    @pytest.fixture
    def archive(self, route, monkeypatch):
        written = []
        monkeypatch.setattr(af, "write_archive_history",
                            lambda db, a, path, version_id, user: written.append(path))
        yield written

    @pytest.mark.parametrize("path", ["//x.txt", "A1//x.txt"])
    def test_a_file_archive_records_the_listed_path(self, archive, path):
        af.archive_file(DB, "A1", path, False, {"tokens": ["alice"], "roles": []})
        assert archive == ["//x.txt"]

    def test_a_folder_archive_records_each_listed_path(self, archive):
        af.archive_file(DB, "A1", "//", True, {"tokens": ["alice"], "roles": []})
        assert sorted(archive) == ["//sub/y.txt", "//x.txt"]
