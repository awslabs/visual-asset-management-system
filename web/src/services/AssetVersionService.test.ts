/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { fetchAllAssetVersions } from "./AssetVersionService";
import { apiClient, ApiError } from "./apiClient";

// Mock apiClient so fetchAssetVersions (called internally) can be driven without auth/network.
jest.mock("./apiClient", () => {
    class MockApiError extends Error {
        status: number;
        body: any;
        constructor(message: string, status: number, body?: any) {
            super(message);
            this.name = "ApiError";
            this.status = status;
            this.body = body;
        }
    }
    return {
        ApiError: MockApiError,
        apiClient: { get: jest.fn() },
    };
});
// fetchAssetS3Files is imported by the module; stub it out.
jest.mock("./APIService", () => ({
    fetchAssetS3Files: jest.fn(),
}));

const mockGet = apiClient.get as jest.Mock;

describe("fetchAllAssetVersions error propagation", () => {
    beforeEach(() => {
        jest.clearAllMocks();
    });

    it("returns [true, {versions}] on success", async () => {
        mockGet.mockResolvedValue({ versions: [{ id: "v1" }, { id: "v2" }] });
        const [success, data] = await fetchAllAssetVersions({
            databaseId: "db",
            assetId: "asset",
        });
        expect(success).toBe(true);
        expect(data.versions).toHaveLength(2);
    });

    it("returns [false, msg] when the first page fails with a 403 (not a masked empty success)", async () => {
        mockGet.mockRejectedValue(new ApiError("Forbidden", 403, { message: "Forbidden" }));
        const [success, data] = await fetchAllAssetVersions({
            databaseId: "db",
            assetId: "asset",
        });
        // Previously this returned [true, {versions: []}] — masking the error.
        expect(success).toBe(false);
        expect(typeof data).toBe("string");
        expect((data as string).length).toBeGreaterThan(0);
    });

    it("returns [false, msg] for missing required params", async () => {
        const [success] = await fetchAllAssetVersions({ databaseId: "", assetId: "" } as any);
        expect(success).toBe(false);
    });
});
