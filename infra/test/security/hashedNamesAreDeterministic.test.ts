/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A name or logical id derived from `generateUniqueNameHash` must be the same in every synth of the same
 * configuration.
 *
 * Three call sites hashed an unresolved CDK Token -- a Lambda `functionArn` for the API Gateway invoke
 * permissions, and role ARNs for the two OpenSearch Serverless access policies. A Token stringifies to
 * `${Token[TOKEN.n]}`, where `n` is a process-wide allocation counter, so the hash encoded construct
 * creation ORDER rather than the resource, and moved whenever anything earlier in the tree allocated a
 * different number of tokens. Measured on two real synths of one unchanged configuration: 7 of 47
 * permission logical ids differed (CloudFormation replaces each on every deploy), and both access-policy
 * names differed (a renamed `CfnAccessPolicy` is a REPLACEMENT of a live data-access policy).
 *
 * Two layers, because either alone can be satisfied vacuously:
 *
 *   1. The helper REFUSES a Token. A synth-time error names the call site; a deploy-time replacement
 *      names nothing. This is what stops a fourth site being written.
 *   2. Two full synths of the same template emit the same permission ids and the same policy names. This
 *      is the property itself, asserted on the emitted templates. Two synths in one process are the
 *      discriminating case -- the token counter keeps running between them, so the old code produced
 *      different ids here and the fixed code cannot.
 *
 * Durable guards, not temporary ones (root CLAUDE.md Rule 13): hashing an ARN is a one-token edit that
 * a copy-paste from any `Fn`-bearing construct would reintroduce.
 */

import * as cdk from "aws-cdk-lib";
import { generateUniqueNameHash } from "../../lib/helper/security";
import { SynthResult, synthTemplate } from "../support/templateSynth";

describe("generateUniqueNameHash refuses an unresolved Token", () => {
    const token = cdk.Lazy.string({ produce: () => "resolved-later" });

    test("as the resource identifier", () => {
        expect(() => generateUniqueNameHash("stack", "123456789012", token)).toThrow(
            /resourceIdentifier is an unresolved CDK Token/
        );
    });

    test("as the account (the pseudo-parameter is a Token too)", () => {
        expect(() => generateUniqueNameHash("stack", cdk.Aws.ACCOUNT_ID, "x")).toThrow(
            /accountId is an unresolved CDK Token/
        );
    });

    test("as the stack name", () => {
        expect(() => generateUniqueNameHash(token, "123456789012", "x")).toThrow(
            /stackName is an unresolved CDK Token/
        );
    });

    test("and is deterministic, truncated and identifier-sensitive on resolved strings", () => {
        // POSITIVE CONTROLS for the throws above: the guard must not have broken the ordinary path.
        const a = generateUniqueNameHash("stack", "123456789012", "Invoke/Fn", 10);
        const b = generateUniqueNameHash("stack", "123456789012", "Invoke/Fn", 10);
        const c = generateUniqueNameHash("stack", "123456789012", "Invoke/OtherFn", 10);
        expect(a).toBe(b);
        expect(a).toHaveLength(10);
        expect(a).toMatch(/^[0-9a-f]{10}$/);
        expect(c).not.toBe(a);
    });
});

describe("hashed names are identical across two synths of one configuration", () => {
    let first: SynthResult;
    let second: SynthResult;

    beforeAll(() => {
        // Distinct cache keys with an identity mutation force two REAL synths in this one process, which
        // is the case that discriminates: the CDK token counter does not reset between them.
        const same = () => {
            /* no change */
        };
        first = synthTemplate("commercial", { mutate: same, mutateKey: "hash-stability-a" });
        second = synthTemplate("commercial", { mutate: same, mutateKey: "hash-stability-b" });
    });

    const invokeIds = (s: SynthResult) =>
        s
            .ofType("AWS::Lambda::Permission")
            .map((r) => r.logicalId)
            .filter((id) => /Invoke[0-9a-f]{10}[0-9A-F]{8}$/.test(id))
            .sort();

    const policyNames = (s: SynthResult) =>
        s
            .ofType("AWS::OpenSearchServerless::AccessPolicy")
            .map((r) => String(r.properties.Name))
            .sort();

    test("the two synths are really two synths (count control)", () => {
        // The assertions below compare sets; two empty sets are equal. The commercial template registers
        // dozens of routes and uses OpenSearch Serverless, so both populations must be non-empty here.
        expect(invokeIds(first).length).toBeGreaterThan(20);
        expect(policyNames(first).length).toBeGreaterThan(0);
        expect(first).not.toBe(second);
    });

    test("every API Gateway invoke-permission logical id is the same in both synths", () => {
        expect(invokeIds(second)).toEqual(invokeIds(first));
    });

    test("every OpenSearch Serverless access-policy name is the same in both synths", () => {
        expect(policyNames(second)).toEqual(policyNames(first));
    });
});
