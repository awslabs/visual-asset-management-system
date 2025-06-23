# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import boto3
import os
from decimal import Decimal
from urllib.parse import urlparse
import time
import re
from opensearchpy import OpenSearch, \
    RequestsHttpConnection, AWSV4SignerAuth, NotFoundError
from boto3.dynamodb.types import TypeDeserializer
from common import get_ssm_parameter_value
from boto3.dynamodb.conditions import Key
from customLogging.logger import safeLogger

logger = safeLogger(service="IndexingStreams")

s3client = boto3.client("s3")
dynamodbClient = boto3.client("dynamodb")
dynamodbResource = boto3.resource('dynamodb')
deserialize = TypeDeserializer().deserialize

s3_asset_buckets_table = os.environ["S3_ASSET_BUCKETS_STORAGE_TABLE_NAME"]
buckets_table = dynamodbResource.Table(s3_asset_buckets_table)

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
        table = dynamodbResource.Table(tableName)
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
    
    def write_asset_table_updated_event(self, databaseId, assetId):
        self.table.update_item(
            Key={
                "databaseId": databaseId,
                "assetId": assetId,
            },
            UpdateExpression="SET #_asset_table_updated = :_asset_table_updated",
            ExpressionAttributeNames={
                "#_asset_table_updated": "_asset_table_updated",
            },
            ExpressionAttributeValues={
                ":_asset_table_updated": Decimal(time.time()),
            },
        )

