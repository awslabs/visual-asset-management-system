# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

def get_asset_object_from_id(asset_id):
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
        "databaseId": "test-database-id",
        "assetName": "Test Asset",
        "assetType": "model/gltf-binary",
        "assetSize": 1024,
        "assetOwnerID": "test_email@amazon.com",
        "assetOwnerUsername": "test_email@amazon.com",
        "dateCreated": "2023-07-06T21:32:15.066148Z",
        "dateModified": "2023-07-06T21:32:15.066148Z"
    }
