# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Owner check and Tier-2 on the {sessionId} routes.

Every negative (another user's session, a denied Casbin verdict, an unknown session) asserts that the
EDH mutation was NOT attempted and the row was NOT updated; each is paired with the positive control
on the caller's own session, because "refused" alone is satisfied by a handler that refuses everyone.
Modelled on test_apiKeyService_user_scope.py.

The path identifier is the EDH session id or the row's bootstrap id, which addresses the row before and after
EDH assigns the session id; a session-less row has nothing in EDH to act on, so those cases assert no EDH client
was constructed at all.
"""

from datetime import datetime, timedelta, timezone

import pytest

from backend.backend.handlers.addon.edh.common.edhClient import EdhNotFoundError, EdhUnavailableError
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    BOOTSTRAP_OTHER,
    BOOTSTRAP_OWN,
    SESSION_OTHER,
    SESSION_OWN,
    USER,
    FakeEdhClient,
    edh_proxy_records,
    install_service_fakes,
    make_event,
    other_row,
    own_row,
    response_body,
    state_record,
    workstation_path,
)
import backend.backend.handlers.addon.edh.edhWorkstationService as service

# route -> (HTTP method, path action, client wrapper, row state afterwards)
ACTIONS = {
    "get": ("GET", None, "get_desktop_states", "ACTIVE"),
    "stop": ("POST", "stop", "stop_desktop", "STOPPED"),
    "start": ("POST", "start", "start_desktop", "CREATING"),
    "delete": ("DELETE", None, "delete_desktop", "DELETED"),
}
MUTATIONS = ("stop", "start", "delete")


def _key_condition(condition):
    expression = condition.get_expression()
    return expression['values'][0].name, expression['operator'], expression['values'][1]


def _event(action, session_id, rest=False, path_params="default"):
    method, segment, _, _ = ACTIONS[action]
    params = {"sessionId": session_id} if path_params == "default" else path_params
    return make_event(method, workstation_path(session_id, segment), path_params=params, rest=rest)


def _call(fakes, action, session_id, **event_kwargs):
    return fakes.module.lambda_handler(_event(action, session_id, **event_kwargs), {})


def _session_less_row(**overrides):
    """The caller's row for a launch EDH has not acknowledged: no sessionId at all."""
    row = own_row(state="CREATING", edhState=None, connectionAvailable=False)
    row.pop("sessionId")
    row.update(overrides)
    return row


def _minutes_ago(minutes):
    return (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()


def _update_values(fakes):
    return [value for placeholder, value in fakes.table.update_item.call_args[1]["ExpressionAttributeValues"].items()
            if placeholder.startswith(":v")]


@pytest.mark.unit
class TestOwnership:
    @pytest.mark.parametrize("action", sorted(ACTIONS))
    def test_another_users_workstation_is_not_found_and_not_touched(self, monkeypatch, action):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {"Items": [other_row()]}
        method, _, wrapper, _ = ACTIONS[action]

        response = _call(fakes, action, SESSION_OTHER)

        assert response["statusCode"] == 404
        assert response_body(response)["message"] == "Workstation not found"
        assert fakes.edh.calls == []
        fakes.table.update_item.assert_not_called()
        fakes.enforcer.enforce.assert_not_called()
        fakes.log_authorization.assert_called_once()
        claims, authorized, custom_data = fakes.log_authorization.call_args.args
        assert authorized is False
        assert custom_data == {"action": method, "obj": {"object__type": "edhWorkstation", "sessionId": SESSION_OTHER}}

    @pytest.mark.parametrize("action", MUTATIONS)
    def test_the_owners_workstation_is_acted_on(self, monkeypatch, action):
        """Positive control for the refusal above: the same call on the caller's own session."""
        fakes = install_service_fakes(monkeypatch)
        # A start is offered to a stopped desktop; the other mutations act on a running one.
        row = own_row(state="STOPPED", edhState="stopped", connectionAvailable=False) if action == "start" else own_row()
        fakes.table.query.return_value = {"Items": [row]}
        _, _, wrapper, state = ACTIONS[action]

        response = _call(fakes, action, SESSION_OWN)

        body = response_body(response)
        if action == "start":
            # A start is handed to the launch worker and answered 202; EDH's start is the worker's (in-dev D10).
            assert response["statusCode"] == 202
            assert fakes.edh.calls_to(wrapper) == []
            fakes.lambda_client.invoke.assert_called_once()
            assert (body["sessionId"], body["state"], body["activity"]) == (SESSION_OWN, "STOPPED", "starting")
            fakes.log_authorization.assert_not_called()
            return
        assert response["statusCode"] == 200
        assert fakes.edh.calls_to(wrapper) == [(edh_uid_for(USER), SESSION_OWN)]
        assert (body["sessionId"], body["state"], body["connectionAvailable"]) == (SESSION_OWN, state, False)
        update = fakes.table.update_item.call_args[1]
        assert update["Key"] == {"bootstrapId": body["bootstrapId"]}
        assert state in update["ExpressionAttributeValues"].values()
        fakes.log_authorization.assert_not_called()

    def test_the_row_is_looked_up_by_the_session_index(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {"Items": [own_row()]}

        _call(fakes, "stop", SESSION_OWN)

        query_kwargs = fakes.table.query.call_args[1]
        assert query_kwargs["IndexName"] == "sessionUuidIndex"
        assert _key_condition(query_kwargs["KeyConditionExpression"]) == ("sessionId", "=", SESSION_OWN)
        fakes.table.scan.assert_not_called()

    def test_get_merges_edh_state_onto_the_owners_row(self, monkeypatch):
        desktop = {
            service.EDH_FIELD_SESSION_UUID: SESSION_OWN,
            service.EDH_FIELD_SESSION_STATE: "stopped",
            service.EDH_FIELD_CONNECTION_STRING: "",
        }
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[desktop]))
        fakes.table.query.return_value = {"Items": [own_row(edhState="running", connectionAvailable=True)]}

        response = _call(fakes, "get", SESSION_OWN)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["state"], body["edhState"], body["connectionAvailable"]) == ("STOPPED", "stopped", False)
        assert fakes.edh.calls_to("get_desktop_states") == [(edh_uid_for(USER), [SESSION_OWN])]
        fakes.table.update_item.assert_called_once()
        assert {"STOPPED", "stopped", False} <= set(_update_values(fakes))

    def test_get_reads_the_state_of_its_one_session(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[state_record(SESSION_OWN)]))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _call(fakes, "get", SESSION_OWN)

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("get_desktop_states") == [(edh_uid_for(USER), [SESSION_OWN])]
        # The display-session check of a running desktop (D21) reads its one session too.
        assert fakes.edh.calls_to("get_desktop_connection") == [(edh_uid_for(USER), SESSION_OWN)]

    def test_an_unknown_session_is_not_found_without_an_audit_record(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)

        response = _call(fakes, "stop", SESSION_OWN)

        assert response["statusCode"] == 404
        assert response_body(response)["message"] == "Workstation not found"
        fakes.log_authorization.assert_not_called()
        assert fakes.edh.calls == []

    @pytest.mark.parametrize("session_id", ["x!", "ab"])
    def test_an_invalid_session_id_is_a_400_before_any_read(self, monkeypatch, session_id):
        fakes = install_service_fakes(monkeypatch)

        response = _call(fakes, "stop", session_id)

        assert response["statusCode"] == 400
        assert "sessionId" in response_body(response)["message"]
        fakes.table.query.assert_not_called()


