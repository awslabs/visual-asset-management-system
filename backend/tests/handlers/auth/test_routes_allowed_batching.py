# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bulk route checks: batched denial audit records, and the probe-path vocabulary.

Two behaviours of ``handlers/auth/routes.py`` are pinned here, both exercised through the
real ``CasbinEnforcer`` and the real audit writer with a stubbed CloudWatch client.

**Batched denial records (FIX-040).** ``GET /auth/routes/api/allowed`` and
``POST /auth/routes`` feed every route/method pair through Casbin. ``CasbinEnforcer.enforce``
writes one authorization audit record per denial, each a separate synchronous
``create_log_stream`` + ``put_log_events`` round trip, so a read-only role produced 100+
sequential CloudWatch calls inside one request. The records are now collected and written in
batches. Audit completeness is the contract -- every per-denial record is kept -- and that is
exactly what a latency-focused fix silently breaks, so every assertion below counts the
EVENTS that reached the stubbed client, in band, alongside the call count. A collector that
is never flushed loses its records with no error, which is indistinguishable from "the user
was allowed everything". The PutLogEvents byte budget is exercised here on the batch object
directly, and on the writer itself in ``tests/common/test_auditLogging_batch_limits.py``.

**Probe-path vocabulary (FIX-041).** The listing used to evaluate Casbin against the route
TEMPLATE (``/database/{databaseId}/assets``) while ``enforceAPI`` evaluates the CONCRETE
request path (``/database/db1/assets``), so a constraint could be reported allowed and then
denied on every real call. The listing now probes a concrete instantiation of the template,
making the concrete request path the single authoritative vocabulary. The tests assert the
listing's verdict EQUALS ``enforceAPI``'s verdict, per constraint shape -- not merely that
the listing returns something -- and that a constraint value matching no VAMS route is
neither rejected nor dropped.

