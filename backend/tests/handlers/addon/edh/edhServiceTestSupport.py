# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Claims, events, rows and fakes shared by the edhWorkstationService tests.

The handler is driven through ``lambda_handler`` with its module attributes replaced:
``request_to_claims`` (runs the real ``normalize_event`` first so REST-shaped events are covered),
``CasbinEnforcer``, ``EdhClient``, the session table handle, the Lambda client that hands a launch to
the worker, the AMI recipe store (which lists no recipe builds unless a test installs its own) and the
two audit hooks. ``FakeEdhClient`` answers each client wrapper from a script and
records every call, so a test can assert that an EDH mutation was -- or was not -- attempted.
"""

import json
from types import SimpleNamespace
from unittest.mock import DEFAULT, MagicMock

from common.auth.apiEvent import normalize_event

import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.backend.handlers.addon.edh.common.edhSessionState import EDH_FIELD_INSTANCE_ID

USER = "alice"
OTHER_USER = "bob"
ROLE = "edhUser"

USER_CLAIMS = {"tokens": [USER], "roles": [ROLE], "mfaEnabled": False, "authMethod": "jwt"}
API_KEY_CLAIMS = {"tokens": [USER], "roles": [ROLE], "mfaEnabled": False, "authMethod": "apiKey"}
NO_IDENTITY_CLAIMS = {"tokens": [], "roles": [], "mfaEnabled": False, "authMethod": "jwt"}
SYSTEM_CLAIMS = {"tokens": ["SYSTEM_USER"], "roles": [], "mfaEnabled": True, "authMethod": "jwt"}

BOOTSTRAP_OWN = "3f2a9c1e-4b7d-4e0a-9d2b-6c1f8a7e5b30"
BOOTSTRAP_OTHER = "7c1d2e3f-4a5b-4c6d-8e9f-0a1b2c3d4e5f"
SESSION_OWN = "9e8d7c6b-5a4f-4e3d-8c2b-1a0f9e8d7c6b"
SESSION_OTHER = "1a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d"

WORKSTATIONS_PATH = "/addon/edh/workstations"
IMAGES_PATH = "/addon/edh/catalog/images"
INSTANCE_TYPES_PATH = "/addon/edh/catalog/instance-types"

# The dcv software stack own_row() was launched from, as EDH lists it: the fake's catalog answers it by
# default, so a launch of that image resolves its OS family and its architecture. Its own profile groups
# the instance types the launch tests use under the image's architecture, and one Graviton type under arm64.
CATALOG_STACK = {
    service.EDH_FIELD_STACK_ID: 42,
    service.EDH_FIELD_STACK_NAME: "Ubuntu 22 GPU",
    service.EDH_FIELD_STACK_BASE_OS: "ubuntu2204",
    service.EDH_FIELD_STACK_OS_FAMILY: "linux",
    service.EDH_FIELD_STACK_ROOT_SIZE: 100,
    service.EDH_FIELD_STACK_DESCRIPTION: "GPU image",
    service.EDH_FIELD_STACK_ARCHITECTURE: "x86_64",
    service.EDH_FIELD_STACK_PROFILE: {
        service.EDH_FIELD_PROFILE_INSTANCE_TYPES: json.dumps({
            "x86_64": ["g5.xlarge", "g5.2xlarge", "p4d.24xlarge"],
            "arm64": ["m7g.large"],
            "i386": [],
        }),
    },
}


def workstation_path(session_id, action=None):
    path = f"{WORKSTATIONS_PATH}/{session_id}"
    return f"{path}/{action}" if action else path


def _authorizer_claims():
    return {
        "vams:tokens": json.dumps([USER]),
        "vams:roles": json.dumps([ROLE]),
        "vams:authMethod": "jwt",
    }


def make_event(method, path, *, body=None, path_params=None, rest=False):
    """An API Gateway event. ``rest=True`` builds the REST (v1) proxy shape with explicit nulls.

    ``body`` may be a dict (JSON-encoded) or a string (sent as-is, for malformed-body cases).
    """
    if rest:
        event = {
            "httpMethod": method,
            "path": path,
            "pathParameters": path_params,
            "queryStringParameters": None,
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
    if body is not None:
        event["body"] = body if isinstance(body, str) else json.dumps(body)
    return event


def own_row(**overrides):
    """A session row owned by USER."""
    row = {
        "bootstrapId": BOOTSTRAP_OWN,
        "sessionId": SESSION_OWN,
        "userId": USER,
        "edhUid": edh_uid_for(USER),
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


def other_row(**overrides):
    """A session row owned by OTHER_USER."""
    row = own_row(
        bootstrapId=BOOTSTRAP_OTHER,
        sessionId=SESSION_OTHER,
        userId=OTHER_USER,
        edhUid=edh_uid_for(OTHER_USER),
        name="Not mine",
    )
    row.update(overrides)
    return row


def state_record(session_id, state="running", instance_id=None):
    """A desktop record as EDH's session-state read gives it: the session id and state, plus the
    instance when given. It carries no connection string."""
    record = {service.EDH_FIELD_SESSION_UUID: session_id, service.EDH_FIELD_SESSION_STATE: state}
    if instance_id is not None:
        record[EDH_FIELD_INSTANCE_ID] = instance_id
    return record


def response_body(response):
    return json.loads(response["body"])


class Sequence:
    """Successive answers for one wrapper; the last one repeats."""

    def __init__(self, *answers):
        self.answers = list(answers)

    def next(self):
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


class FakeEdhClient:
    """A scripted stand-in for ``EdhClient``.

    ``results`` maps a wrapper name to the value it returns, the exception instance it raises, or a
    ``Sequence`` of those answered in turn. Every call is recorded as ``(wrapper, args)``.
    """

    def __init__(self, **results):
        self.results = {
            "get_desktop_states": [],
            "get_desktop_connection": None,
            "get_desktop_failure": None,
            "find_desktop_by_name": None,
            "create_desktop": {},
            "stop_desktop": {},
            "start_desktop": {},
            "delete_desktop": {},
            "update_desktop_schedule": {},
            "list_software_stacks": [dict(CATALOG_STACK)],
            "list_profiles": [],
            "ensure_user": None,
        }
        self.results.update(results)
        self.calls = []

    def _answer(self, wrapper, *args):
        self.calls.append((wrapper, args))
        result = self.results[wrapper]
        if isinstance(result, Sequence):
            result = result.next()
        if isinstance(result, Exception):
            raise result
        return result

    def get_desktop_states(self, edh_uid, session_ids, unanswered=None):
        records = self._answer("get_desktop_states", edh_uid, list(session_ids))
        if unanswered is not None:
            # As EDH does: an id is answered by a record of its own or by a scripted successor
            # (``state_successors``); every other id asked is unanswered.
            answered = {str(record.get("session_uuid")) for record in records or [] if isinstance(record, dict)}
            answered |= {str(session_id) for session_id in self.results.get("state_successors", [])}
            unanswered.update(str(session_id) for session_id in session_ids if str(session_id) not in answered)
        return records

    def get_desktop_connection(self, edh_uid, session_id):
        return self._answer("get_desktop_connection", edh_uid, session_id)

    def get_desktop_failure(self, edh_uid, session_id):
        return self._answer("get_desktop_failure", edh_uid, session_id)

    def find_desktop_by_name(self, edh_uid, session_name):
        return self._answer("find_desktop_by_name", edh_uid, session_name)

    def create_desktop(self, edh_uid, form):
        return self._answer("create_desktop", edh_uid, form)

    def stop_desktop(self, edh_uid, session_id):
        return self._answer("stop_desktop", edh_uid, session_id)

    def start_desktop(self, edh_uid, session_id):
        return self._answer("start_desktop", edh_uid, session_id)

    def delete_desktop(self, edh_uid, session_id):
        return self._answer("delete_desktop", edh_uid, session_id)

    def update_desktop_schedule(self, edh_uid, session_id, schedule):
        return self._answer("update_desktop_schedule", edh_uid, session_id, schedule)

    def list_software_stacks(self, kind):
        return self._answer("list_software_stacks", kind)

    def list_profiles(self, kind):
        return self._answer("list_profiles", kind)

    def ensure_user(self, edh_uid, display_name):
        return self._answer("ensure_user", edh_uid, display_name)

    def calls_to(self, wrapper):
        return [args for name, args in self.calls if name == wrapper]


def install_service_fakes(monkeypatch, *, claims=None, edh=None, enforce=None):
    """Replace the handler module's collaborators; returns the doubles installed."""
    claims = dict(USER_CLAIMS if claims is None else claims)
    edh = edh if edh is not None else FakeEdhClient()

    def claims_for(event):
        normalize_event(event)
        return dict(claims)

    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.side_effect = enforce if enforce is not None else (lambda obj, act: True)
    enforcer_factory = MagicMock(return_value=enforcer)
    client_factory = MagicMock(return_value=edh)

    table = MagicMock()
    table.query.return_value = {"Items": []}
    table.get_item.return_value = {}

    def read_by_key(**kwargs):
        # The table agrees with its session index: a key among the rows a scripted query serves reads as
        # that row, and any other key answers what the test scripted for get_item.
        if table.query.side_effect is None:
            for item in (table.query.return_value or {}).get("Items", []):
                if item.get("bootstrapId") == kwargs["Key"]["bootstrapId"]:
                    return {"Item": item}
        return DEFAULT

    table.get_item.side_effect = read_by_key

    lambda_client = MagicMock()
    lambda_client.invoke.return_value = {"StatusCode": 202}

    log_actions = MagicMock()
    log_authorization = MagicMock()

    recipe_store = MagicMock()
    recipe_store.available_builds_by_stack_id.return_value = {}
    recipe_store.registered_builds_by_stack_id.return_value = ({}, {})

    monkeypatch.setattr(service, "request_to_claims", claims_for)
    monkeypatch.setattr(service, "CasbinEnforcer", enforcer_factory)
    monkeypatch.setattr(service, "EdhClient", client_factory)
    monkeypatch.setattr(service, "edh_session_table", table)
    monkeypatch.setattr(service, "lambda_client", lambda_client)
    monkeypatch.setattr(service, "log_actions", log_actions)
    monkeypatch.setattr(service, "log_authorization", log_authorization)
    monkeypatch.setattr(service, "recipe_store", recipe_store)
    monkeypatch.setattr(service, "read_lease_policy", lambda: {
        "enabled": False, "leaseHours": 8, "forced": False, "graceHours": 24,
        "updatedAt": None, "updatedBy": None})
    return SimpleNamespace(
        module=service,
        table=table,
        enforcer=enforcer,
        enforcer_factory=enforcer_factory,
        client_factory=client_factory,
        edh=edh,
        lambda_client=lambda_client,
        log_actions=log_actions,
        log_authorization=log_authorization,
        recipe_store=recipe_store,
    )


def edh_proxy_records(fakes):
    """The custom_data of every edhProxy record written through log_actions."""
    return [call.args[2] for call in fakes.log_actions.call_args_list if call.args[1] == "edhProxy"]
