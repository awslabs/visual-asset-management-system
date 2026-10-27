/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Databases list carries the compliance page link beneath the database name, not in a column
 * of its own. The list already has many columns; a Compliance column (about 120 px) made it wider
 * than its container even at 1920 px. With the link under the name the list is no wider than
 * without compliance.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import { createDatabaseListDefinition } from "./DatabaseListDefinition";

const nameCell = (definition: any, item: any) => {
    const column = definition.columnDefinitions.find((c: any) => c.id === "databaseId");
    const CellWrapper = column.CellWrapper;
    return render(<CellWrapper item={item}>{item.databaseId}</CellWrapper>);
};

const columnIds = (definition: any) => definition.columnDefinitions.map((c: any) => c.id);

describe("the Databases list's compliance link", () => {
    it("is beneath the database name, with no Compliance column", () => {
        const definition: any = createDatabaseListDefinition({ showCompliance: true });
        expect(columnIds(definition)).not.toContain("compliance");
        expect(definition.visibleColumns).not.toContain("compliance");
        nameCell(definition, { databaseId: "db1" });
        expect(screen.getByRole("link", { name: "db1" })).toHaveAttribute(
            "href",
            "#/databases/db1/assets/"
        );
        expect(screen.getByRole("link", { name: "Compliance" })).toHaveAttribute(
            "href",
            "#/databases/db1/compliance"
        );
    });

    it("is not shown to a caller who may not open the compliance page", () => {
        const definition: any = createDatabaseListDefinition({ showCompliance: false });
        expect(columnIds(definition)).not.toContain("compliance");
        nameCell(definition, { databaseId: "db1" });
        expect(screen.getByRole("link", { name: "db1" })).toBeInTheDocument();
        expect(screen.queryByRole("link", { name: "Compliance" })).toBeNull();
    });

    it("the columns are those of a list without compliance", () => {
        const withCompliance: any = createDatabaseListDefinition({ showCompliance: true });
        const without: any = createDatabaseListDefinition({ showCompliance: false });
        expect(columnIds(withCompliance)).toEqual(columnIds(without));
        expect(withCompliance.visibleColumns).toEqual(without.visibleColumns);
    });
});
