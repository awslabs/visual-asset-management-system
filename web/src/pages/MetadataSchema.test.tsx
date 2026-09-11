/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * "File Type Restriction" is a property of file metadata and file attributes only. The create/edit
 * modal already offers the field for just those two entity types and clears it for the others, so a
 * database, asset or asset-link schema cannot hold one — yet the listing table declared the column
 * unconditionally, so those three tabs showed a column reading "None" on every row for a property
 * their entity type does not have.
 *
 * The column now follows the selected tab. It stays on "All", where file schemas are listed and the
 * value is real data rather than an empty placeholder.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import MetadataSchemaPage from "./MetadataSchema";
import { MetadataSchema, MetadataSchemaEntityType } from "../components/metadataSchema/types";

// A database must be selected or the page renders its selection prompt instead of the table.
jest.mock("react-router-dom", () => ({
    ...jest.requireActual("react-router-dom"),
    useParams: () => ({ databaseId: "db1" }),
    useNavigate: () => jest.fn(),
}));

// The selectors reach for database listings the table cases do not need.
jest.mock("../components/selectors/DatabaseSelectorWithModal", () => () => null);
jest.mock("../components/selectors/DatabaseSelectionRequired", () => () => null);

jest.mock("../services/MetadataSchemaService", () => ({
    fetchMetadataSchemas: jest.fn(),
    createMetadataSchema: jest.fn(),
    updateMetadataSchema: jest.fn(),
    deleteMetadataSchema: jest.fn(),
}));

const schema = (
    schemaName: string,
    entityType: MetadataSchemaEntityType,
    fileKeyTypeRestriction?: string
): MetadataSchema =>
    ({
        metadataSchemaId: `id-${schemaName}`,
        databaseId: "db1",
        metadataSchemaEntityType: entityType,
        schemaName,
        ...(fileKeyTypeRestriction ? { fileKeyTypeRestriction } : {}),
        fields: { fields: [] },
        enabled: true,
    } as MetadataSchema);

const SCHEMAS = [
    schema("db-schema", "databaseMetadata"),
    schema("asset-schema", "assetMetadata"),
    schema("link-schema", "assetLinkMetadata"),
    schema("file-schema", "fileMetadata", "*.glb"),
    schema("attr-schema", "fileAttribute", "*.laz"),
];

const COLUMN = "File Type Restriction";

const service = () =>
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    require("../services/MetadataSchemaService");

const openTab = async (label: RegExp) => {
    const tab = await screen.findByRole("tab", { name: label });
    await userEvent.click(tab);
};

describe("MetadataSchema page — File Type Restriction column", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        // This service resolves to `{ Items }` rather than the `[ok, data]` tuple most of
        // APIService uses; the page reads `response.Items`.
        service().fetchMetadataSchemas.mockResolvedValue({ Items: SCHEMAS });
    });

    const renderPage = async () => {
        render(<MetadataSchemaPage />);
        // The listing has to have arrived, or an absent column proves nothing.
        await waitFor(() => expect(screen.getByText("db-schema")).toBeInTheDocument());
    };

    it("shows the column on All, where file schemas are listed", async () => {
        await renderPage();
        expect(screen.getByRole("columnheader", { name: COLUMN })).toBeInTheDocument();
        // Positive control: the value is really rendered, so the column is carrying data here.
        expect(screen.getByText("*.glb")).toBeInTheDocument();
    });

    it.each([
        ["Database Metadata", /Database Metadata/],
        ["Asset Metadata", /Asset Metadata/],
        ["Asset Link Metadata", /Asset Link Metadata/],
    ])("hides the column on the %s tab", async (_label, pattern) => {
        await renderPage();
        await openTab(pattern);
        await waitFor(() =>
            expect(screen.queryByRole("columnheader", { name: COLUMN })).toBeNull()
        );
    });

    it.each([
        ["File Metadata", /File Metadata/],
        ["File Attribute", /File Attribute/],
    ])("keeps the column on the %s tab", async (_label, pattern) => {
        await renderPage();
        await openTab(pattern);
        await waitFor(() =>
            expect(screen.getByRole("columnheader", { name: COLUMN })).toBeInTheDocument()
        );
    });

    it("still shows the other columns on a tab where the restriction is hidden", async () => {
        // Control: hiding one column must not drop the rest of the table.
        await renderPage();
        await openTab(/Database Metadata/);
        await waitFor(() =>
            expect(screen.queryByRole("columnheader", { name: COLUMN })).toBeNull()
        );
        for (const header of ["Schema Name", "Entity Type", "Fields", "Status", "Actions"]) {
            expect(screen.getByRole("columnheader", { name: header })).toBeInTheDocument();
        }
    });
});
