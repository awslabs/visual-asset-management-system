# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cluster-wide instance views joined from EDH list_all and the VAMS session table, the stale
derivation, and the admin stop / deactivate actions. EDH's record decides the state and the owner;
the row supplies the launch's identity, name and error code. Both actions take the session's
credential back once EDH has acted, and a revoke that fails leaves the action's own result alone."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from backend.backend.handlers.addon.edh.common import edhClient as ec
from backend.backend.handlers.addon.edh.common import edhSessionBootstrap as bootstrap
from backend.backend.handlers.addon.edh.common.edhClient import EdhNotFoundError, edh_session_name
from backend.tests.handlers.addon.edh.edhAdminTestSupport import (
    ADMIN, INSTANCES_PATH, STALE_PATH, FakeEdhClient, edh_desktop, edh_proxy_records, fixture_payload,
    install_admin_fakes, instance_path, make_event, response_body, session_row,
)
from backend.tests.handlers.addon.edh.edhCredentialTestSupport import FakeApiKeyService, FakeSecretsManager
from backend.tests.handlers.addon.edh.edhRowWriteTestSupport import (
    is_about_a_week_ahead, update_attribute_sets, written_values,
)
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
import backend.backend.handlers.addon.edh.edhAdminService as service

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(minutes=5)).isoformat()
OLD = (NOW - timedelta(hours=5)).isoformat()
BOOTSTRAP_LAUNCH = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"


EDH_SESSIONS = [
    edh_desktop("s-1", "vowner1", "running"),
    edh_desktop("s-2", "vowner2", "running", os_family="windows", instance_type="m5.2xlarge",
             software_stack="vams-spike-stack-windows (id: 2)"),
    edh_desktop("s-3", "vowner3", "stopped"),
    edh_desktop("s-6", "vowner6", "stopped"),
    edh_desktop("s-4", edh_uid_for("erin"), "pending", session_name=edh_session_name(BOOTSTRAP_LAUNCH)),
]

SECRET_NAME = "vams/edh-session/b-1"
CLEARED_KEY = dict(bootstrap.CLEARED_KEY_ATTRIBUTES)
CLEARED_ALL = {**CLEARED_KEY, **bootstrap.CLEARED_SECRET_ATTRIBUTES}


def _rows():
    launch = session_row(bootstrapId=BOOTSTRAP_LAUNCH, userId="erin", edhUid=edh_uid_for("erin"),
                         name="fresh", state="CREATING", edhState=None, connectionAvailable=False, updatedAt=RECENT)
    launch.pop("sessionId")
    return [
        session_row(bootstrapId="b-1", sessionId="s-1", userId="alice", edhUid="vowner1", name="cad-1",
                    imageId="7", osFamily="linux", instanceType="g4dn.xlarge", updatedAt=RECENT),
        session_row(bootstrapId="b-9", sessionId="s-9", userId="bob", edhUid="vowner9", name="orphan",
                    imageId="8", osFamily="windows", instanceType="g5.xlarge", updatedAt=OLD),
        session_row(bootstrapId="b-6", sessionId="s-6", userId="dave", edhUid="vowner6", name="parked",
                    state="STOPPED", edhState="stopped", connectionAvailable=False, updatedAt=RECENT),
        session_row(bootstrapId="b-0", sessionId="s-0", userId="zoe", edhUid="vowner0", name="gone",
                    state="DELETED", connectionAvailable=False),
        launch,
    ]


def _client(sessions=None, **results):
    results.setdefault("list_all_desktops", list(EDH_SESSIONS if sessions is None else sessions))
    return FakeEdhClient(**results)


def _by_session(response):
    return {item["sessionId"]: item for item in response_body(response)["Items"]}


def _state_written(fakes):
    """The state value of the single update_item the handler issued."""
    kwargs = fakes.table.update_item.call_args.kwargs
    names = {v: k for k, v in kwargs["ExpressionAttributeNames"].items()}
    return kwargs["ExpressionAttributeValues"][names["state"].replace("#a", ":v")]


