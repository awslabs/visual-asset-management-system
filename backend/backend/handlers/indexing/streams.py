# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
import traceback
from decimal import Decimal
from urllib.parse import urlparse
import time

import re

from opensearchpy import OpenSearch, \
    RequestsHttpConnection, AWSV4SignerAuth, NotFoundError
from boto3.dynamodb.types import TypeDeserializer

from backend.common import get_ssm_parameter_value


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

    @staticmethod
    def from_env(env=os.environ):
        tableName = env.get("METADATA_STORAGE_TABLE_NAME")
        dynamodb = boto3.resource('dynamodb')
        table = dynamodb.Table(tableName)
        return MetadataTable(table)

    @staticmethod
    def generate_prefixes(path):
        prefixes = []
        parts = path.split('/')
        for i in range(1, len(parts)):
            prefix = '/'.join(parts[:i]) + '/'
            prefixes.insert(0, prefix)

        if (not path.endswith('/')):
            prefixes.insert(0, path)
        return prefixes

    @staticmethod
    def generate_prefixes2(path):
        prefixes = []
        parts = path.split('/')
        for i in range(1, len(parts)):
            prefix = '/'.join(parts[:i]) + '/'
            prefixes.append(prefix)

        if (not path.endswith('/')):
            prefixes.append(path)
        return prefixes

    def get_metadata_with_prefix(self, databaseId, assetId, prefix):
        result = {}
        if prefix is not None:
            for paths in [assetId] + self.generate_prefixes2(prefix):
                resp = self.table.get_item(
                    Key={
                        "databaseId": databaseId,
                        "assetId": paths,
                    }
                )
                if "Item" in resp:
                    result = result | resp['Item']

        # remove keys that start with underscores
        for key in list(result.keys()):
            if key.startswith('_'):
                del result[key]

        return result

    def get_metadata(self, databaseId, assetId):
        resp = self.table.get_item(
            Key={
                "databaseId": databaseId,
                "assetId": assetId,
            }
        )
        if "Item" not in resp:
            return None

        # remove keys that start with underscores
        for key in list(resp['Item'].keys()):
            if key.startswith('_'):
                del resp['Item'][key]

        return resp['Item']


