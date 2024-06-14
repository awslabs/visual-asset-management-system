# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
import json
from handlers.authn import request_to_claims
import boto3
import os
from customLogging.logger import safeLogger
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEvent
from urllib.parse import urlparse
from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from common.validators import validate
from common import get_ssm_parameter_value
from handlers.authz import CasbinEnforcer

logger = safeLogger(service="Search")
claims_and_roles = {}

try:
    asset_table = os.environ['ASSET_STORAGE_TABLE_NAME']

except Exception as e:
    logger.exception("Failed loading environment variables")
    raise

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

def get_unique_mapping_fields(mapping):
    ignorePropertiesFields = ["_rectype"] #exclude these exact fields from search
    ignorePropertiesFieldPrefixes = ["num_", "date_", "geo_"] #exclude these field prefixes from search

    arr = []
    if "mappings" in mapping and "properties" in mapping["mappings"]:
        for k, v in mapping["mappings"].get("properties", {}).items():
            #if key field not in the ignorePropertiesFields array and key field does not start with any of the prefixes in ignorePropertiesFieldPrefixes, add it to the output field array
            if k not in ignorePropertiesFields and not any(k.startswith(prefix) for prefix in ignorePropertiesFieldPrefixes):
                arr.append(str(k))

    return arr

def token_to_criteria(token):

    if token.get("propertyKey") is None or token.get("propertyKey") == "all":
        return {
            "multi_match": {
                "query": token.get("value"),
                "type": "best_fields",
                "lenient": True,
            }
        }

    else:
        return {
            "match": {
                token.get("propertyKey"): token.get("value")
            }
        }


def property_token_filter_to_opensearch_query(token_filter, uniqueMappingFieldsForGeneralQuery = [], start=0, size=100):
    """
    Converts a property token filter to an OpenSearch query.
    """
    must_operators = ["=", ":", None]
    must_not_operators = ["!=", "!:"]

    must_criteria = []
    must_not_criteria = []
    filter_criteria = []
    should_criteria = []

    #Add token field if not already added
    if 'tokens' not in token_filter:
        token_filter['tokens'] = []

    #Add filter token to ignore #delete databaseId entries
    token_filter["tokens"].append({"operator":"!=","propertyKey":"str_databaseid","value":"#deleted"})

    #Add properly formatted tokens
    if len(token_filter.get("tokens", [])) > 0:
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
            
            
    #If we have a general search query from the textbar, add that.
    if token_filter.get("query"):
        logger.info("Text field search provided... adding filter")
        for field in uniqueMappingFieldsForGeneralQuery:
            should_criteria.append({
                "wildcard": {
                    field: {
                        "value": "*" + token_filter.get("query") + "*",
                        "case_insensitive": True
                    }
                }
            })

        
    #Add the filters criteria
    filter_criteria.extend(token_filter.get("filters", []))

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
                "str_*": {},
                "list_*": {}
            },
            "fragment_size": 2147483647
        },
        "aggs": {
            "str_assettype": {
                "terms": {
                    "field": "str_assettype.raw",
                    "size": 1000
                }
            },
            "str_fileext": {
                "terms": {
                    "field": "str_fileext.raw",
                    "size": 1000
                }
            },
            "str_databaseid": {
                "terms": {
                    "field": "str_databaseid.raw",
                    "size": 1000
                }
            },
            "list_tags": {
                "terms": {
                    "field": "list_tags.keyword",
                    "size": 1000
                }
            }
        }
    }

    #filters results that are 0 score (no relevancy) when doing a general search
    if token_filter.get("query"):
        query["min_score"] = "0.01"


    return query

