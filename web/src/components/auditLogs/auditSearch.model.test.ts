/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The pure parts of the audit search page: URL state, filter validation against the server limits,
 * the request body, the response readers, the filter/token conversion and the long-poll loop.
 */

import {
    defaultAuditFilters,
    parseAuditSearchUrl,
    serializeAuditFilters,
    MAX_VALUES_PER_FILTER,
} from "./auditSearchUrl";
import { auditFilterProblems, containsTermProblem, DEFAULT_LIMITS } from "./auditSearchValidation";
import {
    buildStartBody,
    normalizeLogGroups,
    normalizeQueryPage,
    isTerminal,
} from "./auditSearchApiModel";
import {
    filtersToQuery,
    pickerValueToRange,
    queryToFilters,
    rangeToPickerValue,
    relativeOptions,
} from "./auditFilterQuery";
import { runPollLoop, PollResult, MIN_POLL_GAP_MS } from "./auditPollLoop";
import { auditTimeMs, visibleText } from "./auditText";
import { AuditFilters, AuditTimeRange } from "./auditSearchTypes";

describe("URL state", () => {
    it("round-trips every filter", () => {
        const filters: AuditFilters = {
            ...defaultAuditFilters(),
            logGroups: ["auditActions", "auditAuthentication"],
            actions: ["assetUpdate"],
            entityTypes: ["asset"],
            entityIds: ["db1/asset1/file.usd"],
            users: ["auth0|123", "alice@example.com"],
            apiKeyIds: ["11111111-2222"],
            databaseIds: ["db1"],
            assetIds: ["asset1"],
            outcomes: ["denied"],
            requestIds: ["aaaaaaaa-bbbb"],
            contains: ["report.usd"],
            includeProbes: true,
            sort: "asc",
            range: {
                type: "absolute",
                startDate: "2026-11-01T00:00:00Z",
                endDate: "2026-11-02T00:00:00Z",
            },
        };
        const query = serializeAuditFilters(filters);
        expect(parseAuditSearchUrl(`#/auth/audit-log-search/?${query}`)).toEqual(filters);
    });

    it("leaves defaults out of the URL and reads an empty URL as the defaults", () => {
        expect(serializeAuditFilters(defaultAuditFilters())).toBe("");
        expect(parseAuditSearchUrl("#/auth/audit-log-search/")).toEqual(defaultAuditFilters());
    });

    it("keeps a relative range", () => {
        const filters = {
            ...defaultAuditFilters(),
            range: { type: "relative", amount: 7, unit: "day" } as const,
        };
        expect(serializeAuditFilters(filters)).toBe("rel=7d");
        expect(parseAuditSearchUrl("?rel=7d").range).toEqual(filters.range);
    });

    it("drops what a hostile URL cannot make valid", () => {
        const many = Array.from({ length: 50 }, (_, i) => `user=u${i}`).join("&");
        const parsed = parseAuditSearchUrl(
            `?${many}&user=${"x".repeat(
                5000
            )}&rel=999999d&from=nope&to=nope&sort=%3Cscript%3E&probes=yes&unknown=1`
        );
        expect(parsed.users).toHaveLength(MAX_VALUES_PER_FILTER);
        expect(parsed.users.every((u) => u.length <= 1024)).toBe(true);
        expect(parsed.range).toEqual(defaultAuditFilters().range);
        expect(parsed.sort).toBe("desc");
        expect(parsed.includeProbes).toBe(false);
    });

    it("ignores a reversed absolute range", () => {
        const parsed = parseAuditSearchUrl("?from=2026-11-02T00:00:00Z&to=2026-11-01T00:00:00Z");
        expect(parsed.range).toEqual(defaultAuditFilters().range);
    });
});

