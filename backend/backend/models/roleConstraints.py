"""Role API models for VAMS."""

from typing import Optional, Literal, List, Union
from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator
from common.validators import validate, object_name_pattern
from customLogging.logger import safeLogger

logger = safeLogger(service_name="RoleModels")

# Bounds on constraint collections. Each criterion becomes a clause in the Casbin
# obj_rule expression evaluated on every authorization check, and each unique
# group/user permission becomes its own denormalized DynamoDB item, so an unbounded
# list drives both per-request authz cost and write fan-out. The caps are generous
# relative to real constraints -- the shipped permission templates use single-digit
# criteria counts and at most 18 criteria in one constraint.
MAX_CRITERIA_PER_CONSTRAINT = 100
MAX_PERMISSIONS_PER_CONSTRAINT = 100
# Each element of a list-valued criterion is a separate regex alternative.
MAX_CRITERIA_VALUES = 100
# One template import writes every constraint in a single request.
MAX_CONSTRAINTS_PER_TEMPLATE = 200
MAX_TEMPLATE_VARIABLES = 100
# Variable names/values are substituted into constraint names, descriptions, and
# criteria values, all of which are themselves bounded at 256 characters.
MAX_TEMPLATE_VARIABLE_NAME_LENGTH = 256
MAX_TEMPLATE_VARIABLE_VALUE_LENGTH = 256
# Roles assigned to one user in a single request.
MAX_ROLES_PER_USER_REQUEST = 500
# Pagination ceilings: a page must fit the 6 MB Lambda response limit.
MAX_LIST_PAGE_SIZE = 10000
MAX_LIST_MAX_ITEMS = 30000
# Opaque base64 pagination tokens; matches the execution/history listing bound.
MAX_LIST_TOKEN_LENGTH = 4096

# The operators whose obj_rule is a regexMatch(...) call. Casbin evaluates that through Python's `re`,
# which raises TypeError on a list — and CasbinEnforcer catches the exception and returns False, so ONE
# such criterion makes a role fail EVERY check on that object type, including entities the criterion was
# never about. `is_one_of` / `is_not_one_of` compile to a plain `in` test and are the operators that work
# on a list.
_REGEX_OPERATORS = ("equals", "contains", "does_not_contain", "starts_with", "ends_with")


def _list_valued_constraint_fields():
    """The constraint fields whose entity value is a list, taken from PERMISSION_CONSTRAINT_FIELDS.

    Derived rather than hard-coded so a newly added list field is covered without a second edit here.
    """
    from common.constants import PERMISSION_CONSTRAINT_FIELDS
    return {name for name, sample in PERMISSION_CONSTRAINT_FIELDS.items() if isinstance(sample, list)}


def _reject_regex_operator_on_list_field(field, operator):
    """Reject a regex operator aimed at a list-valued field.

    Without this the constraint stores cleanly and only misbehaves at authorization time, where it looks
    like a permissions problem rather than a malformed rule: the regex raises, the enforcer denies, and
    the role loses access to every entity of that type. Refusing the write is the only point where the
    author can still be told what to use instead.
    """
    if not field or not operator:
        return
    if field in _list_valued_constraint_fields() and operator in _REGEX_OPERATORS:
        # Naming the field is safe under Rule 11 even though `field` arrives from the caller: this line
        # is reached only after confirming it is a member of PERMISSION_CONSTRAINT_FIELDS, so the value
        # echoed back is one of a fixed known set rather than caller text. The operator is not
        # interpolated at all — the message names the two that work instead.
        raise ValueError(
            f"the '{field}' field holds a list of values, which the pattern-matching operators cannot "
            f"compare. Use 'is_one_of' or 'is_not_one_of' instead.")


######################## Constraint API Models ##########################

