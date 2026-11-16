# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The text a caller typed into a search or a text filter is never recorded in an audit record in clear.

A search or read action records what was asked and never what was returned. Free text is neither an id
nor an option, so it is recorded under the one ``searchText`` key (``common/auditSearchText.py``) that
the log redaction writes as ``<redacted>``. These tests hold three things:

* the helper moves the text under ``searchText`` and the redaction writes it as ``<redacted>``;
* ``searchText`` is in ``CONTENT_KEYS``, and generic names such as ``query`` or ``value`` are not;
* a registered read or search action that records a free-text field name in ``request_parameters``
  outside the helper fails, and so does a registered search action whose module never calls the helper.

The scan reads handler source with ``ast``; every check has a control proving it can fail.
"""

import ast
import importlib
import json
import os

import pytest

from backend.backend.customLogging import logger as real_logger

auditSearchText = importlib.import_module("common.auditSearchText")
REGISTRY = importlib.import_module("common.auditActions")

BACKEND = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend"))
HANDLERS = os.path.join(BACKEND, "handlers")

# Request field names that carry text a caller typed. Matched case-insensitively on string constants.
FREE_TEXT_NAMES = frozenset({
    "text", "query", "q", "search", "searchterm", "term", "keyword", "keywords", "filterpattern",
    "metadataquery", "metadatakey", "metadatavalue", "assetname", "filekey", "tokens", "filters",
    "query_string",
})
# The calls whose arguments are recorded through the redacted key: constants inside them are fine.
_REDIRECTING_CALLS = frozenset({"search_text_parameters", "request_parameters",
                                "_search_request_parameters", "_execution_read_parameters"})
_AUDIT_CALLS = frozenset({"log_action", "log_actions"})


def _call_name(node):
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _free_text_constants(node):
    """The free-text names a ``request_parameters`` expression spells out, skipping the arguments of a
    call that records them through the redacted key."""
    found = []
    if isinstance(node, ast.Call) and _call_name(node) in _REDIRECTING_CALLS:
        return found
    if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.lower() in FREE_TEXT_NAMES:
        found.append(node.value)
    for child in ast.iter_child_nodes(node):
        found.extend(_free_text_constants(child))
    return found


def _scan_source(source):
    """(violations, literal actions recorded, whether the helper is called) for one module's source."""
    tree = ast.parse(source)
    violations, actions = [], set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _call_name(node) not in _AUDIT_CALLS:
            continue
        action = node.args[1] if len(node.args) > 1 else None
        for keyword in node.keywords:
            if keyword.arg == "action":
                action = keyword.value
        action_name = action.value if isinstance(action, ast.Constant) and isinstance(action.value, str) else None
        if action_name:
            actions.add(action_name)
        for keyword in node.keywords:
            if keyword.arg == "request_parameters":
                for name in _free_text_constants(keyword.value):
                    violations.append((action_name, name))
    uses_helper = any(isinstance(node, ast.Call) and _call_name(node) == "search_text_parameters"
                      for node in ast.walk(tree))
    return violations, actions, uses_helper


def _handler_files():
    for directory, _, files in os.walk(HANDLERS):
        for name in files:
            if name.endswith(".py"):
                yield os.path.join(directory, name)


def _kind(action):
    spec = REGISTRY.AUDIT_ACTIONS.get(action)
    return spec.kind if spec else None


@pytest.fixture(scope="module")
def scanned():
    scanned_files = {}
    for path in _handler_files():
        with open(path, encoding="utf-8") as handle:
            scanned_files[path] = _scan_source(handle.read())
    return scanned_files


