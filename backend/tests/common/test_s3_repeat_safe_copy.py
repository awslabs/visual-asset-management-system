# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The final copy of an upload or workflow output is safe to repeat (#1848).

Under a burst of writes to one key Amazon S3 can store a CopyObject's new version and still answer
``503 SlowDown``. The asset client's SDK retry then stored a second, identical version, so one upload or
one workflow run showed two file versions. ``uploadFile.copy_s3_object()`` now stamps a fresh write
token and calls ``common.s3.copy_s3_object_repeat_safe()``, which sends each attempt once and, after a
retryable failure, checks whether that attempt landed before it copies again.

The fault is injected below botocore's retry logic: a ``before-send`` handler registered first on the
shipped module's own clients lets moto store the write and then answers the way S3 did (a 503 with a
``SlowDown`` body, with or without ``x-amz-version-id``; a 200 whose body is an error; a read timeout or
a connection error after the write). botocore parses that answer and its retry handler decides on it
exactly as on a real one, so the same test that passes here showed two versions on the code before the
fix. Waits are injected (``sleep``, ``rng``), so nothing sleeps.

``tests/conftest.py`` replaces ``common.s3`` with ``tests/mocks/common/s3.py``, so the shipped module
is loaded by file path (as ``test_s3_region_routing`` does) and uploadFile's call into it is patched to
the shipped function. Every "one version" assertion reads the key's listed versions, never a call count.
"""

import functools
import importlib.util
import os
import sys
from unittest.mock import MagicMock

import boto3
import pytest
from boto3.s3.transfer import TransferConfig
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ClientError, EndpointConnectionError, ReadTimeoutError
from moto import mock_aws
from moto.core.models import botocore_stubber

# Env vars uploadFile reads at import time (set before importing it).
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-s3-buckets-table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("ASSET_FILE_VERSIONS_STORAGE_TABLE_NAME", "test-asset-file-versions-table")
os.environ.setdefault("ASSET_UPLOAD_TABLE_NAME", "test-asset-upload-table")
os.environ.setdefault("SEND_EMAIL_FUNCTION_NAME", "test-send-email-function")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "3600")

from backend.backend.handlers.assets import uploadFile as uf  # noqa: E402
from backend.backend.common import s3MetadataKeys as keys  # noqa: E402

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
MOCK_PATH = os.path.join(os.path.dirname(__file__), "..", "mocks", "common", "s3.py")

REGION = "us-east-1"
OTHER_REGION = "eu-west-1"
STAGING = "staging-bucket-1848"
REMOTE_STAGING = "remote-staging-bucket-1848"
ASSET = "asset-bucket-1848"
STAGED_KEY = "temp/up-1/part.glb"
FINAL_KEY = "db1/a1/part.glb"
BODY = b"glTF" + b"\x00" * 1020
TOKEN_KEY = keys.VAMS_COPY_UPLOAD_TOKEN_METADATA_KEY
CHANGE = {keys.VAMS_CHANGE_SOURCE_METADATA_KEY: keys.VAMS_CHANGE_SOURCE_UPLOAD,
          keys.VAMS_CHANGE_USER_ID_METADATA_KEY: "user-1"}
# Sum of botocore's largest waits between six attempts: rand <= 1, min(2 ** i, 20) for i = 0..4.
SCHEDULE_BOUND_SECONDS = sum(min(2 ** i, 20) for i in range(5))

SLOWDOWN = (b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>SlowDown</Code>'
            b'<Message>Please reduce your request rate.</Message><RequestId>R1848</RequestId>'
            b'<HostId>H1848</HostId></Error>')
SERVICE_UNAVAILABLE = (b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>ServiceUnavailable</Code>'
                       b'<Message>Service is unable to handle request.</Message></Error>')
INTERNAL_ERROR = (b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>InternalError</Code>'
                  b'<Message>We encountered an internal error. Please try again.</Message></Error>')
ACCESS_DENIED = (b'<?xml version="1.0" encoding="UTF-8"?>\n<Error><Code>AccessDenied</Code>'
                 b'<Message>Access Denied</Message></Error>')


def _load_by_path(module_name, path):
    if BACKEND not in sys.path:
        sys.path.insert(0, BACKEND)
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Raw:
    """The raw body of a synthetic response, in the shape botocore reads (``stream()``)."""

    def __init__(self, body):
        self._body = body

    def stream(self, **_kwargs):
        if self._body:
            yield self._body


def _response(request, status, body=b"", headers=None):
    return AWSResponse(request.url, status, {"x-amz-request-id": "R1848", "Content-Type": "application/xml",
                                             **(headers or {})}, _Raw(body))


class WriteFault:
    """A ``before-send`` handler for the write (CopyObject or PutObject) to the asset key.

    For the first ``times`` writes it lets moto store the request (``store``), optionally writes
    ``burst`` versions of another writer after it, and then answers with ``answer``: ``"503"`` (SlowDown,
    naming the stored version when ``header``), ``"503-unavailable"``, ``"200-error"``, ``"403"``,
    ``"read-timeout"`` or ``"connection"``. Later writes reach moto unchanged. ``sent`` counts every write
    to the key, faulted or not."""

    def __init__(self, answer="503", times=1, store=True, header=True, burst=0, writer=None,
                 header_version=None):
        self.answer = answer
        self.times = times
        self.store = store
        self.header = header
        self.burst = burst
        self.writer = writer
        self.header_version = header_version
        self.sent = 0
        self.faulted = 0

    def __call__(self, request=None, event_name=None, **_kwargs):
        if f"/{ASSET}/" not in request.url:
            return None
        self.sent += 1
        if self.faulted >= self.times:
            return None
        self.faulted += 1
        version = self.header_version
        if self.store:
            stored = botocore_stubber(event_name, request)
            version = stored.headers.get("x-amz-version-id") or version
        for index in range(self.burst):
            self.writer.put_object(Bucket=ASSET, Key=FINAL_KEY, Body=b"other %d" % index,
                                   Metadata={"writer": "other"})
        if self.answer == "read-timeout":
            raise ReadTimeoutError(endpoint_url=request.url, error="Read timed out")
        if self.answer == "connection":
            raise EndpointConnectionError(endpoint_url=request.url)
        if self.answer == "200-error":
            response = _response(request, 200, INTERNAL_ERROR)
        elif self.answer == "403":
            response = _response(request, 403, ACCESS_DENIED)
        else:
            headers = {"x-amz-version-id": version} if (self.header and version) else {}
            body = SERVICE_UNAVAILABLE if self.answer == "503-unavailable" else SLOWDOWN
            response = _response(request, 503, body, headers)
        # botocore runs EVERY before-send handler and keeps the first answer, so moto's own handler,
        # later in the chain, would still store this request. It is switched off for this request only;
        # _ResumeMoto, registered last, switches it back on.
        botocore_stubber.enabled = False
        return response


class _ResumeMoto:
    """Registered last on ``before-send``, after moto's handler: moto handles the next request again."""

    def __call__(self, **_kwargs):
        botocore_stubber.enabled = True


