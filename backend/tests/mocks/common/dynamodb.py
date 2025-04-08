def get_asset_object_from_id(asset_id):
    """
    Mock implementation of the get_asset_object_from_id function.
    """
    return {"assetId": asset_id}

def validate_pagination_info(query_parameters):
    """
    Mock implementation of the validate_pagination_info function.
    """
    if 'maxItems' not in query_parameters:
        query_parameters['maxItems'] = '10'
    if 'pageSize' not in query_parameters:
        query_parameters['pageSize'] = '10'
    if 'startingToken' not in query_parameters:
        query_parameters['startingToken'] = ''
    return query_parameters
