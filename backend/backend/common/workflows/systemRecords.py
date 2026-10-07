# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure rules for system (deployment-owned) pipeline and workflow records.

A pipeline or workflow row carrying ``isSystem: true`` was registered from a ``vamsSchema`` bundle by
the deployment's import custom resource, which owns it: through the API the record may be switched on
and off and the content of its templates may be edited, and every other change is refused with a 400
carrying one of the messages below. The import custom resource is exempt — it re-asserts the bundle on
every deploy — and identifies itself with a marker on its ``lambdaCrossCall`` envelope. An API Gateway
request cannot carry that envelope: the proxy integration builds the event itself and confines caller
data to the body, headers, path and query parameters.

This module has NO AWS or environment dependency so it imports and unit-tests in isolation, like the
other ``common.workflows`` record helpers.
"""

from typing import Optional

from common.workflows.workflowRecords import build_file_upload_trigger_config

# The marker the import custom resource adds to its cross-call envelope:
# {"lambdaCrossCall": {"userName": "SYSTEM_USER", "source": IMPORT_SOURCE_MARKER}}.
IMPORT_SOURCE_MARKER = "vamsSchemaImport"

# The identity the importer runs as. The marker is trusted only together with it.
_IMPORT_USER_NAME = "SYSTEM_USER"

# Refusal messages. Every refusal is a 400 with an explicit body: the CLI and the MCP server surface
# a 400 message verbatim and discard the body of a 403.
SYSTEM_PIPELINE_READONLY_MESSAGE = 'System pipelines are read-only; only "enabled" may be changed.'
SYSTEM_WORKFLOW_READONLY_MESSAGE = 'System workflows are read-only; only "enabled" may be changed.'
SYSTEM_PIPELINE_ARCHIVE_MESSAGE = (
    "System pipelines cannot be archived or restored through the API; the deployment owns them.")
SYSTEM_WORKFLOW_ARCHIVE_MESSAGE = (
    "System workflows cannot be archived or restored through the API; the deployment owns them.")
SYSTEM_TEMPLATE_LOCKED_MESSAGE = "Templates of system pipelines cannot be added or deleted."
SYSTEM_TRIGGER_LOCKED_MESSAGE = "Triggers of system workflows cannot be added or deleted."

# The one field a PUT may change on a system pipeline or workflow.
SYSTEM_UPDATE_ALLOWED_FIELDS = frozenset({"enabled"})

# A system pipeline's template: the fields an API caller may change, and the fields that must equal
# the stored value when supplied (the web form sends the whole template body on every save).
TEMPLATE_UNLOCKED_FIELDS = frozenset({"configBody", "tagSchema", "webFormJson"})
TEMPLATE_LOCKED_FIELDS = ("templateName", "description", "configFormat", "allowCustomEdit",
                          "inputInstructions", "overrides", "isDefault")
# What a stored template row reads as for a field it omits (pipelineRecords.build_template_record).
_TEMPLATE_STORED_DEFAULTS = {
    "templateName": "", "description": "", "configFormat": "json", "allowCustomEdit": False,
    "inputInstructions": "", "overrides": {}, "isDefault": False,
}

# A system workflow's trigger: the locked fields. `enabled` is free.
TRIGGER_LOCKED_FIELDS = ("inputFileFilters", "defaultTemplateIds")


def is_schema_import_call(event) -> bool:
    """True only for the import custom resource's own cross-call: the reserved system identity AND
    the importer's source marker. Every other caller — an API request, which cannot carry a top-level
    lambdaCrossCall key, or another SYSTEM_USER cross-caller — is not exempt."""
    if not isinstance(event, dict):
        return False
    cross_call = event.get("lambdaCrossCall")
    if not isinstance(cross_call, dict):
        return False
    return (cross_call.get("userName") == _IMPORT_USER_NAME
            and cross_call.get("source") == IMPORT_SOURCE_MARKER)


def system_update_allowed_fields(raw_body: dict, allowed: set) -> Optional[str]:
    """The first supplied field (sorted) that a system record's PUT may not change, or None when every
    supplied field is allowed. A key whose value is None is not supplied."""
    supplied = sorted(key for key, value in (raw_body or {}).items() if value is not None)
    for field in supplied:
        if field not in allowed:
            return field
    return None


def _same_template_value(field, supplied, stored):
    if field in ("allowCustomEdit", "isDefault"):
        return bool(supplied) == bool(stored)
    if field == "overrides":
        return dict(supplied or {}) == dict(stored or {})
    return supplied == stored


def template_locked_field_changed(raw_body, stored, unlocked=TEMPLATE_UNLOCKED_FIELDS) -> Optional[str]:
    """The first locked template field the request would change, or None.

    Value-based: a supplied field equal to the stored value is not a change, because the web form
    sends the whole template body on every save; a field that is absent or None is not supplied. Only
    TEMPLATE_LOCKED_FIELDS are compared, and `unlocked` names the fields that may change."""
    body = raw_body or {}
    row = stored or {}
    for field in TEMPLATE_LOCKED_FIELDS:
        if field in unlocked or field not in body or body[field] is None:
            continue
        current = row.get(field)
        if current is None:
            current = _TEMPLATE_STORED_DEFAULTS[field]
        if not _same_template_value(field, body[field], current):
            return field
    return None


def system_template_locked_field_message(field: str) -> str:
    """The 400 body for a template PUT that changes a locked field of a system pipeline's template."""
    return ('Templates of system pipelines are read-only except "configBody", "tagSchema" and '
            f'"webFormJson"; "{field}" differs from the stored value.')