authProvider axis: the denial count is a function of the caller's roles, so a role that
allows everything produces zero denials and a test run that way passes vacuously. The
read-only role below carries the api/web constraint shapes of the shipped templates, and the
allow-everything role is used only as the negative control that proves the count assertions
are load-bearing.
"""

import contextlib
import json
import os
import types

import pytest
from unittest.mock import MagicMock, patch

# get_log_group_name resolves an env-var override before any SSM lookup, so seeding the audit
# log-group name keeps the writer offline. Set before the imports below, which bind the
# resolver at import time.
os.environ.setdefault("AUDIT_LOG_AUTHORIZATION", "test-auditAuthorization")

from common.apiRoutes import get_public_api_routes  # noqa: E402
import backend.backend.handlers.authz as authz_module  # noqa: E402
from backend.backend.handlers.auth import routes as routes_module  # noqa: E402
from backend.backend.customLogging import auditLogging as real_audit  # noqa: E402
from backend.backend.customLogging.logger import mask_sensitive_data  # noqa: E402

USER = "probe-user"
ROLE = "probe-role"
_CLAIMS = {"tokens": [USER], "roles": [ROLE], "externalAttributes": [], "mfaEnabled": True}

# The real PutLogEvents contract.
MAX_BATCH_BYTES = 1_048_576
MAX_BATCH_EVENTS = 10_000
EVENT_OVERHEAD_BYTES = 26

ALL_METHODS = ("GET", "POST", "PUT", "DELETE", "HEAD")

# The api-route prefixes the read-only role grants GET on, in the shape the shipped
# permission templates use (static prefixes, no template syntax).
_READONLY_GET_PREFIXES = (
    "/secure-config", "/amplify-config", "/auth/routes", "/asset-links", "/assets", "/comments",
    "/database", "/metadata", "/metadataschema", "/search", "/tags",
)
_READONLY_WEB_PREFIXES = ("/assets", "/databases")


class _Rejected(Exception):
    """Stands in for the InvalidParameterException an oversized PutLogEvents batch raises."""


class _ResourceAlreadyExists(Exception):
    pass


class _CloudWatchRecorder:
    """A logs client that records accepted batches and refuses what CloudWatch would refuse."""

    def __init__(self):
        self.accepted_batches = []
        self.rejections = []

        class _Exceptions:
            ResourceAlreadyExistsException = _ResourceAlreadyExists

        self.exceptions = _Exceptions()

    def create_log_stream(self, **kwargs):
        return {}

    def put_log_events(self, **kwargs):
        events = kwargs["logEvents"]
        if not events:
            self.rejections.append("empty batch")
            raise _Rejected("logEvents must not be empty")
        if len(events) > MAX_BATCH_EVENTS:
            self.rejections.append(f"{len(events)} events")
            raise _Rejected("too many events in one batch")
        total = sum(len(e["message"].encode("utf-8")) + EVENT_OVERHEAD_BYTES for e in events)
        if total > MAX_BATCH_BYTES:
            self.rejections.append(f"{total} bytes")
            raise _Rejected("batch exceeds the 1 MiB PutLogEvents limit")
        self.accepted_batches.append(events)
        return {}

    @property
    def call_count(self):
        return len(self.accepted_batches) + len(self.rejections)

    @property
    def accepted_events(self):
        return [event for batch in self.accepted_batches for event in batch]


def _constraint(constraint_id, object_type, criteria_or, methods,
                permission_type="allow", criteria_and=None):
    return {
        "constraintId": constraint_id,
        "objectType": object_type,
        "criteriaAnd": list(criteria_and or []),
        "criteriaOr": list(criteria_or or []),
        "groupPermissions": [
            {"groupId": ROLE, "permission": method, "permissionType": permission_type}
            for method in methods
        ],
        "userPermissions": [],
    }


def _route_path_criteria(operator, *values):
    return [{"field": "route__path", "operator": operator, "value": value} for value in values]


def _readonly_constraints():
    return [
        _constraint("ro-api-get", "api",
                    _route_path_criteria("starts_with", *_READONLY_GET_PREFIXES), ("GET",)),
        _constraint("ro-api-user-keys", "api",
                    _route_path_criteria("starts_with", "/auth/user/api-keys"),
                    ("GET", "POST", "PUT", "DELETE")),
        _constraint("ro-web", "web",
                    _route_path_criteria("starts_with", *_READONLY_WEB_PREFIXES), ("GET",)),
    ]


def _allow_everything_constraints():
    """Every method on every path -- the negative control's policy."""
    return [
        _constraint("allow-all-api", "api", _route_path_criteria("starts_with", "/"), ALL_METHODS),
        _constraint("allow-all-web", "web", _route_path_criteria("starts_with", "/"), ALL_METHODS),
    ]


def _admin_constraints():
    """Every method on every path through a criteriaAnd wildcard -- the second control policy.

    ``ALL_METHODS`` is every method the master route registry uses, so an allow-everything
    policy really does produce zero denials (HEAD appears on the file-existence routes).
    """
    return [
        _constraint("admin-api-all", "api", [], ALL_METHODS,
                    criteria_and=_route_path_criteria("contains", ".*")),
    ]


def _make_event(method="GET", path="/auth/routes/api/allowed", body=None):
    event = {
        "requestContext": {
            "http": {"method": method, "path": path},
            "authorizer": {"jwt": {"claims": {
                "vams:tokens": json.dumps([USER]),
                "vams:roles": json.dumps([ROLE]),
            }}},
        },
        "headers": {"authorization": "Bearer test-token"},
    }
    if body is not None:
        event["body"] = json.dumps(body)
    return event


