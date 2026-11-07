#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""#1125 in-dev D16, round 2: the job row records the archive's multipart upload id.

The demo21 recheck of the #1123 D5 rule showed that an abort's immediate answer is not final (an
upload whose abort answered, and whose ListParts then answered NoSuchUpload, was listed again minutes
later). The backend's delayed sweep (common/zipDownloads.sweep_recorded_uploads) therefore aborts the
archive uploads the job row recorded, about 5 and 15 minutes after the stop, and nothing else. So the
container records the upload id (StatusWriter.record_upload, ``archiveUploadIds``) right after
CreateMultipartUpload and before any part, and aborts an upload it could not record.

Every test fails on 0620cfaee (no id is recorded; the sink takes no on_created)."""

import pytest

from zip_archive_pipeline.engine.sink import MIB, MultipartSink
from zip_archive_pipeline.status import StatusWriter
from test_runner import AUX, BASE, FakeS3, FakeTable, _err, _manifest, _zip_job

pytestmark = pytest.mark.unit

KEY = "vams.preparedDownloads/db1/asset1/h/r1/out.zip"


class Recording(FakeS3):
    def __init__(self):
        super().__init__()
        self.calls = []

    def create_multipart_upload(self, **kwargs):
        self.calls.append("create")
        return super().create_multipart_upload(**kwargs)

    def upload_part(self, **kwargs):
        self.calls.append("part")
        return super().upload_part(**kwargs)

    def abort_multipart_upload(self, **kwargs):
        self.calls.append("abort")
        return super().abort_multipart_upload(**kwargs)


def test_the_sink_reports_its_upload_before_any_part():
    s3 = Recording()
    recorded = []
    sink = MultipartSink(s3, AUX, KEY, 5 * MIB,
                         on_created=lambda upload_id: (recorded.append(upload_id), s3.calls.append("recorded")))
    sink.write(b"x" * 10)
    sink.close()
    assert recorded == [sink.upload_id]
    assert s3.calls[:3] == ["create", "recorded", "part"]


def test_an_upload_that_could_not_be_recorded_is_aborted_at_once():
    s3 = Recording()

    def refuse(upload_id):
        raise _err("ProvisionedThroughputExceededException")
    with pytest.raises(Exception):
        MultipartSink(s3, AUX, KEY, 5 * MIB, on_created=refuse)
    assert s3.calls == ["create", "abort"]
    assert (AUX, KEY) not in s3.uploads


def test_the_status_writer_records_the_id_whatever_the_status():
    """A stop that came first still needs the id: written on a CANCELLED row of this run too."""
    table = FakeTable({"status": "CANCELLED", "workflowExecutionId": "exec-1"})
    writer = StatusWriter(table, "alice", "r1", "exec-1")
    writer.record_upload("u1")
    writer.record_upload("u2")
    assert table.row["archiveUploadIds"] == {"u1", "u2"}
    # No status write is counted for it.
    assert table.updates == []


def test_the_status_writer_never_records_on_another_runs_row():
    table = FakeTable({"status": "RUNNING", "workflowExecutionId": "exec-2"})
    writer = StatusWriter(table, "alice", "r1", "exec-1")
    with pytest.raises(Exception):
        writer.record_upload("u1")
    assert "archiveUploadIds" not in table.row


def test_a_zip_download_records_its_archive_upload_on_the_job_row():
    s3 = FakeS3()
    s3.add(BASE + "a.glb", b"A" * 100)
    job, table = _zip_job(s3, _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}]))
    assert job.run()["status"] == "succeeded"
    assert table.row["archiveUploadIds"] == {job.sink.upload_id}