class ReadRefusal:
    """A ``before-send`` handler that answers 403 to a HEAD by version id and to ListObjectVersions on
    the asset bucket: a scoped external bucket policy without s3:GetObjectVersion / s3:ListBucketVersions."""

    def __call__(self, request=None, event_name=None, **_kwargs):
        if f"/{ASSET}" not in request.url:
            return None
        if event_name.endswith("HeadObject") and "versionId=" in request.url:
            return _response(request, 403)
        if event_name.endswith("ListObjectVersions"):
            return _response(request, 403, ACCESS_DENIED)
        return None


class Recorder:
    """A ``before-send`` handler that records (region, operation) of every request a client sends."""

    def __init__(self, region):
        self.region = region
        self.seen = []

    def __call__(self, request=None, event_name=None, **_kwargs):
        self.seen.append((self.region, event_name.rsplit(".", 1)[-1]))
        return None


@pytest.fixture
def s3env(monkeypatch):
    monkeypatch.setenv("AWS_REGION", REGION)
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.delenv("VAMS_LAMBDAS_IN_VPC", raising=False)
    with mock_aws():
        shipped = _load_by_path("_shipped_common_s3_repeat_safe", os.path.join(BACKEND, "common", "s3.py"))
        writer = boto3.client("s3", region_name=REGION)
        writer.create_bucket(Bucket=STAGING)
        writer.create_bucket(Bucket=ASSET)
        writer.put_bucket_versioning(Bucket=ASSET, VersioningConfiguration={"Status": "Enabled"})
        remote = boto3.client("s3", region_name=OTHER_REGION)
        remote.create_bucket(Bucket=REMOTE_STAGING,
                             CreateBucketConfiguration={"LocationConstraint": OTHER_REGION})
        for name, region in ((STAGING, REGION), (ASSET, REGION), (REMOTE_STAGING, OTHER_REGION)):
            shipped.register_bucket_region(name, region)
        writer.put_object(Bucket=STAGING, Key=STAGED_KEY, Body=BODY, ContentType="model/gltf-binary")
        remote.put_object(Bucket=REMOTE_STAGING, Key=STAGED_KEY, Body=BODY, ContentType="model/gltf-binary")
        yield _Env(shipped, writer, monkeypatch)


