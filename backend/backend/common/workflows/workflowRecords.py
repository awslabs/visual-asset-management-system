# Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

"""Pure helpers for the workflow V2 storage data model.

This module has NO AWS or environment dependencies so it can be imported and unit-tested in
isolation, mirroring common/workflows/executionRecords.py. It builds:
  - WorkflowStorageTableV2 rows:          PK databaseId, SK workflowId
  - WorkflowTriggersStorageTable rows:    PK workflowDatabaseId:workflowId, SK triggerType

Each specifiedPipelines ref stores pipelineDatabaseId + pipelineId together so the composite
pipeline key resolves unambiguously.
"""

from common.workflows.executionRecords import METADATA_INPUT_DEFAULTS, iso_now, workflow_composite_key

WORKFLOW_SCHEMA_VERSION = 1

# Constant PK for the global (cross-database) workflow-list GSI (query, not table scan).
ALL_WORKFLOWS_LIST_PARTITION = "workflow"

# Trigger types (only fileUpload implemented now; typed for extensibility).
TRIGGER_TYPES = ("fileUpload",)


def build_workflow_system_config(
    input_file_arity="one",
    asset_scope=None,
    metadata_inputs=None,
    input_file_filters=None,
    concurrency_restriction="none",
    output_target=None,
    allow_workflow_trigger_chaining=False,
    default_output_file_base_execution_path_extension="",
):
    """Workflow system-config block. Defaults match the create-when-unspecified defaults.

    - input_file_arity: none | one | multi
    - concurrency_restriction: none | perAsset | perInputFile
    - output_target: {locationType: asset, allowOverride: bool}
    - allow_workflow_trigger_chaining: whether ANOTHER workflow's output may fire this workflow's
      triggers. Self-triggering is always blocked regardless, so an A->A loop cannot be enabled.
    - default_output_file_base_execution_path_extension: output path prefix used when an execute
      request supplies none. Stored UNRESOLVED so its {{tag}} placeholders resolve per run.
    """
    return {
        "inputFileArity": input_file_arity or "one",
        "assetScope": asset_scope or {
            "crossAssetAllowed": False,
            "singleAssetOnly": True,
            "wholeAssetAllowed": False,
            "folderAllowed": False,
        },
        "metadataInputs": metadata_inputs or dict(METADATA_INPUT_DEFAULTS),
        "inputFileFilters": input_file_filters or {"allow": [], "exclude": []},
        "concurrencyRestriction": concurrency_restriction or "none",
        "outputTarget": output_target or {"locationType": "asset", "allowOverride": False},
        "allowWorkflowTriggerChaining": bool(allow_workflow_trigger_chaining),
        "defaultOutputFileBaseExecutionPathExtension":
            default_output_file_base_execution_path_extension or "",
    }


def build_specified_pipeline_ref(pipeline_database_id, pipeline_id, job_name="", default_template_id=""):
    """One ordered pipeline reference in a workflow's specifiedPipelines snapshot.

    Stores pipelineDatabaseId + pipelineId together (composite pipeline key) so the reference is
    unambiguous even when ids are overridden across databases. `default_template_id` is the fallback
    template this pipeline uses when a run supplies no per-pipeline templateId (empty when none).
    """
    return {
        "pipelineDatabaseId": pipeline_database_id,
        "pipelineId": pipeline_id,
        "pipelineDatabaseId:pipelineId": f"{pipeline_database_id}:{pipeline_id}",
        "jobName": job_name or "",
        "defaultTemplateId": default_template_id or "",
    }


