"""Role API models for VAMS."""

from typing import Optional, Literal, List, Union
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from common.validators import validate, object_name_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="RoleModels")

######################## Constraint API Models ##########################

class ConstraintCriteriaModel(BaseModel, extra='ignore'):
    """Model for constraint criteria (AND/OR)"""
    field: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    operator: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    value: Union[str, List[str]]


class GroupPermissionModel(BaseModel, extra='ignore'):
    """Model for group permissions in constraints"""
    groupId: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    permission: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    permissionType: str = Field(min_length=1, max_length=256, strip_whitespace=True)


class UserPermissionModel(BaseModel, extra='ignore'):
    """Model for user permissions in constraints"""
    userId: str = Field(min_length=3, max_length=256, strip_whitespace=True)
    permission: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    permissionType: str = Field(min_length=1, max_length=256, strip_whitespace=True)


class GetConstraintsRequestModel(BaseModel, extra='ignore'):
    """Request model for listing constraints"""
    maxItems: Optional[int] = Field(default=30000, ge=1)
    pageSize: Optional[int] = Field(default=10000, ge=1)
    startingToken: Optional[str] = None


class CreateConstraintRequestModel(BaseModel, extra='ignore'):
    """Request model for creating/updating a constraint"""
    identifier: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    name: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    objectType: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    criteriaAnd: Optional[List[ConstraintCriteriaModel]] = []
    criteriaOr: Optional[List[ConstraintCriteriaModel]] = []
    groupPermissions: Optional[List[GroupPermissionModel]] = []
    userPermissions: Optional[List[UserPermissionModel]] = []
    
    @root_validator
    def validate_fields(cls, values):
        """Validate constraint fields"""
        # Import here to avoid circular dependency
        from common.constants import (
            ALLOWED_CONSTRAINT_PERMISSIONS,
            ALLOWED_CONSTRAINT_PERMISSION_TYPES,
            ALLOWED_CONSTRAINT_OBJECT_TYPES,
            ALLOWED_CONSTRAINT_OPERATORS
        )
        
        # Validate identifier
        (valid, message) = validate({
            'identifier': {
                'value': values.get('identifier'),
                'validator': 'OBJECT_NAME'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate name
        (valid, message) = validate({
            'name': {
                'value': values.get('name'),
                'validator': 'OBJECT_NAME'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate description
        (valid, message) = validate({
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate objectType
        object_type = values.get('objectType')
        if object_type not in ALLOWED_CONSTRAINT_OBJECT_TYPES:
            message = f"Invalid objectType. Allowed values: {', '.join(ALLOWED_CONSTRAINT_OBJECT_TYPES)}"
            logger.error(message)
            raise ValueError(message)
        
        # Validate that at least one criteria exists
        criteria_and = values.get('criteriaAnd', [])
        criteria_or = values.get('criteriaOr', [])
        
        if not criteria_and and not criteria_or:
            message = "Constraint must include criteriaOr or criteriaAnd statements"
            logger.error(message)
            raise ValueError(message)
        
        total_criteria = len(criteria_and) + len(criteria_or)
        if total_criteria == 0:
            message = "Constraint must include criteriaOr or criteriaAnd statements"
            logger.error(message)
            raise ValueError(message)
        
        # Validate criteriaAnd operators and values
        for criteria in criteria_and:
            if criteria.operator not in ALLOWED_CONSTRAINT_OPERATORS:
                message = f"Invalid operator in criteriaAnd. Allowed values: {', '.join(ALLOWED_CONSTRAINT_OPERATORS)}"
                logger.error(message)
                raise ValueError(message)
            
            # Validate regex pattern in value
            (valid, message) = validate({
                'criteriaAndValue': {
                    'value': criteria.value if isinstance(criteria.value, str) else str(criteria.value),
                    'validator': 'REGEX',
                    'allowGlobalKeyword': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        
        # Validate criteriaOr operators and values
        for criteria in criteria_or:
            if criteria.operator not in ALLOWED_CONSTRAINT_OPERATORS:
                message = f"Invalid operator in criteriaOr. Allowed values: {', '.join(ALLOWED_CONSTRAINT_OPERATORS)}"
                logger.error(message)
                raise ValueError(message)
            
            # Validate regex pattern in value
            (valid, message) = validate({
                'criteriaOrValue': {
                    'value': criteria.value if isinstance(criteria.value, str) else str(criteria.value),
                    'validator': 'REGEX',
                    'allowGlobalKeyword': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        
        # Validate groupPermissions
        for group_perm in values.get('groupPermissions', []):
            # Validate groupId
            (valid, message) = validate({
                'groupId': {
                    'value': group_perm.groupId,
                    'validator': 'OBJECT_NAME'
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
            
            # Validate permission
            if group_perm.permission not in ALLOWED_CONSTRAINT_PERMISSIONS:
                message = f"Invalid permission in groupPermissions. Allowed values: {', '.join(ALLOWED_CONSTRAINT_PERMISSIONS)}"
                logger.error(message)
                raise ValueError(message)
            
            # Validate permissionType
            if group_perm.permissionType not in ALLOWED_CONSTRAINT_PERMISSION_TYPES:
                message = f"Invalid permissionType in groupPermissions. Allowed values: {', '.join(ALLOWED_CONSTRAINT_PERMISSION_TYPES)}"
                logger.error(message)
                raise ValueError(message)
        
        # Validate userPermissions
        for user_perm in values.get('userPermissions', []):
            # Validate userId
            (valid, message) = validate({
                'userId': {
                    'value': user_perm.userId,
                    'validator': 'USERID'
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
            
            # Validate permission
            if user_perm.permission not in ALLOWED_CONSTRAINT_PERMISSIONS:
                message = f"Invalid permission in userPermissions. Allowed values: {', '.join(ALLOWED_CONSTRAINT_PERMISSIONS)}"
                logger.error(message)
                raise ValueError(message)
            
            # Validate permissionType
            if user_perm.permissionType not in ALLOWED_CONSTRAINT_PERMISSION_TYPES:
                message = f"Invalid permissionType in userPermissions. Allowed values: {', '.join(ALLOWED_CONSTRAINT_PERMISSION_TYPES)}"
                logger.error(message)
                raise ValueError(message)
        
        return values


class ConstraintResponseModel(BaseModel, extra='ignore'):
    """Response model for constraint data"""
    constraintId: str
    name: str
    description: str
    objectType: str
    criteriaAnd: Optional[List[ConstraintCriteriaModel]] = []
    criteriaOr: Optional[List[ConstraintCriteriaModel]] = []
    groupPermissions: Optional[List[GroupPermissionModel]] = []
    userPermissions: Optional[List[UserPermissionModel]] = []


class ConstraintOperationResponseModel(BaseModel, extra='ignore'):
    """Response model for constraint operations (create, update, delete)"""
    success: bool
    message: str
    constraintId: str
    operation: Literal["create", "update", "delete"]
    timestamp: str


######################## Constraint Template Import Models ##########################

class TemplateVariableDefinition(BaseModel, extra='ignore'):
    """Variable definition within a permission template"""
    name: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    required: Optional[bool] = True
    description: Optional[str] = Field(None, max_length=512)
    default: Optional[str] = None


class TemplateConstraintPermission(BaseModel, extra='ignore'):
    """Permission entry within a template constraint (template format)"""
    action: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    type: str = Field(default="allow", min_length=1, max_length=256, strip_whitespace=True)


class TemplateConstraintDefinition(BaseModel, extra='ignore'):
    """A single constraint definition within a permission template"""
    name: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    objectType: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    criteriaAnd: Optional[List[ConstraintCriteriaModel]] = []
    criteriaOr: Optional[List[ConstraintCriteriaModel]] = []
    groupPermissions: List[TemplateConstraintPermission]


class TemplateMetadata(BaseModel, extra='ignore'):
    """Metadata about a permission template"""
    name: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    description: Optional[str] = Field(None, max_length=512)
    version: Optional[str] = Field(default="1.0", max_length=50)


class ImportConstraintsTemplateRequestModel(BaseModel, extra='ignore'):
    """Request model for importing constraints from a permission template"""
    template: Optional[TemplateMetadata] = None
    variables: Optional[List[TemplateVariableDefinition]] = []
    variableValues: dict  # {"DATABASE_ID": "my-db", "ROLE_NAME": "my-admin"}
    constraints: List[TemplateConstraintDefinition]

    @root_validator
    def validate_template_import(cls, values):
        """Validate template import request"""
        from common.constants import (
            ALLOWED_CONSTRAINT_PERMISSIONS,
            ALLOWED_CONSTRAINT_PERMISSION_TYPES,
            ALLOWED_CONSTRAINT_OBJECT_TYPES,
            ALLOWED_CONSTRAINT_OPERATORS
        )

        variable_values = values.get('variableValues', {})

        # Validate ROLE_NAME is provided (required for groupId)
        if 'ROLE_NAME' not in variable_values:
            raise ValueError("variableValues must include 'ROLE_NAME' (used as groupId for all constraints)")

        # Validate ROLE_NAME format
        (valid, message) = validate({
            'ROLE_NAME': {
                'value': variable_values['ROLE_NAME'],
                'validator': 'OBJECT_NAME'
            }
        })
        if not valid:
            raise ValueError(f"Invalid ROLE_NAME: {message}")

        # Validate all required variables are provided
        for var_def in values.get('variables', []):
            if var_def.required and var_def.name not in variable_values:
                if var_def.default is not None:
                    variable_values[var_def.name] = var_def.default
                else:
                    raise ValueError(
                        f"Required variable '{var_def.name}' not provided in variableValues. "
                        f"Description: {var_def.description}"
                    )

        # Validate constraints
        constraints = values.get('constraints', [])
        if not constraints:
            raise ValueError("At least one constraint is required")

        for constraint in constraints:
            # Validate objectType
            if constraint.objectType not in ALLOWED_CONSTRAINT_OBJECT_TYPES:
                raise ValueError(
                    f"Invalid objectType '{constraint.objectType}'. "
                    f"Allowed: {', '.join(ALLOWED_CONSTRAINT_OBJECT_TYPES)}"
                )

            # Validate criteria exist
            if not constraint.criteriaAnd and not constraint.criteriaOr:
                raise ValueError(
                    f"Constraint '{constraint.name}' must have at least one criteriaAnd or criteriaOr"
                )

            # Validate operators in criteria
            for criteria in (constraint.criteriaAnd or []) + (constraint.criteriaOr or []):
                if criteria.operator not in ALLOWED_CONSTRAINT_OPERATORS:
                    raise ValueError(
                        f"Invalid operator '{criteria.operator}'. "
                        f"Allowed: {', '.join(ALLOWED_CONSTRAINT_OPERATORS)}"
                    )

            # Validate permissions
            for perm in constraint.groupPermissions:
                if perm.action not in ALLOWED_CONSTRAINT_PERMISSIONS:
                    raise ValueError(
                        f"Invalid permission action '{perm.action}'. "
                        f"Allowed: {', '.join(ALLOWED_CONSTRAINT_PERMISSIONS)}"
                    )
                if perm.type not in ALLOWED_CONSTRAINT_PERMISSION_TYPES:
                    raise ValueError(
                        f"Invalid permission type '{perm.type}'. "
                        f"Allowed: {', '.join(ALLOWED_CONSTRAINT_PERMISSION_TYPES)}"
                    )

        return values


class ImportConstraintsTemplateResponseModel(BaseModel, extra='ignore'):
    """Response model for template import operations"""
    success: bool
    message: str
    constraintsCreated: int
    constraintIds: List[str]
    timestamp: str


######################## Role API Models ##########################

class GetRolesRequestModel(BaseModel, extra='ignore'):
    """Request model for listing roles"""
    maxItems: Optional[int] = Field(default=30000, ge=1)
    pageSize: Optional[int] = Field(default=3000, ge=1)
    startingToken: Optional[str] = None


class CreateRoleRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a role"""
    roleName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    source: Optional[str] = Field(None, max_length=256, strip_whitespace=True)
    sourceIdentifier: Optional[str] = Field(None, max_length=256, strip_whitespace=True)
    mfaRequired: Optional[bool] = False

    @root_validator
    def validate_fields(cls, values):
        """Validate role fields"""
        # Import here to avoid circular dependency
        from common.constants import ALLOWED_ROLE_SOURCES
        
        # Validate roleName
        (valid, message) = validate({
            'roleName': {
                'value': values.get('roleName'),
                'validator': 'OBJECT_NAME'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate description
        (valid, message) = validate({
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate source if provided
        if values.get('source'):
            (valid, message) = validate({
                'source': {
                    'value': values.get('source'),
                    'validator': 'STRING_256',
                    'optional': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
            
            # Check against allowed sources
            if values.get('source') not in ALLOWED_ROLE_SOURCES:
                message = f"Invalid source. Allowed values: {', '.join(ALLOWED_ROLE_SOURCES)}"
                logger.error(message)
                raise ValueError(message)
        
        # Validate sourceIdentifier if provided
        if values.get('sourceIdentifier'):
            (valid, message) = validate({
                'sourceIdentifier': {
                    'value': values.get('sourceIdentifier'),
                    'validator': 'STRING_256',
                    'optional': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        
        # Validate mfaRequired
        (valid, message) = validate({
            'mfaRequired': {
                'value': str(values.get('mfaRequired', False)),
                'validator': 'BOOL'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values


class UpdateRoleRequestModel(BaseModel, extra='ignore'):
    """Request model for updating a role"""
    roleName: str = Field(min_length=1, max_length=256, strip_whitespace=True, pattern=object_name_pattern)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    source: Optional[str] = Field(None, max_length=256, strip_whitespace=True)
    sourceIdentifier: Optional[str] = Field(None, max_length=256, strip_whitespace=True)
    mfaRequired: Optional[bool] = False

    @root_validator
    def validate_fields(cls, values):
        """Validate role fields"""
        # Import here to avoid circular dependency
        from common.constants import ALLOWED_ROLE_SOURCES
        
        # Validate roleName
        (valid, message) = validate({
            'roleName': {
                'value': values.get('roleName'),
                'validator': 'OBJECT_NAME'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate description
        (valid, message) = validate({
            'description': {
                'value': values.get('description'),
                'validator': 'STRING_256'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate source if provided
        if values.get('source'):
            (valid, message) = validate({
                'source': {
                    'value': values.get('source'),
                    'validator': 'STRING_256',
                    'optional': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
            
            # Check against allowed sources
            if values.get('source') not in ALLOWED_ROLE_SOURCES:
                message = f"Invalid source. Allowed values: {', '.join(ALLOWED_ROLE_SOURCES)}"
                logger.error(message)
                raise ValueError(message)
        
        # Validate sourceIdentifier if provided
        if values.get('sourceIdentifier'):
            (valid, message) = validate({
                'sourceIdentifier': {
                    'value': values.get('sourceIdentifier'),
                    'validator': 'STRING_256',
                    'optional': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        
        # Validate mfaRequired
        (valid, message) = validate({
            'mfaRequired': {
                'value': str(values.get('mfaRequired', False)),
                'validator': 'BOOL'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values


class DeleteRoleRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting a role"""
    confirmDelete: Optional[bool] = False


class RoleResponseModel(BaseModel, extra='ignore'):
    """Response model for role data"""
    id: Optional[str] = None
    roleName: str
    description: str
    createdOn: Optional[str] = None
    source: Optional[str] = None
    sourceIdentifier: Optional[str] = None
    mfaRequired: Optional[bool] = False


class GetRolesResponseModel(BaseModel, extra='ignore'):
    """Response model for listing roles (with legacy message wrapper)"""
    Items: list[RoleResponseModel]
    NextToken: Optional[str] = None


class RoleOperationResponseModel(BaseModel, extra='ignore'):
    """Response model for role operations (create, update, delete)"""
    success: bool
    message: str
    roleName: str
    operation: Literal["create", "update", "delete"]
    timestamp: str


######################## User Role API Models ##########################

class GetUserRolesRequestModel(BaseModel, extra='ignore'):
    """Request model for listing user roles"""
    maxItems: Optional[int] = Field(default=30000, ge=1)
    pageSize: Optional[int] = Field(default=3000, ge=1)
    startingToken: Optional[str] = None


class CreateUserRolesRequestModel(BaseModel, extra='ignore'):
    """Request model for creating user roles"""
    userId: str = Field(min_length=3, max_length=256, strip_whitespace=True)
    roleName: list[str] = Field(min_length=1)

    @root_validator
    def validate_fields(cls, values):
        """Validate user role fields"""
        # Validate userId
        (valid, message) = validate({
            'userId': {
                'value': values.get('userId'),
                'validator': 'USERID'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate roleName array
        role_names = values.get('roleName', [])
        if not role_names or len(role_names) == 0:
            message = "At least one role name is required"
            logger.error(message)
            raise ValueError(message)
        
        # Validate each role name
        (valid, message) = validate({
            'roleName': {
                'value': role_names,
                'validator': 'OBJECT_NAME_ARRAY'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values


class UpdateUserRolesRequestModel(BaseModel, extra='ignore'):
    """Request model for updating user roles"""
    userId: str = Field(min_length=3, max_length=256, strip_whitespace=True)
    roleName: list[str] = Field(min_length=1)

    @root_validator
    def validate_fields(cls, values):
        """Validate user role fields"""
        # Validate userId
        (valid, message) = validate({
            'userId': {
                'value': values.get('userId'),
                'validator': 'USERID'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        # Validate roleName array
        role_names = values.get('roleName', [])
        if not role_names or len(role_names) == 0:
            message = "At least one role name is required"
            logger.error(message)
            raise ValueError(message)
        
        # Validate each role name
        (valid, message) = validate({
            'roleName': {
                'value': role_names,
                'validator': 'OBJECT_NAME_ARRAY'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values


class DeleteUserRolesRequestModel(BaseModel, extra='ignore'):
    """Request model for deleting user roles"""
    userId: str = Field(min_length=3, max_length=256, strip_whitespace=True)

    @root_validator
    def validate_fields(cls, values):
        """Validate user role fields"""
        # Validate userId
        (valid, message) = validate({
            'userId': {
                'value': values.get('userId'),
                'validator': 'USERID'
            }
        })
        if not valid:
            logger.error(message)
            raise ValueError(message)
        
        return values


class UserRoleResponseModel(BaseModel, extra='ignore'):
    """Response model for user role data"""
    userId: str
    roleName: list[str]
    createdOn: Optional[str] = None


class GetUserRolesResponseModel(BaseModel, extra='ignore'):
    """Response model for listing user roles"""
    Items: list[UserRoleResponseModel]
    NextToken: Optional[str] = None


class UserRoleOperationResponseModel(BaseModel, extra='ignore'):
    """Response model for user role operations (create, update, delete)"""
    success: bool
    message: str
    userId: str
    operation: Literal["create", "update", "delete"]
    timestamp: str
