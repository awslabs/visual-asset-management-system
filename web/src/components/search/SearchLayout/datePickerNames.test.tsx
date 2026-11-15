/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The calendar button of each date picker says which date it chooses and, once one is picked, which
 * date is selected: the Search page's date modified filter (one date, or a start and an end date)
 * and the metadata date field.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import AdvancedFiltersPanel from "./AdvancedFiltersPanel";
import DateInput from "../../metadataV2/valueTypes/DateInput";
import { datePickerStrings } from "../../../common/helpers/datePickerStrings";

const renderDateFilter = (operator: string, value: string | string[]) =>
    render(
        <AdvancedFiltersPanel
            filters={{ date_lastmodified_filter: { operator, value } } as any}
            onFilterChange={jest.fn()}
            loading={false}
            recordType="file"
        />
    );

describe("date picker calendar buttons", () => {
    it("names the date modified filter's calendar button", () => {
        renderDateFilter(">", "");
        expect(
            screen.getByRole("button", { name: "Choose date modified", hidden: true })
        ).toBeInTheDocument();
    });

    it("names the start and end calendar buttons of a date range", () => {
        renderDateFilter("between", ["", ""]);
        expect(
            screen.getByRole("button", { name: "Choose start date", hidden: true })
        ).toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: "Choose end date", hidden: true })
        ).toBeInTheDocument();
    });

    it("names the metadata date field's calendar button after the field", () => {
        render(<DateInput value="" onChange={jest.fn()} ariaLabel="Release date" />);
        expect(
            screen.getByRole("button", { name: "Choose date for Release date", hidden: true })
        ).toBeInTheDocument();
    });

    it("says which date is selected", () => {
        const { openCalendarAriaLabel } = datePickerStrings("start date");
        expect(openCalendarAriaLabel?.("January 2, 2026")).toBe(
            "Choose start date, selected date is January 2, 2026"
        );
        expect(openCalendarAriaLabel?.(null)).toBe("Choose start date");
    });
});
