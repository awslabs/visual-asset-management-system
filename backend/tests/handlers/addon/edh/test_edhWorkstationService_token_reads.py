# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Only connect and Windows sign-in may read EDH's per-user desktop list for a desktop that can run, which
issues a connection token per desktop it renders, save the get and list routes' check of a running desktop's
display session (in-dev D21, ``_check_display_session``), which reads it for an ACTIVE row EDH's state read
reports running, that row's own session only, and never returns, logs or stores the link. Every other workstation
and catalog route reads desktop state through the token-free session-state route or not at all. The one other
read of the per-user list is the launch-failure read (in-dev D8), which the list and get routes and the session
sweep make for an ORPHANED row's own session only -- a desktop that never ran has no DCV session, so EDH issues no
token for it -- and no other handler module names the per-user route or either wrapper.

Every workstation and catalog route is driven through ``lambda_handler`` with the setups the route suites
already use, and its EDH calls are read from the scripted client. The audit maps, the client module and
the source of every other EDH module, the ``common`` library modules among them, are inspected as well, so
a direct call that bypasses the audit maps is caught too.
"""

import ast
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import backend.backend.handlers.addon.edh.edhAdminService as admin
import backend.backend.handlers.addon.edh.edhContainerService as containers
import backend.backend.handlers.addon.edh.edhLaunchWorker as worker
import backend.backend.handlers.addon.edh.edhWorkstationService as service
from backend.backend.common.apiRoutes import (
    API_EDH_CATALOG_CAPACITY_RESERVATIONS,
    API_EDH_CATALOG_IMAGES,
    API_EDH_CATALOG_INSTANCE_TYPES,
    API_EDH_CATALOG_LEASE,
    API_EDH_WORKSTATION,
    API_EDH_WORKSTATION_CONNECT,
    API_EDH_WORKSTATION_RENEW,
    API_EDH_WORKSTATION_START,
    API_EDH_WORKSTATION_STOP,
    API_EDH_WORKSTATION_WINDOWS_SIGN_IN,
    API_EDH_WORKSTATIONS,
    DELETE,
    EDH_ROUTES,
    GET,
    POST,
)
from backend.backend.handlers.addon.edh.common.edhClient import EDH_ROUTE_DESKTOPS_LIST, EdhClient
from backend.backend.handlers.addon.edh.common.edhIdentity import edh_uid_for
from backend.backend.handlers.addon.edh.common.edhSessionState import LAST_ERROR_EDH_REFUSED
from backend.tests.handlers.addon.edh.edhReservationTestSupport import (
    CR_FREE, FakeEc2, ec2_reservation, registry_item,
)
from backend.tests.handlers.addon.edh.edhServiceTestSupport import (
    IMAGES_PATH,
    INSTANCE_TYPES_PATH,
    SESSION_OTHER,
    SESSION_OWN,
    USER,
    WORKSTATIONS_PATH,
    FakeEdhClient,
    install_service_fakes,
    make_event,
    own_row,
    response_body,
    state_record,
    workstation_path,
)
from backend.tests.handlers.addon.edh.test_edhWorkstationService_catalog_images import _stacks
from backend.tests.handlers.addon.edh.test_edhWorkstationService_catalog_instance_types import _profiles
from backend.tests.handlers.addon.edh.test_edhWorkstationService_catalog_lease import _read
from backend.tests.handlers.addon.edh.test_edhWorkstationService_create import _launch, _post
from backend.tests.handlers.addon.edh.test_edhWorkstationService_renew import _renew_event, _seed_row, _set_policy
from backend.tests.handlers.addon.edh.test_edhWorkstationService_session_lifecycle import (
    _listed,
    _lost,
    _run,
    _stopped_row,
)

# The two routes that read EDH's per-user list, each for its one session.
CONNECTION_ROUTES = {(API_EDH_WORKSTATION_CONNECT.path, GET), (API_EDH_WORKSTATION_WINDOWS_SIGN_IN.path, GET)}
# The scenarios whose route checks the display session of the caller's running ACTIVE row (D21): each reads the
# per-user list for that row's own session, once.
CHECK_SCENARIOS = {"list_mixed_rows", "get_with_session"}
# The functions of the service allowed to call the connection wrapper.
CONNECTION_CALLERS = ("connect_workstation", "windows_sign_in", "_check_display_session")
# The bootstrap of a launch EDH has not listed yet, beside the caller's own session row.
BOOTSTRAP_CREATING = "5d4c3b2a-1f0e-4d9c-8b7a-6f5e4d3c2b1a"
# The EDH handler modules, the client among them, and the per-user route as the client sends it.
HANDLER_DIR = Path(service.__file__).resolve().parent
CLIENT_PATH = HANDLER_DIR / "common" / "edhClient.py"
PER_USER_ROUTE = "/api/dcv/virtual_desktops/list"
CONNECTION_WRAPPER = "get_desktop_connection"
# The launch-failure read, and the functions allowed to call it: the service's list/get capture and the sweep's.
FAILURE_WRAPPER = "get_desktop_failure"
PER_USER_WRAPPERS = (CONNECTION_WRAPPER, FAILURE_WRAPPER)
FAILURE_CALLERS = {"edhWorkstationService.py": "_capture_launch_failures",
                   "edhSessionReconciler.py": "_capture_launch_failure"}
# A library module the module walks must parse, and a floor on how many they parse: the package and its
# common/ subpackage hold 18 besides the client, the package root alone 9.
LIBRARY_MODULE = "common/edhSessionState.py"
WALKED_MODULE_FLOOR = 15
# A running desktop as the per-user list renders it, with synthetic credential values.
CONNECTABLE = {
    service.EDH_FIELD_SESSION_UUID: SESSION_OWN,
    service.EDH_FIELD_SESSION_STATE: "running",
    service.EDH_FIELD_CONNECTION_STRING: "https://dcv.example.internal/?authToken=abc&username=vabc",
    service.EDH_FIELD_LOCAL_ADMIN_PASSWORD: "winpw-" + "x" * 12,
}


def _session_less(state="CREATING", **overrides):
    """The caller's row of a launch EDH has not listed: no sessionId at all."""
    row = own_row(state=state, edhState=None, connectionAvailable=False, **overrides)
    row.pop("sessionId")
    return row


