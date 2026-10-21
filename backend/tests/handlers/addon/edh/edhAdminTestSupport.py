# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Claims, events, rows, fixture access and fakes shared by the edhAdminService tests.

The handler is driven through ``lambda_handler`` with its module attributes replaced:
``request_to_claims`` (runs the real ``normalize_event`` first so REST-shaped events are covered),
``CasbinEnforcer``, ``EdhClient``, the session table handle and the ``log_actions`` audit hook.
``FakeEdhClient`` answers each client wrapper from a script and records every call, so a test can
assert that an EDH mutation was -- or was not -- attempted; its ``projects`` table answers the
assignment that follows a registration.
"""

import json
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

from common.auth.apiEvent import normalize_event

import backend.backend.handlers.addon.edh.edhAdminService as service
import backend.backend.handlers.addon.edh.common.edhClient as edh_client
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.tests.handlers.addon.edh.edhTestSupport import FakeProjects

ADMIN = "carol"
ADMIN_ROLE = "edhAdmin"

ADMIN_CLAIMS = {"tokens": [ADMIN], "roles": [ADMIN_ROLE], "mfaEnabled": True, "authMethod": "jwt"}
NO_IDENTITY_CLAIMS = {"tokens": [], "roles": [], "mfaEnabled": False, "authMethod": "jwt"}
SYSTEM_CLAIMS = {"tokens": ["SYSTEM_USER"], "roles": [], "mfaEnabled": True, "authMethod": "jwt"}

HEALTH_PATH = "/addon/edh/admin/health"
CAPACITY_PATH = "/addon/edh/admin/capacity"
IMAGES_PATH = "/addon/edh/admin/images"
INSTANCES_PATH = "/addon/edh/admin/instances"
STALE_PATH = "/addon/edh/admin/instances/stale"

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")


def image_path(kind, image_id=None):
    path = f"{IMAGES_PATH}/{kind}"
    return f"{path}/{image_id}" if image_id is not None else path


def instance_path(session_id, action):
    return f"{INSTANCES_PATH}/{session_id}/{action}"


def _authorizer_claims():
    return {
        "vams:tokens": json.dumps([ADMIN]),
        "vams:roles": json.dumps([ADMIN_ROLE]),
        "vams:authMethod": "jwt",
    }


def make_event(method, path, *, body=None, path_params=None, query=None, rest=False):
    """An API Gateway event. ``rest=True`` builds the REST (v1) proxy shape with explicit nulls.

    ``body`` may be a dict (JSON-encoded) or a string (sent as-is, for malformed-body cases).
    """
    if rest:
        event = {
            "httpMethod": method,
            "path": path,
            "pathParameters": path_params,
            "queryStringParameters": query,
            "requestContext": {
                "identity": {"sourceIp": "10.0.0.1"},
                "authorizer": _authorizer_claims(),
            },
            "headers": {"Authorization": "Bearer test-token"},
        }
    else:
        event = {
            "requestContext": {
                "http": {"method": method, "path": path},
                "authorizer": {"jwt": {"claims": _authorizer_claims()}},
            },
            "headers": {"authorization": "Bearer test-token"},
        }
        if path_params is not None:
            event["pathParameters"] = path_params
        if query is not None:
            event["queryStringParameters"] = query
    if body is not None:
        event["body"] = body if isinstance(body, str) else json.dumps(body)
    return event


def response_body(response):
    return json.loads(response["body"])


def fixture_payload(file_name):
    """The EDH records of a sanitized capture, unwrapped the way the client unwraps a response."""
    with open(os.path.join(FIXTURES_DIR, file_name), "r", encoding="utf-8") as handle:
        fixture = json.load(handle)
    return edh_client._as_list(edh_client._payload(fixture["response"]["json"]))


def session_row(**overrides):
    """A session row of the shape the workstation service writes."""
    row = {
        "bootstrapId": "3f2a9c1e-4b7d-4e0a-9d2b-6c1f8a7e5b30",
        "sessionId": "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b",
        "userId": "alice",
        "edhUid": edh_uid_for("alice"),
        "state": "ACTIVE",
        "edhState": "running",
        "name": "Design desk",
        "imageId": "42",
        "osFamily": "linux",
        "instanceType": "g5.xlarge",
        "rootSizeGb": 100,
        "createdAt": "2026-09-08T10:00:00+00:00",
        "updatedAt": "2026-09-08T10:05:00+00:00",
        "connectionAvailable": True,
        "lastError": None,
    }
    row.update(overrides)
    return row


def edh_desktop(session_id, owner, state, **fields):
    """A desktop as EDH's cluster listing reports it, with the fields the admin views project."""
    record = {"session_uuid": session_id, "session_owner": owner, "session_state": state,
              "session_name": f"name{session_id}", "created_on": "2026-09-10T12:00:00Z",
              "os_family": "linux", "instance_type": "m5.xlarge", "software_stack": "vams-spike-stack (id: 1)"}
    record.update(fields)
    return record


