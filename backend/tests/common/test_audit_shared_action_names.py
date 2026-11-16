# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""An action name several routes register records the entity of the route the request matched.

The four metadata routes (asset link, asset, file and database metadata) register the same action
names (metadataGet, metadataCreate, metadataUpdate, metadataDelete) with different entity types. A
record written for one of them names the entity type of the request's own route, inside and outside a
request audit context, and an action no route of the request registers keeps the type registered for
its name. The cases come from the real registry, so a new shared name is covered when it is added.
"""

import re
from collections import defaultdict
from types import SimpleNamespace

import pytest

from test_audit_action_records import (
    LAMBDA_CONTEXT,
    REGISTRY,
    RecordingClient,
    _Claims,
    api_event,
    auditLogging,
    group_name,
    mask_sensitive_data,
    parse,
)


def _shared_name_routes():
    """Every (route, method, spec) whose action name another entry registers with another entity."""
    by_name = defaultdict(list)
    for (route, method), audit_spec in REGISTRY.AUDIT_ROUTE_ACTIONS.items():
        by_name[audit_spec.action].append((route, method, audit_spec))
    for audit_spec in REGISTRY.AUDIT_SECONDARY_ACTIONS:
        by_name[audit_spec.action].append((None, None, audit_spec))
    for system in REGISTRY.AUDIT_SYSTEM_ACTIONS:
        by_name[system.spec.action].append((None, None, system.spec))
    cases = []
    for entries in by_name.values():
        if len({audit_spec.entity_type for _, _, audit_spec in entries}) > 1:
            cases.extend((route, method, audit_spec) for route, method, audit_spec in entries if route)
    return sorted(cases, key=lambda case: (case[2].action, case[0], case[1]))


SHARED = _shared_name_routes()
_IDS = [f"{spec.action}-{method}-{route}" for route, method, spec in SHARED]


def _route_event(route, method):
    names = re.findall(r"{([^}+]+)\+?}", route)
    path_parameters = {name: f"{name}-1" for name in names}
    path = re.sub(r"{([^}+]+)\+?}", lambda m: f"{m.group(1)}-1", route)
    event = api_event(path=path, method=method)
    event["resource"] = route
    event["pathParameters"] = path_parameters
    return event


@pytest.fixture
def clients(monkeypatch):
    monkeypatch.setattr(auditLogging, "request_to_claims", _Claims())
    monkeypatch.setattr(auditLogging, "mask_sensitive_data", mask_sensitive_data)
    monkeypatch.setattr(auditLogging, "_created_log_streams", {})
    module_client, flush_client = RecordingClient(), RecordingClient()
    monkeypatch.setattr(auditLogging, "cloudwatch_logs", module_client)
    monkeypatch.setattr(auditLogging, "audit_flush_client", flush_client)
    return SimpleNamespace(module=module_client, flush=flush_client)


def _payload(clients, audit_spec):
    messages = clients.flush.messages(group_name(audit_spec.group)) + clients.module.messages(
        group_name(audit_spec.group))
    assert len(messages) == 1, messages
    return parse(messages[0])[1]


@pytest.mark.unit
class TestSharedActionNames:
    def test_the_registry_has_shared_names_to_check(self):
        assert {spec.action for _, _, spec in SHARED} >= {
            "metadataGet", "metadataCreate", "metadataUpdate", "metadataDelete"}
        entities = {spec.entity_type for _, _, spec in SHARED if spec.action == "metadataUpdate"}
        assert entities == {"assetLink", "asset", "file", "database"}

    @pytest.mark.parametrize("route, method, audit_spec", SHARED, ids=_IDS)
    def test_inside_a_context_the_record_names_the_routes_entity(self, clients, route, method, audit_spec):
        event = _route_event(route, method)
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, audit_spec.action, **auditLogging._path_ids(event))
        payload = _payload(clients, audit_spec)
        assert payload["action"] == audit_spec.action
        assert payload["entity"]["type"] == audit_spec.entity_type
        assert payload["request"]["route"] == route

    @pytest.mark.parametrize("route, method, audit_spec", SHARED, ids=_IDS)
    def test_outside_a_context_the_record_names_the_routes_entity(self, clients, route, method, audit_spec):
        event = _route_event(route, method)
        auditLogging.log_action(event, audit_spec.action, **auditLogging._path_ids(event))
        payload = _payload(clients, audit_spec)
        assert payload["entity"]["type"] == audit_spec.entity_type

    def test_the_asset_metadata_update_names_the_asset(self, clients):
        event = _route_event("/database/{databaseId}/assets/{assetId}/metadata", "PUT")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, "metadataUpdate", database_id="databaseId-1", asset_id="assetId-1")
        payload = _payload(clients, REGISTRY.audit_spec_for_action("metadataUpdate"))
        assert payload["entity"] == {"type": "asset", "id": "assetId-1"}

    def test_an_action_the_route_does_not_register_keeps_its_own_entity(self, clients):
        event = _route_event("/database/{databaseId}/assets/{assetId}", "PUT")
        with auditLogging.audit_request(event, LAMBDA_CONTEXT):
            auditLogging.log_action(event, "metadataUpdate", database_id="databaseId-1", asset_id="assetId-1")
        payload = _payload(clients, REGISTRY.audit_spec_for_action("metadataUpdate"))
        assert payload["entity"]["type"] == REGISTRY.audit_spec_for_action("metadataUpdate").entity_type
