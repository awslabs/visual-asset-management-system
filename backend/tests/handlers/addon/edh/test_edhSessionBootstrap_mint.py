# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Key minting half of edhSessionBootstrap: the client configuration and request budget, the
apiKeyService cross-call, the expiry bound, the base URL the workstation is pointed at, the names a
session key is minted under, and the mint of a re-issued key, which writes no secret. The plaintext
key is asserted to reach no log line: safeLogger redacts by key name and cannot mask a value already
rendered into a message."""

import importlib.util
import io
import json
import os
import re
from contextlib import ExitStack
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from backend.backend.common.constants import TEMPORARY_SESSION_KEY_NAME_PREFIX
from backend.backend.common.validators import object_name_pattern
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap

USER = "user@example.com"
BOOTSTRAP_ID = "5d1c8a2e-0b1f-4a6c-9d3e-7f8a9b0c1d2e"
RAW_KEY = "vams_" + "k" * 64
EVENT = {
    "requestContext": {"http": {"method": "POST", "path": "/addon/edh/workstations"}},
    "pathParameters": None,
    "queryStringParameters": None,
    "headers": {"authorization": "Bearer test-token"},
}
BOOTSTRAP_FLAG = "EDH_SESSION_BOOTSTRAP_ENABLED"
BOOTSTRAP_VALUES = (
    "EDH_SESSION_SECRET_PREFIX", "EDH_SESSION_KEY_TTL_MINUTES", "EDH_WORKSTATION_INSTANCE_ROLE_ARNS",
    "EDH_SESSION_SECRET_KMS_KEY_ARN", "API_KEY_SERVICE_LAMBDA_FUNCTION_NAME", "VAMS_BASE_URL",
)

DUPLICATE_NAME = "An API key with that name already exists. Please choose a different name."

# The instant a re-issue is minted at in the tests that fix it, and the name that instant gives the key.
REISSUED_AT = datetime(2026, 9, 28, 17, 5, 9, 250000, tzinfo=timezone.utc)
REISSUED_NAME = f"temporary-session-edh-{BOOTSTRAP_ID}-r20260928170509"


def _payload(status_code, body):
    """A Lambda invoke response whose Payload is a handler response envelope."""
    return {"Payload": io.BytesIO(
        json.dumps({"statusCode": status_code, "body": json.dumps(body)}).encode("utf-8"))}


def _created_key_payload():
    return _payload(200, {
        "apiKeyId": "key-1", "apiKeyName": f"temporary-session-edh-{BOOTSTRAP_ID}", "userId": USER,
        "apiKey": RAW_KEY, "expiresAt": "2026-09-08T20:00:00Z", "isActive": "true"})


def _log_lines(recorder):
    return [repr(call) for call in recorder.mock_calls]


def _load_fresh(unique_suffix):
    """An independent copy of the module loaded by file path, so an import-time failure is observable.
    Returns the module and the boto3.client double that recorded every client it built."""
    spec = importlib.util.spec_from_file_location(
        f"backend.backend.handlers.addon.edh.common.edhSessionBootstrap_under_test_{unique_suffix}",
        bootstrap.__file__)
    module = importlib.util.module_from_spec(spec)
    factory = MagicMock(return_value=MagicMock())
    with patch("boto3.client", factory):
        spec.loader.exec_module(module)
    return module, factory


def _config_for(factory, service):
    return next(c.kwargs["config"] for c in factory.call_args_list if c.args[0] == service)


@pytest.mark.unit
class TestModuleLoad:
    def test_import_succeeds_and_binds_the_configuration(self):
        module, _factory = _load_fresh("ok")
        assert module.session_bootstrap_enabled is False
        assert module.session_secret_prefix == os.environ["EDH_SESSION_SECRET_PREFIX"]
        assert module.session_key_ttl_minutes == int(os.environ["EDH_SESSION_KEY_TTL_MINUTES"])
        assert module.workstation_instance_role_arns == json.loads(os.environ["EDH_WORKSTATION_INSTANCE_ROLE_ARNS"])
        assert module.api_key_service_function_name == os.environ["API_KEY_SERVICE_LAMBDA_FUNCTION_NAME"]
        assert module.vams_base_url == os.environ["VAMS_BASE_URL"]

    @pytest.mark.parametrize("name", BOOTSTRAP_VALUES)
    def test_with_the_feature_on_a_missing_variable_aborts_the_import(self, monkeypatch, name):
        # Swallowed into a default, the handler would import cleanly and every mint would then fail
        # inside a per-request except: an unavailable credential per launch instead of a deployment fault.
        monkeypatch.setenv(BOOTSTRAP_FLAG, "true")
        monkeypatch.delenv(name)
        with pytest.raises(KeyError):
            _load_fresh(f"missing_{name}")

    def test_with_the_feature_on_and_every_variable_present_the_import_succeeds(self, monkeypatch):
        """Positive control for the cases above: the same load with the feature on and a good environment."""
        monkeypatch.setenv(BOOTSTRAP_FLAG, "true")
        module, _factory = _load_fresh("enabled_ok")
        assert module.is_enabled() is True
        assert module.api_key_service_function_name == os.environ["API_KEY_SERVICE_LAMBDA_FUNCTION_NAME"]

    @pytest.mark.parametrize("name", BOOTSTRAP_VALUES)
    def test_with_the_feature_off_a_missing_variable_leaves_the_module_loadable(self, monkeypatch, name):
        # A Lambda deployed without the feature (the reconciler, the admin service) carries none of the
        # bootstrap environment and must still start.
        monkeypatch.delenv(name)
        module, _factory = _load_fresh(f"optional_{name}")
        assert module.is_enabled() is False

    def test_without_the_flag_the_module_loads_with_the_feature_off_and_mints_nothing(self, monkeypatch):
        for name in (BOOTSTRAP_FLAG,) + BOOTSTRAP_VALUES:
            monkeypatch.delenv(name, raising=False)
        module, factory = _load_fresh("no_environment")
        assert module.is_enabled() is False
        assert (module.session_secret_prefix, module.api_key_service_function_name, module.vams_base_url) == ("", "", "")
        assert module.workstation_instance_role_arns == []
        row = {"bootstrapId": BOOTSTRAP_ID, "userId": USER}
        assert module.bootstrap_session(EVENT, row, mfa_enabled=False) == ("disabled", {})
        factory.return_value.invoke.assert_not_called()

    def test_the_module_reads_exactly_the_variables_the_stack_sets(self):
        # The names the CDK builder sets on every Lambda that imports this module -- the service, the
        # worker, the reconciler and the admin service; a variable read under any other name is unset in
        # the deployed function.
        with open(bootstrap.__file__, "r", encoding="utf-8") as handle:
            source = handle.read()
        read = set(re.findall(r'_configured\("([A-Z_]+)"', source))
        read |= set(re.findall(r'os\.(?:environ(?:\.get)?|getenv)\s*[\[(]\s*"([A-Z_]+)"', source))
        # The only reads of a name held in a variable are the two inside _configured, whose callers the
        # first pattern lists; any other one would read a variable this pin cannot see.
        indirect = re.findall(r'os\.(?:environ(?:\.get)?|getenv)\s*[\[(]\s*([A-Za-z_]\w*)', source)
        assert indirect == ["name", "name"]
        assert read == {BOOTSTRAP_FLAG, *BOOTSTRAP_VALUES} == {
            "EDH_SESSION_BOOTSTRAP_ENABLED", "EDH_SESSION_SECRET_PREFIX", "EDH_SESSION_KEY_TTL_MINUTES",
            "EDH_WORKSTATION_INSTANCE_ROLE_ARNS", "EDH_SESSION_SECRET_KMS_KEY_ARN",
            "API_KEY_SERVICE_LAMBDA_FUNCTION_NAME", "VAMS_BASE_URL",
        }

    def test_import_raises_when_the_ttl_is_not_an_integer(self, monkeypatch):
        monkeypatch.setenv("EDH_SESSION_KEY_TTL_MINUTES", "eight hours")
        with pytest.raises(ValueError):
            _load_fresh("bad_ttl")

    def test_import_raises_when_the_role_list_is_not_json(self, monkeypatch):
        monkeypatch.setenv("EDH_WORKSTATION_INSTANCE_ROLE_ARNS", "arn:aws:iam::123456789012:role/x")
        with pytest.raises(ValueError):
            _load_fresh("bad_roles")

    def test_the_cross_call_client_makes_one_attempt_inside_bounded_timeouts(self):
        # The read timeout covers a cold apiKeyService; the invoke is only ever started while the deadline
        # still holds connect plus read, so the single attempt cannot outrun the caller's Lambda.
        _module, factory = _load_fresh("cross_call_config")
        config = _config_for(factory, "lambda")
        assert config.retries == {"total_max_attempts": 1}
        assert (config.connect_timeout, config.read_timeout) == (1, 10)
        assert bootstrap.CROSS_CALL_TIMEOUT_SECONDS == 11
        assert bootstrap.BOOTSTRAP_BUDGET_SECONDS >= bootstrap.CROSS_CALL_TIMEOUT_SECONDS

    def test_the_secret_and_parameter_clients_retry_adaptively_inside_bounded_timeouts(self):
        _module, factory = _load_fresh("retry_config")
        for service in ("secretsmanager", "ssm"):
            config = _config_for(factory, service)
            assert config.retries == {"max_attempts": 5, "mode": "adaptive"}, service
            assert (config.connect_timeout, config.read_timeout) == (1, 2), service


@pytest.mark.unit
class TestMintSessionKey:
    def test_cross_calls_the_admin_create_route_as_the_launching_user(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            result = bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False)

        kwargs = client.invoke.call_args.kwargs
        assert kwargs["FunctionName"] == "test-apiKeyService"
        assert kwargs["InvocationType"] == "RequestResponse"
        sent = json.loads(kwargs["Payload"].decode("utf-8"))
        assert sent["requestContext"]["http"] == {"method": "POST", "path": "/auth/api-keys"}
        assert sent["pathParameters"] == {}
        assert sent["queryStringParameters"] == {}
        assert sent["lambdaCrossCall"] == {"userName": USER, "mfaEnabled": False}
        body = json.loads(sent["body"])
        assert body["apiKeyName"] == f"temporary-session-edh-{BOOTSTRAP_ID}"
        assert body["userId"] == USER
        assert body["description"] == f"EDH workstation session {BOOTSTRAP_ID}"
        assert body["expiresAt"].endswith("Z")
        assert result == {"apiKeyId": "key-1", "apiKey": RAW_KEY, "expiresAt": body["expiresAt"]}

    def test_propagates_the_callers_real_mfa_state(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)
        sent = json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert sent["lambdaCrossCall"]["mfaEnabled"] is True

    def test_expiry_is_now_plus_the_configured_ttl_in_utc(self):
        now = datetime(2026, 9, 8, 12, 0, 0, tzinfo=timezone.utc)
        with patch.object(bootstrap, "session_key_ttl_minutes", 90):
            assert bootstrap.session_key_expires_at(now) == "2026-09-08T13:30:00Z"

    def test_a_ttl_below_one_minute_never_yields_a_blank_expiry(self):
        with patch.object(bootstrap, "session_key_ttl_minutes", 0):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.session_key_expires_at()

    def test_a_user_without_roles_is_skipped_not_failed(self):
        client = MagicMock()
        client.invoke.return_value = _payload(400, {
            "message": f"User '{USER}' has no roles assigned. Cannot create API key for a user without roles."})
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            assert bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True) is None
        audit.assert_not_called()

    def test_any_other_400_raises(self):
        client = MagicMock()
        client.invoke.return_value = _payload(400, {"message": "Invalid JSON in request body"})
        with patch.object(bootstrap, "lambda_client", client):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)
        client.invoke.assert_called_once()

    def test_a_5xx_raises(self):
        client = MagicMock()
        client.invoke.return_value = _payload(500, {"message": "Internal Server Error"})
        with patch.object(bootstrap, "lambda_client", client):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)

    def test_a_function_error_payload_raises(self):
        client = MagicMock()
        client.invoke.return_value = {"Payload": io.BytesIO(
            json.dumps({"errorMessage": "boom", "errorType": "Exception"}).encode("utf-8"))}
        with patch.object(bootstrap, "lambda_client", client):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)

    def test_a_transport_error_raises(self):
        client = MagicMock()
        client.invoke.side_effect = Exception("throttled")
        with patch.object(bootstrap, "lambda_client", client):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)

    def test_a_200_without_the_plaintext_key_raises(self):
        client = MagicMock()
        client.invoke.return_value = _payload(200, {"apiKeyId": "key-1", "userId": USER})
        with patch.object(bootstrap, "lambda_client", client):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)

    def test_an_exhausted_budget_stops_the_mint_before_the_cross_call(self):
        client = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "_budget_deadline", 100.0), \
                patch.object(bootstrap, "_monotonic", MagicMock(return_value=104.5)):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)
        client.invoke.assert_not_called()

    def test_the_invoke_is_not_started_without_room_for_its_whole_timeout(self):
        # A cold apiKeyService answered after the client gave up would leave a key no row names.
        client = MagicMock()
        left = bootstrap.CROSS_CALL_TIMEOUT_SECONDS - 0.5
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "_budget_deadline", 100.0), \
                patch.object(bootstrap, "_monotonic", MagicMock(return_value=100.0 - left)):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)
        client.invoke.assert_not_called()

    def test_the_invoke_starts_while_its_whole_timeout_fits_the_deadline(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()), \
                patch.object(bootstrap, "_budget_deadline", 100.0), \
                patch.object(bootstrap, "_monotonic", MagicMock(return_value=100.0 - bootstrap.CROSS_CALL_TIMEOUT_SECONDS)):
            assert bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)["apiKeyId"] == "key-1"
        client.invoke.assert_called_once()

    def test_audit_row_names_the_key_id_but_never_carries_the_key(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)
        assert audit.call_args.args[0] is EVENT
        assert audit.call_args.args[1] == "edhSessionApiKeyCreate"
        data = audit.call_args.args[2]
        assert data["apiKeyId"] == "key-1"
        assert data["bootstrapId"] == BOOTSTRAP_ID
        assert data["userId"] == USER
        assert data["operation"] == "create"
        assert "vams_" not in json.dumps(data)

    def test_the_plaintext_key_never_reaches_a_log_line(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        recorder = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "logger", recorder), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=True)
        offenders = [line for line in _log_lines(recorder) if "vams_" in line]
        assert offenders == [], offenders
        # Positive control: the same collector sees a line that does carry the marker.
        recorder.info("control vams_control")
        assert any("vams_" in line for line in _log_lines(recorder))


def _listing_payload(keys, next_token=None):
    body = {"Items": keys}
    if next_token:
        body["NextToken"] = next_token
    return _payload(200, body)


@pytest.mark.unit
class TestStaleKeyRecovery:
    """A mint whose reply was lost leaves a key of the session's name that no row references, and the
    name is unique deployment-wide: every later mint for the desktop would be refused as a duplicate.
    The stale record is taken back and the create sent once more."""

    @staticmethod
    def _stale_key(api_key_id="stale-1", name=None):
        return {"apiKeyId": api_key_id, "apiKeyName": name or f"temporary-session-edh-{BOOTSTRAP_ID}", "userId": USER,
                "expiresAt": "2026-09-08T20:00:00Z", "isActive": "true"}

    @staticmethod
    def _sent(client):
        """(method, path, acting user) of every cross-call, in order."""
        out = []
        for c in client.invoke.call_args_list:
            payload = json.loads(c.kwargs["Payload"].decode("utf-8"))
            http = payload["requestContext"]["http"]
            out.append((http["method"], http["path"], payload["lambdaCrossCall"]["userName"]))
        return out

    def test_a_key_of_the_sessions_name_is_taken_back_and_the_create_sent_once_more(self):
        client = MagicMock()
        client.invoke.side_effect = [
            _payload(400, {"message": DUPLICATE_NAME}),
            _listing_payload([self._stale_key(), self._stale_key("other", "temporary-session-edh-other")]),
            _payload(200, {"message": "deleted"}),
            _created_key_payload(),
        ]
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            result = bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False)
        assert result["apiKeyId"] == "key-1"
        # Only the key of this session's name goes; the listing and the delete run as the system user.
        assert self._sent(client) == [
            ("POST", "/auth/api-keys", USER),
            ("GET", "/auth/api-keys", "SYSTEM_USER"),
            ("DELETE", "/auth/api-keys/stale-1", "SYSTEM_USER"),
            ("POST", "/auth/api-keys", USER),
        ]
        audited = [(c.args[1], c.args[2]["operation"], c.args[2]["apiKeyId"]) for c in audit.call_args_list]
        assert audited == [("edhSessionApiKeyRevoke", "revoke", "stale-1"), ("edhSessionApiKeyCreate", "create", "key-1")]
        assert audit.call_args_list[0].args[2]["bootstrapId"] == BOOTSTRAP_ID

    def test_a_second_duplicate_refusal_raises_rather_than_looping(self):
        client = MagicMock()
        client.invoke.side_effect = [
            _payload(400, {"message": DUPLICATE_NAME}), _listing_payload([self._stale_key()]),
            _payload(200, {}), _payload(400, {"message": DUPLICATE_NAME}),
        ]
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False)
        assert [method for method, _, _ in self._sent(client)] == ["POST", "GET", "DELETE", "POST"]

    def test_without_a_stale_key_in_the_listing_the_create_is_still_sent_once_more(self):
        client = MagicMock()
        client.invoke.side_effect = [
            _payload(400, {"message": DUPLICATE_NAME}), _listing_payload([]), _created_key_payload()]
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            assert bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False)["apiKeyId"] == "key-1"
        assert [method for method, _, _ in self._sent(client)] == ["POST", "GET", "POST"]

    def test_the_recovery_runs_inside_the_mint_budget(self):
        # The listing is the sequence's second cross-call; with the deadline gone it is refused before it
        # is sent, and the mint fails as unavailable rather than overrunning the request.
        client = MagicMock()
        client.invoke.side_effect = [_payload(400, {"message": DUPLICATE_NAME})]
        clock = MagicMock(side_effect=[0.0, 50.0])
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "_budget_deadline", 20.0), \
                patch.object(bootstrap, "_monotonic", clock):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False)
        assert client.invoke.call_count == 1


@pytest.mark.unit
class TestCrossCallResend:
    """A warm container's pooled connection the Lambda service has already closed fails the invoke
    before apiKeyService runs, so such an invoke is sent once more while the budget still holds a whole
    attempt. A read timeout is never resent: the callee may have run."""

    LAMBDA_URL = "https://lambda.us-east-1.amazonaws.com"

    @classmethod
    def _connection_errors(cls):
        from botocore.exceptions import ConnectionClosedError, ConnectTimeoutError, EndpointConnectionError
        return [ConnectionClosedError(endpoint_url=cls.LAMBDA_URL),
                EndpointConnectionError(endpoint_url=cls.LAMBDA_URL),
                ConnectTimeoutError(endpoint_url=cls.LAMBDA_URL)]

    @pytest.mark.parametrize("index", [0, 1, 2], ids=["closed", "endpoint", "connect-timeout"])
    def test_a_connection_failure_is_sent_once_more_and_the_mint_completes(self, index):
        client = MagicMock()
        client.invoke.side_effect = [self._connection_errors()[index], _created_key_payload()]
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            assert bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False)["apiKeyId"] == "key-1"
        assert client.invoke.call_count == 2
        first, second = (c.kwargs for c in client.invoke.call_args_list)
        assert first == second

    def test_a_second_connection_failure_is_not_sent_a_third_time(self):
        from botocore.exceptions import ConnectionClosedError
        client = MagicMock()
        client.invoke.side_effect = [ConnectionClosedError(endpoint_url=self.LAMBDA_URL)] * 3
        with patch.object(bootstrap, "lambda_client", client):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False)
        assert client.invoke.call_count == 2

    def test_the_resend_waits_for_a_whole_attempt_to_fit_the_budget(self):
        from botocore.exceptions import ConnectionClosedError
        client = MagicMock()
        client.invoke.side_effect = [ConnectionClosedError(endpoint_url=self.LAMBDA_URL), _created_key_payload()]
        # The first attempt starts with its whole timeout left; the failure leaves less than that.
        start = 100.0 - bootstrap.CROSS_CALL_TIMEOUT_SECONDS
        clock = MagicMock(side_effect=[start, start + 0.5])
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "_budget_deadline", 100.0), \
                patch.object(bootstrap, "_monotonic", clock):
            with pytest.raises(bootstrap.SessionBootstrapError, match="budget exhausted"):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False)
        assert client.invoke.call_count == 1

    def test_a_read_timeout_is_not_sent_again(self):
        from botocore.exceptions import ReadTimeoutError
        client = MagicMock()
        client.invoke.side_effect = [ReadTimeoutError(endpoint_url=self.LAMBDA_URL), _created_key_payload()]
        with patch.object(bootstrap, "lambda_client", client):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False)
        assert client.invoke.call_count == 1

    def test_a_revoke_whose_connection_was_closed_is_sent_once_more(self):
        from botocore.exceptions import ConnectionClosedError
        client = MagicMock()
        client.invoke.side_effect = [ConnectionClosedError(endpoint_url=self.LAMBDA_URL),
                                     _payload(200, {"message": "deleted"}), _listing_payload([])]
        audit = MagicMock()
        row = {"bootstrapId": BOOTSTRAP_ID, "userId": USER, "apiKeyId": "key-1",
               "secretName": f"vams/edh-session/{BOOTSTRAP_ID}"}
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "secretsmanager_client", MagicMock()), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.revoke_session(EVENT, row, delete_secret=False, actor=USER)
        # The key delete is sent once more on the same terms, and the key is audited as revoked.
        removals = [sent for sent in _sent(client) if sent[0] == "DELETE"]
        assert 2 <= len(removals) <= bootstrap.CROSS_CALL_ATTEMPTS and all(sent == removals[0] for sent in removals)
        assert {c.args[2]["apiKeyId"] for c in audit.call_args_list
                if c.args[1] == "edhSessionApiKeyRevoke"} == {"key-1"}


@pytest.mark.unit
class TestBaseUrl:
    def test_a_static_base_url_is_returned_as_is(self):
        ssm = MagicMock()
        with patch.object(bootstrap, "vams_base_url", "https://vams.example.com"), \
                patch.object(bootstrap, "_resolved_base_url", None), \
                patch.object(bootstrap, "ssm_client", ssm):
            assert bootstrap.resolve_vams_base_url() == "https://vams.example.com"
        ssm.get_parameter.assert_not_called()

    def test_an_ssm_pointer_reads_the_deployed_web_url_parameter_once(self):
        ssm = MagicMock()
        ssm.get_parameter.return_value = {"Parameter": {"Value": "https://d111.cloudfront.net "}}
        with patch.object(bootstrap, "vams_base_url", "ssm:/vams-test/web/deployedUrl"), \
                patch.object(bootstrap, "_resolved_base_url", None), \
                patch.object(bootstrap, "ssm_client", ssm):
            assert bootstrap.resolve_vams_base_url() == "https://d111.cloudfront.net"
            assert bootstrap.resolve_vams_base_url() == "https://d111.cloudfront.net"
        ssm.get_parameter.assert_called_once_with(Name="/vams-test/web/deployedUrl", WithDecryption=False)

    def test_an_unreadable_parameter_raises(self):
        ssm = MagicMock()
        ssm.get_parameter.side_effect = ClientError(
            {"Error": {"Code": "ParameterNotFound", "Message": "missing"}}, "GetParameter")
        with patch.object(bootstrap, "vams_base_url", "ssm:/vams-test/web/deployedUrl"), \
                patch.object(bootstrap, "_resolved_base_url", None), \
                patch.object(bootstrap, "ssm_client", ssm):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.resolve_vams_base_url()

    def test_a_parameter_read_that_fails_below_the_service_layer_raises(self):
        from botocore.exceptions import EndpointConnectionError
        ssm = MagicMock()
        ssm.get_parameter.side_effect = EndpointConnectionError(endpoint_url="https://ssm.example")
        with patch.object(bootstrap, "vams_base_url", "ssm:/vams-test/web/deployedUrl"), \
                patch.object(bootstrap, "_resolved_base_url", None), \
                patch.object(bootstrap, "ssm_client", ssm):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.resolve_vams_base_url()

    def test_a_blank_parameter_value_raises(self):
        ssm = MagicMock()
        ssm.get_parameter.return_value = {"Parameter": {"Value": "  "}}
        with patch.object(bootstrap, "vams_base_url", "ssm:/vams-test/web/deployedUrl"), \
                patch.object(bootstrap, "_resolved_base_url", None), \
                patch.object(bootstrap, "ssm_client", ssm):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.resolve_vams_base_url()

    def test_an_empty_base_url_raises(self):
        with patch.object(bootstrap, "vams_base_url", ""), \
                patch.object(bootstrap, "_resolved_base_url", None):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.resolve_vams_base_url()

    def test_is_enabled_reads_the_module_flag(self):
        with patch.object(bootstrap, "session_bootstrap_enabled", False):
            assert bootstrap.is_enabled() is False
        with patch.object(bootstrap, "session_bootstrap_enabled", True):
            assert bootstrap.is_enabled() is True


@pytest.mark.unit
class TestSessionKeyNames:
    """A launch or start mints under the session's own name; a re-issue under that name and the UTC
    second it was minted at, so the key it replaces can stay valid beside it. Both keep the prefix
    apiKeyService refuses a caller's key by and the orphan sweep lists keys by."""

    def test_a_launch_or_start_key_is_named_after_the_session(self):
        assert bootstrap.session_key_name(BOOTSTRAP_ID) == f"temporary-session-edh-{BOOTSTRAP_ID}"

    def test_a_re_issued_key_carries_the_utc_second_it_was_minted_at(self):
        assert bootstrap.session_key_name(BOOTSTRAP_ID, rotated_at=REISSUED_AT) == REISSUED_NAME

    def test_an_instant_in_another_offset_is_named_in_utc(self):
        elsewhere = REISSUED_AT.astimezone(timezone(timedelta(hours=-7)))
        assert bootstrap.session_key_name(BOOTSTRAP_ID, elsewhere) == REISSUED_NAME

    def test_an_instant_without_an_offset_is_read_as_utc(self):
        assert bootstrap.session_key_name(BOOTSTRAP_ID, REISSUED_AT.replace(tzinfo=None)) == REISSUED_NAME

    def test_both_names_keep_the_session_key_prefix(self):
        for name in (bootstrap.session_key_name(BOOTSTRAP_ID), REISSUED_NAME):
            assert name.startswith(bootstrap.SESSION_KEY_NAME_PREFIX)
            assert name.startswith(TEMPORARY_SESSION_KEY_NAME_PREFIX)

    def test_a_re_issued_name_is_one_apikeyservice_accepts(self):
        # The create route validates the name against the object-name pattern and 256 characters.
        assert re.fullmatch(object_name_pattern, REISSUED_NAME)
        assert len(REISSUED_NAME) <= 256

    @pytest.mark.parametrize("name", [f"temporary-session-edh-{BOOTSTRAP_ID}", REISSUED_NAME],
                             ids=["launch", "re-issue"])
    def test_the_bootstrap_id_is_read_back_from_either_name(self, name):
        assert bootstrap.bootstrap_id_from_key_name(name) == BOOTSTRAP_ID

    @pytest.mark.parametrize("suffix", ["-r2026092817050", "-r202609281705099", "-x20260928170509", "-r2026O928170509"],
                             ids=["13 digits", "15 digits", "another letter", "not all digits"])
    def test_a_suffix_that_is_not_a_re_issue_stamp_is_kept(self, suffix):
        assert bootstrap.bootstrap_id_from_key_name(f"temporary-session-edh-{BOOTSTRAP_ID}{suffix}") == f"{BOOTSTRAP_ID}{suffix}"

    def test_a_name_without_the_prefix_is_read_as_it_is(self):
        assert bootstrap.bootstrap_id_from_key_name(BOOTSTRAP_ID) == BOOTSTRAP_ID

    @pytest.mark.parametrize("name", [None, ""], ids=["none", "blank"])
    def test_no_name_reads_as_no_bootstrap_id(self, name):
        assert bootstrap.bootstrap_id_from_key_name(name) == ""

    def test_the_re_issue_operation_is_its_own_audit_value(self):
        assert bootstrap.AUDIT_OPERATION_REISSUE == "reissue"
        assert bootstrap.AUDIT_OPERATION_REISSUE not in (bootstrap.AUDIT_OPERATION_REVOKE,
                                                         bootstrap.AUDIT_OPERATION_EXPIRE, "create")


