# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Rows, payloads and fakes for the edhLaunchWorker tests: the worker is driven through
``lambda_handler`` with ``EdhClient``, the session table, ``log_actions`` and ``_sleep`` replaced.
EDH desktop records are built from the client's and the session-state module's field constants."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import backend.backend.handlers.addon.edh.edhLaunchWorker as worker
from backend.backend.handlers.addon.edh.common.edhClient import EDH_SESSION_ID_FIELD
from backend.backend.handlers.addon.edh.common.edhSessionState import (
    EDH_CONNECTION_STRING_FIELD,
    EDH_SESSION_STATE_FIELD,
)
from backend.tests.handlers.addon.edh.edhServiceTestSupport import SESSION_OWN, FakeEdhClient, own_row


def creating_row(**overrides):
    """The row the API handler writes: CREATING, no sessionId, no EDH state."""
    row = own_row(state="CREATING", edhState=None, connectionAvailable=False)
    row.pop("sessionId")
    row.update(overrides)
    return row


def worker_event(row):
    return {key: row[key] for key in worker.WORKER_PAYLOAD_KEYS}


def desktop(session_id=SESSION_OWN, state="running",
            connection_string="https://dcv.example.internal/?authToken=abc", **fields):
    record = {
        EDH_SESSION_ID_FIELD: session_id,
        EDH_SESSION_STATE_FIELD: state,
        EDH_CONNECTION_STRING_FIELD: connection_string,
    }
    record.update(fields)
    return record


def install_worker_fakes(monkeypatch, *, row=None, edh=None):
    """Replace the worker module's collaborators; returns the doubles installed."""
    edh = edh if edh is not None else FakeEdhClient()
    table = MagicMock()
    table.get_item.return_value = {"Item": row} if row is not None else {}
    client_factory = MagicMock(return_value=edh)
    log_actions = MagicMock()
    sleep = MagicMock()
    monkeypatch.setattr(worker, "EdhClient", client_factory)
    monkeypatch.setattr(worker, "edh_session_table", table)
    monkeypatch.setattr(worker, "log_actions", log_actions)
    monkeypatch.setattr(worker, "_sleep", sleep)
    return SimpleNamespace(module=worker, table=table, edh=edh, client_factory=client_factory,
                           log_actions=log_actions, sleep=sleep)


def edh_proxy_records(fakes):
    """The custom_data of every edhProxy record written through log_actions."""
    return [call.args[2] for call in fakes.log_actions.call_args_list if call.args[1] == "edhProxy"]


def update_values(fakes):
    """The values the LAST update_item call wrote."""
    return [value for placeholder, value in fakes.table.update_item.call_args[1]["ExpressionAttributeValues"].items()
            if placeholder.startswith(":v")]


def updates(fakes):
    """Every update_item call as the attribute dict it wrote, updatedAt aside, in call order."""
    written = []
    for call in fakes.table.update_item.call_args_list:
        names = call.kwargs["ExpressionAttributeNames"]
        values = call.kwargs["ExpressionAttributeValues"]
        attributes = {name: values[":v" + placeholder[2:]] for placeholder, name in names.items()
                      if placeholder.startswith("#a")}
        written.append({key: value for key, value in attributes.items() if key != "updatedAt"})
    return written
