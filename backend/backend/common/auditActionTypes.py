# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The :class:`AuditSpec` type, the audit log groups and the kinds every audit action list uses.

Action lists import these from here rather than from ``common/auditActions.py``, which aggregates the
core, add-on (``common/addonAuditActions.py``) and fork-owned (``custom/customAuditActions.py``) lists
and so cannot be imported by them. ``auditActions.py`` re-exports every name.
"""

import re
from typing import Dict, NamedTuple, Optional, Tuple

from common.apiRouteTypes import ApiRoute

# The audit log groups, by their audit log type (the last segment of the group's resource
# parameter key in common/resourceNames.py, e.g. cloudwatchLogGroups/auditActions).
GROUP_ACTIONS = "auditActions"
GROUP_AUTH_CHANGES = "auditAuthChanges"
GROUP_AUTH_OTHER = "auditAuthOther"
GROUP_AUTHORIZATION = "auditAuthorization"
GROUP_ERRORS = "auditErrors"
GROUP_FILE_UPLOAD = "auditFileUpload"
GROUP_FILE_DOWNLOAD = "auditFileDownload"
GROUP_FILE_DOWNLOAD_STREAMED = "auditFileDownloadStreamed"

AUDIT_GROUPS = frozenset({
    GROUP_ACTIONS, GROUP_AUTH_CHANGES, GROUP_AUTH_OTHER, GROUP_AUTHORIZATION, GROUP_ERRORS,
    GROUP_FILE_UPLOAD, GROUP_FILE_DOWNLOAD, GROUP_FILE_DOWNLOAD_STREAMED,
})

# What an action does with data. A write records what it wrote or changed; a read, a search and a
# download record the request only, never what was returned.
KIND_WRITE = "write"
KIND_READ = "read"
KIND_SEARCH = "search"
KIND_DOWNLOAD = "download"

AUDIT_KINDS = frozenset({KIND_WRITE, KIND_READ, KIND_SEARCH, KIND_DOWNLOAD})

# Entity types a record's entity.type may name.
AUDIT_ENTITY_TYPES = frozenset({
    "database", "asset", "file", "folder", "assetVersion", "fileVersion", "comment", "metadata",
    "metadataSchema", "assetLink", "tag", "tagType", "subscription", "pipeline", "pipelineTemplate",
    "workflow", "workflowTrigger", "execution", "executionGroup", "role", "userRole", "constraint",
    "apiKey", "user", "search", "export", "workstation", "image", "instance", "capacityReservation",
    "amiRecipe", "amiBuild", "containerJob", "settings", "catalog", "viewer", "system",
    # Compliance: a schema, a database or asset binding, an evaluation, an asset's compliance record
    # (its state, quarantine and exception), a cascade, and the compliance audit trail.
    "complianceSchema", "complianceBinding", "complianceEvaluation", "complianceState",
    "complianceCascade", "complianceAudit",
})

# A canonical action name: lower camelCase <entity><Verb>, letters and digits only. It is written
# inside the [type: ...] prefix segment, so it never carries a character that needs escaping.
AUDIT_ACTION_NAME_PATTERN = re.compile(r"^[a-z][A-Za-z0-9]+$")


class AuditSpec(NamedTuple):
    """How one action is recorded.

    ``action`` is the canonical name (``assetCreate``); ``group`` the audit log group it is written
    to on success or failure (a denial always goes to the authorization group); ``entity_type`` the
    record's ``entity.type``; ``kind`` one of ``AUDIT_KINDS``. ``legacy_type`` is the ``[type: ...]``
    value a record of this action carried before the canonical name existed, kept in the prefix so
    existing queries still match. ``legacy_function`` names the audit function a handler already
    calls for this action when that function writes no ``[type: ...]`` segment (the file upload and
    download functions).
    """

    action: str
    group: str
    entity_type: str
    kind: str
    legacy_type: Optional[str] = None
    legacy_function: Optional[str] = None


# A route-method: the route template and the HTTP method.
RouteMethod = Tuple[str, str]


def spec(action: str, entity_type: str, kind: str = KIND_WRITE, group: str = GROUP_ACTIONS,
         legacy_type: Optional[str] = None, legacy_function: Optional[str] = None) -> AuditSpec:
    """Build an :class:`AuditSpec` with the actions group and the write kind as defaults."""
    return AuditSpec(action, group, entity_type, kind, legacy_type, legacy_function)


def read(action: str, entity_type: str, group: str = GROUP_ACTIONS,
         legacy_type: Optional[str] = None) -> AuditSpec:
    """Build the :class:`AuditSpec` of a read: the request is recorded, never what was returned."""
    return AuditSpec(action, group, entity_type, KIND_READ, legacy_type, None)


def route_entries(route: ApiRoute, specs_by_method: Dict[str, AuditSpec]) -> Dict[RouteMethod, AuditSpec]:
    """The registry entries of one route: ``{(route.path, method): spec}`` for each method given."""
    return {(route.path, method): audit_spec for method, audit_spec in specs_by_method.items()}


class SystemAction(NamedTuple):
    """An action recorded by a handler that serves no API route (a queue, a state machine step,
    a custom resource). ``handler`` is the module path under ``backend/backend``, e.g.
    ``handlers.indexing.sqsBucketSync``."""

    handler: str
    spec: AuditSpec
