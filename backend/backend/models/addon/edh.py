# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pydantic v1 request and response models for the EDH Workstations add-on API.

Request bodies are ``application/json`` only. Every string a caller supplies is bounded, and the
fields that reach EDH or the session table are checked through the shared ``validate()``
dispatcher; the three EDH-specific shapes (an EC2 instance type, an AMI id, an EDH base-OS key) carry
their pattern as a module constant so the CLI and the web share one spelling.
"""

import re
from typing import Dict, List, Optional, Union

from pydantic import Field
from aws_lambda_powertools.utilities.parser import BaseModel, root_validator, validator
from customLogging.logger import safeLogger
from common.validators import validate, aws_partition_group, object_name_pattern, trim_name

logger = safeLogger(service_name="EdhModels")

# An EC2 instance type: family, dot, size (``g5.xlarge``, ``c7i.metal-24xl``).
edh_instance_type_pattern = r'^[a-z0-9-]+\.[a-z0-9-]+\Z'
edh_ami_id_pattern = r'^ami-[0-9a-f]{8,17}\Z'
# A key of EDH's DCV_BASE_OS table (``amazonlinux2023``, ``windows2022``): lowercase alphanumerics, so a
# spelling EDH would refuse is a 400 that names the field, while any key an operator configures passes.
edh_base_os_pattern = r'^[a-z][a-z0-9]{1,63}\Z'

EDH_OS_FAMILIES = ('linux', 'windows')
WORKSTATION_STATES = ('CREATING', 'ACTIVE', 'STOPPED', 'DELETED', 'ORPHANED')
CONTAINER_BACKENDS = ('batch', 'eks')

# Root volume bounds for a desktop, in GiB.
ROOT_SIZE_GB_MIN = 10
ROOT_SIZE_GB_MAX = 2048

# Container job override bounds.
MAX_JOB_COMMAND_ENTRIES = 64
MAX_JOB_COMMAND_ENTRY_LENGTH = 1024
MAX_JOB_ENVIRONMENT_ENTRIES = 64
MAX_JOB_ENVIRONMENT_VALUE_LENGTH = 1024
# The environment-name prefix AWS Batch reserves for the variables it sets itself.
BATCH_RESERVED_ENVIRONMENT_PREFIX = 'AWS_BATCH'

# Session-row attributes the record projection never returns: the session credential's bookkeeping,
# the EC2 placement its secret is bound to, and the TTL expiry a DELETED row leaves the table by. None
# of them is declared on the record model.
WORKSTATION_ROW_INTERNAL_FIELDS = (
    'apiKeyId', 'secretArn', 'secretName', 'keyExpiresAt', 'instanceId', 'secretBoundInstanceId', 'expiresAt',
)
# The keys of the launch-failure reason a row carries (``edhFailure``) and returns, and the EDH events it
# names; a stored value that is not such a map projects as None, and any other key is dropped.
EDH_FAILURE_FIELDS = ('event', 'errorCode', 'operation', 'checkpoint', 'observedAt')
EDH_FAILURE_EVENTS = ('placement_failed', 'session-failed')

AMI_RECIPE_KINDS = ('vdi', 'simulation')
EDH_ARCHITECTURES = ('x86_64', 'arm64')
# The CPU architecture families: an instance type runs an image of its own family. EDH and EC2 list
# some x86 types under i386.
ARCHITECTURE_FAMILIES = {'x86_64': 'x86', 'i386': 'x86', 'arm64': 'arm'}
# The DCV_BASE_OS keys EDH 26.8.0 offers in its own catalog form; centos7 is hidden there and refused here.
EDH_DCV_BASE_OS_KEYS = (
    'amazonlinux2', 'amazonlinux2023', 'rhel7', 'rhel8', 'rhel9', 'rocky8', 'rocky9',
    'ubuntu2204', 'ubuntu2404', 'windows2019', 'windows2022', 'windows2025',
)
AMI_RECIPE_MAX_COMPONENTS = 20
# A recipe names one build instance type, or a list Image Builder chooses among by availability.
AMI_RECIPE_MAX_BUILD_INSTANCE_TYPES = 8
AMI_RECIPE_BUILD_INSTANCE_TYPE_MAX_LENGTH = 64
# The instance types a desktop of a recipe's image may launch on; each is spelled as a build type is.
AMI_RECIPE_MAX_LAUNCH_INSTANCE_TYPES = 64
# Image Builder's CreateComponent carries the document inline and takes at most this many characters.
AMI_RECIPE_COMPONENT_DATA_MAX_BYTES = 16000
KEEP_VERSIONS_MIN = 1
KEEP_VERSIONS_MAX = 20

# The parameter path an SSM parent image is read from: the public AMI parameters AWS publishes.
EDH_PARENT_SSM_PREFIX = 'ssm:/aws/service/'

AMI_RECIPE_MAX_ASSETS = 8
AMI_RECIPE_MAX_PARAMETERS = 16
AMI_RECIPE_PARAMETER_VALUE_MAX = 1024


def _require_one_of(values, field, allowed):
    """Reject a present value outside ``allowed``; an absent value is the field's own concern."""
    value = values.get(field)
    if value is not None and value not in allowed:
        logger.error(f"Rejected {field} value: {value}")
        raise ValueError(f"{field} must be one of: {', '.join(allowed)}")


