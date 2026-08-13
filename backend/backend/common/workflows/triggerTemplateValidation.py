# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cross-entity validation for headless (auto-triggered) template use.

An auto-triggered workflow runs with no person to fill template tags, so a template used as a
trigger's default cannot carry a REQUIRED tag with no default value — every triggered execution
would otherwise fail at render (`validate_tags` -> 'tag X is required'). Interactive executes are
unaffected: the person supplies the value, and the execute path already rejects a missing required
tag at run time.

This module centralizes the three guards so the trigger, template, and pipeline services stay
consistent:
  - trigger save:  reject if a referenced default template has a required-without-default tag.
  - template save: reject if the template is referenced by any trigger AND now has such a tag.
  - pipeline save: WARN (non-blocking) if a require-template pipeline is in an auto-triggered
    workflow whose trigger picked no default template for it.

All reads are best-effort DynamoDB reads scoped by composite keys; the caller passes in the table
resources and the tag-schema loader so this module stays free of module-level resource resolution.
"""

from boto3.dynamodb.conditions import Key

from customLogging.logger import safeLogger
from common.workflows import pipelineRecords as pr
from common.workflows import workflowRecords as wr
from common.workflows.templateTagSchema import required_tags_without_default

logger = safeLogger(service_name="TriggerTemplateValidation")


def _trigger_default_template_ids(trigger_row):
    """The {`pipelineDatabaseId:pipelineId`: templateId} map a fileUpload trigger picked, or {}."""
    return ((trigger_row or {}).get("triggerConfig", {}) or {}).get("defaultTemplateIds", {}) or {}


def validate_trigger_default_templates(default_template_ids, load_tag_schema_fields):
    """For a trigger being saved, return a list of human-readable errors — one per referenced default
    template that has a required tag with no default value (a headless run could never supply it).

    `default_template_ids` is the trigger's {`dbId:pipelineId`: templateId} map.
    `load_tag_schema_fields(pipeline_database_id, pipeline_id, template_id)` returns the template's
    tag-schema fields list (or None)."""
    errors = []
    for composite, template_id in (default_template_ids or {}).items():
        if not template_id:
            continue
        pipeline_db, _, pipeline_id = (composite or "").partition(":")
        if not pipeline_db or not pipeline_id:
            continue
        fields = load_tag_schema_fields(pipeline_db, pipeline_id, template_id) or []
        missing = required_tags_without_default(fields)
        if missing:
            errors.append(
                f"template '{template_id}' (pipeline '{pipeline_id}') is chosen as a trigger default "
                f"but has required tag(s) with no default value: {', '.join(missing)}. A "
                f"triggered (headless) execution cannot supply these, so give each a default value "
                f"or make it optional."
            )
    return errors


def triggers_referencing_template(triggers_table, pipeline_database_id, pipeline_id, template_id):
    """Return the list of (workflowDatabaseId, workflowId, triggerType) tuples whose trigger picks
    this template as a default for this pipeline. Queries TriggersByBaseTypeGSI once per trigger type
    (paginated to exhaustion) rather than scanning the table. Best-effort: returns [] on a read
    error.

    The index partitions on the BARE type: a workflow may carry several triggers of one type, whose sort
    keys are suffixed ("fileUpload#nightly"), and each is a separate row that may pick its own default
    template. Keying this lookup on the sort key would find only the first trigger of each type, so a
    template still referenced by an additional trigger would read as unreferenced. The returned
    triggerType is the row's KEY, so a caller can name the exact trigger."""
    composite = pr.pipeline_composite_key(pipeline_database_id, pipeline_id)
    hits = []
    try:
        for trigger_type in wr.TRIGGER_TYPES:
            kwargs = {
                "IndexName": "TriggersByBaseTypeGSI",
                "KeyConditionExpression": Key("triggerBaseType").eq(trigger_type),
            }
            while True:
                resp = triggers_table.query(**kwargs)
                for row in resp.get("Items", []):
                    default_ids = _trigger_default_template_ids(row)
                    if default_ids.get(composite) == template_id:
                        hits.append((
                            row.get("workflowDatabaseId", ""),
                            row.get("workflowId", ""),
                            row.get("triggerType", ""),
                        ))
                lek = resp.get("LastEvaluatedKey")
                if not lek:
                    break
                kwargs["ExclusiveStartKey"] = lek
    except Exception as e:
        logger.exception(
            f"Error reading triggers referencing template {pipeline_database_id}:{pipeline_id}:"
            f"{template_id}: {e}")
        return []
    return hits


