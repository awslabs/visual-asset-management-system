/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, fireEvent } from "@testing-library/react";
import SearchableSelect from "./SearchableSelect";

const OPTIONS = [
    { value: "a1", label: "Building model", detail: "a1" },
    { value: "a2", label: "Terrain scan", detail: "a2" },
];

describe("SearchableSelect", () => {
    it("shows the selected option's label", () => {
        render(
            <SearchableSelect options={OPTIONS} value="a2" onChange={jest.fn()} ariaLabel="Asset" />
        );
        expect(screen.getByLabelText("Asset")).toHaveTextContent("Terrain scan");
    });

    it("filters options by the typed query and selects one", () => {
        const onChange = jest.fn();
        render(
            <SearchableSelect options={OPTIONS} value="" onChange={onChange} ariaLabel="Asset" />
        );
        fireEvent.click(screen.getByLabelText("Asset"));
        fireEvent.change(screen.getByPlaceholderText("Type to search…"), {
            target: { value: "terrain" },
        });
        expect(screen.queryByText("Building model")).not.toBeInTheDocument();
        fireEvent.click(screen.getByText("Terrain scan"));
        expect(onChange).toHaveBeenCalledWith("a2");
    });

    it("renders a leading option first", () => {
        const onChange = jest.fn();
        render(
            <SearchableSelect
                options={OPTIONS}
                value="/"
                onChange={onChange}
                ariaLabel="File"
                leadingOption={{ value: "/", label: "Whole asset (all files)" }}
            />
        );
        fireEvent.click(screen.getByLabelText("File"));
        // Appears both in the trigger (selected) and the open list.
        expect(screen.getAllByText("Whole asset (all files)").length).toBeGreaterThan(0);
    });
});
