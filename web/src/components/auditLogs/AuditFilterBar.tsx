/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo } from "react";
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
import AuditInfoPopover from "./AuditInfoPopover";

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
    /** Live status of the search (progress, stopped), shown under the Search and Stop buttons. */
    status?: React.ReactNode;
    /** An extra info icon beside the filter label (the Authentication group note). */
    filterInfo?: React.ReactNode;
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
    status,
    filterInfo,
}) => {
    const limits = groups?.limits;
    const options = useMemo(() => filteringOptions(groups), [groups]);
    const groupLabels = useMemo(
        () => new Map((groups?.groups ?? []).map((g) => [g.key, g.label])),
        [groups]
    );
    const properties = useMemo(() => filteringProperties(groupLabels), [groupLabels]);
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
            <div
                data-testid="audit-filter-layout"
                style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: "16px 24px",
                    alignItems: "flex-start",
                }}
            >
                <div data-testid="audit-filter-column" style={{ flex: "1 1 300px", minWidth: 0 }}>
                    <SpaceBetween size="s">
                        <FormField
                            label="Filters"
                            info={
                                <SpaceBetween direction="horizontal" size="xxs">
                                    <AuditInfoPopover
                                        testId="audit-filters-info"
                                        ariaLabel="About the filters"
                                        header="Filters"
                                    >
                                        <SpaceBetween size="xs">
                                            <span>
                                                Choose a property, then a value. Values of one
                                                property are alternatives; different properties all
                                                apply.
                                            </span>
                                            <span>
                                                Contains: {CONTAINS_MIN_LENGTH} to {maxLength}{" "}
                                                characters ({CONTAINS_CHARSET_TEXT}), up to{" "}
                                                {maxTerms} terms that must all match. Contains
                                                searches the identifiers, request parameters and
                                                details of a record, not the values it changed or
                                                its previous values (an asset name on a create
                                                record is a changed value).
                                            </span>
                                        </SpaceBetween>
                                    </AuditInfoPopover>
                                    {filterInfo}
                                </SpaceBetween>
                            }
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
                        <Checkbox
                            checked={!filters.includeProbes}
                            onChange={({ detail }) =>
                                onChange({ ...filters, includeProbes: !detail.checked })
                            }
                            description="Permission checks the web app makes to decide what to show are left out."
                        >
                            Hide permission-probe records
                        </Checkbox>
                    </SpaceBetween>
                </div>

                <div
                    data-testid="audit-search-controls"
                    style={{
                        flex: "0 1 auto",
                        minWidth: 0,
                        display: "flex",
                        flexDirection: "column",
                        gap: "8px",
                    }}
                >
                    {/* Label row to label row: the controls start level with the filter input,
                        whatever the filter column holds below it (tokens, the probe checkbox). */}
                    <div
                        data-testid="audit-search-controls-row"
                        style={{
                            display: "flex",
                            flexWrap: "wrap",
                            gap: "16px 16px",
                            alignItems: "flex-start",
                        }}
                    >
                        <FormField
                            label="Time range"
                            info={
                                <AuditInfoPopover
                                    testId="audit-range-info"
                                    ariaLabel="About the time range"
                                    header="Time range"
                                >
                                    Times are in UTC. A range can span at most {maxDays} days.
                                    Records become searchable a short time after they are written.
                                </AuditInfoPopover>
                            }
                        >
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
                                    const sort =
                                        detail.selectedOption.value === "asc" ? "asc" : "desc";
                                    onChange({ ...filters, sort });
                                    onOrderChange?.(sort);
                                }}
                            />
                        </FormField>
                        {/* An empty label row (hidden from assistive technology) puts the buttons
                            on the input row, level with the picker and the order select. */}
                        <FormField label={<span aria-hidden="true">&nbsp;</span>}>
                            <SpaceBetween direction="horizontal" size="xs" alignItems="center">
                                {/* Always present, so starting a search adds nothing to the
                                    row's width; enabled while a search runs. */}
                                <Button
                                    onClick={onStop}
                                    iconName="status-stopped"
                                    formAction="none"
                                    disabled={!busy}
                                >
                                    Stop
                                </Button>
                                {/* The width of the button while it shows "Searching", so the
                                    loading state does not widen the row. */}
                                <span style={{ display: "inline-block", minWidth: "7rem" }}>
                                    <Button
                                        variant="primary"
                                        formAction="submit"
                                        fullWidth
                                        loading={busy}
                                        loadingText="Searching"
                                        disabled={
                                            problems.length > 0 || noGroups || groups === null
                                        }
                                    >
                                        Search
                                    </Button>
                                </span>
                                <AuditInfoPopover
                                    testId="audit-search-info"
                                    ariaLabel="About running a search"
                                    header="Running a search"
                                >
                                    <SpaceBetween size="xs">
                                        <span>
                                            A search reads CloudWatch audit logs and can take
                                            several seconds. Records appear when the search
                                            finishes. Choose Stop to end it early; a stopped search
                                            shows no records.
                                        </span>
                                        <span>
                                            A link to this page restores the filters and does not
                                            run the search.
                                        </span>
                                    </SpaceBetween>
                                </AuditInfoPopover>
                            </SpaceBetween>
                        </FormField>
                    </div>
                    {/* The status has its own line and takes the width of the controls without
                        adding to it (width 0, min-width 100%), so starting a search never reflows
                        the row. A longer text is cut, not wrapped. */}
                    <div
                        data-testid="audit-status-slot"
                        style={{
                            width: 0,
                            minWidth: "100%",
                            overflow: "hidden",
                            whiteSpace: "nowrap",
                            textOverflow: "ellipsis",
                            minHeight: 0,
                        }}
                    >
                        {status}
                    </div>
                </div>
            </div>
        </form>
    );
};

export default AuditFilterBar;
