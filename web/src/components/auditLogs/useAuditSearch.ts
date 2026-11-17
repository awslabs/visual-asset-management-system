/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getAuditQuery, startAuditQuery, stopAuditQuery } from "../../services/APIService";
import { normalizeQueryPage, normalizeStart } from "./auditSearchApiModel";
import { runPollLoop, PollResult } from "./auditPollLoop";
import { AuditQueryPage, AuditQueryStatistics, AuditRow } from "./auditSearchTypes";

export const AUDIT_PAGE_SIZE = 100;

export type AuditErrorKind = "denied" | "throttled" | "expired" | "invalid" | "failed" | "timeout";

export interface AuditSearchError {
    kind: AuditErrorKind;
    message: string;
}

export type AuditPhase = "idle" | "starting" | "running" | "done" | "stopped" | "error";

export interface AuditSearchState {
    phase: AuditPhase;
    queryId: string | null;
    status: string;
    statistics: AuditQueryStatistics;
    rows: AuditRow[];
    warnings: string[];
    nextToken: string | undefined;
    pageIndex: number;
    /** The pages whose token is known: the last one is open-ended while the query has more. */
    knownPages: number;
    /** Whether a page other than the first is being read. */
    loadingPage: boolean;
    error: AuditSearchError | null;
}

const IDLE: AuditSearchState = {
    phase: "idle",
    queryId: null,
    status: "",
    statistics: {},
    rows: [],
    warnings: [],
    nextToken: undefined,
    pageIndex: 1,
    knownPages: 1,
    loadingPage: false,
    error: null,
};

export const DENIED_MESSAGE = "You do not have access to audit logs.";
export const EXPIRED_MESSAGE = "These results expired. Run the search again.";
export const THROTTLED_MESSAGE =
    "The service is busy with other searches. Wait a moment and search again.";

/** The error the page shows for a failed call, from the API's status and (generic) message. */
export const classifyAuditError = (
    status: number | undefined,
    message: string | undefined
): AuditSearchError => {
    if (status === 403) return { kind: "denied", message: DENIED_MESSAGE };
    if (status === 404) return { kind: "expired", message: EXPIRED_MESSAGE };
    if (status === 429) return { kind: "throttled", message: THROTTLED_MESSAGE };
    if (status === 400) {
        return { kind: "invalid", message: message || "The search was not accepted." };
    }
    return { kind: "failed", message: message || "The audit search could not be completed." };
};

const asPollResult = (answer: any[]): PollResult =>
    answer[0] === true
        ? { ok: true, page: normalizeQueryPage(answer[1]) }
        : { ok: false, message: answer[1], status: answer[2] };

const sleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

/**
 * Runs one search at a time: start, long-poll until a terminal state, then page by token. A new run,
 * a stop and an unmount each end the loop of the run before them; an unmount also stops a running
 * query so it does not hold one of the server's query slots.
 */
