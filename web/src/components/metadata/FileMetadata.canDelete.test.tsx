/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The file metadata editor (both file tabs) offers removing a stored field only to a role whose
 * listing allows DELETE on the file metadata route: a bulk-edit save that leaves a stored field out
 * is refused without it, so the editor keeps such fields instead (MetadataContainer `canDelete`).
 */

import React from "react";
import { act, render, waitFor } from "@testing-library/react";
import FileMetadata from "./FileMetadata";
import * as APIService from "../../services/APIService";
import { __resetCache } from "../../features/orchestration/permissions/useAllowedRoutes";
import {
    ADMIN_ROUTES,
    DATABASE_USER_ROUTES,
    allowedRoutesResponse,
} from "../../features/orchestration/permissions/templateRoleRoutes.testdata";

jest.mock("../../services/APIService", () => ({
    fetchAllowedApiRoutes: jest.fn(),
}));

const mockContainer = jest.fn();
jest.mock("../metadataV2", () => ({
    MetadataContainer: (props: any) => {
        mockContainer(props);
        return null;
    },
}));

const lastCanDelete = () =>
    mockContainer.mock.calls[mockContainer.mock.calls.length - 1][0].canDelete;

const renderAs = (routes: any[]) => {
    (APIService.fetchAllowedApiRoutes as jest.Mock).mockResolvedValue(
        allowedRoutesResponse(routes)
    );
    render(<FileMetadata databaseId="db-1" assetId="asset-1" prefix="/model.glb" />);
};

describe("FileMetadata metadata removal by role", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        __resetCache();
    });

    it("offers no removal of a stored field to a database-user role", async () => {
        renderAs(DATABASE_USER_ROUTES);
        await waitFor(() => expect(APIService.fetchAllowedApiRoutes).toHaveBeenCalled());
        // Let the listing land, so the answer below is the listing's and not the loading state's.
        await act(async () => {
            await new Promise((resolve) => setTimeout(resolve, 0));
        });
        expect(mockContainer.mock.calls.length).toBeGreaterThan(1);
        expect(mockContainer.mock.calls.every(([props]) => props.canDelete === false)).toBe(true);
    });

    it("[control] offers removal to a role with DELETE on the file metadata route", async () => {
        renderAs(ADMIN_ROUTES);
        await waitFor(() => expect(lastCanDelete()).toBe(true));
    });
});
