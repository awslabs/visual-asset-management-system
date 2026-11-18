/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search page gives its provider views the reasons Create Asset, Delete Selected and Unarchive
 * Selected are unavailable to the signed-in role. Roles come from the shipped database-readonly and database-user templates
 * (their allowed-routes listings are in templateRoleRoutes.testdata.ts) and the administrator.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import * as APIService from "../../services/APIService";
import SearchPage from "./SearchPage";
import { __resetCache } from "../../features/orchestration/permissions/useAllowedRoutes";
import {
    PERMISSIONS_FAILED_REASON,
    PERMISSIONS_LOADING_REASON,
} from "../../features/orchestration/permissions/routeDisabledReason";
import {
    ADMIN_ROUTES,
    DATABASE_READONLY_ROUTES,
    DATABASE_USER_ROUTES,
    allowedRoutesResponse,
} from "../../features/orchestration/permissions/templateRoleRoutes.testdata";
import Synonyms from "../../synonyms";

jest.mock("../../services/APIService", () => ({ fetchAllowedApiRoutes: jest.fn() }));

// The provider host passes the reasons to every provider body (SearchTabsHost.test.tsx); here it
// only reports what the page hands it.
const mockContainerProps: any[] = [];
jest.mock("../../searchPlugin", () => ({
    SearchTabsHost: (props: any) => {
        mockContainerProps.push(props);
        return null;
    },
    searchPageTitle: () => "Assets and Files",
}));

const mockFetchAllowedApiRoutes = APIService.fetchAllowedApiRoutes as jest.Mock;

async function reasonsFor(routes: any[]) {
    mockFetchAllowedApiRoutes.mockResolvedValue(allowedRoutesResponse(routes));
    render(
        <MemoryRouter>
            <SearchPage />
        </MemoryRouter>
    );
    await waitFor(() =>
        expect(
            mockContainerProps[mockContainerProps.length - 1].assetActionDisabledReasons.create
        ).not.toBe(PERMISSIONS_LOADING_REASON)
    );
    return mockContainerProps[mockContainerProps.length - 1].assetActionDisabledReasons;
}

describe("search page asset actions by role", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
        mockContainerProps.length = 0;
    });

    it("a database-readonly role may neither create, delete nor unarchive", async () => {
        expect(await reasonsFor(DATABASE_READONLY_ROUTES)).toEqual({
            create: `Your role does not allow creating ${Synonyms.assets}.`,
            delete: `Your role does not allow archiving or deleting ${Synonyms.assets}.`,
            unarchive: `Your role does not allow unarchiving ${Synonyms.assets}.`,
        });
    });

    it("a database-user role may create and delete but not unarchive, as its template says", async () => {
        expect(await reasonsFor(DATABASE_USER_ROUTES)).toEqual({
            create: undefined,
            delete: undefined,
            unarchive: `Your role does not allow unarchiving ${Synonyms.assets}.`,
        });
    });

    it("a role whose listing holds the unarchive route may create, delete and unarchive", async () => {
        // ADMIN_ROUTES leaves the unarchive route out on purpose (ViewAsset.permissions.test.tsx); a
        // database-admin role's listing reports it, as its template grants every method on /database.
        expect(
            await reasonsFor([
                ...ADMIN_ROUTES,
                {
                    path: "/database/{databaseId}/assets/{assetId}/unarchiveAsset",
                    methods: ["PUT"],
                    category: "test",
                },
            ])
        ).toEqual({ create: undefined, delete: undefined, unarchive: undefined });
    });

    it("the administrator listing without the unarchive route may create and delete only", async () => {
        expect(await reasonsFor(ADMIN_ROUTES)).toEqual({
            create: undefined,
            delete: undefined,
            unarchive: `Your role does not allow unarchiving ${Synonyms.assets}.`,
        });
    });

    it("says the permissions check failed, not that the role is refused, when it failed", async () => {
        mockFetchAllowedApiRoutes.mockResolvedValue([false, "Internal Server Error"]);
        render(
            <MemoryRouter>
                <SearchPage />
            </MemoryRouter>
        );
        await waitFor(() =>
            expect(
                mockContainerProps[mockContainerProps.length - 1].assetActionDisabledReasons
            ).toEqual({
                create: PERMISSIONS_FAILED_REASON,
                delete: PERMISSIONS_FAILED_REASON,
                unarchive: PERMISSIONS_FAILED_REASON,
            })
        );
    });
});
