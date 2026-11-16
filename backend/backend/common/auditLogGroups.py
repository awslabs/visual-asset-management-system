# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The audit log group registry: which audit log groups exist and how each one is read.

One :class:`~common.auditActionTypes.AuditLogGroup` descriptor per group. The core groups are below;
the add-on groups are in ``common/addonAuditActions.py`` (``ADDON_AUDIT_LOG_GROUPS``) and a fork's in
``custom/customAuditActions.py`` (``CUSTOM_AUDIT_LOG_GROUPS``, optional). The lanes merge in the order
core, add-on, custom; a later lane never replaces an earlier key.

Two consumers read the merged registry:

* the writer (``customLogging/auditLogging.py``) derives the group resource keys, the prefix labels and
  the groups without a ``[type: ...]`` segment from it;
* the audit search (``common/auditSearch``) derives the groups a caller may search, their record
  prefix, the parser that reads their records and the ``auditLog`` constraint value that authorizes
  them.

Adding an audit group is one descriptor here (or in a lane), its ``ResourceKeys`` entry and the CDK log
group; see "Add an audit group" in ``docs/developer/audit-logging.md``. The module is import-light: it
imports the action lists and the descriptor type only, never ``customLogging.auditLogging``.
"""

import re
from typing import Any, Dict, FrozenSet, Iterable, Optional, Tuple

from common.addonAuditActions import ADDON_AUDIT_LOG_GROUPS
from common.auditActionTypes import (
    GROUP_ACTIONS,
    GROUP_AUTH_CHANGES,
    GROUP_AUTH_OTHER,
    GROUP_AUTHENTICATION,
    GROUP_AUTHORIZATION,
    GROUP_ERRORS,
    GROUP_FILE_DOWNLOAD,
    GROUP_FILE_DOWNLOAD_STREAMED,
    GROUP_FILE_UPLOAD,
    AuditLogGroup,
)
from custom import customAuditActions as _custom_audit_actions

# Sensitivity tags a descriptor may carry.
SENSITIVITY_CONTAINS_PII = "containsPii"

# Legacy parser adapter names (see common/auditSearch/parsers/__init__.py for the adapters).
PARSER_AUTHENTICATION = "authentication"
PARSER_AUTHORIZATION = "authorization"
PARSER_FILE_TRANSFER = "fileTransfer"
PARSER_AUTH_EVENTS = "authEvents"
PARSER_ERRORS = "errors"
PARSER_ACTIONS = "actions"

# A group key is also an ``auditLog`` constraint value, validated as a token.
AUDIT_LOG_GROUP_KEY_PATTERN = re.compile(r"^[A-Za-z][A-Za-z0-9]{0,63}\Z")
# A record prefix label: upper case letters, digits and hyphens, written in brackets.
AUDIT_LOG_GROUP_PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9-]{0,31}\Z")

CORE_AUDIT_LOG_GROUPS: Tuple[AuditLogGroup, ...] = (
    # The order is the order the writer resolves the group names in.
    AuditLogGroup(
        GROUP_AUTHENTICATION, "AUDIT_LOG_AUTHENTICATION", "Authentication",
        "Sign-in events of the web application and the API authorizer.", "AUTHENTICATION", False,
        PARSER_AUTHENTICATION, action_target=False, sensitivity=(SENSITIVITY_CONTAINS_PII,)),
    AuditLogGroup(
        GROUP_AUTHORIZATION, "AUDIT_LOG_AUTHORIZATION", "Authorization",
        "Allowed and denied route and data checks, including the API authorizer's own record.",
        "AUTHORIZATION", True, PARSER_AUTHORIZATION),
    AuditLogGroup(
        GROUP_FILE_UPLOAD, "AUDIT_LOG_FILEUPLOAD", "File uploads",
        "File uploads and denied uploads.", "FILEUPLOAD", False, PARSER_FILE_TRANSFER),
    AuditLogGroup(
        GROUP_FILE_DOWNLOAD, "AUDIT_LOG_FILEDOWNLOAD", "File downloads",
        "File downloads, single and bulk.", "FILEDOWNLOAD", False, PARSER_FILE_TRANSFER),
    AuditLogGroup(
        GROUP_FILE_DOWNLOAD_STREAMED, "AUDIT_LOG_FILEDOWNLOAD_STREAMED", "Streamed file downloads",
        "File downloads served through the stream endpoint.", "FILEDOWNLOAD-STREAMED", False,
        PARSER_FILE_TRANSFER),
    AuditLogGroup(
        GROUP_AUTH_OTHER, "AUDIT_LOG_AUTHOTHER", "Auth, other",
        "Login profile and reads of roles, constraints, API keys and users.", "AUTHOTHER", True,
        PARSER_AUTH_EVENTS, sensitivity=(SENSITIVITY_CONTAINS_PII,)),
    AuditLogGroup(
        GROUP_AUTH_CHANGES, "AUDIT_LOG_AUTHCHANGES", "Auth changes",
        "Changes to roles, user roles, constraints, API keys and Amazon Cognito users.", "AUTHCHANGES", True,
        PARSER_AUTH_EVENTS, sensitivity=(SENSITIVITY_CONTAINS_PII,)),
    AuditLogGroup(
        GROUP_ACTIONS, "AUDIT_LOG_ACTIONS", "Actions",
        "What users and system workers did to data: creates, updates, deletes, reads and searches.",
        "ACTIONS", True, PARSER_ACTIONS),
    AuditLogGroup(
        GROUP_ERRORS, "AUDIT_LOG_ERRORS", "Errors",
        "Request errors recorded by the handlers.", "ERRORS", True, PARSER_ERRORS),
)


def _custom_lane() -> Tuple[AuditLogGroup, ...]:
    # A fork's custom/customAuditActions.py may not define the name (the file ships empty and is never
    # edited upstream), so an older fork file keeps importing.
    return tuple(getattr(_custom_audit_actions, "CUSTOM_AUDIT_LOG_GROUPS", ()) or ())


def merge_audit_log_groups(*lanes: Iterable[AuditLogGroup]) -> Tuple[AuditLogGroup, ...]:
    """The descriptors of the lanes in order, a later lane never replacing an earlier key."""
    merged: Dict[str, AuditLogGroup] = {}
    for lane in lanes:
        for group in lane:
            merged.setdefault(group.key, group)
    return tuple(merged.values())


CUSTOM_AUDIT_LOG_GROUPS: Tuple[AuditLogGroup, ...] = _custom_lane()

AUDIT_LOG_GROUPS: Tuple[AuditLogGroup, ...] = merge_audit_log_groups(
    CORE_AUDIT_LOG_GROUPS, ADDON_AUDIT_LOG_GROUPS, CUSTOM_AUDIT_LOG_GROUPS)

AUDIT_LOG_GROUPS_BY_KEY: Dict[str, AuditLogGroup] = {group.key: group for group in AUDIT_LOG_GROUPS}


def audit_log_group(key: Optional[str],
                    groups: Optional[Dict[str, AuditLogGroup]] = None) -> Optional[AuditLogGroup]:
    """The descriptor of a group key, or None for a key that is not registered."""
    if not isinstance(key, str):
        return None
    return (AUDIT_LOG_GROUPS_BY_KEY if groups is None else groups).get(key)


def audit_log_group_keys() -> Tuple[str, ...]:
    """Every registered group key, in registry order."""
    return tuple(group.key for group in AUDIT_LOG_GROUPS)


def action_target_group_keys() -> FrozenSet[str]:
    """The groups ``log_action`` may write action records to."""
    return frozenset(group.key for group in AUDIT_LOG_GROUPS if group.action_target)


def action_target_group_labels() -> Dict[str, str]:
    """The record prefix label of each group ``log_action`` may write to, by group key."""
    return {group.key: group.prefix for group in AUDIT_LOG_GROUPS if group.action_target}


def groups_without_type_segment() -> FrozenSet[str]:
    """The action-target groups whose records carry no ``[type: ...]`` segment."""
    return frozenset(group.key for group in AUDIT_LOG_GROUPS if group.action_target and not group.has_type_segment)


def resource_key(group: AuditLogGroup) -> Any:
    """The ``ResourceKeys`` entry that resolves a group's CloudWatch log group name.

    Raises AttributeError for a descriptor naming a ``ResourceKeys`` attribute that does not exist; the
    registry contract test fails on that before it reaches a deployment.
    """
    from common.resourceNames import ResourceKeys
    return getattr(ResourceKeys, group.resource_key_name)


def resource_keys() -> Tuple[Any, ...]:
    """The ``ResourceKeys`` entry of every registered group, in registry order."""
    return tuple(resource_key(group) for group in AUDIT_LOG_GROUPS)


def authorization_object(group_key: str) -> Dict[str, str]:
    """The object a group is authorized as: ``{object__type: auditLog, auditLogGroup: <key>}``.

    The audit search checks each requested group with this object and the GET action. The key is the
    group's registry key, so the constraint value and the registry cannot drift apart.
    """
    return {"object__type": "auditLog", "auditLogGroup": group_key}
