# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

# Mirrors common.dynamodb's paginator-budget ceilings. Handlers import these by name at module load,
# so a mock missing them raises ImportError during a from-source reload rather than at the call site
# (tests/CLAUDE.md: a mock must be at least as wide as the thing it replaces). The values match the
# real module so a handler test bounding a PaginationConfig sees the deployed number.
MAX_PAGINATION_MAX_ITEMS = 30000
MAX_PAGINATION_PAGE_SIZE = 10000


def validate_pagination_info(queryParameters, defaultMaxItemsOverride=10000,
                             defaultPageSizeOverride=3000):
    """Mock of common.dynamodb.validate_pagination_info — fills pageSize/maxItems/startingToken
    defaults on the query-parameters dict, matching the real helper's contract."""
    if queryParameters is None:
        queryParameters = {}
    if 'pageSize' not in queryParameters:
        queryParameters['pageSize'] = defaultMaxItemsOverride
    else:
        try:
            int(queryParameters['pageSize'])
        except (ValueError, TypeError):
            queryParameters['pageSize'] = defaultPageSizeOverride
    if 'maxItems' not in queryParameters:
        queryParameters['maxItems'] = int(queryParameters['pageSize'])
    else:
        try:
            int(queryParameters['maxItems'])
        except (ValueError, TypeError):
            queryParameters['maxItems'] = defaultMaxItemsOverride
    if int(queryParameters['pageSize']) < 1:
        queryParameters['pageSize'] = defaultPageSizeOverride
    if int(queryParameters['maxItems']) < 1:
        queryParameters['maxItems'] = defaultMaxItemsOverride
    if int(queryParameters['pageSize']) > int(queryParameters['maxItems']):
        queryParameters['pageSize'] = int(queryParameters['maxItems'])
    if 'startingToken' not in queryParameters:
        queryParameters['startingToken'] = None
    return queryParameters


def query_all_items(table, **query_kwargs):
    """Mock of common.dynamodb.query_all_items — pages to exhaustion on the PRESENCE of
    LastEvaluatedKey, the same contract the real helper carries. Presence rather than value
    is what keeps the loop finite against a bare MagicMock reader, which is what an
    under-stubbed fixture hands it."""
    items = []
    while True:
        response = table.query(**query_kwargs)
        items.extend(response.get('Items', []))

        if 'LastEvaluatedKey' not in response:
            return items
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def query_has_match(table, **query_kwargs):
    """Mock of common.dynamodb.query_has_match — pages until a page yields an item, deciding the
    end of the walk on the PRESENCE of LastEvaluatedKey. A DynamoDB FilterExpression is applied
    after the page is read, so an empty page alongside a present key means the match is on a later
    page; the mock must page for the same reason the real helper does, or a handler test asserting
    an existence check reads a narrower helper than the one that ships."""
    while True:
        response = table.query(**query_kwargs)
        if response.get('Items'):
            return True

        if 'LastEvaluatedKey' not in response:
            return False
        query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']


def to_update_expr(record, op="SET"):
    """Mock of common.dynamodb.to_update_expr — builds the same placeholder-based
    update expression the real helper builds, so a handler that binds this name at
    import time still produces an observable expression."""
    keys = record.keys()
    keys_attr_names = ["#f{n}".format(n=x) for x in range(len(keys))]
    values_attr_names = [":v{n}".format(n=x) for x in range(len(keys))]
    keys_map = {k: key for k, key in zip(keys_attr_names, keys)}
    values_map = {v1: record[v] for v, v1 in zip(keys, values_attr_names)}
    expr = "{op} ".format(op=op) + ", ".join(
        "{f} = {v}".format(f=f, v=v)
        for f, v in zip(keys_attr_names, values_attr_names))
    return keys_map, values_map, expr


def get_asset_object_from_id(database_id, asset_id):
    """
    Mock implementation of the get_asset_object_from_id function for testing purposes.
    
    Args:
        asset_id: The ID of the asset to retrieve
        
    Returns:
        Dictionary containing the asset object with the given ID
    """
    # In the mock implementation, we return a simple asset object
    return {
        "assetId": asset_id,
        "databaseId": database_id,
        "assetName": "Test Asset",
        "assetType": "model/gltf-binary",
        "assetSize": 1024,
        "assetOwnerID": "test_email@amazon.com",
        "assetOwnerUsername": "test_email@amazon.com",
        "dateCreated": "2023-07-06T21:32:15.066148Z",
        "dateModified": "2023-07-06T21:32:15.066148Z"
    }
