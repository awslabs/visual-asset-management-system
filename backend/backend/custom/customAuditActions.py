# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Fork extension point. Upstream VAMS ships this file empty and does not modify it, so edits here do
not conflict on upgrade.

Audit actions of the routes and handlers a fork adds. ``common/auditActions.py`` merges these into
the audit registry after the core and add-on lists. Every route in ``custom/customApiRoutes.py``
needs an entry in ``CUSTOM_AUDIT_ROUTE_ACTIONS`` (or, with a reason, in ``CUSTOM_AUDIT_EXEMPT``):
the route coverage test fails otherwise. Build entries with ``route_entries``, ``spec`` and ``read``
from ``common/auditActionTypes.py``, and record the action from the handler with
``customLogging.auditLogging.log_action`` after the write succeeds.

An entry that names a core or add-on route-method or action, or an action name that is not lower
camelCase, fails the registry tests.
"""

from typing import Dict, Tuple

from common.auditActionTypes import AuditSpec, RouteMethod, SystemAction

CUSTOM_AUDIT_ROUTE_ACTIONS: Dict[RouteMethod, AuditSpec] = {}

CUSTOM_AUDIT_EXEMPT: Dict[RouteMethod, str] = {}

CUSTOM_AUDIT_SECONDARY_ACTIONS: Tuple[AuditSpec, ...] = ()

CUSTOM_AUDIT_SYSTEM_ACTIONS: Tuple[SystemAction, ...] = ()
