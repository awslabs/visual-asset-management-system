// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import {
    unwrapMessage,
    toTuple,
    toTupleWithWarnings,
    pageAll,
    PAGE_ALL_MAX_PAGES,
    failureStatus,
    ServiceCallError,
} from "./client";

describe("orchestration api client helpers", () => {
    it("unwrapMessage returns .message when present", () => {
        expect(unwrapMessage({ message: { a: 1 } })).toEqual({ a: 1 });
        expect(unwrapMessage({ a: 1 })).toEqual({ a: 1 });
    });
    it("toTuple returns [true, data] on success", async () => {
        const r = await toTuple(async () => ({ message: "ok" }));
        expect(r).toEqual([true, "ok"]);
    });
    it("toTuple returns [false, message] on throw", async () => {
        const r = await toTuple(async () => {
            const e: any = new Error("boom");
            throw e;
        });
        expect(r[0]).toBe(false);
        expect(r[1]).toBe("boom");
    });
    it("a failed tuple carries the call's HTTP status without changing its shape", async () => {
        const denied = await toTuple(async () => {
            throw Object.assign(new Error("Not Authorized"), { status: 403 });
        });
        expect(denied).toEqual([false, "Not Authorized"]);
        expect(Object.keys(denied)).toEqual(["0", "1"]);
        expect(failureStatus(denied)).toBe(403);

        const warned = await toTupleWithWarnings(async () => {
            throw Object.assign(new Error("Not Found"), { status: 404 });
        });
        expect(failureStatus(warned)).toBe(404);
    });
    it("a failure with no response has no status", async () => {
        const offline = await toTuple(async () => {
            throw new TypeError("Failed to fetch");
        });
        expect(failureStatus(offline)).toBeUndefined();
        expect(failureStatus([false, "rebuilt by a service"])).toBeUndefined();
    });
    it("ServiceCallError keeps the plain Error text", () => {
        const e = new ServiceCallError("Not Authorized", 403);
        expect(String(e)).toBe("Error: Not Authorized");
        expect(e.status).toBe(403);
    });
    it("toTupleWithWarnings keeps warnings and only the named siblings of message", async () => {
        const resp = {
            message: { groupId: "g1" },
            warnings: ["e1: w"],
            notAbortable: [{ resourceType: "slurmJob", executionId: "e2" }],
        };
        expect(await toTupleWithWarnings(async () => resp)).toEqual([
            true,
            { message: { groupId: "g1" }, warnings: ["e1: w"] },
        ]);
        expect(await toTupleWithWarnings(async () => resp, ["notAbortable"])).toEqual([
            true,
            {
                message: { groupId: "g1" },
                warnings: ["e1: w"],
                notAbortable: [{ resourceType: "slurmJob", executionId: "e2" }],
            },
        ]);
    });
    it("toTupleWithWarnings omits a named sibling that is absent, empty or not an array", async () => {
        for (const notAbortable of [undefined, [], "x"]) {
            const [, result] = await toTupleWithWarnings(
                async () => ({ message: "ok", notAbortable }),
                ["notAbortable"]
            );
            expect(result).toEqual({ message: "ok", warnings: [] });
        }
    });
});

describe("pageAll", () => {
    it("collects every page until no token is returned", async () => {
        const fetchPage = jest
            .fn()
            .mockResolvedValueOnce({ Items: [1, 2], NextToken: "t1" })
            .mockResolvedValueOnce({ message: { Items: [3], NextToken: "t2" } })
            .mockResolvedValueOnce({ Items: [4] });
        await expect(pageAll(fetchPage)).resolves.toEqual([1, 2, 3, 4]);
        expect(fetchPage.mock.calls.map(([token]) => token)).toEqual([undefined, "t1", "t2"]);
    });

    it("rejects when a response repeats a token instead of looping forever", async () => {
        const fetchPage = jest.fn().mockResolvedValue({ Items: [1], NextToken: "same" });
        await expect(pageAll(fetchPage)).rejects.toThrow(/already returned/);
        expect(fetchPage).toHaveBeenCalledTimes(2);
    });

    it("rejects once the page bound is reached", async () => {
        let n = 0;
        const fetchPage = jest.fn(async () => ({ Items: [n], NextToken: `t${++n}` }));
        await expect(pageAll(fetchPage)).rejects.toThrow(`${PAGE_ALL_MAX_PAGES} pages`);
        expect(fetchPage).toHaveBeenCalledTimes(PAGE_ALL_MAX_PAGES);
    });
});