def _reissue_row(**overrides):
    row = {"bootstrapId": BOOTSTRAP_ID, "userId": USER, "state": "ACTIVE", "apiKeyId": "key-current",
           "keyExpiresAt": "2026-09-28T20:00:00Z", "secretName": f"vams/edh-session/{BOOTSTRAP_ID}"}
    row.update(overrides)
    return row


def _sent(client):
    """(method, path, acting user, body) of every cross-call, in order."""
    out = []
    for c in client.invoke.call_args_list:
        payload = json.loads(c.kwargs["Payload"].decode("utf-8"))
        http = payload["requestContext"]["http"]
        out.append((http["method"], http["path"], payload["lambdaCrossCall"]["userName"],
                    json.loads(payload["body"]) if "body" in payload else None))
    return out


@pytest.mark.unit
class TestMintSessionKeyUnderAGivenName:
    def test_the_key_is_created_under_the_name_given_and_audited_with_the_operation_given(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            minted = bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False,
                                                key_name=REISSUED_NAME, operation=bootstrap.AUDIT_OPERATION_REISSUE)
        body = _sent(client)[-1][3]
        assert body["apiKeyName"] == REISSUED_NAME
        # The description still names the session, so an operator reads either key back to its desktop.
        assert body["description"] == f"EDH workstation session {BOOTSTRAP_ID}"
        data = audit.call_args.args[2]
        assert (data["operation"], data["bootstrapId"], data["apiKeyId"]) == ("reissue", BOOTSTRAP_ID, "key-1")
        assert minted == {"apiKeyId": "key-1", "apiKey": RAW_KEY, "expiresAt": body["expiresAt"]}

    def test_without_a_name_the_key_is_named_after_the_session_and_audited_as_a_create(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False)
        assert _sent(client)[-1][3]["apiKeyName"] == f"temporary-session-edh-{BOOTSTRAP_ID}"
        assert audit.call_args.args[2]["operation"] == "create"

    def test_a_lost_reply_takes_back_only_keys_of_the_exact_name_being_minted(self):
        # The session's launch key and an older re-issue share the prefix and the bootstrap id; neither is
        # the key a lost reply left behind.
        client = MagicMock()
        client.invoke.side_effect = [
            _payload(400, {"message": DUPLICATE_NAME}),
            _listing_payload([
                {"apiKeyId": "stale-1", "apiKeyName": REISSUED_NAME, "userId": USER},
                {"apiKeyId": "launch-key", "apiKeyName": f"temporary-session-edh-{BOOTSTRAP_ID}", "userId": USER},
                {"apiKeyId": "older", "apiKeyName": f"temporary-session-edh-{BOOTSTRAP_ID}-r20260928090000", "userId": USER},
            ]),
            _payload(200, {"message": "deleted"}),
            _created_key_payload(),
        ]
        audit = MagicMock()
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", audit):
            bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False, key_name=REISSUED_NAME,
                                       operation=bootstrap.AUDIT_OPERATION_REISSUE)
        deletes = {path for method, path, _user, _body in _sent(client) if method == "DELETE"}
        assert deletes == {"/auth/api-keys/stale-1"}
        # The key taken back is audited with the bootstrap id its name encodes, the suffix removed.
        revokes = [c.args[2] for c in audit.call_args_list if c.args[1] == "edhSessionApiKeyRevoke"]
        assert {r["bootstrapId"] for r in revokes} == {BOOTSTRAP_ID}

    def test_a_key_of_the_name_that_the_row_still_names_is_never_taken_back(self):
        # A key the row names may be the one the desktop signs in with; a duplicate refusal over it fails the
        # mint rather than deleting it.
        client = MagicMock()
        client.invoke.side_effect = [
            _payload(400, {"message": DUPLICATE_NAME}),
            _listing_payload([{"apiKeyId": "key-current", "apiKeyName": REISSUED_NAME, "userId": USER}]),
            _payload(400, {"message": DUPLICATE_NAME}),
        ]
        with patch.object(bootstrap, "lambda_client", client), \
                patch.object(bootstrap, "log_auth_changes", MagicMock()):
            with pytest.raises(bootstrap.SessionBootstrapError):
                bootstrap.mint_session_key(EVENT, USER, BOOTSTRAP_ID, mfa_enabled=False, key_name=REISSUED_NAME,
                                           retain_key_ids=("key-current",))
        assert "DELETE" not in {method for method, _path, _user, _body in _sent(client)}