def _pattern_list(value):
    if isinstance(value, list):
        return sorted(str(pattern) for pattern in value)
    return [str(value)] if value else []


def _normalized_trigger_config(input_file_filters, default_template_ids):
    """The comparable form of a trigger's locked fields: the shape build_file_upload_trigger_config
    stores, with a missing allow/exclude read as empty and the pattern lists order-insensitive."""
    config = build_file_upload_trigger_config(
        input_file_filters=dict(input_file_filters or {}) or None,
        default_template_ids=dict(default_template_ids or {}) or None)
    filters = config["inputFileFilters"]
    return {
        "inputFileFilters": {
            "allow": _pattern_list(filters.get("allow")),
            "exclude": _pattern_list(filters.get("exclude")),
        },
        "defaultTemplateIds": dict(config["defaultTemplateIds"]),
    }


def trigger_locked_field_changed(raw_body, stored_config) -> Optional[str]:
    """The first locked trigger field a PUT would change on a system workflow's trigger, or None.

    Reads the RAW request body: the request model defaults an absent field to an empty mapping, and an
    absent field here means "keep the stored value", not "blank it". A supplied field is compared with
    the stored triggerConfig after both are normalised the way the store writes them, so a client that
    re-sends the stored trigger with only `enabled` flipped compares equal. A supplied value that is not
    a mapping is a change (the request model rejects it as well)."""
    body = raw_body or {}
    stored = stored_config or {}
    supplied = {}
    for field in TRIGGER_LOCKED_FIELDS:
        value = body.get(field)
        if value is None:
            supplied[field] = stored.get(field)
        elif not isinstance(value, dict):
            return field
        else:
            supplied[field] = value
    before = _normalized_trigger_config(stored.get("inputFileFilters"), stored.get("defaultTemplateIds"))
    after = _normalized_trigger_config(supplied["inputFileFilters"], supplied["defaultTemplateIds"])
    for field in TRIGGER_LOCKED_FIELDS:
        if before[field] != after[field]:
            return field
    return None


def system_trigger_locked_field_message(field: str) -> str:
    """The 400 body for a trigger PUT that changes a locked field of a system workflow's trigger."""
    return (f'Triggers of system workflows are read-only except "enabled"; "{field}" differs from '
            'the stored trigger.')
