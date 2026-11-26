# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Runs the REAL inline Python of the S3 asset buckets table populator custom resource (extracted from
# the synthesized template by populateS3AssetBucketsTableIdentity.test.ts) against a fake boto3 and prints
# one JSON object per scenario: how the populator matches stored registrations, pages the scan, retires
# rows that left the configuration and handles two rows for one registration. boto3 and botocore are
# replaced by small fakes so the harness needs only the Python standard library.
import json
import sys
import types

SORT_KEY = "bucketName:baseAssetsPrefix"


class ClientError(Exception):
    def __init__(self, error_response, operation_name):
        self.response = error_response
        self.operation_name = operation_name
        code = error_response.get("Error", {}).get("Code", "")
        super().__init__(f"An error occurred ({code}) when calling the {operation_name} operation")


class FakeKey:
    def __init__(self, name):
        self.name = name

    def eq(self, value):
        return ("eq", self.name, value)


class FakeBucketsTable:
    """The buckets table: items keyed (bucketId, sort key), a scan served in pages of `page_size`."""

    def __init__(self, items, page_size=100):
        self.items = {(item["bucketId"], item[SORT_KEY]): dict(item) for item in items}
        self.page_size = page_size
        self.calls = []

    def scan(self, **kwargs):
        self.calls.append({"op": "scan", "start": kwargs.get("ExclusiveStartKey")})
        ordered = sorted(self.items)
        start = 0
        if kwargs.get("ExclusiveStartKey"):
            start_key = kwargs["ExclusiveStartKey"]
            start = ordered.index((start_key["bucketId"], start_key[SORT_KEY])) + 1
        page = ordered[start:start + self.page_size]
        response = {"Items": [dict(self.items[key]) for key in page]}
        if start + self.page_size < len(ordered):
            last = page[-1]
            response["LastEvaluatedKey"] = {"bucketId": last[0], SORT_KEY: last[1]}
        return response

    def put_item(self, Item):
        # Recorded so a case can tell a whole-item replace from a merge of the owned fields.
        self.calls.append({"op": "put", "bucketId": Item["bucketId"], "sortKey": Item[SORT_KEY]})
        self.items[(Item["bucketId"], Item[SORT_KEY])] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues,
                    ConditionExpression=None):
        # A conditional update is the retire update (the row must still exist); an unconditional one is
        # the merge write of a configured row, which creates the row when it is missing.
        op = "retire" if ConditionExpression else "write"
        self.calls.append({"op": op, "bucketId": Key["bucketId"], "sortKey": Key[SORT_KEY]})
        key = (Key["bucketId"], Key[SORT_KEY])
        if key not in self.items:
            if ConditionExpression:
                raise ClientError({"Error": {"Code": "ConditionalCheckFailedException"}}, "UpdateItem")
            self.items[key] = {"bucketId": Key["bucketId"], SORT_KEY: Key[SORT_KEY]}
        apply_update(self.items[key], UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues)

    def delete_item(self, **kwargs):
        self.calls.append({"op": "delete"})
        raise AssertionError("the populator never deletes a registration row")


def apply_update(item, expression, names, values):
    """Apply a `SET #a = :a, ... [REMOVE #b, ...]` update expression to an item, as DynamoDB does."""
    set_part, _, remove_part = expression.partition(" REMOVE ")
    if not set_part.startswith("SET "):
        raise AssertionError(f"unexpected update expression: {expression}")
    for assignment in set_part[len("SET "):].split(", "):
        name, value = assignment.split(" = ")
        item[names[name]] = values[value]
    for name in remove_part.split(", ") if remove_part else []:
        item.pop(names[name], None)


class FakeAssetTable:
    """The asset table: only Query on the bucketId index is served; anything else fails the case."""

    def __init__(self, referenced, fail=False):
        self.referenced = set(referenced)
        self.fail = fail
        self.calls = []

    def query(self, IndexName, KeyConditionExpression, Limit=None):
        _, name, value = KeyConditionExpression
        self.calls.append({"index": IndexName, "key": name, "value": value, "limit": Limit})
        if self.fail:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "Query")
        return {"Items": [{"bucketId": value, "assetId": "a1"}] if value in self.referenced else []}

    def scan(self, **kwargs):
        raise AssertionError("the populator never scans the asset table")


