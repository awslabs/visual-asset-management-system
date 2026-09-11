# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Resource names resolve once per container, not once per request (backend/CLAUDE.md Rule 10).

`auditLogging` resolved its CloudWatch log-group name inside each of its twelve `log_*` functions,
and `common/dynamodb.get_asset_object_from_id` resolved the asset table on every call. The negative
record added to `common/resourceNames` bounds what a genuinely missing parameter costs, but it does
not make either of these a module-level resolution: a correctly configured deployment still paid a
resolver call — and, in `dynamodb`'s case, a fresh `dynamodb.Table(...)` construction — inside the
request path.

The audit module's silent-failure contract is what shapes the fix. A hard `raise` at import would
convert one unpublished SSM parameter into a cold-start 500 on every request, so an unresolved name
is recorded as `None` and retried on the next write. Both halves are asserted here: a resolved name
costs no resolver call, and an unresolved one is retried and then cached.

The structural pass is the durable half. The per-call shape is trivially reintroduced by copying an
existing `log_*` function, and nothing else in the suite would notice — so the pass fails on any
resolver call reached from a request-path function, and carries a positive control proving the
detector fires on that shape.
"""

import ast
import os
from pathlib import Path

import pytest

# get_log_group_name resolves an env-var override before any SSM lookup. Seeded before the module
# import below so the import-time resolution succeeds offline; the tests that exercise the
# unresolved path set the module's own record rather than depending on which names resolved, because
# another test module may already have imported auditLogging with a different subset seeded.
for _name in (
    "AUDIT_LOG_AUTHENTICATION",
    "AUDIT_LOG_AUTHORIZATION",
    "AUDIT_LOG_FILEUPLOAD",
    "AUDIT_LOG_FILEDOWNLOAD",
    "AUDIT_LOG_FILEDOWNLOAD_STREAMED",
    "AUDIT_LOG_AUTHOTHER",
    "AUDIT_LOG_AUTHCHANGES",
    "AUDIT_LOG_ACTIONS",
    "AUDIT_LOG_ERRORS",
):
    os.environ.setdefault(_name, f"test-{_name.lower()}")

from backend.backend.customLogging import auditLogging  # noqa: E402
from backend.backend.common.resourceNames import ResourceKeys  # noqa: E402

BACKEND_SRC = Path(__file__).resolve().parents[2] / "backend"

EVENT = {
    "requestContext": {"http": {"path": "/database/db1/assets/a1/download", "method": "GET"}},
    "headers": {},
}


class _CountingResolver:
    """Stands in for get_log_group_name, counting the resolutions the request path performs."""

    def __init__(self, value="counted-log-group", raises=False):
        self.calls = []
        self.value = value
        self.raises = raises

    def __call__(self, key):
        self.calls.append(key.param_key)
        if self.raises:
            raise KeyError(f"Resource name parameter not found in SSM: {key.param_key}")
        return self.value


@pytest.fixture
def offline_cloudwatch(monkeypatch):
    """A stubbed Logs client, so an audit write makes no AWS call and records what it wrote."""
    writes = []

    class FakeLogs:
        def create_log_stream(self, **kwargs):
            return {}

        def put_log_events(self, **kwargs):
            writes.append(kwargs)
            return {}

    monkeypatch.setattr(auditLogging, "cloudwatch_logs", FakeLogs())
    monkeypatch.setattr(auditLogging, "_created_log_streams", {})
    return writes


@pytest.fixture
def seeded_names(monkeypatch):
    """Every audit log group resolved, as a correctly configured container has them."""
    resolved = {
        key.param_key: f"resolved-{key.param_key.rsplit('/', 1)[-1]}"
        for key in auditLogging._AUDIT_LOG_GROUP_KEYS
    }
    monkeypatch.setattr(auditLogging, "_audit_log_group_names", resolved)
    return resolved


@pytest.mark.unit
class TestAuditLogGroupNamesResolveOncePerContainer:
    def test_the_module_resolved_every_audit_log_group_at_import(self):
        """Nine keys, nine records — the import-time pass covers the whole set."""
        assert len(auditLogging._AUDIT_LOG_GROUP_KEYS) == 9
        assert set(auditLogging._audit_log_group_names) == {
            key.param_key for key in auditLogging._AUDIT_LOG_GROUP_KEYS
        }

    def test_a_resolved_name_costs_no_resolver_call(self, monkeypatch, seeded_names):
        resolver = _CountingResolver()
        monkeypatch.setattr(auditLogging, "get_log_group_name", resolver)

        name = auditLogging._audit_log_group(ResourceKeys.AUDIT_LOG_FILEDOWNLOAD)

        assert name == seeded_names[ResourceKeys.AUDIT_LOG_FILEDOWNLOAD.param_key]
        assert resolver.calls == []

    def test_repeated_audit_writes_make_no_resolver_call(
        self, monkeypatch, seeded_names, offline_cloudwatch
    ):
        resolver = _CountingResolver()
        monkeypatch.setattr(auditLogging, "get_log_group_name", resolver)

        for _ in range(5):
            auditLogging.log_file_download(EVENT, "db1", "a1", "/f.glb")

        # The write really happened — otherwise "zero resolver calls" is vacuous.
        assert len(offline_cloudwatch) == 5
        assert resolver.calls == []

    def test_writes_across_every_event_type_make_no_resolver_call(
        self, monkeypatch, seeded_names, offline_cloudwatch
    ):
        resolver = _CountingResolver()
        monkeypatch.setattr(auditLogging, "get_log_group_name", resolver)

        auditLogging.log_authentication(EVENT, True)
        auditLogging.log_authorization({"tokens": ["u1"], "roles": []}, True)
        auditLogging.log_authorization_api(EVENT, True)
        auditLogging.log_file_upload(EVENT, "db1", "a1", "/f.glb", False)
        auditLogging.log_file_download(EVENT, "db1", "a1", "/f.glb")
        auditLogging.log_file_download_streamed(EVENT, "db1", "a1", "/f.glb")
        auditLogging.log_auth_other(EVENT, "TEST")
        auditLogging.log_auth_changes(EVENT, "TEST")
        auditLogging.log_actions(EVENT, "TEST")
        auditLogging.log_errors(EVENT, "TEST")

        assert len(offline_cloudwatch) == 10
        assert resolver.calls == []


@pytest.mark.unit
class TestUnresolvedAuditLogGroupIsRetriedThenCached:
    def test_a_name_that_failed_at_import_is_retried_once_and_cached(self, monkeypatch):
        monkeypatch.setattr(
            auditLogging,
            "_audit_log_group_names",
            {ResourceKeys.AUDIT_LOG_ACTIONS.param_key: None},
        )
        resolver = _CountingResolver(value="late-actions-group")
        monkeypatch.setattr(auditLogging, "get_log_group_name", resolver)

        assert auditLogging._audit_log_group(ResourceKeys.AUDIT_LOG_ACTIONS) == "late-actions-group"
        assert len(resolver.calls) == 1
        # Cached: the retry does not become a per-call resolution.
        assert auditLogging._audit_log_group(ResourceKeys.AUDIT_LOG_ACTIONS) == "late-actions-group"
        assert len(resolver.calls) == 1

    def test_an_unresolvable_name_does_not_raise_and_drops_the_write(
        self, monkeypatch, offline_cloudwatch
    ):
        """The silent-failure contract: a missing parameter must not fail the caller's request."""
        monkeypatch.setattr(
            auditLogging,
            "_audit_log_group_names",
            {ResourceKeys.AUDIT_LOG_AUTHENTICATION.param_key: None},
        )
        resolver = _CountingResolver(raises=True)
        monkeypatch.setattr(auditLogging, "get_log_group_name", resolver)

        auditLogging.log_authentication(EVENT, True)  # must not raise

        assert offline_cloudwatch == []
        assert len(resolver.calls) == 1