class Sequence:
    """Successive answers for one wrapper; the last one repeats."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def next(self):
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class FakeEdhClient:
    """A scripted stand-in for ``EdhClient``.

    ``results`` maps a wrapper name to the value it returns, the exception instance it raises, a
    ``Sequence`` of those answered in turn, or a callable of the recorded arguments. Every call is
    recorded as ``(wrapper, args)`` with the acting uid last for the actor-header wrappers.
    """

    def __init__(self, projects=None, **results):
        self.projects = projects if projects is not None else FakeProjects()
        self.results = {
            "list_all_desktops": [],
            "stop_desktop": {},
            "delete_desktop": {},
            "list_software_stacks": [],
            "register_software_stack": {"message": "registered successfully in SOCA"},
            "delete_software_stack": {},
            "list_profiles": [],
            "assign_software_stack_to_project":
                lambda kind, stack_id, project_name, edh_uid: self.projects.assign(kind, stack_id, project_name),
        }
        self.results.update(results)
        self.calls = []

    def _answer(self, wrapper, *args):
        self.calls.append((wrapper, args))
        result = self.results[wrapper]
        if isinstance(result, Sequence):
            result = result.next()
        if callable(result):
            result = result(*args)
        if isinstance(result, Exception):
            raise result
        return result

    def list_all_desktops(self, edh_uid=None):
        return self._answer("list_all_desktops", edh_uid)

    def stop_desktop(self, edh_uid, session_id):
        return self._answer("stop_desktop", edh_uid, session_id)

    def delete_desktop(self, edh_uid, session_id):
        return self._answer("delete_desktop", edh_uid, session_id)

    def list_software_stacks(self, kind):
        return self._answer("list_software_stacks", kind)

    def register_software_stack(self, kind, fields, *, edh_uid=None, thumbnail=None):
        return self._answer("register_software_stack", kind, fields, edh_uid)

    def delete_software_stack(self, kind, image_id, *, edh_uid=None):
        return self._answer("delete_software_stack", kind, image_id, edh_uid)

    def list_profiles(self, kind):
        return self._answer("list_profiles", kind)

    def assign_software_stack_to_project(self, kind, stack_id, project_name, *, edh_uid=None):
        return self._answer("assign_software_stack_to_project", kind, stack_id, project_name, edh_uid)

    def calls_to(self, wrapper):
        return [args for name, args in self.calls if name == wrapper]


def _fake_table(rows):
    """A session table whose scan returns ``rows`` in one page, whose query filters them by sessionId
    (the only index the handler queries) and whose get_item reads them by bootstrapId."""
    table = MagicMock()
    table.scan.return_value = {"Items": list(rows)}

    def query(**kwargs):
        wanted = kwargs["KeyConditionExpression"].get_expression()["values"][1]
        return {"Items": [row for row in rows if row.get("sessionId") == wanted]}

    def get_item(**kwargs):
        wanted = kwargs["Key"]["bootstrapId"]
        found = [row for row in rows if row.get("bootstrapId") == wanted]
        return {"Item": found[0]} if found else {}

    table.query.side_effect = query
    table.get_item.side_effect = get_item
    return table


def install_admin_fakes(monkeypatch, *, claims=None, edh=None, enforce=None, rows=None):
    """Replace the handler module's collaborators; returns the doubles installed."""
    claims = dict(ADMIN_CLAIMS if claims is None else claims)
    edh = edh if edh is not None else FakeEdhClient()
    rows = list(rows or [])

    def claims_for(event):
        normalize_event(event)
        return dict(claims)

    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.side_effect = enforce if enforce is not None else (lambda obj, act: True)
    enforcer_factory = MagicMock(return_value=enforcer)
    client_factory = MagicMock(return_value=edh)
    table = _fake_table(rows)
    log_actions = MagicMock()

    monkeypatch.setattr(service, "request_to_claims", claims_for)
    monkeypatch.setattr(service, "CasbinEnforcer", enforcer_factory)
    monkeypatch.setattr(service, "EdhClient", client_factory)
    monkeypatch.setattr(service, "edh_session_table", table)
    monkeypatch.setattr(service, "log_actions", log_actions)
    return SimpleNamespace(
        module=service,
        table=table,
        rows=rows,
        enforcer=enforcer,
        enforcer_factory=enforcer_factory,
        client_factory=client_factory,
        edh=edh,
        log_actions=log_actions,
    )


def edh_proxy_records(fakes):
    """The custom_data of every edhProxy record written through log_actions."""
    return [call.args[2] for call in fakes.log_actions.call_args_list if call.args[1] == "edhProxy"]
