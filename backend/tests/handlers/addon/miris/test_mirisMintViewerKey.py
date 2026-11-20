# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /addon/miris/viewer-key: a key is returned only after both authorization tiers and the
binding check pass, for the Miris asset recorded on the file. perView mints and records a key;
static returns the deployment key. No key material reaches a log, audit entry, row or error."""

import importlib
import json
import logging
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.miris import mintViewerKey, mirisIntegration
from backend.tests.quarantineRefusalBody import assert_is_the_quarantine_refusal

from .conftest import (
    ASSET_ID,
    DATABASE_ID,
    OTHER_UUID,
    STORED_UUID,
    FakeLambdaContext,
    asset_table as _asset_table,
    claims_after_normalizing,
    enforcer as _enforcer,
    metadata_table as _metadata_table,
    rest_event,
    static_viewer_key,
    VIEWER_KEY_SECRET_ARN,
)

pytestmark = pytest.mark.unit

USER_ID = "alice@example.com"
KEY_ID = "3f2b8c1e-9a4d-4e7f-8b6a-1c2d3e4f5a6b"
VIEWER_KEY_MATERIAL = "minted-viewer-key-material-do-not-log"
MIRIS_ERROR_BODY = "miris-error-body-must-not-surface"
ROUTE_PATH = "/addon/miris/viewer-key"
STATIC_VIEWER_KEY = "static-deployment-viewer-key-do-not-log-9e1d"
FIXED_NOW = datetime(2026, 10, 15, 18, 49, 28, 734000, tzinfo=timezone.utc)
FIXED_EPOCH_SECONDS = 1792090168.5
ASSET_TAG = f"vams-asset-{ASSET_ID}"
DATABASE_TAG = f"vams-database-{DATABASE_ID}"
MODES = ["static", "perView"]

_AS_USER = claims_after_normalizing({"tokens": [USER_ID], "roles": []})
_AS_NOBODY = claims_after_normalizing({"tokens": [], "roles": []})


def _event(file_path="/scene.usd", method="POST", path=ROUTE_PATH, body=None, raw_body=None,
           database_id=DATABASE_ID, asset_id=ASSET_ID):
    if raw_body is None:
        payload = {"databaseId": database_id, "assetId": asset_id, "filePath": file_path}
        payload = {k: v for k, v in payload.items() if v is not None}
        payload.update(body or {})
        raw_body = json.dumps(payload)
    return rest_event(method, path, body=raw_body)


def _streamed(uuid=STORED_UUID):
    return _metadata_table({f"{DATABASE_ID}:{ASSET_ID}:/scene.usd": {"miris_asset_uuid": uuid}})


def _asset_read(tags=("vams", ASSET_TAG, DATABASE_TAG)):
    return (200, {"id": STORED_UUID, "state": "streamable", "tags": list(tags)})


BOUND = _asset_read()
CREATED = (200, {"id": KEY_ID, "key": VIEWER_KEY_MATERIAL, "expires_at": "2026-10-15T19:04:28Z"})


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz is not None else FIXED_NOW.replace(tzinfo=None)


_UNSET = object()


def _invoke(event, miris_api, metadata_table=None, asset_table=None, key_table=_UNSET,
            enforcer=None, audit=None, ttl_minutes="15", context=None, mode="perView",
            static_key=STATIC_VIEWER_KEY, claims=_AS_USER):
    with patch.object(mintViewerKey, "asset_file_metadata_table", metadata_table or _streamed()), \
         patch.object(mintViewerKey, "asset_table", asset_table or _asset_table()), \
         patch.object(mintViewerKey, "miris_viewer_key_table", MagicMock() if key_table is _UNSET else key_table), \
         patch.object(mintViewerKey, "miris_viewer_key_mode", mode), \
         static_viewer_key(static_key), \
         patch.object(mintViewerKey, "CasbinEnforcer", return_value=enforcer or _enforcer()), \
         patch.object(mintViewerKey, "request_to_claims", side_effect=claims), \
         patch.object(mintViewerKey, "log_auth_other", audit or MagicMock()), \
         patch.object(mintViewerKey, "viewer_key_ttl_minutes_setting", ttl_minutes), \
         patch.object(mintViewerKey, "datetime", _FixedDatetime), \
         patch.object(mintViewerKey.time, "time", return_value=FIXED_EPOCH_SECONDS):
        response = mintViewerKey.lambda_handler(event, context or FakeLambdaContext())
    return response, json.loads(response["body"])


def _methods(miris_api, method):
    return [c for c in miris_api.calls if c["method"] == method]


def _no_store(response):
    return "no-store" in [d.strip() for d in response["headers"]["Cache-Control"].split(",")]


class TestPerViewMint:
    def test_mints_records_and_audits_one_key_scoped_to_the_files_asset(self, miris_api):
        miris_api.responses.extend([BOUND, CREATED])
        key_table, audit, event = MagicMock(), MagicMock(), _event()

        response, body = _invoke(event, miris_api, key_table=key_table, audit=audit)

        assert response["statusCode"] == 200
        assert _no_store(response)
        assert body == {"filePath": "/scene.usd", "mirisAssetUuid": STORED_UUID,
                        "viewerKey": VIEWER_KEY_MATERIAL, "expiresAt": "2026-10-15T19:04:28Z"}
        read, create = miris_api.calls
        assert (read["method"], read["url"]) == ("GET", f"{miris_api.base_url}/v1/asset/{STORED_UUID}")
        assert read["headers"]["Miris-API-Key"] == miris_api.integration_key
        assert create["json"] == {
            "name": f"vams:{DATABASE_ID}:{ASSET_ID}:1792090168500",
            "tags": [],
            "asset_ids": [STORED_UUID],
            "expires_at": "2026-10-15T19:04:28Z",
        }

        put = key_table.put_item.call_args.kwargs
        expiry_epoch = int(datetime(2026, 10, 15, 19, 4, 28, tzinfo=timezone.utc).timestamp())
        assert put["Item"] == {
            "mirisViewerKeyId": KEY_ID, "databaseId": DATABASE_ID, "assetId": ASSET_ID,
            "filePath": "/scene.usd", "userId": USER_ID, "mirisAssetUuid": STORED_UUID,
            "createdAt": "2026-10-15T18:49:28Z", "expiresAt": "2026-10-15T19:04:28Z",
            "ttl": expiry_epoch + 3600,
        }
        assert "attribute_not_exists" in put["ConditionExpression"]

        audit_event, secondary_type, data = audit.call_args.args
        assert audit_event is event
        assert secondary_type == "mirisViewerKeyMint"
        assert data == {"userId": USER_ID, "databaseId": DATABASE_ID, "assetId": ASSET_ID,
                        "filePath": "/scene.usd", "mirisAssetUuid": STORED_UUID,
                        "mirisViewerKeyId": KEY_ID, "expiresAt": "2026-10-15T19:04:28Z"}
        assert VIEWER_KEY_MATERIAL not in json.dumps([put, audit.call_args.args[1:]])

    @pytest.mark.parametrize("ttl_minutes, expires_at", [("1", "2026-10-15T18:50:28Z"), ("60", "2026-10-15T19:49:28Z")])
    def test_the_expiry_follows_the_configured_ttl(self, miris_api, ttl_minutes, expires_at):
        miris_api.responses.extend([BOUND, CREATED])
        _, body = _invoke(_event(), miris_api, ttl_minutes=ttl_minutes)
        assert body["expiresAt"] == expires_at
        assert _methods(miris_api, "POST")[0]["json"]["expires_at"] == expires_at

    def test_a_client_supplied_uuid_is_ignored(self, miris_api):
        miris_api.responses.extend([BOUND, CREATED])
        _, body = _invoke(_event(body={"mirisAssetUuid": OTHER_UUID}), miris_api)
        assert body["mirisAssetUuid"] == STORED_UUID
        assert _methods(miris_api, "POST")[0]["json"]["asset_ids"] == [STORED_UUID]

    def test_a_long_asset_id_is_hashed_into_a_capped_name(self, miris_api):
        long_asset_id = "a" * 255
        table = _metadata_table({f"{DATABASE_ID}:{long_asset_id}:/scene.usd": {"miris_asset_uuid": STORED_UUID}})
        miris_api.responses.extend([(200, {"state": "x", "tags": [f"vams-asset-{long_asset_id}", DATABASE_TAG]}), CREATED])
        _invoke(_event(asset_id=long_asset_id), miris_api, metadata_table=table)

        name = _methods(miris_api, "POST")[0]["json"]["name"]
        assert len(name) <= mirisIntegration.MAX_VIEWER_KEY_NAME_LENGTH
        assert long_asset_id not in name
        assert name.startswith("vams:") and name.endswith(":1792090168500")
        assert mirisIntegration.viewer_key_name(DATABASE_ID, "a" * 256, 1) != (
            mirisIntegration.viewer_key_name(DATABASE_ID, "a" * 255 + "b", 1))


class TestStaticMode:
    def test_a_bound_asset_returns_the_deployment_key_without_minting(self, miris_api):
        miris_api.responses.append(BOUND)
        key_table, audit, event = MagicMock(), MagicMock(), _event()

        # The static key needs no TTL setting.
        response, body = _invoke(event, miris_api, key_table=key_table, audit=audit, mode="static", ttl_minutes="")

        assert response["statusCode"] == 200
        assert _no_store(response)
        assert body == {"filePath": "/scene.usd", "mirisAssetUuid": STORED_UUID,
                        "viewerKey": STATIC_VIEWER_KEY, "expiresAt": None}
        assert [(c["method"], c["url"]) for c in miris_api.calls] == [
            ("GET", f"{miris_api.base_url}/v1/asset/{STORED_UUID}")]
        assert STATIC_VIEWER_KEY not in json.dumps(miris_api.calls[0]["headers"])
        key_table.put_item.assert_not_called()
        assert audit.call_args.args == (event, "mirisStaticViewerKeyIssue", {
            "userId": USER_ID, "databaseId": DATABASE_ID, "assetId": ASSET_ID,
            "filePath": "/scene.usd", "mirisAssetUuid": STORED_UUID})


    def test_the_key_is_read_from_its_secret_after_the_binding_check(self, miris_api):
        miris_api.responses.append(BOUND)
        _, body = _invoke(_event(), miris_api, mode="static")
        assert body["viewerKey"] == STATIC_VIEWER_KEY
        assert [c.kwargs["SecretId"] for c in miris_api.secretsmanager.get_secret_value.call_args_list] == [
            miris_api.secret_arn, VIEWER_KEY_SECRET_ARN]

    def test_an_unreadable_key_secret_is_502_without_a_key(self, miris_api):
        def read(SecretId):
            if SecretId == VIEWER_KEY_SECRET_ARN:
                raise RuntimeError(STATIC_VIEWER_KEY)
            return {"SecretString": miris_api.integration_key}

        miris_api.secretsmanager.get_secret_value.side_effect = read
        miris_api.responses.append(BOUND)
        audit = MagicMock()
        response, body = _invoke(_event(), miris_api, mode="static", audit=audit)
        assert response["statusCode"] == 502
        assert "viewerKey" not in body and STATIC_VIEWER_KEY not in response["body"]
        audit.assert_not_called()


@pytest.mark.parametrize("mode", MODES)
class TestNoKeyIsReturned:
    """Each refusal is the same in both modes: no key is returned, minted, recorded or audited."""

    @staticmethod
    def _assert_no_key(response, miris_api, key_table=None, audit=None):
        assert "viewerKey" not in json.loads(response["body"])
        assert STATIC_VIEWER_KEY not in response["body"]
        assert MIRIS_ERROR_BODY not in response["body"]
        assert _methods(miris_api, "POST") == []
        if key_table is not None:
            key_table.put_item.assert_not_called()
        if audit is not None:
            audit.assert_not_called()

    def test_tier1_denial_is_403_before_any_read(self, miris_api, mode):
        asset_table, metadata_table = _asset_table(), _streamed()
        response, _ = _invoke(_event(), miris_api, asset_table=asset_table, metadata_table=metadata_table,
                              enforcer=_enforcer(api=False), mode=mode)
        assert response["statusCode"] == 403
        asset_table.get_item.assert_not_called()
        metadata_table.get_item.assert_not_called()
        assert miris_api.calls == []

    def test_tier2_denial_is_403_before_any_metadata_read(self, miris_api, mode):
        metadata_table, audit = _streamed(), MagicMock()
        response, _ = _invoke(_event(), miris_api, metadata_table=metadata_table,
                              enforcer=_enforcer(entity=False), audit=audit, mode=mode)
        assert response["statusCode"] == 403
        metadata_table.get_item.assert_not_called()
        assert miris_api.calls == []
        self._assert_no_key(response, miris_api, audit=audit)

    def test_tier2_is_checked_as_a_read_of_the_asset(self, miris_api, mode):
        miris_api.responses.extend([BOUND, CREATED])
        enforcer = _enforcer()
        _invoke(_event(), miris_api, enforcer=enforcer, mode=mode)
        asset_arg, action = enforcer.enforce.call_args.args
        assert action == "GET"
        assert (asset_arg["object__type"], asset_arg["databaseId"], asset_arg["assetId"]) == (
            "asset", DATABASE_ID, ASSET_ID)

    @pytest.mark.parametrize("metadata_table, status, state", [
        (_metadata_table(), 404, "notStreamed"),
        (_streamed(uuid="  "), 404, "notStreamed"),
        (_streamed(uuid="not-a-uuid"), 400, "invalidUuid"),
    ], ids=["no-stream", "blank-uuid", "invalid-uuid"])
    def test_a_file_without_a_usable_stream_returns_no_key(self, miris_api, mode, metadata_table, status, state):
        response, body = _invoke(_event(), miris_api, metadata_table=metadata_table, mode=mode)
        assert (response["statusCode"], body["state"]) == (status, state)
        assert miris_api.calls == []

    @pytest.mark.parametrize("outcome", [
        _asset_read(tags=("vams", ASSET_TAG)),
        _asset_read(tags=("vams", ASSET_TAG, "vams-database-db-2")),
        404,
    ], ids=["database-tag-missing", "same-asset-id-other-database", "missing-miris-asset"])
    def test_an_unbound_asset_is_403_not_bound(self, miris_api, mode, outcome):
        miris_api.responses.append(outcome)
        key_table, audit = MagicMock(), MagicMock()
        response, body = _invoke(_event(), miris_api, key_table=key_table, audit=audit, mode=mode)
        assert (response["statusCode"], body["state"]) == (403, "notBound")
        assert ASSET_TAG in body["message"] and DATABASE_TAG in body["message"]
        self._assert_no_key(response, miris_api, key_table, audit)

    @pytest.mark.parametrize("outcome", [
        (500, MIRIS_ERROR_BODY), (302, MIRIS_ERROR_BODY), (200, {"tags": [ASSET_TAG, DATABASE_TAG]}),
        requests.ConnectionError(MIRIS_ERROR_BODY),
    ], ids=["500", "redirect", "no-state", "transport"])
    def test_an_unreadable_binding_is_502(self, miris_api, mode, outcome):
        miris_api.responses.append(outcome)
        key_table = MagicMock()
        response, _ = _invoke(_event(), miris_api, key_table=key_table, mode=mode)
        assert response["statusCode"] == 502
        assert len(miris_api.calls) == 1
        self._assert_no_key(response, miris_api, key_table)

    def test_too_little_time_left_is_504_without_a_miris_call(self, miris_api, mode):
        key_table = MagicMock()
        response, _ = _invoke(_event(), miris_api, key_table=key_table, mode=mode,
                              context=FakeLambdaContext(remaining_ms=2000))
        assert response["statusCode"] == 504
        assert miris_api.calls == []
        self._assert_no_key(response, miris_api, key_table)

    @pytest.mark.parametrize("missing", ["MIRIS_API_KEY_SECRET_ARN", "MIRIS_API_BASE_URL"])
    def test_a_missing_integration_setting_is_503_before_any_read(self, miris_api, monkeypatch, mode, missing):
        monkeypatch.setattr(mirisIntegration, missing, "")
        asset_table = _asset_table()
        response, _ = _invoke(_event(), miris_api, asset_table=asset_table, mode=mode)
        assert response["statusCode"] == 503
        asset_table.get_item.assert_not_called()
        assert miris_api.calls == []
        self._assert_no_key(response, miris_api)


class TestAuthorizationAndValidation:
    def test_no_caller_identity_is_403(self, miris_api):
        response, _ = _invoke(_event(), miris_api, claims=_AS_NOBODY)
        assert response["statusCode"] == 403
        assert miris_api.calls == []

    def test_a_missing_asset_is_400_without_a_miris_call(self, miris_api):
        response, body = _invoke(_event(), miris_api, asset_table=_asset_table(found=False))
        assert (response["statusCode"], body["message"]) == (400, "Asset not found")
        assert miris_api.calls == []

    @pytest.mark.parametrize("event", [
        _event(None),
        _event("/../secret.usd"),
        _event(raw_body="{not json"),
        _event(raw_body="[]"),
        _event(database_id="db 1"),
        _event(asset_id="../asset-2"),
        _event(method="GET"),
        _event(path="/addon/miris/asset-status"),
    ], ids=["missing-path", "traversal", "bad-json", "array", "bad-database", "asset-traversal",
            "other-method", "status-route"])
    def test_an_invalid_request_is_400_without_any_read(self, miris_api, event):
        asset_table = _asset_table()
        response, _ = _invoke(event, miris_api, asset_table=asset_table)
        assert response["statusCode"] == 400
        asset_table.get_item.assert_not_called()
        assert miris_api.calls == []

    def test_ids_in_path_parameters_are_not_read(self, miris_api):
        asset_table = _asset_table()
        event = _event(database_id=None, asset_id=None)
        event["pathParameters"] = {"databaseId": DATABASE_ID, "assetId": ASSET_ID}
        response, _ = _invoke(event, miris_api, asset_table=asset_table)
        assert response["statusCode"] == 400
        asset_table.get_item.assert_not_called()

    def test_a_request_received_long_ago_is_504_without_a_miris_call(self, miris_api):
        event = _event()
        event["requestContext"]["requestTimeEpoch"] = int((FIXED_EPOCH_SECONDS - 27) * 1000)
        response, _ = _invoke(event, miris_api)
        assert response["statusCode"] == 504
        assert miris_api.calls == []


def _compliance_table(state="quarantined", exception_granted=False):
    table = MagicMock()
    table.get_item.return_value = {"Item": {
        "databaseId": DATABASE_ID, "assetId": ASSET_ID, "complianceState": state,
        "exceptionGranted": exception_granted}}
    return table


@pytest.fixture
def quarantine_block():
    """The deployment's quarantine block switched on, with the compliance asset state table the
    handler's own guard reads (the module `check_quarantine_block` was bound from)."""
    guard = importlib.import_module(mintViewerKey.check_quarantine_block.__module__)
    table = _compliance_table()
    with patch.object(guard, "quarantine_blocks_download", True), \
         patch.object(guard, "compliance_asset_state_table", table):
        yield table


