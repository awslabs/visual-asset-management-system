# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Stop, start and delete with session bootstrap: the credential is revoked only after EDH has
acknowledged a stop or delete; a start follows EDH's view of the desktop first and refuses one that is
not stopped without touching its credential, re-mints before EDH is asked, binds the secret to the
desktop's instance once EDH has started it and takes back only the key it minted when EDH refuses or the
secret write fails; ahead of its mint a start deletes only the key the row names, and every other key of the
desktop once EDH has taken the start,
within what the request has left, so the listing that finds them holds up neither the mint nor the start; a
stop, start or delete whose answer is lost is decided by listing the desktop again; a start claims the row
before anything is minted, so of two starts of one desktop only one proceeds, and a start whose outcome
stays unknown keeps that claim, holding off another start of a desktop EDH still lists stopped for a while;
and an API-key principal cannot start a desktop. A stop or delete revokes the key the row holds as the table holds it,
not as the session index may still serve it, and a start whose row is deleted before its claim is refused
as a start of a deleted row. Ordering is asserted through one list every collaborator appends to."""

import json
import re
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ReadTimeoutError

import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.backend.handlers.addon.edh.common.edhClient import (
    EdhApiError, EdhOutcomeUnknownError, EdhUnavailableError,
)
from backend.tests.handlers.addon.edh.edhCredentialTestSupport import FakeApiKeyService
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    API_KEY_CLAIMS,
    BOOTSTRAP_OWN,
    SESSION_OWN,
    USER,
    FakeEdhClient,
    install_service_fakes,
    make_event,
    own_row,
    response_body,
    state_record,
    workstation_path,
)
from backend.tests.handlers.addon.edh.edhRowWriteTestSupport import (
    ConditionalRowTable, is_about_a_week_ahead, update_attribute_sets, written_values,
)

SECRET_NAME = f"vams/edh-session/{BOOTSTRAP_OWN}"
CREDENTIAL = {
    "apiKeyId": "key-2",
    "secretArn": f"arn:aws:secretsmanager:us-east-1:123456789012:secret:{SECRET_NAME}-AbCdEf",
    "secretName": SECRET_NAME,
    "keyExpiresAt": "2026-09-09T04:00:00Z",
    "secretBoundInstanceId": "",
}
CLEARED_KEY = dict(bootstrap.CLEARED_KEY_ATTRIBUTES)
CLEARED_ALL = {**CLEARED_KEY, **bootstrap.CLEARED_SECRET_ATTRIBUTES}
INSTANCE_ID = "i-0123456789abcdef0"
# The 502 of a desktop call whose outcome stays unknown, and of one that never reached EDH.
LOST_ANSWER_MESSAGE = ("EDH did not confirm the request in time; it may still be in progress. "
                       "Refresh before trying again.")
UNAVAILABLE_MESSAGE = "EDH is unavailable. Try again later."
# The 400 of a start EDH found no AWS capacity for.
CAPACITY_MESSAGE = "EDH found no available AWS capacity for this instance type. Try again later."
# The body field that marks the first of those as an outcome EDH did not confirm.
OUTCOME_UNKNOWN_FIELD = "outcomeUnknown"

IN_PROGRESS_MESSAGE = "A start of this workstation is already in progress"
# The body field that marks that 409 as a start still in progress.
START_IN_PROGRESS_FIELD = "startInProgress"


def _keyed_row(**overrides):
    row = own_row(apiKeyId="key-1", secretName=SECRET_NAME, keyExpiresAt="2026-09-08T20:00:00Z")
    row.update(overrides)
    return row


class _RecordingLogger:
    def __init__(self):
        self.records = []

    def _record(self, message, *args, **kwargs):
        self.records.append((message, kwargs.get("extra")))

    debug = info = warning = error = exception = _record


class _OrderedEdh(FakeEdhClient):
    """FakeEdhClient that also records each wrapper on a shared order list."""

    def __init__(self, order, **results):
        super().__init__(**results)
        self._order = order

    def _answer(self, wrapper, *args):
        self._order.append(wrapper)
        return super()._answer(wrapper, *args)


def _run(monkeypatch, method, action, row, *, claims=None, start_error=None, revoke_error=None,
         bind_error=None, credential=CREDENTIAL, rest=False, session_less=False, desktop_states=None,
         context=None, outcome=None, stop_error=None, delete_error=None, mint_error=None, table=None,
         read=None, others_error=None, enabled=True):
    order = []
    # The handler updates the row in place after each collaborator returns, and a MagicMock records
    # the reference it was given; these keep each row as it was handed over.
    revoked_rows = []
    bootstrapped_rows = []
    cleaned_rows = []
    results = {"start_desktop": start_error if start_error is not None else {}}
    if stop_error is not None:
        results["stop_desktop"] = stop_error
    if delete_error is not None:
        results["delete_desktop"] = delete_error
    if desktop_states is not None:
        results["get_desktop_states"] = desktop_states
    edh = _OrderedEdh(order, **results)
    fakes = install_service_fakes(monkeypatch, claims=claims, edh=edh)
    fakes.table.query.return_value = {"Items": [row]}
    if table is not None:
        fakes.table.update_item.side_effect = table
    if read is not None:
        # The table's own reads, where they answer other than the row the session index serves.
        fakes.table.get_item.side_effect = read

    def _revoke(event, row_arg, *, delete_secret, actor, every_key):
        order.append("revoke")
        revoked_rows.append(dict(row_arg))
        if revoke_error is not None:
            raise revoke_error
        return dict(CLEARED_ALL if delete_secret else CLEARED_KEY)

    def _session(event, row_arg, mfa_enabled, *, deadline=None):
        order.append("bootstrap")
        bootstrapped_rows.append(dict(row_arg))
        if mint_error is not None:
            raise mint_error
        return (outcome or ("provisioned" if credential else "skipped")), dict(credential)

    def _bind(row_arg, instance_id):
        order.append("bind")
        if bind_error is not None:
            raise bind_error
        return {"secretBoundInstanceId": instance_id}

    def _delete_others(event, row_arg, *, keep, actor, deadline):
        order.append("others")
        cleaned_rows.append(dict(row_arg))
        if others_error is not None:
            raise others_error

    fakes.revoked_rows = revoked_rows
    fakes.bootstrapped_rows = bootstrapped_rows
    fakes.cleaned_rows = cleaned_rows
    revoke_fn = MagicMock(side_effect=_revoke)
    session_fn = MagicMock(side_effect=_session)
    bind_fn = MagicMock(side_effect=_bind)
    fakes.bind_fn = bind_fn
    others_fn = MagicMock(side_effect=_delete_others)
    fakes.others_fn = others_fn
    monkeypatch.setattr(bootstrap, "session_bootstrap_enabled", enabled)
    monkeypatch.setattr(bootstrap, "revoke_session", revoke_fn)
    monkeypatch.setattr(bootstrap, "delete_other_session_keys", others_fn)
    monkeypatch.setattr(bootstrap, "bootstrap_session", session_fn)
    monkeypatch.setattr(bootstrap, "bind_session_secret", bind_fn)
    identifier = BOOTSTRAP_OWN if session_less else SESSION_OWN
    if session_less:
        fakes.table.query.return_value = {"Items": []}
        fakes.table.get_item.return_value = {"Item": row}
    path_params = None if rest else {"sessionId": identifier}
    event = make_event(method, workstation_path(identifier, action), path_params=path_params, rest=rest)
    response = service.lambda_handler(event, context if context is not None else MagicMock())
    return response, fakes, revoke_fn, session_fn, order


@pytest.mark.unit
class TestStopRevokesAndKeepsTheSecret:
    def test_stop_revokes_after_the_edh_acknowledgement_and_keeps_the_container(self, monkeypatch):
        response, fakes, revoke_fn, _session_fn, order = _run(monkeypatch, "POST", "stop", _keyed_row())
        assert response["statusCode"] == 200
        assert order == ["stop_desktop", "revoke"]
        assert revoke_fn.call_args.args[1]["bootstrapId"] == BOOTSTRAP_OWN
        assert revoke_fn.call_args.kwargs == {"delete_secret": False, "actor": USER, "every_key": True}
        assert written_values(fakes, "state") == [service.STATE_STOPPED]
        assert written_values(fakes, "apiKeyId") == [""]
        assert written_values(fakes, "secretName") == []

    @pytest.mark.parametrize("error", [bootstrap.SessionBootstrapError("down"), RuntimeError("boom")],
                             ids=["bootstrap", "unexpected"])
    def test_a_revoke_failure_does_not_fail_the_stop(self, monkeypatch, error):
        response, fakes, _revoke_fn, _session_fn, _order = _run(
            monkeypatch, "POST", "stop", _keyed_row(), revoke_error=error)
        assert response["statusCode"] == 200
        assert written_values(fakes, "state") == [service.STATE_STOPPED]
        assert written_values(fakes, "apiKeyId") == []

    def test_a_row_without_a_credential_skips_the_revoke(self, monkeypatch):
        response, _fakes, revoke_fn, _session_fn, order = _run(monkeypatch, "POST", "stop", own_row())
        assert response["statusCode"] == 200
        revoke_fn.assert_not_called()
        assert order == ["stop_desktop"]

    def test_a_stopped_row_is_given_no_expiry(self, monkeypatch):
        # The desktop still exists; only a DELETED row is left to the table's TTL.
        _response, fakes, _revoke_fn, _session_fn, _order = _run(monkeypatch, "POST", "stop", _keyed_row())
        assert written_values(fakes, "expiresAt") == []


@pytest.mark.unit
class TestDeleteRevokesAndRemovesTheSecret:
    def test_delete_force_deletes_the_secret(self, monkeypatch):
        response, fakes, revoke_fn, _session_fn, order = _run(monkeypatch, "DELETE", None, _keyed_row())
        assert response["statusCode"] == 200
        assert order == ["delete_desktop", "revoke"]
        assert revoke_fn.call_args.kwargs == {"delete_secret": True, "actor": USER, "every_key": True}
        assert written_values(fakes, "state") == [service.STATE_DELETED]
        assert written_values(fakes, "secretName") == [""]

    def test_the_expiry_is_written_with_the_cleared_credential_once_the_revoke_succeeded(self, monkeypatch):
        response, fakes, _revoke_fn, _session_fn, _order = _run(monkeypatch, "DELETE", None, _keyed_row())
        (expiry,) = written_values(fakes, "expiresAt")
        assert is_about_a_week_ahead(expiry)
        # The state lands first; the expiry travels with the cleared credential attributes, never before.
        assert update_attribute_sets(fakes) == [{"state", "connectionAvailable"}, {*CLEARED_ALL, "expiresAt"}]
        assert "expiresAt" not in response_body(response)

    def test_a_delete_of_a_row_without_a_credential_writes_the_expiry_with_the_state(self, monkeypatch):
        response, fakes, revoke_fn, _session_fn, _order = _run(monkeypatch, "DELETE", None, own_row())
        assert response["statusCode"] == 200
        revoke_fn.assert_not_called()
        assert update_attribute_sets(fakes) == [{"state", "connectionAvailable", "expiresAt"}]
        (expiry,) = written_values(fakes, "expiresAt")
        assert is_about_a_week_ahead(expiry)
        assert "expiresAt" not in response_body(response)

    @pytest.mark.parametrize("error", [bootstrap.SessionBootstrapError("down"), RuntimeError("boom")],
                             ids=["bootstrap", "unexpected"])
    def test_a_revoke_that_fails_on_delete_leaves_the_row_without_an_expiry(self, monkeypatch, error):
        # The credential is still out there; the reconciler takes it back and stamps the row then.
        response, fakes, _revoke_fn, _session_fn, _order = _run(
            monkeypatch, "DELETE", None, _keyed_row(), revoke_error=error)
        assert response["statusCode"] == 200
        assert written_values(fakes, "state") == [service.STATE_DELETED]
        assert written_values(fakes, "expiresAt") == []

    def test_a_row_with_a_secret_but_no_key_still_removes_the_secret(self, monkeypatch):
        _response, _fakes, revoke_fn, _session_fn, _order = _run(
            monkeypatch, "DELETE", None, own_row(secretName=SECRET_NAME))
        revoke_fn.assert_called_once()
        assert revoke_fn.call_args.kwargs == {"delete_secret": True, "actor": USER, "every_key": True}

    def test_deleting_a_session_less_orphaned_launch_takes_its_credential_back(self, monkeypatch):
        row = _keyed_row(state="ORPHANED", edhState=None, connectionAvailable=False, lastError="QUOTA_EXCEEDED")
        row.pop("sessionId")
        response, fakes, revoke_fn, _session_fn, order = _run(
            monkeypatch, "DELETE", None, row, session_less=True)
        assert response["statusCode"] == 200
        assert fakes.edh.calls == []
        assert order == ["revoke"]
        assert revoke_fn.call_args.kwargs == {"delete_secret": True, "actor": USER, "every_key": True}
        assert written_values(fakes, "state") == [service.STATE_DELETED]
        assert written_values(fakes, "secretName") == [""]
        (expiry,) = written_values(fakes, "expiresAt")
        assert is_about_a_week_ahead(expiry)

    def test_deleting_a_session_less_launch_without_a_credential_writes_the_expiry_with_the_state(self, monkeypatch):
        row = own_row(state="ORPHANED", edhState=None, connectionAvailable=False, lastError="QUOTA_EXCEEDED")
        row.pop("sessionId")
        response, fakes, revoke_fn, _session_fn, _order = _run(monkeypatch, "DELETE", None, row, session_less=True)
        assert response["statusCode"] == 200
        revoke_fn.assert_not_called()
        assert update_attribute_sets(fakes) == [{"state", "expiresAt"}]


@pytest.mark.unit
class TestStartReMintsBeforeEdhStarts:
    def test_an_api_key_principal_cannot_start(self, monkeypatch):
        response, fakes, revoke_fn, session_fn, order = _run(
            monkeypatch, "POST", "start", own_row(state="STOPPED"), claims=API_KEY_CLAIMS)
        assert response["statusCode"] == 403
        fakes.table.query.assert_not_called()
        fakes.client_factory.assert_not_called()
        revoke_fn.assert_not_called()
        session_fn.assert_not_called()
        assert order == []


    def test_a_rest_shaped_stop_with_null_path_parameters_revokes(self, monkeypatch):
        response, _fakes, revoke_fn, _session_fn, order = _run(
            monkeypatch, "POST", "stop", _keyed_row(), rest=True)
        assert response["statusCode"] == 200
        revoke_fn.assert_called_once()
        assert order == ["stop_desktop", "revoke"]


class _Clock:
    """The monotonic clock the handler and the bootstrap module both read; the fakes move it."""

    def __init__(self, now):
        self.now = now

    def __call__(self):
        return self.now


class _ClockedEdh(_OrderedEdh):
    """_OrderedEdh whose answer to each wrapper ``seconds`` names takes that long on the clock."""

    def __init__(self, order, clock, seconds, **results):
        super().__init__(order, **results)
        self._clock = clock
        self._seconds = seconds

    def _answer(self, wrapper, *args):
        self._clock.now += self._seconds.get(wrapper, 0.0)
        return super()._answer(wrapper, *args)


class _ClockedKeys(FakeApiKeyService):
    """FakeApiKeyService whose first invoke takes ``first`` seconds on the clock and every later one
    ``then``; each is put on the order list as its method, a delete with the key id it names."""

    def __init__(self, order, clock, *, first, then):
        super().__init__()
        self._order = order
        self._clock = clock
        self._durations = iter([first])
        self._then = then

    def invoke(self, **kwargs):
        sent = json.loads(kwargs["Payload"].decode("utf-8"))
        method = sent["requestContext"]["http"]["method"]
        self._order.append(f"DELETE {sent['pathParameters']['apiKeyId']}" if method == "DELETE" else method)
        self._clock.now += next(self._durations, self._then)
        return super().invoke(**kwargs)


class _TimedKeys(FakeApiKeyService):
    """FakeApiKeyService whose every invoke takes ``seconds`` on the clock or, with ``hangs``, runs out its
    whole connect and read timeout and raises the read timeout; each is put on the order list as its method,
    a delete with the key id it names."""

    def __init__(self, order, clock, *, seconds=0.0, hangs=False):
        super().__init__()
        self._order = order
        self._clock = clock
        self._seconds = seconds
        self._hangs = hangs

    def invoke(self, **kwargs):
        sent = json.loads(kwargs["Payload"].decode("utf-8"))
        method = sent["requestContext"]["http"]["method"]
        self._order.append(f"DELETE {sent['pathParameters']['apiKeyId']}" if method == "DELETE" else method)
        if self._hangs:
            self._clock.now += bootstrap.CROSS_CALL_TIMEOUT_SECONDS
            raise ReadTimeoutError(endpoint_url="https://lambda.us-east-1.amazonaws.com")
        self._clock.now += self._seconds
        return super().invoke(**kwargs)


def _listed(state):
    """EDH's record of the caller's desktop in ``state``, as its session-state read gives it."""
    return state_record(SESSION_OWN, state)


@pytest.mark.unit
class TestStartRefusesADesktopThatIsNotStopped:
    """EDH starts a stopped desktop only; a start of a running one would revoke the credential the desktop
    is using and mint one it never reads."""

    def test_a_running_desktop_is_refused_and_its_credential_left_alone(self, monkeypatch):
        response, fakes, revoke_fn, session_fn, order = _run(
            monkeypatch, "POST", "start", _keyed_row(), desktop_states=[_listed("running")])
        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is already running"
        assert service.RESPONSE_FIELD_SESSION_BOOTSTRAP not in response_body(response)
        assert order == ["get_desktop_states"]
        revoke_fn.assert_not_called()
        session_fn.assert_not_called()
        assert written_values(fakes, "apiKeyId") == []
        assert written_values(fakes, "state") == []


    def test_a_stale_stopped_row_of_a_desktop_edh_reports_running_is_refused_before_any_credential_work(
            self, monkeypatch):
        response, fakes, revoke_fn, session_fn, order = _run(
            monkeypatch, "POST", "start", _keyed_row(state="STOPPED", edhState="stopped", connectionAvailable=False),
            desktop_states=[_listed("running")])
        assert response["statusCode"] == 409
        assert order == ["get_desktop_states"]
        # The leftover key is not replaced either: the desktop that EDH reports running may be using it.
        revoke_fn.assert_not_called()
        session_fn.assert_not_called()
        assert written_values(fakes, "apiKeyId") == []

    @pytest.mark.parametrize("state", ["CREATING", "ORPHANED"])
    def test_a_desktop_in_any_other_state_is_not_stopped(self, monkeypatch, state):
        response, _fakes, revoke_fn, session_fn, order = _run(
            monkeypatch, "POST", "start", own_row(state=state, edhState=None, connectionAvailable=False))
        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is not stopped"
        assert order == ["get_desktop_states"]
        revoke_fn.assert_not_called()
        session_fn.assert_not_called()


@pytest.mark.unit
class TestStartWaitsForEdhToListTheDesktopStopped:
    """VAMS holds a desktop EDH reports stopping or interrupted STOPPED, but EDH starts only one its record
    shows stopped and refuses any other as already running. A start of a desktop EDH reports in any state
    but stopped is refused, naming that state, before anything is minted, claimed or asked of EDH; a
    desktop EDH reports stopped, or reports without a state, is started as before. The state read is
    EDH's session state, not the one the row last stored: a stop leaves the row the state EDH reported
    before it."""

    @pytest.mark.parametrize("edh_state", ["stopping", "interrupting", "interrupted", "hibernating"],
                             ids=["stopping", "interrupting", "interrupted", "outside the mapping"])
    def test_a_desktop_edh_lists_short_of_stopped_is_refused_before_anything_is_minted(
            self, monkeypatch, edh_state):
        response, fakes, revoke_fn, session_fn, order = _run(
            monkeypatch, "POST", "start", _keyed_row(state="STOPPED", connectionAvailable=False),
            desktop_states=[_listed(edh_state)])
        assert response["statusCode"] == 409
        assert response_body(response) == {
            "message": f"The workstation is still {edh_state} in EDH; start it again once it has stopped."}
        # EDH is listed and nothing else: no start is sent, nothing is minted and the kept key is not replaced.
        assert order == ["get_desktop_states"]
        revoke_fn.assert_not_called()
        session_fn.assert_not_called()
        assert written_values(fakes, "apiKeyId") == []
        # No claim is written, so a start once EDH lists the desktop stopped is not held off.
        assert written_values(fakes, "startRequestedAt") == []
        # The row still follows EDH's listing.
        assert written_values(fakes, "edhState") == [edh_state]
        assert written_values(fakes, "state") == []


def _lost(route):
    """The client's error for a desktop call sent once whose answer never came."""
    return EdhOutcomeUnknownError(f"EDH did not answer PUT {route}; it may have been carried out")


