# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Runs the REAL inline Python of the S3 asset buckets table populator custom resource (extracted from
# the synthesized template by s3TransferAccelerationPopulator.test.ts) against a fake boto3 and prints
# one JSON object per scenario. boto3 and botocore are replaced by small fakes so the harness needs
# only the Python standard library.
import json
import sys
import types


class ClientError(Exception):
    def __init__(self, error_response, operation_name):
        self.response = error_response
        self.operation_name = operation_name
        code = error_response.get("Error", {}).get("Code", "")
        message = error_response.get("Error", {}).get("Message", "")
        super().__init__(f"An error occurred ({code}) when calling the {operation_name} operation: {message}")


def client_error(code, operation, status=403, message="Access Denied"):
    return ClientError(
        {"Error": {"Code": code, "Message": message}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


class FakeTable:
    def __init__(self):
        self.items = []

    def scan(self):
        return {"Items": []}

    def put_item(self, Item):
        self.items.append(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames, ExpressionAttributeValues,
                    ConditionExpression=None):
        # The populator's merge write: the item it would store is the key plus every SET field.
        item = dict(Key)
        set_part = UpdateExpression.partition(" REMOVE ")[0]
        for assignment in set_part[len("SET "):].split(", "):
            name, value = assignment.split(" = ")
            item[ExpressionAttributeNames[name]] = ExpressionAttributeValues[value]
        self.items.append(item)


class FakeS3:
    def __init__(self, region, accelerate_response, calls):
        self.region = region
        self.accelerate_response = accelerate_response
        self.calls = calls

    def get_bucket_versioning(self, Bucket):
        return {"Status": "Enabled"}

    def get_bucket_accelerate_configuration(self, Bucket):
        self.calls.append({"region": self.region, "bucket": Bucket})
        if isinstance(self.accelerate_response, Exception):
            raise self.accelerate_response
        return self.accelerate_response


def run_case(source, accelerate_response, request_type, buckets):
    table = FakeTable()
    calls = []

    class FakeResource:
        def Table(self, name):
            return table

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.resource = lambda *args, **kwargs: FakeResource()
    fake_boto3.client = lambda service, region_name=None, **kwargs: FakeS3(region_name, accelerate_response, calls)
    fake_botocore = types.ModuleType("botocore")
    fake_exceptions = types.ModuleType("botocore.exceptions")
    fake_exceptions.ClientError = ClientError
    fake_botocore.exceptions = fake_exceptions
    sys.modules["boto3"] = fake_boto3
    sys.modules["botocore"] = fake_botocore
    sys.modules["botocore.exceptions"] = fake_exceptions

    namespace = {}
    exec(compile(source, "populator_inline.py", "exec"), namespace)
    event = {
        "RequestType": request_type,
        "ResourceProperties": {"tableName": "t", "buckets": json.dumps(buckets)},
    }
    outcome = {"ok": True, "error": None, "errorType": None}
    try:
        namespace["lambda_handler"](event, None)
    except Exception as error:  # noqa: BLE001 - the harness reports whatever the handler raised
        outcome = {"ok": False, "error": str(error), "errorType": type(error).__name__}
    outcome["rows"] = [
        {"bucketName": item["bucketName"], "isAccelerated": item.get("isAccelerated")}
        for item in table.items
    ]
    outcome["calls"] = calls
    return outcome


def bucket(name="vams-x", prefix="/", region="us-east-1", accelerated=True):
    entry = {"bucketName": name, "prefix": prefix, "bucketRegion": region}
    if accelerated:
        entry["isAccelerated"] = True
    return entry


def main():
    source = open(sys.argv[1]).read()
    denied = client_error("AccessDenied", "GetBucketAccelerateConfiguration")
    missing = client_error("NoSuchBucket", "GetBucketAccelerateConfiguration", 404, "The specified bucket does not exist")
    throttled = client_error("SlowDown", "GetBucketAccelerateConfiguration", 503, "Slow Down")
    results = {
        "enabled": run_case(source, {"Status": "Enabled"}, "Create", [bucket()]),
        "enabledOnUpdate": run_case(source, {"Status": "Enabled"}, "Update", [bucket()]),
        "suspended": run_case(source, {"Status": "Suspended"}, "Create", [bucket()]),
        "empty": run_case(source, {}, "Create", [bucket()]),
        "accessDenied": run_case(source, denied, "Create", [bucket()]),
        "noSuchBucket": run_case(source, missing, "Update", [bucket()]),
        "otherClientError": run_case(source, throttled, "Create", [bucket()]),
        "crossRegion": run_case(source, {"Status": "Enabled"}, "Create", [bucket(region="us-west-2")]),
        "crossRegionSuspended": run_case(source, {"Status": "Suspended"}, "Create", [bucket(region="eu-west-1")]),
        "notFlagged": run_case(source, {}, "Create", [bucket(accelerated=False)]),
        "deleteSuspended": run_case(source, {"Status": "Suspended"}, "Delete", [bucket()]),
        "deleteDenied": run_case(source, denied, "Delete", [bucket()]),
        "twoPrefixesAgree": run_case(
            source,
            {"Status": "Enabled"},
            "Create",
            [bucket(prefix="a/"), bucket(prefix="b/")],
        ),
    }
    print(json.dumps(results))


main()
