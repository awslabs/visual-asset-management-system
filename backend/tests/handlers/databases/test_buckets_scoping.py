#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""GET /buckets is scoped to administrator roles by its api route grant alone.

`bucket` is not one of ALLOWED_CONSTRAINT_OBJECT_TYPES, so no per-bucket constraint can be authored
and the listing applies no entity-level filter: whoever holds the api route sees every registered
bucket. The route grant is therefore the whole gate, which is why the grant itself is asserted here
— in the shipped permission templates and in the seeded default role constraints — rather than only
the handler behaviour. The listing's only consumers are the database create/edit surfaces (web
`CreateDatabase.tsx` via `APIService.fetchBuckets`, `vamscli database list-buckets` and its bucket
picker, the MCP `list_buckets` tool).

The pagination assertions belong here for the same reason: the listing reads maxItems / pageSize /
startingToken from its query parameters, and a listing that reads them from the wrong argument
serves a fixed page and re-serves page one forever with no error to notice.

Guards FIX-066 (S2-BACKEND-142): with no per-bucket constraint type available, the api route
grant is the whole gate on this listing, so the grant is what has to be asserted.
"""

import base64
import importlib.util
import json
import pathlib
import re
from unittest.mock import MagicMock, patch

import pytest

from backend.backend.handlers.databases.databaseService import lambda_handler

BUCKET_ID = "b9a3aba3-c092-475f-978a-d39e5d5a2657"
OTHER_BUCKET_ID = "aa11bb22-c092-475f-978a-d39e5d5a2657"

# The effective DynamoDB page size when the caller asks for none.
DEFAULT_PAGE_SIZE = 3000

REPO_ROOT = pathlib.Path(__file__).resolve().parents[4]
TEMPLATES_DIR = REPO_ROOT / "documentation" / "permissionsTemplates"
AUTH_DEFAULTS_DIR = REPO_ROOT / "infra" / "lib" / "nestedStacks" / "auth" / "constructs"
READ_ONLY_CONSTRUCT = AUTH_DEFAULTS_DIR / "dynamodb-authdefaults-ro-construct.ts"
ADMIN_CONSTRUCT = AUTH_DEFAULTS_DIR / "dynamodb-authdefaults-admin-construct.ts"

# The templates that carry api route grants at all; the remaining ones scope a single entity type.
ROUTE_GRANTING_TEMPLATES = (
    "database-admin.json",
    "database-user.json",
    "database-readonly.json",
    "global-readonly.json",
)


def _load_deployed_pagination_helper():
    """`common.dynamodb.validate_pagination_info` as deployed, loaded from its source file.

    `tests/conftest.py` replaces `common.dynamodb` with a bare MagicMock, so the helper the handler
    calls is a no-op in this suite: every pagination default would appear to come from the listing
    itself, and the interaction the assertions below are about — the shared helper seeding an absent
    pageSize from its max-items default — would be invisible.
    """
    source = REPO_ROOT / "backend" / "backend" / "common" / "dynamodb.py"
    spec = importlib.util.spec_from_file_location("_deployed_common_dynamodb", source)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate_pagination_info


DEPLOYED_VALIDATE_PAGINATION_INFO = _load_deployed_pagination_helper()


def _scan_response(rows):
    """A low-level DynamoDB scan response; the listing deserializes the typed shape itself."""
    def typed(value):
        if isinstance(value, bool):
            return {"BOOL": value}
        return {"S": str(value)}

    return {"Items": [{k: typed(v) for k, v in row.items()} for row in rows]}


def _two_buckets():
    """Two buckets registered for different teams — the material for the filtering control."""
    return _scan_response([
        {
            "bucketId": BUCKET_ID,
            "bucketName": "vams-created-asset-bucket",
            "baseAssetsPrefix": "/",
            "isDefault": True,
        },
        {
            "bucketId": OTHER_BUCKET_ID,
            "bucketName": "team-b-imported-bucket",
            "baseAssetsPrefix": "/team-b/",
            "isDefault": False,
        },
    ])


def _rest_event(query=None):
    """A REST API (v1) proxy event for GET /buckets, as API Gateway delivers it.

    The REST shape sends flat httpMethod / path and an explicit JSON `null` for absent query
    parameters, which is the case the listing's pagination reads from.
    """
    return {
        "httpMethod": "GET",
        "path": "/buckets",
        "requestContext": {
            "requestId": "test-request-id",
            "identity": {"sourceIp": "10.0.0.1"},
            "authorizer": {
                "principalId": "test-user",
                "vams:tokens": json.dumps(["test-user"]),
            },
        },
        "queryStringParameters": query,
        "pathParameters": None,
        "headers": {"Authorization": "Bearer test-token"},
    }


def _allowing_enforcer(mock_casbin):
    """Casbin allowing both tiers, so the assertion under test is the only thing that can fail."""
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.return_value = True
    mock_casbin.return_value = enforcer
    return enforcer


@pytest.mark.unit
class TestBucketsListingTier1Scoping:
    """GET /buckets: the api route grant is the only gate, and the listing is unfiltered."""

    def test_the_shared_pagination_helper_defaults_to_a_larger_page(self):
        # Control for the Limit assertions below: the shared helper seeds an absent pageSize from
        # its max-items default, which is not the listing's page size. Without this the "Limit is
        # the listing's default" assertions could be satisfied by the helper's own default.
        seeded = {}
        DEPLOYED_VALIDATE_PAGINATION_INFO(seeded)

        assert int(seeded["pageSize"]) != DEFAULT_PAGE_SIZE
        assert int(seeded["pageSize"]) == 10000

    @patch(
        "backend.backend.handlers.databases.databaseService.validate_pagination_info",
        DEPLOYED_VALIDATE_PAGINATION_INFO,
    )
    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_a_role_holding_the_route_receives_every_registered_bucket(self, mock_casbin, mock_db):
        # An administrator role: its api criterion matches /buckets, so Tier 1 allows. The REST
        # event carries no query string, so this also covers the null-parameters case.
        enforcer = _allowing_enforcer(mock_casbin)
        mock_db.scan.return_value = _two_buckets()

        response = lambda_handler(_rest_event(), MagicMock())

        assert response["statusCode"] == 200
        items = json.loads(response["body"])["Items"]
        # Two buckets in, two buckets out: the listing is NOT filtered per bucket. This is the
        # control that fails if a per-bucket enforce() block is ever (re)introduced — `bucket` is
        # not a constraint objectType, so such a check denies every row.
        assert len(items) == 2
        assert {i["bucketName"] for i in items} == {
            "vams-created-asset-bucket",
            "team-b-imported-bucket",
        }
        assert {i["bucketId"] for i in items} == {BUCKET_ID, OTHER_BUCKET_ID}
        # Tier 1 is the only enforcement on this route; no object-level check is attempted.
        enforcer.enforceAPI.assert_called_once()
        enforcer.enforce.assert_not_called()
        # A caller that asks for no page size gets the listing's own default, not the shared
        # max-items default that validate_pagination_info would otherwise seed.
        assert mock_db.scan.call_args.kwargs["Limit"] == DEFAULT_PAGE_SIZE

    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_a_role_without_the_route_is_denied_before_the_table_is_read(self, mock_casbin, mock_db):
        # A non-administrator role: no api criterion matches /buckets, so Tier 1 denies. This is
        # the behaviour the narrowed permission templates rely on.
        enforcer = MagicMock()
        enforcer.enforceAPI.return_value = False
        mock_casbin.return_value = enforcer
        mock_db.scan.return_value = _two_buckets()

        response = lambda_handler(_rest_event(), MagicMock())

        assert response["statusCode"] == 403
        assert json.loads(response["body"])["message"] == "Not Authorized"
        # Denied before any read: bucket names and prefixes never leave the table.
        mock_db.scan.assert_not_called()

    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    @patch(
        "backend.backend.handlers.databases.databaseService.request_to_claims",
        return_value={"tokens": [], "roles": [], "mfaEnabled": False},
    )
    def test_a_request_with_no_identity_is_denied(self, mock_claims, mock_casbin, mock_db):
        # No resolved identity means authorization cannot be evaluated, so the route denies
        # rather than falling through to the scan.
        response = lambda_handler(_rest_event(), MagicMock())

        assert response["statusCode"] == 403
        mock_casbin.assert_not_called()
        mock_db.scan.assert_not_called()

    @patch(
        "backend.backend.handlers.databases.databaseService.validate_pagination_info",
        DEPLOYED_VALIDATE_PAGINATION_INFO,
    )
    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_pagination_from_the_query_string_reaches_the_scan(self, mock_casbin, mock_db):
        # The caller's page size must bound the scan. A listing that reads its pagination from the
        # wrong argument silently serves a fixed page size, which no response inspection reveals.
        _allowing_enforcer(mock_casbin)
        mock_db.scan.return_value = _two_buckets()

        lambda_handler(_rest_event({"maxItems": "250", "pageSize": "250"}), MagicMock())
        assert mock_db.scan.call_args.kwargs["Limit"] == 250

        # Paired control: with no parameters the Limit is the listing's default, so the assertion
        # above cannot be satisfied by a constant.
        mock_db.scan.reset_mock()
        lambda_handler(_rest_event(), MagicMock())
        assert mock_db.scan.call_args.kwargs["Limit"] == DEFAULT_PAGE_SIZE

    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_a_starting_token_reaches_the_scan_as_the_exclusive_start_key(self, mock_casbin, mock_db):
        # A dropped startingToken re-serves page one with the same NextToken, so a paginating
        # client loops instead of advancing.
        _allowing_enforcer(mock_casbin)
        mock_db.scan.return_value = _two_buckets()
        last_key = {"bucketId": {"S": BUCKET_ID}}
        token = base64.b64encode(json.dumps(last_key).encode("utf-8")).decode("utf-8")

        lambda_handler(_rest_event({"startingToken": token}), MagicMock())

        assert mock_db.scan.call_args.kwargs["ExclusiveStartKey"] == last_key

    @patch("backend.backend.handlers.databases.databaseService.dbClient")
    @patch("backend.backend.handlers.databases.databaseService.CasbinEnforcer")
    def test_a_malformed_starting_token_names_the_token(self, mock_casbin, mock_db):
        # Now that the token is read, a malformed one reaches the decode. The caller has to be told
        # which input to correct — a generic listing failure reads as a server problem and sends
        # them to the wrong place.
        _allowing_enforcer(mock_casbin)
        mock_db.scan.return_value = _two_buckets()

        response = lambda_handler(_rest_event({"startingToken": "not-a-token"}), MagicMock())

        assert response["statusCode"] == 400
        message = json.loads(response["body"])["message"]
        assert "Invalid pagination token" in message
        assert "Error getting buckets" not in message
        mock_db.scan.assert_not_called()

        # Control: the same request with a well-formed token succeeds, so the assertion above is
        # about the token's content and not about tokens being rejected outright.
        token = base64.b64encode(json.dumps({"bucketId": {"S": BUCKET_ID}}).encode()).decode()
        ok = lambda_handler(_rest_event({"startingToken": token}), MagicMock())
        assert ok["statusCode"] == 200


def _api_route_grants(template_path):
    """Every route__path value the template's `api` constraints grant, mapped to its allowed actions."""
    document = json.loads(template_path.read_text(encoding="utf-8"))
    grants = {}
    for constraint in document["constraints"]:
        if constraint.get("objectType") != "api":
            continue
        actions = {
            permission["action"]
            for permission in constraint.get("groupPermissions", [])
            if permission.get("type") == "allow"
        }
        criteria = list(constraint.get("criteriaAnd", [])) + list(constraint.get("criteriaOr", []))
        for criterion in criteria:
            if criterion.get("field") == "route__path":
                grants.setdefault(criterion["value"], set()).update(actions)
    return grants