def _stopped_row(**overrides):
    return own_row(state="STOPPED", edhState="stopped", instanceId=INSTANCE_ID, **overrides)


@pytest.mark.unit
class TestAStopWhoseAnswerIsLost:
    """A stop EDH carried out but answered too late is finished exactly as an answered one once EDH lists
    the desktop stopping or stopped; any other reading answers the lost stop and leaves the row and its
    credential to the next read and to the reconciler."""

    @pytest.mark.parametrize("edh_state", ["stopping", "stopped"])
    def test_a_desktop_edh_lists_as_stopping_finishes_the_stop(self, monkeypatch, edh_state):
        response, fakes, revoke_fn, _session_fn, order = _run(
            monkeypatch, "POST", "stop", _keyed_row(), stop_error=_lost("stop"),
            desktop_states=[_listed(edh_state)])
        assert response["statusCode"] == 200
        assert order == ["stop_desktop", "get_desktop_states", "revoke"]
        assert revoke_fn.call_args.kwargs == {"delete_secret": False, "actor": USER, "every_key": True}
        assert written_values(fakes, "state")[-1] == service.STATE_STOPPED
        assert written_values(fakes, "connectionAvailable")[-1] is False
        assert written_values(fakes, "apiKeyId") == [""]
        body = response_body(response)
        assert (body["state"], body["connectionAvailable"]) == (service.STATE_STOPPED, False)

    @pytest.mark.parametrize("second_read", [[_listed("running")], [_listed("pending")], []],
                             ids=["running", "pending", "not listed"])
    def test_a_desktop_edh_does_not_list_as_stopping_fails_the_stop(self, monkeypatch, second_read):
        response, fakes, revoke_fn, _session_fn, order = _run(
            monkeypatch, "POST", "stop", _keyed_row(), stop_error=_lost("stop"), desktop_states=second_read)
        assert response["statusCode"] == 502
        assert response_body(response)["message"] == LOST_ANSWER_MESSAGE
        assert response_body(response)[OUTCOME_UNKNOWN_FIELD] is True
        assert order == ["stop_desktop", "get_desktop_states"]
        revoke_fn.assert_not_called()
        assert service.STATE_STOPPED not in written_values(fakes, "state")

    def test_a_second_read_that_fails_answers_the_lost_stop(self, monkeypatch):
        response, _fakes, revoke_fn, _session_fn, order = _run(
            monkeypatch, "POST", "stop", _keyed_row(), stop_error=_lost("stop"),
            desktop_states=EdhApiError("refused"))
        assert response["statusCode"] == 502
        assert response_body(response)["message"] == LOST_ANSWER_MESSAGE
        assert response_body(response)[OUTCOME_UNKNOWN_FIELD] is True
        assert order == ["stop_desktop", "get_desktop_states"]
        revoke_fn.assert_not_called()

    def test_a_stop_that_never_reached_edh_is_not_read_again(self, monkeypatch):
        response, _fakes, revoke_fn, _session_fn, order = _run(
            monkeypatch, "POST", "stop", _keyed_row(), stop_error=EdhUnavailableError("connect timeout"),
            desktop_states=[_listed("stopping")])
        assert response["statusCode"] == 502
        assert response_body(response)["message"] == UNAVAILABLE_MESSAGE
        assert OUTCOME_UNKNOWN_FIELD not in response_body(response)
        assert order == ["stop_desktop"]
        revoke_fn.assert_not_called()


