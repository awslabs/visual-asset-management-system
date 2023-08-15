# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

import json
import traceback

from backend.handlers.metadata import logger, mask_sensitive_data, \
    build_response, table, validate_event, ValidationError
from backend.handlers.auth import get_database_set, request_to_claims


def generate_prefixes(path):
    prefixes = []
    parts = path.split('/')
    for i in range(1, len(parts)):
        prefix = '/'.join(parts[:i]) + '/'
        prefixes.insert(0, prefix)

    if (not path.endswith('/')):
        prefixes.insert(0, path)
    return prefixes


def get_metadata_with_prefix(databaseId, assetId, prefix):
    result = {}
    if prefix is not None:
        for paths in generate_prefixes(prefix):
            resp = table.get_item(
                Key={
                    "databaseId": databaseId,
                    "assetId": paths,
                }
            )
            if "Item" in resp:
                result = resp['Item'] | result
        try:
            asset_metadata = get_metadata(databaseId, assetId)
            result = asset_metadata | result
            return result
        except ValidationError:
            return result
    else:
        return get_metadata(databaseId, assetId)


def get_metadata(databaseId, assetId):
    resp = table.get_item(
        Key={
            "databaseId": databaseId,
            "assetId": assetId,
        }
    )
    if "Item" not in resp:
        raise ValidationError(404, "Item Not Found")
    return resp['Item']


def lambda_handler(event, context):
    logger.info(mask_sensitive_data(event))
    try:
        validate_event(event)
        databaseId = event['pathParameters']['databaseId']
        assetId = event['pathParameters']['assetId']
        prefix = None
        if ('queryStringParameters' in event
                and 'prefix' in event['queryStringParameters']):
            prefix = (event['queryStringParameters']
                      and event['queryStringParameters']['prefix'])

        claims_and_roles = request_to_claims(event)
        databases = get_database_set(claims_and_roles['tokens'])
        if (databaseId in databases
                or "super-admin" in claims_and_roles['roles']):

            metadata = get_metadata_with_prefix(databaseId, assetId, prefix)

            # remove private keys that start with underscores
            for key in list(metadata.keys()):
                if key.startswith("_"):
                    del metadata[key]

            return build_response(200, json.dumps({
                "version": "1",
                "metadata": metadata,
            }))
        else:
            print("raising 403 databaseId not in claims and roles?",
                  databaseId, claims_and_roles, databases)
            raise ValidationError(403, "Not Authorized")

    except ValidationError as ex:
        logger.info(traceback.format_exc())
        return build_response(ex.code, json.dumps(ex.resp))
    except Exception:
        logger.error(traceback.format_exc())
        return build_response(500, "Server Error")
