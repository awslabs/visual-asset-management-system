#  Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

"""Subscriptions are stored and addressed by database and asset id (#1860).

Two databases may hold assets with the same asset id. Every subscription request names the asset's
database (`databaseId`), the subscription row is keyed `<entityName>:<databaseId>:<entityId>`, and
Tier 2 is decided on the asset of the named database before the handler reveals whether it exists.
These tests drive the three subscription handlers against moto tables of the deployed shape and a
moto SNS, with two same-id assets in databases A and B:

* a subscription to A's asset is A's alone: its row, its topic, its check, and its removal;
* the listing reads each distinct asset once by both ids, returns each row with its database, drops
  a row the caller may not read or whose asset is gone, and reads again a key a batch read left
  unprocessed rather than dropping it;
* a caller refused on database B gets the same 403 for an asset that exists there and one that does
  not, on every route, and the Tier 2 object carries database B;
* `databaseId` is required and validated on every write and on the check.
"""

import json

import boto3
import pytest
from moto import mock_aws

from backend.backend.common import dynamodb as real_dynamodb
from backend.backend.handlers.subscription import checkSubscriptionService as check_svc
from backend.backend.handlers.subscription import subscriptionService as svc
from backend.backend.handlers.subscription import unsubscribeService as unsub_svc

REGION = "us-east-1"
SUBSCRIPTIONS = "subscriptionsStorageTableV2"
ASSETS = "assetStorageTable"
USERS = "userStorageTable"
DB_A, DB_B = "database-a", "database-b"
SHARED_ID = "xshared-asset"
MISSING_ID = "xmissing-asset-4c1d"
EVENT = "Asset Version Change"
ALICE, BOB = "alice@example.com", "bob@example.com"


class _Enforcer:
    """Tier 1 allows; Tier 2 allows the databases in `allowed`, by the object's database id."""

    allowed = {DB_A, DB_B}
    objects = []

    def __init__(self, claims_and_roles):
        pass

    def enforceAPI(self, event):
        return True

    def enforce(self, obj, action=None):
        _Enforcer.objects.append(dict(obj))
        return obj.get("databaseId") in _Enforcer.allowed


@pytest.fixture
def aws(monkeypatch):
    with mock_aws():
        resource = boto3.resource("dynamodb", region_name=REGION)
        client = boto3.client("dynamodb", region_name=REGION)
        sns = boto3.client("sns", region_name=REGION)
        resource.create_table(
            TableName=SUBSCRIPTIONS, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "eventName", "KeyType": "HASH"},
                       {"AttributeName": "entityName:databaseId:entityId", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "eventName", "AttributeType": "S"},
                                  {"AttributeName": "entityName:databaseId:entityId", "AttributeType": "S"}])
        assets = resource.create_table(
            TableName=ASSETS, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "databaseId", "KeyType": "HASH"},
                       {"AttributeName": "assetId", "KeyType": "RANGE"}],
            AttributeDefinitions=[{"AttributeName": "databaseId", "AttributeType": "S"},
                                  {"AttributeName": "assetId", "AttributeType": "S"}])
        resource.create_table(
            TableName=USERS, BillingMode="PAY_PER_REQUEST",
            KeySchema=[{"AttributeName": "userId", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "userId", "AttributeType": "S"}])
        for database_id in (DB_A, DB_B):
            assets.put_item(Item={"databaseId": database_id, "assetId": SHARED_ID,
                                  "assetName": f"{database_id} asset"})

        def lookup(database_id, asset_id, include_archived=False):
            row = real_dynamodb.get_asset_record(assets, database_id, asset_id, include_archived)
            return {**row, "object__type": "asset"} if row else None

        _Enforcer.allowed = {DB_A, DB_B}
        _Enforcer.objects = []
        for module in (svc, check_svc, unsub_svc):
            monkeypatch.setattr(module, "dynamodb", resource)
            monkeypatch.setattr(module, "subscription_table_name", SUBSCRIPTIONS)
            monkeypatch.setattr(module, "request_to_claims", lambda event: {"tokens": [ALICE]})
            monkeypatch.setattr(module, "CasbinEnforcer", _Enforcer)
            monkeypatch.setattr(module, "get_asset_object_from_id", lookup)
            # raising=False: the stand-in is installed whether or not the module imported the helper
            monkeypatch.setattr(module, "asset_authorization_object", real_dynamodb.asset_authorization_object,
                                raising=False)
            monkeypatch.setattr(module, "log_action", lambda *args, **kwargs: None)
        for module in (svc, unsub_svc):
            monkeypatch.setattr(module, "dynamodb_client", client)
            monkeypatch.setattr(module, "sns_client", sns)
            monkeypatch.setattr(module, "asset_table_name", ASSETS)
        monkeypatch.setattr(svc, "user_table_name", USERS)
        monkeypatch.setattr(svc, "batch_get_items", real_dynamodb.batch_get_items, raising=False)
        monkeypatch.setattr(svc, "validate_pagination_info", lambda params: None)
        yield {"resource": resource, "assets": assets, "sns": sns,
               "subscriptions": resource.Table(SUBSCRIPTIONS)}