@pytest.mark.unit
class TestADeleteWhoseAnswerIsLost:
    """EDH lists every desktop it has not deleted, so one it no longer lists after a delete whose answer
    never came was deleted, and the deletion is recorded as an answered one is; a desktop it still lists
    answers the lost delete."""

    def test_a_desktop_edh_no_longer_lists_is_recorded_deleted(self, monkeypatch):
        response, fakes, revoke_fn, _session_fn, order = _run(
            monkeypatch, "DELETE", None, _keyed_row(), delete_error=_lost("delete"), desktop_states=[])
        assert response["statusCode"] == 200
        assert order == ["delete_desktop", "get_desktop_states", "revoke"]
        assert revoke_fn.call_args.kwargs == {"delete_secret": True, "actor": USER, "every_key": True}
        assert written_values(fakes, "state") == [service.STATE_DELETED]
        assert written_values(fakes, "secretName") == [""]

    @pytest.mark.parametrize("edh_state", ["running", "stopped"])
    def test_a_desktop_edh_still_lists_fails_the_delete(self, monkeypatch, edh_state):
        response, fakes, revoke_fn, _session_fn, order = _run(
            monkeypatch, "DELETE", None, _keyed_row(), delete_error=_lost("delete"),
            desktop_states=[_listed(edh_state)])
        assert response["statusCode"] == 502
        assert response_body(response)["message"] == LOST_ANSWER_MESSAGE
        assert response_body(response)[OUTCOME_UNKNOWN_FIELD] is True
        assert order == ["delete_desktop", "get_desktop_states"]
        revoke_fn.assert_not_called()
        assert service.STATE_DELETED not in written_values(fakes, "state")

    def test_a_second_read_that_fails_answers_the_lost_delete(self, monkeypatch):
        response, fakes, revoke_fn, _session_fn, order = _run(
            monkeypatch, "DELETE", None, _keyed_row(), delete_error=_lost("delete"),
            desktop_states=EdhApiError("refused"))
        assert response["statusCode"] == 502
        assert response_body(response)["message"] == LOST_ANSWER_MESSAGE
        assert response_body(response)[OUTCOME_UNKNOWN_FIELD] is True
        assert order == ["delete_desktop", "get_desktop_states"]
        revoke_fn.assert_not_called()
        assert written_values(fakes, "state") == []

    def test_a_delete_that_never_reached_edh_is_not_read_again(self, monkeypatch):
        response, _fakes, revoke_fn, _session_fn, order = _run(
            monkeypatch, "DELETE", None, _keyed_row(), delete_error=EdhUnavailableError("connect timeout"),
            desktop_states=[])
        assert response["statusCode"] == 502
        assert response_body(response)["message"] == UNAVAILABLE_MESSAGE
        assert OUTCOME_UNKNOWN_FIELD not in response_body(response)
        assert order == ["delete_desktop"]
        revoke_fn.assert_not_called()


