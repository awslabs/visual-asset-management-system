# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""common.dynamodb.to_update_expr combined with a boto3 condition object in one update_item call.

boto3 renders a condition object (`Attr("x").eq(v)`) with its own `#n<i>` / `:v<i>` placeholders and
merges them into the call's ExpressionAttributeNames / ExpressionAttributeValues, replacing any entry
of the same name. When the helper used `:v<i>` too, the condition's value silently replaced the first
SET value: a conditional workflow redeploy stored the old deploymentRevision (a Number) as
workflow_arn. These tests pin that every SET value survives such a call.
"""

import importlib.util
import os

import pytest
from boto3.dynamodb.conditions import Attr, ConditionExpressionBuilder

# tests/conftest.py replaces `common.dynamodb` with a MagicMock; load the shipped module by path so
# this suite tests the real helper.
_MODULE_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "..", "backend", "common", "dynamodb.py"
))
_spec = importlib.util.spec_from_file_location("real_common_dynamodb_for_update_expr", _MODULE_PATH)
ddb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ddb)

TABLE = "t-update-expr"


@pytest.mark.unit
class TestPlaceholdersDoNotCollide:
    def test_no_placeholder_matches_one_a_condition_object_renders(self):
        record = {f"attr{i}": i for i in range(12)}
        names, values, _expr = ddb.to_update_expr(record)
        condition = Attr("a").exists()
        for i in range(12):
            condition = condition & Attr(f"c{i}").eq(i)
        built = ConditionExpressionBuilder().build_expression(condition)
        assert not set(names) & set(built.attribute_name_placeholders)
        assert not set(values) & set(built.attribute_value_placeholders)


def _table():
    import boto3
    resource = boto3.resource("dynamodb", region_name="us-east-1")
    resource.create_table(
        TableName=TABLE,
        KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST")
    return resource.Table(TABLE)


@pytest.mark.aws
class TestConditionObjectKeepsEverySetValue:
    """Run against moto, so boto3's own condition rendering and placeholder merge are exercised."""

    def test_an_equality_condition_leaves_every_set_value_in_place(self):
        from moto import mock_aws
        with mock_aws():
            table = _table()
            table.put_item(Item={"pk": "row", "first": "old", "revision": 3, "last": "old"})

            names, values, expr = ddb.to_update_expr(
                {"first": "new-first", "revision": 4, "last": "new-last"})
            table.update_item(
                Key={"pk": "row"},
                UpdateExpression=expr,
                ExpressionAttributeNames=names,
                ExpressionAttributeValues=values,
                ConditionExpression=Attr("pk").exists() & Attr("revision").eq(3),
            )

            item = table.get_item(Key={"pk": "row"})["Item"]
            assert item["first"] == "new-first"
            assert item["revision"] == 4
            assert item["last"] == "new-last"

    def test_the_condition_is_still_evaluated(self):
        """Control: a fix that dropped the condition's value would also keep the SET values."""
        import botocore.exceptions
        from moto import mock_aws
        with mock_aws():
            table = _table()
            table.put_item(Item={"pk": "row", "first": "old", "revision": 5})

            names, values, expr = ddb.to_update_expr({"first": "new-first", "revision": 4})
            with pytest.raises(botocore.exceptions.ClientError) as raised:
                table.update_item(
                    Key={"pk": "row"},
                    UpdateExpression=expr,
                    ExpressionAttributeNames=names,
                    ExpressionAttributeValues=values,
                    ConditionExpression=Attr("revision").eq(3),
                )
            assert raised.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
            assert table.get_item(Key={"pk": "row"})["Item"]["first"] == "old"
