/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { createEvalBlockTracker, EVAL_BLOCKED_MESSAGE, isEvalViolation } from "./evalBlocked";

const EVAL = { blockedURI: "eval", effectiveDirective: "script-src" };

describe("isEvalViolation", () => {
    it("matches a refused eval under script-src", () => {
        expect(isEvalViolation(EVAL)).toBe(true);
        // Older browsers fill only violatedDirective, with the directive's source list.
        expect(
            isEvalViolation({ blockedURI: "eval", violatedDirective: "script-src 'self'" })
        ).toBe(true);
    });

    it("does not match a refused WebAssembly compile, file or other directive", () => {
        expect(isEvalViolation({ blockedURI: "wasm-eval", effectiveDirective: "script-src" })).toBe(
            false
        );
        expect(
            isEvalViolation({
                blockedURI: "https://cdn.example.com/lib.js",
                effectiveDirective: "script-src-elem",
            })
        ).toBe(false);
        expect(isEvalViolation({ blockedURI: "inline", effectiveDirective: "style-src" })).toBe(
            false
        );
        expect(isEvalViolation({})).toBe(false);
    });
});

describe("createEvalBlockTracker", () => {
    it("reports once when the violation arrives before the load failure", () => {
        const onBlocked = jest.fn();
        const tracker = createEvalBlockTracker(onBlocked);
        tracker.violation(EVAL);
        expect(onBlocked).not.toHaveBeenCalled();
        tracker.loadFailed();
        tracker.violation(EVAL);
        expect(onBlocked).toHaveBeenCalledTimes(1);
    });

    it("reports when the violation arrives after the load failure", () => {
        const onBlocked = jest.fn();
        const tracker = createEvalBlockTracker(onBlocked);
        tracker.loadFailed();
        tracker.violation(EVAL);
        expect(onBlocked).toHaveBeenCalledTimes(1);
    });

    it("does not report a load failure without a refused eval", () => {
        const onBlocked = jest.fn();
        const tracker = createEvalBlockTracker(onBlocked);
        tracker.violation({ blockedURI: "wasm-eval", effectiveDirective: "script-src" });
        tracker.loadFailed();
        expect(onBlocked).not.toHaveBeenCalled();
    });

    it("forgets the previous load on reset", () => {
        const onBlocked = jest.fn();
        const tracker = createEvalBlockTracker(onBlocked);
        tracker.violation(EVAL);
        tracker.reset();
        tracker.loadFailed();
        expect(onBlocked).not.toHaveBeenCalled();
        tracker.violation(EVAL);
        expect(onBlocked).toHaveBeenCalledTimes(1);
    });

    it("names the deployment setting", () => {
        expect(EVAL_BLOCKED_MESSAGE).toContain("app.webUi.allowUnsafeEvalFeatures");
        expect(EVAL_BLOCKED_MESSAGE).toContain("Content Security Policy");
    });
});
