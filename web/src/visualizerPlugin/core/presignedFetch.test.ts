/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A viewer that reads file bytes itself fetches the presigned URL of the authorized download route
 * instead of following the streaming route's cross-origin 307 (#1623). These tests pin the request
 * S3 receives (no Authorization header, no credentials, the web origin), a URL cache that never
 * outlives the URL, the wording of S3's own errors, the sign-out clear, and the abort of the presign.
 */

import {
    clearPresignedUrlCache,
    fetchPresignedFile,
    getPresignedFileUrl,
    PRESIGNED_LINK_UNAVAILABLE_MESSAGE,
    PRESIGNED_URL_CACHE_LIMIT,
    PRESIGNED_URL_MARGIN_MILLIS,
    PRESIGNED_URL_TTL_MILLIS,
    PresignedReadError,
    setPresignedUrlClock,
} from "./presignedFetch";
import { downloadAsset } from "../../services/APIService";
import { ApiRefusalError, DOWNLOAD_FORBIDDEN_MESSAGE } from "./streamFailure";
import {
    QUARANTINE_REFUSAL_MESSAGE,
    QUARANTINE_REFUSED_DOWNLOAD,
} from "./quarantineRefusal.testdata";
import { clearStoredSession } from "../../utils/sessionManager";

jest.mock("../../services/APIService", () => ({ downloadAsset: jest.fn() }));
jest.mock("aws-amplify/auth", () => ({ signOut: jest.fn() }));
jest.mock("../../utils/authTokenUtils", () => ({
    ensureSessionValid: jest.fn(),
    getCurrentTokenExpiryMs: jest.fn(),
    MISSING_USER_ID_CLAIM_KEY: "auth_missing_user_id_claim",
}));
jest.mock("localforage", () => ({ clear: jest.fn() }));

const mockedDownloadAsset = downloadAsset as jest.Mock;

const REQUEST = { databaseId: "db-1", assetId: "asset-1", key: "asset-1/model.glb" };
const WEB_ORIGIN = "https://vams.example.test";

/** A SigV4 query-string stamp, `X-Amz-Date` in the form S3 uses. */
function amzDate(epochMillis: number): string {
    return new Date(epochMillis).toISOString().replace(/[-:]|\.\d{3}/g, "");
}

