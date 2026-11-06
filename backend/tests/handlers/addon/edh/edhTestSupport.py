# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Fixture access and network fakes shared by the EDH client and handler tests.

Fixtures are sanitized request/response envelopes (``fixtureSanitizer.build_envelope``), derived from
the EDH source until the live spike replaces them. Each file is one JSON object::

    {"name": "virtual_desktops_create", "capturedAt": "2026-09-11T00:00:00Z", "edhVersion": "26.8.0",
     "request":  {"method": "POST", "path": "/api/dcv/virtual_desktops/create",
                  "absolutePath": "/api/dcv/virtual_desktops/create", "query": {},
                  "headers": {"X-EDH-USER": "vspikeaaaaaaaaaa"}, "tokenHeaderName": "X-EDH-TOKEN",
                  "body": {"instance_type": "g5.xlarge", ...} | null},
     "response": {"status": 200, "contentType": "application/json", "elapsedSeconds": 0.0,
                  "json": {"success": true, "message": ...}},
     "expect":   {"sessionName": "..."}}

EDH mounts every route at ``/api/...`` and the endpoint carries no path, so ``request.path`` equals
``request.absolutePath``; ``request.query`` is always a dict of strings; ``request.body`` is the flat
form-field dict the recorded call sent (values are strings) or null; ``request.headers`` carries the
user header when the recorded call sent one and never the token; ``response.json`` is the raw EDH
envelope, or ``{"_raw": <text>}`` for the one ``text/plain`` route (the ``.dcv`` connection file);
``expect`` holds the producer's oracles. Every credential-shaped value is the sanitizer's ``REDACTED``
marker. Tests compare what the client sent to ``request`` and read EDH-side field names from the
captures rather than hard-coding them.
"""

import json
import os
import urllib.parse

import pytest

from backend.tests.handlers.addon.edh.fixtureSanitizer import REDACTED  # noqa: F401

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
USER_HEADER = "X-EDH-USER"
# The project every EDH Lambda assigns a registered stack to, as the EDH conftest sets it.
DEFAULT_PROJECT = os.environ["EDH_VDI_DEFAULT_PROJECT"]

# Test key -> the capture under fixtures/, by the fixture registry's file name.
FIXTURE_FILES = {
    "auth_missing_user_header": "auth_missing_user_header.json",
    "auth_wrong_token": "auth_wrong_token.json",
    "desktops_list_active": "virtual_desktops_list_active.json",
    "desktops_list_all": "virtual_desktops_list_all.json",
    "desktops_list_after_create": "virtual_desktops_list_after_create.json",
    "desktops_session_state": "virtual_desktops_session_state.json",
    "desktops_session_state_stopped": "virtual_desktops_session_state_stopped.json",
    "desktop_create": "virtual_desktops_create.json",
    "desktop_create_windows": "virtual_desktops_create_windows.json",
    "desktop_stop": "virtual_desktops_stop.json",
    "desktop_start": "virtual_desktops_start.json",
    "desktop_delete": "virtual_desktops_delete.json",
    "desktop_schedule": "virtual_desktops_schedule.json",
    "desktop_not_found": "virtual_desktops_not_found.json",
    "connection_file_linux": "connection_string_linux.json",
    "connection_file_windows": "connection_string_windows.json",
    "software_stacks_dcv": "software_stacks_dcv_list.json",
    "software_stacks_target_nodes": "software_stacks_target_nodes_list.json",
    "software_stack_register_dcv": "software_stacks_dcv_register.json",
    "software_stack_register_target_nodes": "software_stacks_target_nodes_register.json",
    "software_stack_delete_dcv": "software_stacks_dcv_delete.json",
    "profiles_dcv": "profiles_dcv_list.json",
    "profiles_target_nodes": "profiles_target_nodes_list.json",
    "ldap_user_create": "ldap_user_create.json",
    "ldap_user_duplicate": "ldap_user_duplicate.json",
    "ldap_user_get_by_uid": "ldap_user_get_by_uid.json",
    "ldap_user_get_by_uid_absent": "ldap_user_get_by_uid_absent.json",
    "batch_job_definitions": "batch_job_definition_list.json",
    "batch_job_queues": "batch_job_queue_list.json",
    "batch_job_submit": "batch_job_submit.json",
    "batch_jobs_list": "batch_jobs_list.json",
    "batch_job_kill": "batch_job_kill.json",
    "eks_clusters_list": "eks_clusters_list.json",
    "eks_job_submit": "eks_job_submit.json",
    "eks_jobs_list": "eks_jobs_list.json",
    "eks_job_kill": "eks_job_kill.json",
    "quota_error": "quota_error.json",
    # Recorded refusals and probes, replayed through the client's classification arms.
    "batch_job_definition_register_probe": "batch_job_definition_register_probe.json",
    "containers_ecr_repository_list_v1_probe": "containers_ecr_repository_list_v1_probe.json",
    "desktop_schedule_disabled": "virtual_desktops_schedule_disabled.json",
    "desktop_schedule_not_found": "virtual_desktops_schedule_not_found.json",
    "desktop_start_foreign_owner": "virtual_desktops_start_foreign_owner.json",
    "desktop_stop_foreign_owner": "virtual_desktops_stop_foreign_owner.json",
    "disk_size_out_of_range": "disk_size_out_of_range.json",
    "instance_type_not_allowed": "instance_type_not_allowed.json",
    "ldap_user_create_rejected_minimal": "ldap_user_create_rejected_minimal.json",
    "ldap_user_invalid_uid": "ldap_user_invalid_uid.json",
    "ldap_user_uid_too_long": "ldap_user_uid_too_long.json",
    "ldap_users_list_v1_probe": "ldap_users_list_v1_probe.json",
    "software_stacks_dcv_register_json_probe": "software_stacks_dcv_register_json_probe.json",
}

# Captures a live spike writes only when the recording cluster has the feature on (containers, an EKS
# cluster, a simulation AMI); the source-derived set carries all of them.
OPTIONAL_FIXTURES = frozenset({
    "batch_job_definitions", "batch_job_queues", "batch_job_submit", "batch_jobs_list", "batch_job_kill",
    "eks_clusters_list", "eks_job_submit", "eks_jobs_list", "eks_job_kill",
    "software_stack_register_target_nodes",
})


def fixture_path(key):
    return os.path.join(FIXTURES_DIR, FIXTURE_FILES[key])


def load_fixture(key):
    """The parsed fixture registered under ``key``; a missing file is an error."""
    with open(fixture_path(key), "r", encoding="utf-8") as handle:
        return json.load(handle)


def fixture_or_skip(key):
    """``load_fixture``, except that an OPTIONAL capture that was never recorded skips the test."""
    if key in OPTIONAL_FIXTURES and not os.path.exists(fixture_path(key)):
        pytest.skip(f"{FIXTURE_FILES[key]} was not captured (containers feature off or no EKS cluster on the recording cluster)")
    return load_fixture(key)


def first_value_of(doc, key):
    """The first value stored under ``key`` anywhere in ``doc`` (depth-first), or None."""
    if isinstance(doc, dict):
        if key in doc:
            return doc[key]
        for value in doc.values():
            found = first_value_of(value, key)
            if found is not None:
                return found
    elif isinstance(doc, list):
        for item in doc:
            found = first_value_of(item, key)
            if found is not None:
                return found
    return None


def count_key(doc, key):
    """How many mappings anywhere in ``doc`` carry ``key``."""
    if isinstance(doc, dict):
        return int(key in doc) + sum(count_key(value, key) for value in doc.values())
    if isinstance(doc, list):
        return sum(count_key(item, key) for item in doc)
    return 0


class FakeResponse:
    """The two attributes of a urllib3 response the client reads."""

    def __init__(self, status, body=None):
        self.status = status
        if body is None:
            self.data = b""
        elif isinstance(body, (bytes, bytearray)):
            self.data = bytes(body)
        elif isinstance(body, str):
            self.data = body.encode("utf-8")
        else:
            self.data = json.dumps(body).encode("utf-8")


def replay_response(fixture):
    """A FakeResponse carrying the capture's status and body. The sanitizer stores a non-JSON body as
    ``{"_raw": <text>}``; that replays as the text itself."""
    body = fixture["response"]["json"]
    if isinstance(body, dict) and set(body) == {"_raw"}:
        return FakeResponse(fixture["response"]["status"], body["_raw"])
    return FakeResponse(fixture["response"]["status"], body)


def sent_form(call):
    """The form fields one recorded attempt sent, decoded from its urlencoded body; None without one."""
    body = call.get("body")
    if body is None:
        return None
    pairs = urllib.parse.parse_qsl(body.decode("utf-8"), keep_blank_values=True)
    return {key: value for key, value in pairs}


class FakeClock:
    """Stands in for the client's ``time`` module: a clock that advances only when told.

    ``sleep`` records the pause and advances the clock by it, so a backoff test reads ``slept``
    and a deadline test reads what the client was left with.
    """

    def __init__(self, start=1000.0):
        self.now = float(start)
        self.slept = []

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds

    def advance(self, seconds):
        self.now += seconds


class Slow:
    """A per-attempt outcome that first advances the fake clock by ``seconds``."""

    def __init__(self, seconds, result):
        self.seconds = seconds
        self.result = result


def install_client_fakes(monkeypatch, *, root_keys, results):
    """Fake the secret loader, the HTTP seam and the clock, recording every attempt.

    root_keys: root-key values handed out by successive secret reads.
    results: per-attempt outcomes -- a FakeResponse, an int status (200 with an empty JSON
        object body), an Exception instance to raise from the seam, or a Slow wrapping one of
        those to spend fake time on the attempt first.
    Returns (edhClient module, recorded) where recorded carries ``secret_reads``, the ``clock``,
    and one entry per attempt in ``calls`` (``method``, ``url``, and the kwargs the seam received).
    """
    import backend.backend.handlers.addon.edh.common.edhClient as ec

    key_iter = iter(root_keys)
    result_iter = iter(results)
    clock = FakeClock()
    recorded = {"secret_reads": 0, "calls": [], "clock": clock}

    def fake_load_root_key():
        if ec._cached_root_key is not None:
            return ec._cached_root_key
        recorded["secret_reads"] += 1
        ec._cached_root_key = next(key_iter)
        return ec._cached_root_key

    def fake_http_request(method, url, **kwargs):
        recorded["calls"].append({"method": method, "url": url, **kwargs})
        result = next(result_iter)
        if isinstance(result, Slow):
            clock.advance(result.seconds)
            result = result.result
        if isinstance(result, Exception):
            raise result
        if isinstance(result, int):
            return FakeResponse(result, {})
        return result

    monkeypatch.setattr(ec, "_load_edh_root_key", fake_load_root_key)
    monkeypatch.setattr(ec, "_http_request", fake_http_request)
    monkeypatch.setattr(ec, "time", clock)
    return ec, recorded


class FakeProjects:
    """The projects side of a fake EDH client: the project table a handler suite assigns stacks in.

    The assignment runs the real client wrapper with only its HTTP hop replaced, so a handler suite
    reads the form EDH would receive rather than a second implementation of it. ``rows`` is the
    project table keyed by id as EDH's listing returns it, ``puts`` the form of every project update
    sent, and ``stack_ids`` the membership the updates have built up.
    """

    def __init__(self, name=DEFAULT_PROJECT, *, project_id=1, software_stack_ids=(),
                 target_node_software_stack_ids=(), **fields):
        import backend.backend.handlers.addon.edh.common.edhClient as ec

        self._ec = ec
        self.rows = {str(project_id): {
            "id": project_id,
            "project_name": name,
            "is_active": True,
            "description": "The default project",
            "aws_budget": None,
            "hardware_profile_id": None,
            "software_stack_ids": [int(value) for value in software_stack_ids],
            "target_node_software_stack_ids": [int(value) for value in target_node_software_stack_ids],
            "application_profile_ids": [],
            "allowed_users": "alice,bob",
            "denied_users": "",
            "allowed_groups": "designers",
            "denied_groups": "",
            **fields,
        }}
        self.puts = []
        self._client = ec.EdhClient(profile="batch")
        self._client.request = self._request

    def _request(self, method, path, *, edh_uid=None, form=None, files=None, params=None):
        """The project route, answered from ``rows``; a PUT is recorded and applied as EDH applies it."""
        assert path == self._ec.EDH_ROUTE_PROJECTS, path
        if method == "GET":
            return {"success": True, "message": {key: dict(row) for key, row in self.rows.items()}}
        assert method == "PUT", method
        self.puts.append(dict(form))
        row = self.rows[str(form["project_id"])]
        for listed, updated in self._ec.EDH_PROJECT_STACK_ID_FIELDS.values():
            row[listed] = [int(value) for value in str(form[updated]).split(",") if value]
        return {"success": True, "message": "Project updated"}

    def list_projects(self, *, edh_uid=None):
        return self._client.list_projects()

    def assign(self, kind, stack_id, project_name, *, edh_uid=None):
        """The client's own assignment against this table; raises the errors the client raises."""
        return self._client.assign_software_stack_to_project(kind, stack_id, project_name)

    def stack_ids(self, kind):
        """The stack ids the project carries for one kind, as strings."""
        listed = self._ec.EDH_PROJECT_STACK_ID_FIELDS[kind][0]
        return [str(value) for row in self.rows.values() for value in row[listed]]
