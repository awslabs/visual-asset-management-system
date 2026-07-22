# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure fileUpload trigger matching.

Given an uploaded file (its owning database + asset + asset-relative key) and the set of fileUpload
trigger rows that apply, decide which workflows fire and build the per-trigger execute request body.

No AWS/env dependencies — the dispatcher lambda resolves the trigger rows (WorkflowTriggersTable
TriggersByTypeGSI) + asset and passes them in; this module encodes the matching rules so they
unit-test in isolation, mirroring the other common/workflows pure modules.

Matching rules (per trigger row's triggerConfig):
  - inputFileFilters {allow, exclude}: the uploaded file must pass (reuses executionValidation's
    apply_input_file_filters semantics — empty allow = allow-all; exclude wins).
  - Database scope: a GLOBAL trigger fires for any database's upload; a database-scoped trigger fires
    only for uploads in its own database (mirrors the workflow execute database-scope rule).
  - Disabled trigger rows (enabled=false) never fire.
The built execute body carries pipelineExecutionParameters from the trigger's defaultTemplateIds map
(keyed "pipelineDatabaseId:pipelineId" -> templateId), triggerType="fileUpload", and the single
uploaded input file.
"""

from customLogging.logger import safeLogger
from common.workflows import executionValidation as ev

logger = safeLogger(service_name="TriggerMatching")

GLOBAL_DATABASE = "GLOBAL"
TRIGGER_TYPE_FILE_UPLOAD = "fileUpload"


def _trigger_fires(trigger_row, upload_database_id, relative_file_key):
    """Whether one fileUpload trigger row fires for an uploaded file. Applies enabled, database
    scope, and inputFileFilters."""
    if trigger_row.get("enabled") is False:
        return False

    trigger_database_id = trigger_row.get("workflowDatabaseId", "")
    # GLOBAL trigger fires for any upload; a database trigger only for its own database's uploads.
    if trigger_database_id != GLOBAL_DATABASE and trigger_database_id != upload_database_id:
        return False

    filters = (trigger_row.get("triggerConfig") or {}).get("inputFileFilters") or {}
    candidate = [{"relativeFileKey": relative_file_key}]
    return ev.apply_input_file_filters(candidate, filters) == candidate


def _default_template_params(trigger_row):
    """Build pipelineExecutionParameters from a trigger's defaultTemplateIds map. The map is keyed by
    the composite pipeline key ("pipelineDatabaseId:pipelineId"); the execute request keys
    pipelineExecutionParameters by pipelineId, so the pipelineId (the part after the last ':') is used.
    A pipeline with no default template entry simply gets no parameters (system/exec vars only)."""
    default_template_ids = (trigger_row.get("triggerConfig") or {}).get("defaultTemplateIds") or {}
    params = {}
    for composite_key, template_id in default_template_ids.items():
        if not template_id:
            continue
        pipeline_id = composite_key.split(":")[-1] if ":" in composite_key else composite_key
        # The execute request keys pipelineExecutionParameters by pipelineId, so two default entries
        # sharing a pipelineId across databases collapse to one (a shared limitation of the execute
        # contract). Warn on collision so a legitimately-configured trigger that would silently
        # resolve the wrong / a missing template is diagnosable rather than opaque.
        if pipeline_id in params and params[pipeline_id].get("templateId") != template_id:
            logger.warning(
                f"fileUpload trigger defaultTemplateIds collide on pipelineId '{pipeline_id}' "
                "across databases; the execute request is keyed by pipelineId so one default wins.")
        params[pipeline_id] = {"templateId": template_id}
    return params


def build_trigger_execute_body(trigger_row, database_id, asset_id, relative_file_key, version_id=""):
    """Build the asset-less execute request body for a fired fileUpload trigger. The uploaded file is
    the single input; output target defaults to the input asset (the handler locks it when the
    workflow does not allow override); per-pipeline template params come from defaultTemplateIds."""
    return {
        "inputFiles": [{
            "databaseId": database_id,
            "assetId": asset_id,
            "relativeFileKey": relative_file_key,
            "versionId": version_id or "",
        }],
        "outputAssetId": asset_id,
        "outputDatabaseId": database_id,
        "pipelineExecutionParameters": _default_template_params(trigger_row),
        "triggerType": "fileUpload",
    }


def match_fileupload_triggers(trigger_rows, database_id, asset_id, relative_file_key, version_id=""):
    """Return the list of (workflowDatabaseId, workflowId, executeBody) for every fileUpload trigger
    that fires for an uploaded file. Non-firing triggers (disabled, wrong database, filtered out) are
    omitted. The dispatcher launches one execution per returned entry."""
    matches = []
    for trigger_row in trigger_rows or []:
        if trigger_row.get("triggerType", TRIGGER_TYPE_FILE_UPLOAD) != TRIGGER_TYPE_FILE_UPLOAD:
            continue
        if not _trigger_fires(trigger_row, database_id, relative_file_key):
            continue
        workflow_database_id = trigger_row.get("workflowDatabaseId", "")
        workflow_id = trigger_row.get("workflowId", "")
        if not workflow_id:
            continue
        body = build_trigger_execute_body(
            trigger_row, database_id, asset_id, relative_file_key, version_id)
        matches.append((workflow_database_id, workflow_id, body))
    return matches
