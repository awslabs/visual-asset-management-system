/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "./apiClient";

// --- Schema body (vams-rules-v1) ---

export const VAMS_RULES_V1_FORMAT = "vams-rules-v1";

export type ComplianceEnforcementLevel = "quarantine" | "warn" | "inform";

export type ComplianceRuleType = "pipeline" | "metadata" | "relationship";

/**
 * Where a pipeline rule's workflow lives and which pipeline inside it produces the checked
 * output. The workflow and pipeline database ids may each be a database id or GLOBAL.
 */
export interface CompliancePipelineRef {
    databaseId: string;
    workflowId: string;
    pipelineDatabaseId: string;
    pipelineId: string;
    templateId?: string;
}

export interface ComplianceTolerance {
    operator: "lte" | "gte" | "eq" | "between";
    value?: number;
    min?: number;
    max?: number;
    epsilon?: number;
}

export interface CompliancePipelineCheck {
    name: string;
    description?: string;
    outputField: string;
    tolerance: ComplianceTolerance;
}

/**
 * How a pipeline rule selects the asset files handed to the workflow at launch.
 * `matching` (the default) lists the asset's files and keeps those passing the workflow's and
 * pipeline's own input filters plus the rule's `filter` globs; `wholeAsset` sends the asset root
 * (only where the workflow permits it); `explicit` sends exactly the listed asset-relative keys.
 */
export type CompliancePipelineInputFilesMode = "matching" | "wholeAsset" | "explicit";

export interface CompliancePipelineInputFiles {
    mode: CompliancePipelineInputFilesMode;
    /** Glob allow list applied after the workflow's and pipeline's filters (`matching` only). */
    filter?: string[];
    /** Asset-relative `/path` keys (`explicit` only, at least one). */
    keys?: string[];
}

export const PIPELINE_INPUT_FILES_MODES: CompliancePipelineInputFilesMode[] = [
    "matching",
    "wholeAsset",
    "explicit",
];

export const DEFAULT_PIPELINE_INPUT_FILES: CompliancePipelineInputFiles = { mode: "matching" };

export interface CompliancePipelineRule {
    ruleType: "pipeline";
    enforcement: ComplianceEnforcementLevel;
    pipelineRef: CompliancePipelineRef;
    /** Absent means `{ mode: "matching" }`. */
    inputFiles?: CompliancePipelineInputFiles;
    inputParameters?: Record<string, any>;
    checks: CompliancePipelineCheck[];
}

export interface ComplianceMetadataRule {
    ruleType: "metadata";
    enforcement: ComplianceEnforcementLevel;
    metadataSchemaRef: { databaseId: string; schemaName: string };
    checks: {
        name: string;
        description?: string;
        validateRequired?: boolean;
        validateTypes?: boolean;
        additionalRequiredFields?: string[];
    }[];
}

export interface ComplianceRelationshipRule {
    ruleType: "relationship";
    enforcement: ComplianceEnforcementLevel;
    checks: {
        name: string;
        description?: string;
        direction: "parents" | "children" | "related";
        relationshipType: "parentChild" | "related";
        minCount?: number;
        maxCount?: number;
    }[];
}

export type ComplianceRule =
    | CompliancePipelineRule
    | ComplianceMetadataRule
    | ComplianceRelationshipRule;

export interface VamsRulesV1SchemaBody {
    schemaFormat: typeof VAMS_RULES_V1_FORMAT;
    extends?: string;
    rules: Record<string, ComplianceRule>;
}

/** A schema body is either a vams-rules-v1 rule set or a JSON Schema (draft-07 subset). */
export type ComplianceSchemaBody = VamsRulesV1SchemaBody | Record<string, any>;

export interface ComplianceSchema {
    schemaName: string;
    description?: string;
    schemaBody: ComplianceSchemaBody;
    version?: number;
    createdAt?: string;
    updatedAt?: string;
}

/**
 * States of an asset's compliance record. `exception` is a quarantine verdict the asset is
 * released from by a granted exception; it holds until the exception is revoked or superseded.
 */
export type ComplianceStateValue =
    | "unknown"
    | "pending_evaluation"
    | "compliant"
    | "non_compliant"
    | "quarantined"
    | "exception"
    | "pending_parent_resolution";

