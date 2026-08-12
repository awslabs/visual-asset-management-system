# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for symmetric, graceful constraint field scrubbing (policy + object sides)."""

import pytest
from unittest.mock import MagicMock

from backend.backend.handlers.authz import CasbinEnforcer, CasbinEnforcerService


def _service():
    """A CasbinEnforcerService instance without running __init__ (avoids DynamoDB)."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = "test-user"
    svc._enforcer = MagicMock()
    svc._enforcer.enforce.return_value = True
    return svc


@pytest.mark.unit
class TestObjectSideScrub:
    def test_scrub_drops_foreign_field_for_known_type(self):
        svc = _service()
        obj = {"object__type": "asset", "databaseId": "db1", "workflowId": "wf1"}
        scrubbed = svc._scrub_object_fields(obj)
        assert "databaseId" in scrubbed
        assert "object__type" in scrubbed
        assert "workflowId" not in scrubbed

    def test_scrub_keeps_control_keys(self):
        svc = _service()
        obj = {"object__type": "web", "route__path": "/x", "method": "GET", "stray": "v"}
        scrubbed = svc._scrub_object_fields(obj)
        # route__path is in the web matrix; method/object__type are control keys.
        assert scrubbed["route__path"] == "/x"
        assert scrubbed["method"] == "GET"
        assert "stray" not in scrubbed

    def test_scrub_drops_route_path_when_not_in_type_matrix(self):
        svc = _service()
        # route__path is a mapped field only for api/web; for asset it is out of
        # scope and must be dropped (method/object__type remain control keys).
        obj = {"object__type": "asset", "databaseId": "db1", "route__path": "/x", "method": "GET"}
        scrubbed = svc._scrub_object_fields(obj)
        assert scrubbed["databaseId"] == "db1"
        assert scrubbed["method"] == "GET"
        assert "route__path" not in scrubbed

    def test_scrub_noop_for_unknown_type(self):
        svc = _service()
        obj = {"object__type": "apiKey", "apiKeyId": "k1", "extra": "v"}
        assert svc._scrub_object_fields(obj) == obj

    def test_scrub_noop_when_no_object_type(self):
        svc = _service()
        obj = {"route__path": "/x"}
        assert svc._scrub_object_fields(obj) == obj

    def test_enforce_calls_underlying_with_scrubbed_object(self):
        svc = _service()
        svc.enforce({"object__type": "asset", "databaseId": "db1", "workflowId": "wf1"}, "GET")
        # Underlying enforcer received the enhanced (scrubbed-overlay) object as 2nd arg
        called_obj = svc._enforcer.enforce.call_args[0][1]
        assert called_obj["databaseId"] == "db1"
        # workflowId is not in the asset matrix, so it stays at the seeded default ""
        assert called_obj["workflowId"] == ""

    def test_enforce_does_not_raise_on_deprecated_field(self):
        svc = _service()
        # A foreign/deprecated field must never cause enforce() to raise
        result = svc.enforce({"object__type": "asset", "deprecatedField": "x"}, "GET")
        assert result is True


@pytest.mark.unit
class TestPolicySideFilter:
    def test_generate_rules_skips_out_of_matrix_field(self):
        svc = _service()
        rules = svc._generate_criteria_object_rules(
            [{"field": "workflowId", "operator": "equals", "value": "wf1"}],
            object_type="asset",
        )
        assert rules == []

    def test_generate_rules_keeps_valid_field(self):
        svc = _service()
        rules = svc._generate_criteria_object_rules(
            [{"field": "databaseId", "operator": "equals", "value": "db1"}],
            object_type="asset",
        )
        assert len(rules) == 1
        assert "databaseId" in rules[0]

    def test_generate_rules_no_object_type_keeps_all_known_fields(self):
        svc = _service()
        rules = svc._generate_criteria_object_rules(
            [{"field": "workflowId", "operator": "equals", "value": "wf1"}],
        )
        assert len(rules) == 1

    def test_generate_rules_unknown_object_type_skips_all_fields(self):
        # A stored policy whose objectType is no longer known has an empty matrix,
        # so its criteria are skipped (conservative: the policy grants nothing).
        svc = _service()
        rules = svc._generate_criteria_object_rules(
            [{"field": "databaseId", "operator": "equals", "value": "db1"}],
            object_type="noLongerAType",
        )
        assert rules == []