@pytest.fixture
def probe_env():
    """Wire the REAL CasbinEnforcer and the REAL audit writer into the routes handler.

    ``routes_module.auditLogging`` is patched in place rather than by module name, so the
    batch writer is installed on exactly the module object the handler calls through --
    whether that is the suite's no-op audit stand-in or the real module. Everything is
    restored afterwards so no other test module gains a live CloudWatch writer.
    """
    recorder = _CloudWatchRecorder()
    api_audit_spy = MagicMock()
    state = {"recorder": recorder, "api_audit": api_audit_spy,
             "constraints": _readonly_constraints()}

    previous_policy_map = authz_module.casbin_user_policy_map
    previous_enforcer_map = authz_module.casbin_user_enforcer_map
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}

    def set_constraints(constraints):
        """Install a policy, discarding the per-user enforcer/policy caches it replaces."""
        state["constraints"] = constraints
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}

    state["set_constraints"] = set_constraints

    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.object(
            routes_module.auditLogging, "_write_batch_to_cloudwatch",
            real_audit._write_batch_to_cloudwatch, create=True))
        stack.enter_context(patch.object(real_audit, "cloudwatch_logs", recorder))
        stack.enter_context(patch.object(real_audit, "mask_sensitive_data", mask_sensitive_data))
        # The Tier-2 audit write of an ordinary enforce() must reach the recorder; the Tier-1
        # write is spied on so it can be counted separately from the denial batch.
        stack.enter_context(patch.object(
            authz_module, "log_authorization", real_audit.log_authorization))
        stack.enter_context(patch.object(authz_module, "log_authorization_api", api_audit_spy))
        stack.enter_context(patch.object(
            authz_module, "request_to_claims", lambda event: dict(_CLAIMS)))
        stack.enter_context(patch.object(
            authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
            return_value=[{"userId": USER, "roleName": ROLE}]))
        stack.enter_context(patch.object(
            authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
            side_effect=lambda role_names: state["constraints"]))
        stack.enter_context(patch.object(
            routes_module, "CasbinEnforcer", authz_module.CasbinEnforcer))
        stack.enter_context(patch.object(
            routes_module, "request_to_claims", return_value=dict(_CLAIMS)))
        try:
            yield state
        finally:
            authz_module.casbin_user_policy_map = previous_policy_map
            authz_module.casbin_user_enforcer_map = previous_enforcer_map


def _payload(response):
    body = json.loads(response["body"])
    return body["message"] if "message" in body else body


def _allowed_methods_by_path(payload):
    allowed = {}
    for route in payload["routes"]:
        allowed.setdefault(route["path"], set()).update(route["methods"])
    return allowed


def _expected_denials(payload):
    """Denied route/method pairs, derived from the route registry and the RESPONSE.

    Independent of the audit path: the registry gives every authenticated pair, the response
    gives the allowed subset, and the difference is what must have been denied.
    """
    public = list(get_public_api_routes())
    authenticated_paths = {route.path for route in public if not route.unauthenticated}
    total_pairs = sum(len(route.methods) for route in public if not route.unauthenticated)
    allowed_pairs = sum(len(route["methods"]) for route in payload["routes"]
                        if route["path"] in authenticated_paths)
    return total_pairs - allowed_pairs


