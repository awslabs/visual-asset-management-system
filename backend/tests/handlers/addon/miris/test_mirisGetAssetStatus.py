# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""GET /addon/miris/asset-status: the Miris asset UUID comes only from the file's own metadata row,
Tier-2 GET on the asset is enforced before any metadata read, the binding tags are checked in both
viewer key modes, and every outcome maps onto the documented state set."""

import json
import logging
from unittest.mock import patch

import pytest
import requests

from backend.backend.handlers.addon.miris import getAssetStatus, mirisIntegration

from .conftest import (
    ASSET_ID,
    DATABASE_ID,
    OTHER_UUID,
    SORT_KEY_FIELD,
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

ROUTE_PATH = "/addon/miris/asset-status"
BINDING_TAG = f"vams-asset-{ASSET_ID}"
DATABASE_TAG = f"vams-database-{DATABASE_ID}"
RESPONSE_BODY = "miris-response-body-must-not-surface"
VIEWER_KEY = "viewer-key-0000000000"
MODES = ["static", "perView"]

_AS_USER = claims_after_normalizing({"tokens": ["user"], "roles": []})


def _event(file_path="/scene.usd", method="GET", path=ROUTE_PATH, extra_query=None,
           database_id=DATABASE_ID, asset_id=ASSET_ID):
    query = {"databaseId": database_id, "assetId": asset_id, "filePath": file_path, **(extra_query or {})}
    return rest_event(method, path, query={k: v for k, v in query.items() if v is not None})


def _stream_row(uuid=STORED_UUID, **overrides):
    row = {
        "miris_asset_uuid": uuid,
        "miris_source_version_id": "v-abc",
        "miris_uploaded_at": "2026-10-14T12:00:00Z",
        "miris_display_name": "Scene",
        **overrides,
    }
    return {k: v for k, v in row.items() if v is not None}


def _streamed(file_path="/scene.usd", **row):
    return _metadata_table({f"{DATABASE_ID}:{ASSET_ID}:{file_path}": _stream_row(**row)})


def _bound(state="streamable", tags=(BINDING_TAG, DATABASE_TAG)):
    return (200, {"id": STORED_UUID, "state": state, "tags": list(tags)})


def _queue(miris_api, mode, state):
    """Queue the Miris answers that report `state` in `mode`."""
    if mode == "static":
        miris_api.responses.extend([_bound(state="preview"), (200, {"state": state})])
    else:
        miris_api.responses.append(_bound(state=state))


def _invoke(event, metadata_table=None, asset_table=None, enforcer=None, viewer_key=VIEWER_KEY,
            context=None, mode="static"):
    metadata_table = metadata_table or _streamed()
    with patch.object(getAssetStatus, "asset_file_metadata_table", metadata_table), \
         patch.object(getAssetStatus, "asset_table", asset_table or _asset_table()), \
         patch.object(getAssetStatus, "CasbinEnforcer", return_value=enforcer or _enforcer()), \
         patch.object(getAssetStatus, "request_to_claims", side_effect=_AS_USER), \
         static_viewer_key(viewer_key), \
         patch.object(getAssetStatus, "miris_viewer_key_mode", mode):
        response = getAssetStatus.lambda_handler(event, context or FakeLambdaContext())
    return response["statusCode"], json.loads(response["body"]), metadata_table


class TestStates:
    def test_static_mode_checks_the_binding_then_reports_the_viewer_state(self, miris_api):
        _queue(miris_api, "static", "streamable")
        status, body, _ = _invoke(_event())

        assert status == 200
        assert body == {
            "filePath": "/scene.usd",
            "mirisAssetUuid": STORED_UUID,
            "sourceVersionId": "v-abc",
            "uploadedAt": "2026-10-14T12:00:00Z",
            "displayName": "Scene",
            "state": "streamable",
            "stale": None,
        }
        integration, viewer = miris_api.calls
        assert integration["url"] == f"{miris_api.base_url}/v1/asset/{STORED_UUID}"
        assert integration["headers"]["Miris-API-Key"] == miris_api.integration_key
        assert viewer["url"] == f"{miris_api.base_url}/viewer/v1/asset/{STORED_UUID}"
        assert viewer["headers"]["Miris-Viewer-Key"] == VIEWER_KEY
        assert "Miris-API-Key" not in viewer["headers"]
        assert sum(viewer["timeout"]) <= mirisIntegration._REQUEST_BUDGET_SECONDS

    def test_per_view_mode_reads_the_state_with_the_integration_key_only(self, miris_api):
        _queue(miris_api, "perView", "streamable")
        _, body, _ = _invoke(_event(), viewer_key="", mode="perView")
        assert body["state"] == "streamable"
        [call] = miris_api.calls
        assert call["url"] == f"{miris_api.base_url}/v1/asset/{STORED_UUID}"
        assert call["headers"]["Miris-API-Key"] == miris_api.integration_key

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("miris_state", ["preview", "failed"])
    def test_miris_states_are_passed_through(self, miris_api, mode, miris_state):
        _queue(miris_api, mode, miris_state)
        _, body, _ = _invoke(_event(), mode=mode)
        assert body["state"] == miris_state

    @pytest.mark.parametrize("mode", MODES)
    @pytest.mark.parametrize("metadata_table, state, uuid", [
        (_metadata_table(), "notStreamed", None),
        (_streamed(uuid="   "), "notStreamed", None),
        (_streamed(uuid="not-a-uuid"), "invalidUuid", "not-a-uuid"),
    ], ids=["no-row", "blank-uuid", "invalid-uuid"])
    def test_an_unusable_uuid_calls_no_miris_api(self, miris_api, mode, metadata_table, state, uuid):
        status, body, _ = _invoke(_event(), metadata_table=metadata_table, mode=mode)
        assert (status, body["state"], body["mirisAssetUuid"]) == (200, state, uuid)
        assert miris_api.calls == []
        miris_api.secretsmanager.get_secret_value.assert_not_called()

    def test_no_stream_keys_returns_null_fields(self, miris_api):
        _, body, _ = _invoke(_event(), metadata_table=_metadata_table())
        assert body == {"filePath": "/scene.usd", "mirisAssetUuid": None, "sourceVersionId": None,
                        "uploadedAt": None, "displayName": None, "state": "notStreamed",
                        "stale": None}

    def test_an_empty_source_version_is_preserved(self, miris_api):
        _queue(miris_api, "static", "streamable")
        _, body, _ = _invoke(_event(), metadata_table=_streamed(
            miris_source_version_id="", miris_uploaded_at=None, miris_display_name=None))
        assert (body["sourceVersionId"], body["uploadedAt"], body["displayName"]) == ("", None, None)


@pytest.mark.parametrize("mode", MODES)
class TestBinding:
    @pytest.mark.parametrize("outcome", [
        _bound(tags=["vams", BINDING_TAG]),
        _bound(tags=["vams", BINDING_TAG, "vams-database-db-2"]),
        (200, {"state": "streamable", "tags": None}),
        404,
    ], ids=["database-tag-missing", "same-asset-id-other-database", "null-tags", "missing-miris-asset"])
    def test_an_unbound_asset_is_not_bound_without_a_state_read(self, miris_api, mode, outcome):
        miris_api.responses.append(outcome)
        status, body, _ = _invoke(_event(), mode=mode)
        assert (status, body["state"], body["mirisAssetUuid"]) == (200, "notBound", STORED_UUID)
        assert len(miris_api.calls) == 1

    def test_a_deleted_asset_is_not_bound_without_a_state_read(self, miris_api, mode):
        miris_api.responses.append(_bound(state="deleted"))
        status, body, _ = _invoke(_event(), mode=mode)
        assert (status, body["state"], body["mirisAssetUuid"]) == (200, "notBound", STORED_UUID)
        assert len(miris_api.calls) == 1

    @pytest.mark.parametrize("outcome", [
        (500, RESPONSE_BODY), (302, RESPONSE_BODY), (200, "<html>"), requests.ConnectionError("unreachable"),
    ], ids=["500", "redirect", "non-json", "transport"])
    def test_an_unreadable_binding_is_unknown_without_a_state_read(self, miris_api, mode, outcome):
        miris_api.responses.append(outcome)
        status, body, _ = _invoke(_event(), mode=mode)
        assert (status, body["state"], body["mirisAssetUuid"]) == (200, "unknown", STORED_UUID)
        assert len(miris_api.calls) == 1

    def test_too_little_time_left_is_unknown_without_a_request(self, miris_api, mode):
        _, body, _ = _invoke(_event(), mode=mode, context=FakeLambdaContext(remaining_ms=2000))
        assert body["state"] == "unknown"
        assert miris_api.calls == []


class TestIndeterminateOutcomes:
    """Anything short of a definitive processing failure is `unknown`, and the UUID is still returned."""

    def test_a_viewer_api_deleted_state_is_not_bound(self, miris_api):
        miris_api.responses.extend([_bound(state="preview"), (200, {"state": "deleted"})])
        _, body, _ = _invoke(_event())
        assert (body["state"], body["mirisAssetUuid"]) == ("notBound", STORED_UUID)

    def test_a_viewer_api_400_is_processing(self, miris_api):
        miris_api.responses.extend([_bound(), (400, {"status": 400, "title": "Invalid Request"})])
        _, body, _ = _invoke(_event())
        assert (body["state"], body["mirisAssetUuid"]) == ("processing", STORED_UUID)

    @pytest.mark.parametrize("viewer_outcome", [
        404, 401, (500, "boom"), (200, "<html>"), (200, {"name": "no state"}), (200, {"state": "x" * 200}),
        requests.ConnectionError("unreachable"),
    ], ids=["404", "401", "500", "non-json", "no-state", "malformed-state", "transport"])
    def test_static_viewer_outcomes_are_unknown(self, miris_api, viewer_outcome):
        miris_api.responses.extend([_bound(), viewer_outcome])
        _, body, _ = _invoke(_event())
        assert (body["state"], body["mirisAssetUuid"]) == ("unknown", STORED_UUID)

    @pytest.mark.parametrize("outcome", [(400, "bad"), _bound(state="x" * 200)], ids=["400", "malformed-state"])
    def test_per_view_outcomes_are_unknown(self, miris_api, outcome):
        miris_api.responses.append(outcome)
        _, body, _ = _invoke(_event(), mode="perView")
        assert body["state"] == "unknown"

    def test_a_secret_read_failure_is_unknown_without_a_request(self, miris_api):
        miris_api.secretsmanager.get_secret_value.side_effect = RuntimeError("denied")
        _, body, _ = _invoke(_event(), mode="perView")
        assert body["state"] == "unknown"
        assert miris_api.calls == []

    def test_an_unreadable_static_viewer_key_is_unknown_after_the_binding_read(self, miris_api):
        def read(SecretId):
            if SecretId == VIEWER_KEY_SECRET_ARN:
                raise RuntimeError("denied")
            return {"SecretString": miris_api.integration_key}

        miris_api.secretsmanager.get_secret_value.side_effect = read
        miris_api.responses.append(_bound())
        _, body, _ = _invoke(_event())
        assert body["state"] == "unknown"
        assert len(miris_api.calls) == 1

    @pytest.mark.parametrize("mode, viewer_key, base_url", [
        ("static", "", "https://app.miris.com"),
        ("static", VIEWER_KEY, ""),
        ("", VIEWER_KEY, "https://app.miris.com"),
        ("per-view", VIEWER_KEY, "https://app.miris.com"),
    ], ids=["no-viewer-key", "no-base-url", "empty-mode", "unknown-mode"])
    def test_a_missing_setting_is_unknown_without_a_request(self, miris_api, monkeypatch, mode, viewer_key, base_url):
        monkeypatch.setattr(mirisIntegration, "MIRIS_API_BASE_URL", base_url)
        _, body, _ = _invoke(_event(), viewer_key=viewer_key, mode=mode)
        assert (body["state"], body["mirisAssetUuid"]) == ("unknown", STORED_UUID)
        assert miris_api.calls == []

    def test_a_request_received_long_ago_is_unknown_without_a_request(self, miris_api, monkeypatch):
        monkeypatch.setattr(mirisIntegration.time, "time", lambda: 5000.0)
        event = _event()
        event["requestContext"]["requestTimeEpoch"] = int((5000.0 - 27) * 1000)
        _, body, _ = _invoke(event, mode="perView")
        assert body["state"] == "unknown"
        assert miris_api.calls == []

    def test_no_key_or_response_body_reaches_the_logs(self, miris_api, log_recorder, caplog):
        caplog.set_level(logging.DEBUG)
        miris_api.responses.extend([
            _bound(), (500, RESPONSE_BODY),
            _bound(), (200, RESPONSE_BODY),
            _bound(), requests.ConnectionError(RESPONSE_BODY),
            (500, RESPONSE_BODY),
        ])
        for _ in range(3):
            assert _invoke(_event())[1]["state"] == "unknown"
        assert _invoke(_event(), mode="perView")[1]["state"] == "unknown"

        # Control: each failure was logged.
        assert "HTTP 500" in log_recorder.text()
        assert "ConnectionError" in log_recorder.text()
        for text in (log_recorder.text(), caplog.text):
            for secret in (RESPONSE_BODY, VIEWER_KEY, miris_api.integration_key):
                assert secret not in text


class TestUuidComesOnlyFromTheFile:
    def test_reads_exactly_the_five_keys_for_exactly_this_file(self, miris_api):
        _queue(miris_api, "static", "streamable")
        _, _, table = _invoke(_event())
        assert table.get_item.call_args_list
        assert [c.kwargs["Key"] for c in table.get_item.call_args_list] == [
            {"metadataKey": k, SORT_KEY_FIELD: f"{DATABASE_ID}:{ASSET_ID}:/scene.usd"}
            for k in ("miris_asset_uuid", "miris_source_version_id", "miris_uploaded_at", "miris_display_name",
                      "miris_dependency_manifest")
        ]

    def test_a_client_supplied_uuid_is_ignored(self, miris_api):
        _queue(miris_api, "static", "streamable")
        event = _event(extra_query={"mirisAssetUuid": OTHER_UUID})
        event["pathParameters"] = {"mirisAssetUuid": OTHER_UUID}
        _, body, _ = _invoke(event)
        assert body["mirisAssetUuid"] == STORED_UUID
        assert miris_api.calls
        assert all(OTHER_UUID not in c["url"] for c in miris_api.calls)

    def test_a_stream_on_another_file_is_not_returned(self, miris_api):
        _, body, _ = _invoke(_event("/scene.usd"), metadata_table=_streamed(file_path="/other.usd"))
        assert body["state"] == "notStreamed"
        assert miris_api.calls == []

    def test_a_path_below_the_route_is_not_dispatched(self, miris_api):
        status, _, table = _invoke(_event(path=f"{ROUTE_PATH}/{OTHER_UUID}"))
        assert status == 400
        table.get_item.assert_not_called()
        assert miris_api.calls == []


class TestFilePathNormalization:
    @pytest.mark.parametrize("given", ["scene.usd", "/scene.usd", f"/{ASSET_ID}/scene.usd", f"{ASSET_ID}/scene.usd"])
    def test_equivalent_forms_address_the_same_row(self, miris_api, given):
        _queue(miris_api, "static", "streamable")
        _, body, _ = _invoke(_event(given))
        assert (body["filePath"], body["mirisAssetUuid"]) == ("/scene.usd", STORED_UUID)

    def test_nested_paths_keep_their_folders(self, miris_api):
        _queue(miris_api, "static", "streamable")
        _, body, _ = _invoke(_event("a/b/scene.usda"), metadata_table=_streamed(file_path="/a/b/scene.usda"))
        assert (body["filePath"], body["state"]) == ("/a/b/scene.usda", "streamable")

    @pytest.mark.parametrize("query", [
        {"file_path": None}, {"file_path": "/"}, {"file_path": "/a/../b.usd"}, {"file_path": "/folder/"},
        {"file_path": "/" + "a" * 1100}, {"file_path": "/a\nb.usd"},
        {"database_id": None}, {"database_id": "db 1"}, {"asset_id": "../asset-2"}, {"asset_id": "a" * 300},
    ], ids=["no-path", "root", "traversal", "folder", "too-long", "newline",
            "no-database", "database-space", "asset-traversal", "asset-too-long"])
    def test_an_invalid_request_is_400_without_any_read(self, miris_api, query):
        asset_table = _asset_table()
        status, _, table = _invoke(_event(**query), asset_table=asset_table)
        assert status == 400
        asset_table.get_item.assert_not_called()
        table.get_item.assert_not_called()
        assert miris_api.calls == []

    def test_ids_in_path_parameters_are_not_read(self, miris_api):
        asset_table = _asset_table()
        event = _event(database_id=None, asset_id=None)
        event["pathParameters"] = {"databaseId": DATABASE_ID, "assetId": ASSET_ID}
        status, _, _ = _invoke(event, asset_table=asset_table)
        assert status == 400
        asset_table.get_item.assert_not_called()


# (asset root key, file's full object key, relative path the metadata is stored under)
ASSET_LAYOUTS = {
    "default-bucket": (f"{ASSET_ID}/", f"{ASSET_ID}/folder/scene.usd", "/folder/scene.usd"),
    "base-assets-prefix": (f"assets/{ASSET_ID}/", f"assets/{ASSET_ID}/folder/scene.usd", "/folder/scene.usd"),
    "bucket-existing-key": ("projects/site-a/", "projects/site-a/sub/scene.usd", "/sub/scene.usd"),
    "root-without-slashes": ("projects/site-a", "projects/site-a/sub/scene.usd", "/sub/scene.usd"),
    "root-with-leading-slash": ("/projects/site-a/", "projects/site-a/sub/scene.usd", "/sub/scene.usd"),
}


class TestAssetRootKey:
    """filePath may be the file's full object key under any asset root layout."""

    @pytest.mark.parametrize("layout", sorted(ASSET_LAYOUTS))
    @pytest.mark.parametrize("form", ["key", "slash-key", "relative"])
    def test_every_form_addresses_the_stored_row(self, miris_api, layout, form):
        root_key, full_key, relative = ASSET_LAYOUTS[layout]
        given = {"key": full_key, "slash-key": "/" + full_key, "relative": relative}[form]
        _queue(miris_api, "static", "streamable")
        _, body, _ = _invoke(_event(given), metadata_table=_streamed(file_path=relative),
                             asset_table=_asset_table(root_key=root_key))
        assert (body["filePath"], body["mirisAssetUuid"]) == (relative, STORED_UUID)

    @pytest.mark.parametrize("root_key, given", [
        (f"assets/{ASSET_ID}/", "/assets/asset-2/scene.usd"),
        (f"{ASSET_ID}/", "/asset-2/scene.usd"),
        ("projects/site-a/", "/projects/site-b/scene.usd"),
    ], ids=["other-asset-under-base-prefix", "other-asset-default", "sibling-existing-key"])
    def test_another_assets_key_is_not_streamed(self, miris_api, root_key, given):
        table = _metadata_table({
            f"{DATABASE_ID}:asset-2:/scene.usd": _stream_row(),
            f"{DATABASE_ID}:{ASSET_ID}:/scene.usd": _stream_row(uuid=OTHER_UUID),
        })
        _, body, _ = _invoke(_event(given), metadata_table=table, asset_table=_asset_table(root_key=root_key))
        assert (body["state"], body["mirisAssetUuid"]) == ("notStreamed", None)
        assert {c.kwargs["Key"][SORT_KEY_FIELD] for c in table.get_item.call_args_list} == {
            f"{DATABASE_ID}:{ASSET_ID}:{given}"}

    @pytest.mark.parametrize("root_key, given", [
        (f"assets/{ASSET_ID}/", f"/assets/{ASSET_ID}/../asset-2/scene.usd"),
        ("projects/site-a/", "/projects/site-a/"),
    ], ids=["traversal-in-full-key", "root-is-a-folder"])
    def test_an_unusable_full_key_is_400(self, miris_api, root_key, given):
        status, _, table = _invoke(_event(given), asset_table=_asset_table(root_key=root_key))
        assert status == 400
        table.get_item.assert_not_called()