describe("validation against the server limits", () => {
    const now = Date.parse("2026-11-17T00:00:00Z");

    it("accepts the defaults", () => {
        expect(auditFilterProblems(defaultAuditFilters(), DEFAULT_LIMITS, now)).toEqual([]);
    });

    it("applies the contains rules the server applies", () => {
        const limits = { ...DEFAULT_LIMITS, maxContainsTermLength: 10 };
        expect(containsTermProblem("abc", limits)).toBeNull();
        expect(containsTermProblem("ab", limits)).toMatch(/3 to 10/);
        expect(containsTermProblem("a".repeat(11), limits)).toMatch(/3 to 10/);
        expect(containsTermProblem("caf\u00e9x", limits)).toMatch(/may hold only/);
        expect(containsTermProblem(" abc", limits)).toMatch(/start or end with a space/);
        expect(containsTermProblem("a.b-/", limits)).toMatch(/at least 3 letters or digits/);
        expect(containsTermProblem("a b.usd", limits)).toBeNull();
    });

    it("limits the number of contains terms", () => {
        const filters = { ...defaultAuditFilters(), contains: ["aaa", "bbb", "ccc", "ddd"] };
        expect(auditFilterProblems(filters, DEFAULT_LIMITS, now)).toContain(
            "At most 3 contains terms."
        );
    });

    it("limits the window to the reported number of days", () => {
        const filters = {
            ...defaultAuditFilters(),
            range: { type: "relative", amount: 40, unit: "day" } as const,
        };
        expect(auditFilterProblems(filters, DEFAULT_LIMITS, now)).toContain(
            "The time range can span at most 31 days."
        );
        expect(
            auditFilterProblems(filters, { ...DEFAULT_LIMITS, maxQueryWindowDays: 60 }, now)
        ).toEqual([]);
    });

    it("checks the id and enumerated classes", () => {
        const filters = {
            ...defaultAuditFilters(),
            actions: ["bad action"],
            databaseIds: ["x"],
            apiKeyIds: ["short"],
            outcomes: ["maybe"],
            users: ["a\u0000b"],
        };
        const problems = auditFilterProblems(filters, DEFAULT_LIMITS, now);
        expect(problems).toHaveLength(5);
    });
});

describe("request body and responses", () => {
    it("sends named filters and no query text, leaving out the groups when none is chosen", () => {
        const now = Date.parse("2026-11-17T12:00:00.123Z");
        const body = buildStartBody(
            { ...defaultAuditFilters(), users: ["alice"], contains: ["report"] },
            now
        );
        expect(
            buildStartBody({ ...defaultAuditFilters(), logGroups: ["a1"] }, now).logGroups
        ).toEqual(["a1"]);
        expect(body).toEqual({
            startTime: "2026-11-16T12:00:00Z",
            endTime: "2026-11-17T12:00:00Z",
            includeProbes: false,
            sort: "desc",
            users: ["alice"],
            contains: ["report"],
        });
        expect(Object.keys(body)).not.toContain("query");
    });

    it("reads the log groups and limits, with defaults for what is missing", () => {
        const result = normalizeLogGroups({
            logGroups: [{ key: "auditActions", label: "Actions" }, { label: "no key is dropped" }],
            actions: [
                {
                    name: "assetUpdate",
                    kind: "write",
                    entityType: "asset",
                    logGroup: "auditActions",
                },
                { name: "other", logGroup: "elsewhere" },
            ],
            entityTypes: ["asset"],
            limits: { maxQueryWindowDays: 14, maxContainsTerms: "many" },
        });
        expect(result.groups.map((g) => g.key)).toEqual(["auditActions"]);
        expect(result.groups[0].actions?.map((a) => a.name)).toEqual(["assetUpdate"]);
        expect(result.groups[0].entityTypes).toEqual(["asset"]);
        expect(result.limits.maxQueryWindowDays).toBe(14);
        expect(result.limits.maxContainsTerms).toBe(DEFAULT_LIMITS.maxContainsTerms);
        expect(result.outcomes).toEqual(["success", "failed", "denied"]);
    });

    it("reads a poll page and drops what is not a row", () => {
        const page = normalizeQueryPage({
            status: "Running",
            pollAfterMs: 500,
            Items: [{ action: "a" }, "text", null, 5],
            statistics: { recordsMatched: 3, recordsScanned: "x", candidateLimitReached: true },
            NextToken: "tok",
            warnings: ["widerScan", 3],
        });
        expect(page.rows).toEqual([{ action: "a" }]);
        // A raw prefilter count is never read, even when an older server sends it.
        expect(page.statistics).toEqual({ candidateLimitReached: true });
        expect(page.nextToken).toBe("tok");
        expect(page.warnings).toEqual(["widerScan"]);
        expect(isTerminal(page.status)).toBe(false);
        expect(isTerminal("Complete")).toBe(true);
    });
});

