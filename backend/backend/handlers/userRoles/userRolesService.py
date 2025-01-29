import os
import boto3
import json
import datetime

from common.validators import validate
from common.constants import STANDARD_JSON_RESPONSE
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from customLogging.logger import safeLogger
from common.dynamodb import validate_pagination_info
from boto3.dynamodb.conditions import Key
from boto3.dynamodb.types import TypeDeserializer

claims_and_roles = {}
logger = safeLogger(service="UserRolesService")
dynamodb = boto3.resource('dynamodb')
dynamodb_client = boto3.client('dynamodb')

main_rest_response = STANDARD_JSON_RESPONSE

try:
    roles_table_name = os.environ["ROLES_STORAGE_TABLE_NAME"]
    user_roles_table_name = os.environ["USER_ROLES_TABLE_NAME"]
except:
    logger.exception("Failed loading environment variables")
    main_rest_response['body'] = json.dumps({"message": "Failed Loading Environment Variables"})


def get_all_roles_for_user(user_id):
    resp = dynamodb_client.query(
        TableName=user_roles_table_name,
        KeyConditionExpression='userId = :id',
        ExpressionAttributeValues={':id': {'S': user_id}}
    )
    return resp.get('Items', [])


def get_role(roleName):
    resp = dynamodb_client.query(
        TableName=roles_table_name,
        KeyConditionExpression='roleName = :roleName',
        ExpressionAttributeValues={':roleName': {'S': roleName}}
    )
    return resp.get('Items', [])