def _body(database_id=DB_A, asset_id=SHARED_ID, subscribers=(ALICE,)):
    return {"eventName": EVENT, "entityName": "Asset", "entityId": asset_id, "databaseId": database_id,
            "subscribers": list(subscribers)}


def _event(method, path, body=None, query=None):
    event = {"requestContext": {"http": {"method": method, "path": path}},
             "queryStringParameters": query or {"maxItems": "100", "pageSize": "100", "startingToken": None}}
    if body is not None:
        event["body"] = json.dumps(body)
    return event


def _subscribe(database_id=DB_A, subscribers=(ALICE,)):
    return svc.lambda_handler(_event("POST", "/subscriptions", _body(database_id, subscribers=subscribers)), None)


def _check(database_id, user=ALICE, asset_id=SHARED_ID):
    response = check_svc.lambda_handler(_event("POST", "/check-subscription", {
        "userId": user, "assetId": asset_id, "databaseId": database_id}), None)
    return response["statusCode"], json.loads(response["body"])["message"]


def _row(aws, database_id):
    return aws["subscriptions"].get_item(
        Key={"eventName": EVENT, "entityName:databaseId:entityId": f"Asset:{database_id}:{SHARED_ID}"}).get("Item")


def _list(aws):
    response = svc.lambda_handler(_event("GET", "/subscriptions"), None)
    assert response["statusCode"] == 200, response["body"]
    return json.loads(response["body"])["message"]["Items"]


@pytest.mark.unit
class TestOneDatabasesSubscriptionIsItsOwn:
    def test_create_writes_the_named_databases_row_and_topic(self, aws):
        assert _subscribe(DB_A)["statusCode"] == 200
        row = _row(aws, DB_A)
        assert (row["entityName"], row["databaseId"], row["entityId"], row["subscribers"]) == (
            "Asset", DB_A, SHARED_ID, [ALICE])
        assert _row(aws, DB_B) is None
        a_topic = aws["assets"].get_item(Key={"databaseId": DB_A, "assetId": SHARED_ID})["Item"].get("snsTopic")
        b_asset = aws["assets"].get_item(Key={"databaseId": DB_B, "assetId": SHARED_ID})["Item"]
        assert a_topic and "snsTopic" not in b_asset

    def test_check_answers_for_the_named_database_only(self, aws):
        _subscribe(DB_A)
        assert _check(DB_A) == (200, "success")
        assert _check(DB_B) == (200, "Subscription doesn't exists.")
        assert _check(DB_A, user=BOB) == (200, "Subscription doesn't exists.")

    def test_two_databases_keep_separate_subscriber_lists(self, aws):
        _subscribe(DB_A, subscribers=(ALICE,))
        _subscribe(DB_B, subscribers=(BOB,))
        assert _row(aws, DB_A)["subscribers"] == [ALICE]
        assert _row(aws, DB_B)["subscribers"] == [BOB]
        topics = {aws["assets"].get_item(Key={"databaseId": db, "assetId": SHARED_ID})["Item"]["snsTopic"]
                  for db in (DB_A, DB_B)}
        assert len(topics) == 2

    def test_unsubscribe_removes_the_caller_from_the_named_database_only(self, aws):
        _subscribe(DB_A, subscribers=(ALICE,))
        _subscribe(DB_B, subscribers=(ALICE,))
        response = unsub_svc.lambda_handler(_event("DELETE", "/unsubscribe", _body(DB_A)), None)
        assert response["statusCode"] == 200
        assert _row(aws, DB_A)["subscribers"] == []
        assert _row(aws, DB_B)["subscribers"] == [ALICE]

    def test_update_and_delete_act_on_the_named_database_only(self, aws):
        _subscribe(DB_A, subscribers=(ALICE,))
        _subscribe(DB_B, subscribers=(ALICE,))
        updated = svc.lambda_handler(_event("PUT", "/subscriptions", _body(DB_B, subscribers=(ALICE, BOB))), None)
        assert updated["statusCode"] == 200
        assert _row(aws, DB_B)["subscribers"] == [ALICE, BOB] and _row(aws, DB_A)["subscribers"] == [ALICE]
        deleted = svc.lambda_handler(_event("DELETE", "/subscriptions", _body(DB_B)), None)
        assert deleted["statusCode"] == 200
        assert _row(aws, DB_B) is None and _row(aws, DB_A)["subscribers"] == [ALICE]
        assert "snsTopic" in aws["assets"].get_item(Key={"databaseId": DB_A, "assetId": SHARED_ID})["Item"]

    def test_deleting_a_subscription_the_database_does_not_have_is_400(self, aws):
        _subscribe(DB_A)
        response = svc.lambda_handler(_event("DELETE", "/subscriptions", _body(DB_B)), None)
        assert response["statusCode"] == 400
        assert _row(aws, DB_A)["subscribers"] == [ALICE]


