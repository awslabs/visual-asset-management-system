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
    apiClient: { post: jest.fn() },
}));

import { apiClient, ApiError, NETWORK_FAILURE_MESSAGE } from "./apiClient";
import { ingestAsset } from "./APIService";

/**
 * Both stages of POST /ingest-asset answer with a `message` beside the data the caller needs: the
 * initialize stage's file list and part URLs, the complete stage's per-file results. ingestAsset
 * returns the whole response, so the Asset Ingestion page can upload the parts and complete.
 */
const INITIALIZE_RESPONSE = {
    message: "Upload initialized successfully",
    uploadId: "y-upload-1",
    files: [
        {
            relativeKey: "iasset-1/model.glb",
            uploadIdS3: "s3-upload-1",
            numParts: 2,
            partUploadUrls: [
                { PartNumber: 1, UploadUrl: "https://bucket.example/part1" },
                { PartNumber: 2, UploadUrl: "https://bucket.example/part2" },
            ],
        },
    ],
};

const COMPLETE_RESPONSE = {
    message: "Multipart upload and asset ingestion completed successfully.",
    uploadId: "y-upload-1",
    assetId: "iasset-1",
    fileResults: [{ relativeKey: "iasset-1/model.glb", uploadIdS3: "s3-upload-1", success: true }],
    overallSuccess: true,
    largeFileAsynchronousHandling: false,
};

describe("ingestAsset", () => {
    beforeEach(() => jest.clearAllMocks());

    it("returns the initialize response, file list included", async () => {
        (apiClient.post as jest.Mock).mockResolvedValue(INITIALIZE_RESPONSE);

        const result = await ingestAsset({ databaseId: "db-1", assetId: "iasset-1" });

        expect(result).toStrictEqual([true, INITIALIZE_RESPONSE]);
        expect(apiClient.post).toHaveBeenCalledWith("ingest-asset", {
            body: { databaseId: "db-1", assetId: "iasset-1" },
        });
    });

    it("returns the complete response, per-file results included", async () => {
        (apiClient.post as jest.Mock).mockResolvedValue(COMPLETE_RESPONSE);

        expect(await ingestAsset({ uploadId: "y-upload-1" })).toStrictEqual([
            true,
            COMPLETE_RESPONSE,
        ]);
    });

    it("does not read a success message that mentions an error as a failure", async () => {
        const response = { ...COMPLETE_RESPONSE, message: "Error-free upload of asset Error-1" };
        (apiClient.post as jest.Mock).mockResolvedValue(response);

        expect(await ingestAsset({})).toStrictEqual([true, response]);
    });

    it("returns the server's message for a refused request", async () => {
        (apiClient.post as jest.Mock).mockRejectedValue(
            new ApiError(
                "VAMS General Error: Error completing upload: Upload record not found",
                400,
                {
                    message: "VAMS General Error: Error completing upload: Upload record not found",
                }
            )
        );

        expect(await ingestAsset({})).toStrictEqual([
            false,
            "VAMS General Error: Error completing upload: Upload record not found",
        ]);
    });

    it("returns the network failure message for a request that never reached the service", async () => {
        (apiClient.post as jest.Mock).mockRejectedValue(new TypeError("Failed to fetch"));

        expect(await ingestAsset({})).toStrictEqual([false, NETWORK_FAILURE_MESSAGE]);
    });
});
