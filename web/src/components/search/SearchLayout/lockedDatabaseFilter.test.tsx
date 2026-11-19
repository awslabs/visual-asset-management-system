/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * On a database's search page the Database filter is locked to that database and shows it as the
 * selected value, in the full and the reduced sidebar alike. The route locks the filter with its
 * `value` only (no `values` list), as ModernSearchContainer writes it.
 */
import React from "react";
import { render, waitFor } from "@testing-library/react";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import BasicFiltersPanel from "./BasicFiltersPanel";
import ReducedFiltersPanel from "./ReducedFiltersPanel";
import { fetchAllDatabases, fetchTags } from "../../../services/APIService";
import Synonyms from "../../../synonyms";
import type { SearchFilters } from "../types";

jest.mock("../../../services/APIService", () => ({
    fetchAllDatabases: jest.fn(),
    fetchTags: jest.fn(),
}));

const lockedToDbA: SearchFilters = {
    _rectype: { label: "Files", value: "file" },
    str_databaseid: { label: "db-a", value: "db-a" },
};

/** The Database multiselect: its selected tokens and the text its trigger shows. */
const databaseControl = (container: HTMLElement) => {
    const field = createWrapper(container)
        .findAllFormFields()
        .find((f) => f.findLabel()?.getElement().textContent === Synonyms.Database);
    const multiselect = field?.findControl()?.findMultiselect();
    if (!multiselect) throw new Error("Database multiselect not rendered");
    return {
        tokens: multiselect.findTokens().map((token) => token.findLabel().getElement().textContent),
        trigger: multiselect.findTrigger().getElement().textContent,
        description: field?.findDescription()?.getElement().textContent,
    };
};

describe("the locked Database filter", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (fetchAllDatabases as jest.Mock).mockResolvedValue([
            { databaseId: "db-a" },
            { databaseId: "db-b" },
        ]);
        (fetchTags as jest.Mock).mockResolvedValue([]);
    });

    it("shows the database it is locked to in the full sidebar", async () => {
        const { container } = render(
            <BasicFiltersPanel
                filters={lockedToDbA}
                onFilterChange={jest.fn()}
                recordType="file"
                databaseLocked
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        const control = databaseControl(container);
        expect(control.trigger).toBe("db-a");
        expect(control.description).toBe("Locked by URL parameter");
    });

    it("shows the database it is locked to in the reduced sidebar", async () => {
        const { container } = render(
            <ReducedFiltersPanel
                filters={lockedToDbA}
                onFilterChange={jest.fn()}
                recordType="file"
                databaseLocked
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        const control = databaseControl(container);
        expect(control.trigger).toBe("db-a");
    });

    it("still reads a multi-database choice from its values", async () => {
        const { container } = render(
            <BasicFiltersPanel
                filters={{
                    ...lockedToDbA,
                    str_databaseid: {
                        label: "db-a, db-b",
                        value: "db-a",
                        values: ["db-a", "db-b"],
                    },
                }}
                onFilterChange={jest.fn()}
                recordType="file"
            />
        );
        await waitFor(() => expect(fetchAllDatabases).toHaveBeenCalled());
        const control = databaseControl(container);
        expect(control.tokens).toEqual(["db-a", "db-b"]);
        // Unlocked, the trigger keeps its placeholder and the choice shows as tokens below it.
        expect(control.trigger).toBe(`All ${Synonyms.databases}`);
    });
});
