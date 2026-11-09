/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Estimates how many characters of IAM's per-role inline policy quota a synthesized role uses.
 *
 * IAM limits the aggregate size of every inline policy on one role to 10,240 characters, whitespace
 * excluded, measured on the DEPLOYED documents. A synthesized document still holds intrinsics (a table
 * ARN is a cross-stack parameter, a log group ARN a GetAtt), so each intrinsic is replaced by a string
 * of a deployed value's length. The result is an ESTIMATE:
 *
 * - a DynamoDB table ARN, and any ARN of an unknown kind, is counted at 200 characters
 *   (CloudFormation-generated table names make each about that long, as measured on a deployed
 *   execution service role);
 * - a bucket ARN at its 63-character maximum name, a Lambda ARN at a 64-character name, a log group ARN
 *   at the VAMS audit log group name length;
 * - the partition, Region, account id and URL suffix at their own lengths in the template's target
 *   partition, and every ARN adjusted by the partition's and Region's lengths.
 *
 * Counted per role: every AWS::IAM::Policy in the role's stack that names the role (the default policy,
 * the overflow policies CDK splits from it, and policies of their own such as the execution lock
 * policy), plus the role's own `Policies` property. Managed policies are not counted; IAM does not count
 * them against this quota.
 */

import { SynthResult, TemplateName } from "./templateSynth";

/** IAM: aggregate size of all inline policies on one role, whitespace excluded. Not adjustable. */
export const IAM_INLINE_POLICY_LIMIT = 10240;

const ARN_PLACEHOLDER_LENGTH = 200;

/** Deployed length of each pseudo parameter in each template's target partition and Region. */
export const PSEUDO_LENGTH: Record<TemplateName, Record<string, number>> = {
    commercial: {
        "AWS::Partition": 3,
        "AWS::Region": 9,
        "AWS::AccountId": 12,
        "AWS::URLSuffix": 13,
    },
    govcloud: {
        "AWS::Partition": 10,
        "AWS::Region": 13,
        "AWS::AccountId": 12,
        "AWS::URLSuffix": 13,
    },
    eusovereign: {
        "AWS::Partition": 8,
        "AWS::Region": 14,
        "AWS::AccountId": 12,
        "AWS::URLSuffix": 16,
    },
};

/**
 * Estimated deployed length of an ARN named by a cross-stack parameter or a GetAtt, from what it names
 * (the parameter name embeds the source construct id; a GetAtt's resource type is in the synth). The
 * commercial lengths are adjusted by the target partition's and Region's own lengths.
 */
export function arnLength(name: string, resourceType: string, template: TemplateName): number {
    const pseudo = PSEUDO_LENGTH[template];
    const partitionDelta = pseudo["AWS::Partition"] - 3;
    const regionDelta = pseudo["AWS::Region"] - 9;
    if (/StorageTable(V2)?[0-9A-F]{8}Arn$/.test(name) || resourceType === "AWS::DynamoDB::Table") {
        return ARN_PLACEHOLDER_LENGTH + partitionDelta + regionDelta;
    }
    if (/Bucket[0-9A-F]{8}Arn$/.test(name) || resourceType === "AWS::S3::Bucket") {
        return "arn:aws:s3:::".length + 63 + partitionDelta;
    }
    if (/LogGroup[0-9A-F]{8}Arn$/.test(name) || resourceType === "AWS::Logs::LogGroup") {
        return (
            "arn:aws:logs:us-east-1:123456789012:log-group:/aws/vendedlogs/VAMSAuditAuthentication-0123456789:*"
                .length +
            partitionDelta +
            regionDelta
        );
    }
    if (
        /(Service|Function)[0-9A-F]{8}Arn$/.test(name) ||
        resourceType === "AWS::Lambda::Function"
    ) {
        return (
            "arn:aws:lambda:us-east-1:123456789012:function:".length +
            64 +
            partitionDelta +
            regionDelta
        );
    }
    return ARN_PLACEHOLDER_LENGTH + partitionDelta + regionDelta;
}

/** A value with every intrinsic replaced by a string of its estimated deployed length. */
export function resolveForSize(
    value: any,
    template: TemplateName,
    types: Map<string, string>
): any {
    if (Array.isArray(value)) {
        return value.map((v) => resolveForSize(v, template, types));
    }
    if (value && typeof value === "object") {
        const keys = Object.keys(value);
        if (keys.length === 1 && keys[0] === "Ref") {
            const pseudo = PSEUDO_LENGTH[template][value.Ref];
            return "x".repeat(pseudo ?? arnLength(value.Ref, types.get(value.Ref) ?? "", template));
        }
        if (keys.length === 1 && keys[0] === "Fn::GetAtt") {
            const [logicalId] = value["Fn::GetAtt"];
            return "x".repeat(arnLength(logicalId, types.get(logicalId) ?? "", template));
        }
        if (keys.length === 1 && keys[0] === "Fn::Join") {
            const [separator, parts] = value["Fn::Join"];
            return (parts as any[]).map((p) => resolveForSize(p, template, types)).join(separator);
        }
        if (keys.length === 1 && keys[0] === "Fn::Sub") {
            const text = Array.isArray(value["Fn::Sub"]) ? value["Fn::Sub"][0] : value["Fn::Sub"];
            return String(text).replace(/\$\{[^}]+\}/g, "x".repeat(ARN_PLACEHOLDER_LENGTH));
        }
        const out: Record<string, any> = {};
        for (const key of keys) {
            out[key] = resolveForSize(value[key], template, types);
        }
        return out;
    }
    return value;
}

