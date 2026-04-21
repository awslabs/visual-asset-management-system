# Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Auth Constraints Template Import service handler for VAMS API."""

import os
import boto3
import json
import re
import uuid
from datetime import datetime
from boto3.dynamodb.conditions import Key
from botocore.config import Config
from aws_lambda_powertools.utilities.typing import LambdaContext
from aws_lambda_powertools.utilities.parser import parse, ValidationError
from common.constants import (
    STANDARD_JSON_RESPONSE,
    ALLOWED_CONSTRAINT_PERMISSIONS,
    ALLOWED_CONSTRAINT_PERMISSION_TYPES,
    ALLOWED_CONSTRAINT_OBJECT_TYPES,
    ALLOWED_CONSTRAINT_OPERATORS
)
from common.validators import validate
from handlers.authz import CasbinEnforcer
from handlers.auth import request_to_claims
from customLogging.logger import safeLogger
from customLogging.auditLogging import log_auth_changes
from models.common import (
    APIGatewayProxyResponseV2, internal_error, success,
    validation_error, general_error, authorization_error,
    VAMSGeneralErrorResponse
)
from models.roleConstraints import (
    ImportConstraintsTemplateRequestModel,
    ImportConstraintsTemplateResponseModel
)

# Configure AWS clients with retry configuration
retry_config = Config(
    retries={
        'max_attempts': 5,
        'mode': 'adaptive'
    }
)

dynamodb = boto3.resource('dynamodb', config=retry_config)
logger = safeLogger(service_name="AuthConstraintsTemplateService")

# Global variables for claims and roles
claims_and_roles = {}

# Load environment variables with error handling
try:
    constraints_table_name = os.environ["CONSTRAINTS_TABLE_NAME"]
    roles_table_name = os.environ.get("ROLES_TABLE_NAME")
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

# Initialize DynamoDB tables
constraints_table = dynamodb.Table(constraints_table_name)
roles_table = dynamodb.Table(roles_table_name) if roles_table_name else None


#######################
# Helper Functions
#######################

def substitute_variables(constraints_data, var_values):
    """Replace {{VAR}} placeholders in all string fields within constraint data.

    Operates on the constraint list after parsing, replacing variable
    references in name, description, and criteria value fields.

    Args:
        constraints_data: List of constraint dictionaries
        var_values: Dictionary of variable name -> value mappings

    Returns:
        List of constraints with variables substituted
    """
    def replace_in_string(s):
        if not isinstance(s, str):
            return s
        for var_name, var_value in var_values.items():
            s = s.replace("{{" + var_name + "}}", str(var_value))
        return s

    substituted = []
    for constraint in constraints_data:
        new_constraint = {}
        for key, value in constraint.items():
            if isinstance(value, str):
                new_constraint[key] = replace_in_string(value)
            elif isinstance(value, list):
                new_list = []
                for item in value:
                    if isinstance(item, dict):
                        new_item = {}
                        for k, v in item.items():
                            if isinstance(v, str):
                                new_item[k] = replace_in_string(v)
                            elif isinstance(v, list):
                                new_item[k] = [replace_in_string(i) if isinstance(i, str) else i for i in v]
                            else:
                                new_item[k] = v
                        new_list.append(new_item)
                    elif isinstance(item, str):
                        new_list.append(replace_in_string(item))
                    else:
                        new_list.append(item)
                new_constraint[key] = new_list
            else:
                new_constraint[key] = value
        substituted.append(new_constraint)
    return substituted


def find_unreplaced_variables(constraints_data):
    """Scan constraints for remaining {{VAR}} patterns after substitution.

    Args:
        constraints_data: List of constraint dictionaries after substitution

    Returns:
        Set of unreplaced variable names found
    """
    pattern = re.compile(r'\{\{(\w+)\}\}')
    unreplaced = set()

    def scan_value(value):
        if isinstance(value, str):
            matches = pattern.findall(value)
            unreplaced.update(matches)
        elif isinstance(value, list):
            for item in value:
                scan_value(item)
        elif isinstance(value, dict):
            for v in value.values():
                scan_value(v)

    for constraint in constraints_data:
        for key, value in constraint.items():
            scan_value(value)

    return unreplaced


def validate_constraint_role_exists(group_id):
    """Validate that a role/group exists in the roles table

    Args:
        group_id: The role/group ID to validate

    Returns:
        True if role exists, False otherwise
    """
    if not roles_table:
        logger.warning(f"Roles table not configured, skipping role validation for {group_id}")
        return True

    try:
        role_response = roles_table.get_item(Key={'roleName': group_id})
        return 'Item' in role_response
    except Exception as e:
        logger.warning(f"Could not validate groupId '{group_id}': {e}")
        return True  # Allow if validation fails


