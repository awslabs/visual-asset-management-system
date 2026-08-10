/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

export type ExecutionType = "Lambda" | "SQS" | "EventBridge" | "DeadlineCloud";
export type WaitForCallback = "Enabled" | "Disabled";
export type InputFileArity = "none" | "one" | "multi";
export type ConcurrencyRestriction = "none" | "perAsset" | "perInputFile";
export type OutputLocationType = "asset" | "none";
export type ConfigFormat = "json" | "yaml" | "openjd" | "xml" | "raw";
export type TagType = "string" | "integer" | "number" | "boolean" | "string-list" | "enum";
export type ExecutionStatus =
    | "NEW"
    | "RUNNING"
    | "SUCCEEDED"
    | "FAILED"
    | "ABORTED"
    | "TIMED_OUT"
    // Legacy terminal value carried by migrated execution rows; treated as a success.
    | "COMPLETE";

export interface PipelineExecutionConfig {
    executionType: ExecutionType;
    waitForCallback?: WaitForCallback;
    taskTimeout?: string;
    taskHeartbeatTimeout?: string;
    lambda?: { resourceId?: string };
    sqs?: { queueUrl?: string };
    eventBridge?: { busArn?: string; source?: string; detailType?: string };
    deadlineCloud?: {
        farmId?: string;
        queueId?: string;
        storageProfileId?: string;
        priority?: number;
        maxRetriesPerTask?: number;
        maxFailedTasksCount?: number;
        templateType?: string;
        template?: string;
    };
}

export interface PipelineSystemConfig {
    inputFileArity?: InputFileArity;
    assetScope?: Record<string, boolean>;
    metadataInputs?: Record<string, boolean>;
    requireTemplate?: boolean;
    allowCustomTemplateOverride?: boolean;
    auxPreviewPipelineSuffix?: string;
    inputFileFilters?: { allow?: string[]; exclude?: string[] };
}

export interface Pipeline {
    databaseId: string;
    pipelineId: string;
    pipelineName: string;
    category?: string;
    description?: string;
    enabled?: boolean;
    archived?: boolean;
    executionConfig: PipelineExecutionConfig;
    systemConfig?: PipelineSystemConfig;
    /** Count of saved templates for this pipeline (present on list + details responses). */
    templateCount?: number;
    /** Present on the single-pipeline details response. */
    templates?: Array<Record<string, any>>;
}

/** Create body: pipelineId is null when the backend generates it. */
export type PipelineCreateRequest = Omit<Pipeline, "pipelineId"> & { pipelineId?: string | null };

export interface TagSchemaField {
    tagKey: string;
    type: TagType;
    required?: boolean;
    default?: any;
    label?: string;
    description?: string;
    enumValues?: any[];
}

export interface Template {
    // Create bodies carry databaseId; template responses key the owning database as
    // pipelineDatabaseId.
    databaseId?: string;
    pipelineDatabaseId?: string;
    pipelineId: string;
    templateId: string;
    templateName: string;
    description?: string;
    configFormat: ConfigFormat;
    configBody?: string;
    webFormJson?: string;
    allowCustomEdit?: boolean;
    inputInstructions?: string;
    overrides?: Record<string, any>;
    // At most one template per pipeline is the default (auto-selected first in pickers, and by the
    // backend when a require-template pipeline is executed without a templateId).
    isDefault?: boolean;
    tagSchema?: TagSchemaField[];
}

export interface SpecifiedPipelineRef {
    pipelineId: string;
    pipelineDatabaseId?: string;
    jobName?: string;
    defaultTemplateId?: string;
}

export interface WorkflowSystemConfig {
    inputFileArity?: InputFileArity;
    assetScope?: Record<string, boolean>;
    metadataInputs?: Record<string, boolean>;
    inputFileFilters?: { allow?: string[]; exclude?: string[] };
    concurrencyRestriction?: ConcurrencyRestriction;
    outputTarget?: { locationType?: OutputLocationType; allowOverride?: boolean };
    // Whether a file written by ANOTHER workflow may fire this workflow's triggers. A workflow never
    // fires on output it wrote itself, whatever this is set to, so an A->A loop cannot be enabled.
    allowWorkflowTriggerChaining?: boolean;
    // Output path prefix used when an execution supplies none. Stored UNRESOLVED, so its {{tag}}
    // placeholders are substituted per run (e.g. "/{{jobName}}/" gives each run its own folder).
    defaultOutputFileBaseExecutionPathExtension?: string;
}

export interface Workflow {
    databaseId: string;
    workflowId: string;
    workflowName: string;
    category?: string;
    description?: string;
    subDashboardUrl?: string;
    enabled?: boolean;
    archived?: boolean;
    specifiedPipelines: SpecifiedPipelineRef[];
    systemConfig?: WorkflowSystemConfig;
    workflow_arn?: string;
    aslSchemaVersion?: string;
    warnings?: string[];
    // Total executions for this workflow; present on list responses (computed server-side per page).
    executionCount?: number;
    // How many triggers the workflow has and how many are ENABLED; present on list responses.
    // Both are reported because they differ when a trigger exists but is switched off — the state
    // that explains a workflow which looks configured yet never fires.
    triggerCount?: number;
    triggersEnabledCount?: number;
}

