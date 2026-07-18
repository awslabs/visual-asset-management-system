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
export type ExecutionStatus = "NEW" | "RUNNING" | "SUCCEEDED" | "FAILED" | "ABORTED" | "TIMED_OUT" | "COMPLETE";

export interface PipelineExecutionConfig {
    executionType: ExecutionType;
    waitForCallback?: WaitForCallback;
    taskTimeout?: string; taskHeartbeatTimeout?: string;
    lambda?: { resourceId?: string };
    sqs?: { queueUrl?: string };
    eventBridge?: { busArn?: string; source?: string; detailType?: string };
    deadlineCloud?: { farmId?: string; queueId?: string; storageProfileId?: string; priority?: number; maxRetriesPerTask?: number; maxFailedTasksCount?: number; templateType?: string };
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
    databaseId: string; pipelineId: string; pipelineName: string;
    category?: string; description?: string; enabled?: boolean; archived?: boolean;
    executionConfig: PipelineExecutionConfig; systemConfig?: PipelineSystemConfig;
}

export interface TagSchemaField {
    tagKey: string; type: TagType; required?: boolean; default?: any;
    label?: string; description?: string; enumValues?: any[];
}

export interface Template {
    databaseId: string; pipelineId: string; templateId: string; templateName: string;
    description?: string; configFormat: ConfigFormat; configBody?: string; webFormJson?: string;
    allowCustomEdit?: boolean; inputInstructions?: string;
    overrides?: Record<string, any>; tagSchema?: TagSchemaField[];
}

export interface SpecifiedPipelineRef { pipelineId: string; pipelineDatabaseId?: string; jobName?: string; defaultTemplateId?: string; }

export interface WorkflowSystemConfig {
    inputFileArity?: InputFileArity; assetScope?: Record<string, boolean>;
    metadataInputs?: Record<string, boolean>; inputFileFilters?: { allow?: string[]; exclude?: string[] };
    concurrencyRestriction?: ConcurrencyRestriction;
    outputTarget?: { locationType?: OutputLocationType; allowOverride?: boolean };
}

export interface Workflow {
    databaseId: string; workflowId: string; workflowName: string;
    category?: string; description?: string; subDashboardUrl?: string; enabled?: boolean; archived?: boolean;
    specifiedPipelines: SpecifiedPipelineRef[]; systemConfig?: WorkflowSystemConfig;
    workflow_arn?: string; aslSchemaVersion?: string; warnings?: string[];
}

export interface WorkflowTrigger { triggerType: "fileUpload"; enabled?: boolean; inputFileFilters?: { allow?: string[]; exclude?: string[] }; defaultTemplateIds?: Record<string, string>; }

export interface ExecuteInputFile { databaseId: string; assetId: string; relativeFileKey: string; versionId?: string; }

export interface PipelineExecutionParameters { templateId?: string; templateTags?: { key: string; value: any }[]; customTemplateOverride?: string; }

export interface ExecuteRequest {
    inputFiles: ExecuteInputFile[]; outputAssetId?: string; outputDatabaseId?: string;
    pipelineExecutionParameters?: Record<string, PipelineExecutionParameters>;
    executionGroupId?: string; triggerType?: "manual" | "fileUpload";
}

export interface Execution {
    workflowExecutionId: string; workflowId: string; workflowDatabaseId: string;
    executionStatus: ExecutionStatus; triggeredByUserId?: string; triggerType?: string;
    executionStartDate?: string; executionStopDate?: string; executionGroupId?: string;
    executionError?: string;
}

export interface ExecutionDetail extends Execution {
    pipelines?: any[]; inputFiles?: any[]; outputs?: { files?: any[]; metadata?: any[]; results?: any[] };
    truncatedCollections?: string[];
}
