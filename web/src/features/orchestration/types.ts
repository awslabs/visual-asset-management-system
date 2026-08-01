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
}

/** Create body: workflowId is null when the backend generates it. */
export type WorkflowCreateRequest = Omit<Workflow, "workflowId"> & { workflowId?: string | null };

export interface WorkflowTrigger {
    triggerType: "fileUpload";
    enabled?: boolean;
    inputFileFilters?: { allow?: string[]; exclude?: string[] };
    defaultTemplateIds?: Record<string, string>;
}

export interface ExecuteInputFile {
    databaseId: string;
    assetId: string;
    relativeFileKey: string;
    versionId?: string;
}

export interface PipelineExecutionParameters {
    templateId?: string;
    templateTags?: { key: string; value: any }[];
    customTemplateOverride?: string;
}

export interface ExecuteRequest {
    inputFiles: ExecuteInputFile[];
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
    workflowName?: string;
    workflowDescription?: string;
    pipelines?: any[];
    inputFiles?: any[];
    inputMetadata?: any[];
    outputs?: { files?: any[]; metadata?: any[]; results?: any[] };
    truncatedCollections?: string[];
    // outputLocationType / outputAssetId / outputDatabaseId are inherited from Execution.
    outputFileBaseExecutionPathExtension?: string;
}

export interface ExecuteResponse {
    executionId?: string;
    executionGroupId?: string;
    warnings?: string[];
}
