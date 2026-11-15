/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The SNS message-body filter evaluator the platform tests run the deployment's policies through,
 * on the cases the SNS developer guide spells out: exact string match, `exists` on leaf nodes only
 * (an object is not a leaf), nested keys, element-wise arrays, and a refusal of operators it does
 * not model.
 */

import { snsMessageBodyPolicyAdmits as admits } from "./snsFilterPolicy";

describe("SNS message-body filter evaluator", () => {
    test("an exact string condition matches an equal leaf only", () => {
        expect(admits({ eventSource: ["aws:dynamodb"] }, { eventSource: "aws:dynamodb" })).toBe(
            true
        );
        expect(admits({ eventSource: ["aws:dynamodb"] }, { eventSource: "aws:s3" })).toBe(false);
        expect(admits({ eventSource: ["aws:dynamodb"] }, {})).toBe(false);
        // The string condition is on the top-level key: a nested one of the same name is another key.
        expect(admits({ eventSource: ["aws:s3"] }, { Records: [{ eventSource: "aws:s3" }] })).toBe(
            false
        );
    });

    test("exists applies to leaf nodes only", () => {
        const record = { Keys: { metadataKey: { S: "owner" } } };
        // On the object: no leaf there, so `exists: false` matches and `exists: true` does not.
        expect(admits({ Keys: { metadataKey: [{ exists: false }] } }, record)).toBe(true);
        expect(admits({ Keys: { metadataKey: [{ exists: true }] } }, record)).toBe(false);
        // On the leaf beneath it.
        expect(admits({ Keys: { metadataKey: { S: [{ exists: false }] } } }, record)).toBe(false);
        expect(admits({ Keys: { metadataKey: { S: [{ exists: true }] } } }, record)).toBe(true);
    });

    test("exists: false matches a key whose parents are absent", () => {
        const policy = { dynamodb: { Keys: { metadataKey: { S: [{ exists: false }] } } } };
        expect(admits(policy, { Records: [] })).toBe(true);
        expect(admits(policy, { dynamodb: { Keys: { attributeKey: { S: "a" } } } })).toBe(true);
    });

    test("arrays in the message match element-wise", () => {
        expect(admits({ tags: ["b"] }, { tags: ["a", "b"] })).toBe(true);
        expect(admits({ items: { kind: ["x"] } }, { items: [{ kind: "y" }, { kind: "x" }] })).toBe(
            true
        );
        expect(admits({ items: { kind: ["x"] } }, { items: [{ kind: "y" }] })).toBe(false);
    });

    test("every key of a policy must match", () => {
        const policy = { a: ["1"], b: [{ exists: true }] };
        expect(admits(policy, { a: "1", b: "x" })).toBe(true);
        expect(admits(policy, { a: "1" })).toBe(false);
    });

    test("an operator it does not model is refused, not guessed", () => {
        expect(() => admits({ a: [{ prefix: "x" }] }, { a: "xy" })).toThrow(/Unsupported/);
        expect(() => admits({ a: [{ "anything-but": "x" }] }, { a: "y" })).toThrow(/Unsupported/);
    });
});
