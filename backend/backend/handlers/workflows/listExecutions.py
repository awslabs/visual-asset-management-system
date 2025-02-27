#  Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
#  SPDX-License-Identifier: Apache-2.0

import json
import os
import boto3
import botocore
from boto3.dynamodb.conditions import Key
from common.constants import STANDARD_JSON_RESPONSE
from common.dynamodb import get_asset_object_from_id
from common.validators import validate
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info

claims_and_roles = {}
logger = safeLogger(service="ListExecutionsWorkflow")

sfn = boto3.client('stepfunctions')
dynamodb = boto3.resource('dynamodb')
main_rest_response = STANDARD_JSON_RESPONSE

try:
    workflow_execution_database = os.environ["WORKFLOW_EXECUTION_STORAGE_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps({"message": "Failed Loading Environment Variables"})


def get_executions(asset_id, workflow_id, query_params):
    asset_of_workflow = get_asset_object_from_id(asset_id)

    # Add Casbin Enforcer to check if the current user has permissions to GET the asset
    asset_of_workflow.update({
        "object__type": "asset"
    })

    asset_of_workflow_allowed = False

    if len(claims_and_roles["tokens"]) > 0:
        casbin_enforcer = CasbinEnforcer(claims_and_roles)
        if casbin_enforcer.enforce(asset_of_workflow, "GET"):
            asset_of_workflow_allowed = True

    if asset_of_workflow_allowed:
        logger.info("Listing executions")
        pk = f'{asset_id}-{workflow_id}'

        paginator = dynamodb.meta.client.get_paginator('query')
        page_iterator = paginator.paginate(
            TableName=workflow_execution_database,
            KeyConditionExpression=Key('pk').eq(pk),
            ScanIndexForward=False,
            PaginationConfig={
                'MaxItems': int(query_params['maxItems']),
                'PageSize': int(query_params['pageSize']),
                'StartingToken': query_params['startingToken']
            }
        ).build_full_result()

        result = {
            "Items": []
        }

        for item in page_iterator['Items']:
            try:
                logger.info("workflow execution: ")
                logger.info(item)
                # Add Casbin Enforcer to check if the current user has permissions to GET the workflow:
                item.update({
                    "object__type": "workflow"
                })
                if len(claims_and_roles["tokens"]) > 0:
                    casbin_enforcer = CasbinEnforcer(claims_and_roles)
                    if casbin_enforcer.enforce(item, "GET"):
                        assets = item.get('assets', [])
                        workflow_arn = item['workflow_arn']
                        execution_arn = workflow_arn.replace("stateMachine", "execution")
                        execution_arn = execution_arn + ":" + item['execution_id']
                        logger.info(execution_arn)

                        startDate = item.get('startDate', "")
                        stopDate = item.get('stopDate', "")
                        executionStatus = item.get('executionStatus', "")
                        executionId = item['execution_id']

                        #If we don't have start/stop information, this means we could still have a running process (or now stopped).
                        # Fetch it from step functions.
                        if not stopDate:
                            execution = sfn.describe_execution(
                                executionArn=execution_arn
                            )
                            logger.info(execution)
                            startDate = execution.get('startDate', "")
                            executionStatus = execution['status']
                            executionId = execution['name']

                            if startDate:
                                startDate = startDate.strftime("%m/%d/%Y, %H:%M:%S")
                            stopDate = execution.get('stopDate', "")
                            if stopDate:
                                stopDate = stopDate.strftime("%m/%d/%Y, %H:%M:%S")

                                #Update the execution table to add start/end date and Status once something is found to have stopped running
                                logger.info("Update Execution Table")
                                table = dynamodb.Table(workflow_execution_database)
                                table.put_item(
                                    Item={
                                        'pk': f'{asset_id}-{workflow_id}',
                                        'sk': item['execution_id'],
                                        'database_id': asset_of_workflow.get("databaseId"),
                                        'asset_id': asset_id,
                                        'workflow_id': workflow_id,
                                        'workflow_arn': workflow_arn,
                                        'execution_arn': execution_arn,
                                        'execution_id': item['execution_id'],
                                        'startDate': startDate,
                                        'stopDate': stopDate,
                                        'executionStatus': execution['status'],
                                        'assets': []
                                    }
                                )

                        result["Items"].append({
                            'executionId': executionId,
                            'executionStatus': executionStatus,
                            'startDate': startDate,
                            'stopDate': stopDate,
                            'Items': assets
                        })
            except Exception as e:
                logger.exception(e)
                logger.info("Continuing with trying to fetch exceutions...")

        if "NextToken" in page_iterator:
            result["NextToken"] = page_iterator["NextToken"]

        return {
            "statusCode": 200,
            "message": result
        }
    else:
        return {
            "statusCode": 403,
            "message": "Not Authorized"
        }


def lambda_handler(event, context):
    global claims_and_roles
    response = STANDARD_JSON_RESPONSE
    logger.info(event)

    pathParams = event.get('pathParameters', {})
    queryParameters = event.get('queryStringParameters', {})

    #Set 50 maxitems/page size to avoid performance issues with state machine API call throttling
    validate_pagination_info(queryParameters, 50)

    try:

        logger.info("Validating Parameters")
        (valid, message) = validate({
            'workflowId': {
                'value': pathParams.get('workflowId', ''),
                'validator': 'ID'
            },
            'assetId': {
                'value': pathParams.get('assetId', ''),
                'validator': 'ID'
            },
        })

        if not valid:
            logger.error(message)
            response['body'] = json.dumps({"message": message})
            response['statusCode'] = 400
            return response

        claims_and_roles = request_to_claims(event)
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if method_allowed_on_api:
            logger.info("Listing Workflow Executions")
            result = get_executions(pathParams['assetId'], pathParams['workflowId'], queryParameters)
            response['body'] = json.dumps({"message": result['message']})
            response['statusCode'] = result['statusCode']
            logger.info(response)
            return response
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response
    except botocore.exceptions.ClientError as err:
        if err.response['Error']['Code'] == 'LimitExceededException' or err.response['Error']['Code'] == 'ThrottlingException':
            logger.exception("Throttling Error")
            response['statusCode'] = err.response['ResponseMetadata']['HTTPStatusCode']
            response['body'] = json.dumps({"message": "ThrottlingException: Too many requests within a given period."})
            return response
        elif err.response['Error']['Code'] == 'ExecutionLimitExceeded':
            logger.exception("ExecutionLimitExceeded")
            response['statusCode'] = err.response['ResponseMetadata']['HTTPStatusCode']
            response['body'] = json.dumps({"message": "ExecutionLimitExceeded: Reached the maximum state machine execution limit of 1,000,000"})
            return response
        else:
            logger.exception(err)
            response['statusCode'] = 500
            response['body'] = json.dumps({"message": "Internal Server Error"})
            return response
    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response


if __name__ == "__main__":
    test_response = lambda_handler(None, None)
    logger.info(test_response)
