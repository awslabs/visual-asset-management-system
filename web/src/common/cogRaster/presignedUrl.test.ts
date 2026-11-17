/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    FALLBACK_REFRESH_MS,
    MAX_REFRESH_FAILURES,
    MAX_REFRESH_MS,
    MIN_REFRESH_MS,
    RETRY_BASE_MS,
    RasterDownloadRefused,
    keepFresh,
    presignedLifetime,
    refreshDelayMs,
    requestPresignedUrl,
} from "./presignedUrl";

jest.mock("../../services/APIService", () => ({ downloadAsset: jest.fn() }));
import { downloadAsset } from "../../services/APIService";

const FILE = { databaseId: "db", assetId: "a1", key: "a1/x.tif" };
const signed = (date: string, expires: number) =>
    `https://bucket.s3.amazonaws.com/a1/x.tif?X-Amz-Date=${date}&X-Amz-Expires=${expires}&X-Amz-Signature=abc`;

describe("presignedLifetime and refreshDelayMs", () => {
    it("reads the signing time and lifetime", () => {
        const lifetime = presignedLifetime(signed("20261117T170000Z", 3600))!;
        expect(lifetime.issuedAtMs).toBe(Date.UTC(2026, 10, 17, 17, 0, 0));
        expect(lifetime.lifetimeMs).toBe(3600 * 1000);
    });
    it("schedules the refresh at 80% of the lifetime", () => {
        const issued = Date.UTC(2026, 10, 17, 17, 0, 0);
        expect(refreshDelayMs(signed("20261117T170000Z", 1000), issued)).toBe(800 * 1000);
        expect(refreshDelayMs(signed("20261117T170000Z", 1000), issued + 300 * 1000)).toBe(
            500 * 1000
        );
    });
    it("never waits longer than the cap, whatever the URL's lifetime", () => {
        const issued = Date.UTC(2026, 10, 17, 17, 0, 0);
        expect(refreshDelayMs(signed("20261117T170000Z", 86400), issued)).toBe(MAX_REFRESH_MS);
    });
    it("never goes below the minimum, even for an already late URL", () => {
        const issued = Date.UTC(2026, 10, 17, 17, 0, 0);
        expect(refreshDelayMs(signed("20261117T170000Z", 1000), issued + 5000 * 1000)).toBe(
            MIN_REFRESH_MS
        );
    });
    it("falls back when the URL carries no lifetime", () => {
        expect(presignedLifetime("https://example.com/a.tif")).toBeNull();
        expect(presignedLifetime("not a url")).toBeNull();
        expect(refreshDelayMs("https://example.com/a.tif")).toBe(FALLBACK_REFRESH_MS);
    });
});

describe("requestPresignedUrl", () => {
    beforeEach(() => jest.clearAllMocks());
    it("returns the URL from the download route", async () => {
        (downloadAsset as jest.Mock).mockResolvedValue([true, "https://s3/x"]);
        await expect(requestPresignedUrl(FILE)).resolves.toBe("https://s3/x");
        expect(downloadAsset).toHaveBeenCalledWith(
            expect.objectContaining({ downloadType: "assetFile", key: "a1/x.tif" })
        );
    });
    it("carries the API's own reason for a refusal", async () => {
        (downloadAsset as jest.Mock).mockResolvedValue([
            false,
            "Bad request",
            400,
            "Asset is quarantined and cannot be downloaded",
        ]);
        await expect(requestPresignedUrl(FILE)).rejects.toBeInstanceOf(RasterDownloadRefused);
        await expect(requestPresignedUrl(FILE)).rejects.toThrow(/quarantined/);
    });
});

describe("keepFresh", () => {
    beforeEach(() => jest.useFakeTimers());
    afterEach(() => jest.useRealTimers());

    it("replaces the URL at 80% of its lifetime and schedules the next replacement", async () => {
        jest.setSystemTime(Date.UTC(2026, 10, 17, 17, 0, 0));
        const request = jest
            .fn()
            .mockResolvedValueOnce(signed("20261117T171320Z", 1000))
            .mockResolvedValueOnce(signed("20261117T172640Z", 1000));
        const onRefresh = jest.fn();
        const handle = keepFresh(FILE, signed("20261117T170000Z", 1000), onRefresh, request);

        await jest.advanceTimersByTimeAsync(799 * 1000);
        expect(request).not.toHaveBeenCalled();
        await jest.advanceTimersByTimeAsync(2 * 1000);
        expect(request).toHaveBeenCalledTimes(1);
        expect(onRefresh).toHaveBeenCalledWith(signed("20261117T171320Z", 1000));

        await jest.advanceTimersByTimeAsync(801 * 1000);
        expect(request).toHaveBeenCalledTimes(2);
        handle.stop();
    });

    it("refreshes on demand once even when asked twice, and stops cleanly", async () => {
        const request = jest.fn().mockResolvedValue(signed("20261117T170000Z", 1000));
        const onRefresh = jest.fn();
        const handle = keepFresh(FILE, signed("20261117T170000Z", 1000), onRefresh, request);
        await Promise.all([handle.refreshNow(), handle.refreshNow()]);
        expect(request).toHaveBeenCalledTimes(1);
        handle.stop();
        await jest.advanceTimersByTimeAsync(10 * 1000 * 1000);
        expect(request).toHaveBeenCalledTimes(1);
    });

    it("retries a failed refresh later and keeps the old URL", async () => {
        const request = jest
            .fn()
            .mockRejectedValueOnce(new Error("network"))
            .mockResolvedValueOnce("https://s3/new");
        const onRefresh = jest.fn();
        const handle = keepFresh(FILE, "https://s3/old", onRefresh, request);
        await handle.refreshNow();
        expect(onRefresh).not.toHaveBeenCalled();
        await jest.advanceTimersByTimeAsync(FALLBACK_REFRESH_MS / 4 + 10);
        expect(onRefresh).toHaveBeenCalledWith("https://s3/new");
        handle.stop();
    });

    it("backs off between failed refreshes and gives up after the cap, with no further requests", async () => {
        const request = jest.fn().mockRejectedValue(new Error("refused"));
        const onRefresh = jest.fn();
        const onGiveUp = jest.fn();
        const handle = keepFresh(FILE, "https://s3/old", onRefresh, request, onGiveUp);
        await handle.refreshNow();
        expect(request).toHaveBeenCalledTimes(1);
        await jest.advanceTimersByTimeAsync(RETRY_BASE_MS + 10);
        expect(request).toHaveBeenCalledTimes(2);
        // The second retry waits twice as long.
        await jest.advanceTimersByTimeAsync(RETRY_BASE_MS + 10);
        expect(request).toHaveBeenCalledTimes(2);
        await jest.advanceTimersByTimeAsync(RETRY_BASE_MS + 10);
        expect(request).toHaveBeenCalledTimes(MAX_REFRESH_FAILURES);
        expect(onGiveUp).toHaveBeenCalledTimes(1);
        await jest.advanceTimersByTimeAsync(24 * 60 * 60 * 1000);
        await handle.refreshNow();
        expect(request).toHaveBeenCalledTimes(MAX_REFRESH_FAILURES);
        expect(onRefresh).not.toHaveBeenCalled();
    });
});