class AOSIndexS3Objects():
    def __init__(self,
                 aosclient, indexName, metadataTable=MetadataTable.from_env):
        self.aosclient = aosclient
        self.indexName = indexName
        self.metadataTable = metadataTable()

    @staticmethod
    def from_env(env=os.environ):
        region = env.get('AWS_REGION')
        service = env.get('AOS_TYPE')  # aoss (serverless) or es (provisioned)
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region, service)
        host = get_ssm_parameter_value('AOS_ENDPOINT_PARAM', region, env)
        indexName = get_ssm_parameter_value(
            'AOS_INDEX_NAME_PARAM', region, env)
        aosclient = OpenSearch(
            hosts=[{'host': urlparse(host).hostname, 'port': 443}],
            http_auth=auth,
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            pool_maxsize=20,
        )
        return AOSIndexS3Objects(aosclient, indexName)
    
    def _get_default_bucket_details(self, bucketId):
        """Get default S3 bucket details from database default bucket DynamoDB"""
        try:

            bucket_response = buckets_table.query(
                KeyConditionExpression=Key('bucketId').eq(bucketId),
                Limit=1
            )
            # Use the first item from the query results
            bucket = bucket_response.get("Items", [{}])[0] if bucket_response.get("Items") else {}
            bucket_id = bucket.get('bucketId')
            bucket_name = bucket.get('bucketName')
            base_assets_prefix = bucket.get('baseAssetsPrefix')

            #Check to make sure we have what we need
            if not bucket_name or not base_assets_prefix:
                raise Exception(f"Error getting database default bucket details: {str(e)}")
            
            #Make sure we end in a slash for the path
            if not base_assets_prefix.endswith('/'):
                base_assets_prefix += '/'

            # Remove leading slash from file path if present
            if base_assets_prefix.startswith('/'):
                base_assets_prefix = base_assets_prefix[1:]

            return {
                'bucketId': bucket_id,
                'bucketName': bucket_name,
                'baseAssetsPrefix': base_assets_prefix
            }
        except Exception as e:
            logger.exception(f"Error getting bucket details: {e}")
            raise Exception(f"Error getting bucket details: {str(e)}")

    def _get_s3_object_keys_generator(self, prefix, bucket):
        paginator = s3client.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=bucket,
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

        logger.info("s3object")
        logger.info(s3object)
        logger.info("metadata")
        logger.info(metadata)
        result = {
            x: y
            for k, v in (s3object | metadata).items()
            for x, y in AOSIndexAssetMetadata._determine_field_name(k, v)
        }
        result['_rectype'] = 's3object'
        logger.info("aos s3")
        logger.info(result)
        return result

    def get_asset_fields(self, databaseId, assetId):
        table = dynamodbResource.Table(os.environ.get("ASSET_STORAGE_TABLE_NAME"))

        result = table.get_item(
            Key={
                "assetId": assetId,
                "databaseId": databaseId,
            },
            AttributesToGet=[
                'assetName', "description", "assetType", "tags", "bucketId"
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
            #refresh = True
        )

    def delete_item(self, key):
        try:
            return self.aosclient.delete(
                index=self.indexName,
                id=key,
            )
        except NotFoundError:
            logger.exception("caught not found error on "+key+"likely already deleted.")

    def process_item(self, databaseId, assetIdOrPrefix):
        prefix = None
        assetId = assetIdOrPrefix
        if "/" in assetIdOrPrefix:
            prefix = assetIdOrPrefix
            assetId = assetIdOrPrefix.split("/")[0]

        asset_fields = self.get_asset_fields(databaseId, assetId)
        if asset_fields is None:
            logger.info("asset record does not exist so skipping s3 object indexing")
            return

        if prefix is None:
            logger.info("prefix is None")
            logger.info(assetIdOrPrefix)

        bucket_details = self._get_default_bucket_details(asset_fields['bucketId'])
        bucket = bucket_details['bucketName']

        for s3object in self._get_s3_object_keys_generator(assetIdOrPrefix, bucket):
            self.process_single_s3_object(databaseId, assetId,
                                          s3object, asset_fields)


class AOSIndexAssetMetadata():

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
        logger.info(env.get("AOS_ENDPOINT"))
        logger.info(env.get("AWS_REGION"))
        region = env.get('AWS_REGION')
        service = env.get('AOS_TYPE')  # aoss (serverless) or es (provisioned)
        credentials = boto3.Session().get_credentials()
        auth = AWSV4SignerAuth(credentials, region, service)
        host = get_ssm_parameter_value('AOS_ENDPOINT_PARAM', region, env)
        indexName = get_ssm_parameter_value(
            'AOS_INDEX_NAME_PARAM', region, env)

        return AOSIndexAssetMetadata(
            host=host,
            region=region,
            service=service,
            auth=auth,
            indexName=indexName)

    @staticmethod
    def _determine_field_type(data):

        if data is None:
            return "str"
    

        try:
            j = re.compile(r"^{.*}$")
            dt = re.compile(r"^\d{4}-\d{2}-\d{2}$")
            bool = re.compile(r"^true$|^false$")
            # regex that matches int or float
            num = re.compile(r"^\d+$|^\d+\.\d+$")

            if isinstance(data, Decimal) or isinstance(data, float) \
            or isinstance(data, int):
                return "num"

            if isinstance(data, list):
                return "list"

            if j.match(data):

                try:
                    j = json.loads(data)
                    if "loc" in j and "polygons" in j \
                            and "FeatureCollection" == j["polygons"].get("type"):
                        return "geo_point_and_polygon"

                    return "json"
                except Exception as e:
                    pass

            if dt.match(data):
                return "date"

            if bool.match(data):
                return "bool"

            if num.match(data):
                return "num"

            return "str"
        
        except Exception as e:
            return "str"

    @staticmethod
    def _determine_field_name(field, data):

        if data is None or field is None:
            return []

        field_name = field.lower().replace(" ", "_")
        data_type = AOSIndexAssetMetadata._determine_field_type(data)
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
        result = {
            x: y
            for k, v in item["dynamodb"]["NewImage"].items()
            for x, y in AOSIndexAssetMetadata._determine_field_name(
                k, deserialize(v))
        }
        result['_rectype'] = 'asset'
        return result

    def process_item(self, item):
        try:
            body = AOSIndexAssetMetadata._process_item(item)
            return self.client.index(
                index=self.indexName,
                body=body,
                id=item['dynamodb']['Keys']['assetId']['S'],
                #refresh = True,
            )
        except Exception as e:
            logger.exception(item)
            raise e

    def delete_item(self, assetId):
        try:
            return self.client.delete(
                index=self.indexName,
                id=assetId,
            )
        except NotFoundError:
            logger.exception("caught not found error on opensearch asset record "+assetId+" likely already deleted.")
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
    # 'Keys': {'assetId': {'S': '...'}, 'databaseId': {'S': '...'}}

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
    #logger.info(keys)
    while result.get("Item") is None and attempts < 60:
        attempts += 1
        result = dynamodbClient.get_item(
            TableName=os.environ.get("ASSET_STORAGE_TABLE_NAME"),
            Key=keys,
            AttributesToGet=[
                'assetName', "description", "assetType", "tags"
            ],
        )

        if result.get("Item") is None:
            logger.info("asset record is empty on attempt"+ str(attempts))
            time.sleep(1)

    return result.get('Item')


def lambda_handler_a(event, context,
                     index=AOSIndexAssetMetadata.from_env,
                     s3index=AOSIndexS3Objects.from_env,
                     metadataTable_fn=MetadataTable.from_env,
                     get_asset_fields_fn=get_asset_fields):

    logger.info(event)

    # we need to catch delete events here and delete by query on aos.
    client = index()
    metadataTable = metadataTable_fn()

    if event.get("eventName") == "REMOVE":
        client.delete_item_by_query(event['dynamodb']['Keys']['assetId']['S'])

    if 'Records' not in event:
        return

    for record in event['Records']:
        if record['eventName'] == 'REMOVE':
            client.delete_item_by_query(
                record['dynamodb']['Keys']['assetId']['S'])

        #Asset table inserted / updated
        #Note: updates metadata table for asset updated which triggers the "lambda_handler_m" handler through DynamoDB streams (hack fix for now)
        if record['eventName'] == 'MODIFY' or record['eventName'] == 'INSERT':   
            logger.info("insert or modify asset table record")
            databaseId = record['dynamodb']['Keys']['databaseId']['S']
            assetId = record['dynamodb']['Keys']['assetId']['S']
            metadataTable.write_asset_table_updated_event(
                databaseId, assetId)

def handle_s3_event_record_removed(record, s3index_fn):
    if not record.get("eventName", "").startswith("ObjectRemoved:"):
        logger.info("not an object removed event")
        logger.info(record)
        return

    s3index = s3index_fn()
    s3index.delete_item(record.get("s3", {}).get("object", {}).get("key", ""))


def handle_s3_event_record(record,
                           bucketName = '',
                           bucketPrefix = '',
                           metadata_fn=MetadataTable.from_env,
                           get_asset_fields_fn=get_asset_fields,
                           s3index_fn=AOSIndexS3Objects.from_env,
                           sleep_fn=time.sleep):
    

    if bucketName and bucketName != '' and record.get("s3", {}).get("bucket", {}).get("name", "") != bucketName:
        logger.info("Buckets don't match. Ignoring")
        logger.info(record)
        return
    
    if bucketPrefix and bucketPrefix != '' and not record.get("s3", {}).get("object", {}).get("key", "").startswith(bucketPrefix):
        logger.info("Bucket prefix doesn't match records we care to index. Ignoring")
        logger.info(record)
        return

    #Now set it to empty so we can do proper starts with checks
    if not bucketPrefix:
        bucketPrefix = ''
    
    #Ignore pipeline and preview files from assets
    if record.get("s3", {}).get("object", {}).get("key", "").startswith(bucketPrefix+"pipeline") or \
            record.get("s3", {}).get("object", {}).get("key", "").startswith(bucketPrefix+"preview") or \
            record.get("s3", {}).get("object", {}).get("key", "").startswith(bucketPrefix+"temp-uploads"):
        logger.info("Ignoring pipeline and preview files from assets from indexing")
        return

    handle_s3_event_record_removed(record, s3index_fn)

    if not record.get("eventName", "").startswith("ObjectCreated:"):
        logger.info("not an object created event")
        logger.info(record)
        return

    metadata = metadata_fn()

    head_result = s3client.head_object(
        Bucket=record['s3']['bucket']['name'],
        Key=record['s3']['object']['key'],
    )

    databaseId = head_result['Metadata'].get('databaseid', None)
    assetId = head_result['Metadata'].get('assetid', None)
    deleted = head_result['Metadata'].get('vams-status', '')

    # s3 objects are marked with vams-status=deleted in their s3 metadata
    # by calls to delete assets in assetService.py
    if deleted == 'deleted':
        s3index = s3index_fn()
        s3index.delete_item(
            record.get("s3", {}).get("object", {}).get("key", ""))
        return
    
    if databaseId is None or assetId is None:
        logger.info("databaseId or assetId not found in s3 metadata, skipping file as we may not we ingested yet or external")

    # see if records exist for the asset and metadata tables
    metadata_record = None
    attempt = 0
    while metadata_record is None and attempt < 60:
        metadata_record = metadata.get_metadata(databaseId, assetId)
        attempt += 1
        if metadata_record is None:
            logger.info("metadata record not available yet")
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
            logger.info("asset record not available yet")
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
                     index=AOSIndexAssetMetadata.from_env,
                     s3index=AOSIndexS3Objects.from_env,
                     get_asset_fields_fn=get_asset_fields):

    logger.info(event)
    client = index()

    # 'eventName': 'REMOVE'
    if event.get("eventName") == "REMOVE":
        client.delete_item(event['dynamodb']['Keys']['assetId']['S'])
        return
    else:
        logger.info("Not a DynamoDB remove record")

    #Parse various formats depending on where the event could be fired from
    records = []
    if 'Records' in event:
        records = event['Records']
    elif 'Message' in event:
        message = json.loads(event['Message'])
        if 'Records' in message:
            records = message['Records']

    bucketName = None
    bucketPrefix = None
    
    if 'ASSET_BUCKET_NAME' in event:
        bucketName = event['ASSET_BUCKET_NAME']

    if 'ASSET_BUCKET_PREFIX' in event:
        bucketPrefix = event['ASSET_BUCKET_PREFIX']

        if bucketPrefix == '/':
            bucketPrefix = None

        if bucketPrefix is not None and bucketPrefix != '':
            # Remove leading slash from file path if present
            if bucketPrefix.startswith('/'):
                bucketPrefix = bucketPrefix[1:]

            if not bucketPrefix.endswith('/'):
                bucketPrefix = bucketPrefix + '/'

    for record in records:
        logger.info(record)

        # Coming from SNS by S3 event notification
        if "EventSource" in record and record['EventSource'] == 'aws:sns' and 'Records' in json.loads(record["Sns"]["Message"]):
            for snsS3Record in json.loads(record['Sns']['Message'])['Records']:
                if (snsS3Record['eventSource'] == "aws:s3"):
                    handle_s3_event_record(snsS3Record, bucketName, bucketPrefix)
            continue

        # Coming from SQS by S3 event notification
        if "EventSource" in record and record['EventSource'] == 'aws:sqs' and 'Records' in json.loads(record["Sqs"]["Message"]):
            for sqsS3Record in json.loads(record['Sqs']['Message'])['Records']:
                if (sqsS3Record['eventSource'] == "aws:s3"):
                    handle_s3_event_record(sqsS3Record, bucketName, bucketPrefix)
            continue

        # Coming directly from S3 event notification
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
            logger.info("no asset fields")
            logger.info(record)
            continue

        record['dynamodb']['NewImage'] = record['dynamodb']['NewImage'] | \
            asset_fields
        logger.info("with asset fields")
        logger.info(record)

        # remove keys that start with underscores
        for k in list(record['dynamodb']['NewImage'].keys()):
            if k.startswith("_"):
                del record['dynamodb']['NewImage'][k]

        try:
            # if this is metadata at a s3 key prefix rather than an asset,
            # just index the items with that prefix as files
            if "/" in record['dynamodb']['Keys']['assetId']['S']:
                logger.info("indexing s3 objects only")
                s3index().process_item(
                    record['dynamodb']['Keys']['databaseId']['S'],
                    record['dynamodb']['Keys']['assetId']['S'])
            else:
                logger.info("processing asset and s3 objects")
                logger.info(client.process_item(record))
                s3index().process_item(
                    record['dynamodb']['Keys']['databaseId']['S'],
                    record['dynamodb']['Keys']['assetId']['S'])
        except Exception as e:
            logger.exception(e)
            raise e
