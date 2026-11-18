/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `addIamNagSuppressions` writes an IAM suppression only where cdk-nag reads it (#1647).
 *
 * The shared Lambda suppressions used to be added with `applyToChildren`, which stamps them on every
 * resource under the scope. cdk-nag's AwsSolutions-IAM4 rule reads roles, users and groups, and
 * AwsSolutions-IAM5 reads those plus policies and managed policies, and of a role only its inline
 * `Policies`. The copies anywhere else suppressed nothing and put `ApiBuilder2` over CloudFormation's
 * 1,000,000-byte template limit. These tests pin where each copy lands.
 */

import * as cdk from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as sqs from "aws-cdk-lib/aws-sqs";
import { addIamNagSuppressions } from "../../lib/helper/security";
import { newTestApp } from "../support/testApp";

const IAM4 = { id: "AwsSolutions-IAM4", reason: "test reason for IAM4", appliesTo: ["Policy::x"] };
const IAM5 = {
    id: "AwsSolutions-IAM5",
    reason: "test reason for IAM5",
    appliesTo: ["Action::s3:*"],
};

/** The rule ids in one L1's `cdk_nag` metadata. */
const ruleIds = (resource: cdk.CfnResource): string[] =>
    (resource.getMetadata("cdk_nag")?.rules_to_suppress ?? []).map((r: { id: string }) => r.id);

function build() {
    const stack = new cdk.Stack(newTestApp(), "Stack");
    const fn = new lambda.Function(stack, "Fn", {
        code: lambda.Code.fromInline("def lambda_handler(e, c): pass"),
        handler: "index.lambda_handler",
        runtime: lambda.Runtime.PYTHON_3_12,
    });
    const queue = new sqs.Queue(stack, "Queue");
    queue.grantSendMessages(fn); // creates the role's DefaultPolicy
    const inlineRole = new iam.Role(fn, "InlineRole", {
        assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
        inlinePolicies: {
            inline: new iam.PolicyDocument({
                statements: [new iam.PolicyStatement({ actions: ["s3:*"], resources: ["*"] })],
            }),
        },
    });
    const role = fn.role as iam.Role;
    return {
        fn: fn.node.defaultChild as cdk.CfnResource,
        role: role.node.defaultChild as cdk.CfnResource,
        defaultPolicy: role.node.findChild("DefaultPolicy").node.defaultChild as cdk.CfnResource,
        inlineRole: inlineRole.node.defaultChild as cdk.CfnResource,
        // The function's own L1 is the non-IAM control: neither rule reads it.
        fnScope: fn,
    };
}

describe("addIamNagSuppressions", () => {
    test("IAM4 lands on the roles only", () => {
        const r = build();
        addIamNagSuppressions(r.fnScope, [IAM4]);
        expect(ruleIds(r.role)).toEqual(["AwsSolutions-IAM4"]);
        expect(ruleIds(r.inlineRole)).toEqual(["AwsSolutions-IAM4"]);
        expect(ruleIds(r.defaultPolicy)).toEqual([]);
        expect(ruleIds(r.fn)).toEqual([]);
    });

    test("IAM5 lands on the default policy and on a role with inline policies", () => {
        const r = build();
        addIamNagSuppressions(r.fnScope, [IAM5]);
        expect(ruleIds(r.defaultPolicy)).toEqual(["AwsSolutions-IAM5"]);
        expect(ruleIds(r.inlineRole)).toEqual(["AwsSolutions-IAM5"]);
        // No inline policies and its default policy is suppressed: cdk-nag reads nothing here.
        expect(ruleIds(r.role)).toEqual([]);
        expect(ruleIds(r.fn)).toEqual([]);
    });

    test("IAM5 stays on a role whose default policy is not in the scope", () => {
        // The overflow-policy aspect copies a role's suppressions from the role or its default
        // policy; with no default policy suppressed, the role is the only copy it can find.
        const r = build();
        addIamNagSuppressions(r.role, [IAM5]);
        expect(ruleIds(r.role)).toEqual(["AwsSolutions-IAM5"]);
        expect(ruleIds(r.defaultPolicy)).toEqual([]);
    });

    test("a rule other than IAM4 or IAM5 is rejected", () => {
        const r = build();
        expect(() =>
            addIamNagSuppressions(r.fnScope, [
                { id: "AwsSolutions-L1", reason: "test reason for L1" },
            ])
        ).toThrow(/not an IAM rule/);
    });
});