def build_workflow_record(
    database_id, workflow_id, workflow_name, category, description,
    specified_pipelines, system_config,
    workflow_arn="", asl_schema_version="", sub_dashboard_url="",
    enabled=True, archived=False, created_by="", modified_by="",
    date_created="", date_modified="", job_names=None,
):
    """WorkflowStorageTableV2 row (database-scoped: PK databaseId, SK workflowId).

    job_names are the per-pipeline job names the ASL generator baked into the execution output S3
    paths (workflow order). The execute handler reads them to reconstruct the identical output
    prefixes — the parity contract mirrored from V1's workflow record jobNames.
    """
    now = iso_now()
    return {
        "databaseId": database_id,  # PK
        "workflowId": workflow_id,  # SK
        "databaseId:category": f"{database_id}:{category or ''}",  # GSI PK
        "allListPartition": ALL_WORKFLOWS_LIST_PARTITION,  # by-date GSI PK (global list)
        "workflowName": workflow_name or "",
        "category": category or "",
        "description": description or "",
        "workflow_arn": workflow_arn or "",
        "aslSchemaVersion": asl_schema_version or "",
        "jobNames": job_names or [],
        "specifiedPipelines": specified_pipelines or [],
        "systemConfig": system_config or build_workflow_system_config(),
        "subDashboardUrl": sub_dashboard_url or "",
        "enabled": bool(enabled),
        "archived": bool(archived),
        "dateCreated": date_created or now,
        "dateModified": date_modified or now,
        "createdBy": created_by or "",
        "modifiedBy": modified_by or "",
        "schemaVersion": WORKFLOW_SCHEMA_VERSION,
    }


def trigger_sort_key(trigger_type, trigger_id=""):
    """A trigger row's sort key: the bare type, or 'type#triggerId' for an additional trigger.

    A workflow may carry SEVERAL triggers of one type, each with its own input-file filters and default
    templates, so the type alone no longer identifies a row. The FIRST trigger of a type keeps the bare
    type as its key — which is exactly what every row written before multiple triggers existed holds — so
    those rows stay addressable and keep firing once. Additional triggers suffix an id.

    The sort-key ATTRIBUTE stays named `triggerType`: it is the table's sort key and DynamoDB cannot
    rename a key attribute in place, so the suffix lives in its value. The bare type is carried
    separately in `triggerBaseType` for the by-type GSI, whose query is an exact match and would
    otherwise never find a suffixed row.
    """
    return f"{trigger_type}#{trigger_id}" if trigger_id else trigger_type


def split_trigger_sort_key(sort_key):
    """(triggerType, triggerId) from a sort key. triggerId is '' for a bare-type key."""
    trigger_type, _, trigger_id = (sort_key or "").partition("#")
    return trigger_type, trigger_id


def build_trigger_record(
    workflow_database_id, workflow_id, trigger_type, trigger_config,
    enabled=True, date_created="", date_modified="", trigger_id="",
):
    """WorkflowTriggersStorageTable row (PK workflowDatabaseId:workflowId, SK triggerType).

    trigger_config for fileUpload: {inputFileFilters: {allow, exclude},
    defaultTemplateIds: {"<pipelineDatabaseId>:<pipelineId>": templateId}}.

    `trigger_id` distinguishes several triggers of one type; empty for the first of a type, whose sort
    key is the bare type (see trigger_sort_key).
    """
    now = iso_now()
    return {
        "workflowDatabaseId:workflowId": workflow_composite_key(workflow_database_id, workflow_id),  # PK
        "triggerType": trigger_sort_key(trigger_type, trigger_id),  # SK ('type' or 'type#id')
        "triggerBaseType": trigger_type,  # GSI PK — always the BARE type, never suffixed
        "triggerId": trigger_id or "",
        "workflowDatabaseId": workflow_database_id,
        "workflowId": workflow_id,
        "triggerConfig": trigger_config or {},
        "enabled": bool(enabled),
        "dateCreated": date_created or now,
        "dateModified": date_modified or now,
    }


def build_file_upload_trigger_config(input_file_filters=None, default_template_ids=None):
    """triggerConfig for a fileUpload trigger."""
    return {
        "inputFileFilters": input_file_filters or {"allow": [], "exclude": []},
        "defaultTemplateIds": default_template_ids or {},
    }


