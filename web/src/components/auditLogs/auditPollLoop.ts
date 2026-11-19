/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The long-poll loop of a running search. Each call to the API waits on the server for up to the
 * long-poll wait, so the loop sends about one request per wait instead of one per second. It honours
 * `pollAfterMs` when the server sends one, keeps a floor between requests, stops at once on a
 * refusal (403, 404, 400), retries a dropped connection a few times, and gives up after a client
 * deadline so a handle the server lost cannot keep a tab polling.
 */

import { AuditQueryPage } from "./auditSearchTypes";
import { isTerminal } from "./auditSearchApiModel";

export type PollResult =
    | { ok: true; page: AuditQueryPage }
    | { ok: false; message: string; status?: number };

export interface PollDeps {
    /** One call of `GET /audit/queries/{queryId}`, asking the server to wait `waitSeconds`. */
    fetchPage: (waitSeconds: number) => Promise<PollResult>;
    sleep: (ms: number) => Promise<void>;
    now: () => number;
    isCancelled: () => boolean;
    /** Called with every page received, terminal or not. */
    onUpdate: (page: AuditQueryPage) => void;
}

export type PollOutcome =
    | { kind: "terminal"; page: AuditQueryPage }
    | { kind: "cancelled" }
    | { kind: "error"; message: string; status?: number }
    | { kind: "clientTimeout" };

/** The least wait between two requests, whatever the server says. */
export const MIN_POLL_GAP_MS = 250;
/** The longest wait the loop takes from `pollAfterMs`. */
export const MAX_POLL_GAP_MS = 30_000;
/** Dropped connections (no HTTP status) retried in a row before the loop reports the error. */
export const MAX_CONSECUTIVE_NETWORK_RETRIES = 3;
/** The loop's own deadline: the server's longest query deadline (15 minutes) plus a margin. */
export const CLIENT_DEADLINE_MS = 16 * 60_000;

export const runPollLoop = async (
    deps: PollDeps,
    options: { waitSeconds: number; deadlineMs?: number }
): Promise<PollOutcome> => {
    const deadline = deps.now() + (options.deadlineMs ?? CLIENT_DEADLINE_MS);
    let networkRetries = 0;
    for (;;) {
        if (deps.isCancelled()) return { kind: "cancelled" };
        if (deps.now() > deadline) return { kind: "clientTimeout" };
        const result = await deps.fetchPage(options.waitSeconds);
        if (deps.isCancelled()) return { kind: "cancelled" };
        if (!result.ok) {
            const retryable = result.status === undefined || result.status >= 500;
            if (retryable && networkRetries < MAX_CONSECUTIVE_NETWORK_RETRIES) {
                networkRetries += 1;
                await deps.sleep(Math.min(MIN_POLL_GAP_MS * 2 ** networkRetries, 4000));
                continue;
            }
            return { kind: "error", message: result.message, status: result.status };
        }
        networkRetries = 0;
        deps.onUpdate(result.page);
        if (isTerminal(result.page.status)) return { kind: "terminal", page: result.page };
        const gap = Math.min(
            Math.max(result.page.pollAfterMs ?? 0, MIN_POLL_GAP_MS),
            MAX_POLL_GAP_MS
        );
        await deps.sleep(gap);
    }
};
