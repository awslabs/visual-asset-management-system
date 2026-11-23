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

import { apiClient, NETWORK_FAILURE_MESSAGE } from "./apiClient";
import {
    createZipDownload,
    deleteZipDownload,
    fetchAllComments,
    fetchAssetLinks,
    fetchAssetZipDownloads,
    ZIP_CREATE_OUTCOME_UNKNOWN_MESSAGE,
} from "./APIService";
import { fetchAssetVersions } from "./AssetVersionService";
import { requestFailureMessage } from "./networkFailure";

/**
 * A request whose response the browser never saw rejects with a TypeError ("Failed to fetch"). The
 * services word it as NETWORK_FAILURE_MESSAGE with no status, and a create that may have reached
 * the API says its outcome is unknown, so the page neither shows the browser's text nor invites a
 * second job.
 */
const offline = () => new TypeError("Failed to fetch");

describe("requests that got no response", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });

    afterEach(() => jest.restoreAllMocks());

    it("requestFailureMessage words only a no-response failure as the network message", () => {
        expect(requestFailureMessage(offline(), "Failed")).toBe(NETWORK_FAILURE_MESSAGE);
        expect(requestFailureMessage(new Error("Not Authorized"), "Failed")).toBe("Not Authorized");
        expect(requestFailureMessage({}, "Failed")).toBe("Failed");
    });

    it("a zip download create says the job may exist and points to the Download Queue", async () => {
        (apiClient.post as jest.Mock).mockRejectedValue(offline());
        expect(await createZipDownload({ databaseId: "db1", assetId: "a1", body: {} })).toEqual([
            false,
            ZIP_CREATE_OUTCOME_UNKNOWN_MESSAGE,
            undefined,
        ]);
    });

    it("the zip download reads and remove carry the network message and no status", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(offline());
        (apiClient.del as jest.Mock).mockRejectedValue(offline());
        expect(await fetchAssetZipDownloads({ databaseId: "db1", assetId: "a1" })).toEqual([
            false,
            NETWORK_FAILURE_MESSAGE,
            undefined,
        ]);
        expect(
            await deleteZipDownload({ databaseId: "db1", assetId: "a1", requestId: "r1" })
        ).toEqual([false, NETWORK_FAILURE_MESSAGE, undefined]);
    });

    it("the asset page's comments, relationships and versions reads carry the network message", async () => {
        (apiClient.get as jest.Mock).mockRejectedValue(offline());
        expect(await fetchAllComments({ databaseId: "db1", assetId: "a1" })).toBe(
            NETWORK_FAILURE_MESSAGE
        );
        expect(await fetchAssetLinks({ databaseId: "db1", assetId: "a1" })).toEqual([
            false,
            NETWORK_FAILURE_MESSAGE,
        ]);
        expect(await fetchAssetVersions({ databaseId: "db1", assetId: "a1" })).toEqual([
            false,
            NETWORK_FAILURE_MESSAGE,
        ]);
    });
});
