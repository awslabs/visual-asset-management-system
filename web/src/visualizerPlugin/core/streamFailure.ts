/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { isUnanswered } from "../../services/networkFailure";

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

/**
 * The reason the download route gives for a key that is not in the bucket. The route checks the
 * object before it signs a URL and answers a missing one with a `400` carrying this text
 * (`MSG_FILE_NOT_FOUND` in `backend/backend/common/downloadEligibility.py`). The route's whole answer
 * is in `fileNotFoundBody.testdata.json`, which a web test and a backend handler test both compare,
 * so the two cannot drift apart unnoticed.
 */
export const DOWNLOAD_ROUTE_FILE_NOT_FOUND = "File not found in S3";

/** Whether a failed download answer is the route's missing-file refusal (a `400` naming it). */
export function isFileNotFoundAnswer(status: number | undefined, apiMessage: unknown): boolean {
    return (
        status === 400 &&
        typeof apiMessage === "string" &&
        apiMessage.trim().endsWith(DOWNLOAD_ROUTE_FILE_NOT_FOUND)
    );
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
 * The message of a failed `downloadAsset` tuple that carries no status because no API answer came
 * back: a request the deployment's AWS WAF web ACL blocked (its message with the request ID) or one
 * that never reached the service (`NETWORK_FAILURE_MESSAGE`). `downloadAsset` marks only these two
 * (`markUnanswered`); any other tuple, such as a status-less failure for another reason, gives
 * undefined.
 */
function unansweredDownloadMessage(response: unknown): string | undefined {
    if (!isUnanswered(response) || (response as unknown[])[0] !== false) return undefined;
    const message = (response as unknown[])[1];
    return typeof message === "string" ? message.trim() || undefined : undefined;
}

/**
 * Why a preview or a download did not load, for a surface that otherwise shows its own fixed text
 * ("No preview available ..."): the API's refusal reason (`apiRefusalMessage`) of a failed
 * `downloadAsset` tuple, or the message of one that got no API answer (a WAF block, no response),
 * else null.
 */
export function downloadRefusalReason(response: unknown): string | null {
    return (
        apiRefusalMessage(downloadResponseStatus(response), downloadResponseApiMessage(response)) ??
        unansweredDownloadMessage(response) ??
        null
    );
}

/**
 * The user-facing message for a failed `downloadAsset`, decided ONLY by the status the API returned
 * (never by the caller's role or any client-side rule): 401 is a missing or expired session, 403 a
 * permission denial, 410 an
 * archived version, 404 a missing file, a request with no API answer (a WAF block, no response) its
 * own message, and anything else returns `subject`, the viewer's own generic wording (e.g. "Failed
 * to download PDF file").
 *
 * This is the one place every core viewer turns a download failure into a sentence, so the wording
 * is identical across them (#1211). The Text Diff Viewer classifies per side with the same 403
 * wording.
 */
export function downloadFailureMessage(response: unknown, subject: string): string {
    const unanswered = unansweredDownloadMessage(response);
    if (unanswered) return unanswered;
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

/**
 * The text of `streamResponseFailure` for a failed streaming-route response a viewer fetched
 * itself, with the API's own message read from the response body.
 */
export async function streamResponseFailureMessage(
    response: Response,
    subject: string
): Promise<string> {
    return (await streamResponseFailure(response, subject)).message;
}

/**
 * An error whose message is the API's own reason for refusing a file (`apiRefusalMessage`, D166),
 * for example "Asset is quarantined and cannot be downloaded". A viewer shows it with no format or
 * file hint under it ("Please ensure the file is a supported 3D format", D170): the API refused the
 * file, and nothing about the file would change that.
 */
export class ApiRefusalError extends Error {
    constructor(message: string) {
        super(message);
        this.name = "ApiRefusalError";
    }
}

/** Whether `error` is the API's refusal of a file (`ApiRefusalError`). */
export function isApiRefusalError(error: unknown): error is ApiRefusalError {
    return error instanceof ApiRefusalError;
}

/**
 * What a streaming-route 403 says beyond a plain denial: the message and request ID of a block by
 * the deployment's AWS WAF web ACL (`blockedBy: "waf"`), or the route's own reason (an asset that
 * does not allow file distribution). undefined for a denial by the authorizer or by Casbin, which
 * carries no reason of its own.
 */
function forbiddenReason(body: any): string | undefined {
    if (body?.blockedBy === "waf" && typeof body.message === "string") {
        const requestId =
            typeof body.requestId === "string" && body.requestId
                ? ` (request ID ${body.requestId})`
                : "";
        return `${body.message}${requestId}`;
    }
    const reason = typeof body?.message === "string" ? body.message.trim() : "";
    return reason && !PERMISSION_DENIAL.test(reason) ? reason : undefined;
}

/** The JSON body of a failed response (read from a clone), if it carries one. */
async function responseBody(response: Response): Promise<any> {
    try {
        return await response.clone().json();
    } catch {
        return undefined;
    }
}

/**
 * The error a viewer throws for a failed streaming-route response it fetched itself: an
 * `ApiRefusalError` carrying the reason when the API refused the file with one, or when a 403 says
 * more than a plain denial (an AWS WAF block with its request ID, an asset that is not
 * distributable), else an Error with the worded 401/403/404/410 sentence or `subject`, which a
 * 429 also gets.
 */
export async function streamResponseFailure(response: Response, subject: string): Promise<Error> {
    const body = await responseBody(response);
    const apiMessage = typeof body?.message === "string" ? body.message : undefined;
    const refusal =
        response.status === 403
            ? forbiddenReason(body)
            : apiRefusalMessage(response.status, apiMessage);
    if (refusal) return new ApiRefusalError(refusal);
    return new Error(downloadStatusMessage(response.status, subject, apiMessage));
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
    if (status === 401) {
        return `Unable to load ${subject}: your session is no longer valid. Sign in again.`;
    }
    if (status === 403) {
        const reason = forbiddenReason(body);
        if (!reason) return `Unable to load ${subject}: you don't have permission to view it.`;
        return `Unable to load ${subject}: ${reason.replace(/\.$/, "")}.`;
    }
    if (status === 404) return `Unable to load ${subject}: it was not found.`;
    if (status === 429) return `Unable to load ${subject}: too many requests. Try again shortly.`;
    // Any other refusal that names its reason (a quarantined asset with the download block on).
    const refusal = apiRefusalMessage(status, body?.message);
    if (refusal) return `Unable to load ${subject}: ${refusal.replace(/\.$/, "")}.`;
    return `Unable to load ${subject} (HTTP ${status}).`;
}
