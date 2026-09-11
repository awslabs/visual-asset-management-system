// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { unwrapMessage, toTuple } from "./client";

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
});
