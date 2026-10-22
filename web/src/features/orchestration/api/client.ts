// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import type { NotAbortableSubExecution } from "../types";

export function unwrapMessage(resp: any): any {
    return resp && typeof resp === "object" && "message" in resp ? resp.message : resp;
}

export async function toTuple<T = any>(fn: () => Promise<any>): Promise<[boolean, T | string]> {
    try {
        return [true, unwrapMessage(await fn()) as T];
    } catch (e: any) {
        console.log(e);
        return [false, e?.message || "Request failed"];
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
        return [false, e?.message || "Request failed"];
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
