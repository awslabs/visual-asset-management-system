/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

jest.mock("./apiClient", () => ({
    apiClient: { post: jest.fn(), put: jest.fn(), del: jest.fn() },
}));

import { apiClient } from "./apiClient";
import {
    createAssetLinkMetadata,
    updateAssetLinkMetadata,
    deleteAssetLinkMetadata,
} from "./APIService";

/**
 * The only asset-link metadata route the API registers: one collection path carrying all four
 * verbs (API_ASSET_LINK_METADATA in backend/backend/common/apiRoutes.py). There is no per-key
 * sub-path, so a key belongs in the request body. A per-key path is not a 404 — the authorizer
 * denies the unmatched route first — so the failure reads as a permissions problem.
 */
const ASSET_LINK_ID = "al-1234";
const REGISTERED_PATH = `asset-links/${ASSET_LINK_ID}/metadata`;

/** A bulk write response as the backend returns it (models.metadata.BulkOperationResponseModel). */
const bulkOk = (message: string) => ({
    success: true,
    totalItems: 1,
    successCount: 1,
    failureCount: 0,
    successfulItems: ["Bracket Count"],
    failedItems: [],
    message,
    timestamp: "2026-09-01T00:00:00Z",
});

const bulkFailed = (message: string) => ({
    success: false,
    totalItems: 1,
    successCount: 0,
    failureCount: 1,
    successfulItems: [],
    failedItems: [{ metadataKey: "Bracket Count", error: "schema violation" }],
    message,
    timestamp: "2026-09-01T00:00:00Z",
});

const item = {
    assetLinkId: ASSET_LINK_ID,
    metadataKey: "Bracket Count",
    metadataValue: "7",
    metadataValueType: "string",
};

describe("asset link metadata service calls target the registered collection route", () => {
    beforeEach(() => jest.clearAllMocks());

    it("creates against the collection path with a bulk body", async () => {
        (apiClient.post as jest.Mock).mockResolvedValue(bulkOk("Upserted 1 of 1 metadata items"));

        const result = await createAssetLinkMetadata(item);

        const [path, init] = (apiClient.post as jest.Mock).mock.calls[0];
        expect(path).toBe(REGISTERED_PATH);
        // The backend model requires `metadata` as a list; a flat item fails validation with a 400.
        expect(init.body).toEqual({
            metadata: [
                { metadataKey: "Bracket Count", metadataValue: "7", metadataValueType: "string" },
            ],
        });
        expect(result).toEqual([true, "Upserted 1 of 1 metadata items"]);
    });

    it("updates against the collection path, carrying the key in the body", async () => {
        (apiClient.put as jest.Mock).mockResolvedValue(bulkOk("Upserted 1 of 1 metadata items"));

        const result = await updateAssetLinkMetadata(item);

        const [path, init] = (apiClient.put as jest.Mock).mock.calls[0];
        expect(path).toBe(REGISTERED_PATH);
        expect(path).not.toContain("Bracket Count");
        expect(init.body).toEqual({
            metadata: [
                { metadataKey: "Bracket Count", metadataValue: "7", metadataValueType: "string" },
            ],
        });
        expect(result).toEqual([true, "Upserted 1 of 1 metadata items"]);
    });

    it("deletes against the collection path, carrying the keys in the body", async () => {
        (apiClient.del as jest.Mock).mockResolvedValue(bulkOk("Deleted 1 of 1 metadata items"));

        const result = await deleteAssetLinkMetadata({
            assetLinkId: ASSET_LINK_ID,
            metadataKey: "Bracket Count",
        });

        const [path, init] = (apiClient.del as jest.Mock).mock.calls[0];
        expect(path).toBe(REGISTERED_PATH);
        // The delete handler rejects an absent body outright, so the keys must be sent.
        expect(init.body).toEqual({ metadataKeys: ["Bracket Count"] });
        expect(result).toEqual([true, "Deleted 1 of 1 metadata items"]);
    });

    it("leaves the path intact for a key that is not URL-safe", async () => {
        // A key holding a slash or a space in the path produced a different route again; in the
        // body it is just a value.
        (apiClient.put as jest.Mock).mockResolvedValue(bulkOk("Upserted 1 of 1 metadata items"));

        await updateAssetLinkMetadata({ ...item, metadataKey: "assembly/bracket count" });

        const [path, init] = (apiClient.put as jest.Mock).mock.calls[0];
        expect(path).toBe(REGISTERED_PATH);
        expect(init.body.metadata[0].metadataKey).toBe("assembly/bracket count");
    });

    it("reports a bulk response that succeeded for nothing as a failure", async () => {
        // The response carries a 200 and a message that does not say "error", so the tuple would
        // otherwise read as success while nothing was written.
        const failed = bulkFailed("Upserted 0 of 1 metadata items");
        (apiClient.put as jest.Mock).mockResolvedValue(failed);

        const result = await updateAssetLinkMetadata(item);

        expect(result).toEqual([false, "Upserted 0 of 1 metadata items"]);
    });
});
