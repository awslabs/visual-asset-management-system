/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { QueryClient } from "@tanstack/react-query";

/** Times a failed query is re-sent before its error is shown. */
export const MAX_QUERY_RETRIES = 1;

/**
 * Whether an HTTP status is a final answer that a re-send cannot change: any 4xx except 408
 * (request timeout) and 429 (rate limited). A 403 is the API's authorization decision, a 404 says
 * the object is not there, and a 401 has already been through the API client's one refresh and
 * re-send. 5xx and 429 stay retryable.
 */
export function isFinalStatus(status: number | undefined): boolean {
    return (
        typeof status === "number" &&
        status >= 400 &&
        status < 500 &&
        status !== 408 &&
        status !== 429
    );
}

/** The HTTP status an error carries (`ApiError`, `ServiceCallError`), or undefined. */
export function errorStatus(error: unknown): number | undefined {
    const status = (error as { status?: unknown } | null)?.status;
    return typeof status === "number" ? status : undefined;
}

/**
 * TanStack Query `retry` rule for the app: a final answer fails the query at once, so a page shows
 * "Not Authorized" or "not found" as soon as the API answers. A failure with no status (network
 * error, a service that rebuilt its tuple) or a transient status is re-sent up to
 * MAX_QUERY_RETRIES times, as before.
 */
export function shouldRetryQuery(failureCount: number, error: unknown): boolean {
    if (isFinalStatus(errorStatus(error))) return false;
    return failureCount < MAX_QUERY_RETRIES;
}

/** The app's query client: 30 s stale time and the retry rule above. */
export function createAppQueryClient(): QueryClient {
    return new QueryClient({
        defaultOptions: {
            queries: {
                staleTime: 30000,
                retry: shouldRetryQuery,
            },
        },
    });
}
