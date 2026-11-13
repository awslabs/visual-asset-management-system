/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * D169: with `app.compliance.quarantineBlocksDownload` on, every file route answers a quarantined
 * asset with the body in `quarantineRefusalBody.testdata.json`, which the backend handler tests
 * compare with each route's real response. Here that body goes through the real `apiClient` and
 * `downloadAsset`, and the web shows its `message` as it is: the sentence the documentation quotes.
 */

jest.mock("../utils/sessionManager", () => ({
    ensureValidSession: jest.fn(),
    logoutExpired: jest.fn(),
}));
jest.mock("../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(() => Promise.resolve("Bearer test")),
}));

import { downloadAsset } from "./APIService";
import {
    describeStreamFailure,
    downloadFailureMessage,
    downloadRefusalReason,
    streamResponseFailureMessage,
} from "../visualizerPlugin/core/streamFailure";
import {
    QUARANTINE_REFUSAL_BODY,
    QUARANTINE_REFUSAL_MESSAGE,
    QUARANTINE_REFUSAL_STATUS,
    QUARANTINE_REFUSED_DOWNLOAD,
    quarantineRefusalResponse,
} from "../visualizerPlugin/core/quarantineRefusal.testdata";

describe("the API's quarantine refusal, as the web receives it", () => {
    const originalFetch = global.fetch;

    beforeEach(() => {
        localStorage.setItem("api_path", "https://api.example.test/");
        global.fetch = jest.fn(() => Promise.resolve(quarantineRefusalResponse())) as any;
        jest.spyOn(console, "log").mockImplementation(() => undefined);
    });
    afterEach(() => {
        global.fetch = originalFetch;
        jest.restoreAllMocks();
    });

    it("is the documented sentence, with no prefix", () => {
        expect(QUARANTINE_REFUSAL_STATUS).toBe(400);
        expect(QUARANTINE_REFUSAL_BODY).toStrictEqual({
            message: "Asset is quarantined and cannot be downloaded",
        });
    });

    it("comes back from downloadAsset as the tuple the viewer tests use", async () => {
        const result = await downloadAsset({ databaseId: "db1", assetId: "a1", key: "/m.glb" });

        expect(result).toStrictEqual(QUARANTINE_REFUSED_DOWNLOAD);
        expect(global.fetch).toHaveBeenCalledTimes(1);
    });

    it("is shown as it is by the download and the streaming surfaces", async () => {
        const result = await downloadAsset({ databaseId: "db1", assetId: "a1", key: "/m.glb" });

        expect(downloadFailureMessage(result, "Failed to download file")).toBe(
            QUARANTINE_REFUSAL_MESSAGE
        );
        expect(downloadRefusalReason(result)).toBe(QUARANTINE_REFUSAL_MESSAGE);
        expect(
            await streamResponseFailureMessage(quarantineRefusalResponse(), "Failed to load: m.glb")
        ).toBe(QUARANTINE_REFUSAL_MESSAGE);
        expect(
            describeStreamFailure("the point cloud preview", {
                status: QUARANTINE_REFUSAL_STATUS,
                body: QUARANTINE_REFUSAL_BODY,
            })
        ).toBe(`Unable to load the point cloud preview: ${QUARANTINE_REFUSAL_MESSAGE}.`);
    });
});