class FakeS3:
    def get_bucket_versioning(self, Bucket):
        return {"Status": "Enabled"}

    def get_bucket_accelerate_configuration(self, Bucket):
        return {"Status": "Enabled"}


def row(bucket_id, name, prefix, sort_prefix=None, **extra):
    item = {
        "bucketId": bucket_id,
        SORT_KEY: f"{name}:{prefix if sort_prefix is None else sort_prefix}",
        "bucketName": name,
        "baseAssetsPrefix": prefix,
        "isDefault": False,
    }
    item.update(extra)
    return item


def bucket(name, prefix, default=False, **extra):
    entry = {"bucketName": name, "prefix": prefix, "bucketRegion": "us-east-1", "isDefault": default,
             "tagSyncedObjects": True}
    entry.update(extra)
    return entry


def run_case(source, stored, buckets, request_type="Update", referenced=(), asset_fail=False,
             page_size=100, with_asset_table=True):
    buckets_table = FakeBucketsTable(stored, page_size)
    asset_table = FakeAssetTable(referenced, asset_fail)

    class FakeResource:
        def Table(self, name):
            return asset_table if name == "assets" else buckets_table

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.resource = lambda *args, **kwargs: FakeResource()
    fake_boto3.client = lambda service, region_name=None, **kwargs: FakeS3()
    fake_dynamodb = types.ModuleType("boto3.dynamodb")
    fake_conditions = types.ModuleType("boto3.dynamodb.conditions")
    fake_conditions.Key = FakeKey
    fake_dynamodb.conditions = fake_conditions
    fake_boto3.dynamodb = fake_dynamodb
    fake_botocore = types.ModuleType("botocore")
    fake_exceptions = types.ModuleType("botocore.exceptions")
    fake_exceptions.ClientError = ClientError
    fake_botocore.exceptions = fake_exceptions
    sys.modules["boto3"] = fake_boto3
    sys.modules["boto3.dynamodb"] = fake_dynamodb
    sys.modules["boto3.dynamodb.conditions"] = fake_conditions
    sys.modules["botocore"] = fake_botocore
    sys.modules["botocore.exceptions"] = fake_exceptions

    namespace = {}
    exec(compile(source, "populator_inline.py", "exec"), namespace)
    properties = {"tableName": "buckets", "buckets": json.dumps(buckets)}
    if with_asset_table:
        properties.update({"assetTableName": "assets", "assetTableBucketIdIndex": "BucketIdGSI"})
    event = {"RequestType": request_type, "ResourceProperties": properties}
    outcome = {"ok": True, "error": None, "data": None}
    try:
        outcome["data"] = namespace["lambda_handler"](event, None).get("Data")
    except Exception as error:  # noqa: BLE001 - the harness reports whatever the handler raised
        outcome = {"ok": False, "error": str(error), "data": None}
    outcome["rows"] = sorted(
        (
            {
                "bucketId": item["bucketId"],
                "sortKey": item[SORT_KEY],
                "baseAssetsPrefix": item.get("baseAssetsPrefix"),
                "isDefault": item.get("isDefault"),
                "retired": item.get("retired"),
                "retiredAt": item.get("retiredAt"),
                "duplicateOfBucketId": item.get("duplicateOfBucketId"),
                "source": item.get("source"),
                "defaultSyncDatabaseId": item.get("defaultSyncDatabaseId"),
                "bucketKmsKeyArn": item.get("bucketKmsKeyArn"),
                "isAccelerated": item.get("isAccelerated"),
            }
            for item in buckets_table.items.values()
        ),
        key=lambda r: (r["bucketId"], r["sortKey"]),
    )
    # Every stored item as it is after the run, for the cases that check an attribute the populator
    # does not own is left in place.
    outcome["items"] = {item["bucketId"] + "|" + item[SORT_KEY]: item for item in buckets_table.items.values()}
    outcome["calls"] = buckets_table.calls
    outcome["assetCalls"] = asset_table.calls
    return outcome