def _transform_to_denormalized_format(constraint_data):
    """Transform constraint data to denormalized table format.
    Creates one item per UNIQUE group/user for efficient GSI queries.

    Args:
        constraint_data: Constraint data in API format

    Returns:
        List of items formatted for denormalized ConstraintsStorageTable
    """
    items = []
    base_constraint_id = constraint_data['identifier']

    # Base constraint data shared across all denormalized items
    base_item = {
        'name': constraint_data.get('name', ''),
        'description': constraint_data.get('description', ''),
        'objectType': constraint_data.get('objectType', ''),
        # Store complex data as JSON strings
        'criteriaAnd': json.dumps(constraint_data.get('criteriaAnd', [])),
        'criteriaOr': json.dumps(constraint_data.get('criteriaOr', [])),
        'groupPermissions': json.dumps(constraint_data.get('groupPermissions', [])),
        'userPermissions': json.dumps(constraint_data.get('userPermissions', [])),
        # Metadata
        'dateCreated': constraint_data.get('dateCreated', datetime.utcnow().isoformat()),
        'dateModified': datetime.utcnow().isoformat(),
        'createdBy': constraint_data.get('createdBy', 'SYSTEM_USER'),
        'modifiedBy': constraint_data.get('modifiedBy', 'SYSTEM_USER'),
    }

    # Create one item per UNIQUE groupId (not per permission)
    group_permissions = constraint_data.get('groupPermissions', [])
    unique_group_ids = set()
    for group_perm in group_permissions:
        group_id = group_perm.get('groupId')
        if group_id and group_id not in unique_group_ids:
            unique_group_ids.add(group_id)
            item = base_item.copy()
            item['constraintId'] = f"{base_constraint_id}#group#{group_id}"
            item['groupId'] = group_id  # For GroupPermissionsIndex GSI
            items.append(item)

    # Create one item per UNIQUE userId (not per permission)
    user_permissions = constraint_data.get('userPermissions', [])
    unique_user_ids = set()
    for user_perm in user_permissions:
        user_id = user_perm.get('userId')
        if user_id and user_id not in unique_user_ids:
            unique_user_ids.add(user_id)
            item = base_item.copy()
            item['constraintId'] = f"{base_constraint_id}#user#{user_id}"
            item['userId'] = user_id  # For UserPermissionsIndex GSI
            items.append(item)

    # Safety: If no permissions exist, create one base item
    if len(items) == 0:
        item = base_item.copy()
        item['constraintId'] = base_constraint_id
        items.append(item)

    return items


def build_constraint_data(constraint, role_name, claims_and_roles):
    """Convert template constraint format to API constraint format.

    Generates constraintId UUID, maps action->permission and type->permissionType,
    generates UUID ids for each groupPermission, and sets groupId to role_name.

    Args:
        constraint: Dictionary with template constraint data (after variable substitution)
        role_name: The role name to use as groupId
        claims_and_roles: User claims and roles for metadata

    Returns:
        Dictionary in API constraint format ready for DynamoDB
    """
    constraint_id = str(uuid.uuid4())
    now = datetime.utcnow().isoformat()
    username = claims_and_roles["tokens"][0] if claims_and_roles.get("tokens") else "system"

    # Map template groupPermissions to API format
    group_permissions = []
    for perm in constraint.get('groupPermissions', []):
        group_permissions.append({
            'id': str(uuid.uuid4()),
            'groupId': role_name,
            'permission': perm['action'],
            'permissionType': perm['type']
        })

    # Build API constraint data
    constraint_data = {
        'identifier': constraint_id,
        'name': constraint['name'],
        'description': constraint['description'],
        'objectType': constraint['objectType'],
        'criteriaAnd': constraint.get('criteriaAnd', []),
        'criteriaOr': constraint.get('criteriaOr', []),
        'groupPermissions': group_permissions,
        'userPermissions': [],
        'dateCreated': now,
        'dateModified': now,
        'createdBy': username,
        'modifiedBy': username,
    }

    return constraint_data


#######################
# Business Logic
#######################