export const useAuditSearch = (waitSeconds: number, sort: "asc" | "desc") => {
    const [state, setState] = useState<AuditSearchState>(IDLE);
    const runId = useRef(0);
    const live = useRef<{ queryId: string | null; running: boolean }>({
        queryId: null,
        running: false,
    });
    const tokens = useRef<Record<number, string | undefined>>({});

    const patch = useCallback((update: Partial<AuditSearchState>) => {
        setState((previous) => ({ ...previous, ...update }));
    }, []);

    const applyPage = useCallback(
        (page: AuditQueryPage) => {
            patch({
                status: page.status,
                statistics: page.statistics,
                rows: page.rows,
                warnings: page.warnings,
                nextToken: page.nextToken,
            });
        },
        [patch]
    );

    const run = useCallback(
        async (body: Record<string, unknown>) => {
            runId.current += 1;
            const mine = runId.current;
            const isMine = () => runId.current === mine;
            tokens.current = {};
            setState({ ...IDLE, phase: "starting" });

            const started: any[] = await startAuditQuery(body);
            if (!isMine()) return;
            const handle = started[0] === true ? normalizeStart(started[1]) : null;
            if (!handle) {
                patch({
                    phase: "error",
                    error:
                        started[0] === true
                            ? classifyAuditError(undefined, "The search did not start.")
                            : classifyAuditError(started[2], started[1]),
                });
                return;
            }
            live.current = { queryId: handle.queryId, running: true };
            patch({ phase: "running", queryId: handle.queryId, status: handle.status });

            const outcome = await runPollLoop(
                {
                    fetchPage: async (wait) =>
                        asPollResult(
                            await getAuditQuery({
                                queryId: handle.queryId,
                                wait,
                                pageSize: AUDIT_PAGE_SIZE,
                                sort,
                            })
                        ),
                    sleep,
                    now: () => Date.now(),
                    isCancelled: () => !isMine(),
                    onUpdate: (page) => {
                        if (isMine()) applyPage(page);
                    },
                },
                { waitSeconds }
            );
            if (!isMine()) return;
            live.current.running = false;
            if (outcome.kind === "terminal") {
                tokens.current[2] = outcome.page.nextToken;
                patch({ knownPages: outcome.page.nextToken ? 2 : 1 });
                const status = outcome.page.status.toLowerCase();
                if (status === "failed" || status === "timeout") {
                    patch({
                        phase: "error",
                        error: {
                            kind: status === "timeout" ? "timeout" : "failed",
                            message:
                                status === "timeout"
                                    ? "The search ran past its time limit and was stopped. Narrow the time range or the filters."
                                    : "The search failed. Try again, or narrow the time range.",
                        },
                    });
                } else {
                    patch({ phase: status === "cancelled" ? "stopped" : "done" });
                }
            } else if (outcome.kind === "error") {
                patch({
                    phase: "error",
                    error: classifyAuditError(outcome.status, outcome.message),
                });
            } else if (outcome.kind === "clientTimeout") {
                patch({
                    phase: "error",
                    error: {
                        kind: "timeout",
                        message: "The search took too long. Narrow the time range or the filters.",
                    },
                });
            }
        },
        [applyPage, patch, sort, waitSeconds]
    );

    const stop = useCallback(async () => {
        const queryId = live.current.queryId;
        if (!queryId || !live.current.running) return;
        // The loop ends at its next check; the handle is stopped on the server, and one read
        // shows the rows found so far.
        runId.current += 1;
        live.current.running = false;
        patch({ phase: "stopped", status: "Cancelled" });
        await stopAuditQuery(queryId);
        const answer: any[] = await getAuditQuery({
            queryId,
            wait: 0,
            pageSize: AUDIT_PAGE_SIZE,
            sort,
        });
        if (answer[0] === true) {
            const page = normalizeQueryPage(answer[1]);
            tokens.current[2] = page.nextToken;
            applyPage({ ...page, status: "Cancelled" });
            patch({ knownPages: page.nextToken ? 2 : 1 });
        }
    }, [applyPage, patch, sort]);

    const goToPage = useCallback(
        async (pageIndex: number, sortOverride?: "asc" | "desc") => {
            const queryId = live.current.queryId;
            if (!queryId || pageIndex < 1) return;
            // A new order starts the walk again: a token belongs to the order that produced it.
            if (sortOverride) tokens.current = {};
            const startingToken = pageIndex === 1 ? undefined : tokens.current[pageIndex];
            if (pageIndex > 1 && !startingToken) return;
            patch({ loadingPage: true });
            const answer: any[] = await getAuditQuery({
                queryId,
                wait: 0,
                pageSize: AUDIT_PAGE_SIZE,
                startingToken,
                sort: sortOverride ?? sort,
            });
            if (answer[0] === true) {
                const page = normalizeQueryPage(answer[1]);
                tokens.current[pageIndex + 1] = page.nextToken;
                setState((previous) => ({
                    ...previous,
                    rows: page.rows,
                    nextToken: page.nextToken,
                    warnings: page.warnings.length ? page.warnings : previous.warnings,
                    pageIndex,
                    knownPages:
                        pageIndex === 1
                            ? page.nextToken
                                ? 2
                                : 1
                            : Math.max(
                                  previous.knownPages,
                                  page.nextToken ? pageIndex + 1 : pageIndex
                              ),
                    loadingPage: false,
                }));
            } else {
                patch({ loadingPage: false, error: classifyAuditError(answer[2], answer[1]) });
            }
        },
        [patch, sort]
    );

    const clearError = useCallback(() => patch({ error: null }), [patch]);

    useEffect(
        () => () => {
            runId.current += 1;
            const { queryId, running } = live.current;
            if (queryId && running) void stopAuditQuery(queryId);
        },
        []
    );

    return {
        state,
        run,
        stop,
        goToPage,
        clearError,
        isActive: state.phase === "starting" || state.phase === "running",
    };
};
