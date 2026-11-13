# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every API route-method and every audited handler is in the audit action registry.

``common/auditActions.py`` (core, merged with the add-on and fork-owned lanes) maps each route-method
of ``ALL_API_ROUTES`` to the action it records, or names why it records none. These tests hold that
mapping complete and consistent, and hold the registry to the handler code:

* a route-method that is neither registered nor exempt fails;
* an action name that is not lower camelCase, or two entries of one action that disagree on group,
  kind or legacy type, fail;
* a literal type a handler passes to ``log_actions`` / ``log_auth_changes`` / ``log_auth_other`` /
  ``log_action`` that the registry does not know fails;
* a registered action that no handler records must be on ``AUDIT_PENDING_ACTIONS``, and an action on
  that list that a handler already records fails, so the list only shrinks;
* each non-API handler the registry names exists.

The scans read source with ``ast``; every check has a control proving it can fail.
"""

import ast
import importlib
import os

import pytest

from common.apiRoutes import ALL_API_ROUTES, ApiRoute, GET, POST

REGISTRY = importlib.import_module("common.auditActions")

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
HANDLERS = os.path.join(BACKEND, "handlers")

# The audit functions whose second argument names the action (or its legacy type).
_TYPED_AUDIT_FUNCTIONS = {"log_action", "log_actions", "log_auth_changes", "log_auth_other"}
# The audit functions that record a file action without a [type: ...] segment.
_FILE_AUDIT_FUNCTIONS = {"log_file_upload", "log_file_download", "log_file_download_bulk",
                         "log_file_download_streamed"}
# log_file_download_bulk writes the records log_file_download writes, one per file.
_FILE_FUNCTION_ALIASES = {"log_file_download_bulk": "log_file_download"}


def _route_methods(routes):
    return {(route.path, method) for route in routes for method in route.methods}


def _python_files(root):
    for directory, _, files in os.walk(root):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(directory, name)


def _module_string_constants(tree):
    constants = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    constants[target.id] = node.value.value
    return constants


def _call_name(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _scan_source(source):
    """(literal types passed to the typed audit functions, file audit functions called)."""
    tree = ast.parse(source)
    constants = _module_string_constants(tree)
    literals, file_functions = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _call_name(node)
        if name in _FILE_AUDIT_FUNCTIONS:
            file_functions.add(_FILE_FUNCTION_ALIASES.get(name, name))
        if name not in _TYPED_AUDIT_FUNCTIONS:
            continue
        argument = node.args[1] if len(node.args) > 1 else None
        for keyword in node.keywords:
            if keyword.arg in ("action", "secondary_type"):
                argument = keyword.value
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            literals.add(argument.value)
        elif isinstance(argument, ast.Name) and argument.id in constants:
            literals.add(constants[argument.id])
    return literals, file_functions


def _scan_handlers():
    literals, file_functions = set(), set()
    for path in _python_files(HANDLERS):
        with open(path, encoding="utf-8") as handle:
            found_literals, found_functions = _scan_source(handle.read())
        literals |= found_literals
        file_functions |= found_functions
    return literals, file_functions


def _recorded_actions(actions, literals, file_functions):
    """The registered actions some handler records, by name, legacy type or legacy file function."""
    recorded = set()
    for name, audit_spec in actions.items():
        if name in literals or (audit_spec.legacy_type and audit_spec.legacy_type in literals):
            recorded.add(name)
        elif audit_spec.legacy_function and audit_spec.legacy_function in file_functions:
            recorded.add(name)
    return recorded


def _all_specs():
    return (list(REGISTRY.AUDIT_ROUTE_ACTIONS.values()) + list(REGISTRY.AUDIT_SECONDARY_ACTIONS)
            + [system.spec for system in REGISTRY.AUDIT_SYSTEM_ACTIONS])


@pytest.fixture(scope="module")
def handler_scan():
    return _scan_handlers()


@pytest.mark.unit
class TestRouteCoverage:
    def test_every_route_method_is_registered_or_exempt(self):
        covered = set(REGISTRY.AUDIT_ROUTE_ACTIONS) | set(REGISTRY.AUDIT_EXEMPT)
        missing = sorted(_route_methods(ALL_API_ROUTES) - covered)
        assert not missing, ("route-methods with no audit action and no exemption; add them to "
                             f"common/auditActions.py (or the add-on/custom lane): {missing}")

    def test_no_entry_names_a_route_method_that_does_not_exist(self):
        known = _route_methods(ALL_API_ROUTES)
        stale = sorted((set(REGISTRY.AUDIT_ROUTE_ACTIONS) | set(REGISTRY.AUDIT_EXEMPT)) - known)
        assert not stale, f"audit registry entries for route-methods that no longer exist: {stale}"

    def test_no_route_method_is_both_registered_and_exempt(self):
        both = sorted(set(REGISTRY.AUDIT_ROUTE_ACTIONS) & set(REGISTRY.AUDIT_EXEMPT))
        assert not both, f"route-methods both registered and exempt: {both}"

    def test_every_exemption_has_a_reason(self):
        empty = [key for key, reason in REGISTRY.AUDIT_EXEMPT.items() if not str(reason).strip()]
        assert not empty, f"exemptions without a reason: {empty}"

    def test_the_lanes_do_not_repeat_a_route_method(self):
        lanes = (REGISTRY.CORE_AUDIT_ROUTE_ACTIONS, REGISTRY.ADDON_AUDIT_ROUTE_ACTIONS,
                 REGISTRY.CUSTOM_AUDIT_ROUTE_ACTIONS)
        seen, repeated = set(), []
        for lane in lanes:
            repeated += sorted(set(lane) & seen)
            seen |= set(lane)
        assert not repeated, f"route-methods registered by two lanes: {repeated}"

    def test_the_coverage_check_detects_an_unregistered_route(self):
        # Control: a route the registry has never seen must be reported.
        unknown = ApiRoute("/control/never-registered", (POST,), "control")
        covered = set(REGISTRY.AUDIT_ROUTE_ACTIONS) | set(REGISTRY.AUDIT_EXEMPT)
        assert _route_methods([unknown]) - covered == {("/control/never-registered", POST)}


@pytest.mark.unit
class TestActionNames:
    def test_every_action_name_is_lower_camel_case(self):
        bad = sorted({audit_spec.action for audit_spec in _all_specs()
                      if not REGISTRY.AUDIT_ACTION_NAME_PATTERN.match(audit_spec.action)})
        assert not bad, f"action names that are not lower camelCase: {bad}"

    def test_entries_of_one_action_agree(self):
        by_name = {}
        conflicts = []
        for audit_spec in _all_specs():
            shape = (audit_spec.group, audit_spec.kind, audit_spec.legacy_type, audit_spec.legacy_function)
            first = by_name.setdefault(audit_spec.action, shape)
            if first != shape:
                conflicts.append((audit_spec.action, first, shape))
        assert not conflicts, f"one action registered with different group/kind/legacy: {conflicts}"

    def test_every_spec_uses_a_known_group_kind_and_entity_type(self):
        bad = [audit_spec for audit_spec in _all_specs()
               if audit_spec.group not in REGISTRY.AUDIT_GROUPS
               or audit_spec.kind not in REGISTRY.AUDIT_KINDS
               or audit_spec.entity_type not in REGISTRY.AUDIT_ENTITY_TYPES]
        assert not bad, f"specs with an unknown group, kind or entity type: {bad}"

    def test_a_legacy_type_is_a_valid_prefix_value(self):
        bad = sorted({audit_spec.legacy_type for audit_spec in _all_specs()
                      if audit_spec.legacy_type
                      and not REGISTRY.AUDIT_ACTION_NAME_PATTERN.match(audit_spec.legacy_type)})
        assert not bad, f"legacy types that need escaping in the prefix: {bad}"

    def test_the_name_check_detects_a_bad_name(self):
        # Control.
        assert not REGISTRY.AUDIT_ACTION_NAME_PATTERN.match("Asset-Create]")


@pytest.mark.unit
class TestRegistryMatchesHandlers:
    def test_every_typed_audit_literal_in_a_handler_is_registered(self, handler_scan):
        literals, _ = handler_scan
        known = set(REGISTRY.AUDIT_ACTIONS) | {audit_spec.legacy_type for audit_spec in _all_specs()
                                               if audit_spec.legacy_type}
        unknown = sorted(literals - known)
        assert not unknown, ("handlers record action types the registry does not know; register them "
                             f"in common/auditActions.py: {unknown}")

    def test_every_pending_action_is_registered(self):
        unknown = sorted(REGISTRY.AUDIT_PENDING_ACTIONS - set(REGISTRY.AUDIT_ACTIONS))
        assert not unknown, f"pending actions that are not registered: {unknown}"

    def test_every_registered_action_is_recorded_or_pending(self, handler_scan):
        recorded = _recorded_actions(REGISTRY.AUDIT_ACTIONS, *handler_scan)
        missing = sorted(set(REGISTRY.AUDIT_ACTIONS) - recorded - REGISTRY.AUDIT_PENDING_ACTIONS)
        assert not missing, ("registered actions that no handler records; call log_action from the "
                             f"handler or list them in AUDIT_PENDING_ACTIONS: {missing}")

    # Temporary: the pending list exists only while handlers are being covered. Remove this test, and
    # the list, only once AUDIT_PENDING_ACTIONS is empty (the auth-records change empties it); removing
    # it while entries remain would let a stale entry hide an action a handler already records.
    @pytest.mark.temporary  # pins the shrinking AUDIT_PENDING_ACTIONS list; remove when the list is empty
    def test_no_pending_action_is_already_recorded(self, handler_scan):
        recorded = _recorded_actions(REGISTRY.AUDIT_ACTIONS, *handler_scan)
        stale = sorted(REGISTRY.AUDIT_PENDING_ACTIONS & recorded)
        assert not stale, f"actions on AUDIT_PENDING_ACTIONS that a handler already records: {stale}"

    def test_the_scan_sees_a_literal_a_constant_and_a_file_function(self):
        # Control: the scan must find each emitter shape the checks above rely on.
        source = (
            "AUDIT_TYPE = 'controlConstant'\n"
            "def handler(event):\n"
            "    log_actions(event, 'controlLiteral', {})\n"
            "    log_auth_changes(event, AUDIT_TYPE, {})\n"
            "    auditLogging.log_action(event, action='controlKeyword')\n"
            "    log_file_download_bulk(event, 'db', 'a', [])\n"
        )
        literals, functions = _scan_source(source)
        assert literals == {"controlLiteral", "controlConstant", "controlKeyword"}
        assert functions == {"log_file_download"}

    def test_the_recorded_check_detects_an_unrecorded_action(self):
        # Control: an action no handler names is not recorded.
        spec = REGISTRY.spec("controlNeverRecorded", "asset")
        assert _recorded_actions({"controlNeverRecorded": spec}, set(), set()) == set()


@pytest.mark.unit
class TestSystemActions:
    def test_every_named_handler_module_exists(self):
        names = ([system.handler for system in REGISTRY.AUDIT_SYSTEM_ACTIONS] + list(REGISTRY.AUDIT_SYSTEM_EXEMPT)
                 + list(REGISTRY.AUDIT_ENTRYPOINT_EXEMPT))
        missing = [name for name in names
                   if not os.path.isfile(os.path.join(BACKEND, *name.split(".")) + ".py")]
        assert not missing, f"system actions name handler modules that do not exist: {missing}"

    def test_each_system_action_is_recorded_by_its_own_handler(self):
        wrong = []
        for system in REGISTRY.AUDIT_SYSTEM_ACTIONS:
            path = os.path.join(BACKEND, *system.handler.split(".")) + ".py"
            if not os.path.isfile(path) or system.spec.action in REGISTRY.AUDIT_PENDING_ACTIONS:
                continue
            with open(path, encoding="utf-8") as handle:
                literals, functions = _scan_source(handle.read())
            if not _recorded_actions({system.spec.action: system.spec}, literals, functions):
                wrong.append((system.handler, system.spec.action))
        assert not wrong, f"system actions their handler module does not record: {wrong}"


def _entrypoints(tree):
    """The module's top-level Lambda entrypoints: lambda_handler and lambda_handler_<name>."""
    return [node for node in tree.body
            if isinstance(node, ast.FunctionDef)
            and (node.name == "lambda_handler" or node.name.startswith("lambda_handler_"))]


