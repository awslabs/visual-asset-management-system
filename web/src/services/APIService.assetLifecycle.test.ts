/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Archive, unarchive and permanent delete report the backend's success flag, not whether the
 * success message happens to contain the word "error" (it echoes the caller-chosen assetId).
 */

import { archiveAssetDelete, deleteAssetPermanentDelete, unarchiveAsset } from "./APIService";
import { apiClient } from "./apiClient";

jest.mock("./apiClient", () => ({
    ApiError: class extends Error {},
    apiClient: { put: jest.fn(), del: jest.fn() },
}));

const mockPut = apiClient.put as jest.Mock;
const mockDel = apiClient.del as jest.Mock;

const CALLS: [string, (args: any) => Promise<any>, jest.Mock][] = [
    ["unarchiveAsset", unarchiveAsset, mockPut],
    ["archiveAssetDelete", archiveAssetDelete, mockDel],
    ["deleteAssetPermanentDelete", deleteAssetPermanentDelete, mockDel],
];

describe("asset lifecycle results", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => {});
    });

    afterEach(() => {
        jest.restoreAllMocks();
    });

    it.each(CALLS)(
        "%s reports success for an assetId containing 'error'",
        async (_n, call, mock) => {
            const message = "Asset error-handling-test processed";
            mock.mockResolvedValue({ success: true, message, assetId: "error-handling-test" });

            await expect(
                call({ databaseId: "db1", assetId: "error-handling-test", body: {} })
            ).resolves.toEqual([true, message]);
        }
    );

    it.each(CALLS)("%s reports failure when success is false", async (_n, call, mock) => {
        mock.mockResolvedValue({ success: false, message: "Asset is not archived" });

        await expect(call({ databaseId: "db1", assetId: "a1", body: {} })).resolves.toEqual([
            false,
            "Asset is not archived",
        ]);
    });

    it.each(CALLS)("%s reports failure when the request throws", async (_n, call, mock) => {
        mock.mockRejectedValue(new Error("HTTP 404: Not Found"));

        await expect(call({ databaseId: "db1", assetId: "a1", body: {} })).resolves.toEqual([
            false,
            "HTTP 404: Not Found",
        ]);
    });
});