class _Env:
    def __init__(self, shipped, writer, monkeypatch):
        self.shipped = shipped
        self.writer = writer
        self.monkeypatch = monkeypatch
        self.sleeps = []

    def clients(self, region=REGION):
        """The shipped module's clients for ``region``: the SDK-retried one and, on the fixed module, the
        one-attempt writer."""
        found = [self.shipped.s3_client_for_region(region)]
        if hasattr(self.shipped, "s3_single_attempt_client_for_region"):
            found.append(self.shipped.s3_single_attempt_client_for_region(region))
        return found

    def fault(self, operation="CopyObject", region=REGION, **kwargs):
        fault = WriteFault(writer=self.writer, **kwargs)
        for client in self.clients(region):
            client.meta.events.register_first(f"before-send.s3.{operation}", fault)
            # On the generic event: botocore runs a more specific event's handlers first, and moto's
            # handler is registered on the generic one.
            client.meta.events.register_last("before-send", _ResumeMoto())
        return fault

    def refuse_version_reads(self, region=REGION):
        refusal = ReadRefusal()
        self.shipped.s3_client_for_region(region).meta.events.register_first("before-send.s3.*", refusal)
        return refusal

    def record(self, region):
        recorders = []
        for client in self.clients(region):
            recorder = Recorder(region)
            client.meta.events.register_first("before-send.s3.*", recorder)
            recorders.append(recorder)
        return recorders

    def route_upload_copy(self, rng=lambda: 0.5):
        """uploadFile's final copy, run on the shipped common.s3 with injected waits."""
        self.monkeypatch.setattr(uf, "copy_s3_object_repeat_safe", functools.partial(
            self.shipped.copy_s3_object_repeat_safe, sleep=self.sleeps.append, rng=rng))

    def upload_copy(self, source_bucket=STAGING):
        head = self.writer.head_object(Bucket=STAGING, Key=STAGED_KEY)
        return uf.copy_s3_object(source_bucket, STAGED_KEY, ASSET, FINAL_KEY, "db1", "a1",
                                 extra_metadata=dict(CHANGE), source_head=head)

    def helper_copy(self, token="t" * 32, size=len(BODY), **kwargs):
        extra_args = {"Metadata": {"assetid": "a1", TOKEN_KEY: token}, "MetadataDirective": "REPLACE",
                      "ContentType": "model/gltf-binary", "ACL": "bucket-owner-full-control"}
        return self.shipped.copy_s3_object_repeat_safe(
            STAGING, STAGED_KEY, ASSET, FINAL_KEY, extra_args=extra_args, write_token=token,
            source_size=size, sleep=self.sleeps.append, rng=lambda: 0.5, **kwargs)

    def versions(self):
        listed = self.writer.list_object_versions(Bucket=ASSET, Prefix=FINAL_KEY).get("Versions") or []
        return [v for v in listed if v["Key"] == FINAL_KEY]

    def upload_versions(self):
        """The key's versions written by the upload under test (not the burst's other writer)."""
        found = []
        for version in self.versions():
            head = self.writer.head_object(Bucket=ASSET, Key=FINAL_KEY, VersionId=version["VersionId"])
            if head["Metadata"].get("writer") != "other":
                found.append((version["VersionId"], head["Metadata"]))
        return found


# --------------------------------------------------------------------------------------------- #
# A copy S3 stored and then answered with a retryable error is not written again (fail before)
# --------------------------------------------------------------------------------------------- #

