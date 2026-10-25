# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Field mapping between the VAMS image-registration models and the EDH software-stack routes, the
project assignment that makes a registered stack launchable, and the reader of a listed software-stack
record's CPU architecture.

Shared by the admin register routes, the deploy-time catalog registration and the AMI recipe builds so
all of them send the same form field set for the same VAMS request shape and attach what they register
to the same project, and by the launch catalog and the admin image listing so both name the same
architecture for the same stack.
"""

import os
from typing import Callable, Dict, Optional

from customLogging.logger import safeLogger
from models.addon.edh import RegisterSimulationImageRequestModel, RegisterVdiImageRequestModel

from .edhClient import edh_error_reason

logger = safeLogger(service_name="EdhCatalogFields")

# The project a registered software stack is attached to. EDH resolves a user's launchable stacks
# through the projects a stack is attached to, so a stack no project carries cannot start a desktop.
try:
    EDH_VDI_DEFAULT_PROJECT = os.environ["EDH_VDI_DEFAULT_PROJECT"]
except Exception:
    logger.exception("Failed loading the EDH launch project")
    raise

# Software-stack kinds as the EDH client names them.
KIND_DCV = "dcv"
KIND_TARGET_NODES = "target_nodes"

# Image kind labels carried on merged catalog items and in the admin route paths.
IMAGE_KIND_VDI = "vdi"
IMAGE_KIND_SIMULATION = "simulation"

KIND_BY_IMAGE_KIND = {IMAGE_KIND_VDI: KIND_DCV, IMAGE_KIND_SIMULATION: KIND_TARGET_NODES}

# Form field names of the EDH software-stack create routes.
EDH_STACK_FIELD_AMI_ID = "ami_id"
EDH_STACK_FIELD_STACK_NAME = "stack_name"
EDH_STACK_FIELD_BASE_OS = "base_os"
EDH_STACK_FIELD_ROOT_SIZE = "root_size"
EDH_STACK_FIELD_VDI_PROFILE_ID = "virtual_desktop_profile_id"
EDH_STACK_FIELD_LAUNCH_TENANCY = "launch_tenancy"
EDH_STACK_FIELD_DESCRIPTION = "description"
EDH_STACK_FIELD_TARGET_NODE_PROFILE_ID = "target_node_profile_id"
EDH_STACK_FIELD_USER_DATA_VARIABLES = "user_data_variables"
EDH_STACK_FIELD_TARGET_NODE_USER_DATA_ID = "target_node_user_data_id"

# The complete field set of each software-stack create route, required fields first.
VDI_STACK_FIELDS = (
    EDH_STACK_FIELD_AMI_ID,
    EDH_STACK_FIELD_STACK_NAME,
    EDH_STACK_FIELD_BASE_OS,
    EDH_STACK_FIELD_ROOT_SIZE,
    EDH_STACK_FIELD_VDI_PROFILE_ID,
    EDH_STACK_FIELD_LAUNCH_TENANCY,
    EDH_STACK_FIELD_DESCRIPTION,
)
SIMULATION_STACK_FIELDS = (
    EDH_STACK_FIELD_AMI_ID,
    EDH_STACK_FIELD_STACK_NAME,
    EDH_STACK_FIELD_ROOT_SIZE,
    EDH_STACK_FIELD_TARGET_NODE_PROFILE_ID,
    EDH_STACK_FIELD_TARGET_NODE_USER_DATA_ID,
    EDH_STACK_FIELD_USER_DATA_VARIABLES,
    EDH_STACK_FIELD_DESCRIPTION,
)

# The CPU architecture column of a software-stack record as the list routes return it.
EDH_STACK_RECORD_FIELD_ARCHITECTURE = "ami_arch"

# A software stack whose base OS starts with this prefix is a Windows image; every other is Linux.
WINDOWS_BASE_OS_PREFIX = "windows"
OS_FAMILY_WINDOWS = "windows"
OS_FAMILY_LINUX = "linux"

PROJECT_ASSIGN_FAILED = "registered in EDH but not assigned to project"


def os_family_from_base_os(base_os) -> str:
    if str(base_os or "").strip().lower().startswith(WINDOWS_BASE_OS_PREFIX):
        return OS_FAMILY_WINDOWS
    return OS_FAMILY_LINUX


def stack_architecture(record) -> Optional[str]:
    """The image's CPU architecture as EDH registered it (``x86_64``, ``arm64``), or None."""
    value = record.get(EDH_STACK_RECORD_FIELD_ARCHITECTURE)
    return value.strip() if isinstance(value, str) and value.strip() else None


def vdi_stack_fields(model: RegisterVdiImageRequestModel) -> Dict[str, str]:
    """Form fields for a dcv software-stack create; every value is a string."""
    fields = {
        EDH_STACK_FIELD_AMI_ID: model.amiId,
        EDH_STACK_FIELD_STACK_NAME: model.stackName,
        EDH_STACK_FIELD_BASE_OS: model.baseOs,
        EDH_STACK_FIELD_ROOT_SIZE: str(model.rootSize),
        EDH_STACK_FIELD_VDI_PROFILE_ID: str(model.virtualDesktopProfileId),
    }
    if model.launchTenancy:
        fields[EDH_STACK_FIELD_LAUNCH_TENANCY] = model.launchTenancy
    if model.description:
        fields[EDH_STACK_FIELD_DESCRIPTION] = model.description
    return fields


def simulation_stack_fields(model: RegisterSimulationImageRequestModel) -> Dict[str, str]:
    """Form fields for a target_nodes software-stack create; every value is a string."""
    fields = {
        EDH_STACK_FIELD_AMI_ID: model.amiId,
        EDH_STACK_FIELD_STACK_NAME: model.stackName,
        EDH_STACK_FIELD_ROOT_SIZE: str(model.rootSize),
        EDH_STACK_FIELD_TARGET_NODE_PROFILE_ID: str(model.targetNodeProfileId),
        EDH_STACK_FIELD_TARGET_NODE_USER_DATA_ID: str(model.targetNodeUserDataId),
    }
    if model.userDataVariables:
        fields[EDH_STACK_FIELD_USER_DATA_VARIABLES] = model.userDataVariables
    if model.description:
        fields[EDH_STACK_FIELD_DESCRIPTION] = model.description
    return fields


def project_assign_error(err) -> str:
    """The message of a stack EDH holds that the launch project does not carry.

    ``err`` is the typed EDH error the assignment raised, or a plain reason string.
    """
    detail = err if isinstance(err, str) else edh_error_reason(err)
    return f"{PROJECT_ASSIGN_FAILED} '{EDH_VDI_DEFAULT_PROJECT}' ({detail})"


def ensure_stack_in_project(client, kind: str, stack_id, *,
                            invoke: Optional[Callable[..., bool]] = None) -> bool:
    """Attach a registered software stack to the launch project; returns whether EDH was updated.

    ``invoke`` replaces the client call with the caller's own -- the admin routes send it through their
    audit wrapper -- and takes the wrapper's arguments. Raises the client's typed errors.
    """
    call = invoke or client.assign_software_stack_to_project
    return bool(call(kind, stack_id, EDH_VDI_DEFAULT_PROJECT))
