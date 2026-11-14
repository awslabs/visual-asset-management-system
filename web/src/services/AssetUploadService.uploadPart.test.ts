/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

jest.mock("./apiClient", () => ({ apiClient: { post: jest.fn(), get: jest.fn() } }));

import AssetUploadService from "./AssetUploadService";

describe("AssetUploadService.uploadPart", () => {
    const realFetch = global.fetch;
    afterEach(() => {
        global.fetch = realFetch;
    });

    it("hands the caller's abort signal to the request and returns the unquoted ETag", async () => {
        const fetchMock = jest.fn().mockResolvedValue({
            ok: true,
            headers: { get: () => '"abc123"' },
        });
        global.fetch = fetchMock as unknown as typeof fetch;
        const controller = new AbortController();
        const part = new Blob(["x"]);

        const etag = await AssetUploadService.uploadPart(
            "https://bucket.example/part1",
            part,
            controller.signal
        );

        expect(etag).toBe("abc123");
        expect(fetchMock).toHaveBeenCalledWith("https://bucket.example/part1", {
            method: "PUT",
            body: part,
            signal: controller.signal,
        });
    });

    it("rejects when the request is aborted", async () => {
        global.fetch = jest.fn().mockRejectedValue(new DOMException("aborted", "AbortError"));

        await expect(
            AssetUploadService.uploadPart("https://bucket.example/part1", new Blob(["x"]))
        ).rejects.toThrow("aborted");
    });
});
