/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The search page gives its result list the reasons Create Asset and Delete Selected are unavailable
 * to the signed-in role. Roles come from the shipped database-readonly and database-user templates
 * (their allowed-routes listings are in templateRoleRoutes.testdata.ts) and the administrator.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import * as APIService from "../../services/APIService";
import SearchPage from "./SearchPage";
import { __resetCache } from "../../features/orchestration/permissions/useAllowedRoutes";
import { PERMISSIONS_LOADING_REASON } from "../../features/orchestration/permissions/routeDisabledReason";
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
        });
    });

    it.each([
        ["database-user", DATABASE_USER_ROUTES],
        ["administrator", ADMIN_ROUTES],
    ])("the %s role may create and delete", async (_, routes) => {
        expect(await reasonsFor(routes)).toEqual({ create: undefined, delete: undefined });
    });
});