export interface ComplianceState {
    databaseId: string;
    assetId: string;
    complianceState: ComplianceStateValue;
    schemaName?: string;
    lastEvaluationAt?: string;
    lastEvaluatedAt?: string;
    lastEvaluationId?: string;
    schemaSource?: string;
    updatedAt?: string;
    quarantineReason?: string | null;
    /** Exception fields are set while an exception is active on the record. */
    exceptionGranted?: boolean;
    exceptionReason?: string;
    exceptionGrantedBy?: string;
    exceptionGrantedAt?: string;
    /** The schema and its version the exception was granted against. */
    exceptionSchemaName?: string;
    exceptionSchemaVersion?: number;
}

export type ComplianceEvaluationVerdict =
    | "compliant"
    | "non_compliant"
    | "quarantined"
    | "pending_pipeline"
    | "error";

export interface EvaluationRecord {
    evaluationId: string;
    databaseId: string;
    assetId: string;
    schemaName: string;
    result: ComplianceEvaluationVerdict;
    verdict?: ComplianceEvaluationVerdict;
    status?: "pending_pipeline" | "completed" | "error";
    violations?: string[];
    evaluatedAt: string;
    /** Workflow execution a pipeline rule launched; set once the execution has started. */
    executionId?: string;
    pipelineRuleName?: string;
    /** The verdict was computed while an exception held the asset released from quarantine. */
    exceptionApplied?: boolean;
}

export type CascadeState = "pending_approval" | "executing" | "completed" | "aborted" | "rejected";

export interface CascadeRecord {
    cascadeId: string;
    state: CascadeState;
    triggeredByDatabaseId: string;
    triggeredByAssetId: string;
    /** The trigger asset as the listing carries it; the same ids as `triggeredBy*`. */
    databaseId?: string;
    assetId?: string;
    triggerReason?: string;
    actor?: string;
    requireApproval: boolean;
    createdAt: string;
    /** JSON-encoded map of node key ("databaseId#assetId") to that node's evaluation state. */
    nodes?: string;
    totalNodes?: number;
    completedAt?: string;
    abortReason?: string;
}

/** Response of the cascade create/approve/reject routes; `state` is set when the cascade moves. */
export interface CascadeActionResponse {
    message: string;
    cascadeId: string;
    state?: CascadeState;
}

export interface AuditEntry {
    entryId: string;
    eventType: string;
    databaseId?: string;
    assetId?: string;
    schemaName?: string;
    actor: string;
    timestamp: string;
    details?: string | Record<string, any>;
}

// --- Pagination ---

/** Rows requested per page from the paged compliance listings. */
export const COMPLIANCE_LISTING_PAGE_SIZE = 50;

/** Token paging as the backend exposes it: `startingToken` is the previous page's `NextToken`. */
export interface PagingParams {
    maxItems?: number;
    startingToken?: string;
}

export interface PagedAuditEntries {
    entries: AuditEntry[];
    nextToken?: string;
}

export interface PagedEvaluations {
    evaluations: EvaluationRecord[];
    nextToken?: string;
}

const pagingQuery = (paging?: PagingParams): Record<string, string> => {
    const query: Record<string, string> = {};
    if (paging?.maxItems) {
        query.maxItems = `${paging.maxItems}`;
    }
    if (paging?.startingToken) {
        query.startingToken = paging.startingToken;
    }
    return query;
};

const responseErrored = (response: any): boolean =>
    !!response?.message &&
    typeof response.message === "string" &&
    (response.message.includes("error") || response.message.includes("Error"));

// --- Schema Management ---