class TestAuthorization:
    def test_tier1_denial_is_403_before_any_read(self, miris_api):
        asset_table = _asset_table()
        status, _, table = _invoke(_event(), asset_table=asset_table, enforcer=_enforcer(api=False))
        assert status == 403
        asset_table.get_item.assert_not_called()
        table.get_item.assert_not_called()
        assert miris_api.calls == []

    def test_tier2_denial_is_403_before_any_metadata_read(self, miris_api):
        status, _, table = _invoke(_event(), enforcer=_enforcer(entity=False))
        assert status == 403
        table.get_item.assert_not_called()
        assert miris_api.calls == []

    def test_tier2_is_checked_with_get_on_the_asset(self, miris_api):
        _queue(miris_api, "static", "streamable")
        enforcer = _enforcer()
        _invoke(_event(), enforcer=enforcer)
        asset_arg, action = enforcer.enforce.call_args.args
        assert (action, asset_arg["object__type"], asset_arg["assetId"]) == ("GET", "asset", ASSET_ID)

    def test_a_missing_asset_is_400_and_reads_no_metadata(self, miris_api):
        status, body, table = _invoke(_event(), asset_table=_asset_table(found=False))
        assert (status, body["message"]) == (400, "Asset not found")
        table.get_item.assert_not_called()

    def test_non_get_is_rejected(self, miris_api):
        status, _, table = _invoke(_event(method="POST"))
        assert status == 400
        table.get_item.assert_not_called()
