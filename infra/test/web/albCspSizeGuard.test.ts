/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An over-long Content-Security-Policy must fail at synthesis, naming itself.
 *
 * The ALB delivers the whole policy as one listener attribute, and Elastic Load Balancing caps an
 * attribute value at 1 KB. The shipped policy measures 858 bytes on both restricted templates, so there
 * is roughly 166 bytes of margin — about three more inline-script hashes, or two or three more host
 * sources added through `config/csp/cspAdditionalConfig.json`. Past the cap the deployment fails, and
 * the failure arrives from CloudFormation naming a listener attribute rather than the policy.
 *
 * This is the test that makes the guard load-bearing. `albWebSecurityHeaders.test.ts` measures the
 * SHIPPED policy against the cap, which catches growth — but it would pass just as well against a guard
 * whose comparison was inverted or whose threshold was wrong, because the shipped policy fits either
 * way. Here the policy is deliberately pushed over the cap through the operator-facing file that would
 * do it in practice.
 *
 * Kept in its own file because it mocks `fs.readFileSync`, which the synth harness also uses.
 */

import * as fs from "fs";
import { synthTemplate } from "../support/templateSynth";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** Serves a substitute cspAdditionalConfig.json and delegates every other read. */
function serveCspAdditions(additions: Record<string, string[]>) {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("cspAdditionalConfig.json")) {
                return JSON.stringify(additions);
            }
            return realReadFileSync(p, ...rest);
        }
    );
}

/** One host source of a given length, in the shape the loader accepts. */
const hostSource = (bytes: number) => `https://${"a".repeat(Math.max(1, bytes - 9))}.example.com`;

describe("ALB CSP listener-attribute size guard", () => {
    afterEach(() => {
        // Restored to delegating rather than cleared, or the synth harness in any later test in this
        // process reads nothing instead of failing outright.
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    test("the shipped additions synthesize, which is the control", () => {
        // Without this the throw below could be caused by the mock itself rather than by the size.
        serveCspAdditions({ connectSrc: [], scriptSrc: [] });
        expect(() =>
            synthTemplate("govcloud", {
                mutate: () => undefined,
                mutateKey: "csp-additions-empty",
            })
        ).not.toThrow();
    });

    test("additions that push the policy past 1 KB throw, and the message names the size", () => {
        // ~400 bytes of extra sources against 166 bytes of margin.
        serveCspAdditions({ connectSrc: [hostSource(200)], imgSrc: [hostSource(200)] });
        let thrown: Error | undefined;
        try {
            synthTemplate("govcloud", {
                mutate: () => undefined,
                mutateKey: "csp-additions-oversized",
            });
        } catch (e) {
            thrown = e as Error;
        }
        expect(thrown).toBeDefined();
        // The measured size and the cap both appear, so the operator can see how far over it is.
        expect(thrown!.message).toMatch(/Content-Security-Policy is \d+ bytes/);
        expect(thrown!.message).toContain("1024");
        // And it points at the file that caused it rather than at a listener attribute.
        expect(thrown!.message).toContain("cspAdditionalConfig.json");
    });

    test("the same additions do NOT throw on the CloudFront path", () => {
        // The cap belongs to the ALB listener attribute, not to the policy. CloudFront's
        // response-headers policy allows 1783 bytes, so a fix that rejected long policies everywhere
        // would break commercial deployments to satisfy a limit they do not have.
        serveCspAdditions({ connectSrc: [hostSource(200)], imgSrc: [hostSource(200)] });
        expect(() =>
            synthTemplate("commercial", {
                mutate: () => undefined,
                mutateKey: "csp-additions-oversized-cf",
            })
        ).not.toThrow();
    });
});