class AOSSIndexS3Objects():
    def __init__(self, bucketName, s3client,
                 aosclient, indexName, metadataTable=MetadataTable.from_env):
        self.bucketName = bucketName
        self.s3client = s3client
        self.aosclient = aosclient
        self.indexName = indexName
        self.metadataTable = metadataTable()

    @staticmethod
    def from_env(env=os.environ):
        bucketName = env.get("ASSET_BUCKET_NAME")
        s3client = boto3.client('s3')
        region = env.get('AWS_REGION')
        service = 'aoss'
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region, service)
        host = get_ssm_parameter_value('AOSS_ENDPOINT_PARAM', region, env)
        indexName = get_ssm_parameter_value(
                        'AOSS_INDEX_NAME_PARAM', region, env)
        aosclient = OpenSearch(
            hosts=[{'host': urlparse(host).hostname, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=20,
        )
        return AOSSIndexS3Objects(bucketName, s3client, aosclient, indexName)

    def _get_s3_object_keys_generator(self, prefix):
        paginator = self.s3client.get_paginator('list_objects')
        page_iterator = paginator.paginate(Bucket=self.bucketName,
                                           Prefix=prefix)
        for page in page_iterator:
            for obj in page.get('Contents', []):
                yield obj

    @staticmethod
    def _metadata_and_s3_object_to_opensearch(s3object, metadata):
        s3object['LastModified'] = s3object['LastModified'].strftime(
            "%Y-%m-%d")
        if 'Owner' in s3object:
            del s3object['Owner']
        s3object['fileext'] = s3object['Key'].split('.')[-1]

        print("s3object", s3object)
        print("metadata", metadata)
        result = {
            x: y
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

        return result.get('Item')

    def process_single_s3_object(self, databaseId, assetId,
                                 s3object, asset_fields=None):
        if asset_fields is None:
            asset_fields = self.get_asset_fields(databaseId, assetId)
        metadata = self.metadataTable.get_metadata_with_prefix(
            databaseId, assetId, s3object.get("Key"))
        metadata = metadata | asset_fields

        # enables delete by assetId
        if "assetId" in metadata and "/" in metadata['assetId']:
            metadata['assetId'] = metadata['assetId'].split("/")[0]

        aosrecord = self._metadata_and_s3_object_to_opensearch(
            s3object, metadata)
        self.aosclient.index(
            index=self.indexName,
            body=aosrecord,
            id=s3object['Key'],
        )

    def delete_item(self, key):
        try:
            return self.aosclient.delete(
                index=self.indexName,
                id=key,
            )
        except NotFoundError:
            print("caught not found error on ", key, "likely already deleted.")

    def process_item(self, databaseId, assetIdOrPrefix):
        prefix = None
        assetId = assetIdOrPrefix
        if "/" in assetIdOrPrefix:
            prefix = assetIdOrPrefix
            assetId = assetIdOrPrefix.split("/")[0]

        asset_fields = self.get_asset_fields(databaseId, assetId)
        if asset_fields is None:
            print("asset record does not exist so skipping s3 object indexing")
            return

        if prefix is None:
            print("prefix is None", databaseId, assetIdOrPrefix)

        for s3object in self._get_s3_object_keys_generator(assetIdOrPrefix):
            self.process_single_s3_object(databaseId, assetId,
                                          s3object, asset_fields)


class AOSSIndexAssetMetadata():

    def __init__(self, host, auth, region, service, indexName):
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
        print("env", env.get("METADATA_STORAGE_TABLE_NAME"))
        print("env", env.get("ASSET_STORAGE_TABLE_NAME"))
        print("env", env.get("DATABASE_STORAGE_TABLE_NAME"))
        print("env", env.get("AOSS_ENDPOINT"))
        print("env", env.get("AWS_REGION"))
        region = env.get('AWS_REGION')
        service = 'aoss'
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region, service)
        host = get_ssm_parameter_value('AOSS_ENDPOINT_PARAM', region, env)
        indexName = get_ssm_parameter_value(
                        'AOSS_INDEX_NAME_PARAM', region, env)

        return AOSSIndexAssetMetadata(
            host=host,
            region=region,
            service=service,
            auth=auth,
            indexName=indexName)

    @staticmethod
    def _determine_field_type(data):

        if data is None:
            return "str"

        j = re.compile(r"^{.*}$")
        dt = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        bool = re.compile(r"^true$|^false$")
        # regex that matches int or float
        num = re.compile(r"^\d+$|^\d+\.\d+$")

        if isinstance(data, Decimal) or isinstance(data, float) \
           or isinstance(data, int):
            return "num"

        if j.match(data):

            try:
                j = json.loads(data)
                if "loc" in j and "polygons" in j \
                        and "FeatureCollection" == j["polygons"].get("type"):
                    return "geo_point_and_polygon"

                return "json"
            except Exception:
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
                    "coordinates": j['polygons']['features'][0]
                                    ['geometry']['coordinates']
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
            elif data_type == "num" and (isinstance(data, float)
                                         or isinstance(data, int)):
                return data
            elif data_type == "bool":
                return data == "true"
            else:
                return data

        return [("{data_type}_{name}".format(
            name=field_name, data_type=data_type), _data_conv())]

    @staticmethod
    def _process_item(item):
        deserialize = TypeDeserializer().deserialize
        result = {
            x: y
            for k, v in item["dynamodb"]["NewImage"].items()
            for x, y in AOSSIndexAssetMetadata._determine_field_name(
                            k, deserialize(v))
        }
        result['_rectype'] = 'asset'
        return result

    def process_item(self, item):
        try:
            body = AOSSIndexAssetMetadata._process_item(item)
            return self.client.index(
                index=self.indexName,
                body=body,
                id=item['dynamodb']['Keys']['assetId']['S'],
                # refresh = True,
            )
        except Exception as e:
            print("failed input", item)
            traceback.print_exc()
            raise e

    def delete_item(self, assetId):
        try:
            return self.client.delete(
                index=self.indexName,
                id=assetId,
            )
        except NotFoundError:
            print("caught not found error on opensearch asset record",
                  assetId, "likely already deleted.")
            return None

    def delete_item_by_query(self, assetId):
        results = self.client.search(
            index=self.indexName,
            body={
                "query": {
                    "match": {
                        "str_assetid": assetId
                    }
                }
            }
        )

        ids = [
            r.get('_id')
            for r in results.get("hits", {}).get("hits", [])
            if "_id" in r
        ]
        return [
            self.delete_item(item)
            for item in ids
        ]


def get_asset_fields(keys):
    # 'Keys': {'assetId': {'S': '...'}, 'databaseId': {'S': '...'}},
    client = boto3.client("dynamodb")

    # this allows us to get the asset fields when the provided key
    # is for a metadata prefix rather than just an assetId
    if "assetId" in keys and "S" in keys["assetId"]:
        keys = {
            "assetId": {
                "S": keys['assetId']['S'].split("/")[0]
            },
            "databaseId": {
                "S": keys["databaseId"]["S"]
            }
        }

    attempts = 0
    result = {}
    while result.get("Item") is None and attempts < 60:
        attempts += 1
        result = client.get_item(
            TableName=os.environ.get("ASSET_STORAGE_TABLE_NAME"),
            Key=keys,
            AttributesToGet=[
                'assetName', "description", "assetType"
            ],
        )

        if result.get("Item") is None:
            print("asset record is empty on attempt",
                  attempts, "for keys", keys)
            time.sleep(1)

    return result.get('Item')


def lambda_handler_a(event, context,
                     index=AOSSIndexAssetMetadata.from_env,
                     s3index=AOSSIndexS3Objects.from_env,
                     get_asset_fields_fn=get_asset_fields):

    print("asset table event", event)
    # we need to catch delete events here and delete by query on aoss.
    client = index()

    if event.get("eventName") == "REMOVE":
        client.delete_item_by_query(event['dynamodb']['Keys']['assetId']['S'])

    if 'Records' not in event:
        return

    for record in event['Records']:
        if record['eventName'] == 'REMOVE':
            client.delete_item_by_query(
                record['dynamodb']['Keys']['assetId']['S'])


def handle_s3_event_record_removed(record, s3, s3index_fn):
    if not record.get("eventName", "").startswith("ObjectRemoved:"):
        print("not an object removed event", record)
        return

    s3index = s3index_fn()
    s3index.delete_item(record.get("s3", {}).get("object", {}).get("key", ""))


def handle_s3_event_record(record,
                           s3=boto3.client("s3"),
                           metadata_fn=MetadataTable.from_env,
                           get_asset_fields_fn=get_asset_fields,
                           s3index_fn=AOSSIndexS3Objects.from_env,
                           sleep_fn=time.sleep):

    handle_s3_event_record_removed(record, s3, s3index_fn)

    if not record.get("eventName", "").startswith("ObjectCreated:"):
        print("not an object created event", record)
        return

    metadata = metadata_fn()

    head_result = s3.head_object(
        Bucket=record['s3']['bucket']['name'],
        Key=record['s3']['object']['key'],
    )

    databaseId = head_result['Metadata']['databaseid']
    assetId = head_result['Metadata']['assetid']
    deleted = head_result['Metadata'].get('vams-status')

    # s3 objects are marked with vams-status=deleted in their s3 metadata
    # by calls to delete assets in assetService.py
    if deleted == 'deleted':
        s3index = s3index_fn()
        s3index.delete_item(
            record.get("s3", {}).get("object", {}).get("key", ""))
        return

    # see if records exist for the asset and metadata tables
    metadata_record = None
    attempt = 0
    while metadata_record is None and attempt < 60:
        metadata_record = metadata.get_metadata(databaseId, assetId)
        attempt += 1
        if metadata_record is None:
            print("metadata record not available yet")
            sleep_fn(1)

    asset_record = None
    attempt = 0
    while asset_record is None and attempt < 60:
        asset_record = get_asset_fields_fn({
            "databaseId": {"S": databaseId},
            "assetId": {"S": assetId}
        })
        attempt += 1
        if asset_record is None:
            print("asset record not available yet")
            sleep_fn(1)

    if asset_record is None:
        raise Exception("unable to get asset records after 1 minute")

    if metadata_record is None:
        raise Exception("unable to get metadata records after 1 minute")

    s3index = s3index_fn()

    s3index.process_single_s3_object(
        databaseId,
        assetId,
        {
            "Key": record['s3']['object']['key'],
            "LastModified": head_result['LastModified'],
            "ETag": head_result['ETag'],
        },
    )


def lambda_handler_m(event, context,
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

        if record.get("eventSource") == "aws:s3":
            handle_s3_event_record(record)
            continue

        if record['eventName'] == 'REMOVE':
            client.delete_item(record['dynamodb']['Keys']['assetId']['S'])
            continue

        # TODO when the metadata record contains an s3 key prefix rather than
        # an assetid we need to extract the assetid
        asset_fields = get_asset_fields_fn(record['dynamodb']['Keys'].copy())

        if asset_fields is None:
            print("no asset fields", record)
            continue

        record['dynamodb']['NewImage'] = record['dynamodb']['NewImage'] | \
            asset_fields
        print("with asset fields", record)

        # remove keys that start with underscores
        for k in list(record['dynamodb']['NewImage'].keys()):
            if k.startswith("_"):
                del record['dynamodb']['NewImage'][k]

        try:
            # if this is metadata at a s3 key prefix rather than an asset,
            # just index the items with that prefix as files
            if "/" in record['dynamodb']['Keys']['assetId']['S']:
                print("indexing s3 objects only")
                s3index().process_item(
                    record['dynamodb']['Keys']['databaseId']['S'],
                    record['dynamodb']['Keys']['assetId']['S'])
            else:
                print("processing asset and s3 objects")
                print(client.process_item(record))
                s3index().process_item(
                    record['dynamodb']['Keys']['databaseId']['S'],
                    record['dynamodb']['Keys']['assetId']['S'])
        except Exception as e:
            print("error", e)
            traceback.print_exc()
            raise e