@pytest.mark.parametrize("mode", MODES)
class TestAssetDistributionRule:
    """The rule the core file routes apply before serving content: the asset is distributable and,
    while the quarantine block is on, not quarantined. Checked after both authorization tiers and
    before the file's stream metadata or Miris is read."""

    def test_an_asset_that_is_not_distributable_returns_no_key(self, miris_api, mode):
        metadata_table, key_table, audit = _streamed(), MagicMock(), MagicMock()
        response, body = _invoke(_event(), miris_api, asset_table=_asset_table(distributable=False),
                                 metadata_table=metadata_table, key_table=key_table, audit=audit,
                                 mode=mode)
        assert (response["statusCode"], body) == (400, {"message": "Asset not distributable"})
        metadata_table.get_item.assert_not_called()
        assert miris_api.calls == []
        key_table.put_item.assert_not_called()
        audit.assert_not_called()

    def test_a_quarantined_asset_returns_the_quarantine_refusal(self, miris_api, mode, quarantine_block):
        metadata_table, key_table = _streamed(), MagicMock()
        response, _ = _invoke(_event(), miris_api, metadata_table=metadata_table, key_table=key_table,
                              mode=mode)
        assert_is_the_quarantine_refusal(response)
        quarantine_block.get_item.assert_called_once_with(
            Key={"databaseId": DATABASE_ID, "assetId": ASSET_ID})
        metadata_table.get_item.assert_not_called()
        assert miris_api.calls == []
        key_table.put_item.assert_not_called()

    def test_a_granted_exception_returns_a_key(self, miris_api, mode, quarantine_block):
        quarantine_block.get_item.return_value = _compliance_table(exception_granted=True).get_item.return_value
        miris_api.responses.extend([BOUND, CREATED])
        response, body = _invoke(_event(), miris_api, mode=mode)
        assert response["statusCode"] == 200
        assert body["viewerKey"]

    def test_a_denied_caller_gets_the_denial_before_the_quarantine_is_read(self, miris_api, mode,
                                                                         quarantine_block):
        response, _ = _invoke(_event(), miris_api, enforcer=_enforcer(entity=False), mode=mode)
        assert response["statusCode"] == 403
        quarantine_block.get_item.assert_not_called()
        assert miris_api.calls == []

    def test_with_the_block_off_no_compliance_state_is_read(self, miris_api, mode):
        guard = importlib.import_module(mintViewerKey.check_quarantine_block.__module__)
        table = _compliance_table()
        miris_api.responses.extend([BOUND, CREATED])
        with patch.object(guard, "quarantine_blocks_download", False), \
             patch.object(guard, "compliance_asset_state_table", table):
            response, _ = _invoke(_event(), miris_api, mode=mode)
        assert response["statusCode"] == 200
        table.get_item.assert_not_called()


