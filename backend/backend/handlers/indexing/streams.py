# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
import traceback
from backend.logging.logger import safeLogger
from backend.common.dynamodb import to_update_expr
# from backend.handlers.metadata.read import get_metadata_with_prefix
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


class ValidationError(Exception):
    def __init__(self, code: int, resp: object) -> None:
        self.code = code
        self.resp = resp


class MetadataTable():

    def __init__(self, table):
        self.table = table
        pass

    @staticmethod
    def from_env(env=os.environ):
        tableName = env.get("METADATA_STORAGE_TABLE_NAME")
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(tableName)
        return MetadataTable(table)

    def generate_prefixes(self, path):
        prefixes = []
        parts = path.split('/')
        for i in range(1, len(parts)):
            prefix = '/'.join(parts[:i]) + '/'
            prefixes.insert(0, prefix)

        if(not path.endswith('/')):
            prefixes.insert(0, path)
        return prefixes

    def get_metadata_with_prefix(self, databaseId, assetId, prefix):
        result = {}
        if prefix is not None:
            for paths in self.generate_prefixes(prefix):
                resp = self.table.get_item(
                    Key={
                        "databaseId": databaseId,
                        "assetId": paths,
                    }
                )
                if "Item" in resp:
                    result = resp['Item'] | result
            try:
                asset_metadata = self.get_metadata(databaseId, assetId)
                result = asset_metadata | result
                return result
            except ValidationError as ex:
                return result
        else:
            return self.get_metadata(databaseId, assetId)

    def get_metadata(self, databaseId, assetId):
        resp = self.table.get_item(
            Key={
                "databaseId": databaseId,
                "assetId": assetId,
            }
        )
        if "Item" not in resp:
            raise ValidationError(404, "Item Not Found")
        return resp['Item']


class AOSSIndexS3Objects():
    def __init__(self, bucketName, s3client, aosclient, metadataTable=MetadataTable.from_env):
        self.bucketName = bucketName
        self.s3client = s3client
        self.aosclient = aosclient
        self.metadataTable = metadataTable()

    @staticmethod
    def from_env(env=os.environ):
        bucketName = env.get("ASSET_BUCKET_NAME")
        s3client = boto3.client('s3')
        region = env.get('AWS_REGION')
        service = 'aoss'
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region, service)
        ssm = boto3.client('ssm', region_name=region)
        param = ssm.get_parameter(
            Name=env.get('AOSS_ENDPOINT_PARAM'),
            WithDecryption=False
        )
        host = param.get("Parameter", {}).get("Value")
        aosclient = OpenSearch(
            hosts=[{'host': urlparse(host).hostname, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=20
        )
        return AOSSIndexS3Objects(bucketName, s3client, aosclient)
        
    def _get_s3_object_keys_generator(self, prefix):
        paginator = self.s3client.get_paginator('list_objects')
        page_iterator = paginator.paginate(Bucket=self.bucketName, Prefix=prefix)
        for page in page_iterator:
            for obj in page['Contents']:
                yield obj

    @staticmethod
    def _metadata_and_s3_object_to_opensearch(s3object, metadata):
        s3object['LastModified'] = s3object['LastModified'].strftime("%Y-%m-%d")
        del s3object['Owner']
        s3object['fileext'] = s3object['Key'].split('.')[-1]
        
        print("s3object", s3object)
        print("metadata", metadata)
        result = {
            x : y
            for k, v in (s3object | metadata).items()
            for x, y in AOSSIndexAssetMetadata._determine_field_name(k, v) 
        }
        result['_rectype'] = 's3object'
        print("aoss s3", result)
        return result

    def get_asset_fields(self, databaseId, assetId):
        ddb = boto3.resource("dynamodb")
        table = ddb.Table(os.environ.get("ASSET_STORAGE_TABLE_NAME"))

        result = table.get_item(
            Key={
                "assetId": assetId,
                "databaseId": databaseId,
            },
            AttributesToGet=[
                'assetName', "description", "assetType"
            ],
        )

        item = result['Item']
        return item

    def process_item(self, databaseId, assetIdOrPrefix):
        prefix = None
        assetId = assetIdOrPrefix
        if "/" in assetIdOrPrefix:
            prefix = assetIdOrPrefix
            assetId = assetIdOrPrefix.split("/")[0]
        
        asset_fields = self.get_asset_fields(databaseId, assetId)

        for s3object in self._get_s3_object_keys_generator(assetIdOrPrefix):
            metadata = self.metadataTable.get_metadata_with_prefix(databaseId, assetId, prefix)
            metadata = metadata | asset_fields
            aosrecord = self._metadata_and_s3_object_to_opensearch(s3object, metadata) 
            self.aosclient.index(
                index = "assets1236",
                body = aosrecord,
                id = s3object['Key'],
            )
    

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

        ssm = boto3.client('ssm', region_name=region)
        param = ssm.get_parameter(
            Name=env.get('AOSS_ENDPOINT_PARAM'),
            WithDecryption=False
        )
        host = param.get("Parameter", {}).get("Value")

        return AOSSIndexAssetMetadata(host=host, region=region, service=service, auth=auth)


    @staticmethod
    def _determine_field_type(data):

        if data is None:
            return "str"

        j = re.compile(r"^{.*}$")
        dt = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        bool = re.compile(r"^true$|^false$")
        # regex that matches int or float
        num = re.compile(r"^\d+$|^\d+\.\d+$")

        if isinstance(data, Decimal) or isinstance(data, float) or isinstance(data, int):
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
            return []

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

            if data_type == "num" and isinstance(data, str):
                # determine int or float
                if "." in data:
                    return float(data)
                else:
                    return int(data)
            elif data_type == "num" and (isinstance(data, float) or isinstance(data, int)):
                return data
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

def get_asset_fields(keys):
    # 'Keys': {'assetId': {'S': 'xe46db27e-89f2-4602-9bde-e1596c5a43a2'}, 'databaseId': {'S': 'exxon'}},
    client = boto3.client("dynamodb")
    result = client.get_item(
        TableName = os.environ.get("ASSET_STORAGE_TABLE_NAME"),
        Key = keys,
        AttributesToGet=[
            'assetName', "description", "assetType"
        ],
    )
    item = result['Item']
    return item

def lambda_handler(event, context, 
                   index=AOSSIndexAssetMetadata.from_env, 
                   s3index=AOSSIndexS3Objects.from_env, 
                   get_asset_fields_fn=get_asset_fields):

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
        
        record['dynamodb']['NewImage'] = record['dynamodb']['NewImage'] | get_asset_fields_fn(record['dynamodb']['Keys'])
        print("with asset fields", record)
        # if this is metadata at a s3 key prefix rather than an asset, just index the items with that prefix as files    
        try:
            print(client.process_item(record))
            s3index().process_item(record['dynamodb']['Keys']['databaseId']['S'], record['dynamodb']['Keys']['assetId']['S'])
        except Exception as e:
            print("error", e)
            traceback.print_exc()
            raise e
