# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
# from backend.handlers.auth import request_to_claims
import boto3
import os
import traceback
from backend.logging.logger import safeLogger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEvent
from urllib.parse import urlparse
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth


logger = safeLogger(service=__name__, child=True, level="INFO")
#
# Single doc Example
#
# document = {
#   'title': 'Moneyball',
#   'director': 'Bennett Miller',
#   'year': '2011'
# }
#
# response = client.index(
#     index = 'python-test-index',
#     body = document,
#     id = '1',
#     refresh = True
# )
#
# Bulk indexing example
#
# movies = """
#   { "index" : { "_index" : "my-dsl-index", "_id" : "2" } }
#   { "title" : "Interstellar",
#       "director" : "Christopher Nolan", "year" : "2014"}
#   { "create" : { "_index" : "my-dsl-index", "_id" : "3" } }
#   { "title" : "Star Trek Beyond", "director" : "Justin Lin", "year" : "2015"}
#   { "update" : {"_id" : "3", "_index" : "my-dsl-index" } }
#   { "doc" : {"year" : "2016"} }'
# """
# strip whitespace from each line
# movies = "\n".join([line.strip() for line in movies.split('\n')])
#
# client.bulk(movies)


class ValidationError(Exception):
    def __init__(self, code: int, resp: object) -> None:
        self.code = code
        self.resp = resp


def token_to_criteria(token):

    if token.get("propertyKey") is None or token.get("propertyKey") == "all":
        return {
            "multi_match": {
                "query": token.get("value"),
                "type": "best_fields",
                "lenient": True
            }
        }

    else:
        return {
            "match": {
                token.get("propertyKey"): token.get("value")
            }
        }


def property_token_filter_to_opensearch_query(token_filter, start=0, size=100):
    """
    Converts a property token filter to an OpenSearch query.
    """
    must_operators = ["=", ":", None]
    must_not_operators = ["!=", "!:"]

    must_criteria = []
    must_not_criteria = []
    filter_criteria = []
    should_criteria = []

    if token_filter.get("operation", "AND").upper() == "AND":
        must_criteria = [
            token_to_criteria(tok) for tok in token_filter['tokens']
            if tok['operator'] in must_operators]

        must_not_criteria = [
            token_to_criteria(tok) for tok in token_filter['tokens']
            if tok['operator'] in must_not_operators]

    elif token_filter.get("operation").upper() == "OR":
        must_not_criteria = [
            token_to_criteria(tok) for tok in token_filter['tokens']
            if tok['operator'] in must_not_operators]
        should_criteria = [
            token_to_criteria(tok) for tok in token_filter['tokens']
            if tok['operator'] in must_operators]

    query = {
        "from": token_filter.get("from", start),
        "size": token_filter.get("size", size),
        "sort": token_filter.get("sort", ["_score"]),
        "query": {
            "bool": {
                "must": must_criteria,
                "must_not": must_not_criteria,
                "filter": filter_criteria,
                "should": should_criteria,
            }
        },
        "highlight": {
            "pre_tags": [
                "@opensearch-dashboards-highlighted-field@"
            ],
            "post_tags": [
                "@/opensearch-dashboards-highlighted-field@"
            ],
            "fields": {
                "*": {}
            },
            "fragment_size": 2147483647
        }
    }

    return query


class SearchAOSS():
    def __init__(self, host, auth, region, service):
        self.client = OpenSearch(
            hosts=[{'host': urlparse(host).hostname, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=20
        )

    @staticmethod
    def from_env(env=os.environ):
        print("env endpoint", env.get("AOSS_ENDPOINT"))
        print("env region", env.get("AWS_REGION"))
        region = env.get('AWS_REGION')
        print(region)
        service = 'aoss'
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region, service)
        print(auth)
        return SearchAOSS(
            host=env.get('AOSS_ENDPOINT'),
            region=region,
            service=service,
            auth=auth
        )

    def search(self, query):
        print("aoss query", query)
        return self.client.search(
            body=query,
            index='assets1236'
        )

    def mapping(self):
        return self.client.indices.get_mapping("assets1236")


def lambda_handler(
    event: APIGatewayProxyEvent,
    context: LambdaContext,
    search_fn=SearchAOSS.from_env
):
    logger.info("Received event: " + json.dumps(event, indent=2))
    print("event", event)

    try:

        if event['requestContext']['http']['method'] == "GET":
            search_ao = search_fn()
            return {
                "statusCode": 200,
                "body": json.dumps(search_ao.mapping()),
            }

        if "body" not in event and event['requestContext']['http']['method'] == "POST":
            raise ValidationError(400, {"error": "missing request body"})

        body = json.loads(event['body'])

        search_ao = search_fn()
        result = search_ao.search(
            property_token_filter_to_opensearch_query(body))

        return {
            'statusCode': 200,
            'body': json.dumps(result)
        }
    except ValidationError as ex:
        return {
            'statusCode': 400,
            'body': json.dumps(ex.resp)
        }
    except Exception:
        print("error", traceback.format_exc())
        logger.error(traceback.format_exc())
        return {
            'statusCode': 500,
            'body': json.dumps('Error!')
        }