@pytest.mark.unit
class TestTierTwoFromTheRow:
    @pytest.mark.parametrize("action", sorted(ACTIONS))
    def test_a_denied_verdict_on_the_own_row_is_403_without_edh(self, monkeypatch, action):
        fakes = install_service_fakes(monkeypatch, enforce=lambda obj, act: False)
        fakes.table.query.return_value = {"Items": [own_row(imageId="42", osFamily="linux", instanceType="g5.xlarge")]}
        method, _, _, _ = ACTIONS[action]

        response = _call(fakes, action, SESSION_OWN)

        assert response["statusCode"] == 403
        fakes.enforcer.enforce.assert_called_once_with(
            {"object__type": "edhWorkstation", "edhImageId": "42", "edhOsFamily": "linux",
             "edhInstanceType": "g5.xlarge"},
            method,
        )
        assert fakes.edh.calls == []
        fakes.table.update_item.assert_not_called()


@pytest.mark.unit
class TestEdhOutcomes:
    def test_delete_tolerates_a_desktop_already_gone_in_edh(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(delete_desktop=EdhNotFoundError("gone")))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _call(fakes, "delete", SESSION_OWN)

        assert response["statusCode"] == 200
        assert response_body(response)["state"] == "DELETED"
        assert [record["edhStatus"] for record in edh_proxy_records(fakes)] == ["EdhNotFoundError"]

    def test_edh_unavailable_on_stop_is_502_and_leaves_the_row(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(stop_desktop=EdhUnavailableError("timeout")))
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _call(fakes, "stop", SESSION_OWN)

        assert response["statusCode"] == 502
        fakes.table.update_item.assert_not_called()

    def test_stop_is_audited_as_an_edh_proxy_record(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {"Items": [own_row()]}

        _call(fakes, "stop", SESSION_OWN)

        records = edh_proxy_records(fakes)
        assert [(record["operation"], record["edhStatus"], record["edhUid"]) for record in records] == [
            ("workstationStop", "ok", edh_uid_for(USER))]
        assert (records[0]["edhMethod"], records[0]["edhRoute"]) == service.EDH_AUDIT_ROUTES["stop_desktop"]

    def test_a_rest_shaped_stop_with_null_path_parameters_reads_the_path_segment(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.query.return_value = {"Items": [own_row()]}

        response = _call(fakes, "stop", SESSION_OWN, rest=True, path_params=None)

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("stop_desktop") == [(edh_uid_for(USER), SESSION_OWN)]


@pytest.mark.unit
class TestSessionLessLaunches:
    """A launch EDH never acknowledged is addressed by its bootstrap id and never reaches EDH."""

    def test_an_orphaned_launch_without_a_session_is_deleted_by_its_bootstrap_id(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.get_item.return_value = {
            "Item": _session_less_row(state="ORPHANED", lastError="QUOTA_EXCEEDED", connectionAvailable=False)}

        response = _call(fakes, "delete", BOOTSTRAP_OWN)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["state"], body["lastError"], body["sessionId"]) == ("DELETED", "QUOTA_EXCEEDED", None)
        assert fakes.edh.calls == []
        fakes.table.get_item.assert_called_once_with(Key={"bootstrapId": BOOTSTRAP_OWN}, ConsistentRead=True)
        assert "DELETED" in _update_values(fakes)

    def test_a_creating_launch_without_a_session_cannot_be_deleted_yet(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.get_item.return_value = {"Item": _session_less_row(state="CREATING", updatedAt=_minutes_ago(0))}

        response = _call(fakes, "delete", BOOTSTRAP_OWN)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is still being created"
        fakes.table.update_item.assert_not_called()
        assert fakes.edh.calls == []

    def test_a_creating_launch_the_worker_has_abandoned_is_deleted_locally(self, monkeypatch):
        """Positive control for the refusal above: once the row's last write is older than the launch
        grace window the worker's retries have expired, and the launch record is removed."""
        fakes = install_service_fakes(monkeypatch)
        fakes.table.get_item.return_value = {"Item": _session_less_row(
            state="CREATING", updatedAt=_minutes_ago(service.CREATING_DELETE_GRACE_MINUTES + 1))}

        response = _call(fakes, "delete", BOOTSTRAP_OWN)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["state"], body["sessionId"]) == ("DELETED", None)
        assert fakes.edh.calls == []
        fakes.client_factory.assert_not_called()
        assert "DELETED" in _update_values(fakes)

    @pytest.mark.parametrize("action", ["stop", "start"])
    def test_a_launch_without_a_session_cannot_be_stopped_or_started(self, monkeypatch, action):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.get_item.return_value = {"Item": _session_less_row()}

        response = _call(fakes, action, BOOTSTRAP_OWN)

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation has no EDH session"
        assert fakes.edh.calls == []
        fakes.table.update_item.assert_not_called()

    def test_get_returns_a_session_less_launch_without_asking_edh(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        fakes.table.get_item.return_value = {"Item": _session_less_row()}

        response = _call(fakes, "get", BOOTSTRAP_OWN)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["state"], body["sessionId"]) == ("CREATING", None)
        fakes.client_factory.assert_not_called()

    def test_another_users_session_less_launch_is_not_found(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        row = other_row(state="CREATING", edhState=None, connectionAvailable=False)
        row.pop("sessionId")
        fakes.table.get_item.return_value = {"Item": row}

        response = _call(fakes, "delete", BOOTSTRAP_OTHER)

        assert response["statusCode"] == 404
        assert response_body(response)["message"] == "Workstation not found"
        fakes.log_authorization.assert_called_once()
        assert fakes.edh.calls == []


def _addressed_by_bootstrap_id(fakes, row):
    """Script the table as a {sessionId} route addressed by ``row``'s bootstrap id finds it: the session index
    holds no row of that id, and the consistent read by bootstrap id answers ``row``."""
    fakes.table.query.return_value = {"Items": []}
    fakes.table.get_item.return_value = {"Item": row}


@pytest.mark.unit
class TestBootstrapIdAfterTheSessionId:
    """The bootstrap id keeps addressing the row once EDH has assigned its session id (in-dev D22): the route
    answers the row, and whatever reaches EDH, the response or a log carries the row's session id, never the
    bootstrap id from the path. Ownership and Tier-2 apply as they do by session id."""

    def test_get_answers_the_row_with_its_session_id(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[state_record(SESSION_OWN)]))
        _addressed_by_bootstrap_id(fakes, own_row())

        response = _call(fakes, "get", BOOTSTRAP_OWN)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["bootstrapId"], body["sessionId"], body["state"]) == (BOOTSTRAP_OWN, SESSION_OWN, "ACTIVE")
        assert fakes.edh.calls_to("get_desktop_states") == [(edh_uid_for(USER), [SESSION_OWN])]
        assert BOOTSTRAP_OWN not in str(fakes.edh.calls)
        # The session index is asked first, then the row is read consistently by its key.
        query_kwargs = fakes.table.query.call_args[1]
        assert _key_condition(query_kwargs["KeyConditionExpression"]) == ("sessionId", "=", BOOTSTRAP_OWN)
        assert fakes.table.get_item.call_args_list[0][1] == {"Key": {"bootstrapId": BOOTSTRAP_OWN},
                                                             "ConsistentRead": True}
        fakes.log_authorization.assert_not_called()

    @pytest.mark.parametrize("action", ["stop", "delete"])
    def test_a_mutation_sends_edh_the_session_id(self, monkeypatch, action):
        fakes = install_service_fakes(monkeypatch)
        _addressed_by_bootstrap_id(fakes, own_row())
        _, _, wrapper, state = ACTIONS[action]

        response = _call(fakes, action, BOOTSTRAP_OWN)

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to(wrapper) == [(edh_uid_for(USER), SESSION_OWN)]
        assert BOOTSTRAP_OWN not in str(fakes.edh.calls)
        body = response_body(response)
        assert (body["sessionId"], body["state"]) == (SESSION_OWN, state)
        assert fakes.table.update_item.call_args[1]["Key"] == {"bootstrapId": BOOTSTRAP_OWN}

    def test_start_reads_edh_by_the_session_id_and_answers_it(self, monkeypatch):
        fakes = install_service_fakes(
            monkeypatch, edh=FakeEdhClient(get_desktop_states=[state_record(SESSION_OWN, "stopped")]))
        _addressed_by_bootstrap_id(fakes, own_row(state="STOPPED", edhState="stopped", connectionAvailable=False))

        response = _call(fakes, "start", BOOTSTRAP_OWN)

        assert response["statusCode"] == 202
        assert fakes.edh.calls_to("get_desktop_states") == [(edh_uid_for(USER), [SESSION_OWN])]
        assert BOOTSTRAP_OWN not in str(fakes.edh.calls)
        body = response_body(response)
        assert (body["sessionId"], body["state"], body["activity"]) == (SESSION_OWN, "STOPPED", "starting")
        fakes.lambda_client.invoke.assert_called_once()

    def test_renew_answers_and_audits_the_session_id(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch)
        monkeypatch.setattr(service, "read_lease_policy", lambda: {
            "enabled": True, "leaseHours": 8, "forced": False, "graceHours": 24,
            "updatedAt": None, "updatedBy": None})
        _addressed_by_bootstrap_id(fakes, own_row(state="STOPPED", edhState="stopped", connectionAvailable=False,
                                                  leaseExpiresAt=_minutes_ago(30), leaseStoppedAt=_minutes_ago(30)))
        event = make_event("POST", workstation_path(BOOTSTRAP_OWN, "renew"), path_params={"sessionId": BOOTSTRAP_OWN})

        response = fakes.module.lambda_handler(event, {})

        assert response["statusCode"] == 200
        assert response_body(response)["sessionId"] == SESSION_OWN
        (renew,) = [call.args[2] for call in fakes.log_actions.call_args_list if call.args[1] == "edhWorkstationRenew"]
        assert (renew["sessionId"], renew["bootstrapId"]) == (SESSION_OWN, BOOTSTRAP_OWN)
        assert fakes.edh.calls == []

    @pytest.mark.parametrize("action", sorted(ACTIONS))
    def test_another_users_row_is_not_found_and_audited_with_its_session_id(self, monkeypatch, action):
        fakes = install_service_fakes(monkeypatch)
        _addressed_by_bootstrap_id(fakes, other_row())
        method, _, _, _ = ACTIONS[action]

        response = _call(fakes, action, BOOTSTRAP_OTHER)

        assert response["statusCode"] == 404
        assert response_body(response)["message"] == "Workstation not found"
        assert fakes.edh.calls == []
        fakes.client_factory.assert_not_called()
        fakes.table.update_item.assert_not_called()
        fakes.enforcer.enforce.assert_not_called()
        fakes.log_authorization.assert_called_once()
        _claims, authorized, custom_data = fakes.log_authorization.call_args.args
        assert authorized is False
        assert custom_data == {"action": method, "obj": {"object__type": "edhWorkstation", "sessionId": SESSION_OTHER}}

    def test_a_denied_verdict_is_403_without_edh(self, monkeypatch):
        fakes = install_service_fakes(monkeypatch, enforce=lambda obj, act: False)
        _addressed_by_bootstrap_id(fakes, own_row())

        response = _call(fakes, "stop", BOOTSTRAP_OWN)

        assert response["statusCode"] == 403
        assert fakes.edh.calls == []
        fakes.table.update_item.assert_not_called()

    def test_the_session_index_wins_over_a_bootstrap_id_of_the_same_value(self, monkeypatch):
        """An identifier that is one row's session id and another row's bootstrap id addresses the first."""
        shared = "4b3a2c1d-0e9f-4a8b-9c7d-6e5f4a3b2c1d"
        by_session = own_row(sessionId=shared)
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=[state_record(shared)]))
        fakes.table.query.return_value = {"Items": [by_session]}
        fakes.table.get_item.return_value = {"Item": other_row(bootstrapId=shared)}

        response = _call(fakes, "get", shared)

        assert response["statusCode"] == 200
        body = response_body(response)
        assert (body["bootstrapId"], body["sessionId"]) == (BOOTSTRAP_OWN, shared)
        assert fakes.table.get_item.call_args_list[0][1] == {"Key": {"bootstrapId": BOOTSTRAP_OWN},
                                                             "ConsistentRead": True}
        fakes.log_authorization.assert_not_called()
