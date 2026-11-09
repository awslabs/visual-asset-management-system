# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Test harness for the Download as Zip handlers.

Loads the REAL handler modules by path (the root conftest shadows the handlers package with mocks)
with boto3 patched, the real shared modules they need installed in sys.modules, and the auth modules
replaced by switchable stubs. Every AWS dependency is an in-memory double defined here, so the tests
exercise the real conditional-write, paging and cleanup logic rather than asserting on mock calls.
"""

import base64
import importlib.util
import io
import json
import os
import re
import sys
import types
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from botocore.exceptions import ClientError

# Env read at handler import time (module-level seeding is fine per tests/CLAUDE.md).
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("ZIP_DOWNLOAD_REQUESTS_STORAGE_TABLE_NAME", "zipDownloadRequestsTable")
os.environ.setdefault("WORKFLOW_EXECUTION_STORAGE_TABLE_V2_NAME", "workflowExecutionsV2Table")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "assetStorageTable")
os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "s3AssetBucketsStorageTable")
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-asset-auxiliary-bucket")

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "backend"))
AUX = "test-asset-auxiliary-bucket"
ASSET_BUCKET = "asset-bucket"


def _client_error(code, op="Op", headers=None):
    response = {"Error": {"Code": code, "Message": code}}
    if headers:
        response["ResponseMetadata"] = {"HTTPHeaders": dict(headers)}
    return ClientError(response, op)


def _load(name, rel_path):
    spec = importlib.util.spec_from_file_location(name, os.path.join(BACKEND, rel_path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# DynamoDB double
# ---------------------------------------------------------------------------

def _key_value(condition):
    """The value of a boto3 Key(...).eq(...) condition."""
    return condition.get_expression()["values"][1]


class FakeTable:
    """A dict-backed table that evaluates the expressions the zip handlers write."""

    def __init__(self, hash_key, range_key=None, indexes=None):
        self.hash_key, self.range_key = hash_key, range_key
        self.indexes = indexes or {}
        self.items = {}
        self.writes = []

    def _k(self, key):
        return (key[self.hash_key], key.get(self.range_key) if self.range_key else None)

    def get_item(self, Key, ConsistentRead=False, **_):
        item = self.items.get(self._k(Key))
        return {"Item": json.loads(json.dumps(item))} if item else {}

    def put_item(self, Item, ConditionExpression=None, **_):
        if ConditionExpression and "attribute_not_exists" in ConditionExpression \
                and self._k(Item) in self.items:
            raise _client_error("ConditionalCheckFailedException", "PutItem")
        self.items[self._k(Item)] = json.loads(json.dumps(Item))
        self.writes.append(("put", Item))

    def delete_item(self, Key, ConditionExpression=None, ExpressionAttributeNames=None,
                    ExpressionAttributeValues=None, **_):
        cond = ConditionExpression or ""
        row = self.items.get(self._k(Key))
        names, values = ExpressionAttributeNames or {}, ExpressionAttributeValues or {}
        ok = not ("attribute_exists" in cond and row is None)
        m = re.search(r"(#?\w+) = (:\w+)", cond)
        if ok and row is not None and m:
            ok = row.get(names.get(m.group(1), m.group(1))) == values[m.group(2)]
        if ok and row is not None and "attribute_not_exists(workflowExecutionId)" in cond:
            ok = not row.get("workflowExecutionId")
        if not ok:
            raise _client_error("ConditionalCheckFailedException", "DeleteItem")
        self.items.pop(self._k(Key), None)
        self.writes.append(("delete", Key))

    def update_item(self, Key, UpdateExpression, ConditionExpression=None,
                    ExpressionAttributeNames=None, ExpressionAttributeValues=None, **_):
        names = ExpressionAttributeNames or {}
        values = ExpressionAttributeValues or {}
        row = self.items.get(self._k(Key))
        cond = ConditionExpression or ""

        def attr(token):
            return names.get(token, token)

        ok = True
        if "attribute_exists(requestId)" in cond and row is None:
            ok = False
        if ok and row is not None:
            m = re.search(r"(#?\w+) IN \(([^)]*)\)", cond)
            if m:
                allowed = [values[v.strip()] for v in m.group(2).split(",")]
                ok = row.get(attr(m.group(1))) in allowed
            m = re.search(r"workflowExecutionId = (:\w+)", cond)
            if ok and m and "attribute_not_exists(workflowExecutionId)" not in cond:
                ok = row.get("workflowExecutionId") == values[m.group(1)]
            m = re.search(r"runCheckedAt < (:\w+)", cond)
            if ok and m and row.get("runCheckedAt") is not None:
                ok = row["runCheckedAt"] < values[m.group(1)]
            if ok and "attribute_not_exists(cancelRequestedAt)" in cond:
                ok = not row.get("cancelRequestedAt")
        if row is None and cond:
            ok = False
        if not ok:
            raise _client_error("ConditionalCheckFailedException", "UpdateItem")
        row = row or dict(Key)
        assignments = UpdateExpression.replace("SET ", "", 1).split(",")
        for assignment in assignments:
            left, right = [p.strip() for p in assignment.split("=")]
            row[attr(left)] = json.loads(json.dumps(values[right]))
        self.items[self._k(Key)] = row
        self.writes.append(("update", Key, UpdateExpression))
        return {}

    def query(self, KeyConditionExpression, IndexName=None, Limit=None, ScanIndexForward=True,
              ExclusiveStartKey=None, FilterExpression=None, **_):
        pk_name = self.indexes.get(IndexName, self.hash_key) if IndexName else self.hash_key
        pk_value = _key_value(KeyConditionExpression)
        rows = [r for r in self.items.values() if r.get(pk_name) == pk_value]
        rows.sort(key=lambda r: r.get(self.range_key) or "", reverse=not ScanIndexForward)
        if ExclusiveStartKey:
            start = ExclusiveStartKey[self.range_key]
            rows = [r for r in rows if (r[self.range_key] < start if not ScanIndexForward
                                        else r[self.range_key] > start)]
        page, more = (rows[:Limit], len(rows) > Limit) if Limit else (rows, False)
        if FilterExpression is not None:
            expr = FilterExpression.get_expression()
            allowed = expr["values"][1]
            page_out = [r for r in page if r.get("status") in allowed]
        else:
            page_out = page
        response = {"Items": json.loads(json.dumps(page_out))}
        if more:
            last = page[-1]
            lek = {self.hash_key: last[self.hash_key], self.range_key: last[self.range_key]}
            if IndexName:
                lek[pk_name] = last[pk_name]
            response["LastEvaluatedKey"] = lek
        return response


# ---------------------------------------------------------------------------
# S3 and Lambda doubles
# ---------------------------------------------------------------------------

class FakeS3:
    def __init__(self):
        # (bucket, key) -> list of versions, newest first:
        #   {"VersionId", "Size", "ContentType", "StorageClass", "Metadata", "DeleteMarker"}
        self.objects = {}
        self.uploads = []  # {"Bucket", "Key", "UploadId"}
        self.aborted = []
        self.puts = {}
        self.presigned = []

    def add(self, key, size=10, version="v1", bucket=ASSET_BUCKET, content_type="model/gltf-binary",
            storage_class="STANDARD", metadata=None, delete_marker=False):
        self.objects.setdefault((bucket, key), []).insert(0, {
            "VersionId": version, "Size": size, "ContentType": content_type,
            "StorageClass": storage_class, "Metadata": metadata or {}, "DeleteMarker": delete_marker})

    def head_object(self, Bucket, Key, VersionId=None):
        versions = self.objects.get((Bucket, Key))
        if not versions:
            raise _client_error("404", "HeadObject")
        chosen = versions[0] if not VersionId else next(
            (v for v in versions if v["VersionId"] == VersionId), None)
        if chosen is None:
            raise _client_error("404", "HeadObject")
        if chosen["DeleteMarker"]:
            # Amazon S3 marks both answers with x-amz-delete-marker: true.
            raise _client_error("405" if VersionId else "404", "HeadObject",
                                {"x-amz-delete-marker": "true"})
        head = {"ContentLength": chosen["Size"], "ContentType": chosen["ContentType"],
                "Metadata": chosen["Metadata"], "VersionId": chosen["VersionId"]}
        if chosen["StorageClass"] != "STANDARD":
            head["StorageClass"] = chosen["StorageClass"]
        return head

    def list_object_versions(self, Bucket, Prefix, MaxKeys=1000, KeyMarker="", VersionIdMarker="", **_):
        versions, markers = [], []
        for (bucket, key), entries in sorted(self.objects.items()):
            if bucket != Bucket or not key.startswith(Prefix):
                continue
            for i, v in enumerate(entries):
                item = {"Key": key, "VersionId": v["VersionId"], "IsLatest": i == 0,
                        "Size": v["Size"], "StorageClass": v["StorageClass"]}
                (markers if v["DeleteMarker"] else versions).append(item)
        return {"Versions": versions, "DeleteMarkers": markers, "IsTruncated": False}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.puts[(Bucket, Key)] = Body
        self.add(Key, size=len(Body), version=f"p{len(self.puts)}", bucket=Bucket,
                 content_type=kwargs.get("ContentType", ""))

    def delete_object(self, Bucket, Key, **_):
        self.objects.pop((Bucket, Key), None)

    def delete_objects(self, Bucket, Delete):
        for obj in Delete["Objects"]:
            self.objects.pop((Bucket, obj["Key"]), None)
        return {}

    def list_multipart_uploads(self, Bucket, Prefix, **_):
        return {"Uploads": [u for u in self.uploads if u["Bucket"] == Bucket
                            and u["Key"].startswith(Prefix)], "IsTruncated": False}

    def abort_multipart_upload(self, Bucket, Key, UploadId):
        self.aborted.append((Bucket, Key, UploadId))
        self.uploads = [u for u in self.uploads if u["UploadId"] != UploadId]

    def list_parts(self, Bucket, Key, UploadId, **_):
        # The abort sweep's check (common/zipDownloads.abort_until_gone, #1125 D16).
        if not any(u["UploadId"] == UploadId and u["Key"] == Key for u in self.uploads):
            raise _client_error("NoSuchUpload", "ListParts")
        return {"Parts": []}

    def generate_presigned_url(self, op, Params, ExpiresIn):
        self.presigned.append((op, Params, ExpiresIn))
        return f"https://signed.example/{Params['Key']}?X-Amz-Signature=secret"


class FakeLambda:
    def __init__(self):
        self.calls = []
        self.responses = {}
        # FunctionName -> callable(event) run before answering (a side effect of the invoked function).
        self.effects = {}

    def invoke(self, FunctionName, InvocationType, Payload):
        event = json.loads(Payload.decode("utf-8"))
        self.calls.append((FunctionName, InvocationType, event))
        if FunctionName in self.effects:
            self.effects[FunctionName](event)
        # The execute handler's success envelope ({"message": ExecuteWorkflowResponseModel}); the
        # execution service's abort and details routes are read for their status code only.
        answer = self.responses.get(FunctionName, (200, {"message": {"executionId": "exec-1"}}))
        # A list answers its entries in turn, repeating the last one.
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        status, body = answer
        return {"Payload": io.BytesIO(json.dumps({"statusCode": status,
                                                  "body": json.dumps(body)}).encode("utf-8"))}


# ---------------------------------------------------------------------------
# Auth stubs and module loading
# ---------------------------------------------------------------------------

class AuthState:
    """Switchable identity and decisions for the stubbed Casbin enforcer."""

    def __init__(self):
        self.user = "alice"
        self.api_allowed = True
        self.denied_assets = set()

    def claims(self, event):
        if self.user is None:
            return {"tokens": [], "roles": []}
        return {"tokens": [self.user], "roles": ["role"], "mfaEnabled": False}


AUTH = AuthState()


class StubEnforcer:
    def __init__(self, claims_and_roles):
        self.claims = claims_and_roles

    def enforceAPI(self, event, *_):
        return AUTH.api_allowed

    def enforce(self, obj, act):
        assert obj.get("object__type") == "asset"
        assert act == "GET"
        return obj.get("assetId") not in AUTH.denied_assets


@contextmanager
def _module_overrides():
    names = ("handlers", "handlers.auth", "handlers.authz", "common.zipDownloads",
             "common.workflows", "common.workflows.systemPipelines")
    saved = {n: sys.modules.get(n) for n in names}
    common_saved = getattr(sys.modules.get("common"), "zipDownloads", None)
    try:
        auth = types.ModuleType("handlers.auth")
        auth.request_to_claims = AUTH.claims
        authz = types.ModuleType("handlers.authz")
        authz.CasbinEnforcer = StubEnforcer
        sys.modules["handlers.auth"] = auth
        sys.modules["handlers.authz"] = authz
        zd = _load("common.zipDownloads", "common/zipDownloads.py")
        setattr(sys.modules["common"], "zipDownloads", zd)
        workflows_pkg = types.ModuleType("common.workflows")
        sys.modules["common.workflows"] = workflows_pkg
        sp = _load("common.workflows.systemPipelines", "common/workflows/systemPipelines.py")
        workflows_pkg.systemPipelines = sp
        # Imported once and kept: pydantic v1 refuses to register a model's validators twice.
        importlib.import_module("models.assetsV3")
        yield
    finally:
        for n, m in saved.items():
            if m is None:
                sys.modules.pop(n, None)
            else:
                sys.modules[n] = m
        if common_saved is not None:
            setattr(sys.modules["common"], "zipDownloads", common_saved)


def load_handlers(enabled=True, env=None):
    """Load a fresh copy of the zip download service (API routes, terminal callback and delayed
    sweep in one handler) wired to new doubles."""
    environ = {"ZIPDOWNLOADS_ENABLED": "true" if enabled else "false",
               "EXECUTE_WORKFLOW_V2_LAMBDA_FUNCTION_NAME": "executeFn",
               "EXECUTION_SERVICE_FUNCTION_NAME": "executionServiceFn",
               "PRESIGNED_URL_TIMEOUT_SECONDS": "900",
               "ZIP_DOWNLOAD_MAX_ACTIVE_PER_USER": "5"}
    environ.update(env or {})
    with patch.dict(os.environ, environ), _module_overrides(), \
            patch("boto3.client", return_value=MagicMock()), \
            patch("boto3.resource", return_value=MagicMock()):
        svc = _load("zipDownloadService_under_test", "handlers/assets/zipDownloadService.py")
    # One function serves the API routes, the terminal callback and the delayed sweep: ``cb`` is
    # the same module, kept as a name so callback tests read as such.
    cb = svc
    env_obj = types.SimpleNamespace(
        svc=svc, cb=cb, s3=FakeS3(), lam=FakeLambda(),
        zip=FakeTable("userId", "requestId", {"UserAssetIndex": "userAssetKey"}),
        assets=FakeTable("databaseId", "assetId"),
        buckets=FakeTable("bucketId", None),
        executions=FakeTable("workflowExecutionId", "workflowDatabaseId:workflowId"),
        audit=MagicMock())
    env_obj.buckets.query = lambda **kw: {"Items": [{"bucketId": "b1", "bucketName": ASSET_BUCKET}]}
    svc.s3 = env_obj.s3
    svc.zip_table = env_obj.zip
    svc.lambda_client = env_obj.lam
    svc.asset_table = env_obj.assets
    svc.buckets_table = env_obj.buckets
    svc.executions_table = env_obj.executions
    svc.log_actions = env_obj.audit
    svc.validateUnallowedFileExtensionAndContentType = blocklist
    env_obj.zd = svc.zd
    # The abort sweep (abort_until_gone, #1125 D16) waits between repeats while a double keeps an
    # upload listed (a 204 answer); the doubles do not need the wait.
    env_obj.zd._sleep = lambda seconds: None
    return env_obj


UPLOAD_TOKEN = "tok-this-job"


def record_upload_token(row):
    """What the container's RUNNING write records before it creates the archive upload."""
    row["archiveUploadToken"] = UPLOAD_TOKEN


