/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A metadata filter row the user writes for a location field is theirs to edit and remove (#651).
 * The Map view no longer adds location rows, so no row is locked for its field name.
 */
import React from "react";
import { render, screen, within } from "@testing-library/react";
import MetadataSearchPanel from "./MetadataSearchPanel";

const rows = [
    { key: "Location", value: "*", operator: "=" as const, type: "string" as const },
    { key: "latitude", value: "38.9", operator: "=" as const, type: "string" as const },
    { key: "site", value: "Plant-7", operator: "=" as const, type: "string" as const },
];

describe("MetadataSearchPanel location rows (#651)", () => {
    it("leaves a location, latitude or longitude row editable and removable", () => {
        render(
            <MetadataSearchPanel
                metadataFilters={rows}
                onAddFilter={jest.fn()}
                onRemoveFilter={jest.fn()}
                onUpdateFilter={jest.fn()}
                onSearchModeChange={jest.fn()}
                onOperatorChange={jest.fn()}
            />
        );
        const fieldNames = screen.getAllByPlaceholderText("e.g., product", { exact: true });
        const values = screen.getAllByPlaceholderText("Enter value (use * for wildcard)");
        expect(fieldNames.map((input) => (input as HTMLInputElement).value)).toEqual([
            "Location",
            "latitude",
            "site",
        ]);
        fieldNames.forEach((input) => expect(input).toBeEnabled());
        values.forEach((input) => expect(input).toBeEnabled());

        for (const key of ["Location", "latitude", "site"]) {
            const remove = screen.getByRole("button", {
                name: `Remove metadata filter ${key}`,
                hidden: true,
            });
            expect(remove).toBeEnabled();
        }
    });

    it("offers the metadata mode and the operator unlocked", () => {
        const { container } = render(
            <MetadataSearchPanel
                metadataFilters={rows}
                onAddFilter={jest.fn()}
                onRemoveFilter={jest.fn()}
                onUpdateFilter={jest.fn()}
                metadataSearchMode="both"
                metadataOperator="AND"
                onSearchModeChange={jest.fn()}
                onOperatorChange={jest.fn()}
            />
        );
        expect(within(container).queryByText(/Locked to/)).toBeNull();
        expect(
            within(container).getByText("Choose where to search for your metadata terms")
        ).toBeInTheDocument();
        expect(
            within(container).getByText("Choose how to combine multiple metadata filters")
        ).toBeInTheDocument();
    });
});