@pytest.mark.unit
class TestMintReissueKey:
    """The first half of a re-issue: a fresh key under a rotation name, minted as the row's user inside the
    mint budget and written nowhere -- the caller settles the row before any secret is written."""

    @staticmethod
    def _patched(client, **more):
        patches = [patch.object(bootstrap, "session_bootstrap_enabled", True),
                   patch.object(bootstrap, "vams_base_url", "https://vams.example.com"),
                   patch.object(bootstrap, "_resolved_base_url", None),
                   patch.object(bootstrap, "_budget_deadline", None),
                   patch.object(bootstrap, "lambda_client", client),
                   patch.object(bootstrap, "_now", MagicMock(return_value=REISSUED_AT))]
        patches.extend(patch.object(bootstrap, name, value) for name, value in more.items())
        return patches

    @staticmethod
    def _run(patches, row=None, **kwargs):
        with ExitStack() as stack:
            for p in patches:
                stack.enter_context(p)
            return bootstrap.mint_reissue_key(EVENT, row or _reissue_row(), True, **kwargs)

    def test_with_the_feature_off_nothing_is_minted(self):
        client = MagicMock()
        with patch.object(bootstrap, "session_bootstrap_enabled", False), \
                patch.object(bootstrap, "lambda_client", client):
            assert bootstrap.mint_reissue_key(EVENT, _reissue_row(), True) == ("disabled", None)
        client.invoke.assert_not_called()

    def test_a_key_is_minted_under_the_rotation_name_as_the_rows_user_and_nothing_is_written(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        sm = MagicMock()
        audit = MagicMock()
        outcome, minted = self._run(self._patched(client, secretsmanager_client=sm, log_auth_changes=audit))
        assert outcome == "provisioned"
        assert minted == {"apiKeyId": "key-1", "apiKey": RAW_KEY, "expiresAt": minted["expiresAt"]}
        method, path, user, body = _sent(client)[-1]
        assert (method, path, user) == ("POST", "/auth/api-keys", USER)
        assert body["apiKeyName"] == REISSUED_NAME
        assert body["expiresAt"] == minted["expiresAt"]
        assert audit.call_args.args[2]["operation"] == "reissue"
        # No secret is created, overwritten or bound: the row names the key before any secret holds it.
        assert not sm.mock_calls

    def test_the_callers_mfa_state_is_carried(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        self._run(self._patched(client, log_auth_changes=MagicMock()))
        sent = json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        assert sent["lambdaCrossCall"]["mfaEnabled"] is True

    def test_the_new_key_expires_one_ttl_after_the_re_issue(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        before = datetime.now(timezone.utc).replace(microsecond=0)
        _outcome, minted = self._run(self._patched(client, log_auth_changes=MagicMock()))
        after = datetime.now(timezone.utc)
        expiry = datetime.strptime(minted["expiresAt"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        ttl = timedelta(minutes=bootstrap.session_key_ttl_minutes)
        assert before + ttl <= expiry <= after + ttl

    def test_a_user_without_roles_is_skipped(self):
        client = MagicMock()
        client.invoke.return_value = _payload(400, {"message": "User alice has no roles assigned"})
        assert self._run(self._patched(client, log_auth_changes=MagicMock())) == ("skipped", None)

    @pytest.mark.parametrize("answer", [(500, {"message": "Internal Server Error"}), (400, {"message": "no"}),
                                        (200, {"apiKeyId": "key-1"})],
                             ids=["5xx", "other 400", "no plaintext"])
    def test_a_mint_that_fails_is_unavailable(self, answer):
        client = MagicMock()
        client.invoke.return_value = _payload(*answer)
        assert self._run(self._patched(client, log_auth_changes=MagicMock())) == ("unavailable", None)

    def test_an_unexpected_error_is_unavailable(self):
        client = MagicMock()
        client.invoke.side_effect = RuntimeError("boom")
        assert self._run(self._patched(client, log_auth_changes=MagicMock())) == ("unavailable", None)

    def test_an_unresolvable_base_url_mints_nothing(self):
        # A key minted with no base URL to deliver it under would be settled on the row for nothing.
        client = MagicMock()
        outcome = self._run(self._patched(client, vams_base_url=""))
        assert outcome == ("unavailable", None)
        client.invoke.assert_not_called()

    def test_a_deadline_that_has_passed_mints_nothing(self):
        client = MagicMock()
        outcome = self._run(self._patched(client, _monotonic=MagicMock(return_value=100.0)), deadline=50.0)
        assert outcome == ("unavailable", None)
        client.invoke.assert_not_called()

    def test_the_mint_runs_inside_the_callers_deadline_and_the_budget_is_cleared_after(self):
        client = MagicMock()
        seen = {}

        def invoke(**kwargs):
            seen["deadline"] = bootstrap._budget_deadline
            return _created_key_payload()

        client.invoke.side_effect = invoke
        outcome, _minted = self._run(self._patched(client, log_auth_changes=MagicMock(),
                                                   _monotonic=MagicMock(return_value=10.0)), deadline=40.0)
        assert outcome == "provisioned"
        assert seen["deadline"] == 40.0
        assert bootstrap._budget_deadline is None

    def test_a_lost_reply_takes_back_every_key_of_the_name_but_the_one_the_row_names(self):
        # The key the row names is never deleted as "stale", whatever its name.
        client = MagicMock()
        client.invoke.side_effect = [
            _payload(400, {"message": DUPLICATE_NAME}),
            _listing_payload([{"apiKeyId": "key-current", "apiKeyName": REISSUED_NAME, "userId": USER},
                              {"apiKeyId": "stale-1", "apiKeyName": REISSUED_NAME, "userId": USER},
                              {"apiKeyId": "stale-2", "apiKeyName": REISSUED_NAME, "userId": USER}]),
            _payload(200, {"message": "deleted"}),
            _payload(200, {"message": "deleted"}),
            _created_key_payload(),
        ]
        outcome, _minted = self._run(self._patched(client, log_auth_changes=MagicMock()))
        assert outcome == "provisioned"
        deletes = {path for method, path, _user, _body in _sent(client) if method == "DELETE"}
        assert deletes == {"/auth/api-keys/stale-1", "/auth/api-keys/stale-2"}

    def test_the_plaintext_key_never_reaches_a_log_line(self):
        client = MagicMock()
        client.invoke.return_value = _created_key_payload()
        recorder = MagicMock()
        self._run(self._patched(client, logger=recorder, log_auth_changes=MagicMock()))
        assert not any("vams_" in line for line in _log_lines(recorder))
        recorder.info("control vams_control")
        assert any("vams_" in line for line in _log_lines(recorder))
