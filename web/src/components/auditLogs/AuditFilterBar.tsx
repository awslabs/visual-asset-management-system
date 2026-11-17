/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo } from "react";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import DateRangePicker, {
    DateRangePickerProps,
} from "@cloudscape-design/components/date-range-picker";
import FormField from "@cloudscape-design/components/form-field";
import PropertyFilter from "@cloudscape-design/components/property-filter";
import Select from "@cloudscape-design/components/select";
import SpaceBetween from "@cloudscape-design/components/space-between";
import {
    filteringOptions,
    filteringProperties,
    filtersToQuery,
    pickerValueToRange,
    queryToFilters,
    rangeToPickerValue,
    relativeOptions,
} from "./auditFilterQuery";
import { AuditFilters, AuditLogGroupsResult } from "./auditSearchTypes";
import {
    CONTAINS_CHARSET_TEXT,
    CONTAINS_MIN_LENGTH,
    auditFilterProblems,
} from "./auditSearchValidation";
import { rangeBounds } from "./auditSearchUrl";

const DAY_MS = 86_400_000;

const ORDER_OPTIONS = [
    { label: "Newest first", value: "desc" },
    { label: "Oldest first", value: "asc" },
];

const PICKER_STRINGS: DateRangePickerProps.I18nStrings = {
    todayAriaLabel: "Today",
    nextMonthAriaLabel: "Next month",
    previousMonthAriaLabel: "Previous month",
    customRelativeRangeDurationLabel: "Duration",
    customRelativeRangeDurationPlaceholder: "Enter a duration",
    customRelativeRangeOptionLabel: "Custom range",
    customRelativeRangeOptionDescription: "Set a custom range in the past",
    customRelativeRangeUnitLabel: "Unit of time",
    formatRelativeRange: (value) => {
        const unit = value.amount === 1 ? value.unit : `${value.unit}s`;
        return `Last ${value.amount} ${unit}`;
    },
    formatUnit: (unit, value) => (value === 1 ? unit : `${unit}s`),
    dateTimeConstraintText: "Dates are in UTC. Use the format YYYY/MM/DD and 24-hour time.",
    relativeModeTitle: "Relative range",
    absoluteModeTitle: "Absolute range",
    relativeRangeSelectionHeading: "Choose a range",
    startDateLabel: "Start date",
    endDateLabel: "End date",
    startTimeLabel: "Start time",
    endTimeLabel: "End time",
    clearButtonLabel: "Clear and dismiss",
    cancelButtonLabel: "Cancel",
    applyButtonLabel: "Apply",
};

export interface AuditFilterBarProps {
    filters: AuditFilters;
    onChange: (filters: AuditFilters) => void;
    /** What the caller may search; null while loading. */
    groups: AuditLogGroupsResult | null;
    onSearch: () => void;
    onStop: () => void;
    /** A search is starting or running. */
    busy: boolean;
    /** Another reading of the current results is in flight (order change). */
    disabled?: boolean;
    /** Called when the order changes, so results already shown can be read in the new order. */
    onOrderChange?: (sort: "asc" | "desc") => void;
}

/**
 * The search form: the property filter (log group, action, entity type and id, user, API key,
 * database, asset, outcome, request id and contains), the time range, the probe switch, the order
 * and the Search and Stop buttons. The options come from `GET /audit/log-groups`; the form derives
 * no permission and shows nothing that call did not return.
 */
