/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A role that may not delete metadata is not offered the delete action on a stored field, in the
 * table or in bulk edit. A field added in this edit (not stored yet) can still be removed, and a
 * schema field can still be cleared, since neither calls delete.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import MetadataRow from "./MetadataRow";
import { BulkEditMode } from "./BulkEditMode";
import { MetadataRowState } from "./types/metadata.types";

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

const row = (overrides: Partial<MetadataRowState> = {}): MetadataRowState =>
    ({
        metadataKey: "color",
        metadataValue: "red",
        metadataValueType: "string",
        isEditing: false,
        hasChanges: false,
        isNew: false,
        isDeleted: false,
        editKey: "color",
        editValue: "red",
        editType: "string",
        ...overrides,
    } as MetadataRowState);

const noop = () => undefined;

const renderRow = (state: MetadataRowState, canDelete?: boolean) =>
    render(
        <table>
            <tbody>
                <MetadataRow
                    row={state}
                    index={0}
                    onEdit={noop}
                    onCancel={noop}
                    onSave={noop}
                    onDelete={noop}
                    onKeyChange={noop}
                    onTypeChange={noop}
                    onValueChange={noop}
                    canDelete={canDelete}
                />
            </tbody>
        </table>
    );

describe("metadata row delete by permission", () => {
    it("offers delete on a stored field by default", () => {
        renderRow(row());
        expect(screen.getByRole("button", { name: "Delete metadata" })).toBeInTheDocument();
    });

    it("does not offer delete on a stored field when the role may not delete", () => {
        renderRow(row(), false);
        expect(screen.queryByRole("button", { name: "Delete metadata" })).toBeNull();
    });

    it("still lets a field added in this edit be removed when the role may not delete", () => {
        renderRow(row({ isNew: true, hasChanges: true }), false);
        expect(screen.getByRole("button", { name: "Delete metadata" })).toBeInTheDocument();
    });

    it("bulk edit does not offer delete on a stored field when the role may not delete", () => {
        render(<BulkEditMode rows={[row()]} onSave={noop} onCancel={noop} canDelete={false} />);
        expect(screen.queryByRole("button", { name: "Delete row" })).toBeNull();
    });

    it("bulk edit offers delete on a stored field by default", () => {
        render(<BulkEditMode rows={[row()]} onSave={noop} onCancel={noop} />);
        expect(screen.getByRole("button", { name: "Delete row" })).toBeInTheDocument();
    });
});
