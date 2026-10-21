/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { unwrapMessage } from "../../../features/orchestration/api/client";

/** The parsed body of a failed EDH request. */
export interface EdhErrorBody {
    message?: string;
    /** Set on a 502 for a mutation EDH may have carried out without confirming it. */
    outcomeUnknown?: boolean;
    /** Set on the 409 a start answers while an earlier start of the workstation may be under way. */
    startInProgress?: boolean;
}

/**
 * A failed EDH request with its HTTP status and parsed body beside the message, so a caller can tell
 * a mutation EDH did not confirm from one it refused.
 */
export class EdhRequestError extends Error {
    status?: number;
    body?: EdhErrorBody;

    constructor(message: string, status?: number, body?: EdhErrorBody) {
        super(message);
        this.name = "EdhRequestError";
        this.status = status;
        this.body = body;
    }
}

/** An `apiClient` rejection as an `EdhRequestError`, keeping whatever status and body it carried. */
export function edhRequestError(e: any): EdhRequestError {
    return new EdhRequestError(e?.message || "Request failed", e?.status, e?.body ?? undefined);
}

/**
 * Like `toTuple`, but a failure is an `EdhRequestError` rather than the bare message. The EDH
 * mutations use it, so a page can tell an outcome EDH did not confirm from a refusal.
 */
export async function toOutcomeTuple<T = any>(
    fn: () => Promise<any>
): Promise<[boolean, T | EdhRequestError]> {
    try {
        return [true, unwrapMessage(await fn()) as T];
    } catch (e: any) {
        console.log(e);
        return [false, edhRequestError(e)];
    }
}

/**
 * The statuses a lost reply arrives as. A launch reads either as an unknown outcome; the other
 * mutations only a 504 or a 502 the handler flagged (`isOutcomeUnknown`).
 */
export function isUnknownOutcomeStatus(status: unknown): boolean {
    return status === 502 || status === 504;
}

/**
 * Shown for a gateway 504, whose own body names only the gateway's timeout: the words the add-on's
 * handlers answer a flagged 502 with.
 */
export const OUTCOME_UNKNOWN_MESSAGE =
    "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.";

/** A mutation EDH may have carried out without confirming it: a 502 flagged `outcomeUnknown`, or a 504. */
export function isOutcomeUnknown(err: unknown): boolean {
    const e = err as Partial<EdhRequestError> | null | undefined;
    if (!isUnknownOutcomeStatus(e?.status)) return false;
    return e?.status === 504 || e?.body?.outcomeUnknown === true;
}

/** The 409 a start answers, flagged `startInProgress`, while an earlier start may be under way. */
export function isAlreadyInProgress(err: unknown): boolean {
    const e = err as Partial<EdhRequestError> | null | undefined;
    return e?.status === 409 && e?.body?.startInProgress === true;
}

/** Whether a settled mutation may have changed EDH's state: it succeeded, or EDH did not confirm it. */
export function mayHaveTakenEffect(err: unknown): boolean {
    return !err || isOutcomeUnknown(err);
}

export interface UnconfirmedNotice {
    title: string;
    message: string;
}

/**
 * The warning a mutation raises in place of "<action> failed" when it did not fail: EDH did not
 * confirm it ("<action> not confirmed"), or a start of it is already under way ("<action> already in
 * progress"). Null for every other error.
 */
export function unconfirmedNotice(action: string, err: unknown): UnconfirmedNotice | null {
    if (isOutcomeUnknown(err)) {
        const e = err as Partial<EdhRequestError>;
        return {
            title: `${action} not confirmed`,
            message: e.status === 504 || !e.message ? OUTCOME_UNKNOWN_MESSAGE : e.message,
        };
    }
    if (isAlreadyInProgress(err)) {
        return { title: `${action} already in progress`, message: (err as Error).message };
    }
    return null;
}
