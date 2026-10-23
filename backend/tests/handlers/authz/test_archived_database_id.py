# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Archived records are authorized on the database they belong to.

Archiving an asset rewrites its record under the partition key ``<databaseId>#deleted``, and deleting
a database rewrites the database record the same way. Every path that hands such a record to
``CasbinEnforcer.enforce`` -- the asset listings and the single-asset GET with ``showArchived``, the
database listing and GET with ``showDeleted`` -- therefore evaluates a ``databaseId`` that carries the
suffix. The ``equals`` operator compiles to ``^value\\Z`` and ``ends_with`` to ``(?s:.*)value\\Z``, so
neither matched the suffixed id:

* a role that allows every database and denies ``dbX`` with ``equals`` did not apply that deny to
  ``dbX``'s archived records, and
* a role that allows ``dbX`` with ``equals`` did not reach ``dbX``'s archived records at all.

The documented rule is that an archived asset is authorized on its own attributes exactly as before
archiving (docs: deployment/update-the-solution.md, "Execution visibility"). ``CasbinEnforcerService
.enforce`` now removes the suffix from the evaluated ``databaseId`` -- once, at the single point every
object-level and route-probe decision passes through -- so each operator sees the real database id.
The stored record and the caller's dict are left unchanged, and no other field is rewritten.

These tests run the REAL policy-text generation and the REAL enforcer construction
(``_create_casbin_enforcer_helper``, i.e. ``ExpressionCachingEnforcer`` over
``PERMISSION_CONSTRAINT_POLICY``); only the DynamoDB reads are stubbed.
"""

import pytest
from unittest.mock import patch

from backend.backend.handlers.authz import CasbinEnforcerService
from backend.backend.common.constants import PERMISSION_CONSTRAINT_POLICY

USER_ID = "tester"
ROLE = "roleA"
DENIED_DB = "dbX"
OTHER_DB = "dbY"
ARCHIVED = "#deleted"


def _build_service(policies):
    """A CasbinEnforcerService with the real policy text and enforcer; DynamoDB reads stubbed."""
    svc = CasbinEnforcerService.__new__(CasbinEnforcerService)
    svc._user_id = USER_ID
    svc._mfaEnabled = True
    svc._model_text = PERMISSION_CONSTRAINT_POLICY
    user_roles = [{"userId": USER_ID, "roleName": ROLE}]
    with patch.object(CasbinEnforcerService, "_read_current_user_roles_from_table",
                      return_value=user_roles), \
         patch.object(CasbinEnforcerService, "_read_policies_batch_optimized",
                      return_value=policies):
        policy_text = svc._create_policy_text_helper()
    svc._enforcer = svc._create_casbin_enforcer_helper(policy_text)
    return svc


def _constraint(constraint_id, object_type, criteria, action="GET", effect="allow"):
    return {
        "constraintId": constraint_id,
        "objectType": object_type,
        "criteriaAnd": criteria,
        "criteriaOr": [],
        "groupPermissions": [{"groupId": ROLE, "permission": action, "permissionType": effect}],
        "userPermissions": [],
    }


def _crit(operator, value, field="databaseId"):
    return {"field": field, "operator": operator, "value": value}


def _allow_all(object_type):
    return _constraint(f"allow-all-{object_type}", object_type, [_crit("contains", ".*")])


def _asset(database_id, **extra):
    return {"object__type": "asset", "databaseId": database_id, "assetId": "a1",
            "assetName": "Part", "assetType": ".glb", "tags": [], **extra}


def _database(database_id):
    return {"object__type": "database", "databaseId": database_id}


@pytest.mark.unit
class TestDenyAppliesToArchivedRecords:
    """Allow every database, deny one with `equals`: the deny covers that database's archived rows."""

    @pytest.fixture
    def svc(self):
        return _build_service([
            _allow_all("asset"),
            _allow_all("database"),
            _constraint("deny-asset", "asset", [_crit("equals", DENIED_DB)], effect="deny"),
            _constraint("deny-db", "database", [_crit("equals", DENIED_DB)], effect="deny"),
        ])

    def test_an_archived_asset_in_the_denied_database_is_denied(self, svc):
        assert svc.enforce(_asset(DENIED_DB + ARCHIVED), "GET") is False

    def test_an_archived_asset_in_another_database_is_allowed(self, svc):
        assert svc.enforce(_asset(OTHER_DB + ARCHIVED), "GET") is True

    def test_live_assets_are_decided_as_before(self, svc):
        assert svc.enforce(_asset(DENIED_DB), "GET") is False
        assert svc.enforce(_asset(OTHER_DB), "GET") is True

    def test_an_archived_database_record_is_denied_like_the_live_one(self, svc):
        assert svc.enforce(_database(DENIED_DB + ARCHIVED), "GET") is False
        assert svc.enforce(_database(DENIED_DB), "GET") is False

    def test_an_archived_record_of_another_database_is_allowed(self, svc):
        assert svc.enforce(_database(OTHER_DB + ARCHIVED), "GET") is True

    def test_a_repeated_suffix_is_decided_on_the_same_database(self, svc):
        assert svc.enforce(_asset(DENIED_DB + ARCHIVED + ARCHIVED), "GET") is False
        assert svc.enforce(_asset(OTHER_DB + ARCHIVED + ARCHIVED), "GET") is True