@pytest.mark.unit
class TestPermissionTemplatesScopeBucketsToAdministrators:
    """The shipped templates are where the route grant — the only gate — is authored."""

    def test_only_the_database_admin_template_grants_the_buckets_route(self):
        granting = {
            path.name
            for path in sorted(TEMPLATES_DIR.glob("*.json"))
            if "/buckets" in _api_route_grants(path)
        }

        assert granting == {"database-admin.json"}, (
            "/buckets is an administrator route: the listing returns every registered bucket with "
            "no entity-level filter available")

    def test_the_parser_reads_the_route_grants_it_is_asserting_on(self):
        # Positive control for the assertion above: an api-route parser that silently matched
        # nothing would report every template as /buckets-free. /database is granted by every
        # route-carrying template, so finding it proves the parser sees real grants.
        for name in ROUTE_GRANTING_TEMPLATES:
            grants = _api_route_grants(TEMPLATES_DIR / name)
            assert "/database" in grants, f"{name} grants /database"
            assert "GET" in grants["/database"], f"{name} grants GET on /database"
            assert len(grants) > 5, f"{name} carries a full set of route grants"

    def test_the_administrator_template_still_grants_the_route_for_reads(self):
        grants = _api_route_grants(TEMPLATES_DIR / "database-admin.json")

        assert "GET" in grants["/buckets"], (
            "an administrator populates the default-bucket selector from this route")


