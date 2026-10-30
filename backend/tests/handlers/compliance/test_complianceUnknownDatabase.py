# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A database-level compliance read of an id that names no database answers `400` "Database not
found".

`GET /compliance/state/{databaseId}` for a database that does not exist used to answer `200` with
an empty overview, and the quarantine, cascade and audit listings filtered by such a `databaseId`
an empty page, so a mistyped id read as "no assets". Now each answers `400` "Database not found"
for it, as the other compliance routes do for a database that does not exist (`common.compliance.knownDatabase`), once the caller has passed the read's own check on the
id: a caller refused on the id gets the answer it got before (`403` from the overview, an empty
page from a listing) whether the database exists or not, and the database table is not read for
it. A real database with no compliance data still reads as empty, a deleted database (its row
moved to `<databaseId>#deleted`) keeps its history readable, and `GLOBAL` is not checked.

The services run on moto tables built from the CDK definitions (`_cdkTables`), with a stand-in
enforcer that allows the compliance objects of the databases a test names.
"""

import json
import pathlib
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws

from backend.tests.handlers.compliance import _cdkTables
from backend.tests.handlers.compliance._harness import (
    USER, body_of, claims_for, database_table_with, rest_event,
)
from common.compliance import knownDatabase
from common.compliance.auditRecord import build_audit_item
from handlers.compliance import complianceAuditService as audit_svc
from handlers.compliance import complianceCascadeService as cascade_svc
from handlers.compliance import complianceEvaluateService as evaluate_svc
from handlers.compliance import complianceEvaluationStore as store
from handlers.compliance import complianceQuarantineService as quarantine_svc

SERVICES = (store, evaluate_svc, quarantine_svc, cascade_svc, audit_svc)
REAL_DB = "real-empty-db"
DELETED_DB = "deleted-db"
UNKNOWN_DB = "mistyped-db"
OTHER_DB = "other-db"

# The database-level reads: (service, path, path parameters, query) for a database id.
READS = {
    "overview": lambda db: (evaluate_svc, f"/compliance/state/{db}", {"databaseId": db}, None),
    "quarantine listing": lambda db: (quarantine_svc, "/compliance/quarantine", None,
                                      {"databaseId": db}),
    "cascade listing": lambda db: (cascade_svc, "/compliance/cascades", None,
                                   {"databaseId": db, "state": "completed"}),
    "audit listing": lambda db: (audit_svc, "/compliance/audit", None, {"databaseId": db}),
}


def _seed(doubles):
    doubles["databaseStorage"].put_item(Item={"databaseId": REAL_DB})
    doubles["databaseStorage"].put_item(Item={"databaseId": OTHER_DB})
    # A deleted database's row is kept under `<databaseId>#deleted`; its history stays.
    doubles["databaseStorage"].put_item(Item={"databaseId": f"{DELETED_DB}#deleted"})
    doubles["complianceAuditStorage"].put_item(Item=build_audit_item(
        DELETED_DB, "old-asset", "compliance_check", "SYSTEM_USER", {},
        timestamp="2026-10-01T00:00:00+00:00", asset_name="Old Asset"))
    doubles["complianceCascadeStorage"].put_item(Item={
        "cascadeId": "7a0c0000-0000-4000-8000-0000000000d1", "state": "completed",
        "createdAt": "2026-10-01T00:00:00+00:00", "triggeredByDatabaseId": DELETED_DB,
        "triggeredByAssetId": "old-asset", "triggeredByAssetName": "Old Asset",
        "nodes": "{}", "executionOrder": "[]"})


def _stand_in(allowed):
    """A CasbinEnforcer stand-in: Tier 1 passes, Tier 2 passes for the compliance objects of the
    databases in `allowed` (`*` for every database)."""
    instance = MagicMock(name="CasbinEnforcer")
    instance.enforceAPI.return_value = True
    instance.enforce.side_effect = lambda obj, _action: (
        "*" in allowed or obj.get("databaseId") in allowed)
    return instance


def _reads(allowed, calls):
    """`{(read, database id): response}` for each `(read, database id)` in `calls`, in one seeded
    world, and the database ids the existence check was asked about."""
    asked = []
    real_known = knownDatabase.database_known

    def known(table, database_id):
        asked.append(database_id)
        return real_known(table, database_id)

    instance = _stand_in(allowed)
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, doubles = _cdkTables.table_doubles(resource, SERVICES)
        for module in (evaluate_svc, quarantine_svc, cascade_svc, audit_svc):
            patches += [patch.object(module, "CasbinEnforcer", return_value=instance),
                        patch.object(module, "request_to_claims", claims_for(USER))]
        patches += [patch.object(knownDatabase, "database_known", side_effect=known)]
        for active in patches:
            active.start()
        try:
            _seed(doubles)
            out = {}
            for name, database_id in calls:
                service, path, params, query = READS[name](database_id)
                out[(name, database_id)] = service.lambda_handler(
                    rest_event("GET", path, params, query), MagicMock())
            return out, asked
        finally:
            for active in reversed(patches):
                active.stop()


def _listed(name, response):
    body = body_of(response)
    key = {"overview": "assets", "quarantine listing": "quarantinedAssets",
           "cascade listing": "cascades", "audit listing": "entries"}[name]
    return body[key]


@pytest.mark.unit
@pytest.mark.parametrize("name", list(READS))
def test_an_id_that_names_no_database_is_not_found(name):
    responses, asked = _reads({"*"}, [(name, UNKNOWN_DB)])
    response = responses[(name, UNKNOWN_DB)]
    assert response["statusCode"] == 400, response
    assert body_of(response) == {"message": "Database not found"}
    assert asked == [UNKNOWN_DB]


@pytest.mark.unit
@pytest.mark.parametrize("name", list(READS))
def test_a_caller_refused_on_the_id_learns_nothing_of_its_existence(name):
    """Refused on both ids, the caller gets the same answer for a database that exists and one that
    does not, and the database table is not read for it."""
    responses, asked = _reads({OTHER_DB}, [(name, REAL_DB), (name, UNKNOWN_DB)])
    known, unknown = responses[(name, REAL_DB)], responses[(name, UNKNOWN_DB)]
    assert unknown["statusCode"] == known["statusCode"]
    assert json.loads(unknown["body"]) == json.loads(known["body"])
    assert asked == []
    if name == "overview":
        assert unknown["statusCode"] == 403
    else:
        # A listing never refused a database filter: it lists no row of that database.
        assert unknown["statusCode"] == 200 and _listed(name, unknown) == []


@pytest.mark.unit
@pytest.mark.parametrize("name", list(READS))
def test_a_real_database_without_compliance_data_reads_as_empty(name):
    responses, _ = _reads({"*"}, [(name, REAL_DB)])
    response = responses[(name, REAL_DB)]
    assert response["statusCode"] == 200, response
    assert _listed(name, response) == []
    if name == "overview":
        assert body_of(response)["totalAssets"] == 0


@pytest.mark.unit
@pytest.mark.parametrize("name", ["cascade listing", "audit listing"])
def test_a_deleted_databases_history_stays_readable(name):
    responses, asked = _reads({"*"}, [(name, DELETED_DB)])
    response = responses[(name, DELETED_DB)]
    assert response["statusCode"] == 200, response
    assert len(_listed(name, response)) == 1
    assert asked == [DELETED_DB]


@pytest.mark.unit
@pytest.mark.parametrize("name", list(READS))
def test_global_is_not_a_database_and_is_not_refused(name):
    responses, _ = _reads({"*"}, [(name, "GLOBAL")])
    assert responses[(name, "GLOBAL")]["statusCode"] == 200


@pytest.mark.unit
@pytest.mark.parametrize("name", ["quarantine listing", "cascade listing", "audit listing"])
def test_a_listing_without_a_database_filter_reads_no_database(name):
    service, path, _, _ = READS[name](REAL_DB)
    query = {"state": "completed"} if name == "cascade listing" else None
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, _doubles = _cdkTables.table_doubles(resource, SERVICES)
        patches += [patch.object(service, "CasbinEnforcer", return_value=_stand_in({"*"})),
                    patch.object(service, "request_to_claims", claims_for(USER)),
                    patch.object(knownDatabase, "database_known", MagicMock())]
        for active in patches:
            active.start()
        try:
            response = service.lambda_handler(rest_event("GET", path, None, query), MagicMock())
            assert response["statusCode"] == 200, response
            knownDatabase.database_known.assert_not_called()
        finally:
            for active in reversed(patches):
                active.stop()


@pytest.mark.unit
@pytest.mark.parametrize("path,params", [
    (f"/compliance/state/{UNKNOWN_DB}", {"maxItems": "ten"}),
    (f"/compliance/state/{UNKNOWN_DB}", {"startingToken": "not-base64!"}),
])
def test_a_malformed_request_is_rejected_before_the_lookup(path, params):
    """A request the overview would refuse as malformed is refused so (`400`), whether the id names
    a database or not."""
    responses, asked = {}, []
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name="us-east-1")
        patches, _doubles = _cdkTables.table_doubles(resource, SERVICES)
        patches += [patch.object(evaluate_svc, "CasbinEnforcer", return_value=_stand_in({"*"})),
                    patch.object(evaluate_svc, "request_to_claims", claims_for(USER)),
                    patch.object(store, "database_known", side_effect=asked.append)]
        for active in patches:
            active.start()
        try:
            responses = evaluate_svc.lambda_handler(
                rest_event("GET", path, {"databaseId": UNKNOWN_DB}, params), MagicMock())
        finally:
            for active in reversed(patches):
                active.stop()
    assert responses["statusCode"] == 400
    assert asked == []


# ---------------------------------------------------------------------------------------------
# The lookup
# ---------------------------------------------------------------------------------------------
@pytest.mark.unit
def test_a_live_row_is_one_consistent_keys_only_read():
    table = database_table_with(REAL_DB)
    assert knownDatabase.database_known(table, REAL_DB) is True
    table.get_item.assert_called_once_with(
        Key={"databaseId": REAL_DB}, ConsistentRead=True, ProjectionExpression="databaseId")


@pytest.mark.unit
def test_a_deleted_databases_row_names_it():
    table = database_table_with(f"{DELETED_DB}#deleted")
    assert knownDatabase.database_known(table, DELETED_DB) is True
    assert [c.kwargs["Key"]["databaseId"] for c in table.get_item.call_args_list] == [
        DELETED_DB, f"{DELETED_DB}#deleted"]


@pytest.mark.unit
def test_an_id_with_neither_row_names_none():
    assert knownDatabase.database_known(database_table_with(REAL_DB), UNKNOWN_DB) is False


@pytest.mark.unit
def test_global_reads_nothing():
    table = database_table_with()
    assert knownDatabase.database_known(table, "GLOBAL") is True
    table.get_item.assert_not_called()


@pytest.mark.unit
def test_the_deleted_suffix_is_the_database_services():
    """The row id a deleted database leaves is the one the database service writes when it
    deletes a database (`delete_database`: `database_id + "#deleted"`)."""
    source = (pathlib.Path(__file__).resolve().parents[3] / "backend" / "handlers" / "databases"
              / "databaseService.py").read_text(encoding="utf-8")
    assert f"database_id + \"{knownDatabase.DELETED_DATABASE_ID_SUFFIX}\"" in source