def _list(monkeypatch, rows, states=None):
    """The list route over ``rows``, EDH's session-state read answering ``states``: (response, fakes)."""
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_states=list(states or [])))
    fakes.table.query.return_value = {"Items": rows}
    return fakes.module.lambda_handler(make_event("GET", WORKSTATIONS_PATH), {}), fakes


def _lifecycle(monkeypatch, *args, **kwargs):
    """A {sessionId} route driven by the session-lifecycle suite's runner: (response, fakes)."""
    response, fakes, *_ = _run(monkeypatch, *args, **kwargs)
    return response, fakes


def _renew(monkeypatch):
    """The setup of the renew suite's test_renew_extends_lease_on_active_row: (response, fakes)."""
    fakes = install_service_fakes(monkeypatch)
    _set_policy(fakes, enabled=True, leaseHours=12)
    expiry = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    _seed_row(fakes, own_row(state="ACTIVE", leaseExpiresAt=expiry))
    return fakes.module.lambda_handler(_renew_event(SESSION_OWN), {}), fakes


def _create(monkeypatch):
    fakes = _launch(monkeypatch)
    return _post(fakes), fakes


def _catalog_images(monkeypatch):
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_software_stacks=_stacks()))
    return fakes.module.lambda_handler(make_event("GET", IMAGES_PATH), {}), fakes


def _catalog_instance_types(monkeypatch):
    fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(list_profiles=_profiles()))
    monkeypatch.setattr(service, "allowed_instance_types", ("g5.xlarge", "g5.2xlarge"))
    return fakes.module.lambda_handler(make_event("GET", INSTANCE_TYPES_PATH), {}), fakes


class _RegistryTable:
    """The registry rows of the session table, served by the prefix scan."""

    def scan(self, **kwargs):
        return {"Items": [registry_item(CR_FREE)]}


def _catalog_capacity_reservations(monkeypatch):
    fakes = install_service_fakes(monkeypatch)
    monkeypatch.setattr(service.reservations, "_session_table", _RegistryTable())
    monkeypatch.setattr(service.reservations, "ec2", FakeEc2({CR_FREE: ec2_reservation(CR_FREE)}))
    response = fakes.module.lambda_handler(make_event("GET", API_EDH_CATALOG_CAPACITY_RESERVATIONS.path), {})
    return response, fakes


