/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The asset-link metadata editor offers removing a stored field only to a role whose listing allows
 * DELETE on the asset-link metadata route; a draft in the upload flow has nothing stored, so its
 * fields can always be removed.
 */

import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import AssetLinkMetadata from "./AssetLinkMetadata";
import * as APIService from "../../../../services/APIService";
import { __resetCache } from "../../../../features/orchestration/permissions/useAllowedRoutes";
import {
    DATABASE_READONLY_ROUTES,
    DATABASE_USER_ROUTES,
    allowedRoutesResponse,
} from "../../../../features/orchestration/permissions/templateRoleRoutes.testdata";

jest.mock("../../../../services/APIService", () => ({
    fetchAllowedApiRoutes: jest.fn(),
}));

const mockContainer = jest.fn();
jest.mock("../../../metadataV2", () => ({
    MetadataContainer: (props: any) => {
        mockContainer(props);
        return null;
    },
}));

const lastCanDelete = () =>
    mockContainer.mock.calls[mockContainer.mock.calls.length - 1][0].canDelete;

const renderAs = (routes: any[], mode: "upload" | "view" = "view") => {
    (APIService.fetchAllowedApiRoutes as jest.Mock).mockResolvedValue(
        allowedRoutesResponse(routes)
    );
    render(
        <AssetLinkMetadata assetLinkId="link-1" selectedNode={null} databaseId="db-1" mode={mode} />
    );
};

describe("AssetLinkMetadata metadata removal by role", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
    });

    it("offers no removal of a stored field to a role without DELETE on the route", async () => {
        renderAs(DATABASE_READONLY_ROUTES);
        await waitFor(() => expect(APIService.fetchAllowedApiRoutes).toHaveBeenCalled());
        // Let the listing land, so the answer below is the listing's and not the loading state's.
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });
        expect(mockContainer.mock.calls.length).toBeGreaterThan(1);
        expect(mockContainer.mock.calls.every(([props]) => props.canDelete === false)).toBe(true);
    });

    it("[control] offers removal to a database-user role, which the template grants it", async () => {
        renderAs(DATABASE_USER_ROUTES);
        await waitFor(() => expect(lastCanDelete()).toBe(true));
    });

    it("lets a draft in the upload flow remove its fields whatever the role", () => {
        renderAs(DATABASE_READONLY_ROUTES, "upload");
        expect(lastCanDelete()).toBe(true);
    });
});
