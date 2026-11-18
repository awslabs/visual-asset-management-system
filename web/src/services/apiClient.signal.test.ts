/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** `apiClient` hands a caller's AbortSignal to the request, so a closed viewer stops a presign (#1623). */

import { apiClient } from "./apiClient";
import { downloadAsset } from "./APIService";

jest.mock("../utils/authTokenUtils", () => ({
    getDualAuthorizationHeader: jest.fn(() => Promise.resolve("Bearer t")),
    ensureSessionValid: jest.fn(() => Promise.resolve(true)),
    getCurrentTokenExpiryMs: jest.fn(),
    MISSING_USER_ID_CLAIM_KEY: "auth_missing_user_id_claim",
}));
jest.mock("./appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ api: "https://api.example.test/" })) },
}));

describe("apiClient signal", () => {
    const originalFetch = global.fetch;

    afterEach(() => {
        global.fetch = originalFetch;
    });

    it("passes the signal to fetch and omits it when none is given", async () => {
        global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({}) })) as any;
        const controller = new AbortController();

        await apiClient.post("database/d/assets/a/download", {
            body: {},
            signal: controller.signal,
        });
        await apiClient.post("database/d/assets/a/download", { body: {} });

        expect((global.fetch as jest.Mock).mock.calls[0][1].signal).toBe(controller.signal);
        expect("signal" in (global.fetch as jest.Mock).mock.calls[1][1]).toBe(false);
    });

    it("downloadAsset forwards its signal to the request", async () => {
        global.fetch = jest.fn(async () => ({
            ok: true,
            json: async () => ({ downloadUrl: "https://s3.test/x" }),
        })) as any;
        const controller = new AbortController();

        const result = await downloadAsset({
            databaseId: "d",
            assetId: "a",
            key: "a/x",
            signal: controller.signal,
        });

        expect(result).toEqual([true, "https://s3.test/x"]);
        expect((global.fetch as jest.Mock).mock.calls[0][1].signal).toBe(controller.signal);
    });
});
