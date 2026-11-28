/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { act, renderHook } from "@testing-library/react";
import { useInFlight } from "./useInFlight";

describe("useInFlight", () => {
    it("refuses a second claim of a key in the same tick, before any re-render", () => {
        const { result } = renderHook(() => useInFlight<string>());
        const { claim } = result.current;
        let first = false;
        let second = true;
        act(() => {
            first = claim("s-1", "start");
            second = claim("s-1", "stop");
        });
        expect(first).toBe(true);
        expect(second).toBe(false);
        expect(result.current.inFlight.get("s-1")).toBe("start");
    });

    it("claims each key on its own and frees a key once released", () => {
        const { result } = renderHook(() => useInFlight());
        act(() => {
            expect(result.current.claim("a")).toBe(true);
            expect(result.current.claim("b")).toBe(true);
        });
        act(() => result.current.release("a"));
        expect(result.current.inFlight.has("a")).toBe(false);
        expect(result.current.inFlight.has("b")).toBe(true);
        act(() => {
            expect(result.current.claim("a")).toBe(true);
        });
    });
});
