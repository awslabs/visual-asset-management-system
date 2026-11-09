/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/** The message of a plain authorization denial: the API's "Not Authorized" or API Gateway's own. */
const PERMISSION_DENIAL = /^(not authorized|user is not authorized\b|forbidden$|access denied)/i;

/**
 * What a failed viewer file request reported: the HTTP status, absent when the request got no
 * readable response, and the JSON body when the response carried one.
 */
export interface StreamFailure {
    status?: number;
    body?: any;
}

/**
 * The HTTP status a failed `downloadAsset` call reported. `downloadAsset` returns a tuple
 * `[false, message, status?, apiMessage?]` on failure (APIService.ts): the status is the
 * download-authorization response's status (401/403 denied, 410 archived, 404 missing, 400 for a
 * refusal that names its reason), absent when a block by the AWS WAF web ACL ate the response.
 * `false` (not a tuple) is a non-authorization client error.
 */
export function downloadResponseStatus(response: unknown): number | undefined {
    if (Array.isArray(response) && typeof response[2] === "number") return response[2];
    return undefined;
}

/** The API's own `message` a failed `downloadAsset` tuple carries (its fourth entry), if any. */
export function downloadResponseApiMessage(response: unknown): string | undefined {
    if (Array.isArray(response) && typeof response[3] === "string") return response[3];
    return undefined;
}

/** Statuses whose sentence the viewers word themselves (below), whatever the API's body says. */
const STATUSES_WORDED_HERE = new Set([401, 403, 404, 410, 429]);

/**
 * The API's own reason for refusing a file request, to show in place of a viewer's or a preview's
 * generic failure text (D166): the trimmed `message` of a 4xx answer whose status is not one worded
 * here, for example the `400` "Asset is quarantined and cannot be downloaded" every file route
 * answers for a quarantined asset while `app.compliance.quarantineBlocksDownload` is on. undefined
 * for any other failure (a 5xx, no response, no message), which keeps the caller's own text.
 *
 * Only what the API answered is shown: nothing here, or in any caller, reads the asset's
 * compliance state or decides anything in the browser; the API decides and the web displays it.
 */
export function apiRefusalMessage(
    status: number | undefined,
    apiMessage: unknown
): string | undefined {
    if (typeof status !== "number" || status < 400 || status >= 500) return undefined;
    if (STATUSES_WORDED_HERE.has(status) || typeof apiMessage !== "string") return undefined;
    const message = apiMessage.trim();
    return message || undefined;
}

/**
 * Why a preview or a download did not load, for a surface that otherwise shows its own fixed text
 * ("No preview available ..."): the API's refusal reason (`apiRefusalMessage`) of a failed
 * `downloadAsset` tuple, else null.
 */
export function downloadRefusalReason(response: unknown): string | null {
    return (
        apiRefusalMessage(downloadResponseStatus(response), downloadResponseApiMessage(response)) ??
        null
    );
}

/**
 * The user-facing message for a failed `downloadAsset`, decided ONLY by the status the API returned
 * (never by the caller's role or any client-side rule): 401 is a missing or expired session, 403 a
 * permission denial, 410 an
 * archived version, 404 a missing file, and anything else returns `subject`, the viewer's own
 * generic wording (e.g. "Failed to download PDF file").
 *
 * This is the one place every core viewer turns a download failure into a sentence, so the wording
 * is identical across them (#1211). The Text Diff Viewer classifies per side with the same 403
 * wording.
 */
export function downloadFailureMessage(response: unknown, subject: string): string {
    return downloadStatusMessage(
        downloadResponseStatus(response),
        subject,
        downloadResponseApiMessage(response)
    );
}

/** What a viewer shows when the API refuses a file's download (403). */
export const DOWNLOAD_FORBIDDEN_MESSAGE =
    "You do not have permission to view or download this file.";
/** What a viewer shows when the API answers 401: the session is missing or has expired. */
export const DOWNLOAD_UNAUTHENTICATED_MESSAGE =
    "Your session has expired. Sign in again to view this file.";
/** What a viewer shows when the API answers 410 for a file version. */
export const DOWNLOAD_ARCHIVED_MESSAGE =
    "This file version has been archived and is not available.";
/** What a viewer shows when the API answers 404 for a file or version. */
export const DOWNLOAD_NOT_FOUND_MESSAGE = "This file or version could not be found.";

