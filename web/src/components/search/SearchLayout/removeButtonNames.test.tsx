/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Search page's icon-only remove buttons say what they remove: a metadata filter row's names its
 * field (or its value, or its position when both are empty), and a table column's names the column.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MetadataSearchPanel from "./MetadataSearchPanel";
import DraggableColumnList from "./DraggableColumnList";
import type { MetadataFilter } from "../types";

const filter = (key: string, value: string): MetadataFilter => ({
    key,
    value,
    operator: "=",
    type: "string",
});

describe("Search page remove buttons", () => {
    it("names each metadata filter row's remove button after the filter it removes", async () => {
        const user = userEvent.setup({ delay: null });
        const onRemoveFilter = jest.fn();
        render(
            <MetadataSearchPanel
                metadataFilters={[filter("product", "pump"), filter("", "steel"), filter("", "")]}
                onAddFilter={jest.fn()}
                onRemoveFilter={onRemoveFilter}
                onUpdateFilter={jest.fn()}
            />
        );
        expect(
            screen.getByRole("button", { name: "Remove metadata filter with value steel" })
        ).toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: "Remove metadata filter 3" })
        ).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Remove metadata filter product" }));
        expect(onRemoveFilter).toHaveBeenCalledWith(0);
    });

    it("names each column's remove button after the column", async () => {
        const user = userEvent.setup({ delay: null });
        const onColumnsChange = jest.fn();
        render(
            <DraggableColumnList
                selectedColumns={["str_assetname", "custom_field"]}
                availableColumns={[]}
                onColumnsChange={onColumnsChange}
            />
        );
        expect(
            screen.getByRole("button", { name: "Remove column custom_field" })
        ).toBeInTheDocument();
        await user.click(screen.getByRole("button", { name: "Remove column Asset Name" }));
        expect(onColumnsChange).toHaveBeenCalledWith(["custom_field"]);
    });
});
