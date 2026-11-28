/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Evaluates an Amazon SNS payload-based (`FilterPolicyScope: MessageBody`) filter policy against a
 * message body, for the operators the deployment's policies use, as the SNS developer guide
 * describes them (Amazon SNS message filtering: "Payload-based message filtering", "Attribute key
 * matching", "Exact matching"):
 *
 * - A policy object is matched key by key, and every key must match (AND). A key whose value is an
 *   object is a nested policy for the same key of the message; a key whose value is an array lists
 *   conditions on that key's value, any of which may match (OR).
 * - A condition applies to leaf values only (strings, numbers, booleans, null). A message value
 *   that is an object is not a leaf: a policy that places a condition on it, `exists` included,
 *   finds no leaf there. So `{"exists": false}` on a key whose value is an object matches, and
 *   `{"exists": true}` does not.
 * - Arrays in the message are matched element-wise: a key matches when any element does.
 * - A string condition matches a leaf equal to it; `{"exists": true}` matches when the key has a
 *   leaf value, `{"exists": false}` when it has none (absent, or not a leaf).
 *
 * Any other operator throws, so a policy that starts using one fails its test until this evaluator
 * models it.
 */

type Leaf = string | number | boolean | null;

const isLeaf = (value: unknown): value is Leaf =>
    value === null || ["string", "number", "boolean"].includes(typeof value);

/** The leaf values `key` names in `node`, descending into arrays element-wise. */
const leavesAt = (node: unknown, key: string): Leaf[] => {
    if (Array.isArray(node)) return node.flatMap((element) => leavesAt(element, key));
    if (node === null || typeof node !== "object") return [];
    const value = (node as Record<string, unknown>)[key];
    if (value === undefined) return [];
    if (Array.isArray(value)) return value.filter(isLeaf);
    return isLeaf(value) ? [value] : [];
};

/** The objects `key` names in `node`, descending into arrays element-wise. */
const objectsAt = (node: unknown, key: string): unknown[] => {
    if (Array.isArray(node)) return node.flatMap((element) => objectsAt(element, key));
    if (node === null || typeof node !== "object") return [];
    const value = (node as Record<string, unknown>)[key];
    if (Array.isArray(value)) return value.filter((v) => v !== null && typeof v === "object");
    return value !== null && typeof value === "object" ? [value] : [];
};

const conditionMatches = (condition: unknown, leaves: Leaf[]): boolean => {
    if (isLeaf(condition)) return leaves.some((leaf) => leaf === condition);
    if (condition !== null && typeof condition === "object" && !Array.isArray(condition)) {
        const entries = Object.entries(condition as Record<string, unknown>);
        if (entries.length === 1 && entries[0][0] === "exists") {
            return entries[0][1] === true ? leaves.length > 0 : leaves.length === 0;
        }
    }
    throw new Error(`Unsupported SNS filter condition: ${JSON.stringify(condition)}`);
};

/** Whether `policy` admits a message whose body is `message`, evaluated at one nesting level. */
const policyMatches = (policy: Record<string, unknown>, nodes: unknown[]): boolean =>
    Object.entries(policy).every(([key, rule]) => {
        if (Array.isArray(rule)) {
            const leaves = nodes.flatMap((node) => leavesAt(node, key));
            return rule.some((condition) => conditionMatches(condition, leaves));
        }
        if (rule !== null && typeof rule === "object") {
            return policyMatches(
                rule as Record<string, unknown>,
                nodes.flatMap((node) => objectsAt(node, key))
            );
        }
        throw new Error(`Unsupported SNS filter policy value at ${key}: ${JSON.stringify(rule)}`);
    });

/** Whether an SNS subscription with this message-body filter policy delivers `message`. */
export const snsMessageBodyPolicyAdmits = (
    policy: Record<string, unknown>,
    message: unknown
): boolean => policyMatches(policy, [message]);