def land_archive(env, row, size=10, token=UPLOAD_TOKEN, version="v1"):
    """The archive object this job's upload created: it carries the row's upload token."""
    record_upload_token(row)
    env.s3.add(row["archiveKey"], size=size, bucket=AUX, version=version,
               metadata={env.zd.ARCHIVE_UPLOAD_TOKEN_METADATA: token})


def blocklist(key, content_type):
    """Stand-in for common.s3.validateUnallowedFileExtensionAndContentType: blocks .exe."""
    return not key.lower().endswith(".exe") and content_type != "application/x-msdownload"


def put_asset(env, asset_id="asset1", database_id="db1", distributable=True):
    env.assets.items[(database_id, asset_id)] = {
        "databaseId": database_id, "assetId": asset_id, "assetName": "Engine",
        "isDistributable": distributable, "bucketId": "b1",
        "assetLocation": {"Key": f"x{asset_id}/"}}


def api_event(method, path, path_params=None, body=None, query=None):
    return {"requestContext": {"http": {"method": method, "path": path}},
            "pathParameters": path_params or {},
            "queryStringParameters": query,
            "body": json.dumps(body) if body is not None else None}


def asset_path(database_id="db1", asset_id="asset1", suffix=""):
    return f"/database/{database_id}/assets/{asset_id}/download/zip{suffix}"


def body_of(response):
    return json.loads(response["body"])


def encode_token(key):
    return base64.b64encode(json.dumps(key).encode("utf-8")).decode("utf-8")
