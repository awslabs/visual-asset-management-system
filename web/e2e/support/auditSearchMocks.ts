/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import type { Page, Route } from "@playwright/test";

/**
 * A scripted `/audit/*` API for the Audit Log Search spec. The spec runs against any deployment,
 * including one without the audit backend, so it answers the five routes itself (page.route) in the
 * shape of the API contract and records what the page sent. Nothing here depends on the data of the
 * deployment under test.
 */

export type AuditMockMode =
    | "ok"
    | "empty"
    | "noGroups"
    | "groupsDenied"
    | "startDenied"
    | "startThrottled"
    | "startTooMany"
    | "expired"
    | "failed";

/** Text that would do harm if a page interpreted it as HTML, plus a bidi override. */
export const HOSTILE_TEXT =
    '<img src=x onerror="window.__auditXss=1"><script>window.__auditXss=1</script>\u202egnp.exe';

const row = (n: number, over: Record<string, unknown> = {}) => ({
    timestamp: `2026-11-17 01:${String(10 + n).padStart(2, "0")}:00.000`,
    groupKey: "auditActions",
    groupLabel: "Actions",
    prefix: "ACTIONS",
    action: n % 2 ? "assetUpdate" : "databaseCreate",
    legacyType: null,
    entity: { type: n % 2 ? "asset" : "database", id: `entity-${n}` },
    user: `user${n}@example.com`,
    onBehalfOf: null,
    authMethod: "jwt",
    apiKeyId: null,
    crossCall: null,
    outcome: n % 3 === 0 ? "denied" : "success",
    reason: n % 3 === 0 ? "Not authorized" : null,
    databaseId: "db-sample",
    assetId: n % 2 ? `asset-${n}` : null,
    filePath: null,
    ids: {},
    request: {
        requestId: `00000000-0000-4000-8000-00000000000${n}`,
        method: "PUT",
        route: "/database/db-sample/assets",
        sourceIp: "203.0.113.7",
    },
    bytes: null,
    fileCount: null,
    itemCount: null,
    durationMs: 12,
    requestParameters: { note: `value ${n}` },
    details: null,
    changes: null,
    previous: null,
    truncated: false,
    formatVersion: "contract-v1",
    parseStatus: "parsed",
    probe: false,
    recordId: `rec-${n}`,
    preview: null,
    ...over,
});

/** Seven records over three pages of three: one hostile, one unreadable, one cut short. */
export const MOCK_ROWS = [
    row(1, { requestParameters: { note: HOSTILE_TEXT }, reason: HOSTILE_TEXT }),
    row(2),
    row(3),
    row(4, {
        parseStatus: "unparsed",
        preview: "[ACTIONS] [user: x] <b>not json</b>",
        action: null,
        requestParameters: null,
    }),
    row(5, {
        truncated: true,
        changes: {
            body: { truncated: true, sha256: "ab".repeat(32), bytes: 99999, preview: '{"a":' },
        },
    }),
    row(6),
    row(7),
];

export const MOCK_PAGE_SIZE = 3;

export const GROUPS_RESPONSE = {
    logGroups: [
        {
            key: "auditActions",
            label: "Actions",
            prefix: "ACTIONS",
            description: "API actions",
            sensitivity: [],
        },
        {
            key: "auditAuthentication",
            label: "Authentication",
            prefix: "AUTHENTICATION",
            description: "Sign-ins",
            sensitivity: ["containsPii"],
        },
    ],
    actions: [
        { name: "assetUpdate", kind: "write", entityType: "asset", logGroup: "auditActions" },
        { name: "databaseCreate", kind: "write", entityType: "database", logGroup: "auditActions" },
    ],
    entityTypes: ["asset", "database", "auditQuery"],
    outcomes: ["success", "failed", "denied"],
    limits: {
        maxQueryWindowDays: 31,
        maxRows: 1000,
        maxConcurrentQueries: 5,
        maxConcurrentQueriesPerUser: 2,
        queryDeadlineSeconds: 300,
        longPollWaitSeconds: 20,
        maxContainsTerms: 3,
        maxContainsTermLength: 64,
        pageSize: 100,
        maxPageSize: 500,
    },
};

export interface AuditMock {
    /** Bodies of the `POST /audit/queries` calls, in order. */
    starts: Record<string, any>[];
    /** Query strings of the `GET /audit/queries/{id}` calls. */
    polls: URLSearchParams[];
    stops: number;
    exports: URLSearchParams[];
    mode: AuditMockMode;
    /** Runs before the answer to each poll; lets a test hold the search in Running. */
    holdRunning: boolean;
    /** Milliseconds the answer to the start request is held back, for a stop pressed before the handle arrives. */
    startDelayMs: number;
    /** Every request URL the mock answered, for "no failed request" checks. */
    requests: string[];
}

const CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-headers": "*",
    "access-control-allow-methods": "GET,POST,DELETE,OPTIONS",
};

