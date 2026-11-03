/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The database list offers Create, Edit and Delete Selected only to a role whose API route
 * permissions allow them. Roles come from the shipped database-readonly and database-user templates
 * (their allowed-routes listings are in templateRoleRoutes.testdata.ts) and the administrator.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import * as APIService from "../services/APIService";
import Databases from "./Databases";
import { __resetCache } from "../features/orchestration/permissions/useAllowedRoutes";
import {
    ADMIN_ROUTES,
    DATABASE_READONLY_ROUTES,
    DATABASE_USER_ROUTES,
    allowedRoutesResponse,
} from "../features/orchestration/permissions/templateRoleRoutes.testdata";
import Synonyms from "../synonyms";

jest.mock("../services/APIService", () => ({
    fetchAllDatabases: jest.fn(),
    fetchAllowedApiRoutes: jest.fn(),
}));
jest.mock("../services/appCache", () => ({
    appCache: { getItem: () => ({ featuresEnabled: [] }) },
}));
jest.mock("../components/createupdate/CreateDatabase", () => ({
    __esModule: true,
    default: () => null,
}));
jest.mock("../components/metadataV2/MetadataContainer", () => ({ MetadataContainer: () => null }));
jest.mock("../components/search/SearchResults/DatabaseMapThumbnail", () => ({
    __esModule: true,
    default: () => null,
}));

const mockFetchAllDatabases = APIService.fetchAllDatabases as jest.Mock;
const mockFetchAllowedApiRoutes = APIService.fetchAllowedApiRoutes as jest.Mock;

async function renderAs(routes: any[]) {
    mockFetchAllowedApiRoutes.mockResolvedValue(allowedRoutesResponse(routes));
    render(
        <MemoryRouter>
            <Databases />
        </MemoryRouter>
    );
    await waitFor(() => expect(screen.getByText("db1")).toBeInTheDocument());
    // The permission check resolves after the list; the loading reason disappears once it has.
    const create = screen.getByRole("button", { name: `Create ${Synonyms.Database}` });
    await waitFor(() =>
        expect(create).not.toHaveAccessibleDescription("Checking your permissions.")
    );
    return create;
}

describe("Databases page actions by role", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
        mockFetchAllDatabases.mockResolvedValue([{ databaseId: "db1", description: "first" }]);
    });

    it.each([
        ["database-readonly", DATABASE_READONLY_ROUTES],
        ["database-user", DATABASE_USER_ROUTES],
    ])(
        "a %s role sees Create disabled with the reason and no Edit or Delete",
        async (_, routes) => {
            const create = await renderAs(routes);
            expect(create).toHaveAttribute("aria-disabled", "true");
            expect(create).toHaveAccessibleDescription(
                `Your role does not allow creating ${Synonyms.databases}.`
            );
            expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();
            expect(
                screen.queryByRole("button", { name: "Delete Selected" })
            ).not.toBeInTheDocument();
        }
    );

    it("the administrator sees Create enabled and the Edit and Delete Selected actions", async () => {
        const create = await renderAs(ADMIN_ROUTES);
        expect(create).not.toHaveAttribute("aria-disabled");
        expect(create).not.toBeDisabled();
        await waitFor(() =>
            expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument()
        );
        expect(screen.getByRole("button", { name: "Delete Selected" })).toBeInTheDocument();
    });
});
