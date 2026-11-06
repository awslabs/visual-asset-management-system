# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The archive job: resolve, check, then stream the archive straight into S3.

Two modes, chosen by constructPipeline from the run manifest:

- ``zipDownload`` (a Download as Zip job, launched by VAMS under the system trigger): the request
  manifest in the auxiliary bucket is the authority for what is archived; the archive goes to the
  job's ``preparedDownloads/`` key and the job row gets the status and progress.
- ``archiveRun`` (an ordinary workflow run of the system pipeline): the run's input files are
  archived into its standard outputs files prefix, so the output step ingests the archive into the
  asset as a new file version.

Nothing is staged on disk: members stream from S3 (ranged GETs of pinned versions) through the
archive writer into a bounded multipart upload.
"""

import json
import os
import re
import signal
import threading
import time

import boto3
from boto3.dynamodb.conditions import Key
from botocore.config import Config

from .engine.reader import PrefetchReader
from .engine.sink import MultipartSink, part_size_bytes
from .engine.tarstream import TarStreamWriter
from .engine.zipstream import ZipStreamWriter
from .resolve import check_members, enforce_limits, expand_selection, filter_patterns, list_current
from .rules import CORE_BLOCKED_CONTENT_TYPES, CORE_BLOCKED_EXTENSIONS, Refusal
from .status import StatusWriter

RETRY_CONFIG = Config(retries={"max_attempts": 8, "mode": "adaptive"}, max_pool_connections=64)
REASON_PREFIX = "ZIPDOWNLOAD: "
GENERIC_FAILURE = "The archive job failed"
STOPPED = "The archive job was stopped"
CONTENT_TYPES = {"zip": "application/zip", "tar": "application/x-tar", "tar.gz": "application/gzip"}
EXTENSIONS = {"zip": ".zip", "tar": ".tar", "tar.gz": ".tar.gz"}
PROGRESS_EVERY_BYTES = 64 * 1024 * 1024
PROGRESS_EVERY_FILES = 100
HEARTBEAT_SECONDS = 300
_NAME_ALLOWED = re.compile(r"[^A-Za-z0-9 ._()\-]")


class ArchiveFailure(Exception):
    """A failure with the user-safe reason to report."""

    def __init__(self, reason, refusal=False):
        super().__init__(reason)
        self.reason = reason
        self.refusal = refusal


class Clients:
    """S3 clients per Region (an asset bucket may live in another Region than the deployment)."""

    def __init__(self, bucket_regions=None, factory=None):
        self._regions = dict(bucket_regions or {})
        self._factory = factory or (lambda region: boto3.client(
            "s3", region_name=region or os.environ.get("AWS_REGION"), config=RETRY_CONFIG))
        self._cache = {}

    def for_bucket(self, bucket):
        region = self._regions.get(bucket, "")
        if region not in self._cache:
            self._cache[region] = self._factory(region)
        return self._cache[region]


def estimate_archive_bytes(members, empty_dirs):
    """Upper bound of the archive's size for the part-size choice: data, deflate's worst-case growth,
    and per-entry headers, descriptors and directory records."""
    data = sum(int(m["size"]) for m in members)
    names = sum(len(m["relativePath"].encode("utf-8")) for m in members)
    return int(data * 1.01) + names * 3 + (len(members) + len(empty_dirs)) * 1024 + 1024 * 1024


def safe_output_name(name, archive_format):
    extension = EXTENSIONS[archive_format]
    name = (name or "").replace("\\", "/").split("/")[-1].strip()
    for ext in sorted(EXTENSIONS.values(), key=len, reverse=True):
        if name.lower().endswith(ext):
            name = name[: -len(ext)]
            break
    name = _NAME_ALLOWED.sub("_", name).lstrip(". ") or "archive"
    return name[:200 - len(extension)] + extension


def write_archive(clients, members, empty_dirs, sink, archive_format, compression, on_progress=None):
    """Stream every member into ``sink``. Returns (archive size, files, bytes)."""
    if archive_format == "zip":
        writer = ZipStreamWriter(sink, compression=compression)
    elif archive_format in ("tar", "tar.gz"):
        writer = TarStreamWriter(sink, gzip=archive_format == "tar.gz")
    else:
        raise ArchiveFailure(f"Unsupported archive format {archive_format}")
    reader = PrefetchReader(clients.for_bucket, members)
    files_done = bytes_done = 0
    last_bytes = last_files = 0
    try:
        for rel in empty_dirs:
            writer.add_directory(rel.lstrip("/"))
        for index, member in enumerate(members):
            writer.add_file(member["relativePath"].lstrip("/"), reader.chunks(index), member["size"],
                            member.get("mtime") or 0)
            files_done += 1
            bytes_done += int(member["size"])
            if on_progress and (bytes_done - last_bytes >= PROGRESS_EVERY_BYTES
                                or files_done - last_files >= PROGRESS_EVERY_FILES):
                on_progress(files_done, bytes_done)
                last_bytes, last_files = bytes_done, files_done
        size = writer.close()
    finally:
        reader.close()
    return size, files_done, bytes_done


class Heartbeat:
    """Heartbeats the VAMS workflow's task token while a long archive runs."""

    def __init__(self, sfn_client, token, interval=HEARTBEAT_SECONDS):
        self._sfn, self._token, self._interval = sfn_client, token, interval
        self._stop = threading.Event()
        self._thread = None

    def __enter__(self):
        if self._token:
            self._thread = threading.Thread(target=self._run, daemon=True)
            self._thread.start()
        return self

    def _run(self):
        while not self._stop.wait(self._interval):
            try:
                self._sfn.send_task_heartbeat(taskToken=self._token)
            except Exception:  # nosec B110 - a missed heartbeat is retried on the next interval
                pass

    def __exit__(self, *exc):
        self._stop.set()


