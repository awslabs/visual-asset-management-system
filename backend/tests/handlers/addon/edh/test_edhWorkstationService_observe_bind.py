# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Binding the session secret to the instance EDH places the desktop on, as a read observes it.

The secret is readable from the bound instance alone, and EDH places the desktop after the launch has
been handed over, so the row carries no instance while it is CREATING. The list route and the connect
route both persist what EDH's desktop record implies for the row, and both bind the secret the first
time that record names an instance. A row already bound to that instance, a row that holds no secret
and a desktop EDH has not placed are left alone, and a refused binding is left to the reconciler
without changing the route's own result. A row deleted since the read is terminal: nothing EDH still
lists for it is written over the deletion, and nothing is bound.
"""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.handlers.addon.edh.common.edhSessionState import EDH_FIELD_INSTANCE_ID
from backend.tests.handlers.addon.edh.edhRowWriteTestSupport import ConditionalRowTable
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    BOOTSTRAP_OWN,
    SESSION_OWN,
    WORKSTATIONS_PATH,
    FakeEdhClient,
    install_service_fakes,
    make_event,
    own_row,
    response_body,
    workstation_path,
)

INSTANCE_ID = "i-0abc123def4567890"
SECRET_NAME = "vams/edh-session/" + BOOTSTRAP_OWN
# A connection string carrying no credential: the connect case asserts on the response key, never on
# the value EDH reports.
CONNECTION_STRING = "https://dcv.example.internal/#" + SESSION_OWN


def _desktop(*, instance_id=INSTANCE_ID, state="pending"):
    """EDH's record for the row's desktop; the instance column is present only once EDH placed it."""
    record = {
        service.EDH_FIELD_SESSION_UUID: SESSION_OWN,
        service.EDH_FIELD_SESSION_STATE: state,
        service.EDH_FIELD_CONNECTION_STRING: CONNECTION_STRING,
    }
    if instance_id is not None:
        record[EDH_FIELD_INSTANCE_ID] = instance_id
    return record


def _binder(error=None):
    """The bootstrap module's binder: the row attribute a binding records, or a failure."""
    if error is not None:
        return MagicMock(side_effect=error)
    return MagicMock(side_effect=lambda row, instance_id: {"secretBoundInstanceId": instance_id})


def _fakes(monkeypatch, row, desktop):
    fakes = install_service_fakes(
        monkeypatch, edh=FakeEdhClient(get_desktop_states=[desktop], get_desktop_connection=desktop))
    fakes.table.query.return_value = {"Items": [row]}
    return fakes


def _list(fakes):
    return fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {})


def _connect(fakes):
    event = make_event("GET", workstation_path(SESSION_OWN, "connect"),
                       path_params={"sessionId": SESSION_OWN})
    return fakes.module.lambda_handler(event, {})


def _written(fakes):
    """Every attribute the route's row updates wrote, by name."""
    written = {}
    for call in fakes.table.update_item.call_args_list:
        names = call[1]["ExpressionAttributeNames"]
        values = call[1]["ExpressionAttributeValues"]
        for placeholder, name in names.items():
            if placeholder.startswith("#a"):
                written[name] = values[placeholder.replace("#a", ":v")]
    return written