@pytest.mark.unit
class TestALandedCopyIsNotWrittenTwice:
    def test_t1_slowdown_naming_the_stored_version(self, s3env):
        fault = s3env.fault(answer="503", header=True)
        s3env.route_upload_copy()

        assert s3env.upload_copy() is True

        written = s3env.upload_versions()
        assert len(written) == 1, f"one upload left {len(written)} versions"
        assert len(written[0][1].get(TOKEN_KEY, "")) == 32
        assert fault.sent <= 1

    def test_t1_the_helper_returns_the_landed_version(self, s3env):
        s3env.fault(answer="503", header=True)

        version_id = s3env.helper_copy()

        assert [v["VersionId"] for v in s3env.versions()] == [version_id]

    def test_t1b_slowdown_without_a_version_header(self, s3env):
        s3env.fault(answer="503", header=False)
        s3env.route_upload_copy()

        assert s3env.upload_copy() is True

        assert len(s3env.upload_versions()) == 1

    def test_t1c_slowdown_then_a_burst_of_other_writers(self, s3env):
        # 20 versions of another writer land after the upload's and before the check: the current
        # object is not the upload's, so the check finds it among the versions written since it began.
        s3env.fault(answer="503", header=False, burst=20)
        s3env.route_upload_copy()

        assert s3env.upload_copy() is True

        written = s3env.upload_versions()
        assert len(written) == 1
        assert TOKEN_KEY in written[0][1]

    def test_t7_read_timeout_after_the_write(self, s3env):
        s3env.fault(answer="read-timeout")
        s3env.route_upload_copy()

        assert s3env.upload_copy() is True

        assert len(s3env.upload_versions()) == 1

    def test_t7_connection_error_after_the_write(self, s3env):
        s3env.fault(answer="connection")
        s3env.route_upload_copy()

        assert s3env.upload_copy() is True

        assert len(s3env.upload_versions()) == 1

    def test_t12_a_200_whose_body_is_an_error(self, s3env):
        # S3 can answer CopyObject 200 with an <Error> body; botocore turns it into a 500.
        s3env.fault(answer="200-error")
        s3env.route_upload_copy()

        assert s3env.upload_copy() is True

        assert len(s3env.upload_versions()) == 1

    def test_t3_unversioned_bucket_is_not_copied_again(self, s3env):
        s3env.writer.put_bucket_versioning(Bucket=ASSET, VersioningConfiguration={"Status": "Suspended"})
        fault = s3env.fault(answer="503", header=False)
        s3env.route_upload_copy()

        assert s3env.upload_copy() is True

        # A suspended bucket overwrites the null version, so the copies are counted instead.
        assert fault.sent <= 1
        head = s3env.writer.head_object(Bucket=ASSET, Key=FINAL_KEY)
        assert len(head["Metadata"].get(TOKEN_KEY, "")) == 32


@pytest.mark.unit
class TestInVpcCrossRegionStream:
    def test_t4b_a_landed_put_is_not_written_twice_and_the_source_is_read_once(self, s3env, monkeypatch):
        monkeypatch.setenv("VAMS_LAMBDAS_IN_VPC", "true")
        s3env.fault(operation="PutObject", answer="503", header=True)
        source_reads = s3env.record(OTHER_REGION)
        s3env.route_upload_copy()

        assert s3env.upload_copy(source_bucket=REMOTE_STAGING) is True

        written = s3env.upload_versions()
        assert len(written) == 1
        assert len(written[0][1].get(TOKEN_KEY, "")) == 32
        gets = [op for recorder in source_reads for _, op in recorder.seen if op == "GetObject"]
        assert len(gets) <= 1, f"the source was read {len(gets)} times"
        body = s3env.writer.get_object(Bucket=ASSET, Key=FINAL_KEY)["Body"].read()
        assert body == BODY


# --------------------------------------------------------------------------------------------- #
# A copy that did not land is sent again, within botocore's budget and schedule
# --------------------------------------------------------------------------------------------- #