def validate_template_not_breaking_triggers(triggers_table, pipeline_database_id, pipeline_id,
                                            template_id, tag_schema_fields):
    """For a template being saved, return errors when the template is referenced by any trigger as a
    default AND the new tag schema has a required tag with no default (which would break those
    headless triggers). Returns [] when the template has no such tag or is not trigger-referenced."""
    missing = required_tags_without_default(tag_schema_fields or [])
    if not missing:
        return []
    refs = triggers_referencing_template(
        triggers_table, pipeline_database_id, pipeline_id, template_id)
    if not refs:
        return []
    logger.info(
        f"Template {pipeline_database_id}:{pipeline_id}:{template_id} is a trigger default for "
        f"workflow(s) {', '.join(f'{db}:{wf}' for (db, wf, _t) in refs)}")
    return [
        f"this template is a trigger default for one or more auto-triggered workflows and has "
        f"required tag(s) with no default value: {', '.join(missing)}. A triggered (headless) "
        f"execution cannot supply these — give each a default value, make it optional, or remove "
        f"the template from the trigger first."
    ]


def _workflow_includes_pipeline(workflow_row, pipeline_composite):
    """True when a workflow's specifiedPipelines snapshot lists this pipeline (composite key)."""
    for ref in (workflow_row or {}).get("specifiedPipelines", []) or []:
        if (ref or {}).get("pipelineDatabaseId:pipelineId") == pipeline_composite:
            return True
    return False


def pipeline_trigger_template_warnings(workflows_table, get_trigger_row, pipeline_database_id,
                                       pipeline_id, require_template):
    """Return a list of WARNING strings (non-blocking) for a pipeline being saved that REQUIRES a
    template and belongs to an auto-triggered workflow whose trigger picked NO default template for
    it — a triggered run would fail because no template is selected and none can be chosen headlessly.

    Authoritative membership: scans the workflows table for workflows whose specifiedPipelines
    include this pipeline (bounded, low-frequency save-path scan), then for each such workflow reads
    its trigger via `get_trigger_row(workflowDatabaseId, workflowId)` (returns the fileUpload trigger
    row or None) and checks whether it picked a default template for this pipeline. Best-effort:
    returns [] on any read error or when the pipeline requires no template."""
    if not require_template:
        return []
    composite = pr.pipeline_composite_key(pipeline_database_id, pipeline_id)
    warnings = []
    try:
        kwargs = {}
        while True:
            resp = workflows_table.scan(**kwargs)
            for wf in resp.get("Items", []):
                if not _workflow_includes_pipeline(wf, composite):
                    continue
                wf_db = wf.get("databaseId", "")
                wf_id = wf.get("workflowId", "")
                trigger_row = get_trigger_row(wf_db, wf_id)
                if not trigger_row:
                    continue  # No auto-trigger → interactive runs supply the template; no warning.
                default_ids = _trigger_default_template_ids(trigger_row)
                if not default_ids.get(composite):
                    warnings.append(
                        f"pipeline '{pipeline_id}' requires a template and is part of "
                        f"auto-triggered workflow '{wf_db}:{wf_id}' (trigger "
                        f"'{trigger_row.get('triggerType', '')}'), but that trigger has not chosen a "
                        f"default template for it. Triggered executions will fail until the trigger "
                        f"picks a default template for this pipeline."
                    )
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek
    except Exception as e:
        logger.exception(
            f"Error reading workflows referencing pipeline {pipeline_database_id}:{pipeline_id}: {e}")
        return []
    return warnings
