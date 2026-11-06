# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The auxiliary preview stream route cannot reach Download as Zip jobs (#1125).

Zip archives and their manifests live under the top-level auxiliary-bucket prefix
``vams.preparedDownloads/``. The stream route resolves every key under ``{databaseId}/{assetLocationKey}``
with a validated database id, which has no dot, so no request can name a key under that prefix,
including one for a database named ``preparedDownloads``.
"""

import pytest

from backend.backend.common.s3PathPatterns import AUXILIARY_PREPARED_DOWNLOADS_PREFIX
from backend.backend.common.validators import validate
from backend.tests.handlers.assets.test_streamAuxiliaryPreviewAsset_range_header_casing import _load

PREFIX = AUXILIARY_PREPARED_DOWNLOADS_PREFIX


def _valid(database_id, path):
    return validate({
        "databaseId": {"value": database_id, "validator": "ID"},
        "auxiliaryPreviewAssetPathKey": {"value": path, "validator": "ASSET_AUXILIARYPREVIEW_PATH"},
    })[0]


@pytest.mark.unit
class TestStreamRouteNeverReachesPreparedDownloads:
    @pytest.mark.parametrize("database_id", ["vams.preparedDownloads", "vams.prepareddownloads", "vams."])
    def test_the_prefix_segment_is_not_a_database_id(self, database_id):
        assert _valid(database_id, "scan.e57/preview/r/octree.bin") is False

    @pytest.mark.parametrize("database_id", ["preparedDownloads", "db1"])
    @pytest.mark.parametrize("path", [
        "scan.e57/preview/r/octree.bin",
        "/vams.preparedDownloads/db1/xa1/h/r1/out.zip",
        "vams.preparedDownloads/db1/xa1/h/r1/out.zip",
    ])
    def test_every_resolved_key_is_under_the_database(self, database_id, path):
        module = _load()
        key = module.resolve_asset_file_path(f"{database_id}/xa1/", path)
        assert key.startswith(f"{database_id}/xa1/")
        assert not key.startswith(PREFIX)

    def test_a_dot_dot_path_is_refused(self):
        assert _valid("db1", "../../vams.preparedDownloads/db1/xa1/h/r1/out.zip") is False