export const AuditFilterBar: React.FC<AuditFilterBarProps> = ({
    filters,
    onChange,
    groups,
    onSearch,
    onStop,
    busy,
    disabled = false,
    onOrderChange,
}) => {
    const limits = groups?.limits;
    const options = useMemo(() => filteringOptions(groups), [groups]);
    const properties = useMemo(() => filteringProperties(), []);
    const query = useMemo(() => filtersToQuery(filters), [filters]);
    const allowedKeys = useMemo(() => new Set((groups?.groups ?? []).map((g) => g.key)), [groups]);

    const problems = useMemo(() => {
        if (!limits) return [];
        const list = auditFilterProblems(filters, limits);
        if (groups && filters.logGroups.some((key) => !allowedKeys.has(key))) {
            list.push("A log group you chose is not one you can search.");
        }
        return list;
    }, [filters, limits, groups, allowedKeys]);

    const maxDays = limits?.maxQueryWindowDays ?? 31;

    const isValidRange: DateRangePickerProps.ValidationFunction = (value) => {
        const range = pickerValueToRange(value);
        if (!range) {
            return {
                valid: false,
                errorMessage: "Choose a range in minutes, hours, days or weeks.",
            };
        }
        const { start, end } = rangeBounds(range);
        if (!(start < end)) {
            return { valid: false, errorMessage: "The start must be before the end." };
        }
        if (end - start > maxDays * DAY_MS) {
            return { valid: false, errorMessage: `A range can span at most ${maxDays} days.` };
        }
        return { valid: true };
    };

    const maxTerms = limits?.maxContainsTerms ?? 3;
    const maxLength = limits?.maxContainsTermLength ?? 64;
    const noGroups = groups !== null && groups.groups.length === 0;

    return (
        <form
            onSubmit={(event) => {
                event.preventDefault();
                if (!busy && problems.length === 0 && !noGroups) onSearch();
            }}
        >
            <SpaceBetween size="m">
                <FormField
                    label="Filters"
                    description="Choose a property, then a value. Values of one property are alternatives; different properties all apply."
                    constraintText={`Contains: ${CONTAINS_MIN_LENGTH} to ${maxLength} characters (${CONTAINS_CHARSET_TEXT}), up to ${maxTerms} terms that must all match.`}
                    errorText={
                        problems.length > 0 ? (
                            <ul style={{ margin: 0, paddingLeft: "1.25em" }}>
                                {problems.map((problem) => (
                                    <li key={problem}>{problem}</li>
                                ))}
                            </ul>
                        ) : undefined
                    }
                >
                    <PropertyFilter
                        query={query}
                        onChange={({ detail }) => onChange(queryToFilters(detail, filters))}
                        filteringProperties={properties}
                        filteringOptions={options}
                        disableFreeTextFiltering
                        hideOperations
                        tokenLimit={10}
                        filteringPlaceholder="Filter by log group, action, user, asset, request ID or contains text"
                        filteringAriaLabel="Filter audit records"
                        i18nStrings={{
                            filteringAriaLabel: "Filter audit records",
                            filteringPlaceholder:
                                "Filter by log group, action, user, asset, request ID or contains text",
                            clearFiltersText: "Clear filters",
                            removeTokenButtonAriaLabel: (token) =>
                                `Remove filter ${token.propertyLabel} ${token.value}`,
                            allPropertiesLabel: "All properties",
                            groupPropertiesText: "Properties",
                            groupValuesText: "Values",
                            operatorsText: "Operators",
                            operationAndText: "and",
                            operationOrText: "or",
                            operatorContainsText: "Contains",
                            operatorEqualsText: "Equals",
                            applyActionText: "Apply",
                            cancelActionText: "Cancel",
                            enteredTextLabel: (text) => `Use: "${text}"`,
                            tokenLimitShowMore: "Show more",
                            tokenLimitShowFewer: "Show fewer",
                            clearAriaLabel: "Clear",
                            editTokenHeader: "Edit filter",
                            propertyText: "Property",
                            operatorText: "Operator",
                            valueText: "Value",
                            formatToken: (token) =>
                                `${token.propertyLabel} ${token.operator} ${token.value}`,
                        }}
                    />
                </FormField>

                <SpaceBetween direction="horizontal" size="l" alignItems="end">
                    <FormField label="Time range" description="UTC">
                        <DateRangePicker
                            value={rangeToPickerValue(filters.range)}
                            onChange={({ detail }) => {
                                const range = pickerValueToRange(detail.value);
                                if (range) onChange({ ...filters, range });
                            }}
                            relativeOptions={relativeOptions(maxDays)}
                            isValidRange={isValidRange}
                            getTimeOffset={() => 0}
                            rangeSelectorMode="default"
                            i18nStrings={PICKER_STRINGS}
                            ariaLabel="Time range of the search"
                            placeholder="Filter by a date and time range"
                        />
                    </FormField>
                    <FormField label="Order">
                        <Select
                            selectedOption={
                                ORDER_OPTIONS.find((o) => o.value === filters.sort) ??
                                ORDER_OPTIONS[0]
                            }
                            options={ORDER_OPTIONS}
                            disabled={disabled}
                            ariaLabel="Order of the records"
                            onChange={({ detail }) => {
                                const sort = detail.selectedOption.value === "asc" ? "asc" : "desc";
                                onChange({ ...filters, sort });
                                onOrderChange?.(sort);
                            }}
                        />
                    </FormField>
                    <Checkbox
                        checked={!filters.includeProbes}
                        onChange={({ detail }) =>
                            onChange({ ...filters, includeProbes: !detail.checked })
                        }
                        description="Permission checks the web app makes to decide what to show are left out."
                    >
                        Hide permission-probe records
                    </Checkbox>
                    <SpaceBetween direction="horizontal" size="xs">
                        {busy ? (
                            <Button onClick={onStop} iconName="status-stopped">
                                Stop
                            </Button>
                        ) : null}
                        <Button
                            variant="primary"
                            formAction="submit"
                            loading={busy}
                            loadingText="Searching"
                            disabled={problems.length > 0 || noGroups || groups === null}
                        >
                            Search
                        </Button>
                    </SpaceBetween>
                </SpaceBetween>
                <Box variant="small" color="text-body-secondary">
                    A search reads CloudWatch audit logs and can take several seconds. A link to
                    this page restores the filters and does not run the search.
                </Box>
            </SpaceBetween>
        </form>
    );
};

export default AuditFilterBar;
