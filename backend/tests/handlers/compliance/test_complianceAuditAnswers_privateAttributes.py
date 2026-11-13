# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""The audit listing returns no attribute an audit row keeps for the listing only (D163, D161 run).

An audit row carries `allListPartition` (the constant partition key of `AuditByDateGSI`, which
makes the unfiltered listing one newest-first query) and `searchText` (what the `text` filter
matches). The listing dropped `searchText` and returned `allListPartition`. Now neither is
returned, by the global listing (unfiltered, by event type, by database, by text) or the per-asset
history, and every attribute an entry carries is a documented property of the `auditLogEntry`
schema in documentation/VAMS_API.yaml. The rows keep both attributes, so the date index and the
text filter still find them. Moto tables built from the CDK definitions, a stand-in enforcer that
allows everything.
"""

import pathlib
from unittest.mock import MagicMock, patch

import boto3
import pytest
import yaml
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import USER, body_of, claims_for, rest_event
from backend.tests.handlers.compliance.test_complianceUnknownDatabase import _stand_in
from common.compliance import auditRecord
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceEvaluationStore as store

OPENAPI = pathlib.Path(__file__).resolve().parents[4] / "documentation" / "VAMS_API.yaml"

DB = "audit-answers-db"
ASSET = "turbine-7"
# Spelled out, so the answers are checked against what a row carries, not the code's own list.
LISTING_ATTRIBUTES = ("allListPartition", "searchText")


class World:

    def __enter__(self):
        self._mock = mock_aws()
        self._mock.start()
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        self.patches, self.t = _cdkTables.table_doubles(resource, (store, audit_svc))
        self.patches += [
            patch.object(audit_svc, "CasbinEnforcer", return_value=_stand_in({"*"})),
            patch.object(audit_svc, "request_to_claims", claims_for(USER)),
        ]
        for active in self.patches:
            active.start()
        self.t["databaseStorage"].put_item(Item={"databaseId": DB})
        self.t["assetStorage"].put_item(Item={"databaseId": DB, "assetId": ASSET,
                                              "assetName": "Turbine Blade 7"})
        store.write_audit(DB, ASSET, "compliance_check", USER, details={"verdict": "compliant"},
                          previous_state="pending_evaluation", new_state="compliant",
                          schema_name="std", evaluation_id="8a1d2c3b-4e5f-4a7b-8c9d-0e1f2a3b4c5d",
                          asset_name="Turbine Blade 7")
        store.write_audit(DB, "*", "schema_bound_to_database", USER, schema_name="std",
                          details={"previousSchema": None})
        return self

    def __exit__(self, *exc):
        for active in reversed(self.patches):
            active.stop()
        self._mock.stop()

    def entries(self, path="/compliance/audit", params=None, query=None):
        response = audit_svc.lambda_handler(rest_event("GET", path, params, query), MagicMock())
        assert response["statusCode"] == 200, response
        return body_of(response)["entries"]


@pytest.fixture
def world():
    with World() as active:
        yield active


def _documented():
    spec = yaml.safe_load(OPENAPI.read_text(encoding="utf-8"))
    return set(spec["components"]["schemas"]["auditLogEntry"]["properties"])


READS = [
    pytest.param({}, id="unfiltered"),
    pytest.param({"eventType": "compliance_check"}, id="event type"),
    pytest.param({"databaseId": DB}, id="database"),
    pytest.param({"text": "blade"}, id="text"),
]


@pytest.mark.unit
class TestTheAuditListing:

    @pytest.mark.parametrize("query", READS)
    def test_no_listing_attribute_is_returned(self, world, query):
        entries = world.entries(query=query)
        assert entries
        for entry in entries:
            assert set(entry).isdisjoint(LISTING_ATTRIBUTES), entry

    def test_the_asset_history(self, world):
        entries = world.entries(f"/compliance/audit/{DB}/{ASSET}",
                                {"databaseId": DB, "assetId": ASSET})
        assert [entry["eventType"] for entry in entries] == ["compliance_check"]
        assert set(entries[0]).isdisjoint(LISTING_ATTRIBUTES)

    @pytest.mark.parametrize("query", READS)
    def test_every_returned_attribute_is_documented(self, world, query):
        documented = _documented()
        for entry in world.entries(query=query):
            assert set(entry) - documented == set(), entry["eventType"]

    def test_the_listing_attributes_are_not_documented_and_the_code_keeps_them_private(self):
        assert set(LISTING_ATTRIBUTES).isdisjoint(_documented())
        assert set(auditRecord.PRIVATE_AUDIT_ATTRIBUTES) == set(LISTING_ATTRIBUTES)

    def test_the_rows_keep_them_and_the_listing_still_uses_them(self, world):
        rows = world.t["complianceAuditStorage"].scan()["Items"]
        assert all(set(LISTING_ATTRIBUTES) <= set(row) for row in rows)
        world.entries()
        assert world.t["complianceAuditStorage"].scan()["Items"] == rows
        assert [entry["assetId"] for entry in world.entries(query={"text": "blade"})] == [ASSET]
        assert len(world.entries()) == 2
