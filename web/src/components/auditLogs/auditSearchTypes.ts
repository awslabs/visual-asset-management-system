/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Types of the audit-log search API (`/audit/*`). The web app reads log groups, actions, entity types
 * and limits from `GET /audit/log-groups`; none of them is hard-coded here (the registry on the
 * server is the one list). Every field a record carries comes from the server already redacted and
 * output-encoded; the page still renders it as text only.
 */

/** One action a log group records, as `GET /audit/log-groups` lists it. */
export interface AuditActionInfo {
    name: string;
    kind?: string;
    entityType?: string | null;
}

/** The limits the server enforces on a search; the page shows them and validates against them. */
export interface AuditSearchLimits {
    maxQueryWindowDays: number;
    maxRows: number;
    maxContainsTerms: number;
    maxContainsTermLength: number;
    longPollWaitSeconds?: number;
}

/** One log group the caller may search. */
export interface AuditLogGroupInfo {
    key: string;
    label: string;
    prefix?: string;
    description?: string;
    actions?: AuditActionInfo[];
    entityTypes?: string[];
    outcomes?: string[];
}

/** `GET /audit/log-groups`, normalised by `normalizeLogGroups`. */
export interface AuditLogGroupsResult {
    groups: AuditLogGroupInfo[];
    limits: AuditSearchLimits;
    outcomes: string[];
}

export type AuditOutcome = "success" | "failed" | "denied";

export type AuditParseStatus = "parsed" | "partial" | "unparsed" | "unsupportedVersion";

/** One result row (the server's row model). Absent facts are null or missing. */
export interface AuditRow {
    timestamp?: string | null;
    groupKey?: string | null;
    groupLabel?: string | null;
    prefix?: string | null;
    action?: string | null;
    legacyType?: string | null;
    entity?: { type?: string | null; id?: string | null } | null;
    user?: string | null;
    onBehalfOf?: string | null;
    authMethod?: string | null;
    apiKeyId?: string | null;
    crossCall?: boolean | null;
    outcome?: string | null;
    reason?: string | null;
    databaseId?: string | null;
    assetId?: string | null;
    filePath?: string | null;
    ids?: Record<string, unknown> | null;
    request?: Record<string, unknown> | null;
    bytes?: number | null;
    fileCount?: number | null;
    itemCount?: number | null;
    durationMs?: number | null;
    requestParameters?: unknown;
    details?: unknown;
    changes?: unknown;
    previous?: unknown;
    truncated?: boolean | null;
    formatVersion?: string | null;
    parseStatus?: AuditParseStatus | string | null;
    probe?: boolean | null;
    recordId?: string | null;
    preview?: string | null;
}

/** Statistics of a query; every field is optional because the server may omit what it cannot know. */
export interface AuditQueryStatistics {
    recordsMatched?: number;
    recordsScanned?: number;
    bytesScanned?: number;
    duplicatesRemoved?: number;
    candidateLimitReached?: boolean;
    rowCount?: number;
}

/** The query states of the long-poll; anything not terminal keeps the loop going. */
export type AuditQueryStatus =
    | "Scheduled"
    | "Running"
    | "Complete"
    | "Failed"
    | "Cancelled"
    | "Timeout"
    | string;

/** `GET /audit/queries/{queryId}`, normalised by `normalizeQueryPage`. */
export interface AuditQueryPage {
    queryId?: string;
    status: AuditQueryStatus;
    pollAfterMs?: number;
    statistics: AuditQueryStatistics;
    rows: AuditRow[];
    nextToken?: string;
    warnings: string[];
}

/** The time range of a search: a relative window ending now, or two absolute instants (ISO, UTC). */
export type AuditTimeRange =
    | { type: "relative"; amount: number; unit: "minute" | "hour" | "day" }
    | { type: "absolute"; startDate: string; endDate: string };

/** The filters of a search. Lists are OR within a filter and AND across filters (server rule). */
export interface AuditFilters {
    logGroups: string[];
    actions: string[];
    entityTypes: string[];
    entityIds: string[];
    users: string[];
    apiKeyIds: string[];
    databaseIds: string[];
    assetIds: string[];
    outcomes: string[];
    requestIds: string[];
    contains: string[];
    /** Whether permission-probe records are included (the page hides them by default). */
    includeProbes: boolean;
    sort: "asc" | "desc";
    range: AuditTimeRange;
}

/** The list filters, in one place: key of the request body, URL key, label and one-line help. */
export const AUDIT_LIST_FILTERS = [
    { key: "logGroups", param: "group", label: "Log group" },
    { key: "actions", param: "action", label: "Action" },
    { key: "entityTypes", param: "entityType", label: "Entity type" },
    { key: "entityIds", param: "entityId", label: "Entity ID" },
    { key: "users", param: "user", label: "User" },
    { key: "apiKeyIds", param: "apiKeyId", label: "API key ID" },
    { key: "databaseIds", param: "databaseId", label: "Database ID" },
    { key: "assetIds", param: "assetId", label: "Asset ID" },
    { key: "outcomes", param: "outcome", label: "Outcome" },
    { key: "requestIds", param: "requestId", label: "Request ID" },
    { key: "contains", param: "contains", label: "Contains" },
] as const;

export type AuditListFilterKey = (typeof AUDIT_LIST_FILTERS)[number]["key"];
