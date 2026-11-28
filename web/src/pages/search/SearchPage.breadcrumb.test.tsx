/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search page's breadcrumb trail shows where the page sits and ends with the page itself, named as
 * its h1 is, with no step that links back to the page: the all-databases page (`#/assets/`) is the
 * trail on its own, and a database's page sits under Databases.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import createWrapper from "@cloudscape-design/components/test-utils/dom";
import * as APIService from "../../services/APIService";
import SearchPage from "./SearchPage";
import { __resetCache } from "../../features/orchestration/permissions/useAllowedRoutes";
import {
    ADMIN_ROUTES,
    allowedRoutesResponse,
} from "../../features/orchestration/permissions/templateRoleRoutes.testdata";
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({ fetchAllowedApiRoutes: jest.fn() }));
// The registry uses import.meta.glob, which Jest cannot parse; the page title helper does not use it.
jest.mock("../../searchPlugin/core/SearchProviderRegistry", () => ({
    SearchProviderRegistry: { getInstance: () => ({}) },
}));
jest.mock("../../searchPlugin", () => ({
    SearchTabsHost: () => null,
    searchPageTitle: (databaseId?: string) =>
        jest
            .requireActual("../../searchPlugin/components/SearchTabsHost")
            .searchPageTitle(databaseId),
}));

const renderAt = (path: string) =>
    render(
        <MemoryRouter initialEntries={[path]}>
            <Routes>
                <Route path="/assets" element={<SearchPage />} />
                <Route path="/databases/:databaseId/assets" element={<SearchPage />} />
                <Route path="/search/:databaseId/assets" element={<SearchPage />} />
            </Routes>
        </MemoryRouter>
    );

/** The trail as [text, href] pairs; Cloudscape renders the current page as the last item. */
const trail = () => {
    const group = createWrapper(document.body).findBreadcrumbGroup();
    if (!group) throw new Error("no breadcrumb group");
    return group
        .findBreadcrumbLinks()
        .map((link) => [
            link.getElement().textContent?.trim(),
            link.getElement().getAttribute("href"),
        ]);
};

describe("search page breadcrumb", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
        (APIService.fetchAllowedApiRoutes as jest.Mock).mockResolvedValue(
            allowedRoutesResponse(ADMIN_ROUTES)
        );
    });

    it("is the page alone on the all-databases page, named as its h1", async () => {
        renderAt("/assets");
        await waitFor(() => expect(APIService.fetchAllowedApiRoutes).toHaveBeenCalled());
        const items = trail();
        expect(items.map(([text]) => text)).toEqual([`${Synonyms.Assets} and Files`]);
        expect(screen.queryByText("Search")).toBeNull();
        // No step links to the page the user is on.
        expect(items.some(([, href]) => href === "#/assets/")).toBe(false);
    });

    it.each(["/databases/db-a/assets", "/search/db-a/assets"])(
        "puts a database's page under Databases, ending with the page: %s",
        async (path) => {
            renderAt(path);
            await waitFor(() => expect(APIService.fetchAllowedApiRoutes).toHaveBeenCalled());
            const items = trail();
            expect(items.map(([text]) => text)).toEqual([
                Synonyms.Databases,
                `${Synonyms.Assets} for db-a`,
            ]);
            expect(items[0][1]).toBe("#/databases/");
            expect(
                items.some(
                    ([, href]) => href === "#/assets/" || href === "#/databases/db-a/assets/"
                )
            ).toBe(false);
        }
    );
});