export const fetchComplianceSchemas = async (): Promise<[boolean, ComplianceSchema[] | string]> => {
    try {
        const response = await apiClient.get("compliance/schemas", {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.schemas || response.Items || []];
    } catch (error: any) {
        console.log("fetchComplianceSchemas error:", error);
        return [false, error?.message || "Failed to fetch compliance schemas"];
    }
};

export const fetchComplianceSchema = async (
    schemaName: string
): Promise<[boolean, ComplianceSchema | string]> => {
    try {
        const response = await apiClient.get(`compliance/schemas/${schemaName}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log("fetchComplianceSchema error:", error);
        return [false, error?.message || "Failed to fetch compliance schema"];
    }
};

export const createComplianceSchema = async (
    schema: Omit<ComplianceSchema, "version" | "createdAt" | "updatedAt">
): Promise<[boolean, ComplianceSchema | string]> => {
    try {
        const response = await apiClient.post("compliance/schemas", {
            body: schema,
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log("createComplianceSchema error:", error);
        return [false, error?.message || "Failed to create compliance schema"];
    }
};

export const updateComplianceSchema = async (
    schemaName: string,
    schema: Partial<ComplianceSchema>
): Promise<[boolean, ComplianceSchema | string]> => {
    try {
        const response = await apiClient.put(`compliance/schemas/${schemaName}`, {
            body: schema,
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log("updateComplianceSchema error:", error);
        return [false, error?.message || "Failed to update compliance schema"];
    }
};

// --- Evaluation ---

export const evaluateAssetCompliance = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, EvaluationRecord | string]> => {
    try {
        const response = await apiClient.post(`compliance/evaluate/${databaseId}/${assetId}`, {
            body: {},
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log("evaluateAssetCompliance error:", error);
        return [false, error?.message || "Failed to evaluate asset compliance"];
    }
};

export const sweepSchema = async (schemaName: string): Promise<[boolean, any]> => {
    try {
        const response = await apiClient.post(`compliance/sweep/${schemaName}`, {
            body: {},
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log("sweepSchema error:", error);
        return [false, error?.message || "Failed to sweep schema"];
    }
};

export const fetchEvaluationHistory = async (
    databaseId: string,
    assetId: string,
    paging?: PagingParams
): Promise<[boolean, PagedEvaluations | string]> => {
    try {
        const response = await apiClient.get(`compliance/evaluations/${databaseId}/${assetId}`, {
            queryStringParameters: pagingQuery(paging),
        });
        if (responseErrored(response)) {
            return [false, response.message];
        }
        return [
            true,
            {
                evaluations: response.evaluations || response.Items || [],
                nextToken: response.NextToken || undefined,
            },
        ];
    } catch (error: any) {
        console.log("fetchEvaluationHistory error:", error);
        return [false, error?.message || "Failed to fetch evaluation history"];
    }
};

export const fetchComplianceState = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, ComplianceState | string]> => {
    try {
        const response = await apiClient.get(`compliance/state/${databaseId}/${assetId}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log("fetchComplianceState error:", error);
        return [false, error?.message || "Failed to fetch compliance state"];
    }
};

// --- Database Compliance Overview ---

/** An asset row of the database overview: its state row plus the enriched display name. */
export interface DatabaseComplianceAsset extends ComplianceState {
    assetName?: string;
}

/** `summary` and `totalAssets` cover the whole database; `assets` is one page of it. */
export interface DatabaseComplianceOverview {
    databaseId: string;
    totalAssets: number;
    summary: {
        compliant: number;
        non_compliant: number;
        pending_evaluation: number;
        quarantined: number;
        exception: number;
        unknown: number;
    };
    assets: DatabaseComplianceAsset[];
    nextToken?: string;
}

export const fetchDatabaseComplianceOverview = async (
    databaseId: string,
    paging?: PagingParams
): Promise<[boolean, DatabaseComplianceOverview | string]> => {
    try {
        const response = await apiClient.get(`compliance/state/${databaseId}`, {
            queryStringParameters: pagingQuery(paging),
        });
        if (responseErrored(response)) {
            return [false, response.message];
        }
        const { NextToken, ...overview } = response;
        return [
            true,
            { ...overview, assets: overview.assets || [], nextToken: NextToken || undefined },
        ];
    } catch (error: any) {
        console.log("fetchDatabaseComplianceOverview error:", error);
        return [false, error?.message || "Failed to fetch database compliance overview"];
    }
};

// --- Quarantine ---

export interface QuarantinedAsset extends ComplianceState {
    assetName?: string;
}

export interface PagedQuarantinedAssets {
    quarantinedAssets: QuarantinedAsset[];
    nextToken?: string;
}

/**
 * One page of quarantined assets. Authorization filters the page after it is read, so a page may
 * be empty while `nextToken` is still set; callers keep paging until it is absent.
 */
export const fetchQuarantinedAssets = async (
    paging?: PagingParams
): Promise<[boolean, PagedQuarantinedAssets | string]> => {
    try {
        const response = await apiClient.get("compliance/quarantine", {
            queryStringParameters: pagingQuery(paging),
        });
        if (responseErrored(response)) {
            return [false, response.message];
        }
        return [
            true,
            {
                quarantinedAssets:
                    response.quarantinedAssets || response.assets || response.Items || [],
                nextToken: response.NextToken || undefined,
            },
        ];
    } catch (error: any) {
        console.log("fetchQuarantinedAssets error:", error);
        return [false, error?.message || "Failed to fetch quarantined assets"];
    }
};

export const releaseFromQuarantine = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.post(
            `compliance/quarantine/${databaseId}/${assetId}/release`,
            { body: {} }
        );
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Released from quarantine"];
    } catch (error: any) {
        console.log("releaseFromQuarantine error:", error);
        return [false, error?.message || "Failed to release from quarantine"];
    }
};