@pytest.mark.unit
class TestHelper:
    def test_text_moves_under_the_search_text_key(self):
        recorded = auditSearchText.search_text_parameters(
            {"databaseId": "db1", "size": 10}, {"query": "abc", "tokens": ["de", "f"], "filters": [{"query": "gh"}]})
        assert recorded == {"databaseId": "db1", "size": 10,
                            "searchText": {"query": "abc", "tokens": ["de", "f"], "filters": [{"query": "gh"}]},
                            "searchTextFields": ["query", "tokens", "filters"], "searchTextLength": 8}

    def test_empty_text_adds_nothing(self):
        parameters = {"databaseId": "db1"}
        assert auditSearchText.search_text_parameters(parameters, {"query": "", "tokens": [], "x": None}) == parameters
        assert auditSearchText.search_text_parameters(None, None) == {}

    def test_the_input_is_not_changed(self):
        parameters = {"databaseId": "db1"}
        auditSearchText.search_text_parameters(parameters, {"query": "abc"})
        assert parameters == {"databaseId": "db1"}


@pytest.mark.unit
class TestRedaction:
    def test_the_key_is_redacted_and_generic_names_are_not(self):
        assert auditSearchText.SEARCH_TEXT_KEY in real_logger.CONTENT_KEYS
        recorded = auditSearchText.search_text_parameters({"query": "kept", "value": "kept"}, {"query": "typed-marker"})
        masked = real_logger.mask_sensitive_data(recorded)
        assert masked["searchText"] == real_logger.REDACTED
        assert "typed-marker" not in json.dumps(masked)
        assert masked["query"] == "kept" and masked["value"] == "kept", "generic names stay in other records"
        assert masked["searchTextFields"] == ["query"] and masked["searchTextLength"] == len("typed-marker")

    def test_the_key_is_redacted_inside_a_nested_section(self):
        masked = real_logger.mask_sensitive_data({"requestParameters": {"searchText": {"query": "typed-marker"}}})
        assert "typed-marker" not in json.dumps(masked)


@pytest.mark.unit
class TestRegisteredActions:
    def test_no_read_or_search_action_records_a_free_text_field_outside_the_redacted_key(self, scanned):
        offenders = sorted(
            f"{os.path.relpath(path, BACKEND)}: {action} records {name!r}"
            for path, (violations, _, _) in scanned.items()
            for action, name in violations if _kind(action) in ("read", "search") or action is None)
        assert not offenders, ("free text recorded in request_parameters outside the searchText key; build the "
                               f"parameters with common.auditSearchText.search_text_parameters: {offenders}")

    def test_every_registered_search_action_is_recorded_through_the_helper(self, scanned):
        search_actions = {name for name, spec in REGISTRY.AUDIT_ACTIONS.items() if spec.kind == "search"}
        assert search_actions, "the registry has no search action to check"
        missing = sorted(
            f"{os.path.relpath(path, BACKEND)}: {sorted(search_actions & actions)}"
            for path, (_, actions, uses_helper) in scanned.items()
            if search_actions & actions and not uses_helper)
        assert not missing, f"search actions recorded without search_text_parameters: {missing}"

    def test_the_scan_sees_the_search_handler_and_the_execution_log_read(self, scanned):
        # Control: the checks above are only as good as the scan finding the call sites they name.
        by_name = {os.path.basename(path): result for path, result in scanned.items()}
        assert "search" in by_name["search.py"][1] and by_name["search.py"][2]
        assert by_name["executionService.py"][2]


@pytest.mark.unit
class TestScanControls:
    def test_a_free_text_key_in_a_dict_is_flagged(self):
        source = 'log_action(event, "search", request_parameters={"query": q, "size": 5})'
        violations, actions, uses_helper = _scan_source(source)
        assert violations == [("search", "query")] and actions == {"search"} and not uses_helper

    def test_a_free_text_key_read_from_the_query_is_flagged(self):
        source = 'log_action(event, "assetList", request_parameters=dict(x=params.get("text")))'
        assert _scan_source(source)[0] == [("assetList", "text")]

    def test_the_helper_is_not_flagged(self):
        source = ('log_action(event, "search", request_parameters=search_text_parameters({"size": 5}, '
                  '{"query": q}))')
        violations, _, uses_helper = _scan_source(source)
        assert violations == [] and uses_helper

    def test_identifier_and_paging_keys_are_not_flagged(self):
        source = 'log_action(event, "assetList", request_parameters={"databaseId": d, "maxItems": 5})'
        assert _scan_source(source)[0] == []