/**
 * `downloadFailureMessage` for a viewer that fetched the file itself (the streaming route) and has
 * the response's HTTP status rather than a `downloadAsset` tuple. Another 4xx with the API's own
 * `apiMessage` returns that message (`apiRefusalMessage`, D166). `status` undefined (no response,
 * e.g. a WAF block or a network error) and any other status return `subject`.
 */
export function downloadStatusMessage(
    status: number | undefined,
    subject: string,
    apiMessage?: unknown
): string {
    if (status === 401) return DOWNLOAD_UNAUTHENTICATED_MESSAGE;
    if (status === 403) return DOWNLOAD_FORBIDDEN_MESSAGE;
    if (status === 410) return DOWNLOAD_ARCHIVED_MESSAGE;
    if (status === 404) return DOWNLOAD_NOT_FOUND_MESSAGE;
    return apiRefusalMessage(status, apiMessage) ?? subject;
}

/** The `message` of a failed response's JSON body (read from a clone), if it carries one. */
export async function responseApiMessage(response: Response): Promise<string | undefined> {
    try {
        const body = await response.clone().json();
        return typeof body?.message === "string" ? body.message : undefined;
    } catch {
        return undefined;
    }
}

/**
 * `downloadStatusMessage` for a failed streaming-route response a viewer fetched itself, with the
 * API's own message read from the response body.
 */
export async function streamResponseFailureMessage(
    response: Response,
    subject: string
): Promise<string> {
    return downloadStatusMessage(response.status, subject, await responseApiMessage(response));
}

/**
 * Requests `url` again with the viewer's `headers` to learn why a viewer library failed to load it.
 * Potree's loader rejects without the HTTP status, so the status comes from this second request.
 */
export async function probeStreamFailure(
    url: string,
    headers: HeadersInit
): Promise<StreamFailure> {
    try {
        const response = await fetch(url, { headers });
        if (response.ok) return { status: response.status };
        try {
            return { status: response.status, body: await response.json() };
        } catch {
            return { status: response.status };
        }
    } catch {
        return {};
    }
}

/**
 * A sentence naming why `subject` (for example "the point cloud preview") failed to load. A request
 * with no readable response is what the browser reports when the CORS preflight is refused, which is
 * how a block by the deployment's AWS WAF web ACL, or a rate limit, reaches a streaming viewer; a block
 * on the request itself arrives as the API stage's WAF_FILTERED 403 with `blockedBy: "waf"`.
 */
export function describeStreamFailure(subject: string, failure: StreamFailure): string {
    const { status, body } = failure;
    if (!status) {
        return `Unable to reach ${subject}. The request failed before VAMS answered it: a network error, a block by the deployment's AWS WAF web ACL, or a rate limit.`;
    }
    if (status >= 200 && status < 300) {
        return `Unable to display ${subject}: the viewer could not read the file.`;
    }
    if (status === 403 && body?.blockedBy === "waf" && typeof body.message === "string") {
        const requestId =
            typeof body.requestId === "string" && body.requestId
                ? ` (request ID ${body.requestId})`
                : "";
        return `Unable to load ${subject}: ${body.message}${requestId}.`;
    }
    if (status === 401) {
        return `Unable to load ${subject}: your session is no longer valid. Sign in again.`;
    }
    if (status === 403) {
        // A denial by the authorizer or by Casbin carries no reason of its own; any other 403 (an
        // asset that does not allow file distribution) names what to change.
        const reason = typeof body?.message === "string" ? body.message.trim() : "";
        if (!reason || PERMISSION_DENIAL.test(reason)) {
            return `Unable to load ${subject}: you don't have permission to view it.`;
        }
        return `Unable to load ${subject}: ${reason.replace(/\.$/, "")}.`;
    }
    if (status === 404) return `Unable to load ${subject}: it was not found.`;
    if (status === 429) return `Unable to load ${subject}: too many requests. Try again shortly.`;
    // Any other refusal that names its reason (a quarantined asset with the download block on).
    const refusal = apiRefusalMessage(status, body?.message);
    if (refusal) return `Unable to load ${subject}: ${refusal.replace(/\.$/, "")}.`;
    return `Unable to load ${subject} (HTTP ${status}).`;
}