def main():
    source = open(sys.argv[1]).read()
    default_a = row("id-a", "b", "assets/", sort_prefix="assets", isDefault=True)
    default_b = row("id-b", "b", "assets/", isDefault=True)
    results = {
        # Stored before the edit as 'assets' (sort key b:assets); the configuration now says 'assets/'.
        "prefixSpellingChange": run_case(source, [default_a], [bucket("b", "assets/", default=True)]),
        "prefixDoubleSlash": run_case(source, [row("id-a", "b", "assets//")], [bucket("b", "assets")]),
        # The configured registration is on the second page of the scan.
        "pagedScan": run_case(
            source,
            [row("id-1", "b", "one/"), row("id-2", "b", "two/"), row("id-3", "c", "/")],
            [bucket("c", "/", default=True)],
            page_size=1,
        ),
        "newRegistration": run_case(source, [], [bucket("b", "new", default=True)], request_type="Create"),
        "retireRemoved": run_case(
            source,
            [row("id-a", "b", "a/", isDefault=True), row("id-x", "c", "x/", isDefault=True)],
            [bucket("b", "a/", default=True)],
        ),
        "retiredReturns": run_case(
            source,
            [row("id-a", "b", "a/", retired=True, retiredAt="2026-01-01T00:00:00Z")],
            [bucket("b", "a/", default=True)],
        ),
        "retiredStaysRetired": run_case(
            source,
            [row("id-a", "b", "a/", isDefault=True), row("id-x", "c", "x/", retired=True)],
            [bucket("b", "a/", default=True)],
        ),
        "deleteRetiresNothing": run_case(
            source,
            [row("id-a", "b", "a/", isDefault=True), row("id-x", "c", "x/")],
            [bucket("b", "a/", default=True)],
            request_type="Delete",
        ),
        "emptyListRetiresNothing": run_case(source, [row("id-x", "c", "x/")], []),
        # Two rows for one registration (an earlier release minted id-b after the spelling change).
        "duplicatesOneReferenced": run_case(
            source, [default_a, default_b], [bucket("b", "assets/", default=True)], referenced=["id-a"]),
        "duplicatesStickyPrimary": run_case(
            source,
            [row("id-a", "b", "assets/", sort_prefix="assets", isDefault=True),
             row("id-b", "b", "assets/", duplicateOfBucketId="id-a")],
            [bucket("b", "assets/", default=True)],
            referenced=["id-b"],
        ),
        "duplicatesBothReferenced": run_case(
            source, [default_a, default_b], [bucket("b", "assets/", default=True)],
            referenced=["id-a", "id-b"]),
        "duplicatesNoneReferenced": run_case(
            source, [default_a, default_b], [bucket("b", "assets/", default=True)]),
        "duplicatesReferenceReadFails": run_case(
            source, [default_a, default_b], [bucket("b", "assets/", default=True)], asset_fail=True),
        "duplicatesWithoutAssetTable": run_case(
            source, [default_a, default_b], [bucket("b", "assets/", default=True)], with_asset_table=False),
        "duplicatesRetiredTogether": run_case(
            source,
            [default_a, row("id-b", "b", "assets/", duplicateOfBucketId="id-a"), row("id-c", "c", "/")],
            [bucket("c", "/", default=True)],
        ),
        "twoEntriesOnePair": run_case(source, [], [bucket("b", "assets"), bucket("b", "assets/")]),
        # #1978: the configured row is merged, not replaced. An attribute the populator does not own
        # (a later release's layout field) stays; the row gains source, the fallback database and the
        # declared key; owned fields the configuration no longer gives are removed.
        "mergeKeepsUnowned": run_case(
            source,
            [row("id-a", "b", "assets/", isDefault=True, assetLayout="{assetId}", isAccelerated=True,
                 bucketKmsKeyArn="arn:aws:kms:us-east-1:111111111111:key/old")],
            [bucket("b", "assets/", default=True, defaultSyncDatabaseId="db1")],
        ),
        "upgradeGainsFields": run_case(
            source,
            [row("id-a", "b", "assets/", isDefault=True), row("id-r", "c", "/", retired=True,
                                                              retiredAt="2026-01-01T00:00:00Z")],
            [bucket("b", "assets/", default=True, defaultSyncDatabaseId="db1",
                    bucketKmsKeyArn="arn:aws:kms:us-east-1:111111111111:key/k1")],
        ),
        # A registration added at run time is never matched, rewritten or retired.
        "runtimeRowSurvives": run_case(
            source,
            [row("id-a", "b", "a/", isDefault=True),
             row("id-rt", "r", "rt/", source="runtime", status="active", createdBy="admin",
                 defaultSyncDatabaseId="dbr")],
            [bucket("b", "a/", default=True, defaultSyncDatabaseId="db1")],
        ),
        "runtimeRowSamePair": run_case(
            source,
            [row("id-rt", "b", "a/", source="runtime", status="active")],
            [bucket("b", "a/", default=True, defaultSyncDatabaseId="db1")],
        ),
        # A configured prefix that equals or nests with a stored row the configuration does not include
        # fails the deployment before anything is written.
        "nestedInRetired": run_case(
            source,
            [row("id-old", "b", "assets/", retired=True), row("id-c", "c", "/", isDefault=True)],
            [bucket("c", "/", default=True, defaultSyncDatabaseId="db1"),
             bucket("b", "assets/team1/", defaultSyncDatabaseId="db1")],
        ),
        "retiredNestedInConfigured": run_case(
            source,
            [row("id-old", "b", "assets/team1/", retired=True)],
            [bucket("b", "assets/", default=True, defaultSyncDatabaseId="db1")],
        ),
        "retiredRootOverlaps": run_case(
            source,
            [row("id-old", "b", "/", retired=True)],
            [bucket("b", "x/", default=True, defaultSyncDatabaseId="db1")],
        ),
        "nestedInRowBeingRetired": run_case(
            source,
            [row("id-old", "b", "old/", isDefault=True)],
            [bucket("b", "old/new/", default=True, defaultSyncDatabaseId="db1")],
        ),
        "nestedInRuntimeRow": run_case(
            source,
            [row("id-rt", "b", "rt/", source="runtime", status="pending")],
            [bucket("b", "rt/sub/", default=True, defaultSyncDatabaseId="db1")],
        ),
        "siblingPrefixesDoNotOverlap": run_case(
            source,
            [row("id-old", "b", "assets/", retired=True), row("id-o", "o", "assets/team1/", retired=True)],
            [bucket("b", "assets2/", default=True, defaultSyncDatabaseId="db1")],
        ),
        # A configured registration that already has a row is not refused for an overlap that is
        # already stored (a deployment upgraded with it, or a later deployment of the same config).
        "storedOverlapNotRefused": run_case(
            source,
            [row("id-old", "b", "assets/", isDefault=True), row("id-t", "b", "assets/team1/")],
            [bucket("b", "assets/team1/", default=True, defaultSyncDatabaseId="db1")],
        ),
        # The rollback of the deployment nestedInRetired fails: CloudFormation sends the previous
        # properties to the updated function, and they name only pairs the previous deployment wrote.
        "rollbackAfterRefusedAdd": run_case(
            source,
            [row("id-old", "b", "assets/", retired=True), row("id-c", "c", "/", isDefault=True)],
            [bucket("c", "/", default=True, defaultSyncDatabaseId="db1")],
        ),
        "deleteSkipsOverlapCheck": run_case(
            source,
            [row("id-old", "b", "assets/", retired=True)],
            [bucket("b", "assets/team1/", default=True, defaultSyncDatabaseId="db1")],
            request_type="Delete",
        ),
    }
    print(json.dumps(results))


main()