class ConstraintCriteriaModel(BaseModel, extra='ignore'):
    """Model for constraint criteria (AND/OR)"""
    field: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    operator: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    value: Union[str, List[str]] = Field(max_items=MAX_CRITERIA_VALUES)

    @root_validator
    def validate_criteria_value(cls, values):
        """Bound each criteria value and require it to be a usable regex.

        A criteria value is interpolated into the regexMatch(...) pattern of the
        Casbin obj_rule, so an unbounded or uncompilable value either bloats the
        stored rule or makes every enforce() call on the object type raise and
        deny. Validating here covers every request that carries criteria --
        constraint create/update and template import alike.
        """
        _reject_regex_operator_on_list_field(values.get('field'), values.get('operator'))

        value = values.get('value')
        if value is None:
            return values

        entries = value if isinstance(value, list) else [value]
        for entry in entries:
            (valid, message) = validate({
                'criteriaValue': {
                    'value': entry,
                    'validator': 'STRING_256',
                    'allowGlobalKeyword': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
            (valid, message) = validate({
                'criteriaValue': {
                    'value': entry,
                    'validator': 'REGEX',
                    'allowGlobalKeyword': True
                }
            })
            if not valid:
                logger.error(message)
                raise ValueError(message)
        return values


class GroupPermissionModel(BaseModel, extra='ignore'):
    """Model for group permissions in constraints"""
    groupId: str = Field(min_length=1, max_length=256, strip_whitespace=True, regex=object_name_pattern)
    permission: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    permissionType: str = Field(min_length=1, max_length=256, strip_whitespace=True)


class UserPermissionModel(BaseModel, extra='ignore'):
    """Model for user permissions in constraints"""
    userId: str = Field(min_length=3, max_length=256, strip_whitespace=True)
    permission: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    permissionType: str = Field(min_length=1, max_length=256, strip_whitespace=True)


class GetConstraintsRequestModel(BaseModel, extra='ignore'):
    """Request model for listing constraints"""
    maxItems: Optional[int] = Field(default=30000, ge=1, le=MAX_LIST_MAX_ITEMS)
    pageSize: Optional[int] = Field(default=10000, ge=1, le=MAX_LIST_PAGE_SIZE)
    startingToken: Optional[str] = Field(None, max_length=MAX_LIST_TOKEN_LENGTH)


class CreateConstraintRequestModel(BaseModel, extra='ignore'):
    """Request model for creating/updating a constraint"""
    identifier: str = Field(min_length=1, max_length=256, strip_whitespace=True, regex=object_name_pattern)
    name: str = Field(min_length=1, max_length=256, strip_whitespace=True, regex=object_name_pattern)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    objectType: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    criteriaAnd: Optional[List[ConstraintCriteriaModel]] = Field(default=[], max_items=MAX_CRITERIA_PER_CONSTRAINT)
    criteriaOr: Optional[List[ConstraintCriteriaModel]] = Field(default=[], max_items=MAX_CRITERIA_PER_CONSTRAINT)
    groupPermissions: Optional[List[GroupPermissionModel]] = Field(default=[], max_items=MAX_PERMISSIONS_PER_CONSTRAINT)
    userPermissions: Optional[List[UserPermissionModel]] = Field(default=[], max_items=MAX_PERMISSIONS_PER_CONSTRAINT)

    @root_validator
    def validate_fields(cls, values):
        """Validate constraint fields"""
        # Import here to avoid circular dependency
        from common.constants import (
            ALLOWED_CONSTRAINT_PERMISSIONS,
            ALLOWED_CONSTRAINT_PERMISSION_TYPES,
            ALLOWED_CONSTRAINT_OBJECT_TYPES,
            ALLOWED_CONSTRAINT_OPERATORS,
            get_constraint_fields_for_object_type
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

        # Validate each criterion's field is valid for the chosen objectType
        valid_fields = get_constraint_fields_for_object_type(object_type)
        for criteria in (values.get('criteriaAnd') or []) + (values.get('criteriaOr') or []):
            if criteria.field not in valid_fields:
                message = (f"Invalid field '{criteria.field}' for objectType '{object_type}'. "
                           f"Allowed fields: {', '.join(valid_fields)}")
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


class ConstraintFieldModel(BaseModel, extra='ignore'):
    """A constraint field option (display label + stored value)"""
    label: str
    value: str


class ConstraintObjectTypeModel(BaseModel, extra='ignore'):
    """A constraint object type with its valid fields"""
    label: str
    value: str
    fields: List[ConstraintFieldModel]


class ConstraintOperatorModel(BaseModel, extra='ignore'):
    """A constraint criteria operator option"""
    label: str
    value: str


class ConstraintPermissionModel(BaseModel, extra='ignore'):
    """A constraint permission (HTTP action) option"""
    label: str
    value: str


class ConstraintPermissionTypeModel(BaseModel, extra='ignore'):
    """A constraint permission type (allow/deny) option"""
    label: str
    value: str


class GetConstraintPermissionObjectsResponseModel(BaseModel, extra='ignore'):
    """Response model for GET /auth/constraints/permissionObjects"""
    objectTypes: List[ConstraintObjectTypeModel]
    operators: List[ConstraintOperatorModel]
    permissions: List[ConstraintPermissionModel]
    permissionTypes: List[ConstraintPermissionTypeModel]


######################## Constraint Template Import Models ##########################

class TemplateVariableDefinition(BaseModel, extra='ignore'):
    """Variable definition within a permission template"""
    name: str = Field(min_length=1, max_length=MAX_TEMPLATE_VARIABLE_NAME_LENGTH, strip_whitespace=True)
    required: Optional[bool] = True
    description: Optional[str] = Field(None, max_length=1024)
    default: Optional[str] = Field(None, max_length=MAX_TEMPLATE_VARIABLE_VALUE_LENGTH)


class TemplateConstraintPermission(BaseModel, extra='ignore'):
    """Permission entry within a template constraint (template format)"""
    action: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    type: str = Field(default="allow", min_length=1, max_length=256, strip_whitespace=True)


class TemplateConstraintDefinition(BaseModel, extra='ignore'):
    """A single constraint definition within a permission template"""
    name: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    description: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    objectType: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    criteriaAnd: Optional[List[ConstraintCriteriaModel]] = Field(default=[], max_items=MAX_CRITERIA_PER_CONSTRAINT)
    criteriaOr: Optional[List[ConstraintCriteriaModel]] = Field(default=[], max_items=MAX_CRITERIA_PER_CONSTRAINT)
    groupPermissions: List[TemplateConstraintPermission] = Field(max_items=MAX_PERMISSIONS_PER_CONSTRAINT)


class TemplateMetadata(BaseModel, extra='ignore'):
    """Metadata about a permission template"""
    name: str = Field(min_length=1, max_length=256, strip_whitespace=True)
    description: Optional[str] = Field(None, max_length=1024)
    version: Optional[str] = Field(default="1.0", max_length=50)


class ImportConstraintsTemplateRequestModel(BaseModel, extra='ignore'):
    """Request model for importing constraints from a permission template"""
    template: Optional[TemplateMetadata] = None
    variables: Optional[List[TemplateVariableDefinition]] = Field(default=[], max_items=MAX_TEMPLATE_VARIABLES)
    # {"DATABASE_ID": "my-db", "ROLE_NAME": "my-admin"} -- keys and values are
    # bounded and type-checked in validate_template_import.
    variableValues: dict
    constraints: List[TemplateConstraintDefinition] = Field(max_items=MAX_CONSTRAINTS_PER_TEMPLATE)

    @root_validator
    def validate_template_import(cls, values):
        """Validate template import request"""
        from common.constants import (
            ALLOWED_CONSTRAINT_PERMISSIONS,
            ALLOWED_CONSTRAINT_PERMISSION_TYPES,
            ALLOWED_CONSTRAINT_OBJECT_TYPES,
            ALLOWED_CONSTRAINT_OPERATORS,
            get_constraint_fields_for_object_type
        )

        variable_values = values.get('variableValues', {})

        # Bound the substitution map. Every value is spliced into a constraint name,
        # description, or criteria value -- all of which are themselves capped at 256
        # characters -- so an oversized or non-scalar value would either overflow the
        # stored constraint or stringify a container into a Casbin rule.
        if len(variable_values) > MAX_TEMPLATE_VARIABLES:
            message = f"A maximum of {MAX_TEMPLATE_VARIABLES} template variables can be provided"
            logger.error(message)
            raise ValueError(message)

        for var_name, var_value in variable_values.items():
            if not isinstance(var_name, str) or len(var_name) > MAX_TEMPLATE_VARIABLE_NAME_LENGTH:
                message = (f"Template variable names must be strings of at most "
                           f"{MAX_TEMPLATE_VARIABLE_NAME_LENGTH} characters")
                logger.error(message)
                raise ValueError(message)
            if not isinstance(var_value, (str, int, float, bool)):
                message = "Template variable values must be strings, numbers, or booleans"
                logger.error(message)
                raise ValueError(message)
            if isinstance(var_value, str) and len(var_value) > MAX_TEMPLATE_VARIABLE_VALUE_LENGTH:
                message = (f"Template variable values must be at most "
                           f"{MAX_TEMPLATE_VARIABLE_VALUE_LENGTH} characters")
                logger.error(message)
                raise ValueError(message)

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
                        "A required template variable was not provided in variableValues. "
                        "Check the template's variable definitions for the required names."
                    )

        # Validate constraints
        constraints = values.get('constraints', [])
        if not constraints:
            raise ValueError("At least one constraint is required")

        for constraint in constraints:
            # Validate objectType
            if constraint.objectType not in ALLOWED_CONSTRAINT_OBJECT_TYPES:
                raise ValueError(
                    f"Invalid objectType. Allowed values: {', '.join(ALLOWED_CONSTRAINT_OBJECT_TYPES)}"
                )

            # Validate each criterion's field is valid for the constraint's objectType
            valid_fields = get_constraint_fields_for_object_type(constraint.objectType)
            for criteria in (constraint.criteriaAnd or []) + (constraint.criteriaOr or []):
                if criteria.field not in valid_fields:
                    raise ValueError(
                        f"Invalid criteria field for this objectType. "
                        f"Allowed values: {', '.join(valid_fields)}"
                    )

            # Validate criteria exist
            if not constraint.criteriaAnd and not constraint.criteriaOr:
                raise ValueError(
                    "Each constraint must have at least one criteriaAnd or criteriaOr"
                )

            # Validate operators in criteria
            for criteria in (constraint.criteriaAnd or []) + (constraint.criteriaOr or []):
                if criteria.operator not in ALLOWED_CONSTRAINT_OPERATORS:
                    raise ValueError(
                        f"Invalid criteria operator. Allowed values: {', '.join(ALLOWED_CONSTRAINT_OPERATORS)}"
                    )

            # Validate permissions
            for perm in constraint.groupPermissions:
                if perm.action not in ALLOWED_CONSTRAINT_PERMISSIONS:
                    raise ValueError(
                        f"Invalid permission action. Allowed values: {', '.join(ALLOWED_CONSTRAINT_PERMISSIONS)}"
                    )
                if perm.type not in ALLOWED_CONSTRAINT_PERMISSION_TYPES:
                    raise ValueError(
                        f"Invalid permission type. Allowed values: {', '.join(ALLOWED_CONSTRAINT_PERMISSION_TYPES)}"
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
    maxItems: Optional[int] = Field(default=30000, ge=1, le=MAX_LIST_MAX_ITEMS)
    pageSize: Optional[int] = Field(default=3000, ge=1, le=MAX_LIST_PAGE_SIZE)
    startingToken: Optional[str] = Field(None, max_length=MAX_LIST_TOKEN_LENGTH)


class CreateRoleRequestModel(BaseModel, extra='ignore'):
    """Request model for creating a role"""
    roleName: str = Field(min_length=1, max_length=256, strip_whitespace=True, regex=object_name_pattern)
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
    roleName: str = Field(min_length=1, max_length=256, strip_whitespace=True, regex=object_name_pattern)
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
    maxItems: Optional[int] = Field(default=30000, ge=1, le=MAX_LIST_MAX_ITEMS)
    pageSize: Optional[int] = Field(default=3000, ge=1, le=MAX_LIST_PAGE_SIZE)
    startingToken: Optional[str] = Field(None, max_length=MAX_LIST_TOKEN_LENGTH)


class CreateUserRolesRequestModel(BaseModel, extra='ignore'):
    """Request model for creating user roles"""
    userId: str = Field(min_length=3, max_length=256, strip_whitespace=True)
    roleName: list[str] = Field(min_items=1, max_items=MAX_ROLES_PER_USER_REQUEST)

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
    roleName: list[str] = Field(min_items=1, max_items=MAX_ROLES_PER_USER_REQUEST)

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
