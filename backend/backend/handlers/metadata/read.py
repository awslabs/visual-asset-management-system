import os
import sys
import json
from decimal import Decimal
import traceback

from backend.handlers.metadata import logger, mask_sensitive_data, build_response, table, validate_event, ValidationError


def get_metadata(assetId):
    resp = table.get_item(
        Key={
            "pk": assetId,
            "sk": assetId,
        }
    )
    return resp['Item']


def lambda_handler(event, context):
    logger.info(mask_sensitive_data(event))
    try:
        validate_event(event)
        assetId = event['pathParameters']['assetId']

        return build_response(200, json.dumps({
            "version": "1", 
            "metadata": get_metadata(assetId)
        }))

    except ValidationError as ex:
        logger.info(traceback.format_exc())
        return build_response(ex.code, json.dumps(ex.resp))
    except Exception as ex:
        logger.error(traceback.format_exc())
        return build_response(500, "Server Error")