export const grantException = async (
    databaseId: string,
    assetId: string,
    reason: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.post(
            `compliance/quarantine/${databaseId}/${assetId}/exception`,
            { body: { reason } }
        );
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Exception granted"];
    } catch (error: any) {
        console.log("grantException error:", error);
        return [false, error?.message || "Failed to grant exception"];
    }
};

/** Response of the exception revoke route; `complianceState` is the state the asset returned to. */
export interface ExceptionRevokedResponse {
    message: string;
    databaseId: string;
    assetId: string;
    complianceState: ComplianceStateValue;
}

/**
 * Revokes the active exception of an asset. The asset returns to the state of its last
 * evaluation (re-quarantined when that verdict was quarantined). The backend answers 400 when
 * no exception is active, which surfaces as the failed tuple's message.
 */
export const revokeException = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, ExceptionRevokedResponse | string]> => {
    try {
        const response = await apiClient.del(
            `compliance/quarantine/${databaseId}/${assetId}/exception`,
            {}
        );
        if (responseErrored(response)) {
            return [false, response.message];
        }
        return [
            true,
            {
                message: response.message || "Exception revoked",
                databaseId: response.databaseId || databaseId,
                assetId: response.assetId || assetId,
                complianceState: response.complianceState,
            },
        ];
    } catch (error: any) {
        console.log("revokeException error:", error);
        return [false, error?.message || "Failed to revoke exception"];
    }
};

// --- Cascades ---

export const fetchCascades = async (): Promise<[boolean, CascadeRecord[] | string]> => {
    try {
        const response = await apiClient.get("compliance/cascades", {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.cascades || response.Items || []];
    } catch (error: any) {
        console.log("fetchCascades error:", error);
        return [false, error?.message || "Failed to fetch cascades"];
    }
};

export const fetchCascade = async (
    cascadeId: string
): Promise<[boolean, CascadeRecord | string]> => {
    try {
        const response = await apiClient.get(`compliance/cascades/${cascadeId}`, {});
        if (responseErrored(response)) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log("fetchCascade error:", error);
        return [false, error?.message || "Failed to fetch cascade"];
    }
};

/**
 * Approval starts the cascade asynchronously: the response carries `state: "executing"` and the
 * caller observes completion through `fetchCascade`.
 */
export const approveCascade = async (
    cascadeId: string,
    reason?: string
): Promise<[boolean, CascadeActionResponse | string]> => {
    try {
        const response = await apiClient.post(`compliance/cascades/${cascadeId}/approve`, {
            body: reason === undefined ? {} : { reason },
        });
        if (responseErrored(response)) {
            return [false, response.message];
        }
        return [
            true,
            {
                message: response.message || "Cascade approved",
                cascadeId: response.cascadeId || cascadeId,
                state: response.state,
            },
        ];
    } catch (error: any) {
        console.log("approveCascade error:", error);
        return [false, error?.message || "Failed to approve cascade"];
    }
};

export const rejectCascade = async (
    cascadeId: string,
    reason?: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.post(`compliance/cascades/${cascadeId}/reject`, {
            body: reason === undefined ? {} : { reason },
        });
        if (responseErrored(response)) {
            return [false, response.message];
        }
        return [true, response.message || "Cascade rejected"];
    } catch (error: any) {
        console.log("rejectCascade error:", error);
        return [false, error?.message || "Failed to reject cascade"];
    }
};

// --- Schema Binding ---

