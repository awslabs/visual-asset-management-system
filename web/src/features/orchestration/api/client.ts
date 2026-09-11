// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

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

/** A response's unwrapped payload plus the top-level `warnings` array that sits beside it. */
export interface ResultWithWarnings<T = any> {
    message: T;
    warnings: string[];
}

/**
 * Like `toTuple`, but keeps a response's top-level `warnings` array instead of discarding it.
 *
 * `unwrapMessage` returns `resp.message` whenever the response carries one, so a handler that puts
 * `warnings` BESIDE `message` rather than inside it loses the array before any component can read
 * it. Both callers are operations that succeed while leaving something the operator has to repair —
 * a deleted template still named by a trigger, an aborted execution whose sub-process could not be
 * stopped — so the array is the only signal that the plain success is not the whole answer.
 */
export async function toTupleWithWarnings<T = any>(
    fn: () => Promise<any>
): Promise<[boolean, ResultWithWarnings<T> | string]> {
    try {
        const resp = await fn();
        return [
            true,
            {
                message: unwrapMessage(resp) as T,
                warnings: Array.isArray(resp?.warnings) ? resp.warnings : [],
            },
        ];
    } catch (e: any) {
        console.log(e);
        return [false, e?.message || "Request failed"];
    }
}

export async function pageAll(
    fetchPage: (token?: string) => Promise<any>,
    itemsKey = "Items"
): Promise<any[]> {
    let out: any[] = [];
    let token: string | undefined = undefined;
    do {
        const resp = await fetchPage(token);
        const msg = unwrapMessage(resp);
        out = out.concat(msg?.[itemsKey] || []);
        token = msg?.NextToken || resp?.NextToken;
    } while (token);
    return out;
}
