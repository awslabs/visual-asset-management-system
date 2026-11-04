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
 * Requests `url` again with the viewer's `headers` to learn why a viewer library failed to load it.
 * Potree's loader rejects without the HTTP status, so the status comes from this second request.
 */
export async function probeStreamFailure(
    url: string,
    headers: Record<string, string>
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
    return `Unable to load ${subject} (HTTP ${status}).`;
}
