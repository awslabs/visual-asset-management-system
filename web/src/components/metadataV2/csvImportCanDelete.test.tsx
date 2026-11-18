/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Bulk Edit saves the whole set it shows (`replace_all`), so for a role that may not delete
 * metadata a CSV import must not drop or offer to delete a stored field: a stored field the CSV
 * leaves out is kept with its value, and a stored field the CSV lists stays a stored row.
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import BulkEditMode from "./BulkEditMode";
import { MetadataRecord, MetadataRowState } from "./types/metadata.types";
import {
    ADMIN_ROUTES,
    DATABASE_USER_ROUTES,
} from "../../features/orchestration/permissions/templateRoleRoutes.testdata";

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

const ASSET_METADATA_ROUTE = "/database/{databaseId}/assets/{assetId}/metadata";

/** Whether a template's route listing allows deleting asset metadata, as the asset page asks. */
const mayDeleteMetadata = (routes: { path: string; methods: string[] }[]) =>
    routes.some((r) => r.path === ASSET_METADATA_ROUTE && r.methods.includes("DELETE"));

const records: MetadataRecord[] = [
    { metadataKey: "free", metadataValue: "f", metadataValueType: "string" },
    { metadataKey: "kept", metadataValue: "k", metadataValueType: "string" },
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

const renderBulk = (canDelete: boolean) => {
    const onSave = jest.fn();
    render(
        <BulkEditMode
            rows={asRows(records)}
            mode="online"
            restrictMetadata={false}
            canDelete={canDelete}
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

const saveAll = async (onSave: jest.Mock) => {
    fireEvent.click(screen.getByRole("button", { name: "Save all changes" }));
    fireEvent.click(await screen.findByRole("button", { name: "Confirm Save" }));
    return onSave.mock.calls[0][0] as MetadataRowState[];
};

describe("Bulk Edit CSV import for a role that may not delete metadata", () => {
    it("keeps a stored field the CSV leaves out and offers no delete on stored fields", async () => {
        const canDelete = mayDeleteMetadata(DATABASE_USER_ROUTES);
        expect(canDelete).toBe(false);
        const onSave = renderBulk(canDelete);

        await importCSV(csv("free,string,f2", "extra,string,e"));

        // Only the field the CSV added can be removed again.
        expect(screen.getAllByRole("button", { name: "Delete row" })).toHaveLength(1);
        const saved = await saveAll(onSave);
        const live = saved.filter((row) => !row.isDeleted);
        expect(live.map((row) => row.metadataKey).sort()).toEqual(["extra", "free", "kept"]);
        expect(live.find((row) => row.metadataKey === "kept")?.metadataValue).toBe("k");
        expect(live.find((row) => row.metadataKey === "free")?.metadataValue).toBe("f2");
    });

    it("[control] replaces the fields with the CSV's for a role that may delete metadata", async () => {
        const onSave = renderBulk(mayDeleteMetadata(ADMIN_ROUTES));

        await importCSV(csv("free,string,f2", "extra,string,e"));

        const saved = await saveAll(onSave);
        expect(
            saved
                .filter((row) => !row.isDeleted)
                .map((row) => row.metadataKey)
                .sort()
        ).toEqual(["extra", "free"]);
    });
});