@pytest.mark.unit
class TestTheListing:
    def test_rows_list_with_their_databases(self, aws):
        _subscribe(DB_A)
        _subscribe(DB_B, subscribers=(BOB,))
        items = sorted(_list(aws), key=lambda item: item["databaseId"])
        assert [(i["databaseId"], i["entityId"], i["subscribers"], i["entityValue"]) for i in items] == [
            (DB_A, SHARED_ID, [ALICE], f"{DB_A} asset"), (DB_B, SHARED_ID, [BOB], f"{DB_B} asset")]

    def test_rows_the_caller_may_not_read_are_dropped(self, aws):
        _subscribe(DB_A)
        _subscribe(DB_B)
        _Enforcer.allowed = {DB_A}
        assert [i["databaseId"] for i in _list(aws)] == [DB_A]

    def test_a_row_whose_asset_is_gone_is_dropped(self, aws):
        _subscribe(DB_A)
        _subscribe(DB_B)
        aws["assets"].delete_item(Key={"databaseId": DB_B, "assetId": SHARED_ID})
        assert [i["databaseId"] for i in _list(aws)] == [DB_A]

    def test_a_key_the_batch_read_left_unprocessed_is_read_again(self, aws, monkeypatch):
        _subscribe(DB_A)
        _subscribe(DB_B)

        def batch_leaving_b_unprocessed(resource, table_name, keys, **kwargs):
            rows, unresolved = real_dynamodb.batch_get_items(resource, table_name, keys, **kwargs)
            kept = [row for row in rows if row["databaseId"] != DB_B]
            return kept, unresolved + [{"databaseId": DB_B, "assetId": SHARED_ID}]

        monkeypatch.setattr(svc, "batch_get_items", batch_leaving_b_unprocessed)
        assert sorted(i["databaseId"] for i in _list(aws)) == [DB_A, DB_B]


ROUTES = [
    pytest.param(lambda body: svc.lambda_handler(_event("POST", "/subscriptions", body), None), id="create"),
    pytest.param(lambda body: svc.lambda_handler(_event("PUT", "/subscriptions", body), None), id="update"),
    pytest.param(lambda body: svc.lambda_handler(_event("DELETE", "/subscriptions", body), None), id="delete"),
    pytest.param(lambda body: unsub_svc.lambda_handler(_event("DELETE", "/unsubscribe", body), None),
                 id="unsubscribe"),
    pytest.param(lambda body: check_svc.lambda_handler(_event("POST", "/check-subscription", {
        "userId": ALICE, "assetId": body["entityId"],
        **({"databaseId": body["databaseId"]} if "databaseId" in body else {})}), None), id="check"),
]


@pytest.mark.unit
class TestAuthorizationBeforeExistence:
    @pytest.mark.parametrize("call", ROUTES)
    def test_present_and_missing_answer_alike(self, aws, call):
        _subscribe(DB_B)
        _Enforcer.allowed = {DB_A}
        _Enforcer.objects = []
        present = call(_body(DB_B, SHARED_ID))
        missing = call(_body(DB_B, MISSING_ID))
        assert present["statusCode"] == missing["statusCode"] == 403
        assert present["body"] == missing["body"]
        assert SHARED_ID not in present["body"] and DB_B not in present["body"]
        assert {obj["databaseId"] for obj in _Enforcer.objects} == {DB_B}
        assert _row(aws, DB_B)["subscribers"] == [ALICE]

    @pytest.mark.parametrize("call", ROUTES)
    def test_an_allowed_caller_learns_a_missing_asset(self, aws, call):
        response = call(_body(DB_A, MISSING_ID))
        assert response["statusCode"] == 404


