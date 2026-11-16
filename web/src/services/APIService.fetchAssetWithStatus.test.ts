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

import { apiClient, ApiError, NETWORK_FAILURE_MESSAGE } from "./apiClient";
import { fetchAsset, fetchAssetWithStatus } from "./APIService";

const denied = () => new ApiError("Not Authorized", 403, { message: "Not Authorized" });

describe("fetchAssetWithStatus", () => {
    beforeEach(() => jest.clearAllMocks());
    const params = { databaseId: "db1", assetId: "a1", showArchived: true };

    it("returns the asset", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ assetId: "a1" });
        expect(await fetchAssetWithStatus(params)).toStrictEqual([true, { assetId: "a1" }]);
    });

    it("keeps the 403 of a permission denial", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(denied());
        expect(await fetchAssetWithStatus(params)).toStrictEqual([false, "Not Authorized", 403]);
    });

    it("says the service could not be reached when fetch rejects without a response", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(new TypeError("Failed to fetch"));
        expect(await fetchAssetWithStatus(params)).toStrictEqual([
            false,
            NETWORK_FAILURE_MESSAGE,
            undefined,
        ]);
    });
});

describe("fetchAsset", () => {
    beforeEach(() => jest.clearAllMocks());
    const params = { databaseId: "db1", assetId: "a1", showArchived: true };

    it("returns the asset, a message body or an error's message as before", async () => {
        (apiClient.get as jest.Mock).mockResolvedValueOnce({ assetId: "a1" });
        expect(await fetchAsset(params)).toStrictEqual({ assetId: "a1" });

        (apiClient.get as jest.Mock).mockResolvedValueOnce({ message: "Asset not found" });
        expect(await fetchAsset(params)).toBe("Asset not found");

        (apiClient.get as jest.Mock).mockRejectedValueOnce(denied());
        expect(await fetchAsset(params)).toBe("Not Authorized");
        expect(apiClient.get).toHaveBeenLastCalledWith("database/db1/assets/a1", {
            queryStringParameters: { showArchived: "true" },
        });
    });

    it("returns false without requesting when an id is missing", async () => {
        expect(await fetchAsset({ databaseId: "db1", assetId: "" })).toBe(false);
        expect(apiClient.get).not.toHaveBeenCalled();
    });
});
