/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The width the metadata table keeps for its value column.
 *
 * The table uses a fixed layout: the key, type and actions columns declare their widths and the value
 * column takes what is left. In a container narrower than those three, such as the file details panel,
 * nothing is left, so the table declares a minimum width and its wrapper scrolls sideways. jsdom
 * computes no layout, so this checks the declarations the browser lays the table out from.
 *
 * The row is stubbed so this file never loads `MapMetadataPicker` (react-map-gl is ESM-only under Jest).
 */

import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import MetadataTable from "./MetadataTable";
import DateInput from "./valueTypes/DateInput";
import { MetadataRowState } from "./types/metadata.types";

jest.mock("./MetadataRow", () => ({
    __esModule: true,
    default: () => (
        <tr>
            <td>row</td>
        </tr>
    ),
}));

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

const px = (value: string) => {
    expect(value).toMatch(/^\d+px$/);
    return parseInt(value, 10);
};

describe("date value inside the scrolling table", () => {
    // The table's wrapper scrolls, so a dropdown positioned inside it is limited to the table's own
    // height. The calendar is rendered against the viewport instead, as the Selects in the rows are.
    it("renders its calendar in a portal outside the table wrapper", () => {
        const { container } = render(
            <div data-testid="scroller" style={{ overflowX: "auto" }}>
                <DateInput value="" onChange={noop} ariaLabel="Release date" />
            </div>
        );

        fireEvent.click(screen.getByRole("button", { name: "Choose date for Release date" }));

        const calendar = screen.getByRole("dialog");
        expect(screen.getByTestId("scroller")).not.toContainElement(calendar);
        expect(container).not.toContainElement(calendar);
    });
});

describe("MetadataTable column widths", () => {
    it("keeps room for the value column and scrolls sideways when the container is narrower", () => {
        render(
            <MetadataTable
                rows={[row]}
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
                hasChanges={false}
                canCommit={false}
                isFileAttribute
            />
        );

        const table = screen.getByRole("table");
        expect(table.parentElement).toHaveStyle({ overflowX: "auto" });

        const [key, type, value, actions] = Array.from(table.querySelectorAll("col"));
        expect(value.style.width).toBe("");
        const declared = px(key.style.minWidth) + px(type.style.width) + px(actions.style.width);
        expect(px(table.style.minWidth) - declared).toBeGreaterThanOrEqual(180);
    });
});