def update_user_roles(body):
    response = STANDARD_JSON_RESPONSE
    user_role_table = dynamodb.Table(user_roles_table_name)
    items = get_all_roles_for_user(body["userId"])
    existing_roles = [item["roleName"]['S'] for item in items]
    roles_to_delete = list(set(existing_roles) - set(body["roleName"]))
    roles_to_create = list(set(body["roleName"]) - set(existing_roles))

    user_roles_to_create = []
    user_roles_to_delete = []

    for role in roles_to_create:
        itemsRole = get_role(role)
        if itemsRole and len(itemsRole) == 0:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Role does not exist in available roles"})
            return response
        
        create_user_role = {
            'userId': body["userId"],
            'roleName': role,
            'createdOn': str(datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        }
        # Add Casbin Enforcer to check if the current user has permissions to POST the User Role
        allowed = False
        create_user_role.update({"object__type": "userRole"})
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", create_user_role, "POST"):
                user_roles_to_create.append(create_user_role)
                allowed = True
                break
        if not allowed:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Action not allowed"})
            return response

    with user_role_table.batch_writer() as batch:
        for item in user_roles_to_create:
            batch.put_item(Item=item)

    for role in roles_to_delete:
        delete_user_role = {
            'userId': body["userId"],
            'roleName': role
        }
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the User Roles
        allowed = False
        temp_role_object = {}
        temp_role_object.update(delete_user_role)

        temp_role_object.update({"object__type": "userRole"})
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", temp_role_object, "DELETE"):
                user_roles_to_delete.append(delete_user_role)
                allowed = True
                break
        if not allowed:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Action not allowed"})
            return response

    with user_role_table.batch_writer() as batch:
        for keys in user_roles_to_delete:
            batch.delete_item(Key=keys)

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def delete_user_roles(body):
    response = STANDARD_JSON_RESPONSE
    user_role_table = dynamodb.Table(user_roles_table_name)
    items = get_all_roles_for_user(body["userId"])

    items_to_delete = []
    for role in items:
        user_role = {
            'userId': body["userId"],
            'roleName': role['roleName']['S']
        }
        # Add Casbin Enforcer to check if the current user has permissions to DELETE the User Role
        user_role.update({"object__type": "userRole"})
        allowed = False
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", user_role, "DELETE"):
                items_to_delete.append(user_role)
                allowed = True
                break
        if not allowed:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Action not allowed"})
            return response

    with user_role_table.batch_writer() as batch:
        for keys in items_to_delete:
            keys.pop("object__type")
            batch.delete_item(Key=keys)

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def is_any_user_role_already_existing(items, body):
    existing_roles = [f"{item['userId']['S']}---{item['roleName']['S']}" for item in items]
    new_roles = []
    for role in body["roleName"]:
        new_roles.append(f"{body['userId']}---{role}")

    logger.info("existing_roles...")
    logger.info(existing_roles)
    logger.info("new_roles...")
    logger.info(new_roles)

    for role in new_roles:
        if role in existing_roles:
            return True

    return False


def create_user_roles(body):
    response = STANDARD_JSON_RESPONSE
    user_role_table = dynamodb.Table(user_roles_table_name)
    itemsUserRoles = get_all_roles_for_user(body["userId"])
    logger.info(itemsUserRoles)
    logger.info(is_any_user_role_already_existing(itemsUserRoles, body))
    if is_any_user_role_already_existing(itemsUserRoles, body):
        response['statusCode'] = 400
        response['body'] = json.dumps({"message": "Role is already existing for this user."})
        return response
    
    items_to_insert = []
    for role in body["roleName"]:
        itemsRole = get_role(role)
        if itemsRole and len(itemsRole) == 0:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Role does not exist in available roles"})
            return response

        user_role = {
            'userId': body["userId"],
            'roleName': role,
            'createdOn': str(datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        }
        # Add Casbin Enforcer to check if the current user has permissions to POST the User Roles
        user_role.update({"object__type": "userRole"})
        allowed = False
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", user_role, "POST"):
                items_to_insert.append(user_role)
                allowed = True
                break
        if not allowed:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Action not allowed"})
            return response

    with user_role_table.batch_writer() as batch:
        for item in items_to_insert:
            batch.put_item(Item=item)

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": "success"})
    return response


def get_user_roles(query_params):
    #Custom pagination logic for user roles which may be very non-performant. 
    #TODO: Try to fix performance as this works across the whole users in role dataset every time before providing a subset page at the end. 
    response = STANDARD_JSON_RESPONSE
    deserializer = TypeDeserializer()
    paginator = dynamodb_client.get_paginator('scan')

    rawUserRoles = []
    page_iterator = paginator.paginate(
        TableName=user_roles_table_name,
        PaginationConfig={
            'MaxItems': 1000,
            'PageSize': 1000,
        }
    ).build_full_result()
    if(len(page_iterator["Items"]) > 0):
        rawUserRoles.extend(page_iterator["Items"])
        while("NextToken" in page_iterator):
            page_iterator = paginator.paginate(
                TableName=user_roles_table_name,
                PaginationConfig={
                    'MaxItems': 1000,
                    'PageSize': 1000,
                    'StartingToken': page_iterator["NextToken"]
                }
            ).build_full_result()
            if(len(page_iterator["Items"]) > 0):
                rawUserRoles.extend(page_iterator["Items"])


    grouped_data = {
        "Items": []
    }

    #Process all records initially, which may be large
    for user_role in page_iterator["Items"]:
        deserialized_document = {k: deserializer.deserialize(v) for k, v in user_role.items()}

        # Add Casbin Enforcer to check if the current user has permissions to GET the User Roles
        deserialized_document.update({
            "object__type": "userRole"
        })
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforce(f"user::{user_name}", deserialized_document, "GET"):

                userIdExists = False
                for item in grouped_data["Items"]:
                    if item["userId"] == deserialized_document["userId"]:
                        #Found record so just add the roleName to the existing record
                        item["roleName"].append(deserialized_document["roleName"])
                        userIdExists = True
                        break


                if userIdExists == False:
                    grouped_data["Items"].append({
                        "userId": deserialized_document["userId"],
                        "roleName": [deserialized_document["roleName"]],
                        "createdOn": deserialized_document["createdOn"]
                    })

    #Sort the list results by createdOn so we can properly paginate for NextToken later
    grouped_data["Items"].sort(key=lambda x: x["createdOn"])

    #Do custom pagination here for end results
    #Start record page at previous starting token if given, loop through sorted list (and deleteentries) until we get to starting token
    if "startingToken" in query_params:
        if query_params["startingToken"]:
            for item in grouped_data["Items"]:
                if item["createdOn"] != query_params["startingToken"]:
                    grouped_data["Items"].remove(item)
                else:
                    break

    #Prepare records for next page set
    #Set token for next page entry if exists and delete all records afterwards page
    nextIsToken = False
    startRemovingRecords = False
    recordCount = 0
    for item in grouped_data["Items"]:
        recordCount += 1
        if nextIsToken:
            #set next token as the createdOn of the next record in the list after the maxSize
            grouped_data['NextToken'] = item["createdOn"]
            nextIsToken = False
            startRemovingRecords = True
        if startRemovingRecords:
            #remove the rest of the records after the next token
            grouped_data["Items"].remove(item)
        if recordCount == int(query_params["maxItems"]):
            nextIsToken = True

    response['statusCode'] = 200
    response['body'] = json.dumps({"message": grouped_data})
    return response

########################################################

    # page_iterator = paginator.paginate(
    #     TableName=user_roles_table_name,
    #     PaginationConfig={
    #         'MaxItems': int(query_params['maxItems']),
    #         'PageSize': int(query_params['pageSize']),
    #         'StartingToken': query_params['startingToken']
    #     }
    # ).build_full_result()

    # grouped_data = {
    #     "Items": []
    # }

    # for user_role in page_iterator["Items"]:
    #     deserialized_document = {k: deserializer.deserialize(v) for k, v in user_role.items()}

    #     # Add Casbin Enforcer to check if the current user has permissions to GET the User Roles
    #     deserialized_document.update({
    #         "object__type": "userRole"
    #     })
    #     for user_name in claims_and_roles["tokens"]:
    #         casbin_enforcer = CasbinEnforcer(user_name)
    #         if casbin_enforcer.enforce(f"user::{user_name}", deserialized_document, "GET"):

    #             userIdExists = False
    #             for item in grouped_data["Items"]:
    #                 if item["userId"] == deserialized_document["userId"]:
    #                     #Found record so just add the roleName to the existing record
    #                     item["roleName"].append(deserialized_document["roleName"])
    #                     userIdExists = True
    #                     break


    #             if userIdExists == False:
    #                 grouped_data["Items"].append({
    #                     "userId": deserialized_document["userId"],
    #                     "roleName": [deserialized_document["roleName"]],
    #                     "createdOn": deserialized_document["createdOn"]
    #                 })

    # if 'NextToken' in page_iterator:
    #     grouped_data['NextToken'] = page_iterator['NextToken']

########################################################


def lambda_handler(event, context):
    response = STANDARD_JSON_RESPONSE
    try:
        http_method = event['requestContext']['http']['method']

        global claims_and_roles
        claims_and_roles = request_to_claims(event)

        queryParameters = event.get('queryStringParameters', {})
        validate_pagination_info(queryParameters)

        method_allowed_on_api = False
        for user_name in claims_and_roles["tokens"]:
            casbin_enforcer = CasbinEnforcer(user_name)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True
                break
        if http_method == 'GET' and method_allowed_on_api:
            return get_user_roles(queryParameters)

        if isinstance(event['body'], str):
            event['body'] = json.loads(event['body'])

        if http_method == 'POST' and method_allowed_on_api:
            if 'roleName' not in event['body'] or 'userId' not in event['body']:
                message = "RoleName and userId are required."
                response['statusCode'] = 400
                response['body'] = json.dumps({"message": message})
                return response

            (valid, message) = validate({
                'roleName': {
                    'value': event['body']['roleName'],
                    'validator': 'OBJECT_NAME_ARRAY'
                },
                'userId': {
                    'value': event['body']['userId'],
                    'validator': 'USERID'
                }
            })
            if not valid:
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response
            return create_user_roles(event['body'])
        elif http_method == 'PUT' and method_allowed_on_api:
            if 'roleName' not in event['body'] or 'userId' not in event['body']:
                message = "RoleName and userId are required."
                response['statusCode'] = 400
                response['body'] = json.dumps({"message": message})
                return response

            (valid, message) = validate({
                'roleName': {
                    'value': event['body']['roleName'],
                    'validator': 'OBJECT_NAME_ARRAY'
                },
                'userId': {
                    'value': event['body']['userId'],
                    'validator': 'USERID'
                }
            })
            if not valid:
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response
            return update_user_roles(event['body'])
        elif http_method == 'DELETE' and method_allowed_on_api:
            if not event['body'].get('userId'):
                message = "userId is required."
                response['statusCode'] = 400
                response['body'] = json.dumps({"message": message})
                return response

            (valid, message) = validate({
                'userId': {
                    'value': event['body']['userId'],
                    'validator': 'USERID'
                }
            })
            if not valid:
                response['body'] = json.dumps({"message": message})
                response['statusCode'] = 400
                return response
            return delete_user_roles(event['body'])
        else:
            response['statusCode'] = 403
            response['body'] = json.dumps({"message": "Not Authorized"})
            return response

    except Exception as e:
        logger.exception(e)
        response['statusCode'] = 500
        response['body'] = json.dumps({"message": "Internal Server Error"})
        return response
