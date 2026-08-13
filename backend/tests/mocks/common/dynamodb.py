# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0


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