def import_template_constraints(template_data, claims_and_roles):
    """Main orchestration for importing constraints from a template.

    1. Extract variableValues and ROLE_NAME
    2. Substitute variables in all constraint fields
    3. Check for unreplaced variables
    4. Validate role exists (warn if not)
    5. For each constraint: build API payload, write to DynamoDB
    6. Return response with count and list of created constraintIds

    Args:
        template_data: Validated ImportConstraintsTemplateRequestModel dict
        claims_and_roles: User claims and roles for authorization

    Returns:
        ImportConstraintsTemplateResponseModel
    """
    variable_values = template_data.get('variableValues', {})
    role_name = variable_values['ROLE_NAME']
    constraints = template_data.get('constraints', [])

    # Convert Pydantic models to dicts for substitution
    constraints_dicts = []
    for c in constraints:
        c_dict = {
            'name': c['name'],
            'description': c['description'],
            'objectType': c['objectType'],
            'criteriaAnd': c.get('criteriaAnd', []),
            'criteriaOr': c.get('criteriaOr', []),
            'groupPermissions': c.get('groupPermissions', []),
        }
        constraints_dicts.append(c_dict)

    # Substitute variables
    substituted_constraints = substitute_variables(constraints_dicts, variable_values)

    # Check for unreplaced variables
    unreplaced = find_unreplaced_variables(substituted_constraints)
    if unreplaced:
        raise VAMSGeneralErrorResponse(
            f"Unreplaced template variables found after substitution: {', '.join(sorted(unreplaced))}. "
            f"Provide values for these in variableValues."
        )

    # Validate role exists (warn but don't block)
    role_exists = validate_constraint_role_exists(role_name)
    if not role_exists:
        logger.warning(f"Role '{role_name}' does not exist in roles table. "
                       f"Constraints will be created but may not be effective until the role is created.")

    # Create each constraint
    created_constraint_ids = []
    for constraint in substituted_constraints:
        constraint_data = build_constraint_data(constraint, role_name, claims_and_roles)
        constraint_id = constraint_data['identifier']

        logger.info(f"Creating constraint '{constraint['name']}' with ID {constraint_id}")

        # Transform to denormalized format and write to DynamoDB
        denormalized_items = _transform_to_denormalized_format(constraint_data)

        with constraints_table.batch_writer() as batch:
            for item in denormalized_items:
                batch.put_item(Item=item)

        logger.info(f"Successfully wrote {len(denormalized_items)} denormalized items for constraint {constraint_id}")
        created_constraint_ids.append(constraint_id)

    now = datetime.utcnow().isoformat()
    template_name = template_data.get('template', {}).get('name', 'unknown') if template_data.get('template') else 'unknown'

    return ImportConstraintsTemplateResponseModel(
        success=True,
        message=f"Successfully imported {len(created_constraint_ids)} constraints from template '{template_name}' for role '{role_name}'",
        constraintsCreated=len(created_constraint_ids),
        constraintIds=created_constraint_ids,
        timestamp=now
    )


#######################
# Lambda Handler
#######################

def lambda_handler(event, context: LambdaContext) -> APIGatewayProxyResponseV2:
    """Lambda handler for auth constraints template import API"""
    global claims_and_roles
    claims_and_roles = request_to_claims(event)

    try:
        # Parse request
        path = event['requestContext']['http']['path']
        method = event['requestContext']['http']['method']

        logger.info(f"Processing {method} request for path: {path}")

        # Check API authorization
        method_allowed_on_api = False
        if len(claims_and_roles["tokens"]) > 0:
            casbin_enforcer = CasbinEnforcer(claims_and_roles)
            if casbin_enforcer.enforceAPI(event):
                method_allowed_on_api = True

        if not method_allowed_on_api:
            return authorization_error()

        # Only handle POST method
        if method != 'POST':
            return validation_error(body={'message': "Method not allowed. Only POST is supported."}, event=event)

        # Parse request body
        body = event.get('body')
        if not body:
            return validation_error(body={'message': "Request body is required"}, event=event)

        # Parse JSON body safely
        if isinstance(body, str):
            try:
                body = json.loads(body)
            except json.JSONDecodeError as e:
                logger.exception(f"Invalid JSON in request body: {e}")
                return validation_error(body={'message': "Invalid JSON in request body"}, event=event)
        elif isinstance(body, dict):
            body = body
        else:
            logger.error("Request body is not a string or dict")
            return validation_error(body={'message': "Request body cannot be parsed"}, event=event)

        # Parse and validate the request model
        request_model = parse(body, model=ImportConstraintsTemplateRequestModel)

        # Import constraints from template
        result = import_template_constraints(
            request_model.dict(exclude_unset=True),
            claims_and_roles
        )

        # AUDIT LOG: Template import
        log_auth_changes(event, "constraintsTemplateImport", {
            "templateName": request_model.template.name if request_model.template else "unknown",
            "roleName": request_model.variableValues.get('ROLE_NAME', 'unknown'),
            "constraintsCreated": result.constraintsCreated,
            "constraintIds": result.constraintIds
        })

        # Return success response
        return success(body=result.dict())

    except ValidationError as v:
        logger.exception(f"Validation error: {v}")
        return validation_error(body={'message': str(v)}, event=event)
    except VAMSGeneralErrorResponse as v:
        logger.exception(f"VAMS error: {v}")
        return general_error(body={'message': str(v)}, event=event)
    except Exception as e:
        logger.exception(f"Internal error: {e}")
        return internal_error(event=event)