@pytest.mark.unit
class TestAllowedApiRoutesDenialAudit:
    """GET /auth/routes/api/allowed -- one batch, one event per denial."""

    def test_every_denial_is_written_once_in_a_single_batch(self, probe_env):
        response = routes_module.lambda_handler(_make_event(), {})
        assert response["statusCode"] == 200
        payload = _payload(response)

        # THE PERMITTED HALF: the read-only role still gets its allowed routes back, so the
        # denial count below is a real mixture rather than "everything denied".
        allowed = _allowed_methods_by_path(payload)
        assert allowed.get("/database") == {"GET"}
        assert "GET" in allowed.get("/assets", set())
        # The response carries each route's methods as a list, and the self-service API-key
        # route is granted every method it offers.
        response_methods = {route["path"]: route["methods"] for route in payload["routes"]}
        registry = {route.path: set(route.methods) for route in get_public_api_routes()}
        assert response_methods.get("/database") == ["GET"], response_methods.get("/database")
        assert set(response_methods.get("/auth/user/api-keys", [])) == registry["/auth/user/api-keys"]

        expected = _expected_denials(payload)
        # Sanity bound: the scenario must produce many denials or the assertions below hold
        # trivially.
        assert expected > 50, f"only {expected} denials -- the read-only policy is too permissive"

        # AUDIT COMPLETENESS: one record per denial, none dropped.
        recorder = probe_env["recorder"]
        assert len(recorder.accepted_events) == expected, (
            f"{len(recorder.accepted_events)} of {expected} denial records reached CloudWatch; "
            f"rejections: {recorder.rejections}")
        # THE BATCHING: they arrive in ONE call rather than one call per denial.
        assert recorder.call_count == 1, (
            f"{recorder.call_count} put_log_events calls for {expected} denials")
        # The records are the authorization denials, not some other event type.
        first = recorder.accepted_events[0]["message"]
        assert first.startswith("[AUTHORIZATION][authorized: False]")
        assert f"[user: {USER}]" in first
        assert "--- [event:" in first

    def test_an_all_allowing_role_produces_no_denials_and_no_audit_writes(self, probe_env):
        """NEGATIVE CONTROL. Same endpoint and wiring, a policy that allows everything: zero
        denials and zero writes. This is what proves the counts above track the denials rather
        than something constant, and why the primary test is not run with this policy."""
        probe_env["set_constraints"](_allow_everything_constraints())
        response = routes_module.lambda_handler(_make_event(), {})
        payload = _payload(response)

        assert _expected_denials(payload) == 0
        assert probe_env["recorder"].call_count == 0
        assert probe_env["recorder"].accepted_events == []
        # The permitted half: an allow-everything policy really did return every method.
        assert _allowed_methods_by_path(payload)["/database"] == {"GET", "POST"}

    def test_an_admin_role_produces_no_denials_and_no_audit_writes(self, probe_env):
        """NEGATIVE CONTROL, second policy shape. An allow-everything policy written as a
        criteriaAnd wildcard rather than a criteriaOr prefix: zero denials and zero writes."""
        probe_env["set_constraints"](_admin_constraints())
        response = routes_module.lambda_handler(_make_event(), {})
        payload = _payload(response)

        assert _expected_denials(payload) == 0
        assert probe_env["recorder"].call_count == 0
        assert probe_env["recorder"].accepted_events == []
        # The permitted half: an allow-everything policy really did return every method.
        allowed = {route["path"]: route["methods"] for route in payload["routes"]}
        assert set(allowed["/database"]) >= {"GET", "POST"}

    def test_the_tier_one_api_authorization_record_is_still_emitted(self, probe_env):
        """The Tier-1 ``enforceAPI`` write happens on allow AND deny and must not be folded
        into the denial batch, or the per-request API audit record disappears."""
        response = routes_module.lambda_handler(_make_event(), {})
        assert response["statusCode"] == 200
        assert probe_env["api_audit"].call_count == 1
        event_arg, authorized, custom_data = probe_env["api_audit"].call_args[0]
        assert authorized is True
        assert custom_data["obj"]["route__path"] == "/auth/routes/api/allowed"
        assert custom_data["action"] == "GET"


@pytest.mark.unit
class TestWebRouteDenialAudit:
    """POST /auth/routes -- the same loop, and the exit paths a collector must flush on."""

    def test_denied_web_routes_are_written_once_in_a_single_batch(self, probe_env):
        submitted = [{"method": "GET", "route__path": path} for path in
                     ("/admin", "/settings", "/roles", "/users", "/constraints", "/assets")]
        response = routes_module.lambda_handler(
            _make_event(method="POST", path="/auth/routes", body={"routes": submitted}), {})
        assert response["statusCode"] == 200
        payload = _payload(response)
        allowed_paths = {route["route__path"] for route in payload["allowedRoutes"]}
        # The permitted half.
        assert allowed_paths == {"/assets"}

        expected = len(submitted) - len(allowed_paths)
        recorder = probe_env["recorder"]
        assert len(recorder.accepted_events) == expected, (
            f"{len(recorder.accepted_events)} of {expected} denial records reached CloudWatch; "
            f"rejections: {recorder.rejections}")
        assert recorder.call_count == 1

    def test_denials_collected_before_an_error_are_still_written(self, probe_env):
        """A collector loses its contents on any exit path that does not flush, with no error
        -- indistinguishable from "the user was allowed everything"."""
        submitted = [{"method": "GET", "route__path": path}
                     for path in ("/admin", "/settings", "/assets")]
        with patch.object(routes_module, "AllowedWebRouteModel", side_effect=Exception("boom")):
            response = routes_module.lambda_handler(
                _make_event(method="POST", path="/auth/routes", body={"routes": submitted}), {})

        assert response["statusCode"] == 500
        recorder = probe_env["recorder"]
        assert len(recorder.accepted_events) == 2, (
            "the denials collected before the failure were lost; "
            f"rejections: {recorder.rejections}")
        assert recorder.call_count == 1

    def test_a_high_denial_count_arrives_complete_and_unrejected(self, probe_env):
        """A denial count at endpoint scale reaches CloudWatch with nothing dropped.

        A denial record carries the request object plus the collector's own synthesized audit
        event -- the caller's request event is not echoed into it -- so 400 denied routes is on
        the order of 100 KB, inside one PutLogEvents batch. ``MAX_WEB_ROUTES_PER_REQUEST``
        (500) and the 512-character ``route__path`` limit bound the endpoint's worst case under
        the 1,048,576-byte budget as well, so what this pins is completeness at volume; the
        byte chunking itself is exercised on the batch object directly in
        ``TestDenialBatchLimits``.
        """
        submitted = [{"method": "GET", "route__path": f"/denied-page-{i}"} for i in range(400)]
        event = _make_event(method="POST", path="/auth/routes", body={"routes": submitted})

        response = routes_module.lambda_handler(event, {})
        assert response["statusCode"] == 200
        recorder = probe_env["recorder"]
        assert len(recorder.accepted_events) == len(submitted), (
            f"{len(recorder.accepted_events)} of {len(submitted)} denial records reached "
            f"CloudWatch; rejections: {recorder.rejections}")
        assert recorder.rejections == []


