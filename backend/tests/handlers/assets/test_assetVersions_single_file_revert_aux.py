# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Reverting a single-file asset to a version deletes only the reverted files' own viewer data (D6).

A single-file asset at ``p/inner.txt`` keeps the object's auxiliary data at ``db1/p/inner.txt/preview/...``
and its companion entries' data at ``db1/p/inner.txt/{entry}/preview/...``; a companion folder named
``preview`` puts an entry's data inside the object's ``preview/`` folder
(``db1/p/inner.txt/preview/cloud.ply/preview/...``). An asset version that holds only the object, reverted
to, must delete the object's own data and leave every companion entry's viewer data.
"""

from unittest.mock import MagicMock, patch

import pytest

from tests.handlers.assets.test_aux_delete_all_versions import (  # noqa: F401
    AUX_BUCKET, _entries, _put, assetVersions, aux_s3,
)

LOCATION = "p/inner.txt"
OBJECT_DATA = "db1/p/inner.txt/preview/PotreeViewer/metadata.json"
COMPANION_DATA = "db1/p/inner.txt/pc/cloud.ply/preview/PotreeViewer/metadata.json"
PREVIEW_FOLDER_COMPANION_DATA = "db1/p/inner.txt/preview/cloud.ply/preview/PotreeViewer/metadata.json"


def _revert(files, location=LOCATION):
    request = MagicMock()
    request.assetVersionId = "1"
    request.revertMetadata = False
    request.comment = ""
    av = assetVersions
    with patch.object(av, "get_asset_with_permissions", return_value={"databaseId": "db1", "assetId": "a1"}), \
            patch.object(av, "get_asset_s3_location", return_value=("asset-bucket", location)), \
            patch.object(av, "get_asset_version_metadata", return_value={"assetVersionId": "1"}), \
            patch.object(av, "get_asset_file_versions", return_value={"files": files}), \
            patch.object(av, "reserve_next_asset_version", return_value="3"), \
            patch.object(av, "does_file_version_exist", return_value=True), \
            patch.object(av, "copy_s3_object_version", return_value="s3v-new"), \
            patch.object(av, "save_asset_file_versions", return_value=True), \
            patch.object(av, "save_asset_metadata_version", return_value=True), \
            patch.object(av, "stamp_asset_version_provenance", MagicMock()), \
            patch.object(av, "finalize_asset_version", MagicMock()), \
            patch.object(av, "send_subscription_email", MagicMock()), \
            patch.object(av, "MAX_PARALLEL_S3_WORKERS", 1):
        return av.revert_asset_version("db1", "a1", request, {"tokens": ["tester"], "roles": []})


def _file(relative_key):
    return {"relativeKey": relative_key, "versionId": "s3v1", "size": 1, "etag": "e"}


def _left(client):
    return {key for key, _ in _entries(client, "db1/")}


@pytest.mark.unit
class TestAssetVersionRevertOfASingleFileAsset:
    def test_a_revert_deletes_only_the_reverted_files_own_viewer_data(self, aux_s3):
        for key in (OBJECT_DATA, COMPANION_DATA, PREVIEW_FOLDER_COMPANION_DATA):
            _put(aux_s3, key)
        with patch.object(assetVersions, "s3_client", aux_s3), \
                patch.object(assetVersions, "asset_aux_bucket_name", AUX_BUCKET):
            # A version holding only the object: the companion entries keep their viewer data
            assert _revert([_file("/inner.txt")]).success
            assert _left(aux_s3) == {COMPANION_DATA, PREVIEW_FOLDER_COMPANION_DATA}

            # A version holding the companion entry in the folder named preview: only its data goes
            _put(aux_s3, OBJECT_DATA)
            _revert([_file("/preview/cloud.ply")])
            assert _left(aux_s3) == {OBJECT_DATA, COMPANION_DATA}

            # Control: a folder asset deletes the reverted file's data and nothing else
            _put(aux_s3, "db1/proj/a/x.txt/preview/PotreeViewer/metadata.json")
            _put(aux_s3, "db1/proj/a/y.txt/preview/PotreeViewer/metadata.json")
            _revert([_file("/x.txt")], location="proj/a/")
            assert "db1/proj/a/x.txt/preview/PotreeViewer/metadata.json" not in _left(aux_s3)
            assert "db1/proj/a/y.txt/preview/PotreeViewer/metadata.json" in _left(aux_s3)