def _run_validate(validation_map):
    (valid, message) = validate(validation_map)
    if not valid:
        logger.error(message)
        raise ValueError(message)


def architecture_family(value) -> Optional[str]:
    """The family of an architecture name as AWS spells it, or None for one of no known family (the mac
    architectures, an empty or differently cased value, anything not a string)."""
    return ARCHITECTURE_FAMILIES.get(value) if isinstance(value, str) else None


def edh_failure_projection(value) -> Optional[Dict[str, str]]:
    """The launch-failure reason as the API returns it: the string values of ``EDH_FAILURE_FIELDS`` from a
    stored map whose ``event`` is one of ``EDH_FAILURE_EVENTS``; None for anything else."""
    if not isinstance(value, dict) or value.get('event') not in EDH_FAILURE_EVENTS:
        return None
    return {key: value[key] for key in EDH_FAILURE_FIELDS if isinstance(value.get(key), str)}


class CreateWorkstationRequestModel(BaseModel, extra='ignore'):
    """Request body for ``POST /addon/edh/workstations``.

    ``bootstrapId`` is the client-generated idempotency key; ``osFamily`` is the family of the
    catalog image the caller chose.
    """
    bootstrapId: str = Field(min_length=36, max_length=36)
    imageId: str = Field(min_length=1, max_length=256)
    instanceType: str = Field(min_length=3, max_length=64, regex=edh_instance_type_pattern)
    name: str = Field(min_length=1, max_length=256, regex=object_name_pattern)
    rootSizeGb: int = Field(ge=ROOT_SIZE_GB_MIN, le=ROOT_SIZE_GB_MAX)
    osFamily: str = Field(min_length=1, max_length=16)
    leaseHours: Optional[int] = Field(None, ge=1, le=720)

    _trim_names = validator('name', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        _run_validate({
            'bootstrapId': {'value': values.get('bootstrapId'), 'validator': 'UUID'},
            'imageId': {'value': values.get('imageId'), 'validator': 'STRING_256'},
            'name': {'value': values.get('name'), 'validator': 'OBJECT_NAME'},
        })
        _require_one_of(values, 'osFamily', EDH_OS_FAMILIES)
        return values


class WorkstationRecordModel(BaseModel, extra='ignore'):
    """One workstation session, as stored in the session table and returned by the API. The row
    attributes in ``WORKSTATION_ROW_INTERNAL_FIELDS`` are dropped on the way out, as is anything else
    the row carries that is not declared here."""
    bootstrapId: str
    sessionId: Optional[str] = None
    userId: str
    edhUid: str
    state: str
    edhState: Optional[str] = None
    name: str
    imageId: str
    osFamily: str
    instanceType: str
    # The root volume size requested at launch; a row adopted without it still projects.
    rootSizeGb: Optional[int] = None
    createdAt: str
    updatedAt: str
    connectionAvailable: bool = False
    # Launch bookkeeping kept by the launch worker: the generic error code of a definite refusal
    # and the number of launch starts made for the row.
    lastError: Optional[str] = None
    launchAttempts: int = 0
    leaseExpiresAt: Optional[str] = None
    leaseStoppedAt: Optional[str] = None
    leaseHoursAtLaunch: Optional[int] = None
    # The reason EDH recorded for a failed launch, once VAMS captured it; its read stamp is not declared.
    edhFailure: Optional[Dict[str, str]] = None

    _project_edh_failure = validator('edhFailure', pre=True, allow_reuse=True)(edh_failure_projection)

    @root_validator
    def validate_fields(cls, values):
        _require_one_of(values, 'state', WORKSTATION_STATES)
        _require_one_of(values, 'osFamily', EDH_OS_FAMILIES)
        return values


class ConnectResponseModel(BaseModel, extra='ignore'):
    """Response body for ``GET /addon/edh/workstations/{sessionId}/connect``."""
    sessionId: str
    connectionUrl: str
    osFamily: str


class WindowsSignInResponseModel(BaseModel, extra='ignore'):
    """Response body for ``GET /addon/edh/workstations/{sessionId}/windows-sign-in``. ``password`` is None for
    a directory account, which signs in with the user's own directory password."""
    sessionId: str
    username: str
    password: Optional[str] = None
    directoryAccount: bool


class RegisterVdiImageRequestModel(BaseModel, extra='ignore'):
    """Request body for ``POST /addon/edh/admin/images/vdi``."""
    amiId: str = Field(min_length=12, max_length=21, regex=edh_ami_id_pattern)
    stackName: str = Field(min_length=1, max_length=256, regex=object_name_pattern)
    baseOs: str = Field(min_length=1, max_length=64, regex=edh_base_os_pattern)
    rootSize: int = Field(ge=1, le=ROOT_SIZE_GB_MAX)
    virtualDesktopProfileId: int = Field(ge=0)
    launchTenancy: Optional[str] = Field(None, max_length=30)
    description: Optional[str] = Field(None, max_length=256)

    _trim_names = validator('stackName', pre=True, allow_reuse=True)(trim_name)

    # Free-form caller text trims its surrounding whitespace before the length check.
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        _run_validate({
            'stackName': {'value': values.get('stackName'), 'validator': 'OBJECT_NAME'},
            'baseOs': {'value': values.get('baseOs'), 'validator': 'STRING_256'},
            'launchTenancy': {'value': values.get('launchTenancy'), 'validator': 'STRING_30', 'optional': True},
            'description': {'value': values.get('description'), 'validator': 'STRING_256', 'optional': True},
        })
        return values


class RegisterSimulationImageRequestModel(BaseModel, extra='ignore'):
    """Request body for ``POST /addon/edh/admin/images/simulation``."""
    amiId: str = Field(min_length=12, max_length=21, regex=edh_ami_id_pattern)
    stackName: str = Field(min_length=1, max_length=256, regex=object_name_pattern)
    rootSize: int = Field(ge=1, le=ROOT_SIZE_GB_MAX)
    targetNodeProfileId: int = Field(ge=0)
    # The EDH user-data template the stack boots with; EDH's protected default template is 1.
    targetNodeUserDataId: int = Field(1, ge=1)
    userDataVariables: Optional[str] = Field(None, max_length=16384)
    description: Optional[str] = Field(None, max_length=256)

    _trim_names = validator('stackName', pre=True, allow_reuse=True)(trim_name)

    # Free-form caller text trims its surrounding whitespace before the length check.
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        _run_validate({
            'stackName': {'value': values.get('stackName'), 'validator': 'OBJECT_NAME'},
            'userDataVariables': {'value': values.get('userDataVariables'), 'validator': 'STRING_16384',
                                  'optional': True},
            'description': {'value': values.get('description'), 'validator': 'STRING_256', 'optional': True},
        })
        return values


class LeaseSettingsRequestModel(BaseModel, extra='ignore'):
    """Request body for ``PUT /addon/edh/admin/settings``. Every field is optional; the policy module
    merges the ones supplied and owns the range rules."""
    enabled: Optional[bool] = None
    leaseHours: Optional[int] = None
    forced: Optional[bool] = None
    graceHours: Optional[int] = None


class SubmitContainerJobRequestModel(BaseModel, extra='ignore'):
    """Request body for ``POST /addon/edh/containers/jobs``."""
    jobDefinition: str = Field(min_length=1, max_length=256)
    jobQueue: Optional[str] = Field(None, max_length=256)
    name: str = Field(min_length=1, max_length=256, regex=object_name_pattern)
    command: Optional[List[str]] = None
    environment: Optional[Dict[str, str]] = None
    vcpus: Optional[int] = Field(None, ge=1)
    memory: Optional[int] = Field(None, ge=1)
    gpu: Optional[int] = Field(None, ge=0)
    backend: str = Field(min_length=1, max_length=16)

    _trim_names = validator('name', pre=True, allow_reuse=True)(trim_name)

    @root_validator
    def validate_fields(cls, values):
        _run_validate({
            'jobDefinition': {'value': values.get('jobDefinition'), 'validator': 'STRING_256'},
            'jobQueue': {'value': values.get('jobQueue'), 'validator': 'STRING_256', 'optional': True},
            'name': {'value': values.get('name'), 'validator': 'OBJECT_NAME'},
        })
        _require_one_of(values, 'backend', CONTAINER_BACKENDS)

        command = values.get('command')
        if command is not None:
            if len(command) > MAX_JOB_COMMAND_ENTRIES:
                raise ValueError(f"command accepts at most {MAX_JOB_COMMAND_ENTRIES} entries")
            if any(len(entry) > MAX_JOB_COMMAND_ENTRY_LENGTH for entry in command):
                raise ValueError(f"command entries are limited to {MAX_JOB_COMMAND_ENTRY_LENGTH} characters")

        environment = values.get('environment')
        if environment:
            if len(environment) > MAX_JOB_ENVIRONMENT_ENTRIES:
                raise ValueError(f"environment accepts at most {MAX_JOB_ENVIRONMENT_ENTRIES} entries")
            _run_validate({
                'environment': {'value': list(environment.keys()), 'validator': 'STRING_256_ARRAY'},
            })
            if any(len(value) > MAX_JOB_ENVIRONMENT_VALUE_LENGTH for value in environment.values()):
                raise ValueError(f"environment values are limited to {MAX_JOB_ENVIRONMENT_VALUE_LENGTH} characters")
            if values.get('backend') == 'batch' and any(
                    key.startswith(BATCH_RESERVED_ENVIRONMENT_PREFIX) for key in environment):
                raise ValueError(
                    f"environment names cannot start with {BATCH_RESERVED_ENVIRONMENT_PREFIX} for the batch backend")
        return values


# ---------------------------------------------------------------------------
# AMI recipes
# ---------------------------------------------------------------------------
edh_recipe_id_pattern = r'^[a-z0-9][a-z0-9-]{1,39}\Z'
# A recipe's display name is shown, never used to name a resource, so it also admits the parentheses of
# the "<displayName> (copy)" a duplicate starts from.
edh_recipe_display_name_pattern = r'^[a-zA-Z0-9\-._\s()]{1,256}\Z'
# An AMI id, a public AMI parameter (`ssm:/aws/service/...`), or the AMI EDH lists under a stack name.
edh_parent_image_pattern = (
    r'^(ami-[0-9a-f]{8,17}|ssm:/aws/service/[A-Za-z0-9/_.-]{1,1000}|edhStack:[A-Za-z0-9\-._ ]{1,256})\Z'
)
# An Image Builder component ARN; `${AWS::Partition}` and `${AWS::Region}` are substituted at build time.
edh_component_arn_pattern = (
    r'^arn:(?:' + aws_partition_group + r'|\$\{AWS::Partition\}):imagebuilder:(?:[a-z0-9-]+|\$\{AWS::Region\}):'
    r'(?:aws|[0-9]{12}):component/[a-z0-9-]+/(?:[0-9]+|x)\.(?:[0-9]+|x)\.(?:[0-9]+|x)(?:/[0-9]+)?\Z'
)
edh_component_name_pattern = r'^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z'
edh_asset_id_pattern = r'^[a-z][a-z0-9-]{0,31}\Z'
edh_parameter_key_pattern = r'^[A-Za-z][A-Za-z0-9_]{0,63}\Z'


def os_family_for_base_os(base_os) -> str:
    return 'windows' if str(base_os or '').startswith('windows') else 'linux'


class AmiRecipeComponentModel(BaseModel, extra='ignore'):
    """One recipe step: an Image Builder component ARN, or an inline component document."""
    arn: Optional[str] = Field(None, min_length=1, max_length=512, regex=edh_component_arn_pattern)
    name: Optional[str] = Field(None, min_length=1, max_length=64, regex=edh_component_name_pattern)
    data: Optional[str] = Field(None, min_length=1, max_length=AMI_RECIPE_COMPONENT_DATA_MAX_BYTES)

    @root_validator
    def validate_fields(cls, values):
        has_arn = bool(values.get('arn'))
        has_inline = bool(values.get('name')) or bool(values.get('data'))
        if has_arn == has_inline or (has_inline and not (values.get('name') and values.get('data'))):
            raise ValueError("a component is either {arn} or {name, data}")
        if has_inline and 'schemaVersion' not in values['data']:
            raise ValueError("an inline component document must declare schemaVersion")
        return values


class AmiRecipeAssetModel(BaseModel, extra='ignore'):
    """One repository directory a build fetches; a component reaches it as ``${ASSET_<id>}``."""
    id: str = Field(min_length=1, max_length=32, regex=edh_asset_id_pattern)
    repoPath: str = Field(min_length=1, max_length=256)

    @validator('repoPath')
    def validate_repo_path(cls, value):
        normalized = str(value).replace('\\', '/').strip()
        parts = normalized.split('/')
        if normalized.startswith('/') or ':' in parts[0] or any(p in ('', '.', '..') for p in parts):
            raise ValueError("repoPath must be a relative repository path without empty, '.' or '..' segments")
        if not normalized.startswith('tools/'):
            raise ValueError('repoPath must be under tools/')
        return normalized


class AmiRecipeRequestModel(BaseModel, extra='ignore'):
    """Body of ``POST /addon/edh/admin/ami-recipes`` and ``PUT .../{recipeId}``; also the shape a
    shipped ``recipe.json`` takes once its ``file`` components are inlined."""
    recipeId: str = Field(min_length=2, max_length=40, regex=edh_recipe_id_pattern)
    displayName: str = Field(min_length=1, max_length=256, regex=edh_recipe_display_name_pattern)
    kind: str = Field(min_length=3, max_length=16)
    osFamily: str = Field(min_length=5, max_length=16)
    baseOs: Optional[str] = Field(None, min_length=1, max_length=64, regex=edh_base_os_pattern)
    architecture: str = Field(min_length=5, max_length=8)
    parentImage: str = Field(min_length=12, max_length=1300, regex=edh_parent_image_pattern)
    buildInstanceType: Union[str, List[str]]
    # The instance types a desktop of the recipe's image may launch on; None (absent, null or empty) is no
    # restriction of the recipe's own. Metadata: it is outside the content hash and never forces a build.
    launchInstanceTypes: Optional[List[str]] = None
    rootSizeGb: int = Field(ge=ROOT_SIZE_GB_MIN, le=ROOT_SIZE_GB_MAX)
    stackName: str = Field(min_length=1, max_length=200, regex=object_name_pattern)
    profileId: int = Field(ge=0)
    components: List[AmiRecipeComponentModel] = Field(min_items=1, max_items=AMI_RECIPE_MAX_COMPONENTS)
    assets: Optional[List[AmiRecipeAssetModel]] = Field(None, max_items=AMI_RECIPE_MAX_ASSETS)
    parameters: Optional[Dict[str, str]] = None
    keepVersions: Optional[int] = Field(None, ge=KEEP_VERSIONS_MIN, le=KEEP_VERSIONS_MAX)
    recipeRevision: int = Field(1, ge=1)
    description: Optional[str] = Field(None, max_length=256)
    enabled: bool = True
    # On create, the recipe the new one is duplicated from: its bundled files are copied, not sent.
    duplicateOf: Optional[str] = Field(None, min_length=2, max_length=40, regex=edh_recipe_id_pattern)

    _trim_names = validator('displayName', 'stackName', pre=True, allow_reuse=True)(trim_name)
    _trim_text = validator('description', pre=True, allow_reuse=True)(trim_name)

    @validator('buildInstanceType')
    def validate_build_instance_type(cls, value):
        """One instance type, or 1-8 distinct ones; the value keeps the shape it was given in."""
        instance_types = [value] if isinstance(value, str) else value
        if (not 1 <= len(instance_types) <= AMI_RECIPE_MAX_BUILD_INSTANCE_TYPES
                or len(set(instance_types)) != len(instance_types)
                or any(len(t) > AMI_RECIPE_BUILD_INSTANCE_TYPE_MAX_LENGTH or not re.match(edh_instance_type_pattern, t)
                       for t in instance_types)):
            raise ValueError(
                "buildInstanceType must be an EC2 instance type such as m7i.large, or a list of "
                f"1-{AMI_RECIPE_MAX_BUILD_INSTANCE_TYPES} distinct instance types")
        return value

    @validator('launchInstanceTypes')
    def validate_launch_instance_types(cls, value):
        """1-64 distinct instance types, kept in the order given; an empty list is None."""
        if not value:
            return None
        if (len(value) > AMI_RECIPE_MAX_LAUNCH_INSTANCE_TYPES
                or len(set(value)) != len(value)
                or any(len(t) > AMI_RECIPE_BUILD_INSTANCE_TYPE_MAX_LENGTH or not re.match(edh_instance_type_pattern, t)
                       for t in value)):
            raise ValueError(
                "launchInstanceTypes must be a list of "
                f"1-{AMI_RECIPE_MAX_LAUNCH_INSTANCE_TYPES} distinct EC2 instance types such as g5.2xlarge")
        return value

    @root_validator(pre=True)
    def validate_parent_image_prefix(cls, values):
        """Names the allowed prefix; the field's own pattern reports only that it did not match."""
        parent = values.get('parentImage')
        if isinstance(parent, str) and parent.startswith('ssm:') and not parent.startswith(EDH_PARENT_SSM_PREFIX):
            raise ValueError(f"parentImage as an SSM parameter path must start with {EDH_PARENT_SSM_PREFIX}")
        return values

    @root_validator
    def validate_fields(cls, values):
        _require_one_of(values, 'kind', AMI_RECIPE_KINDS)
        _require_one_of(values, 'osFamily', EDH_OS_FAMILIES)
        _require_one_of(values, 'architecture', EDH_ARCHITECTURES)
        base_os = values.get('baseOs')
        if values.get('kind') == 'vdi':
            if base_os not in EDH_DCV_BASE_OS_KEYS:
                raise ValueError(f"baseOs must be one of: {', '.join(EDH_DCV_BASE_OS_KEYS)}")
            if os_family_for_base_os(base_os) != values.get('osFamily'):
                raise ValueError("osFamily does not match baseOs")
        elif base_os is not None:
            raise ValueError("baseOs applies to vdi recipes only")
        # The Amazon DCV server for Windows is x86_64 only.
        if values.get('osFamily') == 'windows' and values.get('architecture') != 'x86_64':
            raise ValueError("Windows recipes support x86_64 only")
        _run_validate({
            'stackName': {'value': values.get('stackName'), 'validator': 'OBJECT_NAME'},
            'description': {'value': values.get('description'), 'validator': 'STRING_256', 'optional': True},
        })
        assets = values.get('assets') or []
        asset_ids = [asset.id for asset in assets]
        if len(set(asset_ids)) != len(asset_ids):
            raise ValueError('assets carry duplicate ids')
        parameters = values.get('parameters')
        if parameters:
            if len(parameters) > AMI_RECIPE_MAX_PARAMETERS:
                raise ValueError(f"parameters accepts at most {AMI_RECIPE_MAX_PARAMETERS} entries")
            if any(not re.match(edh_parameter_key_pattern, key) for key in parameters):
                raise ValueError(f"parameters keys must match {edh_parameter_key_pattern}")
            if any(len(value) > AMI_RECIPE_PARAMETER_VALUE_MAX for value in parameters.values()):
                raise ValueError(
                    f"parameters values are limited to {AMI_RECIPE_PARAMETER_VALUE_MAX} characters")
        return values
