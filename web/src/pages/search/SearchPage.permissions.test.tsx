/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search page gives its result list the reasons Create Asset, Delete Selected and permanent delete
 * are unavailable to the signed-in role. Roles come from the shipped database-readonly and database-user templates
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

const mockContainerProps: any[] = [];
jest.mock("../../components/search", () => ({
    ModernSearchContainer: (props: any) => {
        mockContainerProps.push(props);
        return null;
    },
}));

const mockFetchAllowedApiRoutes = APIService.fetchAllowedApiRoutes as jest.Mock;
const PERMANENT_DELETE_REASON = `Your role does not allow permanently deleting ${Synonyms.assets}.`;

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

    it("a database-readonly role may neither create nor delete", async () => {
        expect(await reasonsFor(DATABASE_READONLY_ROUTES)).toEqual({
            create: `Your role does not allow creating ${Synonyms.assets}.`,
            delete: `Your role does not allow archiving or deleting ${Synonyms.assets}.`,
            permanentDelete: PERMANENT_DELETE_REASON,
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
                permanentDelete: PERMISSIONS_FAILED_REASON,
            })
        );
    });

    it("a database-user role may create and archive but not permanently delete", async () => {
        expect(await reasonsFor(DATABASE_USER_ROUTES)).toEqual({
            create: undefined,
            delete: undefined,
            permanentDelete: PERMANENT_DELETE_REASON,
        });
    });

    it("a role with the deleteAsset route may create, archive and permanently delete", async () => {
        const routes = [
            ...ADMIN_ROUTES,
            {
                path: "/database/{databaseId}/assets/{assetId}/deleteAsset",
                methods: ["DELETE"],
                category: "test",
            },
        ];
        expect(await reasonsFor(routes)).toEqual({
            create: undefined,
            delete: undefined,
            permanentDelete: undefined,
        });
    });
});
