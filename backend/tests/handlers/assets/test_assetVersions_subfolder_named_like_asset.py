# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An asset version keeps the metadata and attributes of the files it holds, by each file's own path.

A version entry is the file's path in the asset. In an asset ``A1`` stored at ``A1/`` with a root subfolder
named like the asset, the entry ``A1/cloud.las`` is the subfolder file, whose records are keyed
``/A1/cloud.las``. The version snapshot cut a leading ``{assetId}/`` off the entry and keyed it ``/cloud.las``,
the root file. So a version holding only the subfolder file snapshotted the root file's metadata and attributes
and none of its own, and a revert (which deletes every current row of the asset and restores only the snapshot
rows of the versioned paths) lost the subfolder file's records and reset the root file's.
"""

from unittest.mock import MagicMock, patch

import pytest

# Module-scope import so the real handlers package is in sys.modules before the root conftest's autouse
# fixture installs its placeholder (see test_assetVersions_legacy_metadata_snapshot.py).
from backend.backend.handlers.assets import assetVersions as av

DB = "db1"
ASSET = "A1"
METADATA_TABLE = "meta-table"
ATTRIBUTE_TABLE = "attr-table"
VERSIONS_TABLE = "meta-versions-table"


def _typed(row):
    return {k: {"S": v} for k, v in row.items()}


def _current_rows(table_name):
    """The asset's current metadata or attribute rows: the subfolder file's and the root file's."""
    kind = "metadata" if table_name == METADATA_TABLE else "attribute"
    rows = []
    for path, value in (("/A1/cloud.las", "subfolder-file"), ("/cloud.las", "root-file")):
        rows.append(_typed({
            "databaseId:assetId:filePath": f"{DB}:{ASSET}:{path}",
            "databaseId:assetId": f"{DB}:{ASSET}",
            f"{kind}Key": "k",
            f"{kind}Value": value,
            f"{kind}ValueType": "string",
        }))
    return rows


class _Client:
    """A low-level DynamoDB client stub: each table's query pages return its rows; writes are recorded."""

    def __init__(self):
        self.puts = {}
        self.deletes = {}

    def get_paginator(self, _name):
        client = self

        class _Pages:
            def paginate(self, **kwargs):
                result = MagicMock()
                result.build_full_result.return_value = {"Items": _current_rows(kwargs["TableName"])}
                result.__iter__ = lambda _self: iter([{"Items": _current_rows(kwargs["TableName"])}])
                return result

        return _Pages()

    def batch_write_item(self, RequestItems):
        for table, requests in RequestItems.items():
            for request in requests:
                if "PutRequest" in request:
                    self.puts.setdefault(table, []).append(request["PutRequest"]["Item"])
                if "DeleteRequest" in request:
                    self.deletes.setdefault(table, []).append(request["DeleteRequest"]["Key"])
        return {"UnprocessedItems": {}}


def _patches(client):
    return (
        patch.object(av, "dynamodb_client", client),
        patch.object(av, "asset_file_metadata_table", MagicMock()),
        patch.object(av, "file_attribute_table", MagicMock()),
        patch.object(av, "asset_file_metadata_versions_table", MagicMock()),
        patch.object(av, "asset_file_metadata_table_name", METADATA_TABLE),
        patch.object(av, "file_attribute_table_name", ATTRIBUTE_TABLE),
        patch.object(av, "asset_file_metadata_versions_table_name", VERSIONS_TABLE),
    )


@pytest.mark.unit
class TestAVersionOfTheSubfolderFileKeepsItsOwnRecords:
    def test_the_snapshot_holds_the_subfolder_files_rows_and_not_the_root_files(self):
        client = _Client()
        p = _patches(client)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6]:
            assert av.save_asset_metadata_version(DB, ASSET, "2", [{"relativeKey": "A1/cloud.las"}]) is True
        snapshot = client.puts.get(VERSIONS_TABLE, [])
        saved = sorted((item["type:filePath:metadataKey"]["S"], item["metadataValue"]["S"]) for item in snapshot)
        assert saved == [("attribute:/A1/cloud.las:k", "subfolder-file"),
                         ("metadata:/A1/cloud.las:k", "subfolder-file")]

    def test_a_version_of_the_root_file_keeps_the_root_files_rows(self):
        """Control: the root file's entry 'cloud.las' still keys '/cloud.las'."""
        client = _Client()
        p = _patches(client)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6]:
            assert av.save_asset_metadata_version(DB, ASSET, "2", [{"relativeKey": "cloud.las"}]) is True
        saved = sorted(item["type:filePath:metadataKey"]["S"] for item in client.puts.get(VERSIONS_TABLE, []))
        assert saved == ["attribute:/cloud.las:k", "metadata:/cloud.las:k"]

    def test_a_revert_restores_the_subfolder_files_rows(self):
        from models.assetsV3 import AssetVersionMetadataItemModel

        snapshot = [
            AssetVersionMetadataItemModel(type=kind, filePath=path, metadataKey="k", metadataValue=value,
                                          metadataValueType="string")
            for kind in ("metadata", "attribute")
            for path, value in (("/A1/cloud.las", "subfolder-v1"), ("/cloud.las", "root-v1"))
        ]
        client = _Client()
        p = _patches(client)
        with p[0], p[1], p[2], p[3], p[4], p[5], p[6], \
                patch.object(av, "get_asset_metadata_version", return_value=snapshot):
            assert av.revert_asset_metadata_version(DB, ASSET, "1", "3", [{"relativeKey": "A1/cloud.las"}]) is True
        restored = sorted(
            (table, item["databaseId:assetId:filePath"]["S"])
            for table, items in client.puts.items() for item in items)
        assert restored == [(ATTRIBUTE_TABLE, f"{DB}:{ASSET}:/A1/cloud.las"),
                            (METADATA_TABLE, f"{DB}:{ASSET}:/A1/cloud.las")]