def _opens_audit_context(function):
    for decorator in function.decorator_list:
        target = decorator.func if isinstance(decorator, ast.Call) else decorator
        name = target.id if isinstance(target, ast.Name) else getattr(target, "attr", None)
        if name == "audited_handler":
            return True
    for node in ast.walk(function):
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                call = item.context_expr
                if isinstance(call, ast.Call) and _call_name(call) == "audit_request":
                    return True
    return False


def _module_name(path):
    return os.path.relpath(path, BACKEND)[:-3].replace(os.sep, ".")


@pytest.mark.unit
class TestEntrypointsOpenTheAuditContext:
    def test_every_entrypoint_opens_the_audit_context_or_is_exempt(self):
        missing = []
        for path in _python_files(HANDLERS):
            module = _module_name(path)
            with open(path, encoding="utf-8") as handle:
                tree = ast.parse(handle.read())
            for function in _entrypoints(tree):
                if module in REGISTRY.AUDIT_ENTRYPOINT_EXEMPT:
                    continue
                if not _opens_audit_context(function):
                    missing.append(f"{module}.{function.name}")
        assert not missing, ("Lambda entrypoints that do not open the request audit context; decorate them "
                             f"with @audited_handler or list them in AUDIT_ENTRYPOINT_EXEMPT: {sorted(missing)}")

    def test_every_exemption_has_a_reason_and_an_entrypoint(self):
        for module, reason in REGISTRY.AUDIT_ENTRYPOINT_EXEMPT.items():
            assert isinstance(reason, str) and reason.strip(), module
            with open(os.path.join(BACKEND, *module.split(".")) + ".py", encoding="utf-8") as handle:
                assert _entrypoints(ast.parse(handle.read())), f"{module} defines no entrypoint"

    def test_the_check_detects_each_shape(self):
        # Control: decorated, with-block and attribute forms are seen; a bare entrypoint is not.
        source = (
            "@audited_handler\n"
            "def lambda_handler(event, context):\n    pass\n"
            "def lambda_handler_created(event, context):\n"
            "    with auditLogging.audit_request(event, context):\n        pass\n"
            "@auditLogging.audited_handler\n"
            "def lambda_handler_deleted(event, context):\n    pass\n"
            "def lambda_handler_bare(event, context):\n    pass\n"
            "def helper(event):\n    pass\n"
        )
        functions = _entrypoints(ast.parse(source))
        assert [function.name for function in functions] == [
            "lambda_handler", "lambda_handler_created", "lambda_handler_deleted", "lambda_handler_bare"]
        assert [_opens_audit_context(function) for function in functions] == [True, True, True, False]