describe("presignedFetch", () => {
    const originalFetch = global.fetch;
    let clock = Date.UTC(2026, 10, 17, 12, 0, 0);
    let expiresSeconds = 3600;

    /** The URL the download route returns: signed at the test clock, valid for `expiresSeconds`. */
    const presigned = (key: string) =>
        `https://bucket.s3.example.test/${key}?X-Amz-Date=${amzDate(
            clock
        )}&X-Amz-Expires=${expiresSeconds}&X-Amz-Signature=sig`;

    beforeEach(() => {
        clock = Date.UTC(2026, 10, 17, 12, 0, 0);
        expiresSeconds = 3600;
        setPresignedUrlClock(() => clock);
        clearPresignedUrlCache();
        mockedDownloadAsset.mockReset();
        mockedDownloadAsset.mockImplementation(async ({ key }: { key: string }) => [
            true,
            presigned(key),
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
        expect(url).toBe(presigned("asset-1/model.glb"));
        expect(options).toEqual({ signal });
        expect(options.headers).toBeUndefined();
        expect(options.credentials).toBeUndefined();
    });

    it("reads a dependency from a single-origin bucket: web Origin, no Authorization (#1623)", async () => {
        // A bucket whose CORS rule lists only the web origin. The streaming route's 307 reached it
        // with `Origin: null` and no `Access-Control-Allow-Origin` came back; a read that carries
        // the web origin and no Authorization header is the one it answers.
        const s3Requests: Array<{ url: string; origin: string | null; authorization: any }> = [];
        global.fetch = jest.fn(async (url: string, init?: RequestInit) => {
            const headers = new Headers(init?.headers);
            const origin = headers.get("Origin") ?? WEB_ORIGIN;
            s3Requests.push({ url, origin, authorization: headers.get("Authorization") });
            return origin === WEB_ORIGIN ? { ok: true, status: 200 } : { ok: false, status: 403 };
        }) as any;

        const response = await fetchPresignedFile(
            { ...REQUEST, key: "asset-1/textures/wood.png" },
            "wood.png"
        );

        expect(response.ok).toBe(true);
        expect(s3Requests).toHaveLength(1);
        expect(s3Requests[0].authorization).toBeNull();
        expect(s3Requests[0].origin).toBe(WEB_ORIGIN);
        expect(s3Requests[0].origin).not.toBe("null");
        expect(s3Requests[0].url).not.toContain("/download/stream/");
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

    describe("cache lifetime never outlives the URL (M1)", () => {
        it("reuses a long-lived URL for the cache limit, minus the margin, then asks again", async () => {
            await getPresignedFileUrl(REQUEST, "x");
            clock += PRESIGNED_URL_TTL_MILLIS - 1;
            await getPresignedFileUrl(REQUEST, "x");
            expect(mockedDownloadAsset).toHaveBeenCalledTimes(1);

            clock += 1;
            await getPresignedFileUrl(REQUEST, "x");
            expect(mockedDownloadAsset).toHaveBeenCalledTimes(2);
        });

        it.each([
            // seconds, margin millis (the smaller of 10 s and 20 percent), cache millis
            [1, 200, 800],
            [5, 1000, 4000],
            [30, 6000, 24000],
            [3600, PRESIGNED_URL_MARGIN_MILLIS, PRESIGNED_URL_TTL_MILLIS],
        ])(
            "with presignedUrlTimeoutSeconds %i the entry is never served in the last margin",
            async (seconds, marginMillis, cacheMillis) => {
                expiresSeconds = seconds;
                const signedAt = clock;
                await getPresignedFileUrl(REQUEST, "x");

                // Still served just before the cache lifetime ends...
                clock = signedAt + cacheMillis - 1;
                await getPresignedFileUrl(REQUEST, "x");
                expect(mockedDownloadAsset).toHaveBeenCalledTimes(1);

                // ...and never once the margin of the URL's own lifetime is reached.
                clock = signedAt + cacheMillis;
                await getPresignedFileUrl(REQUEST, "x");
                expect(mockedDownloadAsset).toHaveBeenCalledTimes(2);
                expect(cacheMillis).toBeLessThanOrEqual(seconds * 1000 - marginMillis);
            }
        );

        it("counts the time since the URL was signed", async () => {
            expiresSeconds = 30;
            // The URL was signed 20 s before it arrived: 10 s of life, 2 s of margin, 8 s of cache.
            mockedDownloadAsset.mockImplementation(async ({ key }: { key: string }) => [
                true,
                `https://bucket.s3.example.test/${key}?X-Amz-Date=${amzDate(
                    clock - 20_000
                )}&X-Amz-Expires=30&X-Amz-Signature=sig`,
            ]);
            const arrived = clock;
            await getPresignedFileUrl(REQUEST, "x");

            clock = arrived + 7_999;
            await getPresignedFileUrl(REQUEST, "x");
            expect(mockedDownloadAsset).toHaveBeenCalledTimes(1);

            clock = arrived + 8_000;
            await getPresignedFileUrl(REQUEST, "x");
            expect(mockedDownloadAsset).toHaveBeenCalledTimes(2);
        });

        it("does not cache a URL that does not state its lifetime", async () => {
            mockedDownloadAsset.mockImplementation(async () => [
                true,
                "https://bucket.s3.example.test/k?X-Amz-Signature=sig",
            ]);

            await getPresignedFileUrl(REQUEST, "x");
            await getPresignedFileUrl(REQUEST, "x");

            expect(mockedDownloadAsset).toHaveBeenCalledTimes(2);
        });

        it("does not cache a URL already inside its own margin", async () => {
            mockedDownloadAsset.mockImplementation(async ({ key }: { key: string }) => [
                true,
                `https://bucket.s3.example.test/${key}?X-Amz-Date=${amzDate(
                    clock - 3_600_000
                )}&X-Amz-Expires=3600&X-Amz-Signature=sig`,
            ]);

            await getPresignedFileUrl(REQUEST, "x");
            await getPresignedFileUrl(REQUEST, "x");

            expect(mockedDownloadAsset).toHaveBeenCalledTimes(2);
        });
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

    describe("S3's own errors (L1)", () => {
        it("does not re-presign a 403 for a URL that was just issued (a missing object)", async () => {
            global.fetch = jest.fn(async () => ({ ok: false, status: 403 })) as any;

            await expect(fetchPresignedFile(REQUEST, "x")).rejects.toThrow(
                PRESIGNED_LINK_UNAVAILABLE_MESSAGE
            );
            expect(mockedDownloadAsset).toHaveBeenCalledTimes(1);
            expect(global.fetch).toHaveBeenCalledTimes(1);
        });

        it("re-presigns once on a 403 for a reused URL and reads again", async () => {
            await getPresignedFileUrl(REQUEST, "x");
            mockedDownloadAsset.mockClear();
            global.fetch = jest
                .fn()
                .mockResolvedValueOnce({ ok: false, status: 403 })
                .mockResolvedValueOnce({ ok: true, status: 200 }) as any;

            const response = await fetchPresignedFile(REQUEST, "Failed to load: model.glb");

            expect(response.ok).toBe(true);
            expect(mockedDownloadAsset).toHaveBeenCalledTimes(1);
            expect(global.fetch).toHaveBeenCalledTimes(2);
        });

        it("re-presigns past a cached URL that S3 now refuses", async () => {
            await getPresignedFileUrl(REQUEST, "x");
            mockedDownloadAsset.mockClear();
            global.fetch = jest
                .fn()
                .mockResolvedValueOnce({ ok: false, status: 403 })
                .mockResolvedValueOnce({ ok: true, status: 200 }) as any;

            await fetchPresignedFile(REQUEST, "Failed to load: model.glb");

            expect(mockedDownloadAsset).toHaveBeenCalledTimes(1);
        });

        it.each([403, 404])(
            "words a persistent S3 %i as an unavailable link, not a VAMS permission or not-found",
            async (status) => {
                await getPresignedFileUrl(REQUEST, "x"); // reused, so a 403 is re-presigned once
                mockedDownloadAsset.mockClear();
                global.fetch = jest.fn(async () => ({ ok: false, status })) as any;

                const error = await fetchPresignedFile(REQUEST, "Failed to load: model.glb").catch(
                    (e) => e
                );

                expect(error).toBeInstanceOf(PresignedReadError);
                expect(error.message).toBe(PRESIGNED_LINK_UNAVAILABLE_MESSAGE);
                expect(error.message).not.toBe(DOWNLOAD_FORBIDDEN_MESSAGE);
                expect(error.message).not.toMatch(/permission|could not be found/);
                // Only a 403 is re-presigned (once); a missing object is not retried.
                expect(mockedDownloadAsset).toHaveBeenCalledTimes(status === 403 ? 1 : 0);
            }
        );

        it("keeps the file's own message for another S3 status", async () => {
            global.fetch = jest.fn(async () => ({ ok: false, status: 500 })) as any;

            await expect(fetchPresignedFile(REQUEST, "Failed to load: model.glb")).rejects.toThrow(
                "Failed to load: model.glb (500)"
            );
            expect(mockedDownloadAsset).toHaveBeenCalledTimes(1);
        });

        it("keeps the API-originated wording for a refused presign", async () => {
            mockedDownloadAsset.mockResolvedValueOnce([false, "Forbidden", 403]);

            await expect(fetchPresignedFile(REQUEST, "Failed to load: model.glb")).rejects.toThrow(
                DOWNLOAD_FORBIDDEN_MESSAGE
            );
            expect(global.fetch).not.toHaveBeenCalled();
        });
    });

    it("does not cache a refused presign", async () => {
        mockedDownloadAsset.mockResolvedValueOnce([false, "Forbidden", 403]);

        await expect(fetchPresignedFile(REQUEST, "Failed to load: model.glb")).rejects.toThrow(
            DOWNLOAD_FORBIDDEN_MESSAGE
        );

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

    describe("sign-out (L2)", () => {
        it("drops every cached URL when the session is cleared", async () => {
            await getPresignedFileUrl(REQUEST, "x");
            await getPresignedFileUrl(REQUEST, "x");
            expect(mockedDownloadAsset).toHaveBeenCalledTimes(1);

            clearStoredSession();

            await getPresignedFileUrl(REQUEST, "x");
            expect(mockedDownloadAsset).toHaveBeenCalledTimes(2);
        });
    });

    describe("abort (L3)", () => {
        it("hands the abort signal to the presign request", async () => {
            const controller = new AbortController();

            await getPresignedFileUrl({ ...REQUEST, signal: controller.signal }, "x");

            expect(mockedDownloadAsset.mock.calls[0][0].signal).toBe(controller.signal);
        });

        it("stops with an AbortError, not a failure message, when closed during the presign", async () => {
            const controller = new AbortController();
            mockedDownloadAsset.mockImplementation(async () => {
                controller.abort();
                return [false, "The operation was aborted"];
            });

            const error = await fetchPresignedFile(
                { ...REQUEST, signal: controller.signal },
                "Failed to load: model.glb"
            ).catch((e) => e);

            expect(error.name).toBe("AbortError");
            expect(global.fetch).not.toHaveBeenCalled();
        });
    });
});
