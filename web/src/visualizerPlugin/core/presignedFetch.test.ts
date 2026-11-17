/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A viewer that reads file bytes itself fetches the presigned URL of the authorized download route
 * instead of following the streaming route's cross-origin 307 (#1623). These tests pin the request
 * S3 receives (no Authorization header, no credentials), the short-lived URL cache and how a
 * refused presign reaches the user.
 */

import {
    clearPresignedUrlCache,
    fetchPresignedFile,
    getPresignedFileUrl,
    PRESIGNED_URL_CACHE_LIMIT,
    PRESIGNED_URL_TTL_MILLIS,
    setPresignedUrlClock,
} from "./presignedFetch";
import { downloadAsset } from "../../services/APIService";
import { ApiRefusalError, DOWNLOAD_FORBIDDEN_MESSAGE } from "./streamFailure";
import {
    QUARANTINE_REFUSAL_MESSAGE,
    QUARANTINE_REFUSED_DOWNLOAD,
} from "./quarantineRefusal.testdata";

jest.mock("../../services/APIService", () => ({ downloadAsset: jest.fn() }));

const mockedDownloadAsset = downloadAsset as jest.Mock;

const REQUEST = { databaseId: "db-1", assetId: "asset-1", key: "asset-1/model.glb" };

describe("presignedFetch", () => {
    const originalFetch = global.fetch;
    let clock = 1_000_000;

    beforeEach(() => {
        clock = 1_000_000;
        setPresignedUrlClock(() => clock);
        clearPresignedUrlCache();
        mockedDownloadAsset.mockReset();
        mockedDownloadAsset.mockImplementation(async ({ key }: { key: string }) => [
            true,
            `https://bucket.s3.example.test/${key}?X-Amz-Signature=sig`,
        ]);
        global.fetch = jest.fn(async () => ({ ok: true, status: 200 })) as any;
    });

    afterEach(() => {
        global.fetch = originalFetch;
        setPresignedUrlClock(null);
    });

    it("fetches the presigned URL with no Authorization header and no credentials", async () => {
        const signal = new AbortController().signal;

        await fetchPresignedFile({ ...REQUEST, signal }, "Failed to load: model.glb");

        expect(global.fetch).toHaveBeenCalledTimes(1);
        const [url, options] = (global.fetch as jest.Mock).mock.calls[0];
        expect(url).toBe("https://bucket.s3.example.test/asset-1/model.glb?X-Amz-Signature=sig");
        expect(options).toEqual({ signal });
        expect(options.headers).toBeUndefined();
        expect(options.credentials).toBeUndefined();
    });

    it("asks the download route for the asset version when one is set, else the file version", async () => {
        await getPresignedFileUrl({ ...REQUEST, assetVersionId: "av1", versionId: "s3v" }, "x");
        await getPresignedFileUrl({ ...REQUEST, key: "asset-1/b.glb", versionId: "s3v" }, "x");

        expect(mockedDownloadAsset.mock.calls[0][0]).toMatchObject({
            assetVersionId: "av1",
            downloadType: "assetFile",
        });
        expect(mockedDownloadAsset.mock.calls[1][0]).toMatchObject({ versionId: "s3v" });
    });

    it("reuses a URL until the cache lifetime ends, then asks again", async () => {
        await getPresignedFileUrl(REQUEST, "x");
        clock += PRESIGNED_URL_TTL_MILLIS - 1;
        await getPresignedFileUrl(REQUEST, "x");
        expect(mockedDownloadAsset).toHaveBeenCalledTimes(1);

        clock += 1;
        await getPresignedFileUrl(REQUEST, "x");
        expect(mockedDownloadAsset).toHaveBeenCalledTimes(2);
    });

    it("does not share a URL between versions, assets or keys", async () => {
        await getPresignedFileUrl(REQUEST, "x");
        await getPresignedFileUrl({ ...REQUEST, assetVersionId: "av1" }, "x");
        await getPresignedFileUrl({ ...REQUEST, versionId: "s3v" }, "x");
        await getPresignedFileUrl({ ...REQUEST, assetId: "asset-2" }, "x");
        await getPresignedFileUrl({ ...REQUEST, key: "asset-1/other.glb" }, "x");

        expect(mockedDownloadAsset).toHaveBeenCalledTimes(5);
    });

    it("stays within the cache bound", async () => {
        for (let i = 0; i < PRESIGNED_URL_CACHE_LIMIT + 5; i++) {
            await getPresignedFileUrl({ ...REQUEST, key: `asset-1/f${i}.png` }, "x");
        }
        // The oldest entry was evicted, so it is asked for again.
        mockedDownloadAsset.mockClear();
        await getPresignedFileUrl({ ...REQUEST, key: "asset-1/f0.png" }, "x");
        expect(mockedDownloadAsset).toHaveBeenCalledTimes(1);
    });

    it("does not cache a refused presign", async () => {
        mockedDownloadAsset.mockResolvedValueOnce([false, "Forbidden", 403]);

        await expect(fetchPresignedFile(REQUEST, "Failed to load: model.glb")).rejects.toThrow(
            DOWNLOAD_FORBIDDEN_MESSAGE
        );
        expect(global.fetch).not.toHaveBeenCalled();

        await fetchPresignedFile(REQUEST, "Failed to load: model.glb");
        expect(global.fetch).toHaveBeenCalledTimes(1);
    });

    it("surfaces the API's own refusal reason as an ApiRefusalError", async () => {
        mockedDownloadAsset.mockResolvedValueOnce(QUARANTINE_REFUSED_DOWNLOAD);

        const error = await fetchPresignedFile(REQUEST, "Failed to load: model.glb").catch(
            (e) => e
        );

        expect(error).toBeInstanceOf(ApiRefusalError);
        expect(error.message).toBe(QUARANTINE_REFUSAL_MESSAGE);
    });

    it("uses the viewer's own wording when the presign failed with no status", async () => {
        mockedDownloadAsset.mockResolvedValueOnce(false);

        await expect(fetchPresignedFile(REQUEST, "Failed to load: model.glb")).rejects.toThrow(
            "Failed to load: model.glb"
        );
    });
});
