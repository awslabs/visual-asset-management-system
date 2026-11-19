/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Asset Metadata buttons are named by their visible labels (#1591, WCAG 2.5.3 Label in Name).
 *
 * "Add Row" (the metadata table's toolbar and empty state, both Bulk Edit toolbars and the Bulk Edit
 * empty state) was named "Add new row", "Schema Only" was named "Filter schema fields only", "Clear
 * Filters" was named "Clear all filters", "Commit Changes (n)" was named "Commit all changes", and Bulk
 * Edit's "Import CSV" and "Export CSV" were named "Import from CSV" and "Export to CSV", so a speech-input
 * user saying the label they see could not reach the button. Each button's accessible name now contains
 * its visible text.
 *
 * The row and the value inputs are stubbed so this file never loads `MapMetadataPicker` (react-map-gl is
 * ESM-only under Jest).
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import MetadataTable from "./MetadataTable";
import BulkEditMode from "./BulkEditMode";
import MetadataSearchFilter from "./MetadataSearchFilter";
import { MetadataRowState } from "./types/metadata.types";

jest.mock("../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));

jest.mock("./MetadataRow", () => ({
    __esModule: true,
    default: () => (
        <tr>
            <td>row</td>
        </tr>
    ),
}));

jest.mock("./valueTypes", () => {
    const stub = (name: string) => {
        const Component = () => <span data-testid={name} />;
        Component.displayName = name;
        return Component;
    };
    return {
        XYZInput: stub("XYZInput"),
        WXYZInput: stub("WXYZInput"),
        Matrix4x4Input: stub("Matrix4x4Input"),
        LLAInput: stub("LLAInput"),
        GeoPointInput: stub("GeoPointInput"),
        GeoJSONInput: stub("GeoJSONInput"),
        MapMetadataPicker: stub("MapMetadataPicker"),
        JSONTextInput: stub("JSONTextInput"),
        DateInput: stub("DateInput"),
        BooleanInput: stub("BooleanInput"),
        InlineControlledListInput: stub("InlineControlledListInput"),
        RawValueEditor: stub("RawValueEditor"),
    };
});

const row = {
    metadataKey: "material",
    metadataValue: "steel",
    metadataValueType: "string",
    isEditing: false,
    hasChanges: false,
    isNew: false,
    isDeleted: false,
    editKey: "material",
    editValue: "steel",
    editType: "string",
} as MetadataRowState;

const noop = () => undefined;

const renderTable = (rows: MetadataRowState[], { hasChanges = false, canCommit = false } = {}) =>
    render(
        <MetadataTable
            rows={rows}
            loading={false}
            editMode="normal"
            onCancelEdit={noop}
            onDeleteRow={noop}
            onAddNew={noop}
            onKeyChange={noop}
            onTypeChange={noop}
            onValueChange={noop}
            onToggleEditMode={noop}
            onCommitChanges={noop}
            onCancelAllChanges={noop}
            onRefresh={noop}
            hasChanges={hasChanges}
            canCommit={canCommit}
        />
    );

const renderBulk = (rows: MetadataRowState[], mode: "online" | "offline") =>
    render(
        <BulkEditMode
            rows={rows}
            mode={mode}
            restrictMetadata={false}
            onSave={jest.fn()}
            onCancel={jest.fn()}
        />
    );

/** A button's visible label: its text without a trailing count such as " (1)". */
const labelOf = (element: Element) =>
    (element.textContent || "")
        .replace(/\s*\(\d+\)$/, "")
        .replace(/\s+/g, " ")
        .trim();

const renderFilter = () =>
    render(
        <MetadataSearchFilter
            onSearchChange={noop}
            onTypeFilter={noop}
            onSchemaFilter={noop}
            totalRows={2}
            filteredRows={2}
        />
    );

/** Every button that shows `label`, each checked to be named by it. */
const expectNamedByLabel = (label: string) => {
    const buttons = screen
        .getAllByText((_, element) => element?.tagName === "BUTTON" && labelOf(element) === label)
        .map((element) => element.closest("button"));
    expect(buttons.length).toBeGreaterThan(0);
    for (const button of buttons) {
        expect(button).not.toBeNull();
        expect(button).toHaveAccessibleName(expect.stringContaining(label));
    }
    // The label alone finds the button, as speech input and screen-reader commands do.
    expect(screen.getAllByRole("button", { name: new RegExp(label) })).toHaveLength(buttons.length);
};

describe("Asset Metadata buttons are named by their visible labels (#1591)", () => {
    it("the table toolbar's Add Row", () => {
        renderTable([row]);
        expectNamedByLabel("Add Row");
    });

    it("the table's empty-state Add Row", () => {
        renderTable([]);
        expectNamedByLabel("Add Row");
    });

    it("the Bulk Edit toolbar's Add Row, online", () => {
        renderBulk([row], "online");
        expectNamedByLabel("Add Row");
    });

    it("the Bulk Edit toolbar's Add Row, offline", () => {
        renderBulk([row], "offline");
        expectNamedByLabel("Add Row");
    });

    it("the Bulk Edit empty-state Add Row", () => {
        renderBulk([], "online");
        expectNamedByLabel("Add Row");
    });

    it.each(["online", "offline"] as const)("the Bulk Edit toolbar's Import CSV, %s", (mode) => {
        renderBulk([row], mode);
        expectNamedByLabel("Import CSV");
    });

    it.each(["online", "offline"] as const)("the Bulk Edit toolbar's Export CSV, %s", (mode) => {
        renderBulk([row], mode);
        expectNamedByLabel("Export CSV");
    });

    it("the table's Commit Changes, with changes to commit", () => {
        renderTable([{ ...row, hasChanges: true, editValue: "iron" }], {
            hasChanges: true,
            canCommit: true,
        });
        expectNamedByLabel("Commit Changes");
    });

    it("the table's Commit Changes, held back by a validation error", () => {
        renderTable([{ ...row, hasChanges: true, editValue: "", metadataSchemaRequired: true }], {
            hasChanges: true,
            canCommit: false,
        });
        expectNamedByLabel("Commit Changes");
    });

    it("the search filter's Schema Only", () => {
        renderFilter();
        expectNamedByLabel("Schema Only");
    });

    it("the search filter's Clear Filters", () => {
        renderFilter();
        fireEvent.click(screen.getByRole("button", { name: "Schema Only" }));
        expectNamedByLabel("Clear Filters");
    });
});
