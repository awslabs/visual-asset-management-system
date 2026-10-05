/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Isaac Lab Lambdas that read asset buckets hold the customer managed key of an external asset
 * bucket that declares one (`bucketKmsKeyArn`).
 *
 * `bucket.grantRead()` adds a KMS statement only when the bucket construct carries its encryption key.
 * An external bucket is imported by ARN without one, so its key reaches a role only through
 * `grantExternalAssetBucketKmsKeys()`, which the asset-bucket grant helpers call. The vamsExecute lambda
 * reads the workflow input manifest from the default asset bucket, which may be an external one, and
 * fails the launch when that read is refused. openPipeline reads standing defaults from a JSON input
 * file in any asset bucket and drops them when the read is refused. The Batch job role is granted the
 * key directly by the construct and is the positive control.
 */

import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { IsaacLabTrainingConstruct } from "../../lib/nestedStacks/pipelines/simulation/isaacLabTraining/constructs/isaacLabTraining-construct";
import { REGION, makePipelineHarness } from "../support/pipelineConstructHarness";

const EXTERNAL_ACCOUNT = "222222222222";
const EXTERNAL_BUCKET_ARN = "arn:aws:s3:::vams-test-external";
const KEY_ARN = `arn:aws:kms:${REGION}:${EXTERNAL_ACCOUNT}:key/external-asset-bucket-key`;

/** The Isaac Lab Lambdas that read objects from the asset buckets. */
const READING_HANDLERS: Array<[string, string]> = [
    ["openPipeline", "openPipeline.lambda_handler"],
    ["vamsExecute", "vamsExecuteIsaacLabPipeline.lambda_handler"],
];

/** The Isaac Lab Lambdas that make no Amazon S3 call. */
const NON_READING_HANDLERS = [
    "executeBatchJob.lambda_handler",
    "closePipeline.lambda_handler",
    "handleError.lambda_handler",
];

/**
 * One Isaac Lab construct beside the harness's asset bucket and an imported external bucket in another
 * account, whose record carries `kmsKeyArn` (none when it is undefined).
 */
const synthIsaacLab = (stackId: string, kmsKeyArn: string | undefined): Template => {
    const h = makePipelineHarness(stackId, (c) => {
        c.app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS = false;
    });
    s3AssetBuckets.addS3AssetBucket(
        s3.Bucket.fromBucketAttributes(h.stack, "ExternalBucket", {
            bucketArn: EXTERNAL_BUCKET_ARN,
            account: EXTERNAL_ACCOUNT,
        }),
        "/",
        "db",
        EXTERNAL_ACCOUNT,
        kmsKeyArn,
        false
    );
    new IsaacLabTrainingConstruct(h.stack, "IsaacLabTrainingConstruct", {
        config: h.config,
        vpc: h.vpc,
        pipelineSubnets: h.subnets,
        pipelineSubnetsIsolated: h.subnets,
        pipelineSecurityGroups: h.securityGroups,
        storageResources: h.storage,
        lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        codeBuildImage: h.codeBuildImage,
    });
    return Template.fromStack(h.stack);
};

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

/** Resources may be plain ARNs or unresolved intrinsics, so they stay untyped. */
const resourcesOf = (statement: any): any[] =>
    Array.isArray(statement.Resource) ? statement.Resource : [statement.Resource];

/** The logical id of the role that the one Lambda with this Handler runs as. */
const roleOfHandler = (template: Template, handler: string): string => {
    const functions = Object.values(
        template.findResources("AWS::Lambda::Function", { Properties: { Handler: handler } })
    ) as any[];
    expect(functions).toHaveLength(1);
    return functions[0].Properties.Role["Fn::GetAtt"][0];
};

/** The logical id of the role that the one Batch job definition runs its container as. */
const batchJobRole = (template: Template): string => {
    const definitions = Object.values(template.findResources("AWS::Batch::JobDefinition")) as any[];
    expect(definitions).toHaveLength(1);
    return definitions[0].Properties.ContainerProperties.JobRoleArn["Fn::GetAtt"][0];
};

/**
 * Every statement that applies to a role: its inline policies, and each AWS::IAM::Policy and
 * AWS::IAM::ManagedPolicy attached to it. CDK moves statements into a managed policy once the default
 * policy grows too large, so a grant can sit in either.
 */
const roleStatements = (template: Template, roleId: string): any[] => {
    const role = template.findResources("AWS::IAM::Role")[roleId];
    expect(role).toBeDefined();
    const inline = ((role.Properties.Policies ?? []) as any[]).flatMap(
        (policy) => policy.PolicyDocument.Statement
    );
    const referenced = new Set(
        ((role.Properties.ManagedPolicyArns ?? []) as any[]).map((arn) => arn && arn.Ref)
    );
    const attached = ["AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"].flatMap((type) =>
        Object.entries(template.findResources(type))
            .filter(
                ([logicalId, policy]) =>
                    referenced.has(logicalId) ||
                    ((policy.Properties.Roles ?? []) as any[]).some(
                        (ref) => ref && ref.Ref === roleId
                    )
            )
            .flatMap(([, policy]) => policy.Properties.PolicyDocument.Statement as any[])
    );
    return [...inline, ...attached];
};

/** True when a statement allows kms:Decrypt on the external bucket's key. */
const grantsExternalKeyDecrypt = (statement: any): boolean =>
    statement.Effect === "Allow" &&
    resourcesOf(statement).includes(KEY_ARN) &&
    actionsOf(statement).includes("kms:Decrypt");

describe("Isaac Lab roles and an external asset bucket that declares a customer managed key", () => {
    let template: Template;

    beforeAll(() => {
        template = synthIsaacLab("IsaacLabExternalKeyStack", KEY_ARN);
    });

    test.each(READING_HANDLERS)(
        "the %s lambda role can decrypt with the external bucket key",
        (_name, handler) => {
            // A statement may be merged with others, so any statement naming the key will do.
            const statements = roleStatements(template, roleOfHandler(template, handler));
            expect(statements.some(grantsExternalKeyDecrypt)).toBe(true);
        }
    );

    test("the Batch job role can decrypt with the external bucket key", () => {
        // Positive control: the construct grants this role the key directly, so an unregistered key
        // or a statement reader that misses the attached policies fails here as well.
        const statements = roleStatements(template, batchJobRole(template));
        expect(statements.some(grantsExternalKeyDecrypt)).toBe(true);
    });

    test.each(NON_READING_HANDLERS)(
        "the %s lambda role names no external bucket key",
        (handler) => {
            // Negative control: the key follows the asset-bucket read grant, not every role in the
            // construct.
            const statements = roleStatements(template, roleOfHandler(template, handler));
            expect(JSON.stringify(statements)).not.toContain(KEY_ARN);
        }
    );
});

describe("Isaac Lab roles and an external asset bucket without a customer managed key", () => {
    let template: Template;

    beforeAll(() => {
        template = synthIsaacLab("IsaacLabNoExternalKeyStack", undefined);
    });

    test.each(READING_HANDLERS)("the %s lambda role carries no KMS action", (_name, handler) => {
        // Negative control: no record declares a key, so the grant helper adds nothing, and a grant
        // written without that condition (for example kms:Decrypt on "*") fails here.
        const statements = roleStatements(template, roleOfHandler(template, handler));
        // Control: the reader found the role's policy (its Step Functions callback grant).
        expect(statements.length).toBeGreaterThan(0);
        expect(statements.flatMap(actionsOf).filter((a) => a.startsWith("kms:"))).toEqual([]);
    });
});
