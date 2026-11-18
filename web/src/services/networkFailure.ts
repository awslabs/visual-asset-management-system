/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

// Kept apart from apiClient.ts, which re-exports both, so a service whose tests mock apiClient still
// gets the real check from here.

/**
 * Message for a request that never produced a readable response. `fetch` rejects with a
 * TypeError ("Failed to fetch", "NetworkError when attempting to fetch resource.", "Load failed")
 * when the network fails or the browser drops the response, for example when a web ACL also
 * blocks a cross-origin request's CORS preflight.
 */
export const NETWORK_FAILURE_MESSAGE =
    "The request could not reach the service. It may have been blocked by a firewall or the network.";

/** Whether an error is a request that never reached the service or whose response was dropped. */
export function isNetworkFailure(error: unknown): boolean {
    return error instanceof TypeError;
}

/** A thrown request's message, with a network failure worded as `NETWORK_FAILURE_MESSAGE`. */
export function requestFailureMessage(error: any, fallback: string): string {
    if (isNetworkFailure(error)) return NETWORK_FAILURE_MESSAGE;
    return error?.message || fallback;
}