export const bindSchemaToDatabase = async (
    databaseId: string,
    schemaName: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.put(`compliance/bind/${databaseId}`, {
            body: { schemaName },
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Schema bound to database"];
    } catch (error: any) {
        console.log("bindSchemaToDatabase error:", error);
        return [false, error?.message || "Failed to bind schema to database"];
    }
};

export const unbindSchemaFromDatabase = async (databaseId: string): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.del(`compliance/bind/${databaseId}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Schema unbound from database"];
    } catch (error: any) {
        console.log("unbindSchemaFromDatabase error:", error);
        return [false, error?.message || "Failed to unbind schema from database"];
    }
};

export const bindSchemaToAsset = async (
    databaseId: string,
    assetId: string,
    schemaName: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.put(`compliance/bind/${databaseId}/${assetId}`, {
            body: { schemaName },
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Schema bound to asset"];
    } catch (error: any) {
        console.log("bindSchemaToAsset error:", error);
        return [false, error?.message || "Failed to bind schema to asset"];
    }
};

export const unbindSchemaFromAsset = async (
    databaseId: string,
    assetId: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.del(`compliance/bind/${databaseId}/${assetId}`, {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response.message || "Schema unbound from asset"];
    } catch (error: any) {
        console.log("unbindSchemaFromAsset error:", error);
        return [false, error?.message || "Failed to unbind schema from asset"];
    }
};

export interface AssetSchemaOverride {
    databaseId: string;
    assetId: string;
    schemaName: string;
    [key: string]: any;
}

/** `assetOverrideCount` counts every override; `assetOverrides` is one page of them. */
export interface DatabaseBindings {
    databaseId: string;
    databaseSchema?: string | null;
    complianceAutoEval?: boolean;
    assetOverrides: AssetSchemaOverride[];
    assetOverrideCount: number;
    nextToken?: string;
}

export const getDatabaseBindings = async (
    databaseId: string,
    paging?: PagingParams
): Promise<[boolean, DatabaseBindings | string]> => {
    try {
        const response = await apiClient.get(`compliance/bind/${databaseId}`, {
            queryStringParameters: pagingQuery(paging),
        });
        if (responseErrored(response)) {
            return [false, response.message];
        }
        const { NextToken, ...bindings } = response;
        return [
            true,
            {
                ...bindings,
                assetOverrides: bindings.assetOverrides || [],
                assetOverrideCount:
                    bindings.assetOverrideCount ?? (bindings.assetOverrides || []).length,
                nextToken: NextToken || undefined,
            },
        ];
    } catch (error: any) {
        console.log("getDatabaseBindings error:", error);
        return [false, error?.message || "Failed to get database bindings"];
    }
};

// --- Audit ---

/** Filters of the global audit query; `eventType` selects one partition, dates bound `timestamp`. */
export interface AuditQueryParams extends PagingParams {
    eventType?: string;
    startDate?: string;
    endDate?: string;
}

const auditQuery = (params?: AuditQueryParams): Record<string, string> => {
    const query = pagingQuery(params);
    if (params?.eventType) {
        query.eventType = params.eventType;
    }
    if (params?.startDate) {
        query.startDate = params.startDate;
    }
    if (params?.endDate) {
        query.endDate = params.endDate;
    }
    return query;
};

const pagedEntries = (response: any): PagedAuditEntries => ({
    entries: response.entries || response.Items || [],
    nextToken: response.NextToken || undefined,
});

export const fetchAssetAuditHistory = async (
    databaseId: string,
    assetId: string,
    params?: AuditQueryParams
): Promise<[boolean, PagedAuditEntries | string]> => {
    try {
        const response = await apiClient.get(`compliance/audit/${databaseId}/${assetId}`, {
            queryStringParameters: auditQuery(params),
        });
        if (responseErrored(response)) {
            return [false, response.message];
        }
        return [true, pagedEntries(response)];
    } catch (error: any) {
        console.log("fetchAssetAuditHistory error:", error);
        return [false, error?.message || "Failed to fetch audit history"];
    }
};

export const fetchAuditLog = async (
    params?: AuditQueryParams
): Promise<[boolean, PagedAuditEntries | string]> => {
    try {
        const response = await apiClient.get("compliance/audit", {
            queryStringParameters: auditQuery(params),
        });
        if (responseErrored(response)) {
            return [false, response.message];
        }
        return [true, pagedEntries(response)];
    } catch (error: any) {
        console.log("fetchAuditLog error:", error);
        return [false, error?.message || "Failed to fetch audit log"];
    }
};