@pytest.mark.unit
class TestScopedAllowReachesArchivedRecords:
    """A role scoped to one database with `equals` is decided the same for its archived rows."""

    @pytest.fixture
    def svc(self):
        return _build_service([
            _constraint("allow-asset", "asset", [_crit("equals", DENIED_DB)]),
        ])

    def test_an_archived_asset_in_the_scoped_database_is_allowed(self, svc):
        assert svc.enforce(_asset(DENIED_DB + ARCHIVED), "GET") is True

    def test_an_archived_asset_outside_the_scope_is_still_denied(self, svc):
        assert svc.enforce(_asset(OTHER_DB + ARCHIVED), "GET") is False
        assert svc.enforce(_asset(OTHER_DB), "GET") is False


@pytest.mark.unit
class TestOtherOperatorsOnArchivedRecords:
    """Every operator sees the real database id; the prefix and containment forms do not regress."""

    @pytest.mark.parametrize("operator, value", [
        ("equals", DENIED_DB),
        ("starts_with", DENIED_DB),
        ("contains", DENIED_DB),
        ("ends_with", DENIED_DB),
    ])
    def test_a_deny_on_the_database_covers_its_archived_assets(self, operator, value):
        svc = _build_service([
            _allow_all("asset"),
            _constraint("deny", "asset", [_crit(operator, value)], effect="deny"),
        ])

        assert svc.enforce(_asset(DENIED_DB + ARCHIVED), "GET") is False, operator
        assert svc.enforce(_asset(DENIED_DB), "GET") is False, operator
        assert svc.enforce(_asset(OTHER_DB + ARCHIVED), "GET") is True, operator

    def test_a_rule_written_against_the_suffix_no_longer_matches(self):
        """The suffix is a storage detail, not part of the database id a rule is written against."""
        svc = _build_service([
            _allow_all("asset"),
            _constraint("deny", "asset", [_crit("ends_with", ARCHIVED)], effect="deny"),
        ])

        assert svc.enforce(_asset(DENIED_DB + ARCHIVED), "GET") is True


@pytest.mark.unit
class TestNormalizationIsConfined:
    """Only the evaluated databaseId changes: not the caller's dict, not any other field."""

    def test_the_callers_object_is_not_modified(self):
        svc = _build_service([_allow_all("asset")])
        obj = _asset(DENIED_DB + ARCHIVED)

        svc.enforce(obj, "GET")

        assert obj["databaseId"] == DENIED_DB + ARCHIVED

    def test_other_fields_keep_the_suffix_text(self):
        svc = _build_service([
            _allow_all("asset"),
            _constraint("deny-name", "asset", [_crit("equals", "Part", field="assetName")],
                        effect="deny"),
        ])

        assert svc.enforce(_asset(OTHER_DB, assetName="Part" + ARCHIVED), "GET") is True
        assert svc.enforce(_asset(OTHER_DB, assetName="Part"), "GET") is False

    def test_the_suffix_is_removed_only_at_the_end_of_the_id(self):
        svc = _build_service([
            _allow_all("asset"),
            _constraint("deny", "asset", [_crit("equals", DENIED_DB)], effect="deny"),
        ])

        assert svc.enforce(_asset(DENIED_DB + ARCHIVED + "x"), "GET") is True