def _seconds_ago(seconds):
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _in_flight_row(age_seconds, **overrides):
    """A stopped row a start whose outcome stayed unknown left ``age_seconds`` ago, holding its key and
    the secret bound to its instance."""
    return _stopped_row(startRequestedAt=_seconds_ago(age_seconds), apiKeyId="key-2", secretName=SECRET_NAME,
                        secretBoundInstanceId=INSTANCE_ID, **overrides)


def _whole_seconds_ago(seconds):
    """A stamp without the fraction of a second: the form an instant on a whole second is written in."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).replace(microsecond=0).isoformat()


@pytest.mark.unit
class TestAStartAlreadyInProgress:
    """A start whose outcome stays unknown may still be under way in EDH, which accepts a second start of a
    desktop its record still shows stopped. Every start claims the row with the time of the start before
    anything is minted, by a write that holds only while no other start's claim is younger than the window;
    for that while a start of a desktop EDH still lists stopped is refused before anything is minted or EDH
    is asked. A start that did not happen gives its claim up, one whose outcome stays unknown keeps it, and
    a desktop seen leaving `stopped`, a start EDH took and a stop EDH carried out clear it."""


    def test_the_claim_is_written_before_the_mint_to_the_microsecond_in_utc(self, monkeypatch):
        # One fixed-width form for every claim is what lets the table compare two of them as strings.
        _response, fakes, _revoke_fn, _session_fn, _order = _run(
            monkeypatch, "POST", "start", _stopped_row(), desktop_states=[_listed("stopped")])
        # The merge of EDH's listing is a conditional write as well; the claim is the one that names the stamp.
        (claim,) = [call.kwargs for call in fakes.table.update_item.call_args_list
                    if "ConditionExpression" in call.kwargs
                    and "startRequestedAt" in call.kwargs["ExpressionAttributeNames"].values()]
        # The claim writes the stamp alone; the state it names is its condition's.
        written = {name for placeholder, name in claim["ExpressionAttributeNames"].items()
                   if placeholder.startswith("#a")}
        assert written == {"startRequestedAt", "updatedAt"}
        (stamp,) = written_values(fakes, "startRequestedAt")[:1]
        assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{6}\+00:00", stamp)
        assert fakes.bootstrapped_rows[0]["startRequestedAt"] == stamp

    def test_a_start_while_another_holds_a_fresh_claim_is_refused_before_anything_is_minted(
            self, monkeypatch):
        # The other start has claimed the row and is still minting: no fresh key yet, nothing asked of EDH.
        claim = _seconds_ago(2)
        table = ConditionalRowTable(startRequestedAt=claim)
        response, fakes, revoke_fn, session_fn, order = _run(
            monkeypatch, "POST", "start", _stopped_row(startRequestedAt=claim, apiKeyId="key-1"),
            desktop_states=[_listed("stopped")], table=table)
        assert response["statusCode"] == 409
        assert response_body(response) == {"message": IN_PROGRESS_MESSAGE, START_IN_PROGRESS_FIELD: True}
        assert order == ["get_desktop_states"]
        revoke_fn.assert_not_called()
        session_fn.assert_not_called()
        assert written_values(fakes, "startRequestedAt") == []
        assert table.item["startRequestedAt"] == claim

    @pytest.mark.parametrize("read", [{}, {"startRequestedAt": None}], ids=["no stamp", "cleared stamp"])
    @pytest.mark.parametrize("stamp_of, age_seconds", [(_seconds_ago, 1), (_seconds_ago, 170),
                                                       (_whole_seconds_ago, 170)],
                             ids=["1 s old", "170 s old", "170 s old, whole seconds"])
    def test_a_start_that_loses_the_claim_to_one_that_read_the_row_alongside_it_is_refused(
            self, monkeypatch, read, stamp_of, age_seconds):
        # Both starts read the row without a claim; the other one's claim reached the table first.
        stored = stamp_of(age_seconds)
        table = ConditionalRowTable(startRequestedAt=stored)
        response, fakes, revoke_fn, session_fn, order = _run(
            monkeypatch, "POST", "start", _stopped_row(apiKeyId="key-1", **read),
            desktop_states=[_listed("stopped")], table=table)
        assert response["statusCode"] == 409
        assert response_body(response) == {"message": IN_PROGRESS_MESSAGE, START_IN_PROGRESS_FIELD: True}
        # The kept key is not replaced, nothing is minted and EDH is not asked to start the desktop.
        assert order == ["get_desktop_states"]
        revoke_fn.assert_not_called()
        session_fn.assert_not_called()
        assert table.item["startRequestedAt"] == stored


    def test_a_mint_that_raises_gives_up_the_claim(self, monkeypatch):
        table = ConditionalRowTable()
        response, fakes, _revoke_fn, _session_fn, order = _run(
            monkeypatch, "POST", "start", _stopped_row(), mint_error=RuntimeError("boom"),
            desktop_states=[_listed("stopped")], table=table)
        assert response["statusCode"] == 500
        assert order == ["get_desktop_states", "bootstrap"]
        claim, released = written_values(fakes, "startRequestedAt")
        assert claim is not None and released is None
        assert table.item["startRequestedAt"] is None


    @pytest.mark.parametrize("age_seconds", [1, 170])
    def test_a_retry_inside_the_window_edh_still_lists_stopped_is_refused_before_anything_is_minted(
            self, monkeypatch, age_seconds):
        response, fakes, revoke_fn, session_fn, order = _run(
            monkeypatch, "POST", "start", _in_flight_row(age_seconds), desktop_states=[_listed("stopped")])
        assert response["statusCode"] == 409
        # A definite refusal that names the start in progress, not an unknown outcome.
        assert response_body(response) == {"message": IN_PROGRESS_MESSAGE, START_IN_PROGRESS_FIELD: True}
        # The fresh read happens; the mint, the replacement of the kept key and EDH's start do not.
        assert order == ["get_desktop_states"]
        session_fn.assert_not_called()
        revoke_fn.assert_not_called()
        assert written_values(fakes, "apiKeyId") == []
        assert written_values(fakes, "startRequestedAt") == []


    @pytest.mark.parametrize("edh_state,message", [("running", "Workstation is already running"),
                                                   ("pending", "Workstation is not stopped")])
    def test_a_retry_that_finds_the_desktop_started_answers_as_before_and_clears_the_stamp(
            self, monkeypatch, edh_state, message):
        response, fakes, _revoke_fn, session_fn, order = _run(
            monkeypatch, "POST", "start", _in_flight_row(30), desktop_states=[_listed(edh_state)])
        assert response["statusCode"] == 409
        assert response_body(response) == {"message": message}
        assert order == ["get_desktop_states"]
        session_fn.assert_not_called()
        assert written_values(fakes, "startRequestedAt") == [None]

    def test_a_read_that_sees_the_desktop_started_clears_the_stamp(self, monkeypatch):
        response, fakes, _revoke_fn, _session_fn, _order = _run(
            monkeypatch, "GET", None, _in_flight_row(30), desktop_states=[_listed("running")])
        assert response["statusCode"] == 200
        assert written_values(fakes, "startRequestedAt") == [None]
        assert "startRequestedAt" not in response_body(response)

    def test_a_read_that_still_sees_the_desktop_stopped_keeps_the_stamp(self, monkeypatch):
        response, fakes, _revoke_fn, _session_fn, _order = _run(
            monkeypatch, "GET", None, _in_flight_row(30), desktop_states=[_listed("stopped")])
        assert response["statusCode"] == 200
        assert written_values(fakes, "startRequestedAt") == []
        assert "startRequestedAt" not in response_body(response)

    def test_a_stop_edh_carried_out_clears_the_stamp(self, monkeypatch):
        row = _keyed_row(state="ACTIVE", edhState="running", startRequestedAt=_seconds_ago(30))
        response, fakes, _revoke_fn, _session_fn, _order = _run(monkeypatch, "POST", "stop", row)
        assert response["statusCode"] == 200
        assert written_values(fakes, "startRequestedAt") == [None]

    def test_a_stop_of_a_row_without_a_stamp_writes_none(self, monkeypatch):
        _response, fakes, _revoke_fn, _session_fn, _order = _run(monkeypatch, "POST", "stop", _keyed_row())
        assert written_values(fakes, "startRequestedAt") == []


@pytest.mark.unit
class TestAnOwnerActionRevokesTheKeyTheRowHoldsNow:
    """The session index lags the table: just after a start it can still serve the row as it was before,
    holding the key the start replaced. A stop or delete reads the row it acts on from the table itself."""

    @pytest.mark.parametrize("method, action", [("POST", "stop"), ("DELETE", None)], ids=["stop", "delete"])
    def test_the_revoke_is_handed_the_key_a_start_just_wrote_not_the_one_the_index_serves(
            self, monkeypatch, method, action):
        lagging = _stopped_row(apiKeyId="key-1", secretName=SECRET_NAME)
        current = _keyed_row(state="CREATING", edhState="pending", connectionAvailable=False, apiKeyId="key-fresh")

        def read(**kwargs):
            return {"Item": dict(current if kwargs.get("ConsistentRead") else lagging)}

        response, fakes, _revoke_fn, _session_fn, _order = _run(
            monkeypatch, method, action, lagging, read=read)

        assert response["statusCode"] == 200
        assert fakes.revoked_rows, "the credential the row holds was not taken back"
        assert {revoked["apiKeyId"] for revoked in fakes.revoked_rows} == {"key-fresh"}


def _marked_deleted(table):
    table.item = {**table.item, "state": service.STATE_DELETED, "connectionAvailable": False}


def _removed(table):
    table.item = None


@pytest.mark.unit
class TestAStartOfARowDeletedSinceItsRead:
    """A delete of the workstation lands after a start has read the row as stopped and before the start's
    claim reaches the table. The claim does not take a DELETED or removed row, and the start is answered as
    a start of a row read DELETED is: nothing is minted or revoked, and EDH is not asked to start it."""

    @staticmethod
    def _refusal_of_a_row_read_deleted(monkeypatch):
        response, *_ = _run(monkeypatch, "POST", "start", own_row(state="DELETED", connectionAvailable=False))
        return response["statusCode"], response_body(response)

    @pytest.mark.parametrize("delete", [_marked_deleted, _removed], ids=["marked deleted", "removed"])
    def test_the_start_mints_nothing_and_is_refused_as_a_start_of_a_deleted_row(self, monkeypatch, delete):
        expected = self._refusal_of_a_row_read_deleted(monkeypatch)
        row = _stopped_row(apiKeyId="key-1", secretName=SECRET_NAME)
        table = ConditionalRowTable(**row)
        after_delete = []

        def update_item(**kwargs):
            if not after_delete:
                delete(table)
                after_delete.append(None if table.item is None else dict(table.item))
            return table(**kwargs)

        def read(**kwargs):
            # A read that is not consistent may still answer the row as it was before the delete.
            return table.read(**kwargs) if kwargs.get("ConsistentRead") else {"Item": dict(row)}

        response, fakes, revoke_fn, session_fn, order = _run(
            monkeypatch, "POST", "start", row, desktop_states=[_listed("stopped")], table=update_item, read=read)

        assert (response["statusCode"], response_body(response)) == expected
        assert START_IN_PROGRESS_FIELD not in response_body(response)
        session_fn.assert_not_called()
        revoke_fn.assert_not_called()
        assert "start_desktop" not in order
        # Nothing the start wrote landed over the deletion.
        assert after_delete and table.item == after_delete[0]

