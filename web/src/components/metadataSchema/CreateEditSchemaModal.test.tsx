/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CreateEditSchemaModal } from "./CreateEditSchemaModal";
import { MetadataSchema, MetadataSchemaField } from "./types";

// The value-type inputs pull in maplibre through the shared barrel; the schema editor only needs
// them to exist.
jest.mock("../metadataV2/valueTypes", () => {
    const Stub = () => null;
    return {
        XYZInput: Stub,
        WXYZInput: Stub,
        Matrix4x4Input: Stub,
        LLAInput: Stub,
        JSONTextInput: Stub,
        DateInput: Stub,
        BooleanInput: Stub,
        InlineControlledListInput: Stub,
    };
});

const WARNING_HEADER = "Required fields apply to existing records";

const buildField = (
    metadataFieldKeyName: string,
    required: boolean,
    sequence: number
): MetadataSchemaField => ({
    metadataFieldKeyName,
    metadataFieldValueType: "string",
    required,
    sequence,
});

const buildSchema = (fields: any): MetadataSchema => {
    const schema: any = {
        metadataSchemaId: "schema-1",
        databaseId: "db-1",
        metadataSchemaEntityType: "assetMetadata",
        schemaName: "Asset Properties",
        fields,
        enabled: true,
    };

    return schema as MetadataSchema;
};

const renderModal = (editingSchema: MetadataSchema | null) =>
    render(
        <CreateEditSchemaModal
            visible={true}
            onDismiss={jest.fn()}
            onSubmit={jest.fn().mockResolvedValue(undefined)}
            editingSchema={editingSchema}
            databaseId="db-1"
        />
    );

const expandOnlyField = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole("button", { name: "Expand field" }));
};

const toggleRequired = async (user: ReturnType<typeof userEvent.setup>) => {
    await user.click(screen.getByRole("checkbox", { name: /this field is required/i }));
};

describe("CreateEditSchemaModal required-field warning", () => {
    it("warns when an existing optional field is changed to required, without blocking submit", async () => {
        const user = userEvent.setup();
        renderModal(buildSchema({ fields: [buildField("assetOwner", false, 1)] }));

        expect(screen.queryByText(WARNING_HEADER)).not.toBeInTheDocument();

        await expandOnlyField(user);
        await toggleRequired(user);

        expect(screen.getByText(WARNING_HEADER)).toBeInTheDocument();
        expect(
            screen.getByText(/assetOwner changed from optional to required/)
        ).toBeInTheDocument();
        // The warning is advisory -- the edit is still allowed to be saved.
        expect(screen.getByRole("button", { name: "Update Schema" })).toBeEnabled();
    });

    it("does not warn for a new field added in create mode", async () => {
        const user = userEvent.setup();
        renderModal(null);

        await user.click(screen.getByRole("button", { name: /add field/i }));
        await user.type(screen.getByPlaceholderText("Enter field name"), "assetOwner");
        await toggleRequired(user);

        expect(screen.getByRole("checkbox", { name: /this field is required/i })).toBeChecked();
        expect(screen.queryByText(WARNING_HEADER)).not.toBeInTheDocument();
    });

    it("does not warn for a new field added to an existing schema", async () => {
        const user = userEvent.setup();
        renderModal(buildSchema({ fields: [buildField("assetOwner", false, 1)] }));

        await user.click(screen.getByRole("button", { name: /add field/i }));
        await user.type(screen.getByPlaceholderText("Enter field name"), "reviewStatus");
        await toggleRequired(user);

        expect(screen.queryByText(WARNING_HEADER)).not.toBeInTheDocument();
    });

    it("does not warn for an already-required field, nor when required is turned off", async () => {
        const user = userEvent.setup();
        renderModal(buildSchema({ fields: [buildField("assetOwner", true, 1)] }));

        expect(screen.queryByText(WARNING_HEADER)).not.toBeInTheDocument();

        await expandOnlyField(user);
        expect(screen.queryByText(WARNING_HEADER)).not.toBeInTheDocument();

        await toggleRequired(user);

        expect(screen.getByRole("checkbox", { name: /this field is required/i })).not.toBeChecked();
        expect(screen.queryByText(WARNING_HEADER)).not.toBeInTheDocument();
    });

    it("compares against the original required flag when the schema arrives as a JSON string", async () => {
        const user = userEvent.setup();
        renderModal(buildSchema(JSON.stringify({ fields: [buildField("assetOwner", false, 1)] })));

        await expandOnlyField(user);
        await toggleRequired(user);

        expect(
            screen.getByText(/assetOwner changed from optional to required/)
        ).toBeInTheDocument();
    });

    it("attributes the warning by field name after an earlier field is removed", async () => {
        const user = userEvent.setup();
        renderModal(
            buildSchema({
                fields: [buildField("assetOwner", true, 1), buildField("reviewStatus", false, 2)],
            })
        );

        // Removing the first field shifts reviewStatus into position 0, which previously held a
        // required field -- a position-based comparison would report the wrong answer here.
        await user.click(screen.getAllByRole("button", { name: "Remove field" })[0]);
        await expandOnlyField(user);
        await toggleRequired(user);

        expect(
            screen.getByText(/reviewStatus changed from optional to required/)
        ).toBeInTheDocument();
        expect(screen.queryByText(/assetOwner changed/)).not.toBeInTheDocument();
    });
});
