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
 * a short time that never outlives the URL (`presignedUrlCache.ts`), is dropped on sign-out
 * (`sessionManager.clearStoredSession`) and is never logged.
 *
 * A failure of S3 itself is not the API's answer: an expired or invalid signature or a missing
 * object is worded as an unavailable link (`PresignedReadError`), after one fresh presign and one
 * more read when the URL was a reused one.
 */

import { downloadAsset } from "../../services/APIService";
import {
    ApiRefusalError,
    apiRefusalMessage,
    downloadFailureMessage,
    downloadResponseApiMessage,
    downloadResponseStatus,
    isFileNotFoundAnswer,
} from "./streamFailure";
import {
    dropCachedPresignedUrl,
    readCachedPresignedUrl,
    writeCachedPresignedUrl,
} from "./presignedUrlCache";

export {
    clearPresignedUrlCache,
    PRESIGNED_URL_CACHE_LIMIT,
    PRESIGNED_URL_MARGIN_MILLIS,
    PRESIGNED_URL_TTL_MILLIS,
    setPresignedUrlClock,
} from "./presignedUrlCache";

/** What a viewer shows when S3 (not the API) refused or could not find the file. */
export const PRESIGNED_LINK_UNAVAILABLE_MESSAGE =
    "The download link for this file expired or the file is unavailable. Reload the viewer to try again.";

/** S3 answered the presigned read with an error status. */
export class PresignedReadError extends Error {
    status: number;
    constructor(message: string, status: number) {
        super(message);
        this.name = "PresignedReadError";
        this.status = status;
    }
}

export interface PresignedFileRequest {
    databaseId: string;
    assetId: string;
    /** The file key, as the stream route takes it (asset id prefix included). */
    key: string;
    /** S3 version of the file; used for the primary file only. */
    versionId?: string;
    /** Logical asset version; takes precedence over `versionId`. */
    assetVersionId?: string;
    /** Aborts the presign request and the read. */
    signal?: AbortSignal;
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

function abortError(): Error {
    const error = new Error("The operation was aborted");
    error.name = "AbortError";
    return error;
}

/**
 * The error for a failed presign: an `ApiRefusalError` carrying the API's reason when it refused
 * the file with one, else an Error worded from the status the API returned (never a client rule).
 * Either carries that status as `status` (undefined when no API answer came back), and
 * `fileNotFound` when the API answered that the file is not in the bucket, so a caller can tell a
 * missing file from a refused or failed request.
 */
function presignFailure(result: unknown, subject: string): Error {
    const status = downloadResponseStatus(result);
    const apiMessage = downloadResponseApiMessage(result);
    const refusal = apiRefusalMessage(status, apiMessage);
    const error = refusal
        ? new ApiRefusalError(refusal)
        : new Error(downloadFailureMessage(result, subject));
    return Object.assign(error, {
        status,
        fileNotFound: isFileNotFoundAnswer(status, apiMessage),
    });
}

/** A presigned URL for the file, from the authorized download route (cached while it is safe). */
export async function getPresignedFileUrl(
    request: PresignedFileRequest,
    subject: string,
    options: { fresh?: boolean } = {}
): Promise<string> {
    return (await resolvePresignedFileUrl(request, subject, options)).url;
}

export async function resolvePresignedFileUrl(
    request: PresignedFileRequest,
    subject: string,
    options: { fresh?: boolean } = {}
): Promise<{ url: string; cached: boolean }> {
    const key = cacheKey(request);
    if (options.fresh) {
        dropCachedPresignedUrl(key);
    } else {
        const cached = readCachedPresignedUrl(key);
        if (cached) return { url: cached, cached: true };
    }

    const result: any = await downloadAsset({
        databaseId: request.databaseId,
        assetId: request.assetId,
        key: request.key,
        versionId: request.versionId,
        assetVersionId: request.assetVersionId,
        downloadType: "assetFile",
        signal: request.signal,
    });
    // downloadAsset reports an aborted request as a failure tuple; a closed viewer must stop.
    if (request.signal?.aborted) throw abortError();
    if (!Array.isArray(result) || result[0] !== true || typeof result[1] !== "string") {
        throw presignFailure(result, subject);
    }
    writeCachedPresignedUrl(key, result[1]);
    return { url: result[1], cached: false };
}

/**
 * Fetches the file's bytes from its presigned URL, with no `Authorization` header and no
 * credentials, so the request is an ordinary CORS read from the web origin. A 403 from S3 for a
 * reused URL (an expired signature) is read once more through a fresh presign. Returns the OK response,
 * or throws: a failed presign as the API's answer, an S3 error status as a `PresignedReadError`.
 */
export async function fetchPresignedFile(
    request: PresignedFileRequest,
    subject: string
): Promise<Response> {
    const first = await resolvePresignedFileUrl(request, subject);
    let response = await fetch(first.url, { signal: request.signal });
    // A URL just issued cannot have expired, so only a reused one is worth a second presign.
    if (!response.ok && response.status === 403 && first.cached) {
        response = await fetch(await getPresignedFileUrl(request, subject, { fresh: true }), {
            signal: request.signal,
        });
    }
    if (!response.ok) {
        const unavailable = response.status === 403 || response.status === 404;
        throw new PresignedReadError(
            unavailable ? PRESIGNED_LINK_UNAVAILABLE_MESSAGE : `${subject} (${response.status})`,
            response.status
        );
    }
    return response;
}