def _keyed_rows():
    """The rows, with the running desktop s-1 and the unlisted s-9 carrying a session credential."""
    rows = _rows()
    for row in rows:
        if row["bootstrapId"] in ("b-1", "b-9"):
            row.update(apiKeyId=f"key-{row['bootstrapId']}", secretName=f"vams/edh-session/{row['bootstrapId']}",
                       keyExpiresAt="2026-09-08T20:00:00Z")
    return rows


def _lagging_reads(fakes, lagging, current):
    """Point the admin fakes' table at two copies of the rows: the session index, and any table read that
    is not consistent, answer ``lagging``; a consistent read answers ``current``."""
    def query(**kwargs):
        wanted = kwargs["KeyConditionExpression"].get_expression()["values"][1]
        return {"Items": [dict(row) for row in lagging if row.get("sessionId") == wanted]}

    def get_item(**kwargs):
        rows = current if kwargs.get("ConsistentRead") else lagging
        found = [dict(row) for row in rows if row["bootstrapId"] == kwargs["Key"]["bootstrapId"]]
        return {"Item": found[0]} if found else {}

    fakes.table.query.side_effect = query
    fakes.table.get_item.side_effect = get_item


def _install_revoke(monkeypatch, error=None):
    """Replace the credential revoke; answers the cleared attributes the real one returns. ``rows_seen``
    holds a copy of each row as it was handed over, before the handler writes the answer back onto it."""
    rows_seen = []

    def answer(event, row, *, delete_secret, actor):
        rows_seen.append(dict(row))
        if error is not None:
            raise error
        return dict(CLEARED_ALL if delete_secret else CLEARED_KEY)

    revoke = MagicMock(side_effect=answer)
    revoke.rows_seen = rows_seen
    monkeypatch.setattr(bootstrap, "revoke_session", revoke)
    return revoke


