/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search state in the URL's hash query, so a link reproduces a search. Reading is defensive: the
 * URL is typed or pasted by anyone, so unknown keys are ignored, every list is bounded, and a value
 * that is not text of a sane size is dropped. A link restores the filters and never runs the search.
 * The server validates every value again; this only keeps the page from sending junk.
 */

import { AUDIT_LIST_FILTERS, AuditFilters, AuditTimeRange } from "./auditSearchTypes";

/** At most this many values per list filter, as the server allows. */
export const MAX_VALUES_PER_FILTER = 20;
/** Longest value accepted from the URL (the longest the server accepts is 1,024: an entity id). */
export const MAX_URL_VALUE_LENGTH = 1024;

export const DEFAULT_RANGE = { type: "relative", amount: 24, unit: "hour" } as const;

export const defaultAuditFilters = (): AuditFilters => ({
    logGroups: [],
    actions: [],
    entityTypes: [],
    entityIds: [],
    users: [],
    apiKeyIds: [],
    databaseIds: [],
    assetIds: [],
    outcomes: [],
    requestIds: [],
    contains: [],
    includeProbes: false,
    sort: "desc",
    range: { ...DEFAULT_RANGE },
});

const UNIT_LETTER = { minute: "m", hour: "h", day: "d" } as const;
const LETTER_UNIT: Record<string, "minute" | "hour" | "day"> = {
    m: "minute",
    h: "hour",
    d: "day",
};

const ISO_INSTANT = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,3})?Z$/;

const hashQuery = (hash: string): URLSearchParams => {
    const index = hash.indexOf("?");
    return new URLSearchParams(index >= 0 ? hash.slice(index + 1) : "");
};

const isValidInstant = (value: string): boolean =>
    ISO_INSTANT.test(value) && !Number.isNaN(Date.parse(value));

/** The range a `rel` / `from` / `to` set of parameters names, or the default. */
const rangeFromParams = (query: URLSearchParams): AuditTimeRange => {
    const from = query.get("from") || "";
    const to = query.get("to") || "";
    if (isValidInstant(from) && isValidInstant(to) && Date.parse(from) < Date.parse(to)) {
        return { type: "absolute", startDate: from, endDate: to };
    }
    const relative = /^(\d{1,4})([mhd])$/.exec(query.get("rel") || "");
    if (relative && Number(relative[1]) > 0) {
        return { type: "relative", amount: Number(relative[1]), unit: LETTER_UNIT[relative[2]] };
    }
    return { ...DEFAULT_RANGE };
};

/** The filters a hash query (or a whole hash such as `#/auth/audit-log-search/?user=x`) names. */
export const parseAuditSearchUrl = (hashOrQuery: string): AuditFilters => {
    const query = hashOrQuery.includes("#")
        ? hashQuery(hashOrQuery)
        : new URLSearchParams(hashOrQuery.replace(/^\?/, ""));
    const filters = defaultAuditFilters();
    for (const { key, param } of AUDIT_LIST_FILTERS) {
        const values: string[] = [];
        for (const value of query.getAll(param)) {
            if (value.length === 0 || value.length > MAX_URL_VALUE_LENGTH) continue;
            if (!values.includes(value)) values.push(value);
            if (values.length >= MAX_VALUES_PER_FILTER) break;
        }
        filters[key] = values;
    }
    filters.includeProbes = query.get("probes") === "1";
    filters.sort = query.get("sort") === "asc" ? "asc" : "desc";
    filters.range = rangeFromParams(query);
    return filters;
};

/**
 * The query string (no leading `?`) that reproduces `filters`. Defaults are left out, so an
 * unfiltered page has a clean URL.
 */
export const serializeAuditFilters = (filters: AuditFilters): string => {
    const query = new URLSearchParams();
    for (const { key, param } of AUDIT_LIST_FILTERS) {
        for (const value of filters[key]) query.append(param, value);
    }
    if (filters.includeProbes) query.set("probes", "1");
    if (filters.sort === "asc") query.set("sort", "asc");
    const { range } = filters;
    if (range.type === "absolute") {
        query.set("from", range.startDate);
        query.set("to", range.endDate);
    } else if (range.amount !== DEFAULT_RANGE.amount || range.unit !== DEFAULT_RANGE.unit) {
        query.set("rel", `${range.amount}${UNIT_LETTER[range.unit]}`);
    }
    return query.toString();
};

/** The window `range` covers, as epoch milliseconds, relative to `now`. */
export const rangeBounds = (
    range: AuditTimeRange,
    now: number = Date.now()
): { start: number; end: number } => {
    if (range.type === "absolute") {
        return { start: Date.parse(range.startDate), end: Date.parse(range.endDate) };
    }
    const unitMs = { minute: 60_000, hour: 3_600_000, day: 86_400_000 }[range.unit];
    return { start: now - range.amount * unitMs, end: now };
};

/**
 * Writes `filters` into the hash query of the current route. A change of the form replaces the
 * history entry (Back leaves the page); `push` adds one, for a jump such as "records of this
 * request", so Back returns to the search it came from.
 */
export const writeAuditFiltersToUrl = (filters: AuditFilters, push = false): void => {
    const hash = window.location.hash || "#/";
    const base = hash.split("?")[0];
    const search = serializeAuditFilters(filters);
    const url = `${window.location.pathname}${window.location.search}${base}${
        search ? `?${search}` : ""
    }`;
    if (push) {
        window.history.pushState(window.history.state, "", url);
    } else {
        window.history.replaceState(window.history.state, "", url);
    }
};