describe("property filter and range conversion", () => {
    it("converts filters to tokens and back", () => {
        const filters = {
            ...defaultAuditFilters(),
            users: ["alice", "bob"],
            contains: ["report"],
            outcomes: ["denied"],
        };
        const query = filtersToQuery(filters);
        expect(query.tokens).toHaveLength(4);
        expect(queryToFilters(query, defaultAuditFilters())).toEqual(filters);
    });

    it("ignores free text, blank and repeated tokens", () => {
        const next = queryToFilters(
            {
                operation: "and",
                tokens: [
                    { value: "free text", operator: ":" },
                    { propertyKey: "users", operator: "=", value: "  " },
                    { propertyKey: "users", operator: "=", value: "alice" },
                    { propertyKey: "users", operator: "=", value: "alice" },
                    { propertyKey: "nonsense", operator: "=", value: "x" },
                ],
            },
            defaultAuditFilters()
        );
        expect(next.users).toEqual(["alice"]);
    });

    it("maps the picker's value to a range the server takes", () => {
        expect(pickerValueToRange({ type: "relative", amount: 2, unit: "week" })).toEqual({
            type: "relative",
            amount: 14,
            unit: "day",
        });
        expect(pickerValueToRange({ type: "relative", amount: 1, unit: "month" })).toBeNull();
        const absolute = pickerValueToRange({
            type: "absolute",
            startDate: "2026-11-01T00:00:00+00:00",
            endDate: "2026-11-02T00:00:00.000+00:00",
        });
        expect(absolute).toEqual({
            type: "absolute",
            startDate: "2026-11-01T00:00:00Z",
            endDate: "2026-11-02T00:00:00Z",
        });
        expect(pickerValueToRange(rangeToPickerValue(absolute as AuditTimeRange))).toEqual(
            absolute
        );
    });

    it("offers only quick ranges within the window limit", () => {
        expect(relativeOptions(1).map((o) => o.key)).toEqual(["15-minute", "1-hour", "24-hour"]);
        expect(relativeOptions(31)).toHaveLength(5);
    });
});

describe("record times", () => {
    it("reads the Logs Insights form as UTC, whatever the browser's zone", () => {
        expect(auditTimeMs("2026-11-17 01:00:00.000")).toBe(Date.parse("2026-11-17T01:00:00Z"));
        expect(auditTimeMs("2026-11-17T01:00:00+02:00")).toBe(Date.parse("2026-11-16T23:00:00Z"));
        expect(auditTimeMs("garbage")).toBeNaN();
        expect(auditTimeMs(null)).toBeNaN();
    });
});

describe("hostile text", () => {
    it("makes control, bidi and zero-width characters visible", () => {
        expect(visibleText("a\u202eb\u200bc\u0007d\ne")).toBe("a\\u202eb\\u200bc\\u0007d\\u000ae");
        expect(visibleText(null)).toBe("-");
        expect(visibleText("<img src=x onerror=alert(1)>")).toBe("<img src=x onerror=alert(1)>");
    });
});

describe("long-poll loop", () => {
    const page = (status: string, extra: object = {}): PollResult => ({
        ok: true,
        page: { status, statistics: {}, rows: [], warnings: [], ...extra },
    });

    const harness = (results: PollResult[], cancelAfter = Infinity) => {
        let clock = 0;
        const sleeps: number[] = [];
        const updates: string[] = [];
        let calls = 0;
        return {
            sleeps,
            updates,
            calls: () => calls,
            deps: {
                fetchPage: async () => results[Math.min(calls++, results.length - 1)],
                sleep: async (ms: number) => {
                    sleeps.push(ms);
                    clock += ms;
                },
                now: () => clock,
                isCancelled: () => calls >= cancelAfter,
                onUpdate: (p: { status: string }) => updates.push(p.status),
            },
        };
    };

    it("polls until a terminal state and honours pollAfterMs with a floor", async () => {
        const h = harness([
            page("Running", { pollAfterMs: 1000 }),
            page("Running"),
            page("Complete"),
        ]);
        const outcome = await runPollLoop(h.deps, { waitSeconds: 20 });
        expect(outcome.kind).toBe("terminal");
        expect(h.updates).toEqual(["Running", "Running", "Complete"]);
        expect(h.sleeps).toEqual([1000, MIN_POLL_GAP_MS]);
    });

    it("stops at once on a refusal", async () => {
        const h = harness([{ ok: false, message: "Query not found", status: 404 }]);
        const outcome = await runPollLoop(h.deps, { waitSeconds: 20 });
        expect(outcome).toEqual({ kind: "error", message: "Query not found", status: 404 });
        expect(h.calls()).toBe(1);
    });

    it("retries a dropped connection, then reports it", async () => {
        const h = harness([{ ok: false, message: "network" }]);
        const outcome = await runPollLoop(h.deps, { waitSeconds: 20 });
        expect(outcome.kind).toBe("error");
        expect(h.calls()).toBe(4);
    });

    it("recovers when a retry succeeds", async () => {
        const h = harness([{ ok: false, message: "network" }, page("Complete")]);
        expect((await runPollLoop(h.deps, { waitSeconds: 20 })).kind).toBe("terminal");
    });

    it("ends when cancelled and when the client deadline passes", async () => {
        const cancelled = harness([page("Running")], 2);
        expect((await runPollLoop(cancelled.deps, { waitSeconds: 20 })).kind).toBe("cancelled");
        const slow = harness([page("Running", { pollAfterMs: 30000 })]);
        expect((await runPollLoop(slow.deps, { waitSeconds: 20, deadlineMs: 70000 })).kind).toBe(
            "clientTimeout"
        );
    });
});
