# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure fileUpload trigger matching.

Given an uploaded file (its owning database + asset + asset-relative key) and the set of fileUpload
trigger rows that apply, decide which workflows fire and build the per-trigger execute request body.

No AWS/env dependencies — the dispatcher lambda resolves the trigger rows (WorkflowTriggersTable
TriggersByBaseTypeGSI) + asset and passes them in; this module encodes the matching rules so they
unit-test in isolation, mirroring the other common/workflows pure modules.

Matching rules (per trigger row's triggerConfig):
  - inputFileFilters {allow, exclude}: the uploaded file must pass (reuses executionValidation's
    apply_input_file_filters semantics — empty allow = allow-all; exclude wins).
  - Database scope: a GLOBAL trigger fires for any database's upload; a database-scoped trigger fires
    only for uploads in its own database (mirrors the workflow execute database-scope rule).
  - Disabled trigger rows (enabled=false) never fire.
The built execute body carries pipelineExecutionParameters from the trigger's defaultTemplateIds map
(keyed "pipelineDatabaseId:pipelineId" -> templateId), triggerType="fileUpload", and the uploaded
input file — except for an arity-"none" workflow, which takes no input files and uses the uploaded
file's asset only as the output target.
"""

from customLogging.logger import safeLogger
from common.workflows import executionValidation as ev
from common.s3MetadataKeys import VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION

logger = safeLogger(service_name="TriggerMatching")

GLOBAL_DATABASE = "GLOBAL"
TRIGGER_TYPE_FILE_UPLOAD = "fileUpload"

# A workflow declaring this arity accepts no input files, so its trigger fires with an empty
# inputFiles list; the uploaded file's asset is still what the run writes back to.
ARITY_NONE = "none"


def chaining_allows_trigger(candidate_workflow_id, change_source, change_workflow_id,
                            allow_workflow_trigger_chaining):
    """Whether a workflow may fire on this uploaded file, given who wrote it.

    Three cases, in order:

    1. The file was NOT written by a workflow (a user upload, a direct S3 write, a copy/move) — always
       eligible. This is the ordinary trigger path.
    2. The file was written by THIS workflow — never eligible, whatever the flag says. A workflow
       re-firing on its own output is the A->A loop, and there is deliberately no way to enable it.
    3. The file was written by ANOTHER workflow — eligible only when this workflow opts in via
       `allowWorkflowTriggerChaining`. That is what lets a preview or metadata workflow run on a
       conversion pipeline's output, while keeping chained triggering off by default.

    A workflow-sourced record with no recorded originating workflow id is treated as "another
    workflow": it cannot be proven to be self-output, and the conservative reading is the one that
    still requires an explicit opt-in.
    """
    if change_source != VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION:
        return True
    if change_workflow_id and change_workflow_id == candidate_workflow_id:
        return False
    return bool(allow_workflow_trigger_chaining)


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


def build_trigger_execute_body(trigger_row, database_id, asset_id, relative_file_key, version_id="",
                               input_file_arity=""):
    """Build the asset-less execute request body for a fired fileUpload trigger. The uploaded file is
    the single input; output target defaults to the input asset (the handler locks it when the
    workflow does not allow override); per-pipeline template params come from defaultTemplateIds.

    `input_file_arity` is the target workflow's declared arity. An arity-"none" workflow accepts no
    input files, so the body carries none — the uploaded file selected the trigger and named the asset
    the run writes back to, which is the explicit output pair executeWorkflow's zero-input branch
    requires. Any other arity takes the uploaded file as its single input."""
    body = {
        "inputFiles": [] if input_file_arity == ARITY_NONE else [{
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
    return body


def match_fileupload_triggers(trigger_rows, database_id, asset_id, relative_file_key, version_id="",
                              change_source="", change_workflow_id="",
                              chaining_allowed_for=None, input_file_arity_for=None):
    """Return the list of (workflowDatabaseId, workflowId, executeBody) for every fileUpload trigger
    that fires for an uploaded file. Non-firing triggers (disabled, wrong database, filtered out) are
    omitted. The dispatcher launches one execution per returned entry.

    `change_source` / `change_workflow_id` describe who wrote the uploaded object (from its S3
    provenance metadata). `chaining_allowed_for` is an optional callable
    ``(workflowDatabaseId, workflowId) -> bool`` returning that workflow's
    `allowWorkflowTriggerChaining`; it is only consulted for a workflow-written file, so an ordinary
    user upload costs no extra lookups. Omitting it means "no workflow opts in", which reproduces the
    pre-chaining behavior of never re-firing on workflow output.

    `input_file_arity_for` is an optional callable ``(workflowDatabaseId, workflowId) -> str``
    returning that workflow's `inputFileArity`, so an arity-"none" workflow's trigger fires with no
    input files instead of one its own validation rejects. Omitting it treats every workflow as taking
    the uploaded file."""
    matches = []
    for trigger_row in trigger_rows or []:
        # The row's `triggerType` is its SORT KEY, which is the bare type for a workflow's first trigger
        # of that type and "type#triggerId" for an additional one, so the base type is what identifies
        # the kind. An exact comparison here would silently drop every additional trigger. A row written
        # before multiple triggers existed carries the bare type and reads identically.
        base_type = (trigger_row.get("triggerBaseType")
                     or (trigger_row.get("triggerType") or TRIGGER_TYPE_FILE_UPLOAD).split("#", 1)[0])
        if base_type != TRIGGER_TYPE_FILE_UPLOAD:
            continue
        if not _trigger_fires(trigger_row, database_id, relative_file_key):
            continue
        workflow_database_id = trigger_row.get("workflowDatabaseId", "")
        workflow_id = trigger_row.get("workflowId", "")
        if not workflow_id:
            continue
        # Only a workflow-written file needs the per-workflow flag, so the (possibly remote) lookup
        # is deferred until the filters have already matched.
        if change_source == VAMS_CHANGE_SOURCE_WORKFLOW_EXECUTION:
            allowed = bool(chaining_allowed_for(workflow_database_id, workflow_id))                 if chaining_allowed_for else False
            if not chaining_allows_trigger(workflow_id, change_source, change_workflow_id, allowed):
                logger.info(
                    f"Skipping trigger for workflow {workflow_database_id}:{workflow_id} — the file "
                    f"was written by workflow execution of '{change_workflow_id or 'unknown'}' and "
                    "chaining is not permitted for this workflow")
                continue
        arity = (input_file_arity_for(workflow_database_id, workflow_id)
                 if input_file_arity_for else "")
        body = build_trigger_execute_body(
            trigger_row, database_id, asset_id, relative_file_key, version_id,
            input_file_arity=arity)
        matches.append((workflow_database_id, workflow_id, body))
    return matches