class Job:
    """One archive job. ``stop()`` (the SIGTERM handler) aborts the upload and records the stop.

    Mode 1 follows the job row's monotonic rule (backend common/zipDownloads.py): a landed archive
    wins with one outcome. Once the upload completed, no path writes FAILED (the terminal callback
    finds the archive and records SUCCEEDED if this job could not); a failure or stop that cannot
    tell whether the upload completed checks the archive object first; and an archive that landed
    after another writer finalized the job without it is deleted, so the row's outcome is the only
    one."""

    def __init__(self, definition, clients, dynamodb=None, logger=print):
        self.definition = definition
        self.clients = clients
        self.dynamodb = dynamodb
        self.log = logger
        self.sink = None
        self.status = None
        self._stopped = False
        self._landed = None   # (archive size, files, bytes) once the archive upload completed

    def stop(self):
        self._stopped = True
        if self._landed is not None:
            return
        if self.sink is not None:
            self.sink.abort()
        self._record_failure(STOPPED)

    def _archive_landed(self):
        """Whether this job's archive object exists. Asked only after the upload was aborted (or
        failed), so the answer cannot change afterwards. An error answers True: skipping the FAILED
        write is always safe, because the terminal callback finalizes the job from the object."""
        if self.sink is None:
            return False
        try:
            return self.sink.object_exists()
        except Exception:
            return True

    def _record_failure(self, reason):
        if self.status is None:
            return
        if self._landed is not None or self._archive_landed():
            self.log("The archive landed; leaving its outcome to the success write or the terminal callback")
            return
        self.status.failed(reason)

    def _discard_unrecorded_archive(self):
        """The SUCCEEDED write lost: another writer finalized the job. Keep the archive only when the
        job is SUCCEEDED; otherwise it landed after the job ended without it, and is deleted (this
        upload's version only) so the job has one outcome."""
        row = self.status.read()
        if row and row.get("status") == "SUCCEEDED":
            return
        try:
            self.sink.delete_landed()
            self.log("The job ended before its archive landed; the archive was deleted")
        except Exception as e:  # nosec B110 - the prepared-downloads retention rule is the backstop
            self.log(f"Could not delete an archive that landed after its job ended: {e!r}")

    # ---- mode 1 -------------------------------------------------------------------------------
    def run_zip_download(self):
        spec = self.definition["zipDownload"]
        aux = self.clients.for_bucket(spec["manifestBucket"])
        manifest = json.loads(aux.get_object(Bucket=spec["manifestBucket"],
                                             Key=spec["manifestKey"])["Body"].read())
        for field in ("requestId", "userId", "databaseId", "assetId"):
            if manifest.get(field) != spec.get(field):
                raise ArchiveFailure(GENERIC_FAILURE)
        if manifest.get("archiveBucket") != spec["manifestBucket"]:
            raise ArchiveFailure(GENERIC_FAILURE)
        table = self.dynamodb.Table(os.environ["ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE_NAME"])
        self.status = StatusWriter(table, manifest["userId"], manifest["requestId"],
                                   self.definition.get("workflowExecutionId", ""))
        bucket, base_key = manifest["assetBucket"], manifest["assetBaseKey"]
        try:
            members, empty_dirs = expand_selection(self.clients.for_bucket(bucket), bucket, base_key,
                                                   manifest.get("entries"), manifest.get("pinnedFiles"))
            check_members(self.clients.for_bucket, members,
                          tuple(manifest.get("blockedExtensions") or CORE_BLOCKED_EXTENSIONS),
                          tuple(manifest.get("blockedContentTypes") or CORE_BLOCKED_CONTENT_TYPES))
            limits = manifest.get("limits") or {}
            total = enforce_limits(members, limits.get("maxFiles"), limits.get("maxTotalBytes"))
        except Refusal as r:
            raise ArchiveFailure(str(r), refusal=True)
        resolved = "\n".join(json.dumps({"relativePath": m["relativePath"], "versionId": m["versionId"],
                                         "size": m["size"]}) for m in members) + "\n"
        aux.put_object(Bucket=manifest["archiveBucket"], Key=manifest["resolvedManifestKey"],
                       Body=resolved.encode("utf-8"), ContentType="application/x-ndjson")
        if not self.status.running(len(members), total, manifest["resolvedManifestKey"]):
            self.log("The job was cancelled or finalized before the archive started; stopping")
            return {"status": "stopped"}
        self.sink = MultipartSink(aux, manifest["archiveBucket"], manifest["archiveKey"],
                                  part_size_bytes(estimate_archive_bytes(members, empty_dirs)),
                                  content_type=CONTENT_TYPES[manifest["archiveFormat"]])

        def on_progress(files, size):
            if not self.status.progress(files, size):
                if self.status.lost:
                    raise ArchiveFailure(STOPPED)

        try:
            archive_size, files, size = write_archive(
                self.clients, members, empty_dirs, self.sink, manifest["archiveFormat"],
                manifest.get("compression", "store"), on_progress)
            if self._stopped:
                raise ArchiveFailure(STOPPED)
            self.sink.close()
        except BaseException:
            self.sink.abort()
            raise
        self._landed = (archive_size, files, size)
        if not self.status.succeeded(archive_size, files, size):
            self._discard_unrecorded_archive()
            return {"status": "stopped"}
        return {"status": "succeeded", "archiveSizeBytes": archive_size, "files": files}

    # ---- mode 2 -------------------------------------------------------------------------------
    def _pinned_members(self, run, asset_version_id):
        table = self.dynamodb.Table(os.environ["ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME"])
        pk = f"{run['databaseId']}:{run['assetId']}:{asset_version_id}"
        items, kwargs = [], {"KeyConditionExpression": Key("databaseId:assetId:assetVersionId").eq(pk)}
        while True:
            response = table.query(**kwargs)
            items.extend(response.get("Items", []))
            if "LastEvaluatedKey" not in response:
                break
            kwargs["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        if not items:
            raise Refusal("The requested asset version has no files")
        prefixes = [entry["relativePrefix"] for entry in run["inputs"]]
        pinned = []
        for item in items:
            rel = "/" + (item.get("fileKey") or "").lstrip("/")
            if not item.get("versionId") or not any(rel.startswith(p) or rel == p for p in prefixes):
                continue
            pinned.append({"key": run["assetBaseKey"] + rel.lstrip("/"), "versionId": item["versionId"],
                           "size": int(item.get("size") or 0)})
        return pinned

    def _run_inputs(self, spec):
        """The run's input files from its manifest (it is read here rather than passed in the job
        command, whose size AWS Batch bounds): one asset, one bucket, each entry as an asset-relative
        path ('/folder/' for a folder or '/' for the whole asset) with its pinned version."""
        location = spec["runManifest"]
        client = self.clients.for_bucket(location["bucket"])
        manifest = json.loads(client.get_object(Bucket=location["bucket"], Key=location["key"])["Body"].read())
        asset_id = spec.get("assetId", "")
        bucket, base_key, inputs = None, None, []
        for entry in manifest.get("inputFiles") or []:
            key = entry.get("key", "")
            parts = key.split("/")
            if not asset_id or asset_id not in parts:
                raise Refusal("An input file is outside the run's asset")
            index = parts.index(asset_id)
            entry_base = "/".join(parts[:index + 1]) + "/"
            if bucket is None:
                bucket, base_key = entry.get("bucket", ""), entry_base
            elif entry.get("bucket") != bucket or entry_base != base_key:
                raise Refusal("An archive run archives files of one asset")
            version = entry.get("versionId") if isinstance(entry.get("versionId"), str) else ""
            inputs.append({"relativePrefix": "/" + key[len(entry_base):], "versionId": version or None})
        if not inputs:
            raise Refusal("The run has no input files")
        return {"assetBucket": bucket, "assetBaseKey": base_key, "inputs": inputs,
                "assetId": asset_id, "databaseId": spec.get("databaseId", "")}

    def run_archive(self):
        spec = self.definition["archiveRun"]
        config = spec.get("config") or {}
        try:
            run = self._run_inputs(spec)
        except Refusal as r:
            raise ArchiveFailure(str(r), refusal=True)
        run.update(outputBucket=spec["outputBucket"], outputDir=spec["outputDir"])
        archive_format = config.get("format") or "zip"
        if archive_format == "tgz":
            archive_format = "tar.gz"
        if archive_format not in EXTENSIONS:
            raise ArchiveFailure(f"Unsupported archive format {archive_format}", refusal=True)
        compression = config.get("compression") or "store"
        if compression not in ("store", "deflate") or (compression == "deflate" and archive_format != "zip"):
            raise ArchiveFailure("compression must be store, or deflate for zip", refusal=True)
        bucket, base_key = run["assetBucket"], run["assetBaseKey"]
        client = self.clients.for_bucket(bucket)
        try:
            if config.get("assetVersionId"):
                pinned = self._pinned_members(run, str(config["assetVersionId"]))
                members, empty_dirs = expand_selection(client, bucket, base_key, [], pinned)
            else:
                entries = []
                for entry in run["inputs"]:
                    key = base_key + entry["relativePrefix"].lstrip("/")
                    if entry["relativePrefix"].endswith("/"):
                        entries.append({"type": "folder", "key": key})
                    else:
                        entries.append({"type": "file", "key": key, "versionId": entry.get("versionId")})
                members, empty_dirs = expand_selection(client, bucket, base_key, entries)
            members = filter_patterns(members, config.get("includePatterns"), config.get("excludePatterns"))
            check_members(self.clients.for_bucket, members, CORE_BLOCKED_EXTENSIONS,
                          CORE_BLOCKED_CONTENT_TYPES)
            enforce_limits(members, None, 5 * 1024 ** 4)
        except Refusal as r:
            raise ArchiveFailure(str(r), refusal=True)
        out_bucket, out_dir = run["outputBucket"], run["outputDir"].rstrip("/") + "/"
        name = safe_output_name(config.get("outputFileName") or f"{run.get('assetId', 'asset')}-archive",
                                archive_format)
        self.sink = MultipartSink(self.clients.for_bucket(out_bucket), out_bucket, out_dir + name,
                                  part_size_bytes(estimate_archive_bytes(members, empty_dirs)),
                                  content_type=CONTENT_TYPES[archive_format])
        try:
            archive_size, files, _ = write_archive(self.clients, members, empty_dirs, self.sink,
                                                   archive_format, compression)
            if self._stopped:
                raise ArchiveFailure(STOPPED)
            self.sink.close()
        except BaseException:
            self.sink.abort()
            raise
        return {"status": "succeeded", "archiveKey": out_dir + name, "archiveSizeBytes": archive_size,
                "files": files}

    def run(self):
        mode = self.definition.get("mode")
        try:
            if mode == "zipDownload":
                return self.run_zip_download()
            if mode == "archiveRun":
                return self.run_archive()
            raise ArchiveFailure(GENERIC_FAILURE)
        except ArchiveFailure as f:
            self._record_failure(f.reason)
            raise
        except Exception as e:
            self.log(f"Archive job failed: {e!r}")
            self._record_failure(GENERIC_FAILURE)
            raise ArchiveFailure(GENERIC_FAILURE) from e


def install_sigterm(job):
    """AWS Batch sends SIGTERM before SIGKILL on terminate (cancel, abort, timeout)."""
    def handler(signum, frame):
        job.stop()
        raise SystemExit(143)
    signal.signal(signal.SIGTERM, handler)


def main(argv, environ=os.environ):
    definition = json.loads(argv[1])
    region = environ.get("AWS_REGION")
    sfn = boto3.client("stepfunctions", region_name=region, config=RETRY_CONFIG)
    dynamodb = boto3.resource("dynamodb", region_name=region, config=RETRY_CONFIG)
    job = Job(definition, Clients(definition.get("bucketRegions")), dynamodb)
    install_sigterm(job)
    task_token = environ.get("TASK_TOKEN", "")
    started = time.time()
    try:
        with Heartbeat(sfn, definition.get("externalSfnTaskToken", "")):
            result = job.run()
        print(json.dumps({"archive": result, "seconds": round(time.time() - started, 1)}))
        if task_token:
            sfn.send_task_success(taskToken=task_token, output=json.dumps({"status": "Pipeline Success",
                                                                            "archive": result}))
        return 0
    except ArchiveFailure as f:
        print(json.dumps({"archiveFailed": f.reason, "refusal": f.refusal}))
        if task_token:
            sfn.send_task_failure(taskToken=task_token, error="ArchivePipelineError",
                                  cause=(REASON_PREFIX + f.reason)[:256])
        return 1