@pytest.mark.unit
class TestDenialBatchLimits:
    """The batch limits, which only became reachable once the denials were folded together."""

    def test_an_over_budget_batch_is_chunked_and_nothing_is_dropped(self, probe_env):
        """``_write_batch_to_cloudwatch`` chunks by event COUNT only, while PutLogEvents also
        caps a batch at 1,048,576 bytes and rejects the whole batch past it -- into a silent
        ``except``, losing every record for the request. Enough denials to exceed that budget
        must therefore arrive as more than one call with no record lost."""
        deny_all = types.SimpleNamespace(
            service_object=types.SimpleNamespace(enforce=lambda obj, act: False))
        batch = routes_module._DenialAuditBatch(dict(_CLAIMS), deny_all)

        # 507 characters plus a 5-digit index is 512, the longest route__path the web-route
        # model accepts, so this is the largest denial record the endpoint can produce.
        path_prefix = "/" + ("d" * 506)
        denial_count = 2000
        for index in range(denial_count):
            assert batch.enforce(
                {"method": "GET", "route__path": f"{path_prefix}{index:05d}",
                 "object__type": "web"}, "GET") is False
        batch.flush()

        recorder = probe_env["recorder"]
        assert recorder.rejections == [], "an over-budget batch was sent to CloudWatch"
        assert recorder.call_count > 1, "the over-budget batch was not split"
        assert len(recorder.accepted_events) == denial_count, (
            f"{len(recorder.accepted_events)} of {denial_count} denial records survived chunking")
        # The synthesized load really does exceed one batch, or the split above proves nothing.
        total_bytes = sum(len(event["message"].encode("utf-8")) + EVENT_OVERHEAD_BYTES
                          for event in recorder.accepted_events)
        assert total_bytes > MAX_BATCH_BYTES, f"only {total_bytes} bytes -- budget never reached"

    def test_the_batch_budget_matches_the_putlogevents_contract(self):
        """The chunking constants are the real API limits, not a guess."""
        assert routes_module.AUDIT_BATCH_MAX_BYTES == MAX_BATCH_BYTES
        assert routes_module.AUDIT_BATCH_MAX_EVENTS == MAX_BATCH_EVENTS
        assert routes_module.AUDIT_EVENT_OVERHEAD_BYTES == EVENT_OVERHEAD_BYTES