const json = (route: Route, status: number, body: unknown) =>
    route.fulfill({
        status,
        headers: { ...CORS, "content-type": "application/json" },
        body: JSON.stringify(body),
    });

/** Installs the scripted API on `page`; returns the handle the test reads and steers. */
export async function installAuditMocks(
    page: Page,
    mode: AuditMockMode = "ok"
): Promise<AuditMock> {
    const mock: AuditMock = {
        starts: [],
        polls: [],
        stops: 0,
        exports: [],
        mode,
        holdRunning: false,
        startDelayMs: 0,
        requests: [],
    };
    await page.route(/\/audit\/(log-groups|queries)/, async (route) => {
        const request = route.request();
        const url = new URL(request.url());
        const method = request.method();
        if (method === "OPTIONS") {
            return route.fulfill({ status: 204, headers: CORS });
        }
        mock.requests.push(`${method} ${url.pathname}`);
        const path = url.pathname.replace(/^.*\/audit\//, "");

        if (path === "log-groups") {
            if (mock.mode === "groupsDenied")
                return json(route, 403, { message: "Not Authorized" });
            if (mock.mode === "noGroups")
                return json(route, 200, {
                    ...GROUPS_RESPONSE,
                    logGroups: [],
                    actions: [],
                    entityTypes: [],
                });
            return json(route, 200, GROUPS_RESPONSE);
        }
        if (path === "queries" && method === "POST") {
            mock.starts.push(request.postDataJSON());
            if (mock.mode === "startDenied") return json(route, 403, { message: "Not Authorized" });
            if (mock.mode === "startThrottled")
                return json(route, 429, { message: "Too Many Requests" });
            if (mock.mode === "startTooMany") {
                return json(route, 400, {
                    message: "Too many audit searches are running. Try again later.",
                });
            }
            if (mock.startDelayMs > 0) {
                await new Promise((resolve) => setTimeout(resolve, mock.startDelayMs));
            }
            return json(route, 202, {
                queryId: "mock-q-1",
                status: "Running",
                warnings: ["widerScan"],
            });
        }
        if (/^queries\/[^/]+\/export$/.test(path)) {
            mock.exports.push(url.searchParams);
            const token = url.searchParams.get("startingToken");
            const csvHead = '"timestamp","action","user"\r\n';
            return json(
                route,
                200,
                token
                    ? {
                          queryId: "mock-q-1",
                          format: "csv",
                          content:
                              '"2026-11-17 01:17:00.000","assetUpdate","user7@example.com"\r\n',
                          rowCount: 7,
                          warnings: [],
                      }
                    : {
                          queryId: "mock-q-1",
                          format: "csv",
                          content:
                              csvHead +
                              '"2026-11-17 01:11:00.000","assetUpdate","user1@example.com"\r\n',
                          rowCount: 7,
                          warnings: [],
                          NextToken: "1",
                      }
            );
        }
        if (/^queries\/[^/]+$/.test(path) && method === "DELETE") {
            mock.stops += 1;
            return json(route, 200, { queryId: "mock-q-1", status: "Cancelled" });
        }
        if (/^queries\/[^/]+$/.test(path) && method === "GET") {
            mock.polls.push(url.searchParams);
            if (mock.mode === "expired") return json(route, 404, { message: "Query not found" });
            const wait = Number(url.searchParams.get("wait") || 0);
            if (mock.holdRunning || (wait > 0 && mock.polls.length === 1)) {
                // The server's long-poll: hold briefly, then say it is still running.
                await new Promise((resolve) => setTimeout(resolve, mock.holdRunning ? 400 : 600));
                return json(route, 200, {
                    queryId: "mock-q-1",
                    status: "Running",
                    statistics: { recordsScanned: 1200, bytesScanned: 2048 },
                    Items: [],
                    warnings: [],
                });
            }
            if (mock.mode === "failed") {
                return json(route, 200, {
                    queryId: "mock-q-1",
                    status: "Failed",
                    statistics: {},
                    Items: [],
                    warnings: [],
                });
            }
            const rows = mock.mode === "empty" ? [] : MOCK_ROWS;
            const start = Number(url.searchParams.get("startingToken") || 0);
            const page = rows.slice(start, start + MOCK_PAGE_SIZE);
            const next =
                start + MOCK_PAGE_SIZE < rows.length ? String(start + MOCK_PAGE_SIZE) : undefined;
            return json(route, 200, {
                queryId: "mock-q-1",
                status: "Complete",
                statistics: {
                    recordsScanned: 1200,
                    bytesScanned: 2048,
                    rowCount: rows.length,
                    candidateLimitReached: mock.starts[mock.starts.length - 1]?.contains
                        ? true
                        : undefined,
                },
                Items: page,
                ...(next ? { NextToken: next } : {}),
                warnings: [],
            });
        }
        return json(route, 404, { message: "Not mocked" });
    });
    return mock;
}