@pytest.mark.unit
class TestACopyThatDidNotLand:
    def test_t2_is_copied_again_and_lands_once(self, s3env):
        fault = s3env.fault(answer="503", store=False)

        version_id = s3env.helper_copy()

        assert [v["VersionId"] for v in s3env.versions()] == [version_id]
        assert 2 <= fault.sent <= s3env.shipped.SINGLE_REQUEST_COPY_MAX_ATTEMPTS
        assert s3env.sleeps, "the second attempt was sent without botocore's wait"
        assert s3env.sleeps[0] <= 1

    def test_t3c_never_lands_is_bounded_and_raises(self, s3env):
        fault = s3env.fault(answer="503-unavailable", store=False, times=99)

        with pytest.raises(ClientError) as raised:
            s3env.helper_copy()

        assert raised.value.response["Error"]["Code"] == "ServiceUnavailable"
        assert 1 <= fault.sent <= s3env.shipped.SINGLE_REQUEST_COPY_MAX_ATTEMPTS <= 6
        assert sum(s3env.sleeps) <= SCHEDULE_BOUND_SECONDS
        assert s3env.versions() == []

    def test_t3c_the_waits_follow_the_schedule_at_its_largest(self, s3env):
        s3env.fault(answer="503-unavailable", store=False, times=99)
        with pytest.raises(ClientError):
            s3env.shipped.copy_s3_object_repeat_safe(
                STAGING, STAGED_KEY, ASSET, FINAL_KEY,
                extra_args={"Metadata": {TOKEN_KEY: "t" * 32}, "MetadataDirective": "REPLACE"},
                write_token="t" * 32, source_size=len(BODY), sleep=s3env.sleeps.append, rng=lambda: 1.0)
        assert s3env.sleeps, "no wait was recorded"
        for attempt, waited in enumerate(s3env.sleeps):
            assert waited <= min(2 ** attempt, 20)
        assert sum(s3env.sleeps) <= SCHEDULE_BOUND_SECONDS

    def test_t3c_through_upload_returns_false(self, s3env):
        s3env.fault(answer="503-unavailable", store=False, times=99)
        s3env.route_upload_copy()

        assert s3env.upload_copy() is False
        assert s3env.versions() == []

    def test_t8_refused_checks_are_not_confirmed_and_end_with_the_budget(self, s3env):
        s3env.writer.put_object(Bucket=ASSET, Key=FINAL_KEY, Body=b"earlier", Metadata={"writer": "earlier"})
        fault = s3env.fault(answer="503-unavailable", store=False, times=99, header=True,
                            header_version="3HL4kqtJlcpXroDTDmJ.rmSpXd3dIbrHY")
        s3env.refuse_version_reads()
        warning = MagicMock()
        s3env.monkeypatch.setattr(s3env.shipped.logger, "warning", warning)

        with pytest.raises(ClientError):
            s3env.helper_copy()

        assert 1 <= fault.sent <= 6
        assert warning.called
        assert any(FINAL_KEY in str(call.args) for call in warning.call_args_list)

    def test_t8_a_refused_check_degrades_to_copying_again(self, s3env):
        # The upload's version landed, another writer's is current, and the version reads are refused:
        # nothing can confirm the landing, so the copy is sent again (today's behaviour, not a loop).
        fault = s3env.fault(answer="503", header=True, burst=1)
        s3env.refuse_version_reads()

        s3env.helper_copy()

        assert 2 <= fault.sent <= s3env.shipped.SINGLE_REQUEST_COPY_MAX_ATTEMPTS


@pytest.mark.unit
class TestNonRetryableErrors:
    def test_t6_access_denied_fails_at_once_without_a_check(self, s3env):
        fault = s3env.fault(answer="403", store=False, times=99)
        reads = s3env.record(REGION)[0]

        with pytest.raises(ClientError) as raised:
            s3env.helper_copy()

        assert raised.value.response["Error"]["Code"] == "AccessDenied"
        assert fault.sent <= 1
        assert s3env.sleeps == []
        assert [op for _, op in reads.seen if op in ("HeadObject", "ListObjectVersions")] == []

    def test_t6_a_missing_source_fails_at_once(self, s3env):
        token = "n" * 32
        with pytest.raises(ClientError) as raised:
            s3env.shipped.copy_s3_object_repeat_safe(
                STAGING, "temp/up-1/missing.glb", ASSET, FINAL_KEY,
                extra_args={"Metadata": {TOKEN_KEY: token}, "MetadataDirective": "REPLACE"},
                write_token=token, source_size=10, sleep=s3env.sleeps.append)
        assert raised.value.response["Error"]["Code"] == "NoSuchKey"
        assert s3env.sleeps == []


# --------------------------------------------------------------------------------------------- #
# Paths: destination Region, the multipart threshold, and the callers that keep the managed copy
# --------------------------------------------------------------------------------------------- #

