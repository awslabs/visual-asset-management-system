# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The shared Miris Integration API client: request shape, status handling, Integration Key
caching and rotation, deadlines, redirects, and that no key or response body is logged or raised."""

import logging
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import requests

from backend.backend.handlers.addon.miris import mirisIntegration
from backend.backend.handlers.addon.miris.mirisIntegration import MirisIntegrationError

from .conftest import FakeLambdaContext, rest_event

pytestmark = pytest.mark.unit

KEY_ID = "3f2b8c1e-9a4d-4e7f-8b6a-1c2d3e4f5a6b"
ASSET_UUID = "8d65af7c-5412-404b-a494-fbd2cdb62442"
RESPONSE_BODY = "miris-response-body-must-not-surface"
VIEWER_KEY_MATERIAL = "minted-viewer-key-material-do-not-log"
VIEWER_KEY = "static-viewer-key-do-not-log"
EXPIRES_AT = datetime(2026, 10, 15, 19, 4, 28, tzinfo=timezone.utc)
VAMS_ASSET_ID = "asset-1"
VAMS_DATABASE_ID = "db-1"
BINDING_TAG = f"vams-asset-{VAMS_ASSET_ID}"
DATABASE_TAG = f"vams-database-{VAMS_DATABASE_ID}"


def _created(key_id=KEY_ID, key=VIEWER_KEY_MATERIAL):
    return {"id": key_id, "key": key, "expires_at": "2026-10-15T19:04:28Z", "asset_ids": [ASSET_UUID]}


def _assert_status_error(raised, status):
    assert raised.value.status_code == status
    assert RESPONSE_BODY not in str(raised.value)


@pytest.fixture
def frozen_clock(monkeypatch):
    monkeypatch.setattr(mirisIntegration.time, "monotonic", lambda: 1000.0)
    monkeypatch.setattr(mirisIntegration.time, "time", lambda: 5000.0)


class TestDeleteViewerKey:
    def test_sends_one_delete_with_the_integration_key_and_no_redirects(self, miris_api):
        miris_api.responses.append(204)
        mirisIntegration.delete_viewer_key(KEY_ID)

        [call] = miris_api.calls
        assert (call["method"], call["url"]) == ("DELETE", f"{miris_api.base_url}/v1/asset-viewer-key/{KEY_ID}")
        assert call["headers"]["Miris-API-Key"] == miris_api.integration_key
        assert call["timeout"] == mirisIntegration._REQUEST_TIMEOUT_SECONDS
        assert call["allow_redirects"] is False
        miris_api.secretsmanager.get_secret_value.assert_called_once_with(SecretId=miris_api.secret_arn)

    @pytest.mark.parametrize("status", [200, 204, 404])
    def test_success_statuses_return(self, miris_api, status):
        miris_api.responses.append(status)
        assert mirisIntegration.delete_viewer_key(KEY_ID) is None

    @pytest.mark.parametrize("status", [400, 429, 500])
    def test_other_statuses_raise_with_the_status_code_only(self, miris_api, status):
        miris_api.responses.append((status, RESPONSE_BODY))
        with pytest.raises(MirisIntegrationError) as raised:
            mirisIntegration.delete_viewer_key(KEY_ID)
        _assert_status_error(raised, status)

    @pytest.mark.parametrize("error", [requests.ConnectionError, requests.Timeout])
    def test_a_transport_failure_raises_by_type_only(self, miris_api, error):
        miris_api.responses.append(error(f"boom {miris_api.integration_key} {RESPONSE_BODY}"))
        with pytest.raises(MirisIntegrationError, match=error.__name__) as raised:
            mirisIntegration.delete_viewer_key(KEY_ID)
        assert raised.value.status_code is None
        assert miris_api.integration_key not in str(raised.value)
        assert RESPONSE_BODY not in str(raised.value)
        assert raised.value.__cause__ is None

    @pytest.mark.parametrize("key_id", ["", None, "../asset-viewer-key", f"{KEY_ID}/../other", f"{KEY_ID}\n"])
    def test_a_non_uuid_raises_before_any_secret_read_or_request(self, miris_api, key_id):
        with pytest.raises(MirisIntegrationError, match="not a UUID"):
            mirisIntegration.delete_viewer_key(key_id)
        assert miris_api.calls == []
        miris_api.secretsmanager.get_secret_value.assert_not_called()

    def test_an_uppercase_uuid_is_accepted(self, miris_api):
        miris_api.responses.append(204)
        mirisIntegration.delete_viewer_key(KEY_ID.upper())
        assert len(miris_api.calls) == 1


class TestIntegrationKey:
    def test_the_key_is_cached_until_the_cache_window_ends(self, miris_api, monkeypatch):
        miris_api.responses.extend([204, 204, 204])
        mirisIntegration.delete_viewer_key(KEY_ID)
        mirisIntegration.delete_viewer_key(KEY_ID)
        assert miris_api.secretsmanager.get_secret_value.call_count == 1
        monkeypatch.setattr(mirisIntegration, "_SECRET_CACHE_TTL_SECONDS", -1)
        mirisIntegration.delete_viewer_key(KEY_ID)
        assert miris_api.secretsmanager.get_secret_value.call_count == 2

    @pytest.mark.parametrize("status", [401, 403])
    def test_an_auth_failure_rereads_the_key_and_retries_once(self, miris_api, status):
        rotated = "rotated-integration-key-do-not-log"
        miris_api.secretsmanager.get_secret_value.side_effect = [
            {"SecretString": miris_api.integration_key},
            {"SecretString": rotated},
        ]
        miris_api.responses.extend([status, 204])
        mirisIntegration.delete_viewer_key(KEY_ID)
        assert [c["headers"]["Miris-API-Key"] for c in miris_api.calls] == [miris_api.integration_key, rotated]

    def test_a_second_auth_failure_raises(self, miris_api):
        miris_api.responses.extend([401, 401])
        with pytest.raises(MirisIntegrationError) as raised:
            mirisIntegration.delete_viewer_key(KEY_ID)
        assert raised.value.status_code == 401
        assert len(miris_api.calls) == 2

    def test_an_auth_failure_after_the_retry_publishes_an_auth_failure_metric(self, miris_api, monkeypatch):
        monkeypatch.setenv("MIRIS_METRICS_NAMESPACE", "VAMS/test-deployment/Miris")
        logger = MagicMock()
        monkeypatch.setattr(mirisIntegration, "logger", logger)
        miris_api.responses.extend([403, 403])
        with pytest.raises(MirisIntegrationError):
            mirisIntegration.delete_viewer_key(KEY_ID)
        [call] = [c for c in logger.error.call_args_list if c.args[0] == "miris_auth_failed"]
        extra = call.kwargs["extra"]
        assert extra["MirisAuthFailures"] == 1
        assert extra["statusCode"] == 403
        assert extra["_aws"]["CloudWatchMetrics"] == [{
            "Namespace": "VAMS/test-deployment/Miris",
            "Dimensions": [[]],
            "Metrics": [{"Name": "MirisAuthFailures", "Unit": "Count"}],
        }]
        assert isinstance(extra["_aws"]["Timestamp"], int)
        assert miris_api.integration_key not in repr(logger.mock_calls)

    def test_an_auth_failure_the_retry_recovers_from_publishes_no_metric(self, miris_api, monkeypatch):
        monkeypatch.setenv("MIRIS_METRICS_NAMESPACE", "VAMS/test-deployment/Miris")
        logger = MagicMock()
        monkeypatch.setattr(mirisIntegration, "logger", logger)
        miris_api.responses.extend([401, 204])
        mirisIntegration.delete_viewer_key(KEY_ID)
        # The re-read is logged, so the stand-in logger is the one the module writes to.
        logger.warning.assert_called_once()
        logger.error.assert_not_called()

    def test_an_auth_failure_without_a_metrics_namespace_is_logged_without_a_metric(self, miris_api, monkeypatch):
        monkeypatch.delenv("MIRIS_METRICS_NAMESPACE", raising=False)
        logger = MagicMock()
        monkeypatch.setattr(mirisIntegration, "logger", logger)
        miris_api.responses.extend([401, 401])
        with pytest.raises(MirisIntegrationError):
            mirisIntegration.delete_viewer_key(KEY_ID)
        [call] = [c for c in logger.error.call_args_list if c.args[0] == "miris_auth_failed"]
        assert "_aws" not in call.kwargs["extra"]

    def test_a_secrets_manager_failure_raises_without_calling_miris(self, miris_api):
        miris_api.secretsmanager.get_secret_value.side_effect = RuntimeError(f"detail {miris_api.integration_key}")
        with pytest.raises(MirisIntegrationError, match="RuntimeError") as raised:
            mirisIntegration.delete_viewer_key(KEY_ID)
        assert miris_api.integration_key not in str(raised.value)
        assert miris_api.calls == []

    def test_an_empty_secret_raises(self, miris_api):
        miris_api.secretsmanager.get_secret_value.return_value = {"SecretString": "  "}
        with pytest.raises(MirisIntegrationError, match="empty"):
            mirisIntegration.delete_viewer_key(KEY_ID)
        assert miris_api.calls == []

    def test_a_secret_with_surrounding_whitespace_is_trimmed(self, miris_api):
        miris_api.secretsmanager.get_secret_value.return_value = {"SecretString": f"  {miris_api.integration_key}\n"}
        miris_api.responses.append(204)
        mirisIntegration.delete_viewer_key(KEY_ID)
        assert miris_api.calls[0]["headers"]["Miris-API-Key"] == miris_api.integration_key


class TestConfiguration:
    @pytest.mark.parametrize("setting", ["MIRIS_API_BASE_URL", "MIRIS_API_KEY_SECRET_ARN"])
    def test_a_missing_setting_raises_without_a_request(self, miris_api, monkeypatch, setting):
        assert mirisIntegration.integration_configured() is True
        monkeypatch.setattr(mirisIntegration, setting, "")
        assert mirisIntegration.integration_configured() is False
        with pytest.raises(MirisIntegrationError, match=setting):
            mirisIntegration.delete_viewer_key(KEY_ID)
        assert miris_api.calls == []


class TestNoSecretInLogs:
    def test_no_path_logs_a_key_or_a_response_body(self, miris_api, log_recorder, caplog):
        caplog.set_level(logging.DEBUG)
        miris_api.responses.extend([
            404, (401, RESPONSE_BODY), 204, (500, RESPONSE_BODY),
            (200, _created()), (200, {"id": KEY_ID}), (500, RESPONSE_BODY),
            (500, RESPONSE_BODY), requests.ConnectionError(f"{RESPONSE_BODY} {VIEWER_KEY}"),
        ])
        mirisIntegration.delete_viewer_key(KEY_ID)
        mirisIntegration.delete_viewer_key(KEY_ID)
        with pytest.raises(MirisIntegrationError):
            mirisIntegration.delete_viewer_key(KEY_ID)
        mirisIntegration.create_viewer_key(ASSET_UUID, EXPIRES_AT, "n")
        with pytest.raises(MirisIntegrationError):
            mirisIntegration.create_viewer_key(ASSET_UUID, EXPIRES_AT, "n")
        for _ in range(2):
            with pytest.raises(MirisIntegrationError):
                mirisIntegration.get_viewer_asset_state(ASSET_UUID, VIEWER_KEY)

        # Control: the recorder captured lines, so an empty log is not what passes.
        assert any(KEY_ID in line for line in log_recorder.lines)
        assert any("401" in line for line in log_recorder.lines)
        for text in (log_recorder.text(), caplog.text):
            for secret in (miris_api.integration_key, VIEWER_KEY_MATERIAL, VIEWER_KEY, RESPONSE_BODY):
                assert secret not in text


class TestCreateViewerKey:
    def test_sends_one_post_scoped_to_exactly_the_asset_and_returns_the_key(self, miris_api):
        miris_api.responses.append((200, _created()))
        local_expiry = EXPIRES_AT.astimezone(timezone(timedelta(hours=-7)))

        minted = mirisIntegration.create_viewer_key(ASSET_UUID, local_expiry, "vams:db:asset:1")

        [call] = miris_api.calls
        assert (call["method"], call["url"]) == ("POST", f"{miris_api.base_url}/v1/asset-viewer-key")
        assert call["headers"]["Miris-API-Key"] == miris_api.integration_key
        assert call["allow_redirects"] is False
        assert call["json"] == {
            "name": "vams:db:asset:1",
            "tags": [],
            "asset_ids": [ASSET_UUID],
            "expires_at": "2026-10-15T19:04:28Z",
        }
        assert (minted.key_id, minted.key, minted.expires_at) == (KEY_ID, VIEWER_KEY_MATERIAL, "2026-10-15T19:04:28Z")
        assert VIEWER_KEY_MATERIAL not in repr(minted)

    @pytest.mark.parametrize(
        "body, deleted",
        [
            ({"id": KEY_ID}, True),
            ({"id": KEY_ID, "key": "   "}, True),
            ({"id": KEY_ID, "key": 123}, True),
            ({"id": "not-a-uuid", "key": VIEWER_KEY_MATERIAL}, False),
            ({"id": f"{KEY_ID}/../x"}, False),
            ({"key": VIEWER_KEY_MATERIAL}, False),
            (f"<html>{VIEWER_KEY_MATERIAL}</html>", False),
        ],
        ids=["no-key", "blank-key", "non-string-key", "bad-id", "path-id", "no-id", "non-json"],
    )
    def test_a_response_without_a_usable_id_and_key_raises_and_deletes_a_created_key(
            self, miris_api, body, deleted):
        miris_api.responses.extend([(201, body), 204])
        with pytest.raises(MirisIntegrationError, match="no usable key") as raised:
            mirisIntegration.create_viewer_key(ASSET_UUID, EXPIRES_AT, "n")
        assert raised.value.status_code == 201
        assert VIEWER_KEY_MATERIAL not in str(raised.value)
        expected = [("POST", f"{miris_api.base_url}/v1/asset-viewer-key")]
        if deleted:
            expected.append(("DELETE", f"{miris_api.base_url}/v1/asset-viewer-key/{KEY_ID}"))
        assert miris_api.calls
        assert [(c["method"], c["url"]) for c in miris_api.calls] == expected

    @pytest.mark.parametrize("delete_outcome", [(500, RESPONSE_BODY), requests.ConnectionError(RESPONSE_BODY)])
    def test_a_failed_delete_is_logged_and_keeps_the_create_error(self, miris_api, log_recorder, delete_outcome):
        miris_api.responses.extend([(200, {"id": KEY_ID}), delete_outcome])
        with pytest.raises(MirisIntegrationError, match="no usable key") as raised:
            mirisIntegration.create_viewer_key(ASSET_UUID, EXPIRES_AT, "n")
        assert raised.value.status_code == 200
        assert raised.value.__context__ is None
        assert any(line.startswith("error:") and KEY_ID in line for line in log_recorder.lines)

    @pytest.mark.parametrize("status", [400, 404, 500])
    def test_a_non_2xx_raises_with_the_status_code_only(self, miris_api, status):
        miris_api.responses.append((status, RESPONSE_BODY))
        with pytest.raises(MirisIntegrationError) as raised:
            mirisIntegration.create_viewer_key(ASSET_UUID, EXPIRES_AT, "n")
        _assert_status_error(raised, status)

    @pytest.mark.parametrize("value", ["", None, f"{ASSET_UUID}/../x"])
    def test_a_non_uuid_asset_raises_without_a_request(self, miris_api, value):
        with pytest.raises(MirisIntegrationError, match="not a UUID"):
            mirisIntegration.create_viewer_key(value, EXPIRES_AT, "n")
        assert miris_api.calls == []


class TestGetAsset:
    def test_sends_one_get_and_returns_the_state_and_tags(self, miris_api):
        miris_api.responses.append((200, {"id": ASSET_UUID, "state": "streamable", "tags": ["vams", BINDING_TAG]}))

        assert mirisIntegration.get_asset(ASSET_UUID) == ("streamable", ("vams", BINDING_TAG))

        [call] = miris_api.calls
        assert (call["method"], call["url"]) == ("GET", f"{miris_api.base_url}/v1/asset/{ASSET_UUID}")
        assert call["headers"]["Miris-API-Key"] == miris_api.integration_key
        assert call["allow_redirects"] is False

    def test_404_returns_none(self, miris_api):
        miris_api.responses.append(404)
        assert mirisIntegration.get_asset(ASSET_UUID) is None

    @pytest.mark.parametrize(
        "tags, expected",
        [(None, ()), ("absent", ()), ([BINDING_TAG, 7, None], (BINDING_TAG,))],
        ids=["null", "absent", "non-string-dropped"],
    )
    def test_tags_are_normalized(self, miris_api, tags, expected):
        body = {"state": "preview"} if tags == "absent" else {"state": "preview", "tags": tags}
        miris_api.responses.append((200, body))
        assert mirisIntegration.get_asset(ASSET_UUID) == ("preview", expected)

    def test_tags_that_are_not_a_list_raise(self, miris_api):
        miris_api.responses.append((200, {"state": "preview", "tags": BINDING_TAG}))
        with pytest.raises(MirisIntegrationError, match="no tag list"):
            mirisIntegration.get_asset(ASSET_UUID)

    @pytest.mark.parametrize("status", [400, 500])
    def test_other_statuses_raise(self, miris_api, status):
        miris_api.responses.append((status, RESPONSE_BODY))
        with pytest.raises(MirisIntegrationError) as raised:
            mirisIntegration.get_asset(ASSET_UUID)
        _assert_status_error(raised, status)

    @pytest.mark.parametrize("body", [{"id": ASSET_UUID}, {"state": 3}, [], "not json"])
    def test_a_response_without_a_string_state_raises(self, miris_api, body):
        miris_api.responses.append((200, body))
        with pytest.raises(MirisIntegrationError, match="no state"):
            mirisIntegration.get_asset(ASSET_UUID)

    def test_a_non_uuid_raises_without_a_request(self, miris_api):
        with pytest.raises(MirisIntegrationError, match="not a UUID"):
            mirisIntegration.get_asset("../v1/asset-viewer-key")
        assert miris_api.calls == []


class TestBinding:
    def test_the_binding_tags_name_the_vams_asset_and_its_database(self):
        assert mirisIntegration.binding_tags(VAMS_DATABASE_ID, VAMS_ASSET_ID) == (BINDING_TAG, DATABASE_TAG)
        assert mirisIntegration.is_bound_to_asset(("vams", BINDING_TAG, DATABASE_TAG), VAMS_DATABASE_ID, VAMS_ASSET_ID)

    @pytest.mark.parametrize(
        "tags",
        [
            (),
            (BINDING_TAG,),
            (DATABASE_TAG,),
            (BINDING_TAG, "vams-database-db-2"),
            ("vams-asset-asset-2", DATABASE_TAG),
            (f"{BINDING_TAG}x", DATABASE_TAG),
            (BINDING_TAG, f"{DATABASE_TAG}x"),
            (BINDING_TAG.upper(), DATABASE_TAG),
        ],
        ids=["none", "asset-only", "database-only", "other-database", "other-asset",
             "asset-suffix", "database-suffix", "case"],
    )
    def test_not_bound_without_both_exact_tags(self, tags):
        assert not mirisIntegration.is_bound_to_asset(tags, VAMS_DATABASE_ID, VAMS_ASSET_ID)


class TestGetViewerAssetState:
    def test_sends_one_get_with_only_the_viewer_key_header(self, miris_api):
        miris_api.responses.append((200, {"id": ASSET_UUID, "state": "streamable"}))

        assert mirisIntegration.get_viewer_asset_state(ASSET_UUID, VIEWER_KEY) == "streamable"

        [call] = miris_api.calls
        assert (call["method"], call["url"]) == ("GET", f"{miris_api.base_url}/viewer/v1/asset/{ASSET_UUID}")
        assert call["headers"]["Miris-Viewer-Key"] == VIEWER_KEY
        assert "Miris-API-Key" not in call["headers"]
        assert call["allow_redirects"] is False
        miris_api.secretsmanager.get_secret_value.assert_not_called()

    def test_404_returns_none(self, miris_api):
        miris_api.responses.append(404)
        assert mirisIntegration.get_viewer_asset_state(ASSET_UUID, VIEWER_KEY) is None

    @pytest.mark.parametrize("status", [400, 401, 500])
    def test_other_statuses_raise_without_a_retry(self, miris_api, status):
        miris_api.responses.append((status, RESPONSE_BODY))
        with pytest.raises(MirisIntegrationError) as raised:
            mirisIntegration.get_viewer_asset_state(ASSET_UUID, VIEWER_KEY)
        _assert_status_error(raised, status)
        assert len(miris_api.calls) == 1

    def test_a_response_without_a_string_state_raises(self, miris_api):
        miris_api.responses.append((200, RESPONSE_BODY))
        with pytest.raises(MirisIntegrationError, match="no state") as raised:
            mirisIntegration.get_viewer_asset_state(ASSET_UUID, VIEWER_KEY)
        assert RESPONSE_BODY not in str(raised.value)

    @pytest.mark.parametrize("asset, viewer_key, message", [
        (ASSET_UUID, "", "viewer key"), (ASSET_UUID, None, "viewer key"), ("../x", VIEWER_KEY, "not a UUID")])
    def test_bad_input_raises_without_a_request(self, miris_api, asset, viewer_key, message):
        with pytest.raises(MirisIntegrationError, match=message):
            mirisIntegration.get_viewer_asset_state(asset, viewer_key)
        assert miris_api.calls == []


class TestRequestDeadline:
    @pytest.mark.parametrize("context, seconds", [
        (FakeLambdaContext(remaining_ms=300000), mirisIntegration._REQUEST_BUDGET_SECONDS),
        (None, mirisIntegration._REQUEST_BUDGET_SECONDS),
        (FakeLambdaContext(remaining_ms=10000), 10 - mirisIntegration._DEADLINE_MARGIN_SECONDS),
    ], ids=["long-lambda", "no-context", "short-lambda"])
    def test_the_deadline_is_the_budget_capped_by_the_lambdas_time(self, frozen_clock, context, seconds):
        assert mirisIntegration.request_deadline(context) == 1000.0 + seconds

    @pytest.mark.parametrize("request_context", [
        {"requestTimeEpoch": 4980 * 1000},
        {"timeEpoch": 4980 * 1000},
        {"requestTimeEpoch": 4980 * 1000, "timeEpoch": 5000 * 1000},
    ], ids=["rest", "http-api", "rest-wins"])
    def test_an_api_gateway_receipt_long_ago_shortens_the_deadline(self, frozen_clock, request_context):
        event = rest_event("GET", "/addon/miris/asset-status")
        event["requestContext"].update(request_context)
        deadline = mirisIntegration.request_deadline(FakeLambdaContext(remaining_ms=30000), event)
        # 29 s - 20 s elapsed - 3 s margin.
        assert deadline == pytest.approx(1000.0 + 6)

    def test_the_lambdas_remaining_time_still_caps_a_fresh_receipt(self, frozen_clock):
        event = {"requestContext": {"requestTimeEpoch": 5000 * 1000}}
        assert mirisIntegration.request_deadline(FakeLambdaContext(remaining_ms=300000), event) == (
            1000.0 + mirisIntegration._REQUEST_BUDGET_SECONDS)
        assert mirisIntegration.request_deadline(FakeLambdaContext(remaining_ms=8000), event) == (
            1000.0 + 8 - mirisIntegration._DEADLINE_MARGIN_SECONDS)

    @pytest.mark.parametrize("value", ["x", float("nan"), 10**400, True, 5000],
                             ids=["string", "nan", "overflow", "bool", "outside-window"])
    def test_without_a_usable_receipt_time_the_budget_applies(self, frozen_clock, value):
        event = {"requestContext": {"requestTimeEpoch": value}}
        deadline = mirisIntegration.request_deadline(FakeLambdaContext(remaining_ms=300000), event)
        assert deadline == 1000.0 + mirisIntegration._REQUEST_BUDGET_SECONDS

    def test_a_passed_deadline_raises_without_a_request(self, miris_api):
        deadline = mirisIntegration.request_deadline(FakeLambdaContext(remaining_ms=1000))
        with pytest.raises(mirisIntegration.MirisDeadlineExceeded):
            mirisIntegration.get_asset(ASSET_UUID, deadline=deadline)
        assert miris_api.calls == []
        assert issubclass(mirisIntegration.MirisDeadlineExceeded, MirisIntegrationError)

    @pytest.mark.parametrize("remaining, expected", [(60, (5, 15)), (16, (4, 12)), (1, (0.25, 0.75))])
    def test_connect_plus_read_never_exceeds_the_time_left(self, frozen_clock, remaining, expected):
        timeout = mirisIntegration._call_timeout(1000.0 + remaining)
        assert timeout == pytest.approx(expected)
        assert sum(timeout) <= remaining

    def test_less_than_the_minimum_call_time_raises(self, frozen_clock):
        with pytest.raises(mirisIntegration.MirisDeadlineExceeded):
            mirisIntegration._call_timeout(1000.0 + 0.9)

    def test_the_request_timeout_is_cut_to_the_time_left(self, miris_api, frozen_clock):
        miris_api.responses.append((200, {"state": "preview", "tags": []}))
        mirisIntegration.get_asset(ASSET_UUID, deadline=1000.0 + 8)
        assert miris_api.calls[0]["timeout"] == pytest.approx((2, 6))

    def test_the_secret_is_read_only_when_the_bounded_read_and_a_call_fit(self, miris_api, monkeypatch):
        needed = mirisIntegration._SECRET_READ_BOUND_SECONDS + mirisIntegration._MIN_CALL_SECONDS
        monkeypatch.setattr(mirisIntegration.time, "monotonic", lambda: 1000.0)
        with pytest.raises(mirisIntegration.MirisDeadlineExceeded):
            mirisIntegration.get_asset(ASSET_UUID, deadline=1000.0 + needed - 0.1)
        miris_api.secretsmanager.get_secret_value.assert_not_called()

        # A cached key needs no time for the read.
        miris_api.responses.extend([(200, {"state": "preview", "tags": []})] * 2)
        mirisIntegration.get_asset(ASSET_UUID)
        mirisIntegration.get_asset(ASSET_UUID, deadline=1000.0 + 2)
        assert len(miris_api.calls) == 2

    @pytest.mark.parametrize("remaining_ms, seconds", [
        (30000, mirisIntegration._CLEANUP_GRACE_SECONDS), (2000, 2 - mirisIntegration._CLEANUP_MARGIN_SECONDS)])
    def test_the_cleanup_deadline_is_a_grace_period_inside_the_lambdas_time(self, frozen_clock, remaining_ms, seconds):
        assert mirisIntegration.cleanup_deadline(FakeLambdaContext(remaining_ms=remaining_ms)) == 1000.0 + seconds

    def test_an_auth_retry_is_not_sent_once_the_deadline_has_passed(self, miris_api, monkeypatch):
        clock = {"now": 1000.0}
        monkeypatch.setattr(mirisIntegration.time, "monotonic", lambda: clock["now"])
        inner = mirisIntegration._get_session()

        def slow_request(method, url, **kwargs):
            clock["now"] += 9.5
            return inner.request(method, url, **kwargs)

        monkeypatch.setattr(mirisIntegration, "_get_session", lambda: SimpleNamespace(request=slow_request))
        miris_api.responses.extend([401, 204])
        with pytest.raises(mirisIntegration.MirisDeadlineExceeded):
            mirisIntegration.delete_viewer_key(KEY_ID, deadline=1010.0)
        assert len(miris_api.calls) == 1


class TestRedirects:
    @pytest.mark.parametrize("call", [
        lambda: mirisIntegration.get_asset(ASSET_UUID),
        lambda: mirisIntegration.get_viewer_asset_state(ASSET_UUID, VIEWER_KEY),
    ], ids=["integration", "viewer"])
    def test_a_redirect_raises_and_nothing_is_sent_onward(self, miris_api, call):
        miris_api.responses.append((302, RESPONSE_BODY))
        with pytest.raises(MirisIntegrationError) as raised:
            call()
        _assert_status_error(raised, 302)
        assert len(miris_api.calls) == 1

    def test_the_real_session_does_not_follow_a_redirect(self, monkeypatch):
        sent = []

        def fake_send(self, request, **kwargs):
            sent.append(request.url)
            response = requests.Response()
            response.status_code = 302
            response.headers["Location"] = "https://attacker.example/steal"
            response.url = request.url
            response.request = request
            return response

        monkeypatch.setattr(requests.adapters.HTTPAdapter, "send", fake_send)
        monkeypatch.setattr(mirisIntegration, "_session", None)
        monkeypatch.setattr(mirisIntegration, "MIRIS_API_BASE_URL", "https://miris.example.test")

        with pytest.raises(MirisIntegrationError):
            mirisIntegration.get_viewer_asset_state(ASSET_UUID, VIEWER_KEY)
        assert sent == [f"https://miris.example.test/viewer/v1/asset/{ASSET_UUID}"]
