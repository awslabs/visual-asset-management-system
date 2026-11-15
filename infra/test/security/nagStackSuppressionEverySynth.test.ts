/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The once-per-stack CDK Nag suppressions in `lib/helper/security.ts` are applied to EVERY synthesized
 * stack, including a second app's stacks in the same process.
 *
 * The guard sets were keyed by `node.addr`, which is derived from the construct path. A second app
 * with the same stack names has stacks at the same addresses, so the second synth in a process emitted
 * its nested templates without the audit log group and SSM parameter suppressions. A deployment runs
 * one synth per process, so only a test saw it, but it made a template depend on what the same process
 * had synthesized before — which the per-run synth cache (`test/support/synthCache.ts`) relies on not
 * happening.
 */

import { SynthResult, synthTemplate } from "../support/templateSynth";

const AUDIT_LOG_REASON = "within the nine deployment-specific VAMS audit CloudWatch log groups";
const SSM_REASON = "deployment-specific SSM resource-name parameter prefix";

/** Nested templates whose stack-level Nag metadata carries the given suppression reason. */
function stacksSuppressing(s: SynthResult, reason: string): string[] {
    return Object.entries(s.templates)
        .filter(([, tpl]) =>
            ((tpl.Metadata?.cdk_nag?.rules_to_suppress ?? []) as Array<{ reason?: string }>).some(
                (r) => String(r.reason ?? "").includes(reason)
            )
        )
        .map(([name]) => name)
        .sort();
}

describe("once-per-stack Nag suppressions survive a second synth in one process", () => {
    let first: SynthResult;
    let second: SynthResult;

    beforeAll(() => {
        // Two REAL synths of the same stack names in this process; the shared cache would answer the
        // second from the first.
        const same = () => {
            /* no change */
        };
        first = synthTemplate("commercial", {
            mutate: same,
            mutateKey: "nag-once-a",
            sharedCache: false,
        });
        second = synthTemplate("commercial", {
            mutate: same,
            mutateKey: "nag-once-b",
            sharedCache: false,
        });
    }, 600000);

    test.each([
        ["audit log group", AUDIT_LOG_REASON],
        ["SSM resource-name parameter", SSM_REASON],
    ])("the %s suppression is on the same stacks both times", (_label, reason) => {
        // Control: the first synth carries it at all, so equality below is not two empty lists.
        expect(stacksSuppressing(first, reason).length).toBeGreaterThan(0);
        expect(stacksSuppressing(second, reason)).toEqual(stacksSuppressing(first, reason));
    });
});
