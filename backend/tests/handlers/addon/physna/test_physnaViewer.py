# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import pytest
from unittest.mock import MagicMock, patch

# Module-level import ensures the real `backend.backend.handlers` package is
# populated in sys.modules before the root conftest's autouse fixture runs,
# preventing it from stubbing the package with a MockModule.
from backend.backend.handlers.addon.physna import physnaViewer as _pv  # noqa: F401


def _event(
    database_id: str = "db-1",
    asset_id: str = "asset-1.file",
    relative_path: str = "/part.step",
):
    return {
        "requestContext": {
            "http": {"method": "GET", "path": "/addon/physna/viewer"},
            "domainName": "vams.example.com",
            "stage": "$default",
        },
        "queryStringParameters": {
            "databaseId": database_id,
            "assetId": asset_id,
            "relativePath": relative_path,
        },
        "headers": {"authorization": "Bearer test-token"},
    }


def _fake_table_returning(asset_item):
    table = MagicMock()
    table.get_item.return_value = {"Item": asset_item}
    return table


def _happy_asset_item(database_id="db-1", asset_id="asset-1.file"):
    return {
        "databaseId": database_id,
        "assetId": asset_id,
        "assetName": "fake asset",
    }


def _mock_allow_all_enforcer():
    enforcer = MagicMock()
    enforcer.enforce.return_value = True
    enforcer.enforceAPI.return_value = True
    return enforcer


@pytest.mark.unit
class TestStripLeadingAssetId:
    """Web-UI `relativePath` is derived from the S3 key, which in VAMS is
    stored as `{assetId}/{inner_path}`. Physna's path builder prepends
    `databaseId/assetId/` itself, so if the viewer forwarded the raw web
    value the lookup would search for `db/asset/asset/inner_path`.
    `_strip_leading_asset_id` de-duplicates the prefix at the boundary."""

    def test_strips_leading_slash_asset_id(self):
        out = _pv._strip_leading_asset_id(
            "/x86ca1ac2-ba26-42cb-8e3d-7f20139a4e47/part.step",
            "x86ca1ac2-ba26-42cb-8e3d-7f20139a4e47",
        )
        assert out == "/part.step"

    def test_strips_leading_asset_id_without_slash(self):
        out = _pv._strip_leading_asset_id("asset-1/sub/part.step", "asset-1")
        assert out == "/sub/part.step"

    def test_does_not_strip_when_asset_id_is_not_first_segment(self):
        out = _pv._strip_leading_asset_id("/other/asset-1/part.step", "asset-1")
        assert out == "/other/asset-1/part.step"

    def test_does_not_strip_when_filename_merely_starts_with_asset_id(self):
        out = _pv._strip_leading_asset_id("/asset-1.step", "asset-1")
        assert out == "/asset-1.step"

    def test_empty_inputs_return_input(self):
        assert _pv._strip_leading_asset_id("", "asset-1") == ""
        assert _pv._strip_leading_asset_id("/part.step", "") == "/part.step"

    def test_leaves_already_stripped_path_untouched(self):
        assert _pv._strip_leading_asset_id("/part.step", "asset-1") == "/part.step"