# scenario -> (route path, method, build(monkeypatch) -> (response, fakes), expected status, reads state).
# Every build makes its rows and scripts afresh, since the handler updates a row in place and a Sequence
# is consumed.
SCENARIOS = {
    "list_mixed_rows": (
        API_EDH_WORKSTATIONS.path, GET,
        lambda mp: _list(mp, [own_row(), _session_less(bootstrapId=BOOTSTRAP_CREATING)], [state_record(SESSION_OWN)]),
        200, True),
    "list_only_creating_without_session": (
        API_EDH_WORKSTATIONS.path, GET, lambda mp: _list(mp, [_session_less()]), 200, False),
    "get_with_session": (
        API_EDH_WORKSTATION.path, GET,
        lambda mp: _lifecycle(mp, "GET", None, own_row(), desktop_states=[_listed("running")]), 200, True),
    "get_without_session": (
        API_EDH_WORKSTATION.path, GET,
        lambda mp: _lifecycle(mp, "GET", None, _session_less(), session_less=True), 200, False),
    "start_stopped": (
        API_EDH_WORKSTATION_START.path, POST,
        lambda mp: _lifecycle(mp, "POST", "start", _stopped_row(), desktop_states=[_listed("stopped")]), 202, True),
    "start_running_refused": (
        API_EDH_WORKSTATION_START.path, POST,
        lambda mp: _lifecycle(mp, "POST", "start", _stopped_row(), desktop_states=[_listed("running")]), 409, True),
    "stop": (
        API_EDH_WORKSTATION_STOP.path, POST, lambda mp: _lifecycle(mp, "POST", "stop", own_row()), 200, False),
    "stop_lost_answer": (
        API_EDH_WORKSTATION_STOP.path, POST,
        lambda mp: _lifecycle(mp, "POST", "stop", own_row(), stop_error=_lost("stop"),
                              desktop_states=[_listed("stopped")]),
        200, True),
    "delete": (
        API_EDH_WORKSTATION.path, DELETE, lambda mp: _lifecycle(mp, "DELETE", None, own_row()), 200, False),
    "delete_lost_answer": (
        API_EDH_WORKSTATION.path, DELETE,
        lambda mp: _lifecycle(mp, "DELETE", None, own_row(), delete_error=_lost("delete"), desktop_states=[]),
        200, True),
    "delete_without_session": (
        API_EDH_WORKSTATION.path, DELETE,
        lambda mp: _lifecycle(mp, "DELETE", None, _session_less("ORPHANED", lastError=LAST_ERROR_EDH_REFUSED),
                              session_less=True),
        200, False),
    "renew": (API_EDH_WORKSTATION_RENEW.path, POST, _renew, 200, False),
    "create": (API_EDH_WORKSTATIONS.path, POST, _create, 202, False),
    "catalog_images": (API_EDH_CATALOG_IMAGES.path, GET, _catalog_images, 200, False),
    "catalog_instance_types": (API_EDH_CATALOG_INSTANCE_TYPES.path, GET, _catalog_instance_types, 200, False),
    "catalog_lease": (API_EDH_CATALOG_LEASE.path, GET, _read, 200, False),
    "catalog_capacity_reservations": (
        API_EDH_CATALOG_CAPACITY_RESERVATIONS.path, GET, _catalog_capacity_reservations, 200, False),
}


def _written_names(fakes):
    """Every attribute name the route's row updates named."""
    return [name for call in fakes.table.update_item.call_args_list
            for name in call.kwargs["ExpressionAttributeNames"].values()]


def _written_values(fakes):
    """Every attribute value the route's row updates set."""
    return [value for call in fakes.table.update_item.call_args_list
            for placeholder, value in call.kwargs["ExpressionAttributeValues"].items() if placeholder.startswith(":v")]


def _route_values(route):
    """The EDH routes one audit-map entry names; a route keyed by kind or backend contributes each value."""
    _verb, target = route
    return list(target.values()) if isinstance(target, dict) else [target]