@pytest.mark.unit
class TestSecretBindingOnObservation:
    def test_a_listed_desktop_binds_the_secret_to_the_instance_it_is_placed_on(self, monkeypatch):
        row = own_row(state="CREATING", edhState=None, connectionAvailable=False,
                      secretName=SECRET_NAME, secretBoundInstanceId="")
        fakes = _fakes(monkeypatch, row, _desktop())
        bind = _binder()

        with patch.object(service.bootstrap, "bind_session_secret", bind):
            response = _list(fakes)

        assert bind.call_count == 1
        assert bind.call_args.args == (row, INSTANCE_ID)
        assert _written(fakes)["secretBoundInstanceId"] == INSTANCE_ID
        assert response["statusCode"] == 200
        assert [item["bootstrapId"] for item in response_body(response)["Items"]] == [BOOTSTRAP_OWN]

    def test_a_row_already_bound_to_that_instance_is_not_bound_again(self, monkeypatch):
        row = own_row(state="CREATING", edhState="pending", connectionAvailable=False,
                      secretName=SECRET_NAME, instanceId=INSTANCE_ID,
                      secretBoundInstanceId=INSTANCE_ID)
        fakes = _fakes(monkeypatch, row, _desktop())
        bind = _binder()

        with patch.object(service.bootstrap, "bind_session_secret", bind):
            response = _list(fakes)

        assert response["statusCode"] == 200
        bind.assert_not_called()

    def test_a_row_that_holds_no_secret_is_not_bound(self, monkeypatch):
        row = own_row(state="CREATING", edhState="pending", connectionAvailable=False,
                      secretName="", secretBoundInstanceId="")
        fakes = _fakes(monkeypatch, row, _desktop())
        bind = _binder()

        with patch.object(service.bootstrap, "bind_session_secret", bind):
            response = _list(fakes)

        assert response["statusCode"] == 200
        assert _written(fakes)["instanceId"] == INSTANCE_ID
        bind.assert_not_called()

    def test_a_desktop_edh_has_not_placed_binds_nothing(self, monkeypatch):
        row = own_row(state="CREATING", edhState="pending", connectionAvailable=False,
                      secretName=SECRET_NAME, secretBoundInstanceId="")
        fakes = _fakes(monkeypatch, row, _desktop(instance_id=None))
        bind = _binder()

        with patch.object(service.bootstrap, "bind_session_secret", bind):
            response = _list(fakes)

        assert response["statusCode"] == 200
        assert "instanceId" not in _written(fakes)
        bind.assert_not_called()

    def test_a_refused_binding_leaves_the_row_unbound_and_the_listing_whole(self, monkeypatch):
        row = own_row(state="CREATING", edhState=None, connectionAvailable=False,
                      secretName=SECRET_NAME, secretBoundInstanceId="")
        fakes = _fakes(monkeypatch, row, _desktop())
        refused = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "denied"}},
                              "PutResourcePolicy")

        with patch.object(service.bootstrap, "bind_session_secret", _binder(error=refused)):
            response = _list(fakes)

        assert response["statusCode"] == 200
        assert row["secretBoundInstanceId"] == ""
        assert "secretBoundInstanceId" not in _written(fakes)

    def test_a_connect_binds_the_secret_to_the_running_desktop_instance(self, monkeypatch):
        row = own_row(secretName=SECRET_NAME, secretBoundInstanceId="")
        fakes = _fakes(monkeypatch, row, _desktop(state="running"))
        bind = _binder()

        with patch.object(service.bootstrap, "bind_session_secret", bind):
            response = _connect(fakes)

        assert bind.call_count == 1
        assert bind.call_args.args == (row, INSTANCE_ID)
        assert response["statusCode"] == 200
        assert "connectionUrl" in response_body(response)


@pytest.mark.unit
class TestObservationOfARowDeletedSinceItsRead:
    """A delete writes DELETED between a read's query and its merge of what EDH still lists."""

    @staticmethod
    def _launch():
        """A launch EDH placed on an instance its secret is not bound to yet."""
        return own_row(state="CREATING", edhState=None, connectionAvailable=False,
                       secretName=SECRET_NAME, instanceId=INSTANCE_ID, secretBoundInstanceId="")

    @pytest.mark.parametrize("stored_for", [lambda row: ConditionalRowTable(**{**row, "state": "DELETED"}),
                                            lambda row: ConditionalRowTable.missing()],
                             ids=["deleted since the read", "gone since the read"])
    def test_a_listing_writes_and_binds_nothing_onto_the_deleted_row(self, monkeypatch, stored_for):
        row = self._launch()
        fakes = _fakes(monkeypatch, row, _desktop(state="running"))
        stored = stored_for(row)
        before = None if stored.item is None else dict(stored.item)
        fakes.table.update_item.side_effect = stored
        bind = _binder()

        with patch.object(service.bootstrap, "bind_session_secret", bind):
            response = _list(fakes)

        assert response["statusCode"] == 200
        assert stored.item == before
        bind.assert_not_called()

    def test_a_listing_writes_and_binds_what_edh_lists_onto_the_live_row(self, monkeypatch):
        # Control for the case above: the same observation of the row as it was read lands and binds.
        row = self._launch()
        fakes = _fakes(monkeypatch, row, _desktop(state="running"))
        stored = ConditionalRowTable(**row)
        fakes.table.update_item.side_effect = stored
        bind = _binder()

        with patch.object(service.bootstrap, "bind_session_secret", bind):
            response = _list(fakes)

        assert response["statusCode"] == 200
        assert (stored.item["state"], stored.item["instanceId"], stored.item["secretBoundInstanceId"]) == (
            "ACTIVE", INSTANCE_ID, INSTANCE_ID)
        assert bind.called
