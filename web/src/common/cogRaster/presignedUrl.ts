/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { downloadAsset } from "../../services/APIService";
import { downloadFailureMessage } from "../../visualizerPlugin/core/streamFailure";

/** One file of one asset, as the download route addresses it. */
export interface RasterFileRef {
    databaseId: string;
    assetId: string;
    key: string;
    versionId?: string;
    assetVersionId?: string;
}

/** A download the API refused, carrying the sentence to show for it. */
export class RasterDownloadRefused extends Error {}

/** Share of a presigned URL's lifetime after which it is replaced. */
export const REFRESH_FRACTION = 0.8;

/** Used when a URL carries no readable lifetime. Shorter than any lifetime the API issues. */
export const FALLBACK_REFRESH_MS = 4 * 60 * 1000;

/** Never refresh more often than this, whatever the URL says. */
export const MIN_REFRESH_MS = 10 * 1000;

/**
 * Never wait longer than this before replacing a URL. A presigned URL stops working when the
 * credentials that signed it expire, which can be well before its X-Amz-Expires lifetime (the API's
 * role session is shorter than the 24-hour default URL lifetime), so the lifetime alone is not a
 * safe schedule. A read refused in between is caught by the tile error handler.
 */
export const MAX_REFRESH_MS = 45 * 60 * 1000;

/**
 * The moment a SigV4 presigned URL was signed and how long it lives, read from its X-Amz-Date and
 * X-Amz-Expires query parameters. Null when either is missing or unreadable.
 */
export function presignedLifetime(url: string): { issuedAtMs: number; lifetimeMs: number } | null {
    let params: URLSearchParams;
    try {
        params = new URL(url).searchParams;
    } catch {
        return null;
    }
    const date = params.get("X-Amz-Date");
    const expires = Number(params.get("X-Amz-Expires"));
    const match = date ? /^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/.exec(date) : null;
    if (!match || !Number.isFinite(expires) || expires <= 0) return null;
    const [, y, mo, d, h, mi, s] = match;
    const issuedAtMs = Date.UTC(+y, +mo - 1, +d, +h, +mi, +s);
    return { issuedAtMs, lifetimeMs: expires * 1000 };
}

/** Milliseconds from `nowMs` until the URL should be replaced: about 80% of its lifetime, at most MAX_REFRESH_MS. */
export function refreshDelayMs(url: string, nowMs: number = Date.now()): number {
    const lifetime = presignedLifetime(url);
    if (!lifetime) return FALLBACK_REFRESH_MS;
    const due = lifetime.issuedAtMs + lifetime.lifetimeMs * REFRESH_FRACTION;
    return Math.min(MAX_REFRESH_MS, Math.max(MIN_REFRESH_MS, due - nowMs));
}

/** Asks the download route for a presigned URL. The API decides access; a refusal carries its reason. */
export async function requestPresignedUrl(file: RasterFileRef): Promise<string> {
    const response = await downloadAsset({
        databaseId: file.databaseId,
        assetId: file.assetId,
        key: file.key,
        versionId: file.versionId,
        assetVersionId: file.assetVersionId,
        downloadType: "assetFile",
    });
    if (Array.isArray(response) && response[0] === true && typeof response[1] === "string") {
        return response[1];
    }
    const message = downloadFailureMessage(response, "Failed to download file");
    throw message === "Failed to download file"
        ? new Error(message)
        : new RasterDownloadRefused(message);
}

/** Failed refreshes in a row after which the layer is given up, with a message. */
export const MAX_REFRESH_FAILURES = 3;

/** Delay before the first retry of a failed refresh; it doubles with each further failure. */
export const RETRY_BASE_MS = FALLBACK_REFRESH_MS / 4;

/**
 * Keeps a presigned URL fresh. `onRefresh` is called with each replacement URL, about 80% of the
 * way through the previous one's lifetime (at most MAX_REFRESH_MS); `refreshNow` is for a failed read.
 * A failed refresh is retried after RETRY_BASE_MS, then twice and four times that; after
 * MAX_REFRESH_FAILURES failures in a row it stops and calls `onGiveUp` (the API keeps refusing, or
 * cannot be reached). Returns a stop function.
 */
export function keepFresh(
    file: RasterFileRef,
    firstUrl: string,
    onRefresh: (url: string) => void,
    request: (file: RasterFileRef) => Promise<string> = requestPresignedUrl,
    onGiveUp?: () => void
): { stop: () => void; refreshNow: () => Promise<void> } {
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let inFlight: Promise<void> | null = null;
    let failures = 0;

    const schedule = (url: string, delay?: number) => {
        if (stopped) return;
        if (timer) clearTimeout(timer);
        timer = setTimeout(() => void refresh(), delay ?? refreshDelayMs(url));
    };

    const refresh = (): Promise<void> => {
        if (stopped) return Promise.resolve();
        if (inFlight) return inFlight;
        inFlight = request(file)
            .then((url) => {
                if (stopped) return;
                failures = 0;
                onRefresh(url);
                schedule(url);
            })
            .catch(() => {
                if (stopped) return;
                failures += 1;
                if (failures >= MAX_REFRESH_FAILURES) {
                    stopped = true;
                    if (timer) clearTimeout(timer);
                    onGiveUp?.();
                    return;
                }
                schedule(firstUrl, RETRY_BASE_MS * 2 ** (failures - 1));
            })
            .finally(() => {
                inFlight = null;
            });
        return inFlight;
    };

    schedule(firstUrl);
    return {
        stop: () => {
            stopped = true;
            if (timer) clearTimeout(timer);
        },
        refreshNow: refresh,
    };
}
