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
