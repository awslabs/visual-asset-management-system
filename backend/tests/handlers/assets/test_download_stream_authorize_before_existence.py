# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Download and both stream routes decide authorization before existence and the distributable flag.

A caller refused at Tier 1 or Tier 2 gets the same 403 whether the asset exists, is missing, or is
not distributable; Tier 2 decides a missing asset on the request's ids. An authorized caller gets
404 for a missing asset and the route's own answer for a non-distributable one. Each surface the
three handlers serve is covered: download POST, stream GET and HEAD, auxiliary preview GET and HEAD.
"""

import importlib.util
import json
import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("S3_ASSET_BUCKETS_STORAGE_TABLE_NAME", "test-buckets-table")
os.environ.setdefault("S3_ASSET_AUXILIARY_BUCKET", "test-asset-auxiliary-bucket")
os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("PRESIGNED_URL_TIMEOUT_SECONDS", "86400")
os.environ.setdefault("AWS_REGION", "us-east-1")

_HANDLERS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "assets"
)

_DB = "db1"
_ASSET = "asset1"
_NOT_AUTHORIZED = {"message": "Not Authorized"}

_modules = {}


def _load(file_name):
    """Load the real handler module by file path with boto3, Casbin and claims stubbed."""
    if file_name in _modules:
        return _modules[file_name]

    stub_names = ("handlers.assets.assetVersions", "handlers.authz", "handlers.auth")
    saved = {name: sys.modules.get(name) for name in stub_names}
    versions_stub = types.ModuleType("handlers.assets.assetVersions")
    versions_stub.resolve_file_version_from_asset_version = MagicMock(return_value=None)
    versions_stub.resolve_asset_version_id_from_alias = MagicMock(return_value=None)
    sys.modules["handlers.assets.assetVersions"] = versions_stub
    authz_stub = types.ModuleType("handlers.authz")
    authz_stub.CasbinEnforcer = MagicMock()
    sys.modules["handlers.authz"] = authz_stub
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": ["test-user"], "roles": []})
    sys.modules["handlers.auth"] = auth_stub

    try:
        with patch("boto3.client", return_value=MagicMock()), patch(
            "boto3.resource", return_value=MagicMock()
        ):
            spec = importlib.util.spec_from_file_location(
                f"{file_name[:-3]}_authorize_before_existence_under_test",
                os.path.abspath(os.path.join(_HANDLERS_DIR, file_name)),
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
    _modules[file_name] = module
    return module


def _download_event():
    path = f"/database/{_DB}/assets/{_ASSET}/download"
    return {
        "resource": "/database/{databaseId}/assets/{assetId}/download",
        "path": path,
        "httpMethod": "POST",
        "headers": {},
        "pathParameters": {"databaseId": _DB, "assetId": _ASSET},
        "queryStringParameters": None,
        "requestContext": {"identity": {"sourceIp": "10.0.0.7"}, "path": path, "httpMethod": "POST"},
        "body": json.dumps({"downloadType": "assetFile", "key": "/model.glb"}),
    }


def _stream_event(route, proxy, method):
    path = f"/database/{_DB}/assets/{_ASSET}/{route}/{proxy}"
    return {
        "resource": f"/database/{{databaseId}}/assets/{{assetId}}/{route}/{{proxy+}}",
        "path": path,
        "httpMethod": method,
        "headers": {"Accept": "*/*"},
        "pathParameters": {"databaseId": _DB, "assetId": _ASSET, "proxy": proxy},
        "queryStringParameters": None,
        "requestContext": {"identity": {"sourceIp": "10.0.0.7"}, "path": path, "httpMethod": method},
        "body": None,
    }


# (surface id, handler file, the module's S3 client attribute, event factory)
SURFACES = [
    ("download-POST", "downloadAsset.py", "s3", _download_event),
    ("stream-GET", "streamAsset.py", "s3_client",
     lambda: _stream_event("download/stream", "scans/pump.glb", "GET")),
    ("stream-HEAD", "streamAsset.py", "s3_client",
     lambda: _stream_event("download/stream", "scans/pump.glb", "HEAD")),
    ("auxiliary-GET", "streamAuxiliaryPreviewAsset.py", "s3_client",
     lambda: _stream_event("auxiliaryPreviewAssets/stream", "scans/pump.e57/preview/r/octree.bin", "GET")),
    ("auxiliary-HEAD", "streamAuxiliaryPreviewAsset.py", "s3_client",
     lambda: _stream_event("auxiliaryPreviewAssets/stream", "scans/pump.e57/preview/r/octree.bin", "HEAD")),
]

# The answer an authorized caller gets for an asset that is not distributable, per handler.
NOT_DISTRIBUTABLE = {
    "downloadAsset.py": (400, "not distributable"),
    "streamAsset.py": (403, "Asset not distributable"),
    "streamAuxiliaryPreviewAsset.py": (403, "not currently allowing file distribution"),
}

# The 404 body an authorized caller gets for a missing asset, per handler. Download keeps the text
# it answered before (the CLI reads a 404 that names "database" as a missing database).
NOT_FOUND = {
    "downloadAsset.py": {"message": "Asset not found"},
    "streamAsset.py": {"message": "Asset not found in database"},
    "streamAuxiliaryPreviewAsset.py": {"message": "Asset not found in database"},
}

# What the asset table holds for the request, per scenario.
RECORDS = {
    "missing": None,
    "existing": {"isDistributable": True},
    "not-distributable": {"isDistributable": False},
}


def _record(kind):
    fields = RECORDS[kind]
    if fields is None:
        return None
    return {
        "databaseId": _DB, "assetId": _ASSET, "bucketId": "bucket-1",
        "assetLocation": {"Key": f"{_ASSET}/"}, "previewLocation": {"Key": f"{_ASSET}/preview.png"},
        **fields,
    }


def _wire(m, s3_attr, record, api_allowed=True, object_allowed=True):
    """Point the module at a scripted asset record, S3 client and enforcer; return (s3, enforcer)."""
    from common.auth.apiEvent import normalize_event

    def _claims(event):
        normalize_event(event)
        return {"tokens": ["test-user"], "roles": [], "mfaEnabled": False}

    m.request_to_claims = MagicMock(side_effect=_claims)
    enforcer = MagicMock()
    enforcer.return_value.enforceAPI.return_value = api_allowed
    enforcer.return_value.enforce.return_value = object_allowed
    m.CasbinEnforcer = enforcer
    m.get_asset_details = MagicMock(return_value=record)
    m.get_default_bucket_details = MagicMock(return_value={
        "bucketId": "bucket-1", "bucketName": "test-bucket", "baseAssetsPrefix": "",
    })
    m.check_quarantine_block = MagicMock()
    m.validateUnallowedFileExtensionAndContentType = MagicMock(return_value=True)
    for name in ("log_file_download", "log_file_download_bulk", "log_file_download_streamed"):
        if hasattr(m, name):
            setattr(m, name, MagicMock())

    s3 = MagicMock()
    s3.head_object.return_value = {"ContentType": "model/gltf-binary", "ContentLength": 10}
    body = MagicMock()
    body.read.return_value = b"x" * 10
    s3.get_object.return_value = {
        "ContentType": "model/gltf-binary", "ContentLength": 10, "Body": body,
        "ResponseMetadata": {"HTTPHeaders": {
            "accept-ranges": "bytes", "content-type": "model/gltf-binary", "content-length": "10",
        }},
    }
    s3.generate_presigned_url.return_value = "https://example-bucket.s3.amazonaws.com/signed"
    setattr(m, s3_attr, s3)
    return s3, enforcer


def _call(file_name, s3_attr, event_factory, record_kind, **decisions):
    m = _load(file_name)
    s3, enforcer = _wire(m, s3_attr, _record(record_kind), **decisions)
    response = m.lambda_handler(event_factory(), None)
    body = json.loads(response["body"]) if response.get("body") else {}
    return response, body, s3, enforcer, m


def _refused_by(tier):
    return {"api_allowed": tier != 1, "object_allowed": tier != 2}


@pytest.mark.unit
@pytest.mark.parametrize("surface, file_name, s3_attr, event_factory", SURFACES,
                         ids=[s[0] for s in SURFACES])
class TestRefusedCaller:
    @pytest.mark.parametrize("tier", [1, 2])
    @pytest.mark.parametrize("record_kind", sorted(RECORDS))
    def test_refused_caller_gets_the_generic_403_whatever_the_asset_state(
            self, surface, file_name, s3_attr, event_factory, record_kind, tier):
        response, body, s3, _enforcer, m = _call(
            file_name, s3_attr, event_factory, record_kind, **_refused_by(tier))
        assert response["statusCode"] == 403
        assert body == _NOT_AUTHORIZED
        s3.get_object.assert_not_called()
        s3.head_object.assert_not_called()
        s3.generate_presigned_url.assert_not_called()
        m.check_quarantine_block.assert_not_called()

    def test_a_missing_asset_is_decided_on_the_request_ids(
            self, surface, file_name, s3_attr, event_factory):
        _response, _body, _s3, enforcer, _m = _call(
            file_name, s3_attr, event_factory, "missing", object_allowed=False)
        checked = [c.args[0] for c in enforcer.return_value.enforce.call_args_list]
        assert {"databaseId": _DB, "assetId": _ASSET, "object__type": "asset"} in checked


@pytest.mark.unit
@pytest.mark.parametrize("surface, file_name, s3_attr, event_factory", SURFACES,
                         ids=[s[0] for s in SURFACES])
class TestAuthorizedCaller:
    def test_missing_asset_is_404(self, surface, file_name, s3_attr, event_factory):
        response, body, s3, _enforcer, _m = _call(file_name, s3_attr, event_factory, "missing")
        assert response["statusCode"] == 404
        assert body == NOT_FOUND[file_name]
        s3.generate_presigned_url.assert_not_called()

    def test_not_distributable_keeps_the_route_answer(self, surface, file_name, s3_attr, event_factory):
        response, body, s3, _enforcer, _m = _call(
            file_name, s3_attr, event_factory, "not-distributable")
        status, fragment = NOT_DISTRIBUTABLE[file_name]
        assert response["statusCode"] == status
        assert fragment in body["message"]
        s3.generate_presigned_url.assert_not_called()

    def test_existing_asset_is_decided_on_the_stored_record(
            self, surface, file_name, s3_attr, event_factory):
        response, _body, _s3, enforcer, _m = _call(file_name, s3_attr, event_factory, "existing")
        assert response["statusCode"] in (200, 307)
        checked = [c.args[0] for c in enforcer.return_value.enforce.call_args_list]
        assert {**_record("existing"), "object__type": "asset"} in checked