@pytest.mark.unit
class TestTheDatabaseIdIsRequired:
    @pytest.mark.parametrize("call", ROUTES)
    @pytest.mark.parametrize("database_id", [None, "", "bad#db", "a"], ids=["absent", "empty", "hash", "short"])
    def test_a_request_without_a_valid_database_id_is_400(self, aws, call, database_id):
        body = _body(DB_A)
        if database_id is None:
            body.pop("databaseId")
        else:
            body["databaseId"] = database_id
        response = call(body)
        assert response["statusCode"] == 400
        assert _Enforcer.objects == []


@pytest.mark.unit
class TestLookupFailures:
    def test_a_failed_asset_read_on_check_is_400_not_500(self, aws, monkeypatch):
        def failing(database_id, asset_id, include_archived=False):
            raise check_svc.VAMSGeneralErrorResponse("Error retrieving asset.")

        monkeypatch.setattr(check_svc, "get_asset_object_from_id", failing)
        status, message = _check(DB_A)
        assert status == 400 and message.endswith("Error retrieving asset.")

    def test_create_for_an_asset_archived_meanwhile_is_reported(self, aws, monkeypatch):
        # The handler's own read finds the asset; the SNS step's read no longer does.
        monkeypatch.setattr(svc, "get_asset", lambda database_id, asset_id: None)
        response = _subscribe(DB_A)
        assert response["statusCode"] == 400
        assert json.loads(response["body"])["message"].endswith("Asset could not be resolved for this subscription.")

    def test_the_sns_cleanup_skips_an_asset_that_is_no_longer_live(self, aws):
        _subscribe(DB_A)
        aws["assets"].delete_item(Key={"databaseId": DB_A, "assetId": SHARED_ID})
        assert svc.delete_sns_subscriptions(DB_A, SHARED_ID, [ALICE]) is None
        assert unsub_svc.delete_sns_subscriptions(DB_A, SHARED_ID, [ALICE]) is None


class _KeyedOnlyTable:
    """get_item from a dict keyed (databaseId, assetId); a query or scan fails the test."""

    def __init__(self, rows):
        self.rows = rows
        self.keys_read = []

    def get_item(self, Key, **kwargs):
        self.keys_read.append((Key["databaseId"], Key["assetId"]))
        row = self.rows.get((Key["databaseId"], Key["assetId"]))
        return {"Item": dict(row)} if row else {}

    def query(self, **kwargs):
        raise AssertionError("the SNS step's asset read must be a keyed get_item, not a query")

    def scan(self, **kwargs):
        raise AssertionError("the SNS step's asset read must be a keyed get_item, not a scan")


class _KeyedOnlyResource:
    def __init__(self, table):
        self.table = table

    def Table(self, name):
        return self.table


