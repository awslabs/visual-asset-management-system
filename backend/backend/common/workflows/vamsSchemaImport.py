# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure vamsSchema -> V2 cross-call request builder.

A pipeline's ``vamsSchema/`` bundle registers a built-in (or externally self-registered) pipeline +
workflow into the V2 tables at CDK deploy time. This module has NO AWS/env dependency so it unit-tests
in isolation: it turns a parsed schema bundle plus the deploy-time resolved resource values into the
ordered list of ``SYSTEM_USER`` cross-call requests the import custom-resource lambda invokes against
the V2 service handlers (pipelineServiceV2 / pipelineTemplateService / workflowServiceV2 /
workflowTriggerService).

Bundle shape (all but ``pipeline`` optional — minimal-required ingestion, plan decision S14):

    {
      "pipeline":  { pipelineId?, pipelineName, category?, description?, systemConfig?,
                     executionConfig? },
      "workflow":  { workflowId?, workflowName, category?, description?, systemConfig?,
                     subDashboardUrl?, specifiedPipelines?, triggers?: [ {triggerType,
                     inputFileFilters?, defaultTemplateIds?, enabled?} ] },
      "templates": [ { templateId?, templateName, configFormat?, configBody?, webFormJson?,
                       allowCustomEdit?, inputInstructions?, overrides?, tagSchema? } ]
    }

Deploy-time resource injection: the schema files are static, but a built-in pipeline's execution
target (Lambda function name / SQS queue url / EventBridge bus arn) is only known at deploy. Those
resolved values are passed as ``resource_overrides`` and merged into the pipeline ``executionConfig``
per its ``executionType`` so the schema files carry no hard-coded ARNs. ID overrides let a built-in
keep a known id across deployments.

