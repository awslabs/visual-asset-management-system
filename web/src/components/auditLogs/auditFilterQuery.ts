/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The conversion between the page's filters and the tokens of the Cloudscape property filter, and
 * between the filters' time range and the date-range picker's value. Both directions are plain
 * functions so they can be tested without rendering.
 */

import type { PropertyFilterProps } from "@cloudscape-design/components/property-filter";
import type { DateRangePickerProps } from "@cloudscape-design/components/date-range-picker";
import {
    AUDIT_LIST_FILTERS,
    AuditFilters,
    AuditListFilterKey,
    AuditLogGroupsResult,
    AuditTimeRange,
} from "./auditSearchTypes";
import { MAX_VALUES_PER_FILTER } from "./auditSearchUrl";

const KEY_SET = new Set<string>(AUDIT_LIST_FILTERS.map((f) => f.key));

/** The property filter's tokens for `filters`: one token per value, in the order of the filters. */
export const filtersToQuery = (filters: AuditFilters): PropertyFilterProps.Query => ({
    operation: "and",
    tokens: AUDIT_LIST_FILTERS.flatMap(({ key }) =>
        filters[key].map((value) => ({
            propertyKey: key,
            operator: key === "contains" ? ":" : "=",
            value,
        }))
    ),
});

/**
 * `filters` with the lists replaced by what `query` holds. A token that names no known filter (free
 * text, which the page disables) is ignored; repeated values count once; each list is bounded.
 */
export const queryToFilters = (
    query: PropertyFilterProps.Query,
    base: AuditFilters
): AuditFilters => {
    const next: AuditFilters = { ...base };
    for (const { key } of AUDIT_LIST_FILTERS) next[key] = [];
    for (const token of query.tokens) {
        const key = (token as { propertyKey?: string }).propertyKey;
        const value = typeof token.value === "string" ? token.value.trim() : "";
        if (!key || !KEY_SET.has(key) || !value) continue;
        const list = next[key as AuditListFilterKey];
        if (!list.includes(value) && list.length < MAX_VALUES_PER_FILTER) list.push(value);
    }
    return next;
};

/** The property filter's filtering properties; every property takes `=` except contains. */
export const filteringProperties = (): PropertyFilterProps.FilteringProperty[] =>
    AUDIT_LIST_FILTERS.map(({ key, label }) => ({
        key,
        propertyLabel: label,
        groupValuesLabel: `${label} values`,
        operators: key === "contains" ? [":"] : ["="],
    }));

/**
 * The values the server's registry offers, as suggestions for the property filter. Actions and
 * entity types of every group the caller may search are offered, whichever group is chosen, because
 * a record's own group decides which apply and the server rechecks every filter.
 */
export const filteringOptions = (
    groups: AuditLogGroupsResult | null
): PropertyFilterProps.FilteringOption[] => {
    if (!groups) return [];
    const options: PropertyFilterProps.FilteringOption[] = [];
    const seen = new Set<string>();
    const add = (propertyKey: string, value: string, label?: string) => {
        const id = `${propertyKey}\u0000${value}`;
        if (seen.has(id)) return;
        seen.add(id);
        options.push({ propertyKey, value, label: label ?? value });
    };
    for (const group of groups.groups) {
        add("logGroups", group.key, group.label);
        for (const action of group.actions ?? []) add("actions", action.name);
        for (const type of group.entityTypes ?? []) add("entityTypes", type);
        for (const action of group.actions ?? []) {
            if (action.entityType) add("entityTypes", action.entityType);
        }
    }
    for (const outcome of groups.outcomes) add("outcomes", outcome);
    return options.sort((a, b) =>
        a.propertyKey === b.propertyKey
            ? String(a.label).localeCompare(String(b.label))
            : String(a.propertyKey).localeCompare(String(b.propertyKey))
    );
};

// ---------------------------------------------------------------------------------------------
// Time range <-> date-range picker
// ---------------------------------------------------------------------------------------------

const withoutMillis = (date: Date): string => date.toISOString().replace(/\.\d{3}Z$/, "Z");

/** The picker's value for `range`. Absolute instants are shown in UTC (the picker offset is 0). */
export const rangeToPickerValue = (range: AuditTimeRange): DateRangePickerProps.Value => {
    if (range.type === "relative") {
        return {
            type: "relative",
            amount: range.amount,
            unit: range.unit,
            key: `${range.amount}-${range.unit}`,
        };
    }
    return {
        type: "absolute",
        startDate: range.startDate.replace(/Z$/, "+00:00"),
        endDate: range.endDate.replace(/Z$/, "+00:00"),
    };
};

/** The range a picker value names, or null when it names none the server takes. */
export const pickerValueToRange = (
    value: DateRangePickerProps.Value | null
): AuditTimeRange | null => {
    if (!value) return null;
    if (value.type === "absolute") {
        const start = Date.parse(value.startDate);
        const end = Date.parse(value.endDate);
        if (Number.isNaN(start) || Number.isNaN(end)) return null;
        return {
            type: "absolute",
            startDate: withoutMillis(new Date(start)),
            endDate: withoutMillis(new Date(end)),
        };
    }
    switch (value.unit) {
        case "minute":
        case "hour":
        case "day":
            return { type: "relative", amount: value.amount, unit: value.unit };
        case "second":
            return {
                type: "relative",
                amount: Math.max(1, Math.ceil(value.amount / 60)),
                unit: "minute",
            };
        case "week":
            return { type: "relative", amount: value.amount * 7, unit: "day" };
        default:
            // A month or a year is longer than any window the server allows.
            return null;
    }
};

/** The quick choices the picker offers, kept to those within the server's window limit. */
export const relativeOptions = (maxWindowDays: number): DateRangePickerProps.RelativeOption[] =>
    [
        { key: "15-minute", amount: 15, unit: "minute", days: 15 / 1440 },
        { key: "1-hour", amount: 1, unit: "hour", days: 1 / 24 },
        { key: "24-hour", amount: 24, unit: "hour", days: 1 },
        { key: "7-day", amount: 7, unit: "day", days: 7 },
        { key: "30-day", amount: 30, unit: "day", days: 30 },
    ]
        .filter((option) => option.days <= maxWindowDays)
        .map(({ key, amount, unit }) => ({
            key,
            amount,
            unit: unit as DateRangePickerProps.TimeUnit,
            type: "relative" as const,
        }));
