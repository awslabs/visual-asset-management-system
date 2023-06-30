# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from backend.handlers.authn import request_to_claims
from backend.handlers.authz.opensearch import AuthEntities
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


def load_auth_entities(env=os.environ):
    ddb = boto3.resource("dynamodb")
    table = ddb.Table(env.get("AUTH_ENTITIES_TABLE"))
    return AuthEntities(table)


def load_tokens_to_database_set():
    # loading this here enables mocks with unit tests of this module
    from backend.handlers.auth import get_database_set
    return get_database_set


def lambda_handler(
    event: APIGatewayProxyEvent,
    context: LambdaContext,
    search_fn=SearchAOSS.from_env,
    auth_fn=load_auth_entities,
    database_set_fn=load_tokens_to_database_set,

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
        auth_entities = auth_fn()
        all_constraints = auth_entities.all_constraints()
        database_set = database_set_fn()
        print("all constraints", all_constraints)
        authn = request_to_claims(event)
        print("authn", authn)
        auth_filters = auth_entities.claims_to_opensearch_filters(all_constraints, authn['tokens'])
        accessible_databases = database_set(authn['tokens'])
        accessible_databases = "str_databaseid:(%s)" % " OR ".join(accessible_databases)
        print("accessible databases", accessible_databases)
        print("opensearch filters", auth_filters)
        query = property_token_filter_to_opensearch_query(body)
        query['query']['bool']['filter'].append(auth_filters['query'])
        query['query']['bool']['filter'].append({
            'query_string': {
                'query': accessible_databases
            }
        })
        result = search_ao.search(query)

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