@pytest.mark.unit
class TestListInstances:
    def test_joins_edh_records_with_vams_rows_and_derives_the_state_from_edh(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(make_event("GET", INSTANCES_PATH), {})

        assert response["statusCode"] == 200
        items = _by_session(response)
        assert set(items) == {"s-1", "s-2", "s-3", "s-6", "s-4"}
        joined = items["s-1"]
        assert (joined["userId"], joined["bootstrapId"], joined["name"]) == ("alice", "b-1", "cad-1")
        assert (joined["state"], joined["edhState"], joined["owner"]) == ("ACTIVE", "running", "vowner1")
        assert (joined["imageId"], joined["osFamily"], joined["instanceType"]) == ("7", "linux", "g4dn.xlarge")
        assert joined["createdAt"] == "2026-09-10T12:00:00Z" and joined["lastError"] is None
        parked = items["s-6"]
        assert parked["state"] == "STOPPED" and parked["userId"] == "dave"
        assert fakes.edh.calls_to("list_all_desktops") == [(edh_uid_for(ADMIN),)]

    def test_a_session_without_a_row_is_described_from_the_edh_record(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(make_event("GET", INSTANCES_PATH), {})

        unmatched = _by_session(response)["s-2"]
        assert unmatched["userId"] is None and unmatched["bootstrapId"] is None and unmatched["lastError"] is None
        assert (unmatched["owner"], unmatched["name"], unmatched["state"]) == ("vowner2", "names-2", "ACTIVE")
        assert (unmatched["imageId"], unmatched["osFamily"], unmatched["instanceType"]) == (
            "2", "windows", "m5.2xlarge")

    def test_a_session_less_launch_is_joined_by_its_session_name_and_owner(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(make_event("GET", INSTANCES_PATH), {})

        launch = _by_session(response)["s-4"]
        assert (launch["bootstrapId"], launch["userId"], launch["name"]) == (BOOTSTRAP_LAUNCH, "erin", "fresh")
        assert (launch["state"], launch["edhState"]) == ("CREATING", "pending")
        # A read: the row is not completed here (the user list route and the reconciler own that).
        fakes.table.update_item.assert_not_called()

    def test_tier_2_object_comes_from_the_row_or_else_from_the_record(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        fakes.module.lambda_handler(make_event("GET", INSTANCES_PATH), {})

        objects = [call.args[0] for call in fakes.enforcer.enforce.call_args_list]
        assert {"object__type": "edhWorkstation", "edhImageId": "7", "edhOsFamily": "linux",
                "edhInstanceType": "g4dn.xlarge"} in objects
        assert {"object__type": "edhWorkstation", "edhImageId": "2", "edhOsFamily": "windows",
                "edhInstanceType": "m5.2xlarge"} in objects
        assert fakes.enforcer.enforce.call_args_list, "nothing was enforced"
        assert all(call.args[1] == "GET" for call in fakes.enforcer.enforce.call_args_list)

    def test_a_denied_item_is_dropped_and_an_allowed_one_kept(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows(),
                                    enforce=lambda obj, act: obj["edhOsFamily"] == "windows")

        response = fakes.module.lambda_handler(make_event("GET", INSTANCES_PATH), {})

        assert set(_by_session(response)) == {"s-2"}

    def test_the_listing_is_audited(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client())

        fakes.module.lambda_handler(make_event("GET", INSTANCES_PATH), {})

        assert [(r["operation"], r["edhMethod"], r["edhRoute"], r["edhUid"]) for r in edh_proxy_records(fakes)] == [
            ("adminInstancesList", "GET", ec.EDH_ROUTE_DESKTOPS_LIST_ALL, edh_uid_for(ADMIN)),
        ]


@pytest.mark.unit
class TestStaleInstances:
    def test_stopped_desktops_and_rows_missing_in_edh_are_stale(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(make_event("GET", STALE_PATH), {})

        assert response["statusCode"] == 200
        body = response_body(response)
        assert body["idleMinutes"] == 120
        # s-3 is stopped with no row (listed at once); s-6 stopped but its row changed 5 minutes ago
        # (not yet); s-9 has a row EDH no longer lists; the DELETED row b-0 is not a stale candidate.
        assert {(i["sessionId"], i["staleReason"]) for i in body["Items"]} == {
            ("s-3", "stopped"), ("s-9", "missingInEdh")}
        assert body["Items"], "the stale view was empty"
        assert all(i["stale"] is True for i in body["Items"])

    def test_the_idle_minutes_query_parameter_moves_the_cutoff(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(make_event("GET", STALE_PATH, query={"idleMinutes": "1"}), {})

        assert {i["sessionId"] for i in response_body(response)["Items"]} == {"s-3", "s-6", "s-9"}

    def test_a_denied_stale_item_is_absent_and_the_allowed_one_survives(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows(),
                                    enforce=lambda obj, act: obj["edhOsFamily"] == "windows")

        response = fakes.module.lambda_handler(make_event("GET", STALE_PATH, query={"idleMinutes": "1"}), {})

        # Of the three stale desktops (s-3, s-6, s-9) only the Windows row survives; the Linux ones
        # are denied and absent, and every verdict was asked of an edhWorkstation object with GET.
        assert [(item["sessionId"], item["osFamily"]) for item in response_body(response)["Items"]] == [
            ("s-9", "windows")]
        assert {(call.args[0]["object__type"], call.args[1]) for call in fakes.enforcer.enforce.call_args_list} == {
            ("edhWorkstation", "GET")}
        assert {call.args[0]["edhOsFamily"] for call in fakes.enforcer.enforce.call_args_list} == {"linux", "windows"}

    def test_a_running_desktop_is_never_stale(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(make_event("GET", STALE_PATH, query={"idleMinutes": "1"}), {})

        assert {"s-1", "s-2", "s-4"}.isdisjoint({i["sessionId"] for i in response_body(response)["Items"]})

    @pytest.mark.parametrize("value", ["abc", "0", "-5"])
    def test_an_invalid_idle_minutes_value_is_400(self, monkeypatch, value):
        fakes = install_admin_fakes(monkeypatch, edh=_client())

        response = fakes.module.lambda_handler(make_event("GET", STALE_PATH, query={"idleMinutes": value}), {})

        assert response["statusCode"] == 400
        assert fakes.edh.calls == []

    def test_null_query_parameters_on_a_rest_event_use_the_default(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(make_event("GET", STALE_PATH, query=None, rest=True), {})

        assert response["statusCode"] == 200
        assert response_body(response)["idleMinutes"] == 120

    def test_a_row_with_an_unparseable_updated_at_is_stale_when_stopped(self, monkeypatch):
        rows = [session_row(bootstrapId="b-6", sessionId="s-6", userId="dave", edhUid="vowner6",
                            state="STOPPED", updatedAt="yesterday")]
        fakes = install_admin_fakes(monkeypatch, edh=_client([edh_desktop("s-6", "vowner6", "stopped")]), rows=rows)

        response = fakes.module.lambda_handler(make_event("GET", STALE_PATH), {})

        assert [i["sessionId"] for i in response_body(response)["Items"]] == ["s-6"]

    def test_the_stale_view_is_audited_as_its_own_operation(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client())

        fakes.module.lambda_handler(make_event("GET", STALE_PATH), {})

        assert [(r["operation"], r["edhRoute"]) for r in edh_proxy_records(fakes)] == [
            ("adminInstancesStale", ec.EDH_ROUTE_DESKTOPS_LIST_ALL)]


@pytest.mark.unit
class TestStopAndDeactivate:
    def test_stop_calls_edh_under_the_owner_uid_and_marks_the_row_stopped(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "stop"), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("stop_desktop") == [("vowner1", "s-1")]
        assert fakes.edh.calls_to("delete_desktop") == []
        assert fakes.table.update_item.call_args.kwargs["Key"] == {"bootstrapId": "b-1"}
        assert _state_written(fakes) == "STOPPED"
        # The row a user had connected to is not left reading connectable once it is stopped.
        assert update_attribute_sets(fakes) == [{"state", "connectionAvailable"}]
        assert written_values(fakes, "connectionAvailable") == [False]
        body = response_body(response)
        assert (body["sessionId"], body["state"], body["userId"]) == ("s-1", "STOPPED", "alice")

    def test_deactivate_deletes_in_edh_as_the_service_actor_and_marks_the_row_deleted(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "deactivate"), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("delete_desktop") == [(ec.EDH_SERVICE_ACTOR_UID, "s-1")]
        assert fakes.edh.calls_to("stop_desktop") == []
        assert _state_written(fakes) == "DELETED"
        assert written_values(fakes, "connectionAvailable") == [False]
        assert response_body(response)["state"] == "DELETED"

    @pytest.mark.parametrize("action", ["stop", "deactivate"])
    @pytest.mark.parametrize("keyed", [False, True], ids=["no credential", "credential"])
    def test_the_state_write_clears_a_start_stamp_the_row_carries(self, monkeypatch, action, keyed):
        # A start whose outcome EDH did not confirm stamps its row; a desktop EDH stops has taken that
        # start, and one it deletes cannot be started again, so the owner's next start is not held off.
        _install_revoke(monkeypatch)
        rows = _keyed_rows() if keyed else _rows()
        next(row for row in rows if row["bootstrapId"] == "b-1")["startRequestedAt"] = RECENT
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=rows)

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", action), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 200
        assert "startRequestedAt" in update_attribute_sets(fakes)[0]
        assert written_values(fakes, "startRequestedAt") == [None]
        assert "startRequestedAt" not in response_body(response)

    def test_a_session_without_a_vams_row_is_stopped_under_the_edh_owner_and_writes_nothing(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-2", "stop"), path_params={"sessionId": "s-2"}), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("stop_desktop") == [("vowner2", "s-2")]
        fakes.table.update_item.assert_not_called()
        assert response_body(response)["state"] == "STOPPED"

    def test_stop_of_a_row_edh_no_longer_lists_is_409(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-9", "stop"), path_params={"sessionId": "s-9"}), {})

        assert response["statusCode"] == 409
        assert fakes.edh.calls_to("stop_desktop") == []
        fakes.table.update_item.assert_not_called()

    def test_deactivate_of_a_row_edh_no_longer_lists_marks_it_deleted_without_an_edh_call(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-9", "deactivate"), path_params={"sessionId": "s-9"}), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("delete_desktop") == []
        assert fakes.table.update_item.call_args.kwargs["Key"] == {"bootstrapId": "b-9"}
        assert written_values(fakes, "connectionAvailable") == [False]
        assert response_body(response)["state"] == "DELETED"

    def test_a_session_less_launch_is_addressed_by_its_bootstrap_id(self, monkeypatch):
        # EDH lists the desktop (by session name) even though the row has no session id yet.
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path(BOOTSTRAP_LAUNCH, "deactivate"),
                       path_params={"sessionId": BOOTSTRAP_LAUNCH}), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("delete_desktop") == [(ec.EDH_SERVICE_ACTOR_UID, "s-4")]
        assert fakes.table.update_item.call_args.kwargs["Key"] == {"bootstrapId": BOOTSTRAP_LAUNCH}
        assert response_body(response)["sessionId"] == "s-4"

    def test_a_young_creating_launch_edh_does_not_list_cannot_be_deactivated_yet(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client([]), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path(BOOTSTRAP_LAUNCH, "deactivate"),
                       path_params={"sessionId": BOOTSTRAP_LAUNCH}), {})

        assert response["statusCode"] == 409
        assert response_body(response)["message"] == "Workstation is still being created"
        fakes.table.update_item.assert_not_called()

    def test_an_old_creating_launch_edh_does_not_list_is_deactivated_locally(self, monkeypatch):
        rows = _rows()
        rows[-1]["updatedAt"] = OLD
        fakes = install_admin_fakes(monkeypatch, edh=_client([]), rows=rows)

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path(BOOTSTRAP_LAUNCH, "deactivate"),
                       path_params={"sessionId": BOOTSTRAP_LAUNCH}), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("delete_desktop") == []
        assert _state_written(fakes) == "DELETED"

    def test_a_session_less_launch_cannot_be_stopped(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client([]), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path(BOOTSTRAP_LAUNCH, "stop"),
                       path_params={"sessionId": BOOTSTRAP_LAUNCH}), {})

        assert response["statusCode"] == 409
        assert fakes.edh.calls_to("stop_desktop") == []

    def test_an_unknown_session_is_404(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("nope", "stop"), path_params={"sessionId": "nope"}), {})

        assert response["statusCode"] == 404
        assert fakes.edh.calls_to("stop_desktop") == []

    def test_a_tier_2_denial_is_403_before_any_edh_action(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows(), enforce=lambda obj, act: False)

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "stop"), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 403
        assert fakes.edh.calls_to("stop_desktop") == []
        obj, act = fakes.enforcer.enforce.call_args.args
        assert obj == {"object__type": "edhWorkstation", "edhImageId": "7", "edhOsFamily": "linux",
                       "edhInstanceType": "g4dn.xlarge"}
        assert act == "POST"

    def test_null_path_parameters_on_a_rest_event_are_400(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "stop"), path_params=None, rest=True), {})

        assert response["statusCode"] == 400
        assert fakes.edh.calls == []

    def test_edh_not_found_on_stop_maps_to_the_typed_response(self, monkeypatch):
        fakes = install_admin_fakes(
            monkeypatch, edh=_client(stop_desktop=EdhNotFoundError("Unable to find")), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "stop"), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 404
        assert "Unable to find" not in response["body"]
        fakes.table.update_item.assert_not_called()

    def test_the_actions_are_audited_with_the_uid_each_call_was_sent_under(self, monkeypatch):
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "deactivate"), path_params={"sessionId": "s-1"}), {})

        assert [(r["operation"], r["edhMethod"], r["edhRoute"], r["edhUid"]) for r in edh_proxy_records(fakes)] == [
            ("adminInstanceDeactivate", "GET", ec.EDH_ROUTE_DESKTOPS_LIST_ALL, edh_uid_for(ADMIN)),
            ("adminInstanceDeactivate", "DELETE", ec.EDH_ROUTE_DESKTOPS_DELETE, ec.EDH_SERVICE_ACTOR_UID),
        ]