class TestNotConfigured:
    @pytest.mark.parametrize("mode, static_key, key_table", [
        ("static", "", MagicMock()), ("perView", STATIC_VIEWER_KEY, None), ("", STATIC_VIEWER_KEY, MagicMock()),
        ("per-view", STATIC_VIEWER_KEY, MagicMock()),
    ], ids=["static-without-key", "perView-without-table", "empty-mode", "unknown-mode"])
    def test_a_mode_missing_its_settings_is_503(self, miris_api, mode, static_key, key_table):
        asset_table = _asset_table()
        response, _ = _invoke(_event(), miris_api, asset_table=asset_table, key_table=key_table,
                              mode=mode, static_key=static_key)
        assert response["statusCode"] == 503
        assert STATIC_VIEWER_KEY not in response["body"]
        asset_table.get_item.assert_not_called()
        assert miris_api.calls == []

    def test_tier1_is_checked_before_the_configuration(self, miris_api, monkeypatch):
        monkeypatch.setattr(mirisIntegration, "MIRIS_API_KEY_SECRET_ARN", "")
        response, _ = _invoke(_event(), miris_api, enforcer=_enforcer(api=False))
        assert response["statusCode"] == 403

    @pytest.mark.parametrize("ttl_minutes", ["0", "61", "abc"])
    def test_a_bad_ttl_setting_is_500_without_a_miris_call(self, miris_api, ttl_minutes):
        response, _ = _invoke(_event(), miris_api, ttl_minutes=ttl_minutes)
        assert response["statusCode"] == 500
        assert miris_api.calls == []