def _handler_trees():
    """Every EDH module but the client, the common/ library modules among them, parsed:
    [(path relative to the package, tree)]."""
    return [(path.relative_to(HANDLER_DIR).as_posix(), ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            for path in sorted(HANDLER_DIR.rglob("*.py")) if "__pycache__" not in path.parts and path != CLIENT_PATH]


def _service_tree(trees):
    (tree,) = [tree for name, tree in trees if name == "edhWorkstationService.py"]
    return tree


def _audit_routes_entry(tree, wrapper):
    """The key and value nodes of ``wrapper``'s entry in the EDH_AUDIT_ROUTES dict literal."""
    (routes,) = [node.value for node in ast.walk(tree)
                 if isinstance(node, ast.Assign)
                 and any(isinstance(target, ast.Name) and target.id == "EDH_AUDIT_ROUTES" for target in node.targets)]
    assert isinstance(routes, ast.Dict)
    (entry,) = [(key, value) for key, value in zip(routes.keys, routes.values)
                if isinstance(key, ast.Constant) and key.value == wrapper]
    return entry


def _node_ids(node):
    return {id(child) for child in ast.walk(node)}


def _route_loads(trees):
    """Every load of EDH_ROUTE_DESKTOPS_LIST, as a name or an attribute: [(file name, node)]."""
    return [(name, node) for name, tree in trees for node in ast.walk(tree)
            if isinstance(getattr(node, "ctx", None), ast.Load)
            and ((isinstance(node, ast.Name) and node.id == "EDH_ROUTE_DESKTOPS_LIST")
                 or (isinstance(node, ast.Attribute) and node.attr == "EDH_ROUTE_DESKTOPS_LIST"))]


def _connection_wrapper_names(trees, wrapper=CONNECTION_WRAPPER):
    """Every attribute named for, and every string equal to, a per-user wrapper: [(file name, node)]."""
    return [(name, node) for name, tree in trees for node in ast.walk(tree)
            if (isinstance(node, ast.Attribute) and node.attr == wrapper)
            or (isinstance(node, ast.Constant) and node.value == wrapper)]


def _function_body_ids(tree, *names):
    """The ids of every node inside the named functions; each name must be defined."""
    functions = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in names]
    assert sorted(function.name for function in functions) == sorted(names)
    return set().union(*(_node_ids(function) for function in functions))


def _client_tree(*wrappers):
    """The client module, parsed, and the ids of every node inside the named EdhClient wrappers (both
    per-user wrappers when none is named)."""
    tree = ast.parse(CLIENT_PATH.read_text(encoding="utf-8"), filename=str(CLIENT_PATH))
    (client_class,) = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "EdhClient"]
    return tree, _function_body_ids(client_class, *(wrappers or PER_USER_WRAPPERS))


def _route_definition(tree):
    """The value node of the module-level EDH_ROUTE_DESKTOPS_LIST assignment."""
    (value,) = [node.value for node in tree.body if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == "EDH_ROUTE_DESKTOPS_LIST"
                        for target in node.targets)]
    return value