@pytest.mark.unit
@pytest.mark.parametrize("module", [svc, unsub_svc], ids=["subscriptionService", "unsubscribeService"])
class TestTheSnsStepReadsTheLiveAssetOfTheNamedDatabase:
    """The topic is read from the asset row of the named database; an archived row is never the asset."""

    def _use(self, monkeypatch, module, rows):
        table = _KeyedOnlyTable(rows)
        monkeypatch.setattr(module, "dynamodb", _KeyedOnlyResource(table))
        return table

    def test_the_live_rows_topic_is_returned(self, monkeypatch, module):
        table = self._use(monkeypatch, module, {
            (DB_A, SHARED_ID): {"databaseId": DB_A, "assetId": SHARED_ID, "snsTopic": "topic-a"},
            (DB_B, SHARED_ID): {"databaseId": DB_B, "assetId": SHARED_ID, "snsTopic": "topic-b"}})
        assert module.get_asset(DB_B, SHARED_ID) == {"databaseId": DB_B, "snsTopic": "topic-b"}
        assert table.keys_read == [(DB_B, SHARED_ID)]

    def test_an_asset_with_no_topic_has_none(self, monkeypatch, module):
        self._use(monkeypatch, module, {(DB_A, SHARED_ID): {"databaseId": DB_A, "assetId": SHARED_ID}})
        assert module.get_asset(DB_A, SHARED_ID) == {"databaseId": DB_A}

    def test_an_archived_partition_row_is_not_the_asset(self, monkeypatch, module):
        table = self._use(monkeypatch, module, {
            (f"{DB_A}#deleted", SHARED_ID): {"databaseId": f"{DB_A}#deleted", "assetId": SHARED_ID,
                                             "snsTopic": "topic-a"}})
        assert module.get_asset(DB_A, SHARED_ID) is None
        assert table.keys_read == [(DB_A, SHARED_ID)]

    def test_a_row_marked_archived_is_not_the_asset(self, monkeypatch, module):
        self._use(monkeypatch, module, {(DB_A, SHARED_ID): {"databaseId": DB_A, "assetId": SHARED_ID,
                                                            "status": "archived", "snsTopic": "topic-a"}})
        assert module.get_asset(DB_A, SHARED_ID) is None

    def test_cleanup_for_an_asset_that_is_not_live_touches_no_topic(self, monkeypatch, module):
        self._use(monkeypatch, module, {})
        sns = MagicSns()
        monkeypatch.setattr(module, "sns_client", sns)
        assert module.delete_sns_subscriptions(DB_A, SHARED_ID, [ALICE], delete_sns=True) is None
        assert sns.calls == []

    def test_cleanup_for_an_asset_without_a_topic_touches_no_topic(self, monkeypatch, module):
        self._use(monkeypatch, module, {(DB_A, SHARED_ID): {"databaseId": DB_A, "assetId": SHARED_ID}})
        sns = MagicSns()
        monkeypatch.setattr(module, "sns_client", sns)
        assert module.delete_sns_subscriptions(DB_A, SHARED_ID, [ALICE], delete_sns=True) is None
        assert sns.calls == []


class MagicSns:
    """Records any SNS call; the cleanup paths above must make none."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def call(**kwargs):
            self.calls.append(name)
            return {"Subscriptions": []}
        return call


@pytest.mark.unit
class TestHandlerOutcomes:
    def test_unsubscribe_answers_200_when_the_asset_left_before_the_sns_step(self, aws, monkeypatch):
        _subscribe(DB_A)
        monkeypatch.setattr(unsub_svc, "get_asset", lambda database_id, asset_id: None)
        response = unsub_svc.lambda_handler(_event("DELETE", "/unsubscribe", _body(DB_A)), None)
        assert response["statusCode"] == 200
        assert _row(aws, DB_A)["subscribers"] == []

    @pytest.mark.parametrize("call", ROUTES)
    def test_no_identity_is_refused_before_the_asset_read(self, aws, monkeypatch, call):
        for module in (svc, check_svc, unsub_svc):
            monkeypatch.setattr(module, "request_to_claims", lambda event: {"tokens": []})
            monkeypatch.setattr(module, "get_asset_object_from_id", lambda *args, **kwargs: (
                (_ for _ in ()).throw(AssertionError("asset read before the identity check"))))
        response = call(_body(DB_A))
        assert response["statusCode"] == 403
        assert _Enforcer.objects == []

    @pytest.mark.parametrize("call", ROUTES)
    def test_a_route_tier1_refuses_is_403(self, aws, monkeypatch, call):
        class _RouteDenied(_Enforcer):
            def enforceAPI(self, event):
                return False

        for module in (svc, check_svc, unsub_svc):
            monkeypatch.setattr(module, "CasbinEnforcer", _RouteDenied)
        response = call(_body(DB_A))
        assert response["statusCode"] == 403
        assert json.loads(response["body"])["message"] == "Not Authorized"
        assert _Enforcer.objects == []

    def test_an_entity_other_than_an_asset_is_400(self, aws):
        body = dict(_body(DB_A), entityName="Database")
        response = svc.lambda_handler(_event("POST", "/subscriptions", body), None)
        assert response["statusCode"] == 400
        assert "EntityName provided not supported" in json.loads(response["body"])["message"]

    def test_an_unexpected_failure_is_500_without_detail(self, aws, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("table gone: secret-detail")

        monkeypatch.setattr(svc, "create_subscription", boom)
        response = _subscribe(DB_A)
        assert response["statusCode"] == 500
        assert json.loads(response["body"])["message"] == "Internal Server Error"
