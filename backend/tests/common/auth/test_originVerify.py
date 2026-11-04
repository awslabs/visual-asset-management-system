# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""The origin verification module: which values it accepts, how it caches them, and how it fails.

The accepted values are ``current`` and ``previous`` of the secret's AWSCURRENT version plus
``current`` of an AWSPENDING version, read once per cache window. A value matching none of them
re-reads the secret at most once per refresh interval, so a rotation is picked up without a cold
start and a stream of wrong values does not become a stream of Secrets Manager calls. A read failure
accepts nothing and never raises, and the client's timeouts bound a read that cannot reach Secrets
Manager to a few seconds.
"""
import hmac
import json
import sys

import boto3
import pytest
from botocore.exceptions import ConnectTimeoutError, EndpointConnectionError, ReadTimeoutError
from moto import mock_aws

# pragma: allowlist nextline secret
SECRET_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:vams-origin-verify-AbCdEf"


@pytest.fixture
def ov(monkeypatch):
    """The registered originVerify module (the one clientIp reads), with a fresh cache and a fake clock."""
    module = sys.modules["common.auth.originVerify"]
    clock = {"now": 1000.0}
    monkeypatch.setenv(module.SECRET_ARN_ENV, SECRET_ARN)
    monkeypatch.setattr(module, "_now", lambda: clock["now"])
    monkeypatch.setitem(module._cache, "values", ())
    monkeypatch.setitem(module._cache, "fetched_at", None)
    module.clock = clock
    yield module
    del module.clock


def _fetcher(monkeypatch, module, *results):
    """Replace the Secrets Manager read with successive results (a tuple, or an exception to raise)."""
    calls = []

    def fetch(secret_arn):
        calls.append(secret_arn)
        result = results[min(len(calls), len(results)) - 1]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(module, "_fetch_accepted_values", fetch)
    return calls


@pytest.mark.unit
class TestSecretStringParsing:
    def test_current_and_previous_in_key_order(self, ov):
        secret = json.dumps({"previous": "old", "current": "new"})
        assert ov.values_from_secret_string(secret, ("current", "previous")) == ("new", "old")

    def test_missing_previous_before_the_first_rotation(self, ov):
        secret = json.dumps({"current": "new"})
        assert ov.values_from_secret_string(secret, ("current", "previous")) == ("new",)

    @pytest.mark.parametrize(
        "secret",
        [None, "", "not json", "[]", json.dumps({"current": ""}), json.dumps({"current": 7})],
    )
    def test_unusable_secret_strings_yield_nothing(self, ov, secret):
        assert ov.values_from_secret_string(secret, ("current", "previous")) == ()


@pytest.mark.unit
class TestAcceptance:
    def test_accepts_each_value_read(self, ov, monkeypatch):
        _fetcher(monkeypatch, ov, ("new", "old", "pending"))
        for value in ("new", "old", "pending"):
            assert ov.is_valid_origin_verify_value(value) is True

    @pytest.mark.parametrize("value", [None, "", 12345, "NEW", "new ", "ne"])
    def test_rejects_anything_else(self, ov, monkeypatch, value):
        _fetcher(monkeypatch, ov, ("new", "old"))
        assert ov.is_valid_origin_verify_value(value) is False

    def test_rejects_without_a_configured_secret(self, ov, monkeypatch):
        calls = _fetcher(monkeypatch, ov, ("new",))
        monkeypatch.delenv(ov.SECRET_ARN_ENV)
        assert ov.is_valid_origin_verify_value("new") is False
        assert calls == []

    def test_compares_with_compare_digest(self, ov, monkeypatch):
        _fetcher(monkeypatch, ov, ("new", "old"))
        seen = []
        real = hmac.compare_digest

        def spy(a, b):
            seen.append((a, b))
            return real(a, b)

        monkeypatch.setattr(ov.hmac, "compare_digest", spy)
        assert ov.is_valid_origin_verify_value("old") is True
        # Every accepted value is compared, not only up to the first match.
        assert seen == [(b"old", b"new"), (b"old", b"old")]


@pytest.mark.unit
class TestCaching:
    def test_values_are_read_once_per_window(self, ov, monkeypatch):
        calls = _fetcher(monkeypatch, ov, ("new",))
        for _ in range(5):
            assert ov.is_valid_origin_verify_value("new") is True
        assert len(calls) == 1

    def test_the_window_expiring_reads_again(self, ov, monkeypatch):
        calls = _fetcher(monkeypatch, ov, ("old",), ("new", "old"))
        assert ov.is_valid_origin_verify_value("old") is True
        ov.clock["now"] += ov.CACHE_TTL_SECONDS
        assert ov.is_valid_origin_verify_value("new") is True
        assert len(calls) == 2

    def test_a_mismatch_reads_again_so_a_rotation_is_seen_without_a_cold_start(self, ov, monkeypatch):
        calls = _fetcher(monkeypatch, ov, ("old",), ("new", "old"))
        assert ov.is_valid_origin_verify_value("old") is True
        ov.clock["now"] += ov.MISMATCH_REFRESH_INTERVAL_SECONDS
        assert ov.is_valid_origin_verify_value("new") is True
        assert len(calls) == 2

    def test_mismatches_inside_the_refresh_interval_do_not_read_again(self, ov, monkeypatch):
        calls = _fetcher(monkeypatch, ov, ("new",))
        assert ov.is_valid_origin_verify_value("new") is True
        for _ in range(20):
            assert ov.is_valid_origin_verify_value("wrong") is False
        assert len(calls) == 1

    def test_one_read_per_interval_under_a_stream_of_wrong_values(self, ov, monkeypatch):
        calls = _fetcher(monkeypatch, ov, ("new",))
        for _ in range(3):
            assert ov.is_valid_origin_verify_value("wrong") is False
            ov.clock["now"] += ov.MISMATCH_REFRESH_INTERVAL_SECONDS
            assert ov.is_valid_origin_verify_value("wrong") is False
        # The first call reads for an empty cache; each interval after it allows one more.
        assert len(calls) == 4


@pytest.mark.unit
class TestReadFailure:
    def test_a_failed_first_read_accepts_nothing_and_does_not_raise(self, ov, monkeypatch):
        _fetcher(monkeypatch, ov, RuntimeError("AccessDeniedException"))
        assert ov.is_valid_origin_verify_value("new") is False

    def test_a_failed_refresh_keeps_the_values_already_read(self, ov, monkeypatch):
        _fetcher(monkeypatch, ov, ("new",), RuntimeError("ThrottlingException"))
        assert ov.is_valid_origin_verify_value("new") is True
        ov.clock["now"] += ov.CACHE_TTL_SECONDS
        assert ov.is_valid_origin_verify_value("new") is True

    def test_a_failed_read_is_not_retried_inside_the_refresh_interval(self, ov, monkeypatch):
        calls = _fetcher(monkeypatch, ov, RuntimeError("AccessDeniedException"))
        for _ in range(10):
            assert ov.is_valid_origin_verify_value("new") is False
        assert len(calls) == 1

    def test_the_failure_log_names_no_value(self, ov, monkeypatch):
        messages = []
        monkeypatch.setattr(ov.logger, "warning", lambda message, *a, **k: messages.append(message))
        _fetcher(monkeypatch, ov, RuntimeError("secret value newValue123 in an error"))
        ov.is_valid_origin_verify_value("newValue123")
        assert messages and all("newValue123" not in m for m in messages)


class _UnreachableSecretsManager:
    """A Secrets Manager client whose every call fails the way an unreachable endpoint does."""

    def __init__(self, error):
        self.error = error
        self.calls = 0

    def get_secret_value(self, **kwargs):
        self.calls += 1
        raise self.error


@pytest.mark.unit
class TestSecretsManagerClientBound:
    """The secret read cannot outlast the authorizer: short timeouts, few retries, then fall back.

    With botocore's defaults (60 s connect timeout, several attempts) a read from a VPC with no path
    to Secrets Manager outlasts the authorizer's 60 s timeout, so the request fails instead of
    falling back to the TCP peer.
    """

    AUTHORIZER_TIMEOUT_SECONDS = 60

    def test_the_client_uses_short_timeouts_and_two_retries(self, ov):
        config = ov.secrets_client.meta.config
        assert config.connect_timeout == 2
        assert config.read_timeout == 2
        # botocore counts max_attempts as retries after the first request and normalises
        # {"max_attempts": 2} to three attempts in total.
        assert config.retries == {"mode": "standard", "total_max_attempts": 3}

    def test_a_failed_read_is_bounded_well_inside_the_authorizer_timeout(self, ov):
        config = ov.secrets_client.meta.config
        per_attempt = config.connect_timeout + config.read_timeout
        worst_case = config.retries["total_max_attempts"] * per_attempt
        assert worst_case <= 12
        assert worst_case < self.AUTHORIZER_TIMEOUT_SECONDS / 4

    @pytest.mark.parametrize(
        "error",
        [
            ConnectTimeoutError(endpoint_url="https://secretsmanager.us-east-1.amazonaws.com"),
            EndpointConnectionError(endpoint_url="https://secretsmanager.us-east-1.amazonaws.com"),
            ReadTimeoutError(endpoint_url="https://secretsmanager.us-east-1.amazonaws.com"),
        ],
        ids=["connect-timeout", "no-route", "read-timeout"],
    )
    def test_an_unreachable_endpoint_accepts_nothing_and_does_not_raise(self, ov, monkeypatch, error):
        client = _UnreachableSecretsManager(error)
        monkeypatch.setattr(ov, "secrets_client", client)
        assert ov.is_valid_origin_verify_value("new") is False
        # The AWSCURRENT read failed, so the AWSPENDING read is not attempted.
        assert client.calls == 1


@pytest.mark.unit
class TestSecretsManagerRead:
    """The real read path against moto: AWSCURRENT current + previous, and AWSPENDING current."""

    @pytest.fixture
    def secret(self, ov, monkeypatch):
        for name, value in (
            ("AWS_ACCESS_KEY_ID", "testing"),
            ("AWS_SECRET_ACCESS_KEY", "testing"),
            ("AWS_SESSION_TOKEN", "testing"),
            ("AWS_DEFAULT_REGION", "us-east-1"),
        ):
            monkeypatch.setenv(name, value)
        with mock_aws():
            client = boto3.client("secretsmanager", region_name="us-east-1")
            monkeypatch.setattr(ov, "secrets_client", client)
            arn = client.create_secret(
                Name="vams-origin-verify", SecretString=json.dumps({"current": "first"})
            )["ARN"]
            yield client, arn

    def test_reads_current_before_the_first_rotation(self, ov, secret):
        client, arn = secret
        assert ov._fetch_accepted_values(arn) == ("first",)

    def test_reads_current_previous_and_pending(self, ov, secret):
        client, arn = secret
        client.put_secret_value(
            SecretId=arn, SecretString=json.dumps({"current": "second", "previous": "first"})
        )
        client.put_secret_value(
            SecretId=arn,
            ClientRequestToken="11111111-2222-3333-4444-555555555555",
            SecretString=json.dumps({"current": "third", "previous": "second"}),
            VersionStages=["AWSPENDING"],
        )
        assert ov._fetch_accepted_values(arn) == ("second", "first", "third")