The builder is pure: it emits request descriptors (each a dict with ``target``, ``method``, ``path``,
``pathParameters``, ``body``). The lambda maps ``target`` -> the concrete V2 service function name and
invokes it as a ``lambdaCrossCall`` SYSTEM_USER event. Idempotency (create-vs-update, unarchive) is
decided by the lambda after probing existence; this module only produces the create-shaped bodies +
the update-shaped bodies so the lambda can pick.
"""

GLOBAL_DATABASE = "GLOBAL"

# assetScope shorthand -> canonical key (mirrors executionValidation._ASSET_SCOPE_SHORTHAND). A bundle
# may spell whole-asset support either way, so a shorthand declaration must suppress the canonical
# default rather than being silently overridden by it.
_ASSET_SCOPE_SHORTHAND = {"wholeAsset": "wholeAssetAllowed"}

# Cross-call targets (mapped to concrete function names by the lambda).
TARGET_PIPELINE_SERVICE = "pipelineService"
TARGET_TEMPLATE_SERVICE = "templateService"
TARGET_WORKFLOW_SERVICE = "workflowService"
TARGET_TRIGGER_SERVICE = "triggerService"


class VamsSchemaError(Exception):
    """Raised when a vamsSchema bundle is structurally invalid (before any cross-call)."""


def _require(mapping, key, where):
    value = mapping.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise VamsSchemaError(f"{where}: required field '{key}' is missing or empty")
    return value


def _inject_execution_resources(execution_config, resource_overrides):
    """Merge deploy-time resolved resource values into the pipeline executionConfig, per its
    executionType. resource_overrides is the flat map the CR receives, e.g.
    {lambdaName, sqsQueueUrl, eventBridgeBusArn, eventBridgeSource, eventBridgeDetailType,
    deadlineFarmId, deadlineQueueId, ...}. Returns a NEW dict; inputs are not mutated. A value only
    overrides when non-empty, so a schema that hard-codes a resource (external self-registration) is
    left intact when no override is supplied."""
    config = dict(execution_config or {})
    overrides = resource_overrides or {}
    exec_type = config.get("executionType", "Lambda")

    def _sub(block_key):
        block = dict(config.get(block_key) or {})
        return block

    if exec_type == "Lambda":
        lam = _sub("lambda")
        if overrides.get("lambdaName"):
            lam["resourceId"] = overrides["lambdaName"]
        config["lambda"] = lam
    elif exec_type == "SQS":
        sqs = _sub("sqs")
        if overrides.get("sqsQueueUrl"):
            sqs["queueUrl"] = overrides["sqsQueueUrl"]
        config["sqs"] = sqs
    elif exec_type == "EventBridge":
        eb = _sub("eventBridge")
        if overrides.get("eventBridgeBusArn"):
            eb["busArn"] = overrides["eventBridgeBusArn"]
        if overrides.get("eventBridgeSource"):
            eb["source"] = overrides["eventBridgeSource"]
        if overrides.get("eventBridgeDetailType"):
            eb["detailType"] = overrides["eventBridgeDetailType"]
        config["eventBridge"] = eb
    elif exec_type == "DeadlineCloud":
        dc = _sub("deadlineCloud")
        for src, dst in (("deadlineFarmId", "farmId"), ("deadlineQueueId", "queueId"),
                         ("deadlineStorageProfileId", "storageProfileId")):
            if overrides.get(src):
                dc[dst] = overrides[src]
        config["deadlineCloud"] = dc
    return config


def _fill_system_config_defaults(declared, defaults):
    """A bundle's partial systemConfig, completed with the builder defaults for every field it omits.

    Both the pipeline and workflow records store systemConfig WHOLESALE — create/update replace the
    field rather than merging it — so a bundle that declares only some fields would otherwise persist a
    partial block, and adding a new systemConfig field later would silently change the stored meaning
    of every existing bundle. Filling here keeps a bundle's declaration authoritative for what it says
    while making omissions explicitly the documented defaults, so a new field is inert for bundles
    written before it existed.

    Nested boolean maps (`assetScope`, `metadataInputs`) and `inputFileFilters` are filled key-by-key
    for the same reason: a partial `{"wholeAsset": true}` should not drop the other three scope rules.
    A declared `assetScope` shorthand suppresses its canonical counterpart's default, since both
    spellings mean the same thing and supplying both would let the default contradict the declaration.

    `declared` is not mutated; a `None`/empty declaration yields the defaults unchanged.
    """
    merged = dict(defaults)
    for key, declared_value in (declared or {}).items():
        merged[key] = declared_value
    for key, default_value in defaults.items():
        declared_value = (declared or {}).get(key)
        if not isinstance(default_value, dict) or not isinstance(declared_value, dict):
            continue
        nested = dict(default_value)
        if key == "assetScope":
            for shorthand, canonical in _ASSET_SCOPE_SHORTHAND.items():
                if shorthand in declared_value:
                    nested.pop(canonical, None)
        nested.update(declared_value)
        merged[key] = nested
    return merged


def _pipeline_system_config(pipeline):
    """The pipeline systemConfig to store: what the bundle declares, defaults for the rest."""
    from common.workflows.pipelineRecords import build_pipeline_system_config
    return _fill_system_config_defaults(
        pipeline.get("systemConfig") or {}, build_pipeline_system_config())


def _workflow_system_config(workflow):
    """The workflow systemConfig to store: what the bundle declares, defaults for the rest."""
    from common.workflows.workflowRecords import build_workflow_system_config
    return _fill_system_config_defaults(
        workflow.get("systemConfig") or {}, build_workflow_system_config())


def _pipeline_create_body(pipeline, database_id, pipeline_id, execution_config):
    return {
        "databaseId": database_id,
        "pipelineId": pipeline_id,
        "pipelineName": pipeline.get("pipelineName") or pipeline_id,
        "category": pipeline.get("category", "") or "",
        "description": pipeline.get("description", "") or "",
        "executionConfig": execution_config,
        "systemConfig": _pipeline_system_config(pipeline),
        "enabled": pipeline.get("enabled", True),
    }


def _pipeline_update_body(pipeline, execution_config):
    # Update omits ids (path-scoped) and always re-enables (re-register unarchive/enable, decision 12).
    return {
        "pipelineName": pipeline.get("pipelineName"),
        "category": pipeline.get("category", "") or "",
        "description": pipeline.get("description", "") or "",
        "executionConfig": execution_config,
        "systemConfig": _pipeline_system_config(pipeline),
        "enabled": True,
    }


def _template_create_body(template, template_id):
    return {
        "templateId": template_id,
        "templateName": template.get("templateName") or template_id,
        "description": template.get("description", "") or "",
        "configFormat": template.get("configFormat", "json") or "json",
        "configBody": template.get("configBody", "") or "",
        "webFormJson": template.get("webFormJson", "") or "",
        "allowCustomEdit": bool(template.get("allowCustomEdit", False)),
        "inputInstructions": template.get("inputInstructions", "") or "",
        "overrides": template.get("overrides", {}) or {},
        "tagSchema": template.get("tagSchema"),
        # The pipeline's default template is auto-selected at execute time, which is what lets a
        # requireTemplate pipeline run without the caller naming a template.
        "isDefault": bool(template.get("isDefault", False)),
    }


def _workflow_create_body(workflow, database_id, workflow_id, pipeline_database_id, pipeline_id):
    # A built-in workflow references its one pipeline by default; an explicit specifiedPipelines list
    # in the schema (multi-pipeline built-ins) wins.
    specified = workflow.get("specifiedPipelines")
    if not specified:
        specified = [{"pipelineDatabaseId": pipeline_database_id, "pipelineId": pipeline_id}]
    return {
        "databaseId": database_id,
        "workflowId": workflow_id,
        "workflowName": workflow.get("workflowName") or workflow_id,
        "category": workflow.get("category", "") or "",
        "description": workflow.get("description", "") or "",
        "specifiedPipelines": specified,
        "systemConfig": _workflow_system_config(workflow),
        "subDashboardUrl": workflow.get("subDashboardUrl", "") or "",
    }


def _workflow_update_body(workflow, pipeline_database_id, pipeline_id):
    specified = workflow.get("specifiedPipelines")
    if not specified:
        specified = [{"pipelineDatabaseId": pipeline_database_id, "pipelineId": pipeline_id}]
    body = {
        "workflowName": workflow.get("workflowName"),
        "category": workflow.get("category", "") or "",
        "description": workflow.get("description", "") or "",
        "specifiedPipelines": specified,
        "subDashboardUrl": workflow.get("subDashboardUrl", "") or "",
        "enabled": True,
    }
    # systemConfig is always sent FILLED — the bundle's declaration plus the defaults for whatever it
    # omits. Sending the raw `{}` of a bundle that declares nothing is what once blanked the stored
    # block on every redeploy (update_workflow stores the field verbatim), and merely OMITTING it
    # instead left rows already blanked that way stuck empty, since nothing ever rewrote them. Sending
    # the completed block makes registration idempotent and self-healing: after any deploy the stored
    # row equals declaration + current defaults, which is what keeps a newly-added field from changing
    # an existing bundle's meaning. A built-in's systemConfig is therefore deploy-owned; an operator
    # edit to one is re-asserted on the next deploy.
    body["systemConfig"] = _workflow_system_config(workflow)
    return body


def _trigger_body(trigger, trigger_enabled_override=None):
    # `enabled` comes from the schema by default; a deploy-time override (the pipeline's
    # autoRegisterAutoTriggerOnFileUpload config) wins when supplied, so a built-in ships its trigger
    # definition (filters + default templates) but only auto-fires when the deployment opts in.
    enabled = trigger.get("enabled", True)
    if trigger_enabled_override is not None:
        enabled = bool(trigger_enabled_override)
    return {
        "inputFileFilters": trigger.get("inputFileFilters", {}) or {},
        "defaultTemplateIds": trigger.get("defaultTemplateIds", {}) or {},
        "enabled": enabled,
    }


def resolve_ids(bundle, id_overrides=None):
    """Resolve the effective pipeline/workflow/database ids for a bundle, applying deploy-time id
    overrides (built-ins keep known ids for external references). Returns
    (pipeline_database_id, pipeline_id, workflow_database_id, workflow_id). Built-ins are GLOBAL by
    default; a bundle/override may set a specific database. Raises VamsSchemaError when a required id
    cannot be resolved (no schema value and no override)."""
    overrides = id_overrides or {}
    pipeline = bundle.get("pipeline") or {}
    workflow = bundle.get("workflow") or {}

    pipeline_database_id = (overrides.get("pipelineDatabaseId")
                            or pipeline.get("databaseId") or GLOBAL_DATABASE)
    pipeline_id = overrides.get("pipelineId") or pipeline.get("pipelineId")
    if not pipeline_id:
        raise VamsSchemaError("pipeline: a pipelineId is required (schema value or id override)")

    workflow_database_id = (overrides.get("workflowDatabaseId")
                            or workflow.get("databaseId") or GLOBAL_DATABASE)
    workflow_id = overrides.get("workflowId") or workflow.get("workflowId") or pipeline_id
    return pipeline_database_id, pipeline_id, workflow_database_id, workflow_id


def build_import_requests(bundle, resource_overrides=None, id_overrides=None,
                          trigger_enabled_override=None):
    """Turn a parsed vamsSchema bundle into the ordered list of cross-call request descriptors.

    Order matters: pipeline first (create/update), then its templates (a template references its
    owning pipeline), then the workflow (references the pipeline), then the workflow's triggers
    (reference the workflow). Each descriptor is
      {target, method, path, pathParameters, createBody, updateBody?, existsPath?}
    where the lambda probes ``existsPath`` (GET) to choose create (POST) vs update (PUT). Templates
    and triggers use PUT-idempotent set semantics where the service supports it; pipelines/workflows
    use create-then-update.

    ``trigger_enabled_override`` (deploy-time) forces every trigger's ``enabled`` flag when not None,
    so a built-in ships its trigger definition but only auto-fires when the deployment opts in (the
    pipeline's autoRegisterAutoTriggerOnFileUpload config). None leaves the schema value intact.

    Only ``pipeline`` is required. ``workflow``/``templates`` are optional (minimal-required
    ingestion). Raises VamsSchemaError on a structurally invalid bundle."""
    if not isinstance(bundle, dict):
        raise VamsSchemaError("vamsSchema bundle must be a JSON object")
    pipeline = bundle.get("pipeline")
    if not pipeline:
        raise VamsSchemaError("vamsSchema bundle: 'pipeline' is required")
    _require(pipeline, "pipelineName", "pipeline")

    pdb, pid, wdb, wid = resolve_ids(bundle, id_overrides)
    execution_config = _inject_execution_resources(
        pipeline.get("executionConfig", {}), resource_overrides)

    requests = []

    # 1) Pipeline (create when absent, else update + re-enable).
    requests.append({
        "target": TARGET_PIPELINE_SERVICE,
        "kind": "pipeline",
        "id": pid,
        "existsPath": f"/database/{pdb}/pipelines/{pid}",
        "existsPathParameters": {"databaseId": pdb, "pipelineId": pid},
        "createPath": f"/database/{pdb}/pipelines",
        "createPathParameters": {"databaseId": pdb},
        "updatePath": f"/database/{pdb}/pipelines/{pid}",
        "updatePathParameters": {"databaseId": pdb, "pipelineId": pid},
        "createBody": _pipeline_create_body(pipeline, pdb, pid, execution_config),
        "updateBody": _pipeline_update_body(pipeline, execution_config),
    })

    # 2) Templates (optional). Each is scoped to the owning pipeline; the template service is
    #    create(POST)/update(PUT) keyed on templateId under the pipeline.
    templates = bundle.get("templates") or []

    # A requireTemplate pipeline cannot execute unless the caller names a template or the pipeline
    # has a default to auto-select. When the bundle ships exactly one template and marks none as
    # default, that single template is the only possible choice, so promote it.
    if (
        len(templates) == 1
        and (pipeline.get("systemConfig") or {}).get("requireTemplate")
        and not any(t.get("isDefault") for t in templates)
    ):
        templates = [{**templates[0], "isDefault": True}]

    for template in templates:
        _require(template, "templateName", "template")
        tpl_id = template.get("templateId")
        if not tpl_id:
            raise VamsSchemaError("template: a templateId is required for a built-in template")
        requests.append({
            "target": TARGET_TEMPLATE_SERVICE,
            "kind": "template",
            "id": tpl_id,
            "existsPath": f"/database/{pdb}/pipelines/{pid}/templates/{tpl_id}",
            "existsPathParameters": {"databaseId": pdb, "pipelineId": pid, "templateId": tpl_id},
            "createPath": f"/database/{pdb}/pipelines/{pid}/templates",
            "createPathParameters": {"databaseId": pdb, "pipelineId": pid},
            "updatePath": f"/database/{pdb}/pipelines/{pid}/templates/{tpl_id}",
            "updatePathParameters": {"databaseId": pdb, "pipelineId": pid, "templateId": tpl_id},
            "createBody": _template_create_body(template, tpl_id),
            "updateBody": _template_create_body(template, tpl_id),
        })

    # 3) Workflow (optional but usual — one built-in workflow per pipeline).
    workflow = bundle.get("workflow")
    if workflow:
        _require(workflow, "workflowName", "workflow")
        requests.append({
            "target": TARGET_WORKFLOW_SERVICE,
            "kind": "workflow",
            "id": wid,
            "existsPath": f"/database/{wdb}/workflows/{wid}",
            "existsPathParameters": {"databaseId": wdb, "workflowId": wid},
            "createPath": f"/database/{wdb}/workflows",
            "createPathParameters": {"databaseId": wdb},
            "updatePath": f"/database/{wdb}/workflows/{wid}",
            "updatePathParameters": {"databaseId": wdb, "workflowId": wid},
            "createBody": _workflow_create_body(workflow, wdb, wid, pdb, pid),
            "updateBody": _workflow_update_body(workflow, pdb, pid),
        })

        # 4) Triggers (optional; PUT-idempotent set on the workflow).
        for trigger in workflow.get("triggers") or []:
            trigger_type = _require(trigger, "triggerType", "trigger")
            requests.append({
                "target": TARGET_TRIGGER_SERVICE,
                "kind": "trigger",
                "id": trigger_type,
                "setPath": f"/database/{wdb}/workflows/{wid}/triggers/{trigger_type}",
                "setPathParameters": {"databaseId": wdb, "workflowId": wid, "triggerType": trigger_type},
                "setBody": _trigger_body(trigger, trigger_enabled_override),
            })

    return requests


def collect_ids(bundle, id_overrides=None):
    """Return {pipelineDatabaseId, pipelineId, workflowDatabaseId, workflowId} for a bundle — used by
    the CR to stamp the CloudFormation physical id and to drive DELETE (archive) of the built-in."""
    pdb, pid, wdb, wid = resolve_ids(bundle, id_overrides)
    return {
        "pipelineDatabaseId": pdb, "pipelineId": pid,
        "workflowDatabaseId": wdb, "workflowId": wid,
    }
