/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Turns the page's filters into the `POST /audit/queries` body, and the API's answers into the types
 * the page uses. The readers are lenient about field names the contract may spell two ways and strict
 * about types: a value of the wrong type is dropped, never passed on.
 */

import {
    AuditFilters,
    AuditLogGroupInfo,
    AuditLogGroupsResult,
    AuditQueryPage,
    AuditQueryStatistics,
    AuditRow,
} from "./auditSearchTypes";
import { DEFAULT_LIMITS } from "./auditSearchValidation";
import { rangeBounds } from "./auditSearchUrl";

/** The `POST /audit/queries` body. The caller never sends query text, only these named filters. */
export const buildStartBody = (
    filters: AuditFilters,
    now: number = Date.now()
): Record<string, unknown> => {
    const { start, end } = rangeBounds(filters.range, now);
    const body: Record<string, unknown> = {
        startTime: new Date(start).toISOString().replace(/\.\d{3}Z$/, "Z"),
        endTime: new Date(end).toISOString().replace(/\.\d{3}Z$/, "Z"),
        includeProbes: filters.includeProbes,
        sort: filters.sort,
    };
    const lists = [
        "actions",
        "entityTypes",
        "entityIds",
        "users",
        "apiKeyIds",
        "databaseIds",
        "assetIds",
        "outcomes",
        "requestIds",
        "contains",
    ] as const;
    // No group chosen: the key is left out and the server searches every group the caller may.
    if (filters.logGroups.length > 0) body.logGroups = filters.logGroups;
    for (const key of lists) {
        if (filters[key].length > 0) body[key] = filters[key];
    }
    return body;
};

const asRecord = (value: unknown): Record<string, unknown> =>
    typeof value === "object" && value !== null && !Array.isArray(value)
        ? (value as Record<string, unknown>)
        : {};

const asString = (value: unknown): string | undefined =>
    typeof value === "string" && value.length > 0 ? value : undefined;

const asNumber = (value: unknown): number | undefined =>
    typeof value === "number" && Number.isFinite(value) ? value : undefined;

const asStringList = (value: unknown): string[] =>
    Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];

const positive = (value: unknown, fallback: number): number => {
    const n = asNumber(value);
    return n !== undefined && n > 0 ? n : fallback;
};

/** `GET /audit/log-groups` as the page uses it. */
export const normalizeLogGroups = (response: unknown): AuditLogGroupsResult => {
    const data = asRecord(response);
    const rawGroups = Array.isArray(data.logGroups) ? data.logGroups : [];
    // The contract lists actions once, each naming its group; entity types once for all groups.
    const rawActions = (Array.isArray(data.actions) ? data.actions : []).map(asRecord);
    const allEntityTypes = asStringList(data.entityTypes);
    const groups: AuditLogGroupInfo[] = [];
    for (const raw of rawGroups) {
        const group = asRecord(raw);
        const key = asString(group.key);
        if (!key) continue;
        const actions = rawActions
            .filter((a) => typeof a.name === "string" && a.logGroup === key)
            .map((a) => ({
                name: a.name as string,
                kind: asString(a.kind),
                entityType: asString(a.entityType) ?? null,
            }));
        groups.push({
            key,
            label: asString(group.label) ?? key,
            prefix: asString(group.prefix),
            description: asString(group.description),
            actions,
            entityTypes: allEntityTypes,
            outcomes: [],
        });
    }
    const limits = asRecord(data.limits);
    const outcomes = asStringList(data.outcomes);
    return {
        groups,
        limits: {
            maxQueryWindowDays: positive(
                limits.maxQueryWindowDays,
                DEFAULT_LIMITS.maxQueryWindowDays
            ),
            maxRows: positive(limits.maxRows, DEFAULT_LIMITS.maxRows),
            maxContainsTerms: positive(limits.maxContainsTerms, DEFAULT_LIMITS.maxContainsTerms),
            maxContainsTermLength: positive(
                limits.maxContainsTermLength,
                DEFAULT_LIMITS.maxContainsTermLength
            ),
            longPollWaitSeconds: positive(
                limits.longPollWaitSeconds,
                DEFAULT_LIMITS.longPollWaitSeconds ?? 20
            ),
        },
        outcomes: outcomes.length > 0 ? outcomes : ["success", "failed", "denied"],
    };
};

/** A row as the table reads it: only an object is a row. */
const normalizeRow = (raw: unknown): AuditRow | null =>
    typeof raw === "object" && raw !== null && !Array.isArray(raw) ? (raw as AuditRow) : null;

const normalizeStatistics = (raw: unknown): AuditQueryStatistics => {
    const stats = asRecord(raw);
    const result: AuditQueryStatistics = {};
    for (const key of [
        "recordsMatched",
        "recordsScanned",
        "bytesScanned",
        "duplicatesRemoved",
        "rowCount",
    ] as const) {
        const value = asNumber(stats[key]);
        if (value !== undefined) result[key] = value;
    }
    if (typeof stats.candidateLimitReached === "boolean") {
        result.candidateLimitReached = stats.candidateLimitReached;
    }
    return result;
};

/** `POST /audit/queries` (202): the handle and first status. */
export const normalizeStart = (
    response: unknown
): { queryId: string; status: string; warnings: string[] } | null => {
    const data = asRecord(response);
    const queryId = asString(data.queryId);
    return queryId
        ? {
              queryId,
              status: asString(data.status) ?? "Running",
              warnings: asStringList(data.warnings),
          }
        : null;
};

/** `GET /audit/queries/{queryId}`: one poll, one page. */
export const normalizeQueryPage = (response: unknown): AuditQueryPage => {
    const data = asRecord(response);
    const rawRows = Array.isArray(data.Items) ? data.Items : [];
    const pollAfter = asNumber(data.pollAfterMs);
    return {
        queryId: asString(data.queryId),
        status: asString(data.status) ?? "Running",
        pollAfterMs: pollAfter !== undefined && pollAfter >= 0 ? pollAfter : undefined,
        statistics: normalizeStatistics(data.statistics),
        rows: rawRows.map(normalizeRow).filter((row): row is AuditRow => row !== null),
        nextToken: asString(data.NextToken) ?? asString(data.nextToken),
        warnings: asStringList(data.warnings),
    };
};

/** The states after which the server will not change the result. */
export const TERMINAL_STATUSES = ["complete", "failed", "cancelled", "timeout"];

export const isTerminal = (status: string): boolean =>
    TERMINAL_STATUSES.includes(status.toLowerCase());

/** One export chunk: the text (CSV) or rows (JSON) and the token of the next chunk. */
export interface AuditExportChunk {
    text?: string;
    rows?: unknown[];
    nextToken?: string;
    rowCount?: number;
}

export const normalizeExportChunk = (response: unknown): AuditExportChunk => {
    if (typeof response === "string") return { text: response };
    const data = asRecord(response);
    const body = data.content;
    return {
        text: typeof body === "string" ? body : undefined,
        rows: Array.isArray(body) ? body : undefined,
        nextToken: asString(data.NextToken) ?? asString(data.nextToken),
        rowCount: asNumber(data.rowCount),
    };
};
