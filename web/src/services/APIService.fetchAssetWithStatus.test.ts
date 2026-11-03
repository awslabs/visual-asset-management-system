/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

jest.mock("../utils/sessionManager", () => ({
    ensureValidSession: jest.fn(),
    logoutExpired: jest.fn(),
}));
jest.mock("../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(),
}));
jest.mock("./apiClient", () => ({
    ...jest.requireActual("./apiClient"),
    apiClient: { get: jest.fn() },
}));

import { apiClient, ApiError } from "./apiClient";
import { fetchAssetWithStatus } from "./APIService";

const denied = () => new ApiError("Not Authorized", 403, { message: "Not Authorized" });

describe("fetchAssetWithStatus", () => {
    beforeEach(() => jest.clearAllMocks());
    const params = { databaseId: "db1", assetId: "a1", showArchived: true };

    it("returns the asset", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ assetId: "a1" });
        expect(await fetchAssetWithStatus(params)).toStrictEqual({ asset: { assetId: "a1" } });
    });

    it("keeps the 403 of a permission denial", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(denied());
        expect(await fetchAssetWithStatus(params)).toStrictEqual({
            message: "Not Authorized",
            status: 403,
        });
    });
});