@pytest.mark.unit
class TestDenialBatchEnforcerCoupling:
    """COUPLING GUARD. ``_DenialAuditBatch.enforce`` runs its check as
    ``CasbinEnforcer.service_object.enforce``, which is what keeps the enforcer's own per-denial
    audit write from firing so the records can be kept and written together.
    ``handlers/auth/routes.py`` is the only place outside ``handlers/authz`` that reaches for
    that attribute, so a rename or a change to the enforcer's composition would break the
    batching with no import error and no failure anywhere near the cause."""

    def test_casbin_enforcer_exposes_a_service_object_that_enforces(self, probe_env):
        enforcer = authz_module.CasbinEnforcer(dict(_CLAIMS))

        service_object = getattr(enforcer, "service_object", None)
        assert service_object is not None, (
            "CasbinEnforcer no longer exposes service_object; _DenialAuditBatch.enforce in "
            "handlers/auth/routes.py calls it directly")
        service_enforce = getattr(service_object, "enforce", None)
        assert callable(service_enforce), (
            "CasbinEnforcer.service_object no longer offers a callable enforce(obj, act); "
            "_DenialAuditBatch.enforce in handlers/auth/routes.py calls it directly")

        # The attribute is the working enforcer rather than a placeholder: the read-only policy
        # denies this asset check and the verdict comes back through it...
        asset = {"object__type": "asset", "databaseId": "db1", "assetName": "Turbine"}
        assert service_enforce(asset, "PUT") is False
        # ...and reaching it directly writes nothing, which is the property the batch relies on.
        assert probe_env["recorder"].call_count == 0

        # The batch drives that same attribute, and holds the denial until it is flushed.
        batch = routes_module._DenialAuditBatch(dict(_CLAIMS), enforcer)
        assert batch.enforce(asset, "PUT") is False
        assert probe_env["recorder"].call_count == 0
        batch.flush()
        assert probe_env["recorder"].call_count == 1
        # One event per denial, which is the audit-completeness contract at its smallest size.
        assert len(probe_env["recorder"].accepted_events) == 1

        # POSITIVE CONTROL for the two zeroes above: the public wrapper on the same enforcer,
        # given the same check, does write -- so those zeroes are the bypass and not a dead
        # fixture.
        assert enforcer.enforce(asset, "PUT") is False
        assert probe_env["recorder"].call_count == 2
        assert len(probe_env["recorder"].accepted_events) == 2


@pytest.mark.unit
class TestOrdinaryTierTwoDenialUnchanged:
    """OVER-TIGHTENING CATCHER. ``CasbinEnforcer.enforce`` is the shared Tier-2 audit write
    for every handler in the codebase, so an ordinary single denial must keep producing
    exactly one write with one event."""

    def test_a_single_tier_two_denial_still_writes_one_event(self, probe_env):
        enforcer = authz_module.CasbinEnforcer(dict(_CLAIMS))
        asset = {"object__type": "asset", "databaseId": "db1", "assetName": "Turbine"}

        assert enforcer.enforce(asset, "PUT") is False
        recorder = probe_env["recorder"]
        assert recorder.call_count == 1
        assert len(recorder.accepted_events) == 1
        message = recorder.accepted_events[0]["message"]
        assert message.startswith("[AUTHORIZATION][authorized: False]")
        assert "--- [event:" in message
        assert "Turbine" in message

    def test_an_allowed_tier_two_check_writes_nothing(self, probe_env):
        """The permitted half: only denials are audited by enforce(), by design."""
        probe_env["set_constraints"]([_constraint(
            "allow-assets", "asset",
            [{"field": "databaseId", "operator": "equals", "value": "db1"}], ("PUT",))])
        enforcer = authz_module.CasbinEnforcer(dict(_CLAIMS))
        assert enforcer.enforce(
            {"object__type": "asset", "databaseId": "db1", "assetName": "Turbine"}, "PUT") is True
        assert probe_env["recorder"].call_count == 0


# ---------------------------------------------------------------------------
# Probe-path vocabulary
# ---------------------------------------------------------------------------

def _concrete_path(path_template, value="realid"):
    """The concrete request path a caller reaches a route template through."""
    if "{" not in path_template:
        return path_template
    return "/".join(
        value if segment.startswith("{") and segment.endswith("}") else segment
        for segment in path_template.split("/")
    )


