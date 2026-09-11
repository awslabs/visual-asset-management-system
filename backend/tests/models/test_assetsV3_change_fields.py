# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import pytest
from models.assetsV3 import AssetFileItemModel, FileVersionModel, FileInfoResponseModel


@pytest.mark.unit
def test_asset_file_item_has_change_fields():
    m = AssetFileItemModel(
        fileName="x.glb", key="a1/x.glb", relativePath="x.glb",
        isFolder=False, dateCreatedCurrentVersion="2026-06-09T00:00:00Z",
        changeSource="upload", changeUserId="alice",
    )
    assert m.changeSource == "upload"
    assert m.changeUserId == "alice"


@pytest.mark.unit
def test_file_version_has_all_provenance_fields():
    v = FileVersionModel(
        versionId="v1", lastModified="2026-06-09T00:00:00Z", size=10, isLatest=True,
        changeSource="fileCopy", changeUserId="bob",
        changeWorkflowId="", changeWorkflowExecutionId="",
        changeAssetIdFrom="a2", changeDatabaseIdFrom="db1", changeAssetFilePathFrom="old/x.glb",
        changeAssetFileVersionFrom="srcver-1",
    )
    assert v.changeAssetIdFrom == "a2"
    assert v.changeSource == "fileCopy"
    assert v.changeAssetFileVersionFrom == "srcver-1"


@pytest.mark.unit
def test_change_fields_default_none():
    v = FileVersionModel(versionId="v1", lastModified="t", size=1, isLatest=False)
    assert v.changeSource is None
    assert v.changeUserId is None
    assert v.changeWorkflowId is None

    f = AssetFileItemModel(
        fileName="test.txt", key="a/test.txt", relativePath="test.txt",
        isFolder=False, dateCreatedCurrentVersion="2026-06-09T00:00:00Z"
    )
    assert f.changeSource is None
    assert f.changeUserId is None

    r = FileInfoResponseModel(
        fileName="test.txt", key="a/test.txt", relativePath="test.txt",
        isFolder=False, lastModified="2026-06-09T00:00:00Z"
    )
    assert r.changeSource is None
    assert r.changeUserId is None
