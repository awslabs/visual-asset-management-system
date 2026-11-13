# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Checks that decide which databases a compliance request's work or listing covers.

Some compliance requests name no database and work across the databases the caller may act on:
approving a cascade, or creating one that starts at once (the reach recorded for its executor),
sweeping a schema bound in many databases, listing the audit trail across databases, and the
schema listing's create scopes. Each asks the engine, for every candidate database, whether the
caller may act there. That is scoping, not a refused request: the caller asked for none of those
databases, so a database it may not act on is left out without an authorization record. Writing
one denial record per database the caller lacks fills the authorization log with refusals of
requests nobody made (D198).

`scope_allows` asks the engine the question `CasbinEnforcer.enforce` asks and gets the same answer,
deny by default: no enforcer, no engine, or any answer other than `True` is a refusal. It only
leaves out the authorization record `enforce` writes for a denial. Use it only to decide which
databases a request covers. A decision on what the request names or acts on (the database in its
path, a cascade's parent, a row a listing read, an asset it evaluates) is made with `enforce`,
which records each denial.
"""

from typing import Any, Dict


def scope_allows(enforcer: Any, obj: Dict[str, Any], action: str) -> bool:
    """Whether the caller may take `action` on `obj`, as `enforcer.enforce` decides it, without the
    authorization record of a denial. `enforcer` is the caller's `CasbinEnforcer` (or None)."""
    service = getattr(enforcer, "service_object", None) if enforcer is not None else None
    if service is None:
        return False
    return service.enforce(obj, action) is True
