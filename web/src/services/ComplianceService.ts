/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { apiClient } from "./apiClient";

// --- Schema body (vams-rules-v1) ---

export const VAMS_RULES_V1_FORMAT = "vams-rules-v1";

export type ComplianceEnforcementLevel = "quarantine" | "warn" | "inform";

export type ComplianceRuleType =
    | "pipeline"
    | "metadata"
    | "relationship"
    | "fileStructure"
    | "versionCoverage"
    | "assetAttributes";

/** Every rule type of the vams-rules-v1 format, in the order the editor offers them. */
export const COMPLIANCE_RULE_TYPES: ComplianceRuleType[] = [
    "pipeline",
    "metadata",
    "relationship",
    "fileStructure",
    "versionCoverage",
    "assetAttributes",
];

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

/**
 * Which metadata a metadata rule validates: the asset's own (`asset`, the default), each current
 * file's metadata (`files`) or attributes (`fileAttributes`), or each asset link's metadata
 * (`links`). The referenced metadata schema must be of the matching entity type.
 */
export type ComplianceMetadataScope = "asset" | "files" | "fileAttributes" | "links";

export const COMPLIANCE_METADATA_SCOPES: ComplianceMetadataScope[] = [
    "asset",
    "files",
    "fileAttributes",
    "links",
];

