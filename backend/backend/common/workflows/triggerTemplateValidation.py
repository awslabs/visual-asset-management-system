# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cross-entity validation for headless (auto-triggered) template use.

An auto-triggered workflow runs with no person to fill template tags, so a template used as a
trigger's default cannot carry a REQUIRED tag with no default value — every triggered execution
would otherwise fail at render (`validate_tags` -> 'tag X is required'). Interactive executes are
unaffected: the person supplies the value, and the execute path already rejects a missing required
tag at run time.

The same absence of a person applies to the template CHOICE. A pipeline whose systemConfig sets
`requireTemplate` runs only with a template named for it, and a headless run can take one from just
three places: the trigger's own map, the workflow reference's `defaultTemplateId`, or the pipeline's
default template. A pipeline that requires no template needs none of them, so a template-less
pipeline — and a trigger that names no templates at all — stays valid.

This module centralizes the three guards so the trigger, template, and pipeline services stay
consistent:
  - trigger save:  reject if a referenced default template has a required-without-default tag, or if
    a require-template pipeline of the workflow has no default template from any of the three.
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

# Bounds on the workflows walk behind the pipeline-save trigger-template warning. The walk runs
# synchronously on every save of a require-template pipeline, so the page cap keeps one save from
# reading an arbitrarily large workflow table and the page size bounds each individual read.
# Stopping at the cap leaves part of the table unread, which the returned list reports explicitly:
# the walk produces WARNINGS, so a shortened list must not read as "nothing is misconfigured".
# Mirrors pipelineService.MAX_REFERENCING_WORKFLOW_PAGES / REFERENCING_WORKFLOW_PAGE_SIZE.
WORKFLOW_WALK_PAGE_SIZE = 200
MAX_WORKFLOW_WALK_PAGES = 20

# Bounds on the two costs the page cap does not bound. Each workflow the scan MATCHES costs one
# further sequential read (the caller's get_trigger_row is a single get_item), and each match whose
# trigger picked no default template adds one multi-line string to a list the pipeline save returns
# inline in its response body. Paging alone therefore still permits thousands of sequential get_item
# calls and a warning list far larger than the response is meant to carry, on a synchronous save.
# 200 trigger reads cap the added latency at roughly one to two seconds; 25 reported workflows are
# enough to act on and mirror pipelineService.MAX_REFERENCING_WORKFLOWS. Hitting either cap stops the
# walk and is reported the same way the page cap is: this function produces WARNINGS, so a shortened
# list must not read as "nothing is misconfigured".
MAX_TRIGGER_ROW_LOOKUPS = 200
MAX_TRIGGER_TEMPLATE_WARNINGS = 25


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


def trigger_supplied_pipeline_ids(default_template_ids):
    """The pipelineIds a trigger's `defaultTemplateIds` map supplies a template for.

    The map is keyed by the composite `pipelineDatabaseId:pipelineId`, but a headless run resolves it
    by pipelineId alone: triggerMatching._default_template_params keys the execute request's
    pipelineExecutionParameters by the part after the last ':' and drops the database half, and
    executeWorkflow._resolve_pipeline_configs then looks those parameters up by the pipeline record's
    own pipelineId. So the database half of a key does not have to agree with the workflow step for the
    template to reach it, and matching on the whole composite would reject a run that works."""
    supplied = set()
    for composite, template_id in (default_template_ids or {}).items():
        if not template_id:
            continue
        key = composite or ""
        supplied.add(key.split(":")[-1] if ":" in key else key)
    supplied.discard("")
    return supplied


def validate_trigger_required_templates(default_template_ids, workflow_pipelines):
    """For a trigger being saved, return a list of human-readable errors — one per pipeline of the
    parent workflow that REQUIRES a template while nothing would supply one to a headless run.

    A trigger never has to name a template, so this is deliberately narrow: it says nothing about a
    pipeline that requires no template, nothing about a template-less pipeline, and nothing about
    whether a named template is a good choice. The single unrunnable combination is a pipeline whose
    systemConfig sets `requireTemplate` and for which NO template is named anywhere — the trigger
    picks none, the workflow reference carries no fallback, and the pipeline has no default template of
    its own — because a triggered execution has nobody to choose one and template resolution rejects
    the run ('this pipeline requires a template (templateId) for execution').

    `default_template_ids` is the trigger's {`dbId:pipelineId`: templateId} map, read the way a
    headless run reads it (see trigger_supplied_pipeline_ids).
    `workflow_pipelines` is the parent workflow's ordered pipeline references, one dict per step:
    {pipelineDatabaseId, pipelineId, systemConfig, defaultTemplateId?, pipelineDefaultTemplateId?}.
    The two template keys are the other two sources executeWorkflow._resolve_pipeline_configs falls
    back to when the trigger names none for that step — the workflow reference's own
    `defaultTemplateId`, then the pipeline's own default template (`isDefault`, which the vamsSchema
    importer promotes a lone shipped template to for a require-template pipeline). Any one of the three
    satisfies the requirement. A step whose `systemConfig` is absent is skipped rather than read as
    requiring a template, so a caller that could not load a pipeline record never turns an unknown into
    a rejection."""
    errors = []
    supplied = trigger_supplied_pipeline_ids(default_template_ids)
    for ref in workflow_pipelines or []:
        system_config = (ref or {}).get("systemConfig") or {}
        if not system_config.get("requireTemplate"):
            continue
        pipeline_id = (ref or {}).get("pipelineId", "")
        if (pipeline_id in supplied
                or (ref or {}).get("defaultTemplateId")
                or (ref or {}).get("pipelineDefaultTemplateId")):
            continue
        errors.append(
            f"pipeline '{pipeline_id}' requires a template but no default template is set for it. A "
            f"triggered (headless) execution cannot choose one, so pick a default template for this "
            f"pipeline in the trigger."
        )
    return errors


