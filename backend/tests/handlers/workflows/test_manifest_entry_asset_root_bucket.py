# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Manifest ``inputFiles[]`` entries carry ``assetRootBucket``, the bucket the ``assetRootS3Key`` is in.

``bucket`` + ``key`` locate the object a pipeline reads, and ``assetRootS3Key`` names the file's asset
root. For a file read from its asset the two share one bucket. From the second step of a workflow on, an
input an earlier step's output shadows is read from the run's output bucket while its asset root stays
the original asset's, so ``bucket`` + ``assetRootS3Key`` would name a folder that does not exist. The
entry therefore carries the asset root's own bucket beside it, and a pipeline that joins a bucket with
the asset root (GR00T, Isaac Lab, Cosmos Transfer's control input) reads ``assetRootBucket``.

The field is additive: the manifest schema version is unchanged, and an entry without an asset root (a
new file an earlier step wrote) carries ``""`` for both."""

import pytest

from backend.backend.common.workflows import executionOutputs as eo
from backend.backend.common.workflows import executionRecords as er


def _original(**overrides):
    entry = er.build_manifest_entry(
        "/models/pump.glb", "asset-bucket", "projects/site-a/models/pump.glb", "v1",
        database_id="db1", asset_id="xidA", asset_root_s3_key="projects/site-a/",
        aux_preview_prefix="db1/projects/site-a/models/pump.glb/preview", bucket_id="bkt-01")
    entry.update(overrides)
    return entry


@pytest.mark.unit
class TestBuildManifestEntry:
    def test_an_entry_read_from_its_asset_pairs_the_root_with_its_own_bucket(self):
        assert _original()["assetRootBucket"] == "asset-bucket"

    def test_an_explicit_asset_root_bucket_wins(self):
        entry = er.build_manifest_entry("/a.glb", "run-bucket", "out/files/a.glb",
                                        asset_root_s3_key="a1/", asset_root_bucket="asset-bucket")
        assert (entry["bucket"], entry["assetRootBucket"]) == ("run-bucket", "asset-bucket")

    def test_an_entry_without_an_asset_root_carries_no_root_bucket(self):
        entry = er.build_manifest_entry("/new.glb", "run-bucket", "out/files/new.glb")
        assert entry["assetRootS3Key"] == "" and entry["assetRootBucket"] == ""
        # A bucket given without a root is still not a root bucket.
        assert er.build_manifest_entry("/n.glb", "run-bucket", "k", asset_root_bucket="x")["assetRootBucket"] == ""

    def test_the_key_is_always_present(self):
        assert "assetRootBucket" in er.build_manifest_entry("/a.glb", "bkt", "x/a.glb")


@pytest.mark.unit
class TestShadowedEntriesKeepTheAssetBucket:
    """resolve_manifest_input_files: an output at an input's path shadows it."""

    def _resolve(self, originals, outputs):
        return eo.resolve_manifest_input_files(None, originals, "run-bucket", "out/files/",
                                               current_output_files=outputs)

    def test_a_shadowed_entry_reads_the_run_bucket_but_keeps_the_asset_bucket_for_its_root(self):
        outputs = [{"key": "out/files/models/pump.glb", "relativePath": "/models/pump.glb",
                    "versionId": "v9", "fileSize": 1, "contentType": ""}]
        resolved = self._resolve([_original()], outputs)
        assert len(resolved) == 1
        entry = resolved[0]
        assert (entry["bucket"], entry["key"]) == ("run-bucket", "out/files/models/pump.glb")
        assert (entry["assetRootBucket"], entry["assetRootS3Key"]) == ("asset-bucket", "projects/site-a/")

    def test_a_rebuilt_original_without_the_field_uses_its_own_bucket(self):
        # interimPipelineTracking rebuilds the originals from the stored input rows, which carry the
        # asset bucket as `bucket` and no assetRootBucket.
        original = _original()
        original.pop("assetRootBucket")
        outputs = [{"key": "out/files/models/pump.glb", "relativePath": "/models/pump.glb",
                    "versionId": "v9", "fileSize": 1, "contentType": ""}]
        assert self._resolve([original], outputs)[0]["assetRootBucket"] == "asset-bucket"
        assert self._resolve([original], [])[0]["assetRootBucket"] == "asset-bucket"

    def test_an_unshadowed_original_keeps_its_bucket_and_root(self):
        entry = self._resolve([_original()], [])[0]
        assert (entry["bucket"], entry["assetRootBucket"]) == ("asset-bucket", "asset-bucket")

    def test_a_new_output_file_has_no_asset_root(self):
        outputs = [{"key": "out/files/extra/new.glb", "relativePath": "/extra/new.glb",
                    "versionId": "v1", "fileSize": 1, "contentType": ""}]
        resolved = self._resolve([_original()], outputs)
        new = [e for e in resolved if e["relativePath"] == "/extra/new.glb"][0]
        assert (new["bucket"], new["assetRootS3Key"], new["assetRootBucket"]) == ("run-bucket", "", "")

    def test_two_assets_in_two_buckets_each_keep_their_own_root_bucket(self):
        a = _original()
        b = er.build_manifest_entry(
            "/models/pump.glb", "other-bucket", "xidB/models/pump.glb", "v1",
            database_id="db2", asset_id="xidB", asset_root_s3_key="xidB/")
        outputs = [{"key": "out/files/models/pump.glb", "relativePath": "/models/pump.glb",
                    "versionId": "v9", "fileSize": 1, "contentType": ""}]
        resolved = self._resolve([a, b], outputs)
        assert sorted((e["assetId"], e["assetRootBucket"]) for e in resolved) == [
            ("xidA", "asset-bucket"), ("xidB", "other-bucket")]
