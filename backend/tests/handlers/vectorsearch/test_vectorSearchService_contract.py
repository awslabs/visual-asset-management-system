# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""POST /search/nlp wire contract through botocore Stubber: the exact ``InvokeModel`` and ``SearchVectors``
parameters the real store and embedding adapter send, and the 503 arm when the index is being created.
"""

import json
import os
import sys

from botocore.stub import ANY

import pytest

from tests.handlers.vectorsearch import test_vectorSearchService as behaviour
from tests.vectorStub import invoke_model_response, stubbed_bedrock_runtime, stubbed_dynamodb

MODEL = behaviour.MODEL
DIMENSIONS = 256  # a Titan V2 length; the support env uses a 4-wide index for the indexer suites
TABLE = os.environ["VECTOR_EMBEDDINGS_STORAGE_TABLE_NAME"]
INDEX = os.environ["VECTOR_INDEX_NAME"]

BREADTH_EXPRESSION = (
    "#databaseId = :databaseId AND #isLatest = :isLatest AND #isArchived = :isArchived"
    " AND #embeddingModelId = :embeddingModelId AND #segmentKind = :segmentKind"
)
DEPTH_EXPRESSION = (
    "#databaseId = :databaseId AND #isLatest = :isLatest AND #isArchived = :isArchived"
    " AND #embeddingModelId = :embeddingModelId"
)


def _search_call(expression, names, values, top_k, response=None, error=None):
    call = {
        "method": "search_vectors",
        "expected_params": {
            "TableName": TABLE,
            "IndexName": INDEX,
            "SearchVector": ANY,
            "TopK": top_k,
            "SearchConditionExpression": expression,
            "ExpressionAttributeNames": {f"#{n}": n for n in names},
            "ExpressionAttributeValues": {f":{n}": {"S": v} for n, v in values.items()},
        },
    }
    if error is not None:
        call["error"] = error
    else:
        call["response"] = response if response is not None else {"SearchResults": []}
    return call


def _embed_call():
    return {
        "method": "invoke_model",
        "expected_params": {"modelId": MODEL, "body": ANY, "accept": "application/json", "contentType": "application/json"},
        "response": invoke_model_response({"embedding": [0.1] * DIMENSIONS}),
    }


svc = behaviour.svc  # the behaviour module's fixture: auth surface, embedding and database access replaced


def _run(svc, ddb, bedrock, body):
    """The handler with the real store on the stubbed DynamoDB client and the real adapter on the stubbed
    Bedrock client; ``search_vectors`` runs on worker threads, so the calls are made sequential."""
    svc.embed_text = sys.modules["common.vectorsearch.embeddings"].embed_text
    real_embed = svc.embed_text
    svc.dynamodb_client = ddb
    svc.embed_text = lambda text, **kw: real_embed(text, client=bedrock, **kw)
    svc.SEARCH_WORKERS = 1
    svc.embedding_dimensions = DIMENSIONS
    resp = svc.lambda_handler(behaviour.event(body), None)
    return resp["statusCode"], json.loads(resp["body"])


def test_exact_search_vectors_and_invoke_model_parameters(svc):
    behaviour.rows(svc, ("db1", "a1"))
    breadth_values = {"databaseId": "db1", "isLatest": "true", "isArchived": "false", "embeddingModelId": MODEL, "segmentKind": "none"}
    depth_values = {k: v for k, v in breadth_values.items() if k != "segmentKind"}
    with stubbed_bedrock_runtime([_embed_call()]) as bedrock, stubbed_dynamodb([
        _search_call(BREADTH_EXPRESSION, list(breadth_values), breadth_values, 50),
        _search_call(DEPTH_EXPRESSION, list(depth_values), depth_values, 50),
    ]) as ddb:
        status, body = _run(svc, ddb, bedrock, {"query": "red tractor"})
    assert status == 200
    assert body["hits"]["hits"] == [] and body["nlp"]["databasesSearched"] == 1
    assert body["nlp"]["embeddingModelId"] == MODEL


def test_single_class_filter_and_size_reach_the_wire(svc):
    behaviour.rows(svc, ("db1", "a1"))
    values = {"databaseId": "db1", "isLatest": "true", "isArchived": "false", "fileClass": "mesh", "embeddingModelId": MODEL, "segmentKind": "none"}
    expression = (
        "#databaseId = :databaseId AND #isLatest = :isLatest AND #isArchived = :isArchived AND #fileClass = :fileClass"
        " AND #embeddingModelId = :embeddingModelId AND #segmentKind = :segmentKind"
    )
    with stubbed_bedrock_runtime([_embed_call()]) as bedrock, stubbed_dynamodb([
        _search_call(expression, list(values), values, 10),
    ]) as ddb:
        status, _ = _run(svc, ddb, bedrock, {"query": "tractor", "fileClasses": ["mesh"], "size": 5, "includeSegments": False})
    assert status == 200


def test_index_being_created_is_503(svc):
    values = {"databaseId": "db1", "isLatest": "true", "isArchived": "false", "embeddingModelId": MODEL, "segmentKind": "none"}
    with stubbed_bedrock_runtime([_embed_call()]) as bedrock, stubbed_dynamodb([
        _search_call(BREADTH_EXPRESSION, list(values), values, 50,
                     error={"code": "ValidationException", "message": "Index is being created", "http_status_code": 400}),
    ]) as ddb:
        status, body = _run(svc, ddb, bedrock, {"query": "tractor", "includeSegments": False})
    assert status == 503
    assert body == {"message": "Vector index is being built"}


def test_a_returned_item_becomes_a_hit(svc):
    behaviour.rows(svc, ("db1", "a1"))
    values = {"databaseId": "db1", "isLatest": "true", "isArchived": "false", "embeddingModelId": MODEL, "segmentKind": "none"}
    stored = {
        "databaseId": {"S": "db1"}, "assetId": {"S": "a1"}, "filePath": {"S": "f.glb"}, "versionId": {"S": "v1"},
        "isLatest": {"S": "true"}, "isArchived": {"S": "false"}, "fileClass": {"S": "mesh"}, "fileExt": {"S": "glb"},
        "embeddingModelId": {"S": MODEL}, "segmentKey": {"S": ""}, "segmentKind": {"S": "none"}, "segmentLabel": {"S": ""},
        "segmentStartMs": {"NULL": True}, "segmentEndMs": {"NULL": True}, "fileSize": {"N": "10"},
        "indexedAt": {"S": "2026-09-13T00:00:00Z"}, "sourceModalities": {"L": [{"S": "file-identity"}]},
    }
    with stubbed_bedrock_runtime([_embed_call()]) as bedrock, stubbed_dynamodb([
        _search_call(BREADTH_EXPRESSION, list(values), values, 50, response={"SearchResults": [{"Item": stored, "Score": 0.25}]}),
    ]) as ddb:
        status, body = _run(svc, ddb, bedrock, {"query": "tractor", "includeSegments": False})
    assert status == 200
    hits = body["hits"]["hits"]
    assert len(hits) == 1
    assert hits[0]["_id"] == "db1#a1#f.glb#v1"
    assert hits[0]["_vector"]["distance"] == pytest.approx(0.25)
    assert hits[0]["_source"]["num_filesize"] == 10
