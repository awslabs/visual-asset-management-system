# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared harness for the search permission-filter tests: search.py loaded by path over the real authz
module, and real policies for one caller built from constraint fixtures."""

import contextlib
import copy
import importlib.util
import os
import sys
import types
from unittest.mock import MagicMock, patch

import backend.backend.handlers.authz as authz_module

os.environ.setdefault("ASSET_STORAGE_TABLE_NAME", "test-asset-table")
os.environ.setdefault("DATABASE_STORAGE_TABLE_NAME", "test-db-table")
os.environ.setdefault("OPENSEARCH_ASSET_INDEX_SSM_PARAM", "/test/asset-index")
os.environ.setdefault("OPENSEARCH_FILE_INDEX_SSM_PARAM", "/test/file-index")
os.environ.setdefault("OPENSEARCH_ENDPOINT_SSM_PARAM", "/test/endpoint")
os.environ.setdefault("OPENSEARCH_TYPE", "provisioned")

_SEARCH_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "backend", "handlers", "osSemanticSearch", "search.py"
)

USER_ID = "filter-user"
ROLE = "searcher"
OTHER_ROLE = "second"
CLAIMS = {"tokens": [USER_ID], "roles": [], "mfaEnabled": True}
ARCHIVED = "#deleted"


def _boto_client(name, *args, **kwargs):
    client = MagicMock()
    if name == "ssm":
        client.get_parameter.return_value = {"Parameter": {"Value": "test-value"}}
    return client


def load_search_module(module_name):
    """search.py loaded by path with the real authz module and the asset-table helpers stubbed."""
    names = ("handlers.auth", "handlers.authz", "common.dynamodb")
    saved = {name: sys.modules.get(name) for name in names}
    sys.modules["handlers.authz"] = authz_module
    auth_stub = types.ModuleType("handlers.auth")
    auth_stub.request_to_claims = MagicMock(return_value={"tokens": [USER_ID]})
    sys.modules["handlers.auth"] = auth_stub
    dynamodb_stub = types.ModuleType("common.dynamodb")
    dynamodb_stub.validate_pagination_info = MagicMock()
    dynamodb_stub.batch_get_items = MagicMock(return_value=([], []))
    sys.modules["common.dynamodb"] = dynamodb_stub
    try:
        with patch("boto3.client", side_effect=_boto_client), patch("boto3.resource", return_value=MagicMock()):
            spec = importlib.util.spec_from_file_location(module_name, os.path.abspath(_SEARCH_PATH))
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
    finally:
        for name, mod in saved.items():
            if mod is not None:
                sys.modules[name] = mod
            else:
                sys.modules.pop(name, None)
    return module


class FakeClock:
    """A monotonic clock the search under test reads instead of the real one (``install_fake_clock``):
    each in-memory fetch and each per-hit decision advances it by a set cost, so window sizes and the
    time bound follow those costs and never how fast the runner is (#1633).

    The defaults are a fast runner's: 10 ms a fetch and 0.1 ms a decision, so 2,020 candidates are
    fetched and checked in well under a second of the 10 s bound and every window grows the full
    SEARCH_FILL_GROWTH_FACTOR. A test about the time bound sets its own with ``costs``.

    ``time()`` is the wall clock (epoch seconds) moving with ``monotonic()``, for a test that sets when
    API Gateway received the request (``requestContext.requestTimeEpoch``, #1763)."""

    FETCH_SECONDS = 0.01
    FETCH_SECONDS_PER_HIT = 0.0
    DECISION_SECONDS = 0.0001
    # The wall clock when the monotonic clock reads its start
    WALL_START = 1_763_600_000.0

    def __init__(self):
        self.now = 1000.0
        self._wall_offset = self.WALL_START - self.now
        self.costs(self.FETCH_SECONDS, self.FETCH_SECONDS_PER_HIT, self.DECISION_SECONDS)

    def costs(self, fetch_seconds, fetch_seconds_per_hit, decision_seconds):
        """Set what each fetch (plus a per-hit share) and each decision costs; returns the clock."""
        self.fetch_seconds = fetch_seconds
        self.fetch_seconds_per_hit = fetch_seconds_per_hit
        self.decision_seconds = decision_seconds
        return self

    def monotonic(self):
        return self.now

    def time(self):
        return self.now + self._wall_offset

    def epoch_ms(self, seconds_ago=0.0):
        """``requestTimeEpoch`` for a request API Gateway received ``seconds_ago`` before now."""
        return int(round((self.time() - seconds_ago) * 1000))


def install_fake_clock(module, monkeypatch):
    """Put ``module`` (a search.py from load_search_module) on a FakeClock for the rest of the test: its
    ``time`` reads the clock, and every FakeSearchManager fetch and every per-hit decision advances it.
    Returns the clock."""
    from backend.tests.handlers.authz.opensearch_dsl_oracle import FakeSearchManager

    clock = FakeClock()
    monkeypatch.setattr(module, "time", clock)
    fetch = FakeSearchManager.search_dual_index

    def timed_fetch(self, asset_query, file_query, entity_types):
        response = fetch(self, asset_query, file_query, entity_types)
        clock.now += clock.fetch_seconds + clock.fetch_seconds_per_hit * len(response["hits"]["hits"])
        return response

    monkeypatch.setattr(FakeSearchManager, "search_dual_index", timed_fetch)
    decide = module.DualIndexResponseProcessor._hit_decision

    def timed_decision(self, hit, claims):
        clock.now += clock.decision_seconds
        return decide(self, hit, claims)

    monkeypatch.setattr(module.DualIndexResponseProcessor, "_hit_decision", timed_decision)
    return clock


@contextlib.contextmanager
def policies(constraints, roles):
    """Real policy text and enforcer for USER_ID holding ``roles``, built from ``constraints``."""
    authz_module.casbin_user_policy_map = {}
    authz_module.casbin_user_enforcer_map = {}
    try:
        with patch.object(
            authz_module.CasbinEnforcerService, "_read_current_user_roles_from_table",
            return_value=[{"userId": USER_ID, "roleName": role} for role in roles],
        ), patch.object(
            authz_module.CasbinEnforcerService, "_read_policies_batch_optimized",
            return_value=copy.deepcopy(list(constraints)),
        ):
            yield
    finally:
        authz_module.casbin_user_policy_map = {}
        authz_module.casbin_user_enforcer_map = {}


def constraint(cid, criteria_and=(), criteria_or=(), effect="allow", group=ROLE, permission="GET",
               object_type="asset", users=()):
    return {
        "constraintId": cid,
        "objectType": object_type,
        "criteriaAnd": list(criteria_and),
        "criteriaOr": list(criteria_or),
        "groupPermissions": [{"groupId": group, "permission": permission, "permissionType": effect}]
        if group else [],
        "userPermissions": [{"userId": u, "permission": permission, "permissionType": effect} for u in users],
    }


def crit(field, operator, value):
    return {"field": field, "operator": operator, "value": value}


ALLOW_ALL = constraint("allow-all", [crit("databaseId", "contains", ".*")])
