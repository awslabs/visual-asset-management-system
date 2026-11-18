/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * In-memory cache of presigned download URLs for `presignedFetch.ts`. It has no imports so the
 * session manager can clear it on sign-out without an import cycle through the API client.
 *
 * A presigned URL is a bearer credential: it is held in memory only, never logged or persisted, and
 * is dropped when the session ends. An entry never outlives its URL: its lifetime comes from the
 * URL's own `X-Amz-Date` and `X-Amz-Expires`, and it is never served with less than a safety margin
 * left (the smaller of 10 seconds and 20 percent of the URL's lifetime).
 */

/** The longest an entry is reused, however long the URL itself lives. */
export const PRESIGNED_URL_TTL_MILLIS = 60_000;

/** Upper bound on cached URLs, so a viewer that opens many files cannot grow the cache without limit. */
export const PRESIGNED_URL_CACHE_LIMIT = 200;

/** The most time ever kept in reserve before a URL expires. */
export const PRESIGNED_URL_MARGIN_MILLIS = 10_000;

/** The share of a URL's lifetime kept in reserve when that is smaller than the margin above. */
export const PRESIGNED_URL_MARGIN_RATIO = 0.2;

interface CacheEntry {
    url: string;
    /** The entry is served only while the clock is before this instant. */
    usableUntil: number;
}

const urlCache = new Map<string, CacheEntry>();

let now: () => number = () => Date.now();

/** Test hook: the clock the cache reads. */
export function setPresignedUrlClock(clock: (() => number) | null): void {
    now = clock ?? (() => Date.now());
}

export function clearPresignedUrlCache(): void {
    urlCache.clear();
}

/** `20260102T030405Z` to epoch milliseconds, undefined when it is not that format. */
function parseAmzDate(value: string | null): number | undefined {
    const match = value?.match(/^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$/);
    if (!match) return undefined;
    const [, y, mo, d, h, mi, s] = match.map(Number);
    return Date.UTC(y, mo - 1, d, h, mi, s);
}

/**
 * How long a presigned URL stays valid from now, in milliseconds, read from its own query string
 * (`X-Amz-Expires` seconds, less the age from `X-Amz-Date`). undefined when the URL does not say
 * (not a SigV4 URL), which means it is not cached. A client clock ahead of the signer only shortens
 * the answer; one behind it is treated as no age, so the answer is never too long.
 */
export function presignedUrlRemainingMillis(url: string): number | undefined {
    let params: URLSearchParams;
    try {
        params = new URL(url).searchParams;
    } catch {
        return undefined;
    }
    const expires = Number(params.get("X-Amz-Expires"));
    if (!Number.isFinite(expires) || expires <= 0) return undefined;
    const signedAt = parseAmzDate(params.get("X-Amz-Date"));
    const age = signedAt === undefined ? 0 : Math.max(0, now() - signedAt);
    return expires * 1000 - age;
}

/** Reads a URL that still has its margin left; an entry past it is dropped. */
export function readCachedPresignedUrl(key: string): string | undefined {
    const entry = urlCache.get(key);
    if (!entry) return undefined;
    if (now() >= entry.usableUntil) {
        urlCache.delete(key);
        return undefined;
    }
    return entry.url;
}

export function dropCachedPresignedUrl(key: string): void {
    urlCache.delete(key);
}

/**
 * Caches `url` for as long as it is safe: at most PRESIGNED_URL_TTL_MILLIS, and only while more
 * than the margin of its own remaining lifetime is left. A URL with no readable lifetime, or none
 * left after the margin, is not cached.
 */
export function writeCachedPresignedUrl(key: string, url: string): void {
    const remaining = presignedUrlRemainingMillis(url);
    if (remaining === undefined) return;
    const margin = Math.min(PRESIGNED_URL_MARGIN_MILLIS, remaining * PRESIGNED_URL_MARGIN_RATIO);
    const lifetime = Math.min(PRESIGNED_URL_TTL_MILLIS, remaining - margin);
    if (lifetime <= 0) return;

    const current = now();
    urlCache.forEach((entry, existing) => {
        if (current >= entry.usableUntil) urlCache.delete(existing);
    });
    while (urlCache.size >= PRESIGNED_URL_CACHE_LIMIT) {
        const oldest = urlCache.keys().next().value;
        if (oldest === undefined) break;
        urlCache.delete(oldest);
    }
    urlCache.set(key, { url, usableUntil: current + lifetime });
}
