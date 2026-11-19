/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Checks a search against the limits the server reports (`GET /audit/log-groups`), so the page tells
 * the user what is wrong before a request. These checks mirror the server's value classes
 * (`common/auditSearch/queryBuilder.py`, `containsFilter.py`) and are advisory: the server validates
 * every request again and decides.
 */

import { AuditFilters, AuditSearchLimits } from "./auditSearchTypes";
import { MAX_VALUES_PER_FILTER, rangeBounds } from "./auditSearchUrl";

/** Limits the server applies when it reports none (its defaults). */
export const DEFAULT_LIMITS: AuditSearchLimits = {
    maxQueryWindowDays: 31,
    maxRows: 1000,
    maxContainsTerms: 3,
    maxContainsTermLength: 64,
    longPollWaitSeconds: 20,
};

export const CONTAINS_MIN_LENGTH = 3;
/** The characters a contains term may hold (ASCII only: audit JSON is ASCII-escaped). */
export const CONTAINS_CHARSET = /^[A-Za-z0-9 ._:@+/-]+$/;
export const CONTAINS_CHARSET_TEXT = "letters, digits, space and . _ : @ + / -";

const ACTION = /^[A-Za-z][A-Za-z0-9_]{0,63}$/;
const ENTITY_TYPE = /^[A-Za-z][A-Za-z0-9]{0,63}$/;
const ID = /^[-_A-Za-z0-9]{3,64}$/;
const TOKEN_ID = /^[A-Za-z0-9-]{8,64}$/;
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u001f\u007f-\u009f]/;

const OUTCOMES = ["success", "failed", "denied"];

const DAY_MS = 86_400_000;

/** The reason a contains term is refused, or null when the server would take it. */
export const containsTermProblem = (term: string, limits: AuditSearchLimits): string | null => {
    if (term.length < CONTAINS_MIN_LENGTH || term.length > limits.maxContainsTermLength) {
        return `A contains term has ${CONTAINS_MIN_LENGTH} to ${limits.maxContainsTermLength} characters.`;
    }
    if (!CONTAINS_CHARSET.test(term)) {
        return `A contains term may hold only ${CONTAINS_CHARSET_TEXT}.`;
    }
    if (term !== term.trim()) {
        return "A contains term cannot start or end with a space.";
    }
    if ((term.match(/[A-Za-z0-9]/g) || []).length < CONTAINS_MIN_LENGTH) {
        return `A contains term needs at least ${CONTAINS_MIN_LENGTH} letters or digits.`;
    }
    return null;
};

/** The reason a value is refused for a list filter, or null. */
export const valueProblem = (
    filter: keyof AuditFilters,
    value: string,
    limits: AuditSearchLimits
): string | null => {
    const bad = (label: string) => `"${value.slice(0, 40)}" is not a valid ${label}.`;
    switch (filter) {
        case "actions":
            return ACTION.test(value) ? null : bad("action");
        case "entityTypes":
            return ENTITY_TYPE.test(value) ? null : bad("entity type");
        case "entityIds":
            return value.length >= 1 && value.length <= 1024 && !CONTROL.test(value)
                ? null
                : bad("entity ID");
        case "users":
            return value.length >= 1 && value.length <= 256 && !CONTROL.test(value)
                ? null
                : bad("user");
        case "databaseIds":
            return ID.test(value) ? null : bad("database ID");
        case "assetIds":
            return ID.test(value) ? null : bad("asset ID");
        case "apiKeyIds":
            return TOKEN_ID.test(value) ? null : bad("API key ID");
        case "requestIds":
            return TOKEN_ID.test(value) ? null : bad("request ID");
        case "outcomes":
            return OUTCOMES.includes(value) ? null : bad("outcome");
        case "contains":
            return containsTermProblem(value, limits);
        default:
            return null;
    }
};

const LIST_KEYS: (keyof AuditFilters)[] = [
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
];

/** Every problem with `filters`, as sentences for the user. Empty when the server would take it. */
export const auditFilterProblems = (
    filters: AuditFilters,
    limits: AuditSearchLimits,
    now: number = Date.now()
): string[] => {
    const problems: string[] = [];
    for (const key of [...LIST_KEYS, "logGroups" as keyof AuditFilters]) {
        const values = filters[key] as string[];
        if (values.length > MAX_VALUES_PER_FILTER) {
            problems.push(`At most ${MAX_VALUES_PER_FILTER} values per filter.`);
        }
    }
    for (const key of LIST_KEYS) {
        for (const value of filters[key] as string[]) {
            const problem = valueProblem(key, value, limits);
            if (problem) problems.push(problem);
        }
    }
    if (filters.contains.length > limits.maxContainsTerms) {
        problems.push(`At most ${limits.maxContainsTerms} contains terms.`);
    }
    const { start, end } = rangeBounds(filters.range, now);
    if (!Number.isFinite(start) || !Number.isFinite(end) || start >= end) {
        problems.push("The start of the time range must be before its end.");
    } else if (end - start > limits.maxQueryWindowDays * DAY_MS) {
        problems.push(`The time range can span at most ${limits.maxQueryWindowDays} days.`);
    }
    return Array.from(new Set(problems));
};
