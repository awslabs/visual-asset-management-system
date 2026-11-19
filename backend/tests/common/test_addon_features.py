# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every add-on route and permission object type names the feature switch it is deployed under.

The route listings and the constraint editor leave an entry out when the deployment did not enable
its switch (``common/addonFeatures.py``), so an untagged add-on entry would be listed on every
deployment, and a tag naming no published switch would never be listed. The switch names mirror
``infra/common/addonAppFeatures.ts``.
"""

import os
import re

import pytest

from common.addonApiRoutes import ADDON_ROUTES
from common.addonConstants import ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES, ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS
from common.addonFeatures import (
    CORE_FEATURE_AUDIT_LOG_SEARCH,
    ADDON_FEATURES,
    ADDON_FEATURES_ENABLED_ENV,
    addon_feature_deployed,
    constraint_object_type_deployed,
    deployed_addon_features,
)
from common.apiRoutes import ALL_API_ROUTES

_ADDON_APP_FEATURES_TS = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "..", "infra", "common", "addonAppFeatures.ts"))


def test_the_switch_names_mirror_the_cdk_enum():
    with open(_ADDON_APP_FEATURES_TS, encoding="utf-8") as handle:
        body = re.search(r"export enum ADDON_APP_FEATURES\s*\{(.*?)\}", handle.read(), re.S).group(1)
    declared = set(re.findall(r'=\s*"([A-Z0-9_]+)"', body))
    assert declared, "parsed no member of ADDON_APP_FEATURES"
    assert set(ADDON_FEATURES) == declared


def test_every_add_on_route_is_tagged_with_a_published_switch():
    assert ADDON_ROUTES, "no add-on route, so this would be vacuous"
    untagged = [route.path for route in ADDON_ROUTES if route.addon_feature not in ADDON_FEATURES]
    assert untagged == []


def test_no_core_or_fork_route_is_tagged_except_the_audit_search_routes():
    addon = {id(route) for route in ADDON_ROUTES}
    tagged = [
        route.path for route in ALL_API_ROUTES
        if id(route) not in addon and route.addon_feature and route.category != "audit"
    ]
    assert tagged == []


def test_every_audit_route_carries_the_audit_search_switch_and_nothing_else_does():
    audit = [route for route in ALL_API_ROUTES if route.category == "audit"]
    assert len(audit) == 4
    assert {route.addon_feature for route in audit} == {CORE_FEATURE_AUDIT_LOG_SEARCH}
    assert CORE_FEATURE_AUDIT_LOG_SEARCH not in ADDON_FEATURES


def test_the_audit_search_switch_mirrors_the_cdk_enum():
    ts = os.path.abspath(os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "infra", "common", "vamsAppFeatures.ts"))
    with open(ts, encoding="utf-8") as handle:
        assert f'AUDITLOGSEARCH = "{CORE_FEATURE_AUDIT_LOG_SEARCH}"' in handle.read()


def test_the_audit_routes_are_listed_only_when_the_deployment_enabled_search(monkeypatch):
    from common.apiRoutes import get_public_api_routes

    def audit_paths():
        return {route.path for route in get_public_api_routes() if route.category == "audit"}

    monkeypatch.delenv(ADDON_FEATURES_ENABLED_ENV, raising=False)
    assert len(audit_paths()) == 4  # switches unknown (local server, unit test): everything listed
    monkeypatch.setenv(ADDON_FEATURES_ENABLED_ENV, "")
    assert audit_paths() == set()
    monkeypatch.setenv(ADDON_FEATURES_ENABLED_ENV, "PHYSNA_ADDON")
    assert audit_paths() == set()
    monkeypatch.setenv(ADDON_FEATURES_ENABLED_ENV, f"PHYSNA_ADDON,{CORE_FEATURE_AUDIT_LOG_SEARCH}")
    assert len(audit_paths()) == 4


def test_every_add_on_object_type_is_tagged_once_with_a_published_switch():
    assert ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS, "no add-on object type, so this would be vacuous"
    assert set(ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES) == set(ADDON_CONSTRAINT_OBJECT_TYPE_FIELDS)
    assert set(ADDON_CONSTRAINT_OBJECT_TYPE_FEATURES.values()) <= set(ADDON_FEATURES)


@pytest.mark.parametrize("raw,expected", [
    ("", frozenset()),
    ("EDH_WORKSTATIONS", frozenset({"EDH_WORKSTATIONS"})),
    (" PHYSNA_ADDON , EDH_WORKSTATIONS,", frozenset({"PHYSNA_ADDON", "EDH_WORKSTATIONS"})),
])
def test_the_variable_is_read_as_a_comma_separated_list(monkeypatch, raw, expected):
    monkeypatch.setenv(ADDON_FEATURES_ENABLED_ENV, raw)
    assert deployed_addon_features() == expected


def test_an_untagged_entry_is_always_listed_and_a_tagged_one_only_with_its_switch(monkeypatch):
    monkeypatch.delenv(ADDON_FEATURES_ENABLED_ENV, raising=False)
    assert deployed_addon_features() is None
    assert addon_feature_deployed("EDH_WORKSTATIONS") is True
    monkeypatch.setenv(ADDON_FEATURES_ENABLED_ENV, "PHYSNA_ADDON")
    assert addon_feature_deployed(None) is True
    assert addon_feature_deployed("PHYSNA_ADDON") is True
    assert addon_feature_deployed("EDH_WORKSTATIONS") is False
    assert constraint_object_type_deployed("asset") is True
    assert constraint_object_type_deployed("edhWorkstation") is False
    monkeypatch.setenv(ADDON_FEATURES_ENABLED_ENV, "EDH_WORKSTATIONS")
    assert constraint_object_type_deployed("edhWorkstation") is True
    assert constraint_object_type_deployed("edhContainer") is False