# A seeded criterion in the auth-defaults constructs. The id is a template literal, and a value can
# carry a trailing comment, so both are matched loosely.
_TS_ROUTE_CRITERION = re.compile(
    r'field:\s*\{\s*S:\s*"route__path",\s*\},\s*'
    r'id:\s*\{.*?\},\s*'
    r'operator:\s*\{\s*S:\s*"([^"]+)",\s*\},\s*'
    r'value:\s*\{\s*S:\s*"([^"]+)"',
    re.S,
)

# A criterion in the exact shape the constructs use, for controlling the extractor below.
_A_SEEDED_BUCKETS_CRITERION = """
                        {
                            M: {
                                field: {
                                    S: "route__path",
                                },
                                id: {
                                    S: `15_${roleNameIDClean}_api_paths`,
                                },
                                operator: {
                                    S: "starts_with",
                                },
                                value: {
                                    S: "/buckets",
                                },
                            },
                        },
"""


def _seeded_route_criteria(construct_path, source=None):
    """The (operator, value) pairs of every route__path criterion a construct seeds."""
    text = source if source is not None else construct_path.read_text(encoding="utf-8")
    return set(_TS_ROUTE_CRITERION.findall(text))


@pytest.mark.unit
class TestSeededDefaultRolesScopeBucketsToAdministrators:
    """A fresh deployment seeds its default roles from the auth-defaults constructs, so the same
    scoping has to hold there — a template edit alone leaves every new deployment granting the
    route."""

    def test_the_extractor_sees_a_seeded_buckets_criterion(self):
        # Positive control, stated first: the negative assertion below is only meaningful if the
        # extractor can recognise a /buckets criterion written the way the constructs write one.
        assert ("starts_with", "/buckets") in _seeded_route_criteria(
            None, source=_A_SEEDED_BUCKETS_CRITERION)

    def test_the_seeded_read_only_role_does_not_grant_the_buckets_route(self):
        source = READ_ONLY_CONSTRUCT.read_text(encoding="utf-8")
        criteria = _seeded_route_criteria(READ_ONLY_CONSTRUCT, source=source)

        # The read-only role keeps the routes its pages need; only the bucket listing is withheld.
        assert ("starts_with", "/database") in criteria
        assert ("starts_with", "/assets") in criteria
        assert ("starts_with", "/workflows") in criteria
        assert ("starts_with", "/buckets") not in criteria
        # A grant can only be spelled as this literal, so the whole file is checked rather than
        # only the criteria the extractor paired up.
        assert '"/buckets"' not in source
        assert '"/database"' in source

    def test_the_seeded_administrator_role_matches_every_api_route(self):
        # Administrators continue to see all buckets: the admin default constraint matches every
        # api route rather than enumerating them, so no /buckets entry is needed there.
        source = ADMIN_CONSTRUCT.read_text(encoding="utf-8")
        marker = source.index('"all_api_paths"')
        api_constraint = source[max(0, marker - 600):marker + 600]

        assert ("contains", ".*") in _seeded_route_criteria(None, source=api_constraint)
