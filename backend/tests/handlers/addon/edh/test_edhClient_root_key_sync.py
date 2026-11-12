# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The EDH client asks for a root key sync when EDH still rejects the key the secret holds.

On a 401 the client re-reads the secret and sends the request once more; a second 401 means the secret
itself is out of date, which EDH's regeneration of the key on each start of its web interface causes. The
client then invokes the sync function asynchronously (fire and forget) and still raises the credential error
the caller maps today. It does so only while the deployment names a sync function, at most once per
interval per container, and never with the key in the request. The reconciler's sweep, which lists the
cluster under the root key every 15 minutes, takes the same path, so a rotation is repaired within one sweep
without any user traffic.
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from backend.backend.handlers.addon.edh.common import edhClient as _ec  # noqa: F401
from backend.backend.handlers.addon.edh.common import edhRootKeySyncTrigger as trigger
from backend.tests.handlers.addon.edh.edhTestSupport import FakeResponse, install_client_fakes

PROFILES = "/api/dcv/profiles"
SYNC_FUNCTION = "vams-edh-root-key-sync"
REJECTED = FakeResponse(401, {"success": False, "message": "Not authorized"})


class FakeLambda:
    def __init__(self, error=None):
        self.error = error
        self.invocations = []

    def invoke(self, **kwargs):
        self.invocations.append(kwargs)
        if self.error is not None:
            raise self.error
        return {"StatusCode": 202}


class RecordingLogger:
    def __init__(self):
        self.records = []

    def _record(self, message, *args, **kwargs):
        self.records.append([message, kwargs.get("extra")])

    debug = info = warning = error = exception = _record


class FakeMonotonic:
    def __init__(self, now=5000.0):
        self.now = now

    def monotonic(self):
        return self.now


@pytest.fixture
def lambdas(monkeypatch):
    """The sync enabled, with a recording Lambda client and a clock for the trigger's interval."""
    fake = FakeLambda()
    clock = FakeMonotonic()
    monkeypatch.setattr(trigger, "EDH_ROOT_KEY_SYNC_FUNCTION_NAME", SYNC_FUNCTION)
    monkeypatch.setattr(trigger, "_get_lambda_client", lambda: fake)
    monkeypatch.setattr(trigger, "time", clock)
    trigger._reset_for_tests()
    fake.clock = clock
    yield fake
    trigger._reset_for_tests()


def _rejected_twice(monkeypatch, *keys):
    return install_client_fakes(monkeypatch, root_keys=list(keys) or ["k-old", "k-new"], results=[REJECTED, REJECTED])


@pytest.mark.unit
class TestTheClientRequestsASync:
    def test_a_401_after_the_reread_requests_one_asynchronous_sync_and_still_raises(self, monkeypatch, lambdas):
        ec, recorded = _rejected_twice(monkeypatch)

        with pytest.raises(ec.EdhAuthError) as excinfo:
            ec.EdhClient().request("GET", PROFILES)

        assert excinfo.value.status == 401
        assert excinfo.value.root_key_sync_requested is True
        assert len(recorded["calls"]) == 2
        assert len(lambdas.invocations) == 1
        invocation = lambdas.invocations[0]
        assert invocation["FunctionName"] == SYNC_FUNCTION
        assert invocation["InvocationType"] == "Event"
        payload = json.loads(invocation["Payload"])
        assert payload == {"trigger": trigger.ROOT_KEY_SYNC_TRIGGER}
        assert "k-old" not in invocation["Payload"].decode() and "k-new" not in invocation["Payload"].decode()

    def test_the_caller_still_gets_the_credential_response(self, monkeypatch, lambdas):
        ec, _ = _rejected_twice(monkeypatch)

        with pytest.raises(ec.EdhAuthError) as excinfo:
            ec.EdhClient().request("GET", PROFILES)

        assert ec.edh_error_response_parts(excinfo.value) == (502, "EDH rejected the deployment credential.")

    def test_a_401_the_reread_key_clears_requests_nothing(self, monkeypatch, lambdas):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k-old", "k-new"], results=[REJECTED, 200])

        ec.EdhClient().request("GET", PROFILES)

        assert lambdas.invocations == []

    def test_a_redirect_requests_nothing(self, monkeypatch, lambdas):
        ec, _ = install_client_fakes(monkeypatch, root_keys=["k1"], results=[FakeResponse(302, b"")])

        with pytest.raises(ec.EdhAuthError) as excinfo:
            ec.EdhClient().request("GET", PROFILES)

        assert excinfo.value.root_key_sync_requested is False
        assert lambdas.invocations == []

    def test_a_mutation_rejected_twice_requests_a_sync_too(self, monkeypatch, lambdas):
        ec, recorded = _rejected_twice(monkeypatch)

        with pytest.raises(ec.EdhAuthError):
            ec.EdhClient().request("POST", "/api/dcv/virtual_desktops/create", edh_uid="v1", form={"a": "1"})

        assert len(recorded["calls"]) == 2
        assert len(lambdas.invocations) == 1


