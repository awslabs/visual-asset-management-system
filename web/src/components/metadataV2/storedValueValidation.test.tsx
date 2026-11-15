/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The editor checks a schema value only when the user changes it, as the API does (#968). A record
 * can hold a saved value outside a controlled list, for example one a pipeline wrote back, and that
 * value must not block saving an edit to another field. A value the user enters outside the list is
 * still refused, and required fields are still checked on the whole record.
 *
 * Covered: normal-mode Commit Changes, Bulk Edit Save All, and Bulk Edit CSV import.
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import BulkEditMode from "./BulkEditMode";
import MetadataContainer from "./MetadataContainer";
import { MetadataRecord, MetadataRowState } from "./types/metadata.types";
import { buildStoredMetadataValues, validateAllRows } from "./utils/validationHelpers";

jest.mock("../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));

jest.mock("../../services/MetadataService", () => ({
    createMetadata: jest.fn(),
    updateMetadata: jest.fn(() => Promise.resolve({ success: true })),
    deleteMetadata: jest.fn(),
}));

jest.mock("./hooks/useMetadataFetch", () => ({
    useMetadataFetch: jest.fn(),
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

const { useMetadataFetch } = jest.requireMock("./hooks/useMetadataFetch");

// A restricted record: `colour` holds a value a pipeline wrote outside the list red,blue
const records: MetadataRecord[] = [
    {
        metadataKey: "colour",
        metadataValue: "green",
        metadataValueType: "inline_controlled_list",
        metadataSchemaName: "s",
        metadataSchemaField: true,
        metadataSchemaControlledListKeys: ["red", "blue"],
    },
    {
        metadataKey: "declared",
        metadataValue: "x",
        metadataValueType: "string",
        metadataSchemaName: "s",
        metadataSchemaField: true,
        metadataSchemaRequired: true,
    },
];

const asRows = (data: MetadataRecord[]): MetadataRowState[] =>
    data.map((record) => ({
        ...record,
        metadataValue: record.metadataValue ?? "",
        isEditing: false,
        hasChanges: false,
        isNew: false,
        isDeleted: false,
        editKey: record.metadataKey,
        editValue: record.metadataValue ?? "",
        editType: record.metadataValueType,
    })) as MetadataRowState[];

beforeEach(() => {
    jest.clearAllMocks();
    useMetadataFetch.mockReturnValue({
        data: records,
        loading: false,
        error: null,
        refetch: jest.fn(() => Promise.resolve()),
        restrictMetadataOutsideSchemas: true,
        attributeCount: undefined,
        metadataCount: undefined,
    });
});

describe("normal mode Commit Changes", () => {
    it("is enabled after editing another field while a saved value is outside the list", async () => {
        render(<MetadataContainer entityType="asset" entityId="a1" databaseId="db1" />);

        const input = await screen.findByLabelText("declared value");
        fireEvent.change(input, { target: { value: "edited" } });

        const commit = await screen.findByRole("button", { name: "Commit all changes" });
        expect(commit).toBeEnabled();
    });

    it("stays disabled when a required field is cleared", async () => {
        render(<MetadataContainer entityType="asset" entityId="a1" databaseId="db1" />);

        const input = await screen.findByLabelText("declared value");
        fireEvent.change(input, { target: { value: "" } });

        const commit = await screen.findByRole("button", { name: "Commit all changes" });
        expect(commit).toBeDisabled();
    });
});

describe("Bulk Edit Save All", () => {
    const renderBulk = (onSave = jest.fn()) => {
        render(
            <BulkEditMode
                rows={asRows(records)}
                mode="online"
                restrictMetadata={true}
                onSave={onSave}
                onCancel={jest.fn()}
            />
        );
        return onSave;
    };

    const saveAll = () => screen.getByRole("button", { name: "Save all changes" });

    it("saves an edit to another field while a saved value is outside the list", async () => {
        const onSave = renderBulk();

        fireEvent.change(screen.getByLabelText("Value row 2"), { target: { value: "edited" } });

        expect(saveAll()).toBeEnabled();
        fireEvent.click(saveAll());
        expect(screen.queryByText(/Value must be one of/)).not.toBeInTheDocument();
        fireEvent.click(await screen.findByRole("button", { name: "Confirm Save" }));

        expect(onSave).toHaveBeenCalledTimes(1);
        const saved = onSave.mock.calls[0][0] as MetadataRowState[];
        expect(saved.find((row) => row.metadataKey === "colour")?.metadataValue).toBe("green");
        expect(saved.find((row) => row.metadataKey === "declared")?.metadataValue).toBe("edited");
    });

    it("refuses a value the user enters outside the list", () => {
        renderBulk();

        fireEvent.change(screen.getByLabelText("Value row 1"), { target: { value: "purple" } });

        expect(screen.getByText("Value must be one of: red, blue")).toBeInTheDocument();
        expect(saveAll()).toBeDisabled();
    });

    it("keeps checking required fields on the whole record", () => {
        renderBulk();

        fireEvent.change(screen.getByLabelText("Value row 2"), { target: { value: "" } });

        expect(saveAll()).toBeDisabled();
    });

    it("accepts an unchanged CSV re-import of a record with a saved value outside the list", async () => {
        const onSave = renderBulk();

        fireEvent.click(screen.getByRole("button", { name: "Import from CSV" }));
        const dialog = screen
            .getAllByRole("dialog")
            .find((d) => within(d).queryByText("Import Metadata from CSV")) as HTMLElement;
        const csv = [
            "Metadata Key,Value Type,Metadata Value",
            "colour,inline_controlled_list,green",
            "declared,string,x",
        ].join("\n");
        const fileInput = dialog.querySelector('input[type="file"]') as HTMLInputElement;
        await act(async () => {
            fireEvent.change(fileInput, {
                target: { files: [new File([csv], "m.csv", { type: "text/csv" })] },
            });
        });
        await act(async () => {
            fireEvent.click(within(dialog).getByRole("button", { name: "Import" }));
        });

        await waitFor(() => expect(within(dialog).queryByText("Import Errors:")).toBeNull());
        expect(screen.queryByText(/Value must be one of/)).not.toBeInTheDocument();
        expect(saveAll()).toBeEnabled();
        fireEvent.click(saveAll());
        fireEvent.click(await screen.findByRole("button", { name: "Confirm Save" }));
        expect(onSave).toHaveBeenCalledTimes(1);
    });
});

describe("validateAllRows with saved values", () => {
    it("checks a saved value outside the list only once the user changes it", () => {
        const rows = asRows(records);
        const stored = buildStoredMetadataValues(rows);

        expect(validateAllRows(rows, stored).isValid).toBe(true);
        expect(validateAllRows(rows).isValid).toBe(false);

        const changed = rows.map((row) =>
            row.metadataKey === "colour" ? { ...row, editValue: "purple", hasChanges: true } : row
        );
        expect(validateAllRows(changed, stored).errors.join()).toContain(
            "Value must be one of: red, blue"
        );
    });
});
