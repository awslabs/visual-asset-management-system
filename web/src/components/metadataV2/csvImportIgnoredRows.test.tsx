/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Bulk Edit CSV import keeps schema fields at their current values (#1012). A CSV row that sets a
 * schema field to a different value is not applied, and the import now lists those rows with their
 * line numbers instead of dropping them silently. A row that repeats the current value is not
 * listed, so re-importing an unchanged export shows no notice.
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import BulkEditMode from "./BulkEditMode";
import { MetadataRecord, MetadataRowState } from "./types/metadata.types";
import { importFromCSV } from "./utils/csvHelpers";

jest.mock("../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
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

const records: MetadataRecord[] = [
    {
        metadataKey: "declared",
        metadataValue: "x",
        metadataValueType: "string",
        metadataSchemaName: "s",
        metadataSchemaField: true,
    },
    {
        metadataKey: "title",
        metadataValue: "t",
        metadataValueType: "string",
        metadataSchemaName: "s",
        metadataSchemaField: true,
    },
    {
        metadataKey: "free",
        metadataValue: "f",
        metadataValueType: "string",
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

const csv = (...rows: string[]) => ["Metadata Key,Value Type,Metadata Value", ...rows].join("\n");

const NOTICE = /imported rows? (was|were) ignored/;

const renderBulk = (onSave = jest.fn()) => {
    render(
        <BulkEditMode
            rows={asRows(records)}
            mode="online"
            restrictMetadata={false}
            onSave={onSave}
            onCancel={jest.fn()}
        />
    );
    return onSave;
};

const importCSV = async (content: string) => {
    fireEvent.click(screen.getByRole("button", { name: "Import from CSV" }));
    const dialog = screen
        .getAllByRole("dialog")
        .find((d) => within(d).queryByText("Import Metadata from CSV")) as HTMLElement;
    const fileInput = dialog.querySelector('input[type="file"]') as HTMLInputElement;
    await act(async () => {
        fireEvent.change(fileInput, {
            target: { files: [new File([content], "m.csv", { type: "text/csv" })] },
        });
    });
    await act(async () => {
        fireEvent.click(within(dialog).getByRole("button", { name: "Import" }));
    });
    await waitFor(() => expect(within(dialog).queryByText("Import Errors:")).toBeNull());
};

describe("Bulk Edit CSV import of schema fields", () => {
    it("lists the schema-field rows it did not apply, with their line numbers", async () => {
        const onSave = renderBulk();

        await importCSV(
            csv("free,string,f2", "declared,string,changed", "title,string,t", "extra,string,e")
        );

        expect(await screen.findByText("1 imported row was ignored")).toBeInTheDocument();
        expect(screen.getByText(/^This row sets a schema field,/)).toBeInTheDocument();
        expect(screen.getByText(/The field keeps its current value;/)).toBeInTheDocument();
        expect(screen.queryByText(/These rows set schema fields/)).not.toBeInTheDocument();
        expect(screen.getByText("declared (line 3)")).toBeInTheDocument();
        expect(screen.queryByText(/^title \(line/)).not.toBeInTheDocument();

        fireEvent.click(screen.getByRole("button", { name: "Save all changes" }));
        fireEvent.click(await screen.findByRole("button", { name: "Confirm Save" }));
        const saved = onSave.mock.calls[0][0] as MetadataRowState[];
        expect(saved.find((row) => row.metadataKey === "declared")?.metadataValue).toBe("x");
        expect(saved.find((row) => row.metadataKey === "free")?.metadataValue).toBe("f2");
        expect(saved.find((row) => row.metadataKey === "extra")?.metadataValue).toBe("e");
    });

    it("counts every ignored row in the notice", async () => {
        renderBulk();

        await importCSV(csv("declared,string,changed", "title,string,other"));

        expect(await screen.findByText("2 imported rows were ignored")).toBeInTheDocument();
        expect(screen.getByText(/^These rows set schema fields,/)).toBeInTheDocument();
        expect(screen.getByText(/The fields keep their current values;/)).toBeInTheDocument();
        expect(screen.queryByText(/This row sets a schema field/)).not.toBeInTheDocument();
        expect(screen.getByText("declared (line 2)")).toBeInTheDocument();
        expect(screen.getByText("title (line 3)")).toBeInTheDocument();
    });

    it("shows no notice when the CSV repeats the schema fields' current values", async () => {
        renderBulk();

        await importCSV(csv("declared,string,x", "title,string,t", "free,string,f2"));

        expect(screen.queryByText(NOTICE)).not.toBeInTheDocument();
    });

    it("clears the notice when it is dismissed", async () => {
        renderBulk();
        await importCSV(csv("declared,string,changed"));
        await screen.findByText("1 imported row was ignored");

        fireEvent.click(screen.getByRole("button", { name: "Dismiss ignored rows notice" }));

        await waitFor(() => expect(screen.queryByText(NOTICE)).not.toBeInTheDocument());
    });
});

describe("importFromCSV ignoredSchemaRows", () => {
    it("names each schema-field row with a different value and its line", () => {
        const { errors, ignoredSchemaRows } = importFromCSV(
            csv("declared,string,changed", "free,string,f2", "title,string,t"),
            asRows(records)
        );

        expect(errors).toEqual([]);
        expect(ignoredSchemaRows).toEqual([{ key: "declared", line: 2 }]);
    });

    it("lists nothing for rows that are not schema fields", () => {
        const { ignoredSchemaRows } = importFromCSV(
            csv("free,string,f2", "extra,string,e"),
            asRows(records)
        );

        expect(ignoredSchemaRows).toEqual([]);
    });
});
