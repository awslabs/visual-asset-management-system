// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { NotAbortableSubExecution } from "../types";

export function unwrapMessage(resp: any): any {
    return resp && typeof resp === "object" && "message" in resp ? resp.message : resp;
}

/**
 * The HTTP status a failed call answered with, carried on its `[false, message]` tuple so a query
 * can tell a final answer (a 403 or 404) from a transient failure without parsing the message.
 * Non-enumerable, so destructuring, equality checks and serialization see the plain tuple.
 */
const FAILURE_STATUS = Symbol("failureStatus");

/** The `[false, message]` tuple for a thrown call, tagged with its HTTP status when it has one. */
function failureTuple(e: any): [false, string] {
    const tuple: [false, string] = [false, e?.message || "Request failed"];
    if (typeof e?.status === "number") {
        Object.defineProperty(tuple, FAILURE_STATUS, { value: e.status, enumerable: false });
    }
    return tuple;
}

/**
 * The HTTP status of a failed `toTuple` / `toTupleWithWarnings` result, or undefined when the call
 * failed without a response (a network failure) or the tuple was rebuilt by its service.
 */
export function failureStatus(result: readonly unknown[]): number | undefined {
    const status = (result as any)?.[FAILURE_STATUS];
    return typeof status === "number" ? status : undefined;
}

export async function toTuple<T = any>(fn: () => Promise<any>): Promise<[boolean, T | string]> {
    try {
        return [true, unwrapMessage(await fn()) as T];
    } catch (e: any) {
        console.log(e);
        return failureTuple(e);
    }
}

/**
 * A response's unwrapped payload plus the top-level arrays that sit beside `message`: `warnings`
 * always (empty when the response has none), and each sibling the caller named when the response
 * carries it non-empty.
 */
export interface ResultWithWarnings<T = any> {
    message: T;
    warnings: string[];
    /** An abort's registered sub-processes of a type VAMS cannot stop; a group abort's entries
     *  carry the member `executionId`. */
    notAbortable?: NotAbortableSubExecution[];
}

/** The optional top-level arrays `toTupleWithWarnings` can carry beside `message` and `warnings`. */
export type MessageSibling = Exclude<keyof ResultWithWarnings, "message" | "warnings">;

/**
 * Like `toTuple`, but keeps a response's top-level `warnings` array — and each sibling named in
 * `siblings` — instead of discarding it.
 *
 * `unwrapMessage` returns `resp.message` whenever the response carries one, so a handler that puts
 * an array BESIDE `message` rather than inside it loses the array before any component can read
 * it. The callers are operations that succeed while leaving something the operator has to repair —
 * a deleted template still named by a trigger, an aborted execution whose sub-process could not be
 * stopped — so these arrays are the only signal that the plain success is not the whole answer.
 * A named sibling is present on the result only when the response carries it as a non-empty array.
 */
export async function toTupleWithWarnings<T = any>(
    fn: () => Promise<any>,
    siblings: readonly MessageSibling[] = []
): Promise<[boolean, ResultWithWarnings<T> | string]> {
    try {
        const resp = await fn();
        const result: ResultWithWarnings<T> = {
            message: unwrapMessage(resp) as T,
            warnings: Array.isArray(resp?.warnings) ? resp.warnings : [],
        };
        for (const key of siblings) {
            const value = resp?.[key];
            if (Array.isArray(value) && value.length > 0) {
                result[key] = value;
            }
        }
        return [true, result];
    } catch (e: any) {
        console.log(e);
        return failureTuple(e);
    }
}

/**
 * The error a query hook throws for a failed service tuple: the service's message plus the HTTP
 * status, when the call got one, so the query client's retry rule can see a final answer. Keeps
 * the plain `Error` name, so pages that print the error show the same text as before.
 */
export class ServiceCallError extends Error {
    status?: number;
    constructor(message: string, status?: number) {
        super(message);
        this.status = status;
    }
}

/** Upper bound on the pages pageAll requests, far above any real listing. */
export const PAGE_ALL_MAX_PAGES = 1000;

/**
 * Requests every page of a NextToken listing. Rejects when a response repeats a token already
 * requested or the page bound is reached, since callers need the complete set.
 */
export async function pageAll(
    fetchPage: (token?: string) => Promise<any>,
    itemsKey = "Items"
): Promise<any[]> {
    let out: any[] = [];
    let token: string | undefined = undefined;
    const requested = new Set<string>();
    let pages = 0;
    do {
        if (token !== undefined) {
            if (requested.has(token)) {
                throw new Error("Listing returned a page token it had already returned");
            }
            requested.add(token);
        }
        if (pages >= PAGE_ALL_MAX_PAGES) {
            throw new Error(`Listing exceeded ${PAGE_ALL_MAX_PAGES} pages`);
        }
        const resp = await fetchPage(token);
        pages += 1;
        const msg = unwrapMessage(resp);
        out = out.concat(msg?.[itemsKey] || []);
        token = msg?.NextToken || resp?.NextToken;
    } while (token);
    return out;
}