def triggers_referencing_template(triggers_table, pipeline_database_id, pipeline_id, template_id):
    """Return the list of (workflowDatabaseId, workflowId, triggerType) tuples whose ENABLED trigger
    picks this template as a default for this pipeline. Queries TriggersByBaseTypeGSI once per trigger
    type (paginated to exhaustion) rather than scanning the table. Best-effort: returns [] on a read
    error.

    Disabled triggers are excluded — see the comment at the filter for why that does not lose the
    check.

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
                    # A DISABLED trigger cannot fire, so it cannot be broken by a required tag, and it
                    # must not block a template change. Measured: a disabled fileUpload trigger created
                    # weeks earlier held `isaaclab-evaluation-cartpole` as its default and refused a
                    # correct edit to that template -- one making CHECKPOINT_PATH required, without which
                    # a defaults-only evaluation renders an empty checkpoint path and the container exits
                    # 1 after provisioning a GPU.
                    #
                    # The check is not lost by skipping it here: saving a trigger re-validates its chosen
                    # default templates (`validate_trigger_default_templates` in workflowTriggerService),
                    # so re-enabling one whose template has since gained a required tag is refused at that
                    # point -- where the operator is acting on the trigger and can act on the message.
                    #
                    # A row with NO `enabled` key is treated as ENABLED: absent is not disabled, and the
                    # conservative direction for a guard is to keep blocking.
                    if row.get("enabled") is False:
                        continue
                    default_ids = _trigger_default_template_ids(row)
                    if default_ids.get(composite) == template_id:
                        hits.append((
                            row.get("workflowDatabaseId", ""),
                            row.get("workflowId", ""),
                            row.get("triggerType", ""),
                        ))
                # Paged on the PRESENCE of the key, which is how DynamoDB signals the last page.
                if "LastEvaluatedKey" not in resp:
                    break
                kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
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
    include this pipeline, then for each such workflow reads
    its trigger via `get_trigger_row(workflowDatabaseId, workflowId)` (returns the fileUpload trigger
    row or None) and checks whether it picked a default template for this pipeline. Best-effort:
    returns [] on any read error or when the pipeline requires no template.

    The walk is bounded on all three of its costs, because it runs on the synchronous save path:
    MAX_WORKFLOW_WALK_PAGES pages of WORKFLOW_WALK_PAGE_SIZE items scanned, MAX_TRIGGER_ROW_LOOKUPS
    `get_trigger_row` reads across those pages, and MAX_TRIGGER_TEMPLATE_WARNINGS reported workflows.
    A walk that stops at any of the three has not seen the whole table, and says so as one further
    warning in the returned list: every other outcome of this function is "no warning" (a read error,
    a clean deployment, a projection that dropped an attribute), so a truncated walk with no signal
    would be indistinguishable from a correctly configured one. The returned list is therefore at
    most MAX_TRIGGER_TEMPLATE_WARNINGS + 1 strings long.

    The scan projects only the three attributes the membership test and the message use, which cuts
    the bytes transferred and deserialized per save rather than the read capacity consumed - DynamoDB
    charges a Scan on the size of the items it EVALUATES, so read capacity still scales with the
    whole table. Reducing that would take a different access pattern, not a projection.
    It stays a table scan: the constant-partition `WorkflowsByDateGSI` is
    sparse, so a workflow row written without `allListPartition` would be invisible to it — exactly
    the row most likely to be misconfigured."""
    if not require_template:
        return []
    composite = pr.pipeline_composite_key(pipeline_database_id, pipeline_id)
    warnings = []
    try:
        kwargs = {"ProjectionExpression": "databaseId, workflowId, specifiedPipelines",
                  "Limit": WORKFLOW_WALK_PAGE_SIZE}
        read_every_page = False
        incomplete_reason = None
        trigger_lookups = 0
        for _ in range(MAX_WORKFLOW_WALK_PAGES):
            resp = workflows_table.scan(**kwargs) or {}
            for wf in resp.get("Items", []):
                if not _workflow_includes_pipeline(wf, composite):
                    continue
                if len(warnings) >= MAX_TRIGGER_TEMPLATE_WARNINGS:
                    incomplete_reason = (
                        f"names only the first {MAX_TRIGGER_TEMPLATE_WARNINGS} affected workflows")
                    break
                if trigger_lookups >= MAX_TRIGGER_ROW_LOOKUPS:
                    incomplete_reason = (
                        f"read the trigger of only the first {MAX_TRIGGER_ROW_LOOKUPS} workflows "
                        f"that use this pipeline")
                    break
                wf_db = wf.get("databaseId", "")
                wf_id = wf.get("workflowId", "")
                trigger_lookups += 1
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
            if incomplete_reason:
                break
            # Paged on the PRESENCE of the key, which is how DynamoDB signals the last page.
            if "LastEvaluatedKey" not in resp:
                read_every_page = True
                break
            kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
        if incomplete_reason is None and not read_every_page:
            incomplete_reason = (
                f"read only the first {MAX_WORKFLOW_WALK_PAGES} pages of workflows")
        if incomplete_reason:
            logger.warning(
                f"Trigger-template warning walk for {composite} stopped early: "
                f"{incomplete_reason}.")
            warnings.append(
                f"the check for auto-triggered workflows needing a default template for this "
                f"pipeline {incomplete_reason}, so this list may be incomplete. Review the "
                f"file-upload triggers of the workflows that use this pipeline directly."
            )
    except Exception as e:
        logger.exception(
            f"Error reading workflows referencing pipeline {pipeline_database_id}:{pipeline_id}: {e}")
        return []
    return warnings