@pytest.mark.unit
class TestTokenReads:
    def test_every_workstation_and_catalog_route_is_classified(self):
        listed = {(route.path, method) for route in EDH_ROUTES
                  if route.path.startswith(("/addon/edh/workstations", "/addon/edh/catalog"))
                  for method in route.methods}
        scenario_routes = {(path, method) for path, method, *_ in SCENARIOS.values()}
        assert listed == CONNECTION_ROUTES | scenario_routes
        assert not CONNECTION_ROUTES & scenario_routes

    @pytest.mark.parametrize("scenario", sorted(SCENARIOS))
    def test_a_route_that_is_not_a_connection_route_reads_the_per_user_list_only_to_check_its_own_session(
            self, monkeypatch, scenario):
        _path, _method, build, status, _reads_state = SCENARIOS[scenario]

        response, fakes = build(monkeypatch)

        assert response["statusCode"] == status
        checks = [(edh_uid_for(USER), SESSION_OWN)] if scenario in CHECK_SCENARIOS else []
        assert fakes.edh.calls_to(CONNECTION_WRAPPER) == checks
        list_wrappers = {wrapper for wrapper, audit in service.EDH_AUDIT_ROUTES.items()
                         if audit[1] == EDH_ROUTE_DESKTOPS_LIST}
        assert CONNECTION_WRAPPER in list_wrappers and len(list_wrappers) > 1
        assert all(fakes.edh.calls_to(wrapper) == [] for wrapper in list_wrappers - {CONNECTION_WRAPPER})

    @pytest.mark.parametrize("scenario", sorted(SCENARIOS))
    def test_the_scenarios_reach_edh(self, monkeypatch, scenario):
        """Control for the guard above: each scenario reads EDH's state exactly when its route does."""
        _path, _method, build, _status, reads_state = SCENARIOS[scenario]

        _response, fakes = build(monkeypatch)

        assert bool(fakes.edh.calls_to("get_desktop_states")) is reads_state

    @pytest.mark.parametrize("action, overrides", [("connect", {}), ("windows-sign-in", {"osFamily": "windows"})],
                             ids=["connect", "windows-sign-in"])
    def test_connect_and_sign_in_read_exactly_their_own_session(self, monkeypatch, action, overrides):
        fakes = install_service_fakes(monkeypatch, edh=FakeEdhClient(get_desktop_connection=dict(CONNECTABLE)))
        fakes.table.query.return_value = {"Items": [own_row(**overrides)]}
        event = make_event("GET", workstation_path(SESSION_OWN, action), path_params={"sessionId": SESSION_OWN})

        response = fakes.module.lambda_handler(event, {})

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to(CONNECTION_WRAPPER) == [(edh_uid_for(USER), SESSION_OWN)]
        assert fakes.edh.calls_to("get_desktop_states") == []

    def test_only_the_connection_wrapper_is_routed_to_the_per_user_list(self):
        maps = {
            "edhWorkstationService": (service.EDH_AUDIT_ROUTES,),
            "edhLaunchWorker": (worker.WORKER_AUDIT_ROUTES,),
            "edhAdminService": (admin.EDH_CALLS, admin.EDH_KIND_CALLS),
            "edhContainerService": (containers.EDH_CALLS, containers.EDH_BACKEND_CALLS),
        }
        routed = {(handler, wrapper) for handler, tables in maps.items() for table in tables
                  for wrapper, route in table.items() if EDH_ROUTE_DESKTOPS_LIST in _route_values(route)}
        assert routed == {("edhWorkstationService", wrapper) for wrapper in PER_USER_WRAPPERS}

    def test_only_the_connection_wrapper_names_the_per_user_route_in_the_client(self):
        # Module-level helpers, other classes and every EdhClient member are walked alike.
        tree, allowed = _client_tree()
        stray = [node.lineno for _name, node in _route_loads([(CLIENT_PATH.name, tree)]) if id(node) not in allowed]
        assert stray == []
        literal = [node for node in ast.walk(tree) if isinstance(node, ast.Constant) and node.value == PER_USER_ROUTE]
        assert [id(node) for node in literal] == [id(_route_definition(tree))]
        assert not hasattr(EdhClient, "list_desktops")

    @pytest.mark.parametrize("wrapper", PER_USER_WRAPPERS)
    def test_the_client_walk_finds_each_per_user_wrappers_route_load(self, wrapper):
        """Control for the walk above: it finds each wrapper's own load of the route."""
        tree, allowed = _client_tree(wrapper)
        loads = _route_loads([(CLIENT_PATH.name, tree)])
        assert any(id(node) in allowed for _name, node in loads)

    def test_the_module_walks_parse_the_library_modules_but_not_the_client(self):
        """Control for the walks below: they reach common/, so a library module is held like a handler."""
        names = [name for name, _tree in _handler_trees()]
        assert LIBRARY_MODULE in names
        assert CLIENT_PATH.relative_to(HANDLER_DIR).as_posix() not in names
        assert len(names) >= WALKED_MODULE_FLOOR

    def test_no_handler_module_sends_the_per_user_route_itself(self):
        trees = _handler_trees()
        literal = [(name, node.lineno) for name, tree in trees for node in ast.walk(tree)
                   if isinstance(node, ast.Constant) and node.value == PER_USER_ROUTE]
        assert literal == []
        allowed = set().union(*(_node_ids(_audit_routes_entry(_service_tree(trees), wrapper)[1])
                                for wrapper in PER_USER_WRAPPERS))
        stray = [(name, node.lineno) for name, node in _route_loads(trees)
                 if name != "edhWorkstationService.py" or id(node) not in allowed]
        assert stray == []

    @pytest.mark.parametrize("wrapper", PER_USER_WRAPPERS)
    def test_the_route_walk_finds_each_per_user_wrappers_audit_entry(self, wrapper):
        """Control for the walk above: it finds the load in each entry it allows, in the service only."""
        trees = _handler_trees()
        _key, value = _audit_routes_entry(_service_tree(trees), wrapper)
        loads = _route_loads(trees)
        assert {name for name, _node in loads} == {"edhWorkstationService.py"}
        assert any(id(node) in _node_ids(value) for _name, node in loads)

    def test_only_connect_sign_in_and_the_display_session_check_call_the_connection_wrapper(self):
        trees = _handler_trees()
        tree = _service_tree(trees)
        key, _value = _audit_routes_entry(tree, CONNECTION_WRAPPER)
        allowed = {id(key)} | _function_body_ids(tree, *CONNECTION_CALLERS)
        stray = [(name, node.lineno) for name, node in _connection_wrapper_names(trees)
                 if name != "edhWorkstationService.py" or id(node) not in allowed]
        assert stray == []

    def test_only_the_failure_captures_call_the_failure_wrapper(self):
        trees = dict(_handler_trees())
        key, _value = _audit_routes_entry(trees["edhWorkstationService.py"], FAILURE_WRAPPER)
        allowed = {id(key)}
        for name, function in FAILURE_CALLERS.items():
            allowed |= _function_body_ids(trees[name], function)
        stray = [(name, node.lineno) for name, node in _connection_wrapper_names(trees.items(), FAILURE_WRAPPER)
                 if id(node) not in allowed]
        assert stray == []

    def test_the_failure_wrapper_walk_finds_the_audit_key_and_both_captures(self):
        """Control for the walk above: the audit key, and a use in each capture function."""
        trees = dict(_handler_trees())
        found = _connection_wrapper_names(trees.items(), FAILURE_WRAPPER)
        assert {name for name, _node in found} == set(FAILURE_CALLERS)
        ids = {id(node) for _name, node in found}
        key, _value = _audit_routes_entry(trees["edhWorkstationService.py"], FAILURE_WRAPPER)
        assert id(key) in ids
        for name, function in FAILURE_CALLERS.items():
            assert ids & _function_body_ids(trees[name], function), function

    def test_the_failure_read_asks_for_the_orphaned_rows_own_session_only(self, monkeypatch):
        orphaned = own_row(state="ORPHANED", edhState="error", connectionAvailable=False,
                           lastError=LAST_ERROR_EDH_REFUSED)
        response, fakes = _list(monkeypatch, [own_row(bootstrapId=BOOTSTRAP_CREATING, sessionId=SESSION_OTHER),
                                              orphaned],
                                [state_record(SESSION_OTHER), state_record(SESSION_OWN, "error")])

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to(FAILURE_WRAPPER) == [(edh_uid_for(USER), SESSION_OWN)]
        # The display-session check (D21) reads the running row's own session, never the orphaned one's.
        assert fakes.edh.calls_to(CONNECTION_WRAPPER) == [(edh_uid_for(USER), SESSION_OTHER)]

    def test_the_wrapper_walk_finds_the_audit_key_and_the_three_calls(self):
        """Control for the walk above: the audit key, and one call in each of connect, sign-in and the check."""
        trees = _handler_trees()
        tree = _service_tree(trees)
        key, _value = _audit_routes_entry(tree, CONNECTION_WRAPPER)
        found = _connection_wrapper_names(trees)
        assert len(found) == 4
        assert {name for name, _node in found} == {"edhWorkstationService.py"}
        ids = {id(node) for _name, node in found}
        assert id(key) in ids
        for function in CONNECTION_CALLERS:
            assert len(ids & _function_body_ids(tree, function)) == 1, function

    def test_no_user_route_writes_a_session_id_from_a_state_read(self, monkeypatch):
        # EDH answers an id no row asked for beside the one asked for.
        response, fakes = _list(monkeypatch, [own_row(), _session_less(bootstrapId=BOOTSTRAP_CREATING)],
                                [state_record(SESSION_OWN), state_record(SESSION_OTHER)])

        assert response["statusCode"] == 200
        assert fakes.edh.calls_to("get_desktop_states") == [(edh_uid_for(USER), [SESSION_OWN])]
        assert "sessionId" not in _written_names(fakes)
        (creating,) = [item for item in response_body(response)["Items"] if item["bootstrapId"] == BOOTSTRAP_CREATING]
        assert creating["sessionId"] is None

    def test_the_same_answer_updates_the_row_whose_state_it_reports(self, monkeypatch):
        """Control for the guard above: the answer is merged onto the row holding its id."""
        stale = own_row(state="CREATING", edhState="pending", connectionAvailable=False)
        response, fakes = _list(monkeypatch, [stale, _session_less(bootstrapId=BOOTSTRAP_CREATING)],
                                [state_record(SESSION_OWN), state_record(SESSION_OTHER)])

        assert response["statusCode"] == 200
        assert {"ACTIVE", "running", True} <= set(_written_values(fakes))
        assert "sessionId" not in _written_names(fakes)
