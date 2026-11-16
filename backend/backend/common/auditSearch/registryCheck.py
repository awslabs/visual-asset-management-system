# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
The completeness check of the audit log group registry.

An audit group is readable by the search only when its descriptor has everything the search needs: a
key that is a valid ``auditLog`` constraint value, a label, a record prefix, a parser, and a
``ResourceKeys`` entry that resolves its CloudWatch log group. :func:`descriptor_problems` lists what is
missing; the registry contract test (``tests/common/auditSearch/test_audit_search_registry_contract.py``)
fails when it returns anything for the merged registry, so a group added without its parser, label or
authorization mapping fails a test instead of reaching a deployment.
"""

from typing import Any, Iterable, List

from common.auditActionTypes import AuditLogGroup
from common.auditLogGroups import AUDIT_LOG_GROUP_KEY_PATTERN, AUDIT_LOG_GROUP_PREFIX_PATTERN
from common.auditSearch.parsers.legacy import LEGACY_PARSERS

CLOUDWATCH_LOG_GROUP_PARAM_PREFIX = "cloudwatchLogGroups/"


def descriptor_problems(groups: Iterable[AuditLogGroup], resource_keys: Any) -> List[str]:
    """What is missing or wrong in the descriptors, as ``<group key>: <problem>`` lines (empty when complete).

    ``resource_keys`` is the ``ResourceKeys`` class the descriptors' ``resource_key_name`` is looked up on.
    """
    problems: List[str] = []
    seen_keys = set()
    seen_prefixes = set()
    for group in groups:
        name = str(getattr(group, "key", "?"))
        if not isinstance(group.key, str) or not AUDIT_LOG_GROUP_KEY_PATTERN.match(group.key):
            problems.append(f"{name}: key is not a valid auditLog constraint value")
        if group.key in seen_keys:
            problems.append(f"{name}: key is registered twice")
        seen_keys.add(group.key)
        if not group.label or not str(group.label).strip():
            problems.append(f"{name}: no label")
        if not group.description or not str(group.description).strip():
            problems.append(f"{name}: no description")
        if not isinstance(group.prefix, str) or not AUDIT_LOG_GROUP_PREFIX_PATTERN.match(group.prefix):
            problems.append(f"{name}: prefix is not a record prefix label")
        elif group.prefix in seen_prefixes:
            problems.append(f"{name}: prefix is used by another group")
        seen_prefixes.add(group.prefix)
        if group.legacy_parser not in LEGACY_PARSERS:
            problems.append(f"{name}: no parser named {group.legacy_parser!r}")
        resource_key = getattr(resource_keys, str(group.resource_key_name), None)
        if resource_key is None:
            problems.append(f"{name}: no ResourceKeys.{group.resource_key_name}")
        elif getattr(resource_key, "param_key", None) != CLOUDWATCH_LOG_GROUP_PARAM_PREFIX + str(group.key):
            problems.append(f"{name}: ResourceKeys.{group.resource_key_name} does not name the group's log group")
    return problems