@pytest.mark.unit
class TestPaths:
    def test_t4_cross_region_outside_the_vpc_copies_and_checks_in_the_destination_region(self, s3env):
        s3env.fault(answer="503", header=True)
        source_side = s3env.record(OTHER_REGION)
        dest_side = s3env.record(REGION)
        token = "x" * 32

        s3env.shipped.copy_s3_object_repeat_safe(
            REMOTE_STAGING, STAGED_KEY, ASSET, FINAL_KEY,
            extra_args={"Metadata": {TOKEN_KEY: token}, "MetadataDirective": "REPLACE"},
            write_token=token, source_size=len(BODY), sleep=s3env.sleeps.append, rng=lambda: 0.5)

        dest_ops = {op for recorder in dest_side for _, op in recorder.seen}
        assert {"CopyObject", "HeadObject"} <= dest_ops
        assert [entry for recorder in source_side for entry in recorder.seen] == []
        assert len(s3env.versions()) == 1

    def test_t5_at_the_threshold_it_is_the_managed_copy_and_still_stamped(self, s3env):
        size = s3env.shipped.SINGLE_REQUEST_COPY_MAX_BYTES
        s3env.writer.put_object(Bucket=STAGING, Key=STAGED_KEY, Body=b"\x01" * size)
        s3env.monkeypatch.setattr(s3env.shipped, "s3_single_attempt_client_for_region",
                                  MagicMock(side_effect=AssertionError("the one-attempt client was used")))
        token = "m" * 32

        assert s3env.shipped.copy_s3_object_repeat_safe(
            STAGING, STAGED_KEY, ASSET, FINAL_KEY,
            extra_args={"Metadata": {TOKEN_KEY: token}, "MetadataDirective": "REPLACE"},
            write_token=token, source_size=size) is None

        assert len(s3env.versions()) == 1
        head = s3env.writer.head_object(Bucket=ASSET, Key=FINAL_KEY)
        assert head["Metadata"][TOKEN_KEY] == token
        assert head["ContentLength"] == size

    @pytest.mark.parametrize("size", [None, "10"])
    def test_an_unknown_size_is_the_managed_copy(self, s3env, size):
        s3env.monkeypatch.setattr(s3env.shipped, "copy_s3_object", MagicMock())
        token = "u" * 32
        extra_args = {"Metadata": {TOKEN_KEY: token}, "MetadataDirective": "REPLACE"}

        assert s3env.shipped.copy_s3_object_repeat_safe(
            STAGING, STAGED_KEY, ASSET, FINAL_KEY, extra_args=extra_args, write_token=token,
            source_size=size) is None

        s3env.shipped.copy_s3_object.assert_any_call(STAGING, STAGED_KEY, ASSET, FINAL_KEY,
                                                     extra_args=extra_args, source_version_id=None)

    def test_t10_the_routing_client_copies_keep_the_managed_copy(self, s3env):
        managed = MagicMock()
        s3env.monkeypatch.setattr(s3env.shipped, "copy_s3_object", managed)
        s3env.monkeypatch.setattr(s3env.shipped, "copy_s3_object_repeat_safe",
                                  MagicMock(side_effect=AssertionError("repeat-safe copy used")))
        router = s3env.shipped.region_routing_s3_client()
        resource = s3env.shipped.region_routing_s3_resource()

        router.copy(CopySource={"Bucket": STAGING, "Key": STAGED_KEY}, Bucket=ASSET, Key="k1")
        router.copy_object(CopySource={"Bucket": STAGING, "Key": STAGED_KEY}, Bucket=ASSET, Key="k2")
        resource.Object(ASSET, "k3").copy({"Bucket": STAGING, "Key": STAGED_KEY})

        assert {call.args[3] for call in managed.call_args_list} == {"k1", "k2", "k3"}
        assert s3env.shipped._s3_single_attempt_clients_by_region == {}

    def test_t10_a_routing_client_copy_runs_on_the_retrying_client(self, s3env):
        fault = WriteFault(answer="503-unavailable", store=False)
        retrying = s3env.shipped.s3_client_for_region(REGION)
        retrying.meta.events.register_first("before-send.s3.CopyObject", fault)
        retrying.meta.events.register_last("before-send", _ResumeMoto())
        router = s3env.shipped.region_routing_s3_client()

        router.copy(CopySource={"Bucket": STAGING, "Key": STAGED_KEY}, Bucket=ASSET, Key=FINAL_KEY)

        # The managed copy keeps the SDK retries (no token, no check): botocore sent the copy again.
        assert fault.sent >= 2
        assert len(s3env.versions()) == 1
        assert s3env.shipped._s3_single_attempt_clients_by_region == {}

    def test_t11_the_threshold_is_s3transfers(self, s3env):
        assert s3env.shipped.SINGLE_REQUEST_COPY_MAX_BYTES == TransferConfig().multipart_threshold
        mock = _load_by_path("_mock_common_s3_repeat_safe", MOCK_PATH)
        assert mock.SINGLE_REQUEST_COPY_MAX_BYTES == s3env.shipped.SINGLE_REQUEST_COPY_MAX_BYTES