@pytest.fixture
def clock(monkeypatch, miris_api):
    """A controllable `time.monotonic`; `per_request[method]` seconds pass during each request."""
    state = SimpleNamespace(now=1000.0, per_request={})
    monkeypatch.setattr(mirisIntegration.time, "monotonic", lambda: state.now)
    inner = mirisIntegration._get_session()

    def request(method, url, **kwargs):
        state.now += state.per_request.get(method, 0)
        return inner.request(method, url, **kwargs)

    monkeypatch.setattr(mirisIntegration, "_get_session", lambda: SimpleNamespace(request=request))
    return state


class TestPerViewFailures:
    @pytest.mark.parametrize("outcomes", [
        [(500, MIRIS_ERROR_BODY)],
        [(200, {"id": KEY_ID}), 204],
        [(200, MIRIS_ERROR_BODY)],
        [requests.ConnectionError(MIRIS_ERROR_BODY)],
    ], ids=["500", "no-key", "non-json", "transport"])
    def test_a_failed_create_is_502_without_the_miris_body(self, miris_api, outcomes):
        miris_api.responses.extend([BOUND] + outcomes)
        key_table, audit = MagicMock(), MagicMock()
        response, body = _invoke(_event(), miris_api, key_table=key_table, audit=audit)
        assert response["statusCode"] == 502
        assert MIRIS_ERROR_BODY not in response["body"]
        assert "viewerKey" not in body
        key_table.put_item.assert_not_called()
        audit.assert_not_called()

    @pytest.mark.parametrize("error, delete_outcome", [
        (ClientError({"Error": {"Code": "ConditionalCheckFailedException", "Message": "x"}}, "PutItem"), 204),
        (RuntimeError("boom"), (503, MIRIS_ERROR_BODY)),
    ], ids=["duplicate-id", "other-and-delete-fails"])
    def test_a_failed_table_put_deletes_the_minted_key_and_is_500(self, miris_api, error, delete_outcome):
        miris_api.responses.extend([BOUND, CREATED, delete_outcome])
        key_table, audit = MagicMock(), MagicMock()
        key_table.put_item.side_effect = error
        response, _ = _invoke(_event(), miris_api, key_table=key_table, audit=audit)

        assert response["statusCode"] == 500
        assert VIEWER_KEY_MATERIAL not in response["body"]
        assert [c["url"] for c in _methods(miris_api, "DELETE")] == [
            f"{miris_api.base_url}/v1/asset-viewer-key/{KEY_ID}"]
        audit.assert_not_called()

    def test_an_unexpected_failure_after_the_mint_is_500_and_logs_only_its_type(self, miris_api, log_recorder):
        miris_api.responses.extend([BOUND, CREATED])
        audit = MagicMock(side_effect=RuntimeError(f"boom {VIEWER_KEY_MATERIAL}"))
        response, _ = _invoke(_event(), miris_api, audit=audit)

        assert response["statusCode"] == 500
        assert VIEWER_KEY_MATERIAL not in response["body"]
        assert "RuntimeError" in log_recorder.text()
        assert "boom" not in log_recorder.text()
        assert not any(line.startswith("exception:") for line in log_recorder.lines)

    def test_a_deadline_reached_after_the_read_is_504_without_a_create(self, miris_api, clock):
        clock.per_request["GET"] = 100
        miris_api.responses.append(BOUND)
        response, _ = _invoke(_event(), miris_api)
        assert response["statusCode"] == 504
        assert _methods(miris_api, "POST") == []

    def test_each_call_fits_in_the_time_left(self, miris_api, clock):
        clock.per_request["GET"] = 3
        miris_api.responses.extend([BOUND, CREATED])
        response, _ = _invoke(_event(), miris_api, context=FakeLambdaContext(remaining_ms=14000))
        assert response["statusCode"] == 200
        # The deadline is 11 s out: the read may use all 11 s, the create the 8 s left after it.
        read, create = miris_api.calls
        assert sum(read["timeout"]) == pytest.approx(11)
        assert sum(create["timeout"]) == pytest.approx(8)

    @pytest.mark.parametrize("late_step", ["create-without-key", "put-fails"])
    def test_a_key_left_without_a_row_is_deleted_even_after_the_deadline(self, miris_api, clock, late_step):
        key_table = MagicMock()
        if late_step == "create-without-key":
            clock.per_request["POST"] = 100
            miris_api.responses.extend([BOUND, (200, {"id": KEY_ID}), 204])
        else:
            miris_api.responses.extend([BOUND, CREATED, 204])

            def put_after_the_deadline(**kwargs):
                clock.now += 100
                raise RuntimeError("boom")

            key_table.put_item.side_effect = put_after_the_deadline
        response, _ = _invoke(_event(), miris_api, key_table=key_table)

        assert response["statusCode"] in (500, 502)
        [delete] = _methods(miris_api, "DELETE")
        assert delete["url"] == f"{miris_api.base_url}/v1/asset-viewer-key/{KEY_ID}"
        assert sum(delete["timeout"]) <= mirisIntegration._CLEANUP_GRACE_SECONDS