@pytest.mark.unit
class TestStopAndDeactivateRevokeTheCredential:
    def test_stop_revokes_the_key_after_the_edh_stop_and_keeps_the_container(self, monkeypatch):
        revoke = _install_revoke(monkeypatch)
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_keyed_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "stop"), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("stop_desktop") == [("vowner1", "s-1")]
        assert revoke.call_args.args[1]["bootstrapId"] == "b-1"
        assert revoke.call_args.kwargs == {"delete_secret": False, "actor": ADMIN}
        # The revoke is handed the row's stored key: the state write itself touches no credential
        # attribute, so a key the revoke could not take back stays on the row for the sweep to find.
        assert revoke.rows_seen[-1]["apiKeyId"] == "key-b-1"
        # The state lands first, then the cleared key attributes; the container's name stays.
        assert update_attribute_sets(fakes) == [{"state", "connectionAvailable"}, set(CLEARED_KEY)]
        assert written_values(fakes, "state") == ["STOPPED"]
        assert written_values(fakes, "apiKeyId") == [""]
        assert written_values(fakes, "secretName") == []

    def test_deactivate_revokes_the_key_and_removes_the_container(self, monkeypatch):
        revoke = _install_revoke(monkeypatch)
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_keyed_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "deactivate"), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("delete_desktop") == [(ec.EDH_SERVICE_ACTOR_UID, "s-1")]
        assert revoke.call_args.kwargs == {"delete_secret": True, "actor": ADMIN}
        assert written_values(fakes, "state") == ["DELETED"]
        assert written_values(fakes, "apiKeyId") == [""]
        assert written_values(fakes, "secretName") == [""]
        # The row holds no credential any more and is left to the table's TTL; the expiry travels with the
        # cleared attributes and never reaches the response.
        assert update_attribute_sets(fakes) == [{"state", "connectionAvailable"}, {*CLEARED_ALL, "expiresAt"}]
        (expiry,) = written_values(fakes, "expiresAt")
        assert is_about_a_week_ahead(expiry)
        assert "expiresAt" not in response_body(response)

    def test_deactivate_of_a_row_without_a_credential_writes_the_expiry_with_the_state(self, monkeypatch):
        revoke = _install_revoke(monkeypatch)
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "deactivate"), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 200
        revoke.assert_not_called()
        assert update_attribute_sets(fakes) == [{"state", "connectionAvailable", "expiresAt"}]
        (expiry,) = written_values(fakes, "expiresAt")
        assert is_about_a_week_ahead(expiry)

    def test_stop_gives_the_row_no_expiry(self, monkeypatch):
        _install_revoke(monkeypatch)
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_keyed_rows())

        fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "stop"), path_params={"sessionId": "s-1"}), {})

        assert written_values(fakes, "expiresAt") == []

    def test_deactivate_of_a_row_edh_no_longer_lists_still_takes_the_credential_back(self, monkeypatch):
        revoke = _install_revoke(monkeypatch)
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_keyed_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-9", "deactivate"), path_params={"sessionId": "s-9"}), {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("delete_desktop") == []
        assert revoke.call_args.args[1]["bootstrapId"] == "b-9"
        assert revoke.call_args.kwargs["delete_secret"] is True
        assert written_values(fakes, "secretName") == [""]

    @pytest.mark.parametrize("error", [bootstrap.SessionBootstrapError("down"), RuntimeError("boom")],
                             ids=["bootstrap", "unexpected"])
    @pytest.mark.parametrize("action,state", [("stop", "STOPPED"), ("deactivate", "DELETED")])
    def test_a_revoke_failure_is_logged_and_leaves_the_edh_action_and_its_result_standing(
            self, monkeypatch, error, action, state):
        _install_revoke(monkeypatch, error=error)
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_keyed_rows())
        recorder = MagicMock()
        monkeypatch.setattr(fakes.module, "logger", recorder)

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", action), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 200
        assert response_body(response)["state"] == state
        assert written_values(fakes, "state") == [state]
        # The credential attributes stay on the row for the reconciler to find, and so does the expiry.
        assert written_values(fakes, "apiKeyId") == []
        assert written_values(fakes, "expiresAt") == []
        assert recorder.exception.call_args_list, "the deferred revoke was not logged"
        assert any("b-1" in str(call) for call in recorder.exception.call_args_list)

    @pytest.mark.parametrize("action", ["stop", "deactivate"])
    def test_a_row_without_a_credential_is_not_revoked(self, monkeypatch, action):
        revoke = _install_revoke(monkeypatch)
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", action), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 200
        revoke.assert_not_called()

    @pytest.mark.parametrize("identifier, bootstrap_id", [("s-1", "b-1"), (BOOTSTRAP_LAUNCH, BOOTSTRAP_LAUNCH)],
                             ids=["by session id", "by bootstrap id"])
    def test_the_revoke_is_handed_the_key_the_row_holds_now_not_one_a_lagging_read_returns(
            self, monkeypatch, identifier, bootstrap_id):
        # A start of the desktop has just replaced its key, or a launch has just recorded its first one: the
        # session index, and a table read that is not consistent, still return the row without it.
        revoke = _install_revoke(monkeypatch)
        lagging, current = _keyed_rows(), _keyed_rows()
        for row in current:
            if row["bootstrapId"] == bootstrap_id:
                row.update(apiKeyId="key-fresh", secretName=f"vams/edh-session/{bootstrap_id}")
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=lagging)
        _lagging_reads(fakes, lagging, current)

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path(identifier, "deactivate"), path_params={"sessionId": identifier}), {})

        assert response["statusCode"] == 200
        assert revoke.rows_seen, "the credential the row holds was not taken back"
        assert revoke.rows_seen[-1]["apiKeyId"] == "key-fresh"

    def test_a_session_without_a_row_has_no_credential_to_revoke(self, monkeypatch):
        revoke = _install_revoke(monkeypatch)
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_keyed_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-2", "stop"), path_params={"sessionId": "s-2"}), {})

        assert response["statusCode"] == 200
        revoke.assert_not_called()
        fakes.table.update_item.assert_not_called()

    def test_an_edh_refusal_leaves_the_credential_untouched(self, monkeypatch):
        revoke = _install_revoke(monkeypatch)
        fakes = install_admin_fakes(
            monkeypatch, edh=_client(stop_desktop=EdhNotFoundError("Unable to find")), rows=_keyed_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", "stop"), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 404
        revoke.assert_not_called()
        fakes.table.update_item.assert_not_called()


@pytest.mark.unit
class TestStopAndDeactivateSignOutEveryKeyOfTheDesktop:
    """A running desktop keeps every key a renew gave it until the key's own expiry; an administrator's stop
    or deactivate takes back, through the real revoke, the key the row names and every other key whose name
    encodes the row's bootstrap, and no key of another desktop."""

    @staticmethod
    def _wired(monkeypatch):
        keys, secrets = FakeApiKeyService(), FakeSecretsManager()
        for key_id, name in (("key-b-1", "temporary-session-edh-b-1-r20260928170000"), ("key-prev", "temporary-session-edh-b-1"),
                             ("key-b-9", "temporary-session-edh-b-9")):
            keys.seed(key_id, name, user_id="alice", expires_at="2099-01-01T00:00:00Z",
                      created_at="2026-09-28T12:00:00Z")
        secrets.seed(SECRET_NAME, '{"apiKey": "vams_key-b-1"}')
        for name, value in (("lambda_client", keys), ("secretsmanager_client", secrets),
                            ("log_auth_changes", MagicMock())):
            monkeypatch.setattr(bootstrap, name, value)
        return keys, secrets

    @pytest.mark.parametrize("action", ["stop", "deactivate"])
    def test_every_key_of_the_bootstrap_is_deleted_and_the_row_cleared(self, monkeypatch, action):
        keys, secrets = self._wired(monkeypatch)
        fakes = install_admin_fakes(monkeypatch, edh=_client(), rows=_keyed_rows())

        response = fakes.module.lambda_handler(
            make_event("POST", instance_path("s-1", action), path_params={"sessionId": "s-1"}), {})

        assert response["statusCode"] == 200
        assert {"key-b-1", "key-prev"} <= set(keys.deleted)
        # Another desktop's key is not this action's to take back.
        assert "key-b-9" in keys.records
        assert written_values(fakes, "apiKeyId") == [""]
        if action == "stop":
            assert secrets.key_value_held(SECRET_NAME) is None
        else:
            assert SECRET_NAME not in secrets.values


@pytest.mark.unit
class TestFixtureShape:
    def test_the_list_all_capture_maps_to_a_complete_instance_item(self):
        records = fixture_payload("virtual_desktops_list_all.json")
        assert records, "the list_all capture holds no sessions"
        item = service._instance_item(records[0], None)
        for key in ("sessionId", "owner", "edhState", "name", "createdAt", "imageId", "osFamily", "instanceType"):
            assert item[key], key
        assert item["state"] in ("CREATING", "ACTIVE", "STOPPED", "ORPHANED")
        assert service._parse_timestamp(item["createdAt"]) is not None
