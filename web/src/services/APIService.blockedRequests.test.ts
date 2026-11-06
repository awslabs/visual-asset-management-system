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
    apiClient: { del: jest.fn(), post: jest.fn(), get: jest.fn() },
}));

import { apiClient, ApiError, NETWORK_FAILURE_MESSAGE } from "./apiClient";
import {
    deleteAssetLink,
    deleteComment,
    downloadAsset,
    fetchAsset,
    fetchAssetWithStatus,
    fetchFileInfo,
} from "./APIService";
import { fetchFileVersions } from "./AssetVersionService";
import { fetchMetadata } from "./MetadataService";

/**
 * A 403 the deployment's AWS WAF web ACL answered (the WAF_FILTERED body) is not a permission
 * denial, so these calls hand back its message rather than the status their callers turn into a
 * permission sentence.
 */
const BLOCKED_MESSAGE = "Request blocked by the deployment's AWS WAF web ACL (request ID r-1)";
const blocked = () =>
    new ApiError(BLOCKED_MESSAGE, 403, {
        message: "Request blocked by the deployment's AWS WAF web ACL",
        blockedBy: "waf",
        requestId: "r-1",
    });
const denied = () => new ApiError("Not Authorized", 403, { message: "Not Authorized" });

describe("deleteAssetLink", () => {
    beforeEach(() => jest.clearAllMocks());

    it("returns a blocked request's message without the status", async () => {
        (apiClient.del as jest.Mock).mockRejectedValue(blocked());
        expect(await deleteAssetLink({ assetLinkId: "al-1" })).toStrictEqual([
            false,
            BLOCKED_MESSAGE,
            undefined,
        ]);
    });

    it("keeps the 403 of a permission denial", async () => {
        (apiClient.del as jest.Mock).mockRejectedValue(denied());
        expect(await deleteAssetLink({ assetLinkId: "al-1" })).toStrictEqual([
            false,
            "Not Authorized",
            403,
        ]);
    });
});

describe("deleteComment", () => {
    beforeEach(() => jest.clearAllMocks());

    it("returns a blocked request's message rather than the 403", async () => {
        (apiClient.del as jest.Mock).mockRejectedValue(blocked());
        expect(
            await deleteComment({ assetId: "a1", assetVersionIdAndCommentId: "v1:c1" })
        ).toStrictEqual([false, BLOCKED_MESSAGE]);
    });

    it("keeps the bare 403 of a permission denial", async () => {
        (apiClient.del as jest.Mock).mockRejectedValue(denied());
        expect(await deleteComment({ assetId: "a1", assetVersionIdAndCommentId: "v1:c1" })).toBe(
            403
        );
    });
});

describe("downloadAsset", () => {
    beforeEach(() => jest.clearAllMocks());

    const download = () =>
        downloadAsset({ databaseId: "db1", assetId: "a1", key: "/f.txt", versionId: "v1" });

    it("returns a blocked request's message without the status", async () => {
        (apiClient.post as jest.Mock).mockRejectedValue(blocked());
        expect(await download()).toStrictEqual([false, BLOCKED_MESSAGE, undefined]);
    });

    it("keeps the 403 of a permission denial", async () => {
        (apiClient.post as jest.Mock).mockRejectedValue(denied());
        expect(await download()).toStrictEqual([false, "Not Authorized", 403]);
    });
});

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

    it("returns a blocked request's message without the status", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(blocked());
        expect(await fetchAssetWithStatus(params)).toStrictEqual([
            false,
            BLOCKED_MESSAGE,
            undefined,
        ]);
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

// The file page tells a missing file and a refusal from a request that may pass when tried again
// by the status fetchFileInfo hands back.
describe("fetchFileInfo", () => {
    beforeEach(() => jest.clearAllMocks());
    const params = {
        databaseId: "db1",
        assetId: "a1",
        fileKey: "model.glb",
        includeVersions: true,
    };

    it("returns the file's details", async () => {
        (apiClient.get as jest.Mock).mockResolvedValue({ key: "a1/model.glb" });
        expect(await fetchFileInfo(params)).toStrictEqual([true, { key: "a1/model.glb" }]);
    });

    it("keeps the 403 of a permission denial", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(denied());
        expect(await fetchFileInfo(params)).toStrictEqual([false, "Not Authorized", 403]);
    });

    it("keeps the status of a missing file or asset", async () => {
        (apiClient.get as jest.Mock).mockRejectedValueOnce(
            new ApiError("File not found.", 400, { message: "File not found." })
        );
        expect(await fetchFileInfo(params)).toStrictEqual([false, "File not found.", 400]);

        (apiClient.get as jest.Mock).mockRejectedValueOnce(
            new ApiError("Asset not found", 404, { message: "Asset not found" })
        );
        expect(await fetchFileInfo(params)).toStrictEqual([false, "Asset not found", 404]);
    });

    it("returns a blocked request's message without the status", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(blocked());
        expect(await fetchFileInfo(params)).toStrictEqual([false, BLOCKED_MESSAGE, undefined]);
    });

    it("says the service could not be reached when fetch rejects without a response", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(new TypeError("Failed to fetch"));
        expect(await fetchFileInfo(params)).toStrictEqual([
            false,
            NETWORK_FAILURE_MESSAGE,
            undefined,
        ]);
    });
});

// The file page's versions table and metadata panel show these loads' messages.
describe("the file page's other loads when fetch rejects without a response", () => {
    beforeEach(() => jest.clearAllMocks());

    it("fetchFileVersions says the service could not be reached", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(new TypeError("Failed to fetch"));
        expect(
            await fetchFileVersions({ databaseId: "db1", assetId: "a1", filePath: "model.glb" })
        ).toStrictEqual([false, NETWORK_FAILURE_MESSAGE]);
    });

    it("fetchMetadata says the service could not be reached", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(new TypeError("Failed to fetch"));
        await expect(fetchMetadata("file", "a1", "db1", "/model.glb", "metadata")).rejects.toThrow(
            NETWORK_FAILURE_MESSAGE
        );
    });

    it("fetchMetadata keeps an API error's message", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(denied());
        await expect(fetchMetadata("file", "a1", "db1", "/model.glb", "metadata")).rejects.toThrow(
            "Not Authorized"
        );
    });
});