# Routes the enforcement side is checked on: parameter-free, single-parameter,
# multi-parameter, greedy-proxy, and the executions/logs route the shipped read-only
# template denies.
_ENFORCED_SAMPLE_PATHS = (
    "/database",
    "/database/{databaseId}",
    "/database/{databaseId}/assets",
    "/database/{databaseId}/assets/{assetId}",
    "/database/{databaseId}/assets/{assetId}/download/stream/{proxy+}",
    "/assets",
    "/auth/routes/api",
    "/workflows/executions",
    "/workflows/executions/{executionId}/details",
    "/workflows/executions/{executionId}/logs",
    "/tags",
    "/tags/{tagId}",
)

# Tier-1 access to the listing endpoint itself, present in every case below.
_BASE_LISTING_GRANT = _constraint(
    "base-listing", "api", _route_path_criteria("starts_with", "/auth/routes"), ("GET",))


def _sample_routes():
    sample = [route for route in get_public_api_routes()
              if route.path in _ENFORCED_SAMPLE_PATHS]
    # The sample is a literal list of paths; a route renamed in the registry would silently
    # shrink it to nothing.
    assert len(sample) == len(_ENFORCED_SAMPLE_PATHS), [route.path for route in sample]
    return sample


_VOCABULARY_CASES = {
    # A static prefix -- the shape every shipped permission template uses.
    "starts_with-static": [_constraint(
        "c", "api", _route_path_criteria("starts_with", "/database"), ALL_METHODS)],
    # A value authored from the route listing's own dropdown. It matches the template text
    # and no request path, so both sides must deny.
    "equals-template": [_constraint(
        "c", "api", _route_path_criteria("equals", "/database/{databaseId}/assets"),
        ALL_METHODS)],
    "contains-static": [_constraint(
        "c", "api", _route_path_criteria("contains", "/assets"), ALL_METHODS)],
    "ends_with-static": [_constraint(
        "c", "api", _route_path_criteria("ends_with", "/logs"), ALL_METHODS)],
    "is_one_of": [_constraint(
        "c", "api", _route_path_criteria("is_one_of", "/database", "/tags"), ALL_METHODS)],
    # Allow everything, then take the logs route back -- the shipped read-only DENY shape.
    "allow-all-with-logs-deny": [
        _constraint("allow", "api", _route_path_criteria("starts_with", "/"), ALL_METHODS),
        _constraint("deny", "api", _route_path_criteria("ends_with", "/logs"), ALL_METHODS,
                    permission_type="deny",
                    criteria_and=_route_path_criteria(
                        "starts_with", "/workflows/executions/")),
    ],
    # A DENY written with a concrete resource value: it matches no probe path and no sampled
    # request path, so both sides must agree that it does not fire here.
    "concrete-valued-deny": [
        _constraint("allow", "api", _route_path_criteria("starts_with", "/"), ALL_METHODS),
        _constraint("deny", "api", _route_path_criteria("contains", "/database/db-a/"),
                    ALL_METHODS, permission_type="deny"),
    ],
}