def test_no_path_logs_a_key_or_a_secret(miris_api, log_recorder, caplog):
    caplog.set_level(logging.DEBUG)
    failing_table = MagicMock()
    failing_table.put_item.side_effect = RuntimeError(f"boom {VIEWER_KEY_MATERIAL}")
    failing_audit = MagicMock(side_effect=RuntimeError(f"boom {STATIC_VIEWER_KEY}"))
    miris_api.responses.extend([
        BOUND, CREATED,                                       # perView success
        BOUND, CREATED, (503, MIRIS_ERROR_BODY),              # put fails, cleanup delete fails
        BOUND, (500, MIRIS_ERROR_BODY),                       # create fails
        BOUND,                                                # static success
        _asset_read(tags=("vams",)),                          # static not bound
        BOUND,                                                # static, then the audit write fails
    ])
    statuses = [
        _invoke(_event(), miris_api)[0]["statusCode"],
        _invoke(_event(), miris_api, key_table=failing_table)[0]["statusCode"],
        _invoke(_event(), miris_api)[0]["statusCode"],
        _invoke(_event(), miris_api, mode="static")[0]["statusCode"],
        _invoke(_event(), miris_api, mode="static")[0]["statusCode"],
        _invoke(_event(), miris_api, mode="static", audit=failing_audit)[0]["statusCode"],
    ]
    assert statuses == [200, 500, 502, 200, 403, 500]

    # Control: the paths were logged, so an empty log is not what passes.
    text = log_recorder.text()
    assert KEY_ID in text and "HTTP 500" in text and "RuntimeError" in text
    for logged in (text, caplog.text):
        for secret in (VIEWER_KEY_MATERIAL, STATIC_VIEWER_KEY, miris_api.integration_key, MIRIS_ERROR_BODY, "boom"):
            assert secret not in logged