/** Create body: workflowId is null when the backend generates it. */
export type WorkflowCreateRequest = Omit<Workflow, "workflowId"> & { workflowId?: string | null };

/**
 * The trigger types VAMS can configure. `fileUpload` is the only one implemented today; the editor is
 * driven by this list rather than by a hard-coded type, so adding one here surfaces it in the UI.
 */
export const TRIGGER_TYPES = [
    {
        type: "fileUpload",
        label: "File upload",
        description: "Runs the workflow when an uploaded file matches this trigger's filters.",
    },
] as const;

export type TriggerBaseType = (typeof TRIGGER_TYPES)[number]["type"];

export interface WorkflowTrigger {
    /**
     * The trigger's KEY, and what the trigger endpoints take: the bare type for a workflow's first
     * trigger of that type, or `type#triggerId` for an additional one. A workflow may carry several
     * triggers of one type, each with its own filters and default templates.
     */
    triggerType: string;
    /** The plain type, for grouping and display. Absent on a row written before it was reported. */
    triggerBaseType?: TriggerBaseType | string;
    /** Distinguishes several triggers of one type; empty for a workflow's first trigger of a type. */
    triggerId?: string;
    enabled?: boolean;
    inputFileFilters?: { allow?: string[]; exclude?: string[] };
    defaultTemplateIds?: Record<string, string>;
}

/** A trigger's plain type, falling back to splitting the key for a row that does not report it. */
export function triggerBaseTypeOf(trigger: WorkflowTrigger): string {
    return trigger.triggerBaseType || (trigger.triggerType || "").split("#")[0];
}

export interface ExecuteInputFile {
    databaseId: string;
    assetId: string;
    relativeFileKey: string;
    versionId?: string;
}

/**
 * One asset named purely as a metadata source. It carries no file key — a metadata source is an
 * entity, never a file — and is not an input file, so it takes no part in arity, input-file filters,
 * or output-target resolution.
 */
export interface MetadataSourceAsset {
    databaseId: string;
    assetId: string;
}

export interface PipelineExecutionParameters {
    templateId?: string;
    templateTags?: { key: string; value: any }[];
    customTemplateOverride?: string;
}

export interface ExecuteRequest {
    inputFiles: ExecuteInputFile[];
    // Metadata sources: entities whose stored metadata is captured into the run's metadata payload.
    // ONE concrete database ("GLOBAL" is rejected server-side) and any number of source assets.
    metadataSourceDatabaseId?: string;
    metadataSourceAssets?: MetadataSourceAsset[];
    outputAssetId?: string;
    outputDatabaseId?: string;
    // Optional base path (under the output asset) output files are written beneath; supports
    // dynamic-tag placeholders resolved at launch. Omitted = asset root.
    outputFileBaseExecutionPathExtension?: string;
    pipelineExecutionParameters?: Record<string, PipelineExecutionParameters>;
    executionGroupId?: string;
    triggerType?: "manual" | "fileUpload";
}

export interface Execution {
    workflowExecutionId: string;
    workflowId: string;
    workflowDatabaseId: string;
    executionStatus: ExecutionStatus;
    triggeredByUserId?: string;
    triggerType?: string;
    executionStartDate?: string;
    executionStopDate?: string;
    executionGroupId?: string;
    executionError?: string;
    // Output target of the run: "none" (results-only) or "asset" with the destination ids. Present on
    // the global list rows too — the backend projects them from the execution's configuration row,
    // which it already reads to authorize output-asset visibility.
    outputLocationType?: string;
    outputAssetId?: string;
    outputDatabaseId?: string;
}

export interface ExecutionDetail extends Execution {
    /**
     * The workflow's systemConfig, read LIVE from the workflow record — so it reflects the workflow as
     * it stands now, not necessarily as it was when this execution ran. Per-step settings below ARE the
     * recorded snapshot; a settings view must label the difference.
     */
    workflowSystemConfig?: Record<string, any>;
    workflowName?: string;
    workflowDescription?: string;
    pipelines?: any[];
    inputFiles?: any[];
    inputMetadata?: any[];
    /** A metadata-source database's own metadata — its own collection because it belongs to no asset. */
    inputDatabaseMetadata?: any[];
    outputs?: { files?: any[]; metadata?: any[]; results?: any[] };
    /**
     * Names of the collections the server returned partial, because the run exceeded the per-collection
     * bound: "inputFiles", "inputMetadata", "inputDatabaseMetadata", "outputs.files",
     * "outputs.metadata", "outputs.results". A named section holds fewer rows than the run produced,
     * and there is no token to fetch the rest — so it must be shown as partial, never as the full set.
     */
    truncatedCollections?: string[];
    // outputLocationType / outputAssetId / outputDatabaseId are inherited from Execution.
    outputFileBaseExecutionPathExtension?: string;
}

export interface ExecuteResponse {
    executionId?: string;
    executionGroupId?: string;
    warnings?: string[];
}
