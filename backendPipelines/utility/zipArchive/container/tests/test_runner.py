#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""zipArchive job end to end against in-memory S3 and DynamoDB doubles: resolution, the refusal rules,
limits, status writes and their conditions, multipart abort on failure and on SIGTERM, and both modes.
Also pins the container's core blocklists to the backend's."""

import ast
import io
import json
import os
import re
import zipfile
from datetime import datetime, timezone

import pytest
from botocore.exceptions import ClientError

from zip_archive_pipeline import runner
from zip_archive_pipeline.resolve import expand_selection
from zip_archive_pipeline.rules import (ARCHIVED_IN_SELECTION, CORE_BLOCKED_CONTENT_TYPES,
                                        CORE_BLOCKED_EXTENSIONS, is_blocked)
from zip_archive_pipeline.status import StatusWriter

pytestmark = pytest.mark.unit
REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 5))
AUX, ASSETS = "aux", "assets"
BASE = "xasset1/"


def _err(code, delete_marker=False):
    response = {"Error": {"Code": code}}
    if delete_marker:
        # S3 marks a HeadObject 404 on a current delete marker with this header.
        response["ResponseMetadata"] = {"HTTPHeaders": {"x-amz-delete-marker": "true"}}
    return ClientError(response, "Op")


class FakeS3:
    def __init__(self):
        self.objects = {}   # (bucket, key) -> list of versions newest first
        self.uploads = {}
        self.completed = {}
        self.aborted = []

    def add(self, key, data=b"x", version="v1", bucket=ASSETS, content_type="model/gltf-binary",
            storage_class="STANDARD", metadata=None, delete_marker=False):
        self.objects.setdefault((bucket, key), []).insert(0, dict(
            VersionId=version, Data=data, ContentType=content_type, StorageClass=storage_class,
            Metadata=metadata or {}, DeleteMarker=delete_marker))

    def _version(self, bucket, key, version_id):
        versions = self.objects.get((bucket, key)) or []
        if not versions:
            raise _err("404")
        v = versions[0] if not version_id else next((x for x in versions if x["VersionId"] == version_id), None)
        if v is None:
            raise _err("404")
        if v["DeleteMarker"]:
            raise _err("405") if version_id else _err("404", delete_marker=True)
        return v

    def head_object(self, Bucket, Key, VersionId=None):
        v = self._version(Bucket, Key, VersionId)
        head = {"ContentLength": len(v["Data"]), "ContentType": v["ContentType"], "Metadata": v["Metadata"],
                "VersionId": v["VersionId"], "LastModified": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        if v["StorageClass"] != "STANDARD":
            head["StorageClass"] = v["StorageClass"]
        return head

    def get_object(self, Bucket, Key, VersionId=None, Range=None):
        v = self._version(Bucket, Key, VersionId)
        data = v["Data"]
        if Range:
            start, end = (int(x) for x in Range.replace("bytes=", "").split("-"))
            data = data[start:end + 1]
        return {"Body": io.BytesIO(data)}

    def list_object_versions(self, Bucket, Prefix, **_):
        versions = []
        for (bucket, key), entries in sorted(self.objects.items()):
            if bucket == Bucket and key.startswith(Prefix):
                for i, v in enumerate(entries):
                    if not v["DeleteMarker"]:
                        versions.append({"Key": key, "VersionId": v["VersionId"], "IsLatest": i == 0,
                                         "Size": len(v["Data"]), "StorageClass": v["StorageClass"],
                                         "LastModified": datetime(2026, 1, 2, tzinfo=timezone.utc)})
        return {"Versions": versions, "IsTruncated": False}

    def put_object(self, Bucket, Key, Body, **_):
        self.add(Key, data=Body, bucket=Bucket, version="p")

    def create_multipart_upload(self, Bucket, Key, Metadata=None, **_):
        self.uploads[(Bucket, Key)] = {}
        self.upload_metadata = getattr(self, "upload_metadata", {})
        self.upload_metadata[(Bucket, Key)] = dict(Metadata or {})
        return {"UploadId": "u1"}

    def upload_part(self, Bucket, Key, UploadId, PartNumber, Body):
        self.uploads[(Bucket, Key)][PartNumber] = Body
        return {"ETag": str(PartNumber)}

    def complete_multipart_upload(self, Bucket, Key, UploadId, MultipartUpload):
        if (Bucket, Key) not in self.uploads:
            # S3 refuses to complete an aborted upload.
            raise _err("NoSuchUpload")
        parts = self.uploads.pop((Bucket, Key))
        data = b"".join(parts[p["PartNumber"]] for p in MultipartUpload["Parts"])
        self.completed[(Bucket, Key)] = data
        version = f"m{len(self.completed)}"
        self.add(Key, data=data, bucket=Bucket, version=version, content_type="application/zip",
                 metadata=getattr(self, "upload_metadata", {}).get((Bucket, Key)))
        return {"VersionId": version}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.aborted.append(Key)
        self.uploads.pop((Bucket, Key), None)

    def list_parts(self, Bucket, Key, UploadId, **_):
        if (Bucket, Key) not in self.uploads:
            raise _err("NoSuchUpload")
        return {"Parts": [{"PartNumber": n} for n in sorted(self.uploads[(Bucket, Key)])]}

    def list_multipart_uploads(self, Bucket, Prefix, **_):
        return {"Uploads": [{"Key": k, "UploadId": "u1"} for (b, k) in self.uploads
                            if b == Bucket and k.startswith(Prefix)], "IsTruncated": False}

    def delete_objects(self, Bucket, Delete):
        # The backend's working-object cleanup (common/zipDownloads.delete_working_objects).
        # Recorded apart from ``deleted`` (the container's own archive deletes the races pin).
        self.batch_deleted = getattr(self, "batch_deleted", [])
        for obj in Delete["Objects"]:
            self.batch_deleted.append((obj["Key"], obj.get("VersionId")))
            versions = self.objects.get((Bucket, obj["Key"])) or []
            self.objects[(Bucket, obj["Key"])] = [v for v in versions
                                                  if v["VersionId"] != obj.get("VersionId")]
        return {}

    def delete_object(self, Bucket, Key, VersionId=None):
        self.deleted = getattr(self, "deleted", []) + [(Key, VersionId)]
        versions = self.objects.get((Bucket, Key)) or []
        if VersionId:
            self.objects[(Bucket, Key)] = [v for v in versions if v["VersionId"] != VersionId]
        else:
            versions.insert(0, dict(VersionId="dm", Data=b"", ContentType="", StorageClass="STANDARD",
                                    Metadata={}, DeleteMarker=True))
        return {}


class FakeTable:
    def __init__(self, row=None):
        self.row = row
        self.updates = []

    def update_item(self, Key, UpdateExpression, ConditionExpression, ExpressionAttributeNames,
                    ExpressionAttributeValues):
        self.updates.append((UpdateExpression, ConditionExpression, dict(ExpressionAttributeValues)))
        row = self.row
        ok = row is not None and row["status"] in ("QUEUED", "RUNNING") and (
            "workflowExecutionId" not in row or row["workflowExecutionId"] == ExpressionAttributeValues[":eid"])
        if not ok:
            raise _err("ConditionalCheckFailedException")
        for assignment in UpdateExpression.replace("SET ", "").split(", "):
            left, right = [p.strip() for p in assignment.split("=")]
            row[ExpressionAttributeNames.get(left, left)] = ExpressionAttributeValues[right]

    def get_item(self, Key, ConsistentRead=False):
        return {"Item": dict(self.row)} if self.row is not None else {}


class FakeDynamo:
    def __init__(self, table):
        self.table = table

    def Table(self, name):
        return self.table


class OneClient(runner.Clients):
    def __init__(self, s3):
        super().__init__(factory=lambda region: s3)


def _manifest(entries=None, pinned=None, fmt="zip", **extra):
    m = {"schemaVersion": 1, "mode": "zipDownload", "requestId": "r1", "userId": "alice", "databaseId": "db1",
         "assetId": "asset1", "assetBucket": ASSETS, "assetBaseKey": BASE, "archiveBucket": AUX,
         "archiveKey": "vams.preparedDownloads/db1/asset1/h/r1/out.zip",
         "resolvedManifestKey": "vams.preparedDownloads/db1/asset1/h/r1/resolved-manifest.jsonl",
         "archiveFormat": fmt, "compression": "store", "entries": entries or [], "pinnedFiles": pinned or [],
         "limits": {"maxFiles": 100, "maxTotalBytes": 10 ** 9},
         "blockedExtensions": list(CORE_BLOCKED_EXTENSIONS), "blockedContentTypes": list(CORE_BLOCKED_CONTENT_TYPES)}
    m.update(extra)
    return m


def _zip_job(s3, manifest, row=None, execution_id="exec-1"):
    s3.add("vams.preparedDownloads/db1/asset1/h/r1/manifest.json", json.dumps(manifest).encode(), bucket=AUX)
    table = FakeTable(row if row is not None else {"status": "QUEUED", "workflowExecutionId": "exec-1"})
    definition = {"mode": "zipDownload", "workflowExecutionId": execution_id,
                  "zipDownload": {"manifestBucket": AUX, "manifestKey": "vams.preparedDownloads/db1/asset1/h/r1/manifest.json",
                                  "requestId": "r1", "userId": "alice", "databaseId": "db1", "assetId": "asset1"}}
    return runner.Job(definition, OneClient(s3), FakeDynamo(table), logger=lambda *a: None), table


@pytest.fixture
def s3():
    s = FakeS3()
    s.add(BASE + "a.glb", b"A" * 100)
    s.add(BASE + "tex/t1.png", b"T1", content_type="image/png")
    s.add(BASE + "tex/t2.png", b"T2", content_type="image/png")
    s.add(BASE + "tex/empty/", b"", content_type="")
    return s


class TestZipDownloadMode:
    def test_whole_asset_archive(self, s3):
        job, table = _zip_job(s3, _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}]))
        result = job.run()
        assert result["status"] == "succeeded" and result["files"] == 3
        data = s3.completed[(AUX, "vams.preparedDownloads/db1/asset1/h/r1/out.zip")]
        with zipfile.ZipFile(io.BytesIO(data)) as z:
            assert sorted(z.namelist()) == ["a.glb", "tex/empty/", "tex/t1.png", "tex/t2.png"]
            assert z.read("a.glb") == b"A" * 100
        assert table.row["status"] == "SUCCEEDED" and table.row["archiveSizeBytes"] == len(data)
        assert table.row["filesTotal"] == 3
        resolved = s3.objects[(AUX, "vams.preparedDownloads/db1/asset1/h/r1/resolved-manifest.jsonl")][0]["Data"]
        assert [json.loads(l)["relativePath"] for l in resolved.decode().splitlines()] == [
            "/a.glb", "/tex/t1.png", "/tex/t2.png"]

    def test_pinned_versions_are_read(self, s3):
        s3.add(BASE + "a.glb", b"NEW", version="v2")
        job, _ = _zip_job(s3, _manifest(pinned=[{"key": BASE + "a.glb", "relativePath": "/a.glb", "versionId": "v1", "size": 100}]))
        job.run()
        with zipfile.ZipFile(io.BytesIO(next(iter(s3.completed.values())))) as z:
            assert z.read("a.glb") == b"A" * 100

    @pytest.mark.parametrize("setup,needle", [
        (lambda s: s.add(BASE + "tex/run.exe", b"MZ"), "file type"),
        (lambda s: s.add(BASE + "tex/x.bin", b"1", content_type="application/x-msdownload; charset=binary"), "file type"),
        (lambda s: s.add(BASE + "tex/cold.png", b"1", storage_class="GLACIER"), "archived"),
        (lambda s: s.add(BASE + "tex/old.png", b"1", metadata={"vams-status": "archived"}), "archived"),
    ])
    def test_refusals_fail_the_whole_job(self, s3, setup, needle):
        setup(s3)
        job, table = _zip_job(s3, _manifest(entries=[{"type": "folder", "key": BASE + "tex/", "relativePath": "/tex/"}]))
        with pytest.raises(runner.ArchiveFailure) as e:
            job.run()
        assert e.value.refusal and needle in e.value.reason
        assert table.row["status"] == "FAILED" and needle in table.row["errorMessage"]
        assert s3.completed == {} and s3.uploads == {}

    def test_deleted_pinned_version_refused(self, s3):
        s3.add(BASE + "gone.glb", b"", version="dm", delete_marker=True)
        job, table = _zip_job(s3, _manifest(pinned=[{"key": BASE + "gone.glb", "relativePath": "/gone.glb", "versionId": "dm", "size": 0}]))
        with pytest.raises(runner.ArchiveFailure):
            job.run()
        assert "deleted version" in table.row["errorMessage"]

    def test_a_file_archived_after_create_reads_as_the_create_refusal(self, s3):
        """An explicit file whose current version became a delete marker between the create and the
        run fails the job with the create's wording, not '<path> was not found', and without naming
        the file."""
        s3.add(BASE + "r2-fail.txt", b"", version="dm", delete_marker=True)
        job, table = _zip_job(s3, _manifest(entries=[
            {"type": "file", "key": BASE + "r2-fail.txt", "relativePath": "/r2-fail.txt"}]))
        with pytest.raises(runner.ArchiveFailure) as e:
            job.run()
        assert e.value.refusal
        assert table.row["errorMessage"] == ARCHIVED_IN_SELECTION
        assert "r2-fail" not in table.row["errorMessage"] and "not found" not in table.row["errorMessage"]

    def test_a_file_marked_archived_after_create_reads_as_the_create_refusal(self, s3):
        s3.add(BASE + "old.png", b"1", metadata={"vams-status": "archived"})
        job, table = _zip_job(s3, _manifest(entries=[
            {"type": "file", "key": BASE + "old.png", "relativePath": "/old.png"}]))
        with pytest.raises(runner.ArchiveFailure):
            job.run()
        assert table.row["errorMessage"] == ARCHIVED_IN_SELECTION

    def test_a_missing_file_still_reads_as_not_found(self, s3):
        job, table = _zip_job(s3, _manifest(entries=[
            {"type": "file", "key": BASE + "never.glb", "relativePath": "/never.glb"}]))
        with pytest.raises(runner.ArchiveFailure):
            job.run()
        assert "was not found" in table.row["errorMessage"]

    def test_limits(self, s3):
        manifest = _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}],
                             limits={"maxFiles": 2, "maxTotalBytes": 10 ** 9})
        job, table = _zip_job(s3, manifest)
        with pytest.raises(runner.ArchiveFailure):
            job.run()
        assert "more than 2 files" in table.row["errorMessage"]

    def test_an_archive_s3_could_not_store_is_refused_before_streaming(self, s3, monkeypatch):
        # #1125 L1: refused at resolution, not at CompleteMultipartUpload after hours of streaming.
        manifest = _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}])
        job, table = _zip_job(s3, manifest)
        # Data (104 bytes) fits any byte limit; the headers and end records do not fit this "S3".
        monkeypatch.setattr(runner, "S3_MAX_OBJECT_BYTES", 1024 * 1024 + 104)
        with pytest.raises(runner.ArchiveFailure) as e:
            job.run()
        assert e.value.refusal and "larger than 5 TiB" in e.value.reason
        assert table.row["status"] == "FAILED" and "larger than 5 TiB" in table.row["errorMessage"]
        assert s3.completed == {} and s3.uploads == {}

    @pytest.mark.parametrize("fmt,compression", [
        ("zip", "store"), ("zip", "deflate"), ("tar", "store"), ("tar.gz", "store")])
    def test_the_upper_bound_holds_for_a_real_archive(self, s3, fmt, compression):
        s3.add(BASE + "deep/" + "n" * 180 + "/" + "é" * 60 + ".bin", os.urandom(70000))
        manifest = _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}], fmt=fmt,
                             compression=compression)
        job, _ = _zip_job(s3, manifest)
        job.run()
        data = next(iter(s3.completed.values()))
        members, dirs = expand_selection(s3, ASSETS, BASE, [{"type": "asset", "key": BASE}])
        assert len(data) <= runner.archive_upper_bound(members, dirs, fmt, compression)

    def test_manifest_identity_must_match_definition(self, s3):
        job, table = _zip_job(s3, _manifest(userId="mallory", entries=[{"type": "asset", "key": BASE, "relativePath": "/"}]))
        with pytest.raises(runner.ArchiveFailure):
            job.run()
        assert table.updates == []

    def test_cancelled_job_is_left_alone(self, s3):
        job, table = _zip_job(s3, _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}]),
                              row={"status": "CANCELLED", "workflowExecutionId": "exec-1"})
        assert job.run() == {"status": "stopped"}
        assert table.row["status"] == "CANCELLED" and s3.completed == {}

    def test_other_runs_cannot_write_the_row(self, s3):
        job, table = _zip_job(s3, _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}]),
                              execution_id="exec-other")
        assert job.run() == {"status": "stopped"}
        assert all(":eid" in u[2] and u[2][":eid"] == "exec-other" for u in table.updates)
        assert "workflowExecutionId = :eid" in table.updates[0][1]

    def test_stream_failure_aborts_upload(self, s3, monkeypatch):
        job, table = _zip_job(s3, _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}]))

        def broken(*a, **k):
            raise IOError("network down")
        monkeypatch.setattr(runner, "write_archive", broken)
        with pytest.raises(runner.ArchiveFailure) as e:
            job.run()
        assert e.value.reason == runner.GENERIC_FAILURE
        assert s3.aborted == ["vams.preparedDownloads/db1/asset1/h/r1/out.zip"]
        assert table.row["errorMessage"] == runner.GENERIC_FAILURE

    def test_sigterm_stop_aborts_and_records(self, s3):
        job, table = _zip_job(s3, _manifest(entries=[{"type": "asset", "key": BASE, "relativePath": "/"}]))
        job.status = StatusWriter(table.__class__({"status": "RUNNING", "workflowExecutionId": "exec-1"}),
                                  "alice", "r1", "exec-1")

        class Sink:
            aborted = False

            def abort(self):
                Sink.aborted = True

            def head(self):
                return None
        job.sink = Sink()
        job.stop()
        assert Sink.aborted and job.status._table.row["errorMessage"] == runner.STOPPED

    @pytest.mark.parametrize("fmt", ["tar", "tar.gz"])
    def test_tar_formats(self, s3, fmt):
        import tarfile
        manifest = _manifest(entries=[{"type": "file", "key": BASE + "a.glb", "relativePath": "/a.glb"}], fmt=fmt)
        job, _ = _zip_job(s3, manifest)
        job.run()
        data = next(iter(s3.completed.values()))
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as t:
            assert t.extractfile("a.glb").read() == b"A" * 100


class TestZipArchiveRunMode:
    def _job(self, s3, inputs, config=None):
        s3.add("pipelines/run/manifest.json", json.dumps({"inputFiles": inputs}).encode(), bucket=ASSETS)
        definition = {"mode": "archiveRun", "archiveRun": {
            "runManifest": {"bucket": ASSETS, "key": "pipelines/run/manifest.json"}, "assetId": "xasset1",
            "databaseId": "db1", "outputBucket": ASSETS, "outputDir": "pipelines/run/output/files/",
            "config": config or {}}}
        return runner.Job(definition, OneClient(s3), None, logger=lambda *a: None)

    def test_archive_into_outputs(self, s3):
        job = self._job(s3, [{"bucket": ASSETS, "key": BASE, "versionId": ""}],
                        {"format": "zip", "outputFileName": "../bundle", "excludePatterns": ["*.png"]})
        result = job.run()
        assert result["archiveKey"] == "pipelines/run/output/files/bundle.zip"
        with zipfile.ZipFile(io.BytesIO(s3.completed[(ASSETS, result["archiveKey"])])) as z:
            assert "a.glb" in z.namelist() and not any(n.endswith(".png") for n in z.namelist())

    def test_input_from_another_asset_refused(self, s3):
        job = self._job(s3, [{"bucket": ASSETS, "key": "xother/a.glb"}])
        with pytest.raises(runner.ArchiveFailure) as e:
            job.run()
        assert e.value.refusal

    def test_blocked_type_refused(self, s3):
        s3.add(BASE + "evil.bat", b"x")
        job = self._job(s3, [{"bucket": ASSETS, "key": BASE, "versionId": ""}])
        with pytest.raises(runner.ArchiveFailure):
            job.run()

    def test_an_archive_s3_could_not_store_is_refused_before_streaming(self, s3, monkeypatch):
        # #1125 L1, the workflow run (mode 2): refused before the output upload starts.
        monkeypatch.setattr(runner, "S3_MAX_OBJECT_BYTES", 1024 * 1024 + 104)
        job = self._job(s3, [{"bucket": ASSETS, "key": BASE, "versionId": ""}], {"format": "zip"})
        with pytest.raises(runner.ArchiveFailure) as e:
            job.run()
        assert e.value.refusal and "larger than 5 TiB" in e.value.reason
        assert s3.completed == {} and s3.uploads == {}


@pytest.mark.parametrize("fmt,compression", [("zip", "store"), ("zip", "deflate"), ("tar", "store"),
                                             ("tar.gz", "store")])
def test_a_selection_at_the_5_tib_limit_is_refused_and_one_with_headroom_fits(fmt, compression):
    # #1125 L1: maxTotalBytesPerRequest allows exactly 5 TiB of data, whose archive S3 cannot store.
    at_limit = [{"relativePath": "/big.bin", "size": runner.S3_MAX_OBJECT_BYTES}]
    with pytest.raises(runner.Refusal):
        runner.check_archive_fits(at_limit, [], fmt, compression)
    headroom = [{"relativePath": "/big.bin", "size": runner.S3_MAX_OBJECT_BYTES - 6 * 1024 ** 3}]
    runner.check_archive_fits(headroom, [], fmt, compression)


def test_expand_dedupes_and_finds_empty_dirs(s3):
    members, dirs = expand_selection(s3, ASSETS, BASE, [
        {"type": "folder", "key": BASE + "tex/"}, {"type": "file", "key": BASE + "tex/t1.png"}])
    assert [m["relativePath"] for m in members] == ["/tex/t1.png", "/tex/t2.png"]
    assert dirs == ["/tex/empty/"]


def test_output_names_are_safe():
    assert runner.safe_output_name("../../x/évil.zip", "zip") == "_vil.zip"
    assert runner.safe_output_name("", "tar.gz") == "archive.tar.gz"


@pytest.mark.parametrize("key,ctype,blocked", [
    ("a.EXE", "", True), ("a.exe.", "", True), ("a.exe::$DATA", "", True), ("a:b.exe", "", True),
    ("notes:v2.txt", "", False), ("a.glb", "Application/X-MSDownload", True), ("a.glb", "model/gltf-binary", False)])
def test_blocklist_matching_mirrors_backend(key, ctype, blocked):
    assert is_blocked(key, ctype, CORE_BLOCKED_EXTENSIONS, CORE_BLOCKED_CONTENT_TYPES) is blocked


def test_core_blocklists_match_backend():
    """The container's core lists (used by zipArchive runs) must equal backend CORE_UNALLOWED_*."""
    path = os.path.join(REPO, "backend", "backend", "common", "constants.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    values = {n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
              if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
              and n.targets[0].id in ("CORE_UNALLOWED_FILE_EXTENSION_LIST", "CORE_UNALLOWED_MIME_LIST")}
    assert set(CORE_BLOCKED_EXTENSIONS) == set(values["CORE_UNALLOWED_FILE_EXTENSION_LIST"])
    assert set(CORE_BLOCKED_CONTENT_TYPES) == set(values["CORE_UNALLOWED_MIME_LIST"])


def test_archived_refusal_matches_backend():
    """The container's archived refusal uses the create's words (backend MSG_ARCHIVED_IN_SELECTION)."""
    path = os.path.join(REPO, "backend", "backend", "common", "zipDownloads.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    value = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign)
                 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == "MSG_ARCHIVED_IN_SELECTION")
    assert ARCHIVED_IN_SELECTION == value


def test_part_size_matches_backend():
    from zip_archive_pipeline.engine.sink import part_size_bytes
    path = os.path.join(REPO, "backend", "backend", "common", "zipDownloads.py")
    source = open(path, encoding="utf-8").read()
    assert "ceil(max(int(estimated_total_bytes), 0) / 9500)" in source
    assert part_size_bytes(1024 ** 4) == 111 * 1024 * 1024
