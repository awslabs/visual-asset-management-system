# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The zipArchive pipeline's archive-into-asset runs (mode 2, #1125) on the combined stack with #1123.

A zipArchive run streams its archive into the run's standard output files prefix (the runner's
``outputDir`` is the run's ``outputS3AssetFilesPath``), so the output step ingests it like any other
workflow output. An archive over the async threshold (1 GiB) must therefore be handed to the large
object copy engine, which has no time limit, rather than copied inside the ingestion function or
queued: ZIP64 archives past 4 GiB and tar.gz archives up to the 5 TiB object limit are the case this
engine exists for. An archive at the threshold keeps the synchronous path.
"""

import pytest

from backend.tests.handlers.assets import test_uploadFile_large_output_handoff as handoff

GIB = handoff.GIB
TIB = 1024 * GIB
# The runner names the archive <outputFileName or assetId-archive>.<ext> at the root of outputDir.
ARCHIVES = {
    "xasset1-archive.zip": 6 * GIB,          # ZIP64: a member or the archive past 4 GiB
    "bundle.tar.gz": 2 * TIB,                # a multi-terabyte tar.gz
    "scans.tar": 5 * TIB,                    # at the S3 single-object limit
    "small-archive.zip": GIB,                # at the threshold: synchronous
}


@pytest.fixture(autouse=True)
def _archive_sizes(monkeypatch):
    monkeypatch.setattr(handoff, "SIZES", {**handoff.SIZES, **ARCHIVES})


def _job_for(start, key):
    return next(c.args[3] for c in start.call_args_list if c.args[3]["relativeKey"] == key)


@pytest.mark.unit
class TestZipArchiveRunOutputsUseTheCopyEngine:
    @pytest.mark.parametrize("name", ["xasset1-archive.zip", "bundle.tar.gz", "scans.tar"])
    def test_a_large_archive_is_handed_to_a_copy_job(self, name):
        with handoff._env() as (uf, m):
            response = uf.complete_external_upload("up-1", handoff._request(name), {})
        result = handoff._result(response, name)
        assert result.success is True and result.largeFileAsynchronousHandling is True
        assert result.ingestionJobId.startswith(handoff.EXEC + "#")
        job = _job_for(m["start"], name)
        assert job["kind"] == "workflowOutput"
        assert job["source"]["size"] == ARCHIVES[name]
        assert job["source"]["key"] == handoff.TEMP_PREFIX + name
        assert job["dest"]["key"] == "asset-1/" + name
        assert job["deleteSourceOnSuccess"] is True
        # Never the queue (its consumer copies inside a Lambda function), never a copy here.
        m["queue"].assert_not_called()
        m["copy"].assert_not_called()

    def test_an_archive_at_the_threshold_keeps_the_synchronous_path(self):
        with handoff._env() as (uf, m):
            response = uf.complete_external_upload("up-1", handoff._request("small-archive.zip"), {})
        assert handoff._result(response, "small-archive.zip").ingestionJobId is None
        m["start"].assert_not_called()
        m["copy"].assert_called_once()

    def test_an_archive_beside_small_outputs_is_the_only_engine_job(self):
        with handoff._env() as (uf, m):
            uf.complete_external_upload(
                "up-1", handoff._request("small.glb", "xasset1-archive.zip"), {})
        assert [c.args[3]["relativeKey"] for c in m["start"].call_args_list] == [
            "xasset1-archive.zip"]
        assert [c.args[1] for c in m["copy"].call_args_list] == [handoff.TEMP_PREFIX + "small.glb"]