@pytest.mark.unit
class TestTheRequestIsBounded:
    def test_without_a_sync_function_nothing_is_invoked(self, monkeypatch, lambdas):
        monkeypatch.setattr(trigger, "EDH_ROOT_KEY_SYNC_FUNCTION_NAME", "")
        ec, _ = _rejected_twice(monkeypatch)

        with pytest.raises(ec.EdhAuthError) as excinfo:
            ec.EdhClient().request("GET", PROFILES)

        assert excinfo.value.root_key_sync_requested is False
        assert lambdas.invocations == []

    def test_a_burst_of_rejections_inside_the_interval_invokes_once(self, monkeypatch, lambdas):
        for _ in range(3):
            ec, _ = _rejected_twice(monkeypatch)
            with pytest.raises(ec.EdhAuthError) as excinfo:
                ec.EdhClient().request("GET", PROFILES)
            assert excinfo.value.root_key_sync_requested is True
            lambdas.clock.now += 10

        assert len(lambdas.invocations) == 1

    def test_a_rejection_after_the_interval_invokes_again(self, monkeypatch, lambdas):
        for _ in range(2):
            ec, _ = _rejected_twice(monkeypatch)
            with pytest.raises(ec.EdhAuthError):
                ec.EdhClient().request("GET", PROFILES)
            lambdas.clock.now += trigger.ROOT_KEY_SYNC_MIN_INTERVAL_SECONDS

        assert len(lambdas.invocations) == 2

    def test_an_invoke_that_fails_is_not_requested_and_the_caller_sees_only_the_credential_error(
            self, monkeypatch, lambdas):
        lambdas.error = ClientError({"Error": {"Code": "TooManyRequestsException", "Message": "slow down"}},
                                    "Invoke")
        sink = RecordingLogger()
        monkeypatch.setattr(trigger, "logger", sink)
        ec, _ = _rejected_twice(monkeypatch)

        with pytest.raises(ec.EdhAuthError) as excinfo:
            ec.EdhClient().request("GET", PROFILES)

        assert excinfo.value.root_key_sync_requested is False
        assert ec.edh_error_response_parts(excinfo.value) == (502, "EDH rejected the deployment credential.")
        assert [record for record in sink.records if record[0] == "EDH root key sync could not be requested"] == [
            ["EDH root key sync could not be requested", {"error": "ClientError"}]]
        # A failed invoke does not hold the interval: the next rejection tries again.
        lambdas.error = None
        ec, _ = _rejected_twice(monkeypatch)
        with pytest.raises(ec.EdhAuthError) as excinfo:
            ec.EdhClient().request("GET", PROFILES)
        assert excinfo.value.root_key_sync_requested is True
        assert len(lambdas.invocations) == 2

    def test_the_invoke_is_one_short_attempt(self):
        config = trigger._LAMBDA_CLIENT_CONFIG
        assert config.retries["total_max_attempts"] == 1
        assert config.connect_timeout <= 2 and config.read_timeout <= 3

    def test_the_sync_function_is_read_from_the_environment_at_import(self):
        assert trigger.ROOT_KEY_SYNC_FUNCTION_ENV == "EDH_ROOT_KEY_SYNC_FUNCTION_NAME"


@pytest.mark.unit
class TestTheReconcilerSweepRequestsASync:
    def test_a_sweep_whose_listing_is_rejected_after_the_reread_requests_a_sync_and_fails(self, monkeypatch, lambdas):
        import backend.backend.handlers.addon.edh.edhSessionReconciler as rec

        ec, recorded = _rejected_twice(monkeypatch)
        monkeypatch.setattr(rec, "read_lease_policy", lambda: None)
        scan = MagicMock()
        monkeypatch.setattr(rec, "_scan_swept_rows", scan)

        with pytest.raises(ec.EdhAuthError):
            rec.lambda_handler({}, MagicMock())

        assert len(recorded["calls"]) == 2
        assert len(lambdas.invocations) == 1
        assert lambdas.invocations[0]["FunctionName"] == SYNC_FUNCTION
        scan.assert_not_called()

    @pytest.mark.parametrize("results", [
        [FakeResponse(503, b"Service Unavailable")] * 3,
        [OSError("connection refused")] * 3,
        [FakeResponse(302, b"")],
        [REJECTED, FakeResponse(503, b"Service Unavailable"), FakeResponse(503, b""), FakeResponse(503, b"")],
    ], ids=["unavailable", "unreachable", "redirect", "rejected then unavailable"])
    def test_a_sweep_that_fails_otherwise_requests_nothing(self, monkeypatch, lambdas, results):
        import backend.backend.handlers.addon.edh.edhSessionReconciler as rec

        ec, _ = install_client_fakes(monkeypatch, root_keys=["k-old", "k-new"], results=results)
        monkeypatch.setattr(rec, "read_lease_policy", lambda: None)
        monkeypatch.setattr(rec, "_scan_swept_rows", MagicMock())

        with pytest.raises(ec.EdhError):
            rec.lambda_handler({}, MagicMock())

        assert lambdas.invocations == []
