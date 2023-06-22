# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
import traceback
from backend.logging.logger import safeLogger
from backend.common.dynamodb import to_update_expr
from boto3.dynamodb.conditions import Key, Attr
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.data_classes import APIGatewayProxyEvent
from decimal import Decimal
from urllib.parse import urlparse

import re

from opensearchpy import OpenSearch, RequestsHttpConnection, AWSV4SignerAuth
from boto3.dynamodb.types import TypeDeserializer

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
#   { "title" : "Interstellar", "director" : "Christopher Nolan", "year" : "2014"} 
#   { "create" : { "_index" : "my-dsl-index", "_id" : "3" } }
#   { "title" : "Star Trek Beyond", "director" : "Justin Lin", "year" : "2015"} 
#   { "update" : {"_id" : "3", "_index" : "my-dsl-index" } } 
#   { "doc" : {"year" : "2016"} }'
# """
# strip whitespace from each line
# movies = "\n".join([line.strip() for line in movies.split('\n')])
#
# client.bulk(movies)



class AOSSIndexAssetMetadata():

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
        print("env", env.get("METADATA_STORAGE_TABLE_NAME"))
        print("env", env.get("ASSET_STORAGE_TABLE_NAME"))
        print("env", env.get("DATABASE_STORAGE_TABLE_NAME"))
        print("env", env.get("AOSS_ENDPOINT"))
        print("env", env.get("AWS_REGION"))
        region = env.get('AWS_REGION')
        service = 'aoss'
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region, service)
        return AOSSIndexAssetMetadata(host=env.get('AOSS_ENDPOINT'), region=region, service=service, auth=auth)


    @staticmethod
    def _determine_field_type(data):

        if data is None:
            return "str"

        j = re.compile(r"^{.*}$")
        dt = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        bool = re.compile(r"^true$|^false$")
        # regex that matches int or float
        num = re.compile(r"^\d+$|^\d+\.\d+$")

        if isinstance(data, Decimal):
            return "num"

        if j.match(data):

            try:
                j = json.loads(data)
                if "loc" in j and "polygons" in j and "FeatureCollection" == j["polygons"].get("type"):
                    return "geo_point_and_polygon"

                return "json"
            except:
                pass

        if dt.match(data):
            return "date"

        if bool.match(data):
            return "bool"

        if num.match(data):
            return "num"

        return "str"

    @staticmethod
    def _determine_field_name(field, data):

        if data is None or field is None:
            return None

        field_name = field.lower().replace(" ", "_")
        data_type = AOSSIndexAssetMetadata._determine_field_type(data)
        if data_type == "geo_point_and_polygon":
            j = json.loads(data)
            return [
                ("gp_{name}".format(name=field_name), {
                    "lon": float(j['loc'][0]),
                    "lat": float(j['loc'][1]),
                }), 
                ("gs_{name}".format(name=field_name), {
                    "type": "polygon",
                    "coordinates": j['polygons']['features'][0]['geometry']['coordinates']
                }),
            ]

        # convert field to lower case and replace spaces with underscores
        def _data_conv():

            if isinstance(data, Decimal):
                return float(data)

            if data_type == "num":
                # determine int or float
                if "." in data:
                    return float(data)
                else:
                    return int(data)
            elif data_type == "bool":
                return data == "true"
            else: return data

        return [("{data_type}_{name}".format(name=field_name, data_type=data_type), _data_conv() )]

    @staticmethod
    def _process_item(item):
        deserialize = TypeDeserializer().deserialize
        result = {
            x : y 
            for k, v in item["dynamodb"]["NewImage"].items()
            for x, y in AOSSIndexAssetMetadata._determine_field_name(k, deserialize(v)) 
        }
        result['_rectype'] = 'asset'
        return result

    def process_item(self, item):
        try:
            body = AOSSIndexAssetMetadata._process_item(item)
            return self.client.index(
                index = "assets1236",
                body = body,
                id = item['dynamodb']['Keys']['assetId']['S'],
                # refresh = True,
            )
        except Exception as e:
            print("failed input", item)
            traceback.print_exc()
            raise e

    def delete_item(self, assetId):
        return self.client.delete(
            index = "assets1236",
            id = assetId,
        )

def lambda_handler(event, context, index=AOSSIndexAssetMetadata.from_env):

    print("the event", event)
    client = index()

    # 'eventName': 'REMOVE'
    if event.get("eventName") == "REMOVE":
        client.delete_item(event['dynamodb']['Keys']['assetId']['S'])
        return
    else:
        print("not a remove record")

    if 'Records' not in event:
        return

    for record in event['Records']:
        print("record", record)
        if record['eventName'] == 'REMOVE':
            client.delete_item(record['dynamodb']['Keys']['assetId']['S'])
            continue
        try:
            print(client.process_item(record))
        except Exception as e:
            print("error", e)
            traceback.print_exc()
            raise e

