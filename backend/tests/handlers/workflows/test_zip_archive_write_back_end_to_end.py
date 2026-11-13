# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download as Zip mode 2 (archive into the asset), end to end through the output step's write-back
for the GLOBAL zipArchive system workflow (#1125; the #1123 D2 lesson).

A zipArchive workflow run writes its archive at the root of the run's outputs files prefix, and the
output step ingests it into the asset as a new file version. The zipArchive workflow is registered
under the GLOBAL keyword (GLOBAL/utility-zip-archive), which is exactly the case #1123 D2 broke: the
file-ingestion model refused workflowDatabaseId GLOBAL, so every GLOBAL workflow's write-back failed.

This reuses P2b's end-to-end harness (test_processOutput_write_back_end_to_end.py): the output step's
REAL process_external_upload builds the cross-call and the REAL uploadFile handler parses, authorizes
and completes it. Here the run is the zipArchive workflow's and the outputs are its archives: a small
zip, a ZIP64 zip over 5 GiB, a 2 TiB tar.gz and a 5 TiB tar (every one over 1 GiB becomes a copy job
of this run's ingestion batch), and the empty output of a mode 1 run (a zip download job writes its
archive to the auxiliary bucket, never to the run's outputs)."""

import json
from unittest.mock import patch

import pytest

from backend.tests.handlers.workflows import test_processOutput_write_back_end_to_end as wb

GIB = wb.GIB
TIB = 1024 * GIB
WORKFLOW_DATABASE_ID = "GLOBAL"
WORKFLOW_ID = "utility-zip-archive"

ARCHIVES = {
    "small-zip": [{"Key": wb.FILES_PREFIX + "asset1-archive.zip", "Size": 4096}],
    "zip64-over-5-gib": [{"Key": wb.FILES_PREFIX + "asset1-archive.zip", "Size": 6 * GIB}],
    "tar-gz-2-tib": [{"Key": wb.FILES_PREFIX + "scans.tar.gz", "Size": 2 * TIB}],
    "tar-5-tib": [{"Key": wb.FILES_PREFIX + "everything.tar", "Size": 5 * TIB}],
}


def _zip_archive_run(objects):
    """P2b's _run for a run of the zipArchive workflow (GLOBAL/utility-zip-archive)."""
    original = wb.base._event

    def event(**overrides):
        return original(**dict(overrides, workflowId=WORKFLOW_ID))
    with patch.object(wb.base, "_event", event):
        return wb._run(WORKFLOW_DATABASE_ID, objects)


@pytest.mark.unit
@pytest.mark.parametrize("archive", list(ARCHIVES))
class TestZipArchiveWriteBack:
    def test_the_ingestion_handler_accepts_the_archive(self, archive):
        _resp, ingestion, _m = _zip_archive_run(ARCHIVES[archive])
        assert len(ingestion) == 1
        assert ingestion[0]["statusCode"] == 200, ingestion[0].get("body")
        body = json.loads(ingestion[0]["body"])
        assert body["overallSuccess"] is True
        assert [r["relativeKey"] for r in body["fileResults"] if r["success"]] == [
            ARCHIVES[archive][0]["Key"][len(wb.FILES_PREFIX):]]

    def test_the_cross_call_names_the_global_zip_archive_workflow(self, archive):
        _resp, ingestion, m = _zip_archive_run(ARCHIVES[archive])
        large = ARCHIVES[archive][0]["Size"] > GIB
        if large:
            # The copy engine finds the run's main row by workflowDatabaseId:workflowId.
            jobs = [c.args[3] for c in m["start"].call_args_list]
            assert len(jobs) == 1
            assert jobs[0]["workflowDatabaseId"] == WORKFLOW_DATABASE_ID
            assert jobs[0]["workflowId"] == WORKFLOW_ID
            assert jobs[0]["batchId"] == wb.EXEC
            m["queue"].assert_not_called()
            m["copy"].assert_not_called()
        else:
            m["start"].assert_not_called()

    def test_the_run_ends_as_its_archive_requires(self, archive):
        resp, _ingestion, m = _zip_archive_run(ARCHIVES[archive])
        assert resp["statusCode"] == 200
        if ARCHIVES[archive][0]["Size"] > GIB:
            assert resp["outputIngestionPending"] is True
            m["record"].assert_not_called()
        else:
            assert resp["outputIngestionPending"] is False
            assert m["record"].call_args.kwargs["execution_status"] == "SUCCEEDED"


@pytest.mark.unit
class TestTheArchiveWriteBackIsSentOnce:
    """A mode 2 archive is written back through the no-retry synchronous client (#1205): a
    completion that outlives the read timeout is not sent a second time, and the retrying client
    (Event invokes only) is never used for it."""

    def test_a_timed_out_archive_completion_is_not_repeated(self):
        from botocore.exceptions import ReadTimeoutError
        from unittest.mock import MagicMock
        po = wb.po
        invoke_client = MagicMock()
        invoke_client.invoke.side_effect = ReadTimeoutError(endpoint_url="https://lambda")
        event_client = MagicMock()
        with patch.object(po, "invoke_client", invoke_client), patch.object(po, "client", event_client):
            result = po.process_external_upload(
                "upl-1", "asset1", wb.DB, "assetFile", [wb.FILES_PREFIX + "asset1-archive.zip"],
                wb.FILES_PREFIX, {"http": {}})
        assert result is None
        invoke_client.invoke.assert_called_once()
        assert invoke_client.invoke.call_args.kwargs["InvocationType"] == "RequestResponse"
        event_client.invoke.assert_not_called()


@pytest.mark.unit
def test_a_zip_download_runs_output_step_has_nothing_to_write_back():
    resp, ingestion, m = _zip_archive_run([])
    assert resp["statusCode"] == 200
    assert ingestion == []
    m["start"].assert_not_called()
    assert m["record"].call_args.kwargs["execution_status"] == "SUCCEEDED"
