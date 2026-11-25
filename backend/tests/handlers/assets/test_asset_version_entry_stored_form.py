# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset version stores each file's entry without a leading slash, however the caller wrote it (#1795).

`POST .../createVersion` accepts a file list whose `relativeKey`s may start with `/` (`/inner.txt`, the form
the file list shows) or not (`inner.txt`). The entry's `fileKey` used to keep the caller's form, which every
reader then had to match; it is now stored in one form, the one a version built from the asset's current
files has always stored. `/a.txt` and `a.txt` in one request name one file and are refused as duplicates,
a path made only of slashes names no file, and a revert of an older version that holds both forms of one
file writes one entry. The versions table is a moto table with the deployment's key schema.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from aws_lambda_powertools.utilities.parser import ValidationError
from moto import mock_aws

from backend.backend.handlers.assets import assetVersions as av  # noqa: E402

PK = "databaseId:assetId:assetVersionId"
DB = "db1"
ASSET = "a1"
PREFIX = f"{ASSET}/"
CLAIMS = {"tokens": ["alice"]}


def _versions_table():
    return boto3.resource("dynamodb", region_name="us-east-1").create_table(
        TableName="test-asset-file-versions-table",
        KeySchema=[{"AttributeName": PK, "KeyType": "HASH"}, {"AttributeName": "fileKey", "KeyType": "RANGE"}],
        AttributeDefinitions=[{"AttributeName": PK, "AttributeType": "S"},
                              {"AttributeName": "fileKey", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def table(monkeypatch):
    with mock_aws():
        created = _versions_table()
        monkeypatch.setattr(av, "asset_file_versions_table", created)
        yield created


def _stored(table):
    return sorted((item[PK], item["fileKey"], item.get("versionId")) for item in table.scan()["Items"])


def _entry(relative_key, version_id):
    return {"relativeKey": relative_key, "versionId": version_id, "size": 12,
            "lastModified": "2026-11-01T10:00:00+00:00", "etag": "e"}


@pytest.mark.unit
class TestSaveAssetFileVersions:
    def test_each_entry_is_stored_without_its_leading_slash(self, table):
        """Exactly one leading '/' is the path marker: '//c.txt' names '{location}/c.txt' and keeps its slashes."""
        assert av.save_asset_file_versions(DB, ASSET, "3", [
            _entry("/inner.txt", "v-1"), _entry("sub/b.txt", "v-2"), _entry("//c.txt", "v-3")]) is True
        assert _stored(table) == [("db1:a1:3", "//c.txt", "v-3"), ("db1:a1:3", "inner.txt", "v-1"),
                                  ("db1:a1:3", "sub/b.txt", "v-2")]

    def test_two_forms_of_one_file_are_one_entry(self, table):
        """A revert copies an older version's entries, which can hold both forms of one file."""
        assert av.save_asset_file_versions(DB, ASSET, "3", [
            _entry("/inner.txt", "v-1"), _entry("inner.txt", "v-2")]) is True
        assert _stored(table) == [("db1:a1:3", "inner.txt", "v-2")]

    def test_an_entry_that_names_no_file_is_left_out(self, table):
        assert av.save_asset_file_versions(DB, ASSET, "3", [_entry("/", "v-1"), _entry("a.txt", "v-2")]) is True
        assert _stored(table) == [("db1:a1:3", "a.txt", "v-2")]


@pytest.mark.unit
class TestTheCreateVersionRequest:
    def _model(self, files):
        return av.CreateAssetVersionRequestModel(comment="snapshot", files=files)

    def test_both_forms_are_accepted(self):
        model = self._model([{"relativeKey": "/inner.txt", "versionId": "v-1"},
                             {"relativeKey": "sub/b.txt", "versionId": "v-2"}])
        assert [f.relativeKey for f in model.files] == ["/inner.txt", "sub/b.txt"]

    def test_two_forms_of_one_file_are_refused_as_duplicates(self):
        with pytest.raises(ValidationError, match="Duplicate relative keys are not allowed"):
            self._model([{"relativeKey": "/inner.txt", "versionId": "v-1"},
                         {"relativeKey": "inner.txt", "versionId": "v-2"}])

    @pytest.mark.parametrize("relative_key", ["/", "//"])
    def test_a_path_made_only_of_slashes_is_refused(self, relative_key):
        with pytest.raises(ValidationError, match="relativeKey must name a file"):
            self._model([{"relativeKey": relative_key, "versionId": "v-1"}])


@pytest.mark.unit
class TestCreateAssetVersion:
    def test_a_file_list_with_slashes_is_stored_without_them(self, table):
        s3 = MagicMock()
        s3.head_object.return_value = {"ContentLength": 12, "ETag": '"e"',
                                       "LastModified": datetime(2026, 11, 1, tzinfo=timezone.utc)}
        request = av.CreateAssetVersionRequestModel(comment="snapshot", files=[
            {"relativeKey": "/inner.txt", "versionId": "v-1"}, {"relativeKey": "/sub/b.txt", "versionId": "v-2"}])
        with patch.object(av, "get_asset_with_permissions", return_value={"assetId": ASSET}), \
                patch.object(av, "get_asset_s3_location", return_value=("bucket", PREFIX)), \
                patch.object(av, "nested_locations_for", return_value=MagicMock(keep=lambda key: True)), \
                patch.object(av, "validate_s3_files_exist", return_value=[]), \
                patch.object(av, "s3_client", s3), \
                patch.object(av, "reserve_next_asset_version", return_value="2"), \
                patch.object(av, "save_asset_metadata_version", return_value=True), \
                patch.object(av, "stamp_asset_version_provenance"), \
                patch.object(av, "finalize_asset_version"), \
                patch.object(av, "send_subscription_email"):
            response = av.create_asset_version(DB, ASSET, request, CLAIMS)
        assert response.success is True and response.skippedFiles is None
        assert _stored(table) == [("db1:a1:2", "inner.txt", "v-1"), ("db1:a1:2", "sub/b.txt", "v-2")]
        assert {c.kwargs["Key"] for c in s3.head_object.call_args_list} == {"a1/inner.txt", "a1/sub/b.txt"}