@pytest.mark.unit
class TestDynamodbAssetTableIsBuiltOncePerContainer:
    def test_the_module_exposes_a_memoized_table_accessor(self):
        from backend.backend.common import dynamodb as dynamodb_module

        assert hasattr(dynamodb_module, "_asset_table_resource")
        assert hasattr(dynamodb_module, "_asset_table")

    def test_the_accessor_builds_the_table_once(self, monkeypatch):
        from backend.backend.common import dynamodb as dynamodb_module

        built = []

        class FakeResource:
            def Table(self, name):
                built.append(name)
                return f"table::{name}"

        monkeypatch.setattr(dynamodb_module, "dynamodb", FakeResource())
        monkeypatch.setattr(dynamodb_module, "get_table_name", lambda key: "assetStorageTable")
        monkeypatch.setattr(dynamodb_module, "_asset_table", None)

        assert dynamodb_module._asset_table_resource() == "table::assetStorageTable"
        assert dynamodb_module._asset_table_resource() == "table::assetStorageTable"
        assert built == ["assetStorageTable"]


# ---------------------------------------------------------------------------------------------
# Structural pass: no resource-name resolution reachable from a request-path function
# ---------------------------------------------------------------------------------------------

RESOLVER_NAMES = frozenset({"get_table_name", "get_bucket_name", "get_log_group_name",
                            "get_resource_name"})