# --------------------------------------------------------------------------------------------- #
# The token, the refusal, and the parity with botocore's retry policy
# --------------------------------------------------------------------------------------------- #

@pytest.mark.unit
class TestTokenAndMetadata:
    def test_t9_the_token_is_on_the_object_and_is_system_owned(self, s3env):
        s3env.route_upload_copy()

        assert s3env.upload_copy() is True

        head = s3env.writer.head_object(Bucket=ASSET, Key=FINAL_KEY)
        assert len(head["Metadata"][TOKEN_KEY]) == 32
        assert keys.is_system_metadata_key(TOKEN_KEY)
        assert TOKEN_KEY not in keys.SEARCHABLE_VAMS_METADATA_KEYS

    def test_t9_each_copy_gets_its_own_token(self, s3env):
        s3env.route_upload_copy()

        assert s3env.upload_copy() is True
        assert s3env.upload_copy() is True

        tokens = {metadata.get(TOKEN_KEY) for _, metadata in s3env.upload_versions()}
        assert len(tokens) == 2 and None not in tokens

    def test_t9_the_token_counts_toward_the_2_kb_limit(self, s3env):
        # Metadata 40 bytes under the limit fits; with the 52-byte token it does not, and the copy is
        # refused before any request.
        copy = MagicMock()
        s3env.monkeypatch.setattr(uf, "copy_s3_object_repeat_safe", copy)
        base = {keys.DATABASE_ID_METADATA_KEY: "db1", keys.ASSET_ID_METADATA_KEY: "a1"}
        filler = keys.S3_USER_METADATA_LIMIT_BYTES - 40 - keys.user_metadata_size(base) - len("x-fill")
        extra = {"x-fill": "f" * filler}
        assert keys.user_metadata_size({**base, **extra}) == keys.S3_USER_METADATA_LIMIT_BYTES - 40

        assert uf.copy_s3_object(STAGING, STAGED_KEY, ASSET, FINAL_KEY, "db1", "a1", extra_metadata=extra,
                                 source_head={"ContentLength": 10}) is False

        assert not copy.called
        with pytest.raises(keys.UserMetadataTooLargeError):
            keys.replace_metadata_copy_args({}, {**base, **extra, TOKEN_KEY: "t" * 32})

    def test_t9_the_largest_valid_upload_metadata_fits_with_the_token(self):
        # The id validators' maxima (ASCII: botocore refuses non-ASCII metadata): databaseId 63,
        # assetId 256, user id 256, workflowId 63, workflow execution id 36.
        metadata = {keys.DATABASE_ID_METADATA_KEY: "d" * 63, keys.ASSET_ID_METADATA_KEY: "a" * 256,
                    **uf.build_workflow_change_metadata("u" * 256, "w" * 63, "e" * 36),
                    TOKEN_KEY: "t" * 32}
        assert keys.user_metadata_size(metadata) <= 900 < keys.S3_USER_METADATA_LIMIT_BYTES
        keys.replace_metadata_copy_args({}, metadata)

    @pytest.mark.parametrize("extra_args, token", [
        ({"Metadata": {TOKEN_KEY: "t1"}}, "t1"),                                  # no REPLACE
        ({"Metadata": {TOKEN_KEY: "t1"}, "MetadataDirective": "COPY"}, "t1"),
        ({"Metadata": {}, "MetadataDirective": "REPLACE"}, "t1"),                  # no token
        ({"Metadata": {TOKEN_KEY: "t2"}, "MetadataDirective": "REPLACE"}, "t1"),   # another token
        ({"Metadata": {TOKEN_KEY: ""}, "MetadataDirective": "REPLACE"}, ""),       # empty token
        ({"MetadataDirective": "REPLACE"}, "t1"),
        (None, "t1"),
    ])
    def test_a_copy_without_its_token_is_refused(self, s3env, extra_args, token):
        with pytest.raises(ValueError):
            s3env.shipped.copy_s3_object_repeat_safe(STAGING, STAGED_KEY, ASSET, FINAL_KEY,
                                                     extra_args=extra_args, write_token=token, source_size=10)
        assert s3env.versions() == []

    def test_the_error_response_names_its_version_in_the_parsed_headers(self, s3env):
        # Step 1 of the check reads ResponseMetadata.HTTPHeaders of a ClientError.
        s3env.fault(answer="503", header=True)
        client = s3env.shipped.s3_single_attempt_client_for_region(REGION)
        with pytest.raises(ClientError) as raised:
            client.copy_object(CopySource={"Bucket": STAGING, "Key": STAGED_KEY}, Bucket=ASSET, Key=FINAL_KEY)
        assert raised.value.response["ResponseMetadata"]["HTTPHeaders"].get("x-amz-version-id")
        assert raised.value.response["ResponseMetadata"]["HTTPStatusCode"] == 503


