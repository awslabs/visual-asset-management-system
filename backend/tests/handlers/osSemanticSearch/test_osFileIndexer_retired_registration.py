# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The file indexer splits a key only by the prefixes of registrations still configured (#1889).

A retired registration (it left the configuration) keeps its row for the assets that name it, but it no
longer decides which prefix a key of its bucket sits under. The fixture loads the real module by path.
"""

from unittest.mock import patch

import pytest

from backend.tests.handlers.osSemanticSearch.test_osFileIndexer_permanent_delete_bucket_scoping import (  # noqa: F401
    _BucketRegistry,
    fileIndexer,
)


@pytest.mark.unit
class TestRegisteredPrefixes:
    def test_a_retired_registration_does_not_split_keys(self, fileIndexer):  # noqa: F811 - the imported fixture
        registry = _BucketRegistry({}, by_bucket_name={"shared-bucket": [
            {"bucketName": "shared-bucket", "baseAssetsPrefix": "old/", "retired": True},
            {"bucketName": "shared-bucket", "baseAssetsPrefix": "new/"},
        ]})
        fileIndexer._registered_prefix_cache.clear()
        with patch.object(fileIndexer, "s3_asset_buckets_table", registry):
            assert fileIndexer.get_registered_bucket_prefixes("shared-bucket") == ["new/"]
            assert fileIndexer.resolve_registered_bucket_prefix("shared-bucket", "old/a1/x.glb", "/") != "old/"
