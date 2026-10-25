/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Databases page serves a caller who may only list databases. The Compliance Admin template
 * grants `GET /database` and no other database route: the page lists the role's database with the
 * Compliance column link, offers no Create, Edit or Delete Selected action the caller cannot call,
 * and never requests the administrator-only bucket listing (`GET /buckets`), which the
 * create/edit modal reads only once it is opened.
 */
import React from "react";
import { render, screen, waitFor, fireEvent, within, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import Databases from "./Databases";
import Synonyms from "../synonyms";

jest.mock("../services/APIService", () => ({
    fetchAllDatabases: jest.fn(),
    fetchBuckets: jest.fn(),
    createDatabase: jest.fn(),
    updateDatabase: jest.fn(),
    deleteElement: jest.fn(),
}));
jest.mock("../services/ComplianceService", () => ({
    fetchComplianceSchemas: jest.fn(() => Promise.resolve([true, []])),
    getDatabaseBindings: jest.fn(() => Promise.resolve([true, { databaseId: "db1" }])),
    bindSchemaToDatabase: jest.fn(),
    unbindSchemaFromDatabase: jest.fn(),
}));
jest.mock("../services/appCache", () => ({ appCache: { getItem: () => null } }));
jest.mock("../components/metadataV2/MetadataContainer", () => ({
    MetadataContainer: () => null,
}));
jest.mock("../components/search/SearchResults/DatabaseMapThumbnail", () => () => null);

// The routes the caller may call, as "METHOD path-template" strings.
let mockAllowed: string[] = [];
jest.mock("../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: () => ({
        loading: false,
        can: (method: string, path: string) => mockAllowed.includes(`${method} ${path}`),
    }),
}));

const { fetchAllDatabases, fetchBuckets } = jest.requireMock("../services/APIService");

const LIST_ONLY = ["GET /database", "GET /compliance/state/{databaseId}"];
const ADMINISTRATOR = [
    ...LIST_ONLY,
    "POST /database",
    "PUT /database/{databaseId}",
    "DELETE /database/{databaseId}",
    "GET /buckets",
];

const mount = () =>
    render(
        <MemoryRouter>
            <Databases />
        </MemoryRouter>
    );

const selectRow = (databaseId: string) => {
    const row = screen.getByText(databaseId).closest("tr") as HTMLElement;
    fireEvent.click(within(row).getByRole("checkbox"));
};

// The page's Create button; the create modal stays in the DOM while closed and carries a submit
// button with the same name.
const pageCreateButtons = () =>
    screen
        .queryAllByRole("button", { name: `Create ${Synonyms.Database}` })
        .filter((button) => !button.closest('[role="dialog"]'));

beforeEach(() => {
    jest.clearAllMocks();
    jest.spyOn(console, "log").mockImplementation(() => undefined);
    fetchAllDatabases.mockResolvedValue([
        { databaseId: "db1", description: "compliance database", defaultBucketId: "b1" },
    ]);
    fetchBuckets.mockResolvedValue({
        Items: [{ bucketId: "b1", bucketName: "bucket-one", baseAssetsPrefix: "/" }],
    });
});

afterEach(() => jest.restoreAllMocks());

describe(`${Synonyms.Databases} page for a caller who may only list ${Synonyms.databases}`, () => {
    beforeEach(() => {
        mockAllowed = LIST_ONLY;
    });

    it("lists the database with its compliance link and no error", async () => {
        mount();
        await screen.findByText("db1");
        expect(screen.getByRole("link", { name: "Compliance" })).toHaveAttribute(
            "href",
            "#/databases/db1/compliance"
        );
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(screen.queryByText(/Not Authorized/i)).not.toBeInTheDocument();
    });

    it("offers no create, edit or delete action", async () => {
        mount();
        await screen.findByText("db1");
        expect(pageCreateButtons()).toHaveLength(0);
        expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Delete Selected" })).not.toBeInTheDocument();
    });

    it("never requests the bucket listing, with a row selected or not", async () => {
        mount();
        await screen.findByText("db1");
        selectRow("db1");
        // Let any effect the selection mounts run before asserting on its absence.
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });
        expect(fetchBuckets).not.toHaveBeenCalled();
    });
});

describe(`${Synonyms.Databases} page for an administrator`, () => {
    beforeEach(() => {
        mockAllowed = ADMINISTRATOR;
    });

    it("offers create, edit and delete", async () => {
        mount();
        await screen.findByText("db1");
        expect(pageCreateButtons()).toHaveLength(1);
        expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Delete Selected" })).toBeInTheDocument();
    });

    it("reads the bucket listing only once the create modal opens", async () => {
        mount();
        await screen.findByText("db1");
        expect(fetchBuckets).not.toHaveBeenCalled();
        fireEvent.click(pageCreateButtons()[0]);
        await waitFor(() => expect(fetchBuckets).toHaveBeenCalledTimes(1));
    });
});