@pytest.mark.unit
class TestPhysnaViewerResponses:
    """The viewer lambda returns JSON envelopes keyed by ``status``. The
    frontend switches on that value to decide whether to show the iframe,
    a loading/indexing message, or an error page."""

    # ---- unsupported & validation ----

    def test_unsupported_extension_returns_400_unsupported(self):
        event = _event(relative_path="/notes.txt")
        table = _fake_table_returning(_happy_asset_item())
        enforcer = _mock_allow_all_enforcer()
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 400
        assert response["headers"]["Content-Type"].startswith("application/json")
        # REST API returns the Lambda response verbatim, so the handler must set the CORS
        # origin header itself (regression guard for the browser CORS fix).
        assert response["headers"].get("Access-Control-Allow-Origin") == "*"
        body = json.loads(response["body"])
        assert body["status"] == "unsupported"

    def test_invalid_theme_param_is_ignored_post_refactor(self):
        """The pre-refactor model required ``theme`` in ("light","dark").
        The new model no longer accepts or validates theme — the frontend
        owns theme selection. Presence of an unknown query param must
        not produce a validation error."""
        event = _event()
        event["queryStringParameters"]["theme"] = "blurple"
        table = _fake_table_returning(_happy_asset_item())
        enforcer = _mock_allow_all_enforcer()
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(_pv, "lookup_physna_asset_id", return_value=None), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "not_synced"

    # ---- authz ----

    def test_api_authz_denial_returns_403_forbidden(self):
        """``enforceAPI`` == False must short-circuit before any Physna
        call."""
        table = _fake_table_returning(_happy_asset_item())
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = False
        enforcer.enforce.return_value = False
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 403
        body = json.loads(response["body"])
        assert body["status"] == "forbidden"

    def test_object_authz_denial_returns_403_forbidden(self):
        """``enforce`` == False on the asset item must deny access even
        when the API route is allowed."""
        table = _fake_table_returning(_happy_asset_item())
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = True
        enforcer.enforce.return_value = False
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 403
        body = json.loads(response["body"])
        assert body["status"] == "forbidden"

    def test_missing_asset_returns_404_not_found(self):
        table = _fake_table_returning(None)
        enforcer = _mock_allow_all_enforcer()
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 404
        body = json.loads(response["body"])
        assert body["status"] == "not_found"

    # ---- Physna lookup / state ----

    def test_asset_not_found_in_physna_returns_not_synced(self):
        table = _fake_table_returning(_happy_asset_item())
        enforcer = _mock_allow_all_enforcer()
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(_pv, "lookup_physna_asset_id", return_value=None), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "not_synced"

    def test_indexing_state_returns_indexing_envelope(self):
        """Physna's list endpoint omits indexing assets, but text-search
        returns them; the handler then reads state via GET /assets/{uuid}.
        An ``indexing`` state must surface to the frontend so it knows to
        poll instead of rendering the viewer."""
        table = _fake_table_returning(_happy_asset_item())
        enforcer = _mock_allow_all_enforcer()
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(
                _pv, "lookup_physna_asset_id", return_value="uuid-123"
            ), \
            patch.object(
                _pv, "get_physna_asset", return_value={"state": "indexing"}
            ), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "indexing"
        assert body["physnaState"] == "indexing"

    @pytest.mark.parametrize(
        "state", ["failed", "unsupported", "no-3d-data", "missing-dependencies"]
    )
    def test_permanent_failure_states_return_failed(self, state):
        table = _fake_table_returning(_happy_asset_item())
        enforcer = _mock_allow_all_enforcer()
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(
                _pv, "lookup_physna_asset_id", return_value="uuid-123"
            ), \
            patch.object(
                _pv, "get_physna_asset", return_value={"state": state}
            ), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "failed"
        assert body["physnaState"] == state

    # ---- ready: viewer token mint and response shape ----

    def test_finished_state_returns_ready_with_viewer_token_bundle(self):
        table = _fake_table_returning(_happy_asset_item())
        enforcer = _mock_allow_all_enforcer()
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(
                _pv, "lookup_physna_asset_id", return_value="uuid-abc"
            ), \
            patch.object(
                _pv, "get_physna_asset", return_value={"state": "finished"}
            ), \
            patch.object(
                _pv, "_mint_viewer_token", return_value="view-tok-xyz"
            ), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 200
        body = json.loads(response["body"])
        assert body["status"] == "ready"
        assert body["physnaAssetId"] == "uuid-abc"
        assert body["viewerToken"] == "view-tok-xyz"
        # Tenant id comes from PHYSNA_TENANT_ID env (set by conftest).
        assert body["tenantId"]
        # physnaApiBase is the trimmed form (no trailing slash).
        assert body["physnaApiBase"]
        assert not body["physnaApiBase"].endswith("/")

    def test_viewer_token_mint_failure_returns_upstream_unavailable(self):
        """A failed /viewer/token call after the asset is confirmed
        ``finished`` must degrade gracefully — the frontend should show a
        retryable error rather than an empty iframe."""
        table = _fake_table_returning(_happy_asset_item())
        enforcer = _mock_allow_all_enforcer()
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(
                _pv, "lookup_physna_asset_id", return_value="uuid-abc"
            ), \
            patch.object(
                _pv, "get_physna_asset", return_value={"state": "finished"}
            ), \
            patch.object(_pv, "_mint_viewer_token", return_value=None), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 502
        body = json.loads(response["body"])
        assert body["status"] == "upstream_unavailable"

    def test_physna_lookup_exception_returns_upstream_unavailable(self):
        table = _fake_table_returning(_happy_asset_item())
        enforcer = _mock_allow_all_enforcer()
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(
                _pv,
                "lookup_physna_asset_id",
                side_effect=RuntimeError("physna boom"),
            ), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(_event(), MagicMock())
        assert response["statusCode"] == 502
        body = json.loads(response["body"])
        assert body["status"] == "upstream_unavailable"

    def test_non_get_method_returns_method_not_allowed(self):
        event = _event()
        event["requestContext"]["http"]["method"] = "POST"
        table = _fake_table_returning(_happy_asset_item())
        enforcer = _mock_allow_all_enforcer()
        with patch.object(_pv, "asset_storage_table", table), \
            patch.object(_pv, "_CasbinEnforcer", return_value=enforcer), \
            patch.object(_pv, "PhysnaClient") as client_cls:
            client_cls.return_value = MagicMock()
            response = _pv.lambda_handler(event, MagicMock())
        assert response["statusCode"] == 405
        body = json.loads(response["body"])
        assert body["status"] == "method_not_allowed"


@pytest.mark.unit
class TestMintViewerToken:
    def test_extracts_token_field(self):
        client = MagicMock()
        resp = MagicMock()
        resp.status = 200
        resp.data = json.dumps({"token": "t-1"}).encode("utf-8")
        client.request.return_value = resp
        assert _pv._mint_viewer_token(client) == "t-1"

    def test_extracts_viewerToken_field(self):
        client = MagicMock()
        resp = MagicMock()
        resp.status = 200
        resp.data = json.dumps({"viewerToken": "t-2"}).encode("utf-8")
        client.request.return_value = resp
        assert _pv._mint_viewer_token(client) == "t-2"

    def test_non_200_returns_none(self):
        client = MagicMock()
        resp = MagicMock()
        resp.status = 500
        resp.data = b""
        client.request.return_value = resp
        assert _pv._mint_viewer_token(client) is None

    def test_unrecognized_body_returns_none(self):
        client = MagicMock()
        resp = MagicMock()
        resp.status = 200
        resp.data = json.dumps({"unexpected": "shape"}).encode("utf-8")
        client.request.return_value = resp
        assert _pv._mint_viewer_token(client) is None


