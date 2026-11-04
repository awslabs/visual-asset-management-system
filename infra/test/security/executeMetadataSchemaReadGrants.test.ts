/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The workflow execute handler's reads for metadataInputs.metadataSchema, in every shipped config
 * template: exactly a Query on the metadata schema table's database/entity-type index and a GetItem on
 * the database table, in a policy of their own, and the role's inline policies still well under IAM's
 * 10,240-character limit.
 *
 * Tables live in the storage nested stack and the handler in the API stack, so a statement's Resource
 * carries the cross-stack parameter whose NAME embeds the table's construct id; the match is on that
 * name, as in workflowExecutionLockGrants.test.ts.
 *
 * The size check counts every inline policy attached to the role (the default policy, its overflow
 * policies, the lock policy and the schema policy), whitespace excluded, with each intrinsic replaced by
 * a string of a deployed value's length (an ESTIMATE; see arnLength): a DynamoDB table ARN is counted at
 * 200 characters (CloudFormation-generated table names make each about that long, as measured for the
 * execution service role), a bucket ARN at its 63-character name, a Lambda ARN at a 64-character
 * name, a log group ARN at the VAMS audit-log name length, any other ARN at 200, and the partition,
 * Region and account id at their own lengths in the template's target partition.
 */

import { EXECUTE_METADATA_SCHEMA_READ_POLICY_ID } from "../../lib/lambdaBuilder/workflowFunctions";
import { METADATA_SCHEMA_DATABASE_ENTITY_TYPE_INDEX } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { ALL_TEMPLATES, SynthResult, TemplateName, synthTemplate } from "../support/templateSynth";

jest.setTimeout(600_000);

const EXECUTE_ROLE = /^executeWorkflowServiceRole/;
const SCHEMA_TABLE = /MetadataSchemaStorageTableV2/;
const DATABASE_TABLE = /DatabaseStorageTable/;
const IAM_INLINE_POLICY_LIMIT = 10240;
/**
 * The estimate this role must stay under. It is the IAM limit itself, not a margin under it: with the
 * schema policy the GovCloud estimate is within about 50 characters of the limit (the same exposure
 * #1001 records for the execution service role, which grows with the stack name), so this check
 * catches any further growth while the role's headroom is restored separately.
 */
const INLINE_POLICY_BUDGET = IAM_INLINE_POLICY_LIMIT;
const ARN_PLACEHOLDER_LENGTH = 200;