class SearchAOS():
    def __init__(self, host, auth, indexName):
        self.client = OpenSearch(
            hosts=[{'host': urlparse(host).hostname, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=20
        )
        self.indexName = indexName

    @staticmethod
    def from_env(env=os.environ):
        logger.info(env.get("AOS_ENDPOINT_PARAM"))
        logger.info(env.get("AOS_INDEX_NAME_PARAM"))
        logger.info(env.get("AWS_REGION"))
        region = env.get('AWS_REGION')
        service = env.get('AOS_TYPE')  # aoss (serverless) or es (provisioned)
        aos_disabled = env.get('AOS_DISABLED')


        if aos_disabled == "true":
            return
        else:
            credentials = boto3.Session().get_credentials()
            auth = AWSV4SignerAuth(credentials, region, service)
            host = get_ssm_parameter_value('AOS_ENDPOINT_PARAM', region, env)
            indexName = get_ssm_parameter_value(
                'AOS_INDEX_NAME_PARAM', region, env)
            
            logger.info("AOS endpoint:" + host)
            logger.info("Index endpoint:" + indexName)

            return SearchAOS(
                host=host,
                auth=auth,
                indexName=indexName
            )

    def search(self, query):
        logger.info("aos query")
        logger.info(query)
        return self.client.search(
            body=query,
            index=self.indexName,
        )

    def mapping(self):
        return self.client.indices.get_mapping(
            self.indexName).get(self.indexName)
    


def lambda_handler(
    event: APIGatewayProxyEvent,
    context: LambdaContext,
    search_fn=SearchAOS.from_env,
):
    global claims_and_roles
    aos_disabled = os.environ.get('AOS_DISABLED')

    logger.info("Received event: " + json.dumps(event, indent=2))
    logger.info(event)

    try:
        if "body" not in event and \
                event['requestContext']['http']['method'] == "POST":
            raise ValidationError(400, {"error": "Missing request body for POST"})

        #ABAC Checks
        claims_and_roles = request_to_claims(event)

        operation_allowed_on_asset = False
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if  casbin_enforcer.enforceAPI(event):
                operation_allowed_on_asset = True
                break

        if operation_allowed_on_asset:

            #If AOS not disabled (i.e. OpenSearch is deployed), go the AOS route. Otherwise error
            if aos_disabled == "false":

                search_ao = search_fn()
                #Get's return a mapping for the search index (no actual asset data returned so no ABAC check)
                if event['requestContext']['http']['method'] == "GET":  
                    return {
                        "statusCode": 200,
                        "body": json.dumps(search_ao.mapping()),
                    }
                
                #Load body for POST after taking care of GET
                body = json.loads(event['body'])
                
                #Get unique mapping fields for general query
                uniqueMappingFieldsForGeneralQuery = []
                if body.get("query"):
                    uniqueMappingFieldsForGeneralQuery = get_unique_mapping_fields(search_ao.mapping())

                #get query
                query = property_token_filter_to_opensearch_query(body, uniqueMappingFieldsForGeneralQuery)

                result = search_ao.search(query)
                filtered_hits = []
                for hit in result["hits"]["hits"]:
                    
                    #Exclude if deleted (this is a catch-all and should already be filtered through the input query)
                    if hit["_source"]["str_databaseid"].endswith("#deleted"):
                        continue

                    #Casbin ABAC check
                    hit_document = {
                        "databaseId": hit["_source"]["str_databaseid"],
                        "assetName": hit["_source"]["str_assetname"],
                        "tags": hit["_source"]["list_tags"],
                        "assetType": hit["_source"]["str_assettype"],
                        "object__type": "asset" #for the purposes of checking ABAC, this should always be type "asset" until ABAC is implemented with asset files object types
                    }

                    for user_name in claims_and_roles["tokens"]:
                        casbin_enforcer = CasbinEnforcer(user_name)
                        if casbin_enforcer.enforce(f"user::{user_name}", hit_document, "GET"):
                            filtered_hits.append(hit)
                            break

                result["hits"]["hits"] = filtered_hits
                result["hits"]["total"]["value"] = len(filtered_hits)

                return {
                    'statusCode': 200,
                    'body': json.dumps(result)
                }
            else:
                return {
                    'statusCode': 400,
                    'body': json.dumps({"message": 'Search is not available when OpenSearch feature is not enabled. '})
                }

        else:
            return {
                'statusCode': 403,
                'body': json.dumps({"message": 'Not Authorized'})
            }
    except ValidationError as ex:
        return {
            'statusCode': ex.code,
            'body': json.dumps(ex.resp)
        }
    except Exception as e:
        logger.exception(e)
        return {
            'statusCode': 500,
            'body': json.dumps({"message":'Internal Server Error'})
        }
