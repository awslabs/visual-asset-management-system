/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The bulk presign map stands in for up to MAX_DOWNLOAD_KEYS_PER_REQUEST individual presign
 * requests. A chunk failure that is silently dropped therefore turns one request into one per
 * file, against the endpoint that just failed, with nothing telling the user why the download
 * appears to hang.
 */

import { generateBulkDownloadUrlMap } from "./FileOperationsService";

const mockPost = jest.fn();
jest.mock("./apiClient", () => ({
    apiClient: {
        post: (...args: any[]) => mockPost(...args),
    },
}));

const signed = (...keys: string[]) => ({
    files: keys.map((key) => ({ key, success: true, downloadUrl: `https://s3.test/${key}` })),
});

describe("generateBulkDownloadUrlMap", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        jest.spyOn(console, "error").mockImplementation(() => {});
    });

    afterEach(() => {
        (console.error as jest.Mock).mockRestore();
    });

    it("positive control: signs a chunk in a single request when the call succeeds", async () => {
        // Establishes that mockPost counts real calls, so the counts asserted below mean what
        // they say rather than "the request was never made".
        mockPost.mockResolvedValue(signed("a.glb", "b.glb"));
        const onChunkError = jest.fn();

        const urlByKey = await generateBulkDownloadUrlMap(
            "db1",
            "asset1",
            ["a.glb", "b.glb"],
            undefined,
            onChunkError
        );

        expect(mockPost).toHaveBeenCalledTimes(1);
        expect(urlByKey.get("a.glb")).toBe("https://s3.test/a.glb");
        expect(urlByKey.get("b.glb")).toBe("https://s3.test/b.glb");
        expect(onChunkError).not.toHaveBeenCalled();
    });

    it("re-checks a failed chunk once and keeps the bulk result when the retry succeeds", async () => {
        mockPost
            .mockRejectedValueOnce(Object.assign(new Error("Rate limit exceeded"), { status: 429 }))
            .mockResolvedValueOnce(signed("a.glb"));
        const onChunkError = jest.fn();

        const urlByKey = await generateBulkDownloadUrlMap(
            "db1",
            "asset1",
            ["a.glb"],
            undefined,
            onChunkError
        );

        expect(mockPost).toHaveBeenCalledTimes(2);
        // The key is signed, so the caller does not fall back to a per-file request for it.
        expect(urlByKey.get("a.glb")).toBe("https://s3.test/a.glb");
        expect(onChunkError).not.toHaveBeenCalled();
    });

    it("reports the unsigned keys when the retry also fails, instead of dropping them silently", async () => {
        const throttled = Object.assign(new Error("Rate limit exceeded"), { status: 429 });
        mockPost.mockRejectedValue(throttled);
        const onChunkError = jest.fn();

        const urlByKey = await generateBulkDownloadUrlMap(
            "db1",
            "asset1",
            ["a.glb", "b.glb"],
            undefined,
            onChunkError
        );

        expect(mockPost).toHaveBeenCalledTimes(2); // one attempt + one re-check, then conceded
        expect(urlByKey.size).toBe(0);
        expect(onChunkError).toHaveBeenCalledTimes(1);
        expect(onChunkError).toHaveBeenCalledWith({
            keys: ["a.glb", "b.glb"],
            error: throttled,
        });
        // A request-level failure is an error, not an informational log.
        expect(console.error).toHaveBeenCalled();
    });

    it("passes the asset version pin through on the re-check as well", async () => {
        mockPost.mockRejectedValueOnce(new Error("boom")).mockResolvedValueOnce(signed("a.glb"));

        await generateBulkDownloadUrlMap("db1", "asset1", ["a.glb"], "v3");

        for (const call of mockPost.mock.calls) {
            expect(call[1].body.assetVersionId).toBe("v3");
        }
    });
});