/** Logical id to resource type, for every resource of the synth (what arnLength reads a GetAtt by). */
export function resourceTypes(synth: SynthResult): Map<string, string> {
    return new Map(synth.resources.map((r) => [r.logicalId, r.type] as [string, string]));
}

/** Estimated deployed size of one policy document, whitespace excluded. */
export function policyDocumentSize(
    doc: any,
    template: TemplateName,
    types: Map<string, string>
): number {
    return JSON.stringify(resolveForSize(doc, template, types)).replace(/\s/g, "").length;
}

/**
 * Every inline policy document attached to the role: [policy logical id, document]. Only policies in
 * the role's own stack are considered when `stack` is given (a policy names its role by Ref, which
 * cannot cross a stack).
 */
export function inlinePolicies(
    synth: SynthResult,
    roleId: string,
    stack?: string
): [string, any][] {
    const docs: [string, any][] = [];
    for (const policy of synth.ofType("AWS::IAM::Policy")) {
        if (stack !== undefined && policy.stack !== stack) continue;
        if (JSON.stringify((policy.properties as any).Roles ?? []).includes(`"${roleId}"`)) {
            docs.push([policy.logicalId, (policy.properties as any).PolicyDocument]);
        }
    }
    const role = synth
        .ofType("AWS::IAM::Role")
        .find((r) => r.logicalId === roleId && (stack === undefined || r.stack === stack));
    for (const doc of ((role?.properties as any)?.Policies ?? []) as any[]) {
        docs.push([`${roleId}:${doc.PolicyName}`, doc.PolicyDocument]);
    }
    return docs;
}

/** IAM: size of one customer managed policy document, whitespace excluded. Not adjustable. */
export const IAM_MANAGED_POLICY_LIMIT = 6144;

/**
 * Every customer managed policy of the role's stack attached to the role, either through the role's
 * ManagedPolicyArns (Role.addManagedPolicy) or through the policy's own Roles: [logical id, document].
 */
export function managedPolicies(
    synth: SynthResult,
    roleId: string,
    stack: string
): [string, any][] {
    const role = synth
        .ofType("AWS::IAM::Role")
        .find((r) => r.logicalId === roleId && r.stack === stack);
    const attached = JSON.stringify((role?.properties as any)?.ManagedPolicyArns ?? []);
    return synth
        .ofType("AWS::IAM::ManagedPolicy")
        .filter(
            (p) =>
                p.stack === stack &&
                (attached.includes(`"${p.logicalId}"`) ||
                    JSON.stringify((p.properties as any).Roles ?? []).includes(`"${roleId}"`))
        )
        .map((p) => [p.logicalId, (p.properties as any).PolicyDocument] as [string, any]);
}

/** One role's estimated inline policy usage. */
export interface RoleInlineSize {
    roleId: string;
    stack: string;
    total: number;
    /** Each attached inline policy and its estimated size. */
    policies: [string, number][];
    /** The three largest statements, for a failure message: [estimated size, actions]. */
    largestStatements: [number, string][];
    /** Each attached customer managed policy and its estimated size (not counted in `total`). */
    managed: [string, number][];
}

/** Estimated inline policy usage of one role. */
export function roleInlineSize(
    synth: SynthResult,
    roleId: string,
    stack: string,
    types: Map<string, string> = resourceTypes(synth)
): RoleInlineSize {
    const docs = inlinePolicies(synth, roleId, stack);
    const policies = docs.map(
        ([id, doc]) => [id, policyDocumentSize(doc, synth.name, types)] as [string, number]
    );
    const statements: [number, string][] = docs.flatMap(([, doc]) =>
        ((doc?.Statement ?? []) as any[]).map(
            (st) =>
                [
                    policyDocumentSize(st, synth.name, types),
                    ([] as string[]).concat(st.Action ?? []).join(","),
                ] as [number, string]
        )
    );
    statements.sort((a, b) => b[0] - a[0]);
    return {
        roleId,
        stack,
        total: policies.reduce((sum, [, size]) => sum + size, 0),
        policies,
        largestStatements: statements.slice(0, 3),
        managed: managedPolicies(synth, roleId, stack).map(
            ([id, doc]) => [id, policyDocumentSize(doc, synth.name, types)] as [string, number]
        ),
    };
}

/** Estimated inline policy usage of every role in one emitted template, largest first. */
export function roleInlineSizesInStack(synth: SynthResult, stack: string): RoleInlineSize[] {
    const types = resourceTypes(synth);
    return synth
        .ofType("AWS::IAM::Role")
        .filter((r) => r.stack === stack)
        .map((r) => roleInlineSize(synth, r.logicalId, stack, types))
        .sort((a, b) => b.total - a.total);
}

/**
 * The emitted template of the nested stack built by construct id `construct`.
 *
 * CDK names a nested-stack artifact `<sanitized stack name><construct path><8 hex uniqueid>`, so
 * stripping the fixed-width suffix and requiring the remainder to END with the construct id separates
 * `ApiBuilder` from `ApiBuilder2` exactly.
 */
export function nestedTemplateKeyFor(s: SynthResult, construct: string): string {
    const keys = Object.keys(s.templates)
        .map((key) => ({ key, id: key.replace(/\.nested$/, "") }))
        .filter(({ id }) => /[0-9A-F]{8}$/.test(id) && id.slice(0, -8).endsWith(construct))
        .map(({ key }) => key);
    if (keys.length !== 1) {
        throw new Error(
            `expected exactly one nested template for construct ${construct}, found ` +
                `${keys.length} (${keys.join(", ")}).`
        );
    }
    return keys[0];
}
