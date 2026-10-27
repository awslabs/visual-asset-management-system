# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""A failed comment read answers 500 instead of an empty list.

GET /comments/assets/{assetId} and GET /comments/assets/{assetId}/assetVersionId/{assetVersionId}
let an exception from the comment query reach the handler, which answers 500, so a DynamoDB
fault is never reported as 200 {"message": []} ("this asset has no comments").
"""

import json
from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

import backend.backend.handlers.comments.commentService as commentService

ASSET_ID = "asset-1"


class _FailingPaginator:
    def paginate(self, **kwargs):
        raise ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"}},
            "Query",
        )


class _ItemsPaginator:
    def paginate(self, **kwargs):
        result = MagicMock()
        result.build_full_result.return_value = {"Items": [{"commentId": "c1"}]}
        return result


def _resource(paginator):
    resource = MagicMock()
    resource.meta.client.get_paginator.return_value = paginator
    return resource


@pytest.fixture
def allowed(monkeypatch):
    enforcer = MagicMock()
    enforcer.enforceAPI.return_value = True
    enforcer.enforce.return_value = True
    monkeypatch.setattr(commentService, "request_to_claims", lambda event: {"tokens": ["u1"], "roles": []})
    monkeypatch.setattr(commentService, "CasbinEnforcer", MagicMock(return_value=enforcer))
    monkeypatch.setattr(
        commentService, "get_asset_object_from_id",
        lambda database_id, asset_id: {"assetId": asset_id, "databaseId": "db1"},
    )
    monkeypatch.setattr(commentService, "comment_database", "commentStorageTable")


def _event(path_parameters):
    return {
        "requestContext": {"http": {"method": "GET"}},
        "pathParameters": path_parameters,
        "queryStringParameters": {"maxItems": "10", "pageSize": "10", "startingToken": ""},
    }


ROUTES = [
    pytest.param({"assetId": ASSET_ID}, id="asset comments"),
    pytest.param({"assetId": ASSET_ID, "assetVersionId": "1"}, id="asset version comments"),
]


@pytest.mark.unit
class TestCommentListReadFailure:
    @pytest.mark.parametrize("path_parameters", ROUTES)
    def test_read_failure_answers_500(self, allowed, monkeypatch, path_parameters):
        monkeypatch.setattr(commentService, "dynamodb", _resource(_FailingPaginator()))

        response = commentService.lambda_handler(_event(path_parameters), None)

        assert response["statusCode"] == 500
        assert json.loads(response["body"]) == {"message": "Internal Server Error"}

    @pytest.mark.parametrize("path_parameters", ROUTES)
    def test_successful_read_still_returns_the_comments(self, allowed, monkeypatch, path_parameters):
        monkeypatch.setattr(commentService, "dynamodb", _resource(_ItemsPaginator()))

        response = commentService.lambda_handler(_event(path_parameters), None)

        assert response["statusCode"] == 200
        assert json.loads(response["body"]) == {"message": [{"commentId": "c1"}]}
