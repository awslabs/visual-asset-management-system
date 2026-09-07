#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0
import boto3
from botocore.config import Config
from typing import Tuple
from typing import Any
from typing import Dict
from typing import List
from boto3.dynamodb.conditions import Key
from customLogging.logger import safeLogger
from models.common import VAMSGeneralErrorResponse
from common.resourceNames import ResourceKeys, get_table_name

logger = safeLogger(service_name="DynamoDBCommon")

retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})

dynamodb_client = boto3.client('dynamodb', config=retry_config)
dynamodb = boto3.resource('dynamodb', config=retry_config)

# Resource names resolve once per container (backend/CLAUDE.md Rule 10). This module is imported by
# handlers that never touch the asset table, so a name that does not resolve is deliberately not
# fatal at import: the handle is left unbuilt and _asset_table_resource() retries, raising to the one
# caller that needs it instead of failing every importer's cold start.
try:
    _asset_table = dynamodb.Table(get_table_name(ResourceKeys.ASSET_STORAGE_TABLE))
except Exception:
    logger.exception("Failed resolving asset storage table name at import; retrying on first use")
    _asset_table = None


def _asset_table_resource():
    """The asset storage table, built once per container."""
    global _asset_table
    if _asset_table is None:
        _asset_table = dynamodb.Table(get_table_name(ResourceKeys.ASSET_STORAGE_TABLE))
    return _asset_table


def query_all_items(table, **query_kwargs) -> List[Dict]:
    """Query a table, paging to exhaustion, and return every matching item.

    A single DynamoDB query returns at most 1 MB of items, so a caller that reads only
    `response['Items']` silently truncates once the matched set exceeds that page —
    for an asset-version file snapshot this lands in the low thousands of files. This
    follows LastEvaluatedKey so the caller always receives the complete set.

    Use where completeness matters (internal reads that must reflect the whole set).
    User-facing GETs that return a page plus a NextToken should page externally
    instead — see Rule 15 in backend/CLAUDE.md.

    Args:
        table: A boto3 DynamoDB Table resource.
        **query_kwargs: Passed through to Table.query (KeyConditionExpression, etc.).
            ExclusiveStartKey is managed here and must not be supplied.

    Returns:
        List of every item matching the query.
    """
    items = []
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get('Items', []))

        if 'LastEvaluatedKey' not in response:
            return items
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def query_has_match(table, **query_kwargs) -> bool:
    """Query a table, paging until an item is found, and return whether one was.

    DynamoDB applies a FilterExpression AFTER the 1 MB page read, so an existence check
    decided from one page is a false negative whenever the matching item sits beyond it:
    an asset with thousands of links returns an empty first page while the one link of
    the filtered type is on a later one. Empty `Items` alongside a present
    LastEvaluatedKey means "the match is on a later page", not "no such item".

    Stops at the first non-empty page, so a match near the start costs one query.

    Args:
        table: A boto3 DynamoDB Table resource.
        **query_kwargs: Passed through to Table.query (KeyConditionExpression,
            FilterExpression, etc.). ExclusiveStartKey is managed here and must not be
            supplied.

    Returns:
        True if any page yielded an item, False once the walk is exhausted.
    """
    while True:
        response = table.query(**query_kwargs)
        if response.get('Items'):
            return True

        if 'LastEvaluatedKey' not in response:
            return False
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def to_update_expr(record, op="SET") -> Tuple[Dict[str, str], Dict[str, Any], str]:
    """
    :param record:
    :param op:
    :return:
    """
    keys = record.keys()
    keys_attr_names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values_attr_names = [":v{n}".format(n=x) for x in range(len(keys))]

    keys_map = {
        k: key
        for k, key in zip(keys_attr_names, keys)
    }
    values_map = {
        v1: record[v]
        for v, v1 in zip(keys, values_attr_names)
    }
    expr = "{op} ".format(op=op) + ", ".join([
        "{f} = {v}".format(f=f, v=v)
        for f, v in zip(keys_attr_names, values_attr_names)
    ])
    return keys_map, values_map, expr