@pytest.mark.unit
class TestRouteResolution:
    @pytest.mark.parametrize("path,expected", [
        ("/database/db1/assets/a1", "/database/{databaseId}/assets/{assetId}"),
        ("/auth/constraints/permissionObjects", "/auth/constraints/permissionObjects"),
        ("/auth/constraints/c1", "/auth/constraints/{constraintId}"),
        ("/database/db1/assets/a1/metadata/file", "/database/{databaseId}/assets/{assetId}/metadata/file"),
        ("/database/db1/assets/a1/download/stream/dir/x.glb",
         "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}"),
        ("/not/a/route", None),
    ])
    def test_a_concrete_path_resolves_to_its_template(self, path, expected):
        assert REGISTRY.resolve_route_template(path) == expected

    def test_a_registered_template_hint_is_used(self):
        assert REGISTRY.resolve_route_template("/x", "/database/{databaseId}") == "/database/{databaseId}"

    def test_an_unknown_template_hint_falls_back_to_the_path(self):
        assert REGISTRY.resolve_route_template("/database/db1", "/evil/{x}") == "/database/{databaseId}"

    def test_every_route_resolves_to_itself(self):
        # A sample concrete path of every template must resolve back to that template.
        wrong = []
        for route in ALL_API_ROUTES:
            sample = "/".join("seg" if part.startswith("{") else part for part in route.path.split("/"))
            if REGISTRY.resolve_route_template(sample) != route.path:
                wrong.append((route.path, REGISTRY.resolve_route_template(sample)))
        assert not wrong, f"templates a sample path does not resolve back to: {wrong}"

    def test_the_spec_lookup_uses_template_and_method(self):
        found = REGISTRY.audit_spec_for_route("/database/{databaseId}", "put")
        assert found is not None and found.action == "databaseUpdate"
        assert REGISTRY.audit_spec_for_route("/database/{databaseId}", GET).action == "databaseGet"
        assert REGISTRY.audit_spec_for_route(None, GET) is None