const PSEUDO_LENGTH: Record<string, Record<string, number>> = {
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

const actionsOf = (st: any): string[] =>
    (Array.isArray(st.Action) ? st.Action : [st.Action]).filter(Boolean);

function executeRoleId(synth: SynthResult): string {
    const roles = synth.ofType("AWS::IAM::Role").filter((r) => EXECUTE_ROLE.test(r.logicalId));
    expect(roles).toHaveLength(1);
    return roles[0].logicalId;
}

/** Every inline policy document attached to the role: [logicalId, document]. */
function inlinePolicies(synth: SynthResult, roleId: string): [string, any][] {
    const docs: [string, any][] = [];
    for (const policy of synth.ofType("AWS::IAM::Policy")) {
        if (JSON.stringify((policy.properties as any).Roles ?? []).includes(`"${roleId}"`)) {
            docs.push([policy.logicalId, (policy.properties as any).PolicyDocument]);
        }
    }
    const role = synth.ofType("AWS::IAM::Role").find((r) => r.logicalId === roleId)!;
    for (const doc of ((role.properties as any).Policies ?? []) as any[]) {
        docs.push([`${roleId}:${doc.PolicyName}`, doc.PolicyDocument]);
    }
    return docs;
}

/**
 * Estimated deployed length of an ARN named by a cross-stack parameter or a GetAtt, from what it names
 * (the parameter name embeds the source construct id; a GetAtt's resource type is in the synth). The
 * commercial lengths are adjusted by the target partition's and Region's own lengths.
 */
function arnLength(name: string, resourceType: string, template: TemplateName): number {
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
function resolveForSize(value: any, template: TemplateName, types: Map<string, string>): any {
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

const sizeOf = (doc: any, template: TemplateName, types: Map<string, string>): number =>
    JSON.stringify(resolveForSize(doc, template, types)).replace(/\s/g, "").length;

describe.each(ALL_TEMPLATES)("execute handler metadata schema reads (%s template)", (name) => {
    let synth: SynthResult;
    let roleId: string;

    beforeAll(() => {
        synth = synthTemplate(name);
        roleId = executeRoleId(synth);
    });

    test("the schema and database tables are in this synth", () => {
        // Control for the assertions below: a template without the tables satisfies "no grant" trivially.
        expect(
            synth.where("AWS::DynamoDB::Table", (r) => SCHEMA_TABLE.test(r.logicalId))
        ).toHaveLength(1);
        expect(
            synth.where("AWS::DynamoDB::Table", (r) => DATABASE_TABLE.test(r.logicalId))
        ).toHaveLength(1);
    });

    test("the role reads the schema index with Query and the database table with GetItem only", () => {
        const statements = inlinePolicies(synth, roleId).flatMap(
            ([, doc]) => (doc?.Statement ?? []) as any[]
        );
        const onSchema = statements.filter((st) => SCHEMA_TABLE.test(JSON.stringify(st.Resource)));
        const onDatabase = statements.filter((st) =>
            DATABASE_TABLE.test(JSON.stringify(st.Resource))
        );
        expect(onSchema.flatMap(actionsOf)).toEqual(["dynamodb:Query"]);
        expect(onDatabase.flatMap(actionsOf)).toEqual(["dynamodb:GetItem"]);
        const schemaResources = ([] as any[]).concat(onSchema[0].Resource);
        expect(schemaResources).toHaveLength(1);
        expect(JSON.stringify(schemaResources[0])).toContain(
            `/index/${METADATA_SCHEMA_DATABASE_ENTITY_TYPE_INDEX}`
        );
        const databaseResources = ([] as any[]).concat(onDatabase[0].Resource);
        expect(databaseResources).toHaveLength(1);
        expect(JSON.stringify(databaseResources[0])).not.toContain("/index/");
    });

    test("the reads live in the handler's own schema policy, not its default or overflow policy", () => {
        const holders = inlinePolicies(synth, roleId)
            .filter(([, doc]) =>
                ((doc?.Statement ?? []) as any[]).some(
                    (st) =>
                        SCHEMA_TABLE.test(JSON.stringify(st.Resource)) ||
                        DATABASE_TABLE.test(JSON.stringify(st.Resource))
                )
            )
            .map(([logicalId]) => logicalId);
        expect(holders).toHaveLength(1);
        expect(holders[0]).toMatch(new RegExp(EXECUTE_METADATA_SCHEMA_READ_POLICY_ID));
        expect(holders[0]).not.toMatch(/DefaultPolicy|OverflowPolicy/);
    });

    test("the role's inline policies stay under the IAM limit with the schema policy added", () => {
        const types = new Map(
            synth.resources.map((r) => [r.logicalId, r.type] as [string, string])
        );
        const sizes = inlinePolicies(synth, roleId).map(
            ([logicalId, doc]) => [logicalId, sizeOf(doc, name, types)] as [string, number]
        );
        const total = sizes.reduce((sum, [, size]) => sum + size, 0);
        const schemaPolicy = sizes
            .filter(([logicalId]) => logicalId.includes(EXECUTE_METADATA_SCHEMA_READ_POLICY_ID))
            .reduce((sum, [, size]) => sum + size, 0);
        expect(schemaPolicy).toBeGreaterThan(0);
        // Reported for the PR record: the role's total before (without the schema policy) and after.
        console.log(
            `[${name}] execute role inline policies: before=${
                total - schemaPolicy
            } after=${total} schemaPolicy=${schemaPolicy} limit=${IAM_INLINE_POLICY_LIMIT}`
        );
        expect(total).toBeLessThan(INLINE_POLICY_BUDGET);
    });
});
