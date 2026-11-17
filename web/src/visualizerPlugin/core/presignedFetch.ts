/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Reads a file straight from S3 through a presigned URL that the authorized download route issued
 * (`downloadAsset`), for a viewer that fetches file bytes itself.
 *
 * The streaming route answers a cross-origin 307 to the presigned URL. A browser follows that
 * redirect with `Origin: null`, which an external bucket's documented single-origin CORS rule
 * (the VAMS web origin only) does not match, so S3 sends no `Access-Control-Allow-Origin` and the
 * read fails (#1623). A fetch made straight to the presigned URL carries the web origin and
 * matches that rule.
 *
 * Authorization stays in the API: the presign call is the same authorized download route every
 * other viewer uses, and a refusal reaches the user as the API's own answer. No `Authorization`
 * header is ever sent to S3. A presigned URL is a bearer credential: it is held in memory only, for
 * a short time, and is never logged.
 */

import { downloadAsset } from "../../services/APIService";
import {
    ApiRefusalError,
    apiRefusalMessage,
    downloadFailureMessage,
    downloadResponseApiMessage,
    downloadResponseStatus,
} from "./streamFailure";

/**
 * How long a presigned URL is reused. It stays well under the lifetime the download route gives a
 * URL, so a cached URL never reaches S3 already expired.
 */
export const PRESIGNED_URL_TTL_MILLIS = 60_000;

/** Upper bound on cached URLs, so a viewer that opens many files cannot grow the cache without limit. */
export const PRESIGNED_URL_CACHE_LIMIT = 200;

export interface PresignedFileRequest {
    databaseId: string;
    assetId: string;
    /** The file key, as the stream route takes it (asset id prefix included). */
    key: string;
    /** S3 version of the file; used for the primary file only. */
    versionId?: string;
    /** Logical asset version; takes precedence over `versionId`. */
    assetVersionId?: string;
    signal?: AbortSignal;
}

interface CacheEntry {
    url: string;
    expiresAt: number;
}

const urlCache = new Map<string, CacheEntry>();

/** Test hook: the clock the cache reads. */
let now: () => number = () => Date.now();

export function setPresignedUrlClock(clock: (() => number) | null): void {
    now = clock ?? (() => Date.now());
}

export function clearPresignedUrlCache(): void {
    urlCache.clear();
}

function cacheKey(request: PresignedFileRequest): string {
    return JSON.stringify([
        request.databaseId,
        request.assetId,
        request.key,
        request.assetVersionId || "",
        request.assetVersionId ? "" : request.versionId || "",
    ]);
}

function readCache(key: string): string | undefined {
    const entry = urlCache.get(key);
    if (!entry) return undefined;
    if (entry.expiresAt <= now()) {
        urlCache.delete(key);
        return undefined;
    }
    return entry.url;
}

function writeCache(key: string, url: string): void {
    const current = now();
    urlCache.forEach((entry, existing) => {
        if (entry.expiresAt <= current) urlCache.delete(existing);
    });
    while (urlCache.size >= PRESIGNED_URL_CACHE_LIMIT) {
        const oldest = urlCache.keys().next().value;
        if (oldest === undefined) break;
        urlCache.delete(oldest);
    }
    urlCache.set(key, { url, expiresAt: current + PRESIGNED_URL_TTL_MILLIS });
}

/**
 * The error for a failed presign: an `ApiRefusalError` carrying the API's reason when it refused
 * the file with one, else an Error worded from the status the API returned (never a client rule).
 */
function presignFailure(result: unknown, subject: string): Error {
    const refusal = apiRefusalMessage(
        downloadResponseStatus(result),
        downloadResponseApiMessage(result)
    );
    if (refusal) return new ApiRefusalError(refusal);
    return new Error(downloadFailureMessage(result, subject));
}

/** A presigned URL for the file, from the authorized download route (cached briefly). */
export async function getPresignedFileUrl(
    request: PresignedFileRequest,
    subject: string
): Promise<string> {
    const key = cacheKey(request);
    const cached = readCache(key);
    if (cached) return cached;

    const result: any = await downloadAsset({
        databaseId: request.databaseId,
        assetId: request.assetId,
        key: request.key,
        versionId: request.versionId,
        assetVersionId: request.assetVersionId,
        downloadType: "assetFile",
    });
    if (!Array.isArray(result) || result[0] !== true || typeof result[1] !== "string") {
        throw presignFailure(result, subject);
    }
    writeCache(key, result[1]);
    return result[1];
}

/**
 * Fetches the file's bytes from its presigned URL, with no `Authorization` header and no
 * credentials, so the request is an ordinary CORS read from the web origin. Returns the response;
 * the caller checks `ok`. A failed presign throws (see `presignFailure`).
 */
export async function fetchPresignedFile(
    request: PresignedFileRequest,
    subject: string
): Promise<Response> {
    const url = await getPresignedFileUrl(request, subject);
    return fetch(url, { signal: request.signal });
}