# The one function in each module whose whole job is to resolve a name. Everything else must read a
# value that was resolved at import.
RESOLVER_EXEMPT_FUNCTIONS = {
    "customLogging/auditLogging.py": {"_resolve_audit_log_group"},
    "common/dynamodb.py": {"_asset_table_resource"},
}


def _resolver_calls_inside_functions(source: str, exempt: set) -> list:
    """Return `(function, line, resolver)` for each resolver call inside a non-exempt function."""
    tree = ast.parse(source)
    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name in exempt:
            continue
        for inner in ast.walk(node):
            if not isinstance(inner, ast.Call):
                continue
            func = inner.func
            name = func.id if isinstance(func, ast.Name) else getattr(func, "attr", None)
            if name in RESOLVER_NAMES:
                offenders.append((node.name, inner.lineno, name))
    return offenders


PER_CALL_SNIPPET = """
def log_something(event):
    log_group_name = get_log_group_name(ResourceKeys.AUDIT_LOG_ACTIONS)
    return log_group_name
"""

MODULE_LEVEL_SNIPPET = """
name = get_log_group_name(ResourceKeys.AUDIT_LOG_ACTIONS)


def log_something(event):
    return name
"""


@pytest.mark.unit
class TestNoResolverCallInTheRequestPath:
    def test_detector_flags_a_per_call_resolution(self):
        """Positive control: the shape this pass forbids must actually be detected."""
        assert _resolver_calls_inside_functions(PER_CALL_SNIPPET, set()) == [
            ("log_something", 3, "get_log_group_name")
        ]

    def test_detector_accepts_a_module_level_resolution(self):
        assert _resolver_calls_inside_functions(MODULE_LEVEL_SNIPPET, set()) == []

    @pytest.mark.parametrize("relative_path", sorted(RESOLVER_EXEMPT_FUNCTIONS))
    def test_module_resolves_names_at_import_only(self, relative_path):
        path = BACKEND_SRC / relative_path
        assert path.is_file(), f"{path} does not exist"
        source = path.read_text(encoding="utf-8")
        # Control: the module really does resolve names, so an empty offender list means the calls
        # are at module level rather than that the module never resolves anything.
        assert any(name in source for name in RESOLVER_NAMES)
        offenders = _resolver_calls_inside_functions(
            source, RESOLVER_EXEMPT_FUNCTIONS[relative_path]
        )
        assert offenders == [], (
            f"{relative_path} resolves resource names inside the request path "
            "(backend/CLAUDE.md Rule 10): "
            + "; ".join(f"{fn}:{line} -> {resolver}" for fn, line, resolver in offenders)
        )