def get_asset_object_from_id(databaseId, assetId):
    """Resolve an asset record for authorization, keyed by databaseId+assetId or by assetId alone.

    Returns the asset record annotated with object__type 'asset', or None when the asset
    does not exist. Callers must handle the None: an object with no attributes evaluates
    against ABAC criteria as if the asset were present, which produces allow/deny outcomes
    for an asset that is not there.

    Raises VAMSGeneralErrorResponse when assetId is empty, when the read fails, and when
    an assetId alone matches more than one live asset (assetIds are unique within a
    database, not across databases, so that match cannot be resolved without a databaseId).
    """
    if not assetId:
        raise VAMSGeneralErrorResponse("Empty assetId or databaseId received")

    try:
        asset_table = _asset_table_resource()
    except Exception:
        logger.exception("Failed resolving asset storage table name")
        raise

    if databaseId:
        """Get asset details from DynamoDB"""
        try:
            response = asset_table.query(
                KeyConditionExpression=Key('databaseId').eq(databaseId) & Key('assetId').eq(assetId),
                ScanIndexForward=False
            )
            
            if not response.get('Items'):
                return None

            #get first object
            asset_object = response['Items'][0]
            asset_object.update({
                "object__type": "asset"
            })
            return asset_object
        
        except Exception as e:
            logger.exception(f"Error getting asset details: {e}")
            raise VAMSGeneralErrorResponse(f"Error retrieving asset.")
    else:
        #Kept right now for backwards capability until all tables can be updated to use datbaseId/Assetid (comments, subscriptions, asset links)
        #assetIdGSI is partitioned on assetId, so this is a keyed read of the index that
        #returns only the rows carrying this assetId, not a filtered pass over the table.
        try:
            items = query_all_items(
                asset_table,
                IndexName='assetIdGSI',
                KeyConditionExpression=Key('assetId').eq(assetId)
            )
        except Exception as e:
            logger.exception(f"Error getting asset details: {e}")
            raise VAMSGeneralErrorResponse("Error retrieving asset.")

        #Archiving rewrites a record under a "{databaseId}#deleted" partition, so an
        #archived row is not the live asset and cannot stand in for it.
        live_items = [
            item for item in items
            if not str(item.get('databaseId', '')).endswith('#deleted')
            and item.get('status') != 'archived'
        ]

        #assetIds are unique within a database only. Two live matches cannot be
        #distinguished without a databaseId, so the ambiguity is surfaced instead of
        #resolved to whichever row the index returned first.
        if len(live_items) > 1:
            logger.error(
                f"assetId {assetId} matches {len(live_items)} live assets, in databases "
                f"{sorted(str(item.get('databaseId')) for item in live_items)}"
            )
            raise VAMSGeneralErrorResponse("Asset ID matches more than one asset. Provide a database ID.")

        if not live_items:
            logger.info(
                f"No live asset for assetId {assetId}. Archived matches: {len(items)}")
            return None

        item = live_items[0]
        return {
            "object__type": "asset",
            "assetId": item.get('assetId'),
            "assetName": item.get('assetName'),
            "databaseId": item.get('databaseId'),
            "assetType": item.get('assetType'),
            "tags": list(item.get('tags') or [])
        }


# Ceilings for a boto3 paginator's PaginationConfig budget. build_full_result() accumulates pages
# until MaxItems is reached, so a caller-supplied maxItems fed straight into it walks a table to
# exhaustion inside one invocation. Every such site bounds its budget with these, which holds even
# for a handler that reaches the paginator with raw query parameters -- the tag listing falls back to
# exactly that when its request model rejects the request.
#
# The values match the model-layer ceilings (models/databases.py MAX_LIST_MAX_ITEMS /
# MAX_LIST_PAGE_SIZE), so the bound a request is rejected against and the bound the work is done
# under are the same number. They are deliberately NOT applied inside validate_pagination_info: see
# the note in that function.
MAX_PAGINATION_MAX_ITEMS = 30000
MAX_PAGINATION_PAGE_SIZE = 10000


def validate_pagination_info(queryParameters, defaultMaxItemsOverride=10000, defaultPageSizeOverride=3000):
    """
    Sets the pagination infor from the query parameters
    :param queryParameters: dictionary containing pagination info
    :param defaultMaxItemsOverride: default max items to return, set to 10000 if not set
    :param defaultPageSizeOverride: default page size to return, set to 3000 if not set
    """

    if queryParameters is None:
        queryParameters = {}

    if 'pageSize' not in queryParameters:
        queryParameters['pageSize'] = defaultMaxItemsOverride
    else:
        #Check to make sure maxItems is a number, otherwise log and reset to defaultPageSizeOverride
        try:
            int(queryParameters['pageSize'])
        except ValueError:
            queryParameters['pageSize'] = defaultPageSizeOverride
            logger.warn("pageSize parameter is not a number. Re-Setting to defaultPageSizeOverride.")


    if 'maxItems' not in queryParameters:
        #max items should be page count size if not included
        queryParameters['maxItems'] = int(queryParameters['pageSize'])
    else:
        #Check to make sure maxItems is a number, otherwise log and reset to defaultMaxItemsOverride
        try:
            int(queryParameters['maxItems'])
        except ValueError:
            queryParameters['maxItems'] = defaultMaxItemsOverride
            logger.warn("maxItems parameter is not a number. Re-Setting to defaultMaxItemsOverride.")

    #Check min size
    if int(queryParameters['pageSize']) < 1:
        queryParameters['pageSize'] = defaultPageSizeOverride

    if int(queryParameters['maxItems']) < 1:
        queryParameters['maxItems'] = defaultMaxItemsOverride

    #No ceiling is applied here on purpose: this helper runs AHEAD of the request model, so clamping
    #an over-ceiling value would hide it from the model and turn a 400 into a 200 carrying a quietly
    #reduced page. The ceiling is enforced where it can be answered as a caller error (the models'
    #le= bounds) and where an unbounded value would actually do work (the PaginationConfig budgets
    #below, which use MAX_PAGINATION_MAX_ITEMS / MAX_PAGINATION_PAGE_SIZE).

    #Limit page size to maxitems
    if int(queryParameters['pageSize']) > int(queryParameters['maxItems']):
        queryParameters['pageSize'] = int(queryParameters['maxItems'])
        logger.warn("Data page size exceeds max items, reseting page size to max items. ")

    if 'startingToken' not in queryParameters:
        queryParameters['startingToken'] = None