export interface ComplianceMetadataRule {
    ruleType: "metadata";
    enforcement: ComplianceEnforcementLevel;
    /** Without `databaseId` the schema is looked up in the evaluated asset's database, then GLOBAL. */
    metadataSchemaRef: { databaseId?: string; schemaName: string };
    /** Absent means `asset`. */
    scope?: ComplianceMetadataScope;
    /** `files` / `fileAttributes` only: regular expressions a file path must match (any one). */
    includePaths?: string[];
    /** `files` / `fileAttributes` only: regular expressions of file paths left out. */
    excludePaths?: string[];
    /** `links` only; absent means both directions. */
    linkDirection?: "parents" | "children" | "related";
    /** `links` only; absent means either relationship type. */
    relationshipType?: "parentChild" | "related";
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

/**
 * Checks over the asset's current, non-archived files. Patterns are regular expressions searched in
 * each file's asset-relative path without its leading `/`.
 */
export interface ComplianceFileStructureCheck {
    name: string;
    description?: string;
    /** Every pattern must match at least one file. */
    requiredPaths?: string[];
    /** No file may match any pattern. */
    forbiddenPaths?: string[];
    minFiles?: number;
    maxFiles?: number;
}

export interface ComplianceFileStructureRule {
    ruleType: "fileStructure";
    enforcement: ComplianceEnforcementLevel;
    checks: ComplianceFileStructureCheck[];
}

/** Every current file not matched by `excludePaths` has its current version in an asset version. */
export interface ComplianceVersionCoverageCheck {
    name: string;
    description?: string;
    excludePaths?: string[];
}

export interface ComplianceVersionCoverageRule {
    ruleType: "versionCoverage";
    enforcement: ComplianceEnforcementLevel;
    checks: ComplianceVersionCoverageCheck[];
}

export type ComplianceAssetAttributeField = "assetName" | "description" | "tags";

/**
 * One check on the asset row. `assetName` and `description` take exactly one of `matches` /
 * `notMatches`; `tags` takes at least one of `requiredTags`, `forbiddenTags`, `minTags`.
 */
export interface ComplianceAssetAttributeCheck {
    name: string;
    description?: string;
    field: ComplianceAssetAttributeField;
    matches?: string;
    notMatches?: string;
    requiredTags?: string[];
    forbiddenTags?: string[];
    minTags?: number;
}

export interface ComplianceAssetAttributesRule {
    ruleType: "assetAttributes";
    enforcement: ComplianceEnforcementLevel;
    checks: ComplianceAssetAttributeCheck[];
}

export type ComplianceRule =
    | CompliancePipelineRule
    | ComplianceMetadataRule
    | ComplianceRelationshipRule
    | ComplianceFileStructureRule
    | ComplianceVersionCoverageRule
    | ComplianceAssetAttributesRule;

export interface VamsRulesV1SchemaBody {
    schemaFormat: typeof VAMS_RULES_V1_FORMAT;
    extends?: string;
    rules: Record<string, ComplianceRule>;
}

/** A schema body is either a vams-rules-v1 rule set or a JSON Schema (draft-07 subset). */
export type ComplianceSchemaBody = VamsRulesV1SchemaBody | Record<string, any>;

/**
 * The format of a schema record as the listing reports it: `legacy` marks a row whose body is not a
 * vams-rules-v1 document. A legacy schema cannot be bound, updated or evaluated; it is kept only
 * so it can be found and deleted.
 */
export type ComplianceSchemaFormat = typeof VAMS_RULES_V1_FORMAT | "legacy";

/**
 * The caller's allowed actions on one listed item, computed by the service. A response that
 * predates the field carries none; `itemAllows` then defers to the route-level check alone.
 */
export interface ItemPermissions {
    [action: string]: boolean | undefined;
}

/**
 * Whether the item's `permissions` allow `action`. An item without `permissions` (a response that
 * predates them) allows it, so the page falls back to its route-level gating.
 */
export const itemAllows = (
    item: { permissions?: ItemPermissions } | null | undefined,
    action: string
): boolean => {
    if (!item?.permissions) return true;
    return item.permissions[action] === true;
};

export interface ComplianceSchema {
    schemaName: string;
    description?: string;
    /** The database the schema is available to: "GLOBAL" (every database) or one database id. */
    databaseId?: string;
    schemaBody: ComplianceSchemaBody;
    /** Derived by the backend from the body; absent on a response that predates the field. */
    schemaFormat?: ComplianceSchemaFormat;
    version?: number;
    createdAt?: string;
    updatedAt?: string;
    isSystem?: boolean;
    /** Listing only: `edit`, `delete` and `bind` (see `itemAllows`). */
    permissions?: ItemPermissions;
}

/**
 * The listed schemas a binding picker offers: those the service marks bindable for the caller
 * (readable, a vams-rules-v1 body, and the binding permission in the listing's database).
 */
export const bindableSchemas = (schemas: ComplianceSchema[]): ComplianceSchema[] =>
    schemas.filter((schema) => itemAllows(schema, "bind"));

/** The schema listing with the scopes the caller may create a schema in (unfiltered listing). */
export interface ComplianceSchemaListing {
    schemas: ComplianceSchema[];
    /** `GLOBAL` and database ids; absent on a response that predates the field. */
    createScopes?: string[];
}

/** The format of a schema record, derived from its body when the record does not carry it. */
export const complianceSchemaFormat = (schema: ComplianceSchema): ComplianceSchemaFormat => {
    if (schema.schemaFormat === VAMS_RULES_V1_FORMAT || schema.schemaFormat === "legacy") {
        return schema.schemaFormat;
    }
    return (schema.schemaBody as any)?.schemaFormat === VAMS_RULES_V1_FORMAT
        ? VAMS_RULES_V1_FORMAT
        : "legacy";
};

/**
 * States of an asset's compliance record. `exception` is a quarantine verdict the asset is
 * released from by a granted exception; it holds until the exception is revoked or superseded.
 * `archived` is never stored: the asset-state read reports it for an archived asset, which is not
 * evaluated while it is archived, with the stored state as `recordedComplianceState`.
 */
export type ComplianceStateValue =
    | "unknown"
    | "pending_evaluation"
    | "compliant"
    | "non_compliant"
    | "quarantined"
    | "exception"
    | "pending_parent_resolution"
    | "archived";

/**
 * Status of an asset's last evaluation. `error` means it produced no verdict — every rule errored
 * or the schema could not be loaded — so `complianceState` is the state the asset held before it.
 */
export type ComplianceLastEvaluationStatus = "error" | "completed" | "pending_pipeline";

export interface ComplianceState {
    databaseId: string;
    assetId: string;
    complianceState: ComplianceStateValue;
    /** Set when `complianceState` is `archived`: the state the record holds, which applies again once the asset is unarchived. */
    recordedComplianceState?: ComplianceStateValue;
    schemaName?: string;
    lastEvaluationAt?: string;
    lastEvaluatedAt?: string;
    lastEvaluationId?: string;
    lastEvaluationStatus?: ComplianceLastEvaluationStatus;
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

/**
 * Outcome of one rule. `status` `error` is a non-verdict result: the rule's tooling failed (an
 * input selection that could not be satisfied, a launch that did not start), so `passed` is false
 * but the rule's enforcement did not apply to the verdict.
 */
export interface ComplianceRuleResult {
    ruleName: string;
    ruleType: ComplianceRuleType;
    enforcement: ComplianceEnforcementLevel;
    passed: boolean;
    status?: "evaluated" | "error";
    message?: string | null;
    measured?: Record<string, any> | null;
    expected?: Record<string, any> | null;
}

export interface EvaluationRecord {
    evaluationId: string;
    databaseId: string;
    assetId: string;
    schemaName: string;
    /** Version of the schema the evaluation read. */
    schemaVersion?: number | null;
    result: ComplianceEvaluationVerdict;
    verdict?: ComplianceEvaluationVerdict;
    status?: "pending_pipeline" | "completed" | "error";
    violations?: string[];
    /** The rule results, JSON-encoded on the history listing. */
    ruleResults?: string | ComplianceRuleResult[];
    /** At least one rule errored; its name is in `errorRules` and it did not count toward the verdict. */
    hasRuleErrors?: boolean;
    errorRules?: string[];
    evaluatedAt: string;
    /** Workflow execution a pipeline rule launched; set once the execution has started. */
    executionId?: string;
    pipelineRuleName?: string;
    /** The verdict was computed while an exception held the asset released from quarantine. */
    exceptionApplied?: boolean;
}

/** Names of the rules of an evaluation that errored, read from `errorRules` or the rule results. */
export const erroredRuleNames = (evaluation: EvaluationRecord): string[] => {
    if (Array.isArray(evaluation.errorRules)) {
        return evaluation.errorRules;
    }
    let results = evaluation.ruleResults;
    if (typeof results === "string") {
        try {
            results = JSON.parse(results);
        } catch {
            return [];
        }
    }
    return Array.isArray(results)
        ? results.filter((rule) => rule?.status === "error").map((rule) => rule.ruleName)
        : [];
};

/** Response of the evaluate route: the verdict of the rules that completed in the request. */
export interface EvaluateAssetResponse {
    message: string;
    evaluationId: string;
    schemaName: string;
    /** Version of the schema the evaluation read. */
    schemaVersion?: number;
    verdict: ComplianceEvaluationVerdict;
    complianceState: ComplianceStateValue;
    ruleResults: ComplianceRuleResult[];
    pipelineRulesPending: number;
    exceptionApplied?: boolean;
    hasRuleErrors?: boolean;
}

export type CascadeState = "pending_approval" | "executing" | "completed" | "aborted" | "rejected";

/**
 * State of one node of a cascade. `pending` and `evaluating` are in flight; every other value is
 * final: the verdict the node's evaluation produced, `skipped` (no bound schema, or the asset no
 * longer exists), `skipped_not_authorized` (in a database the caller who started the cascade may
 * not evaluate), `skipped_archived` (archived when the cascade reached it; not evaluated; returned
 * only to a caller who may see that archived asset),
 * `superseded` (a newer evaluation of the asset had already started, so this one did not run; not
 * a failure) or `error`.
 */
export type CascadeNodeState =
    | "pending"
    | "evaluating"
    | "skipped"
    | "skipped_not_authorized"
    | "skipped_archived"
    | "superseded"
    | ComplianceEvaluationVerdict;

export interface CascadeRecord {
    cascadeId: string;
    state: CascadeState;
    triggeredByDatabaseId: string;
    triggeredByAssetId: string;
    /** The trigger asset as the listing carries it; the same ids as `triggeredBy*`. */
    databaseId?: string;
    assetId?: string;
    /** The trigger asset's name when the cascade was created. */
    assetName?: string;
    triggerReason?: string;
    actor?: string;
    requireApproval: boolean;
    createdAt: string;
    /** When a cascade awaiting approval times out (24 hours after creation); past it, it is aborted. */
    approvalTimeoutAt?: string;
    /** JSON-encoded map of node key ("databaseId:assetId") to that node's `CascadeNodeState`. */
    nodes?: string;
    totalNodes?: number;
    completedAt?: string;
    abortReason?: string;
    /** `approve`: whether the caller may approve or reject the cascade (see `itemAllows`). */
    permissions?: ItemPermissions;
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
    /** The asset's name when the entry was written (absent on older entries). */
    assetName?: string;
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

/** Adds the non-empty filter values to a listing query. */
const withFilters = (
    query: Record<string, string>,
    filters?: Record<string, string | undefined>
): Record<string, string> => {
    Object.entries(filters || {}).forEach(([key, value]) => {
        if (value) {
            query[key] = value;
        }
    });
    return query;
};

const responseErrored = (response: any): boolean =>
    !!response?.message &&
    typeof response.message === "string" &&
    (response.message.includes("error") || response.message.includes("Error"));

/** The suffix of an archived asset's database id, as the asset routes return its record. */
export const ARCHIVED_DATABASE_ID_SUFFIX = "#deleted";

/**
 * The database id the compliance routes take for an asset: its live database id. An archived
 * asset's record carries `<databaseId>#deleted`, which no compliance route accepts; its compliance
 * records keep the live id.
 */
export const liveDatabaseId = (databaseId?: string | null): string | undefined =>
    databaseId && databaseId.endsWith(ARCHIVED_DATABASE_ID_SUFFIX)
        ? databaseId.slice(0, -ARCHIVED_DATABASE_ID_SUFFIX.length)
        : databaseId || undefined;

// --- Schema Management ---

/**
 * The compliance schemas the caller may read. With `databaseId`, only the schemas that database may
 * bind: the GLOBAL ones and those available to that database alone.
 */
export const fetchComplianceSchemas = async (
    databaseId?: string
): Promise<[boolean, ComplianceSchema[] | string]> => {
    try {
        const response = await apiClient.get(
            "compliance/schemas",
            databaseId ? { queryStringParameters: { databaseId } } : {}
        );
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

/**
 * The unfiltered schema listing: every schema the caller may read, and `createScopes`, the scopes
 * the caller may create a schema in.
 */
export const fetchComplianceSchemaListing = async (): Promise<
    [boolean, ComplianceSchemaListing | string]
> => {
    try {
        const response = await apiClient.get("compliance/schemas", {});
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [
            true,
            {
                schemas: response?.schemas || response?.Items || [],
                createScopes: Array.isArray(response?.createScopes)
                    ? response.createScopes
                    : undefined,
            },
        ];
    } catch (error: any) {
        console.log("fetchComplianceSchemaListing error:", error);
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
): Promise<[boolean, EvaluateAssetResponse | string]> => {
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

/** The response of POST /compliance/evaluate/{databaseId}. */
export interface EvaluateDatabaseResponse {
    message: string;
    databaseId: string;
    assetsTriggered: {
        databaseId: string;
        assetId: string;
        schemaName: string;
        evaluationId?: string;
        verdict?: string;
        superseded?: boolean;
    }[];
    assetsRemaining: number;
    superseded?: number;
    registered?: number;
    withoutSchema?: number;
    /**
     * Archived assets the caller may see: not evaluated, their records left as they are
     * (`skippedArchived`). 0 for a caller who may not see the database's archive.
     */
    skipped?: number;
    skippedArchived?: number;
    /** Present while assets remain: pass it back as `startingToken` to continue the evaluation. */
    NextToken?: string;
}

/** The body of a sweep or database-evaluation call: the token continues an earlier one. */
const sweepBody = (startingToken?: string) => (startingToken ? { startingToken } : {});

/**
 * Evaluates every tracked asset of one database, each against its own bound schema (the database
 * binding or the asset's override); no other database is evaluated. Bounded like a sweep: while
 * `assetsRemaining` is above 0 the response carries `NextToken`, and a call with it as
 * `startingToken` continues with the assets not evaluated yet.
 */
export const evaluateDatabaseCompliance = async (
    databaseId: string,
    startingToken?: string
): Promise<[boolean, EvaluateDatabaseResponse | string]> => {
    try {
        const response = await apiClient.post(`compliance/evaluate/${databaseId}`, {
            body: sweepBody(startingToken),
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [true, response];
    } catch (error: any) {
        console.log("evaluateDatabaseCompliance error:", error);
        return [false, error?.message || "Failed to evaluate the assets"];
    }
};

/** One call of a schema sweep; `startingToken` (the previous call's `NextToken`) continues it. */
export const sweepSchema = async (
    schemaName: string,
    startingToken?: string
): Promise<[boolean, any]> => {
    try {
        const response = await apiClient.post(`compliance/sweep/${schemaName}`, {
            body: sweepBody(startingToken),
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

/**
 * `summary` and `totalAssets` cover the whole database; `assets` is one page of it. `summary.error`
 * counts the assets whose last evaluation ended in an error — an overlay on the state buckets,
 * not a state, so it is not part of their sum.
 */
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
        error?: number;
    };
    assets: DatabaseComplianceAsset[];
    nextToken?: string;
    /**
     * Set when `sort=name` was asked while the name index is still being built (after an
     * upgrade); the page is then in asset ID order.
     */
    nameSortUnavailable?: boolean;
}

/** Server-side filters of the database overview; the summary ignores them. */
export interface OverviewFilters {
    state?: string;
    schemaName?: string;
    schemaSource?: string;
    /** "true": the last evaluation errored; "false": it did not. */
    evaluationError?: string;
    text?: string;
    sort?: string;
}

export const fetchDatabaseComplianceOverview = async (
    databaseId: string,
    paging?: PagingParams,
    filters?: OverviewFilters
): Promise<[boolean, DatabaseComplianceOverview | string]> => {
    try {
        const response = await apiClient.get(`compliance/state/${databaseId}`, {
            queryStringParameters: withFilters(pagingQuery(paging), { ...filters }),
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
    /** `release` and `exception` (see `itemAllows`). */
    permissions?: ItemPermissions;
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
    paging?: PagingParams,
    filters?: { databaseId?: string; text?: string }
): Promise<[boolean, PagedQuarantinedAssets | string]> => {
    try {
        const response = await apiClient.get("compliance/quarantine", {
            queryStringParameters: withFilters(pagingQuery(paging), { ...filters }),
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

/**
 * Releases a quarantined asset. The reason, when given, is recorded on the audit entry; without
 * one the backend records its default.
 */
export const releaseFromQuarantine = async (
    databaseId: string,
    assetId: string,
    reason?: string
): Promise<[boolean, string]> => {
    try {
        const response = await apiClient.post(
            `compliance/quarantine/${databaseId}/${assetId}/release`,
            { body: reason ? { reason } : {} }
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

export interface CascadeListParams extends PagingParams {
    /** pending_approval (the default), executing, completed or aborted. */
    state?: string;
    databaseId?: string;
    text?: string;
}

export interface PagedCascades {
    cascades: CascadeRecord[];
    nextToken?: string;
}

/** One page of cascades, newest first, filtered on the server. */
export const fetchCascades = async (
    params?: CascadeListParams
): Promise<[boolean, PagedCascades | string]> => {
    try {
        const response = await apiClient.get("compliance/cascades", {
            queryStringParameters: withFilters(pagingQuery(params), {
                state: params?.state,
                databaseId: params?.databaseId,
                text: params?.text,
            }),
        });
        if (
            response?.message &&
            (response.message.includes("error") || response.message.includes("Error"))
        ) {
            return [false, response.message];
        }
        return [
            true,
            {
                cascades: response.cascades || response.Items || [],
                nextToken: response.NextToken || undefined,
            },
        ];
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
    /** Global listing only: one database, one asset of it, and a contains search. */
    databaseId?: string;
    assetId?: string;
    text?: string;
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

const globalAuditQuery = (params?: AuditQueryParams): Record<string, string> =>
    withFilters(auditQuery(params), {
        databaseId: params?.databaseId,
        assetId: params?.assetId,
        text: params?.text,
    });

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
            queryStringParameters: globalAuditQuery(params),
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
