/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The rhino3dm library Online3DViewer loads for Rhinoceros 3D (.3dm) files compiles code with
 * `eval`/`new Function`, which a script-src without 'unsafe-eval' refuses. The viewer itself then
 * shows only "Failed to import model. (Failed to load rhino3dm.)", which does not name the cause.
 * The .3dm entry is offered only with ALLOWUNSAFEEVAL (#921); when eval is refused anyway, the
 * viewer shows this message instead, naming the deployment setting as the CAD path does.
 */
export const EVAL_BLOCKED_MESSAGE =
    "Rhinoceros 3D (.3dm) viewing is blocked by the Content Security Policy. The rhino3dm " +
    "library this viewer uses for .3dm files needs app.webUi.allowUnsafeEvalFeatures set to true " +
    "in the deployment configuration. Please contact your administrator to enable it.";

/** The fields of a SecurityPolicyViolationEvent this check reads. */
export interface PolicyViolation {
    blockedURI?: string;
    effectiveDirective?: string;
    violatedDirective?: string;
}

/**
 * True for the browser refusing `eval`/`new Function` under script-src. Browsers report that
 * violation with the blocked URI "eval"; a refused WebAssembly compile reports "wasm-eval" and a
 * refused file reports its URL, so neither matches.
 */
export function isEvalViolation(violation: PolicyViolation): boolean {
    const directive = violation.effectiveDirective || violation.violatedDirective || "";
    return violation.blockedURI === "eval" && directive.startsWith("script-src");
}

/**
 * Tracks one model load. The browser queues the violation event as a task, so it can arrive before
 * or after the viewer's load-failed callback; `onBlocked` runs once, when both have been seen.
 */
export function createEvalBlockTracker(onBlocked: () => void) {
    let evalBlocked = false;
    let loadFailed = false;
    let reported = false;
    const report = () => {
        if (evalBlocked && loadFailed && !reported) {
            reported = true;
            onBlocked();
        }
    };
    return {
        /** Starts a new load: forgets what the previous one saw. */
        reset() {
            evalBlocked = false;
            loadFailed = false;
            reported = false;
        },
        violation(violation: PolicyViolation) {
            if (isEvalViolation(violation)) {
                evalBlocked = true;
                report();
            }
        },
        loadFailed() {
            loadFailed = true;
            report();
        },
    };
}
