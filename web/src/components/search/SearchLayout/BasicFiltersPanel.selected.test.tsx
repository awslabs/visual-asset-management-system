/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A multiselect filter shows what it filters on: the Database filter a database's search page locks
 * with a single `value` names that database, not "All databases" (#1253).
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import BasicFiltersPanel, { selectedOptionsOf } from "./BasicFiltersPanel";

jest.mock("../../../services/APIService", () => ({
    fetchAllDatabases: () => Promise.resolve([{ databaseId: "smoke-db" }, { databaseId: "other" }]),
    fetchTags: () => Promise.resolve([]),
}));

describe("selectedOptionsOf", () => {
    it("lists a multiselect's values", () => {
        expect(selectedOptionsOf({ value: "a", values: ["a", "b"] })).toEqual([
            { label: "a", value: "a" },
            { label: "b", value: "b" },
        ]);
    });

    it("falls back to a single value", () => {
        expect(selectedOptionsOf({ value: "smoke-db" })).toEqual([
            { label: "smoke-db", value: "smoke-db" },
        ]);
    });

    it("selects nothing for no filter, an empty value or all", () => {
        expect(selectedOptionsOf(undefined)).toEqual([]);
        expect(selectedOptionsOf(null)).toEqual([]);
        expect(selectedOptionsOf({ value: "" })).toEqual([]);
        expect(selectedOptionsOf({ value: "all" })).toEqual([]);
        expect(selectedOptionsOf({ value: "x", values: [] })).toEqual([{ label: "x", value: "x" }]);
    });
});

describe("BasicFiltersPanel locked Database filter", () => {
    it("names the database it is locked to", async () => {
        render(
            <BasicFiltersPanel
                filters={{ str_databaseid: { label: "smoke-db", value: "smoke-db" } }}
                onFilterChange={jest.fn()}
                databaseLocked={true}
                recordType="asset"
            />
        );
        expect((await screen.findAllByText("smoke-db")).length).toBeGreaterThan(0);
        expect(screen.queryByText(/^All .*databases$/i)).not.toBeInTheDocument();
    });

    it("reads All databases with nothing selected", async () => {
        render(<BasicFiltersPanel filters={{}} onFilterChange={jest.fn()} recordType="asset" />);
        expect(await screen.findByText(/^All .*databases$/i)).toBeInTheDocument();
    });
});
