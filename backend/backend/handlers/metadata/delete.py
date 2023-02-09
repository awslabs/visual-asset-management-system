import os
import sys
import json
from decimal import Decimal
from backend.common.validators import validate
import traceback

from backend.handlers.metadata import logger, mask_sensitive_data, build_response, ValidationError, table, validate_event


def delete_item(assetId):
    table.delete_item(
        Key={
            "pk": assetId,
            "sk": assetId,
        }
    )

def lambda_handler(event, context):
    logger.info(mask_sensitive_data(event))
    try:
        validate_event(event)
        assetId = event['pathParameters']['assetId']
        delete_item(assetId)        
        response = { "status": "OK", "message": "{assetId} deleted".format(assetId=assetId) }
        return build_response(200, json.dumps(response))
    except ValidationError as ex:
        logger.info(traceback.format_exc())
        return build_response(ex.code, json.dumps(ex.resp))
    except Exception as ex:
        logger.error(traceback.format_exc())
        return build_response(500, "Server Error")