@pytest.mark.unit
class TestBotocoreParity:
    """The loop retries what the asset client's SDK retries did, on the same schedule. A botocore
    upgrade that changes either turns these red, and the constants in common/s3.py are re-mirrored."""

    def test_the_retryable_sets_mirror_the_pinned_standard_checker(self, s3env):
        from botocore.retries import standard
        shipped = s3env.shipped
        transient = standard.TransientRetryableChecker
        assert shipped._RETRYABLE_TRANSIENT_ERROR_CODES == set(transient._TRANSIENT_ERROR_CODES)
        assert shipped._RETRYABLE_TRANSIENT_STATUS_CODES == set(transient._TRANSIENT_STATUS_CODES)
        assert shipped._RETRYABLE_THROTTLING_ERROR_CODES == set(
            standard.ThrottledRetryableChecker._THROTTLED_ERROR_CODES)

    def test_the_backoff_is_botocores(self, s3env):
        from botocore.configprovider import NEW_RETRIES_ENABLED
        from botocore.retries import standard
        if NEW_RETRIES_ENABLED:
            pytest.skip("botocore's opt-in AWS_NEW_RETRIES_2026 schedule is not the default this mirrors")
        for value in (0.0, 0.37, 1.0):
            backoff = standard.ExponentialBackoff(random=lambda value=value: value)
            for attempt in range(8):
                context = MagicMock(attempt_number=attempt + 1)
                assert s3env.shipped.single_request_copy_backoff(attempt, lambda value=value: value) == \
                    pytest.approx(backoff.delay_amount(context))

    def test_the_attempt_budget_is_the_asset_clients(self, s3env):
        resolved = s3env.shipped.s3_client_for_region(REGION).meta.config.retries
        assert s3env.shipped.SINGLE_REQUEST_COPY_MAX_ATTEMPTS == resolved["total_max_attempts"]
        single = s3env.shipped.s3_single_attempt_client_for_region(REGION).meta.config
        assert single.retries.get("total_max_attempts") == 1
        assert single.retries.get("mode") == "adaptive"
        assert single.signature_version == "s3v4"
        assert single.s3.get("addressing_style") == "path"

    @pytest.mark.parametrize("error, retryable", [
        (ClientError({"Error": {"Code": "SlowDown"}, "ResponseMetadata": {"HTTPStatusCode": 503}}, "CopyObject"), True),
        (ClientError({"Error": {"Code": "503"}, "ResponseMetadata": {"HTTPStatusCode": 503}}, "CopyObject"), True),
        (ClientError({"Error": {"Code": "InternalError"}, "ResponseMetadata": {"HTTPStatusCode": 500}}, "CopyObject"),
         True),
        (ClientError({"Error": {"Code": "RequestTimeout"}, "ResponseMetadata": {"HTTPStatusCode": 400}}, "CopyObject"),
         True),
        (ReadTimeoutError(endpoint_url="https://s3", error="timed out"), True),
        (EndpointConnectionError(endpoint_url="https://s3"), True),
        (ClientError({"Error": {"Code": "AccessDenied"}, "ResponseMetadata": {"HTTPStatusCode": 403}}, "CopyObject"),
         False),
        (ClientError({"Error": {"Code": "NoSuchKey"}, "ResponseMetadata": {"HTTPStatusCode": 404}}, "CopyObject"),
         False),
        (ClientError({"Error": {"Code": "InvalidRequest"}, "ResponseMetadata": {"HTTPStatusCode": 400}}, "CopyObject"),
         False),
        (ValueError("not an AWS error"), False),
    ])
    def test_the_classifier(self, s3env, error, retryable):
        assert s3env.shipped.is_retryable_write_error(error) is retryable
        mock = _load_by_path("_mock_common_s3_classifier", MOCK_PATH)
        assert mock.is_retryable_write_error(error) is retryable