@pytest.mark.unit
class TestListingAgreesWithEnforcement:
    """The listing's verdict must equal the verdict a real request receives."""

    @pytest.mark.parametrize("case", sorted(_VOCABULARY_CASES))
    def test_listing_verdict_equals_enforce_api_verdict(self, probe_env, case):
        probe_env["set_constraints"]([_BASE_LISTING_GRANT] + _VOCABULARY_CASES[case])

        response = routes_module.lambda_handler(_make_event(), {})
        assert response["statusCode"] == 200
        listed = _allowed_methods_by_path(_payload(response))

        enforcer = authz_module.CasbinEnforcer(dict(_CLAIMS))
        disagreements = []
        for route in _sample_routes():
            for method in route.methods:
                listing_allows = method in listed.get(route.path, set())
                enforced_allows = enforcer.enforceAPI(
                    _make_event(method=method, path=_concrete_path(route.path)))
                if listing_allows != bool(enforced_allows):
                    disagreements.append(
                        f"{method} {route.path}: listed={listing_allows} "
                        f"enforced={bool(enforced_allows)}")
        assert disagreements == [], f"case {case}: " + "; ".join(disagreements)

    def test_a_template_valued_grant_is_reported_denied_because_it_is_denied(self, probe_env):
        """The reported defect, stated directly: a grant written as the route template used to
        be listed as allowed and then 403 on every call. Both sides now deny it."""
        probe_env["set_constraints"]([_BASE_LISTING_GRANT, _constraint(
            "template-grant", "api",
            _route_path_criteria("equals", "/database/{databaseId}/assets"), ("GET",))])

        listed = _allowed_methods_by_path(_payload(routes_module.lambda_handler(_make_event(), {})))
        enforcer = authz_module.CasbinEnforcer(dict(_CLAIMS))

        assert "/database/{databaseId}/assets" not in listed
        assert not enforcer.enforceAPI(_make_event(path="/database/db1/assets"))
        # The permitted half: the same policy's static-prefix grant is still listed, so the
        # assertion above is not "the listing returned nothing".
        assert "GET" in listed["/auth/routes/api"]

    def test_a_concrete_valued_deny_still_denies_its_own_resource(self, probe_env):
        """A per-resource rule cannot be represented in a per-route answer. The listing reports
        the route as reachable (it is, for other databases) while the request for the denied
        database is refused -- the same split as before the probe path was introduced."""
        probe_env["set_constraints"]([
            _constraint("allow", "api", _route_path_criteria("starts_with", "/"), ALL_METHODS),
            _constraint("deny", "api", _route_path_criteria("contains", "/database/db-a/"),
                        ALL_METHODS, permission_type="deny"),
        ])
        listed = _allowed_methods_by_path(_payload(routes_module.lambda_handler(_make_event(), {})))
        enforcer = authz_module.CasbinEnforcer(dict(_CLAIMS))

        assert "GET" in listed["/database/{databaseId}/assets"]
        assert not enforcer.enforceAPI(_make_event(path="/database/db-a/assets"))
        assert enforcer.enforceAPI(_make_event(path="/database/db-b/assets"))

    @pytest.mark.parametrize("value", ["db%20one", "100%25", "a.b-c_d"])
    def test_a_raw_encoded_parameter_value_agrees_with_the_probe(self, probe_env, value):
        """The enforced path is stage-stripped but NOT percent-decoded, so a parameter value
        carrying an escape reaches Casbin raw. A static-prefix grant must cover it exactly as
        the listing reports."""
        probe_env["set_constraints"]([_BASE_LISTING_GRANT, _constraint(
            "prefix", "api", _route_path_criteria("starts_with", "/database"), ("GET",))])
        listed = _allowed_methods_by_path(_payload(routes_module.lambda_handler(_make_event(), {})))
        enforcer = authz_module.CasbinEnforcer(dict(_CLAIMS))

        assert "GET" in listed["/database/{databaseId}/assets"]
        assert enforcer.enforceAPI(
            _make_event(path=_concrete_path("/database/{databaseId}/assets", value)))

    def test_a_constraint_matching_no_registry_route_is_kept_and_still_enforced(self, probe_env):
        """External APIs registered outside VAMS, and deprecated paths still sitting in a
        constraint, match nothing in the route registry. They must keep working: the value is
        compiled and enforced as written, and the listing simply has no route to report it on.
        """
        external = _constraint(
            "external", "api",
            _route_path_criteria("equals", "/external/thing")
            + _route_path_criteria("starts_with", "/deprecated"),
            ("GET",))
        probe_env["set_constraints"](_readonly_constraints() + [external])

        enforcer = authz_module.CasbinEnforcer(dict(_CLAIMS))
        assert enforcer.enforceAPI(_make_event(path="/external/thing"))
        assert enforcer.enforceAPI(_make_event(path="/deprecated/old-endpoint"))
        assert not enforcer.enforceAPI(_make_event(method="POST", path="/external/thing"))

        response = routes_module.lambda_handler(_make_event(), {})
        assert response["statusCode"] == 200
        listed = _allowed_methods_by_path(_payload(response))
        # Not invented as a route, and the registry routes the same policy grants are intact.
        assert "/external/thing" not in listed
        assert "/deprecated" not in listed
        assert listed.get("/database") == {"GET"}