def _load_handler_copy(monkeypatch, mode):
    """A private copy of the handler module, imported with `MIRIS_VIEWER_KEY_MODE` set to `mode`."""
    import importlib.util
    import types

    # The root conftest swaps these packages for bare stand-ins; the copy needs only these names.
    for module_name, attribute in (("handlers.auth", "request_to_claims"),
                                   ("handlers.authz", "CasbinEnforcer"),
                                   ("customLogging.auditLogging", "log_auth_other")):
        stand_in = types.ModuleType(module_name)
        setattr(stand_in, attribute, MagicMock())
        monkeypatch.setitem(sys.modules, module_name, stand_in)
    sys.modules["customLogging.auditLogging"].audited_handler = lambda handler: handler
    monkeypatch.setenv("MIRIS_VIEWER_KEY_MODE", mode)
    monkeypatch.setenv("MIRIS_VIEWER_KEY_STORAGE_TABLE_NAME", "probe-viewer-key-table")
    name = f"{mintViewerKey.__package__}._mint_viewer_key_load_probe"
    spec = importlib.util.spec_from_file_location(name, mintViewerKey.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("mode, table_name", [("perView", "probe-viewer-key-table"), ("static", None)])
def test_the_key_table_is_resolved_in_per_view_mode_only(monkeypatch, mode, table_name):
    module = _load_handler_copy(monkeypatch, mode)
    assert module.miris_viewer_key_table_name == table_name
    assert (module.miris_viewer_key_table.name if table_name else module.miris_viewer_key_table) == table_name
