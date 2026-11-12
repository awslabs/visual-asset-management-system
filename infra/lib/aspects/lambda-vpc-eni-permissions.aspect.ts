/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cdk from "aws-cdk-lib";
import { IConstruct } from "constructs";

/** The AWS managed policy CDK attaches to the role of a function it places in a VPC. */
export const LAMBDA_VPC_ACCESS_POLICY_NAME = "service-role/AWSLambdaVPCAccessExecutionRole";

/**
 * The network interface changes `AWSLambdaVPCAccessExecutionRole` allows. Lambda makes these calls itself,
 * outside the function's execution environment; no VAMS function code calls them. The two read actions
 * the policy also allows (`ec2:DescribeNetworkInterfaces`, `ec2:DescribeSubnets`) are left out because
 * some VAMS function code calls them.
 */
export const LAMBDA_ENI_CHANGE_ACTIONS = [
    "ec2:AssignPrivateIpAddresses",
    "ec2:CreateNetworkInterface",
    "ec2:DeleteNetworkInterface",
    "ec2:UnassignPrivateIpAddresses",
];

/** Construct id of the per-stack customer managed policy that holds the function-code Deny. */
export const LAMBDA_ENI_DENY_POLICY_ID = "LambdaEniChangesDeniedToFunctionCode";

/**
 * Keeps the network interface permissions on every Lambda execution role while the deployment has a VPC.
 *
 * VAMS puts most functions in the VPC only when `app.useGlobalVpc.useForAllLambdas` is on, and CDK attaches
 * `AWSLambdaVPCAccessExecutionRole` to a function's role only while the function is in the VPC. An update
 * that takes a function out of the VPC therefore detaches the policy (the role is updated first, because
 * the function references it) and then removes the function's `VpcConfig`. Lambda deletes the function's
 * network interfaces afterwards, asynchronously and as the execution role, so the call is denied; Lambda
 * does not retry it, and the orphaned interfaces then block deleting the old security groups and the VPC
 * subnets. A rolled-back update that put the function in the VPC fails the same way.
 *
 * With a VPC present, this aspect attaches the policy to the role of every function, in the VPC or not, so
 * an update that moves a function never removes it. CDK renders `ManagedPolicyArns` as a set, so a role
 * that already has the policy is unchanged. The allow stays on `Resource: "*"`, which Lambda requires for
 * these actions. For a function outside the VPC, the role also gets a customer managed policy (one per
 * stack) with a Deny of the network interface changes for calls made by function code: Lambda adds
 * `lambda:SourceFunctionArn` to the credentials of the execution environment and not to its own network
 * interface calls, so the Deny takes nothing from Lambda and gives the code of a function outside the VPC
 * no new EC2 access. A partition without that condition key leaves the Deny inert, never stricter.
 *
 * shortcut: turning `app.useGlobalVpc.enabled` off removes the VPC and this aspect in one update, so the
 * functions still in the VPC at that point lose the policy as they leave it; the troubleshooting guide's
 * two-update procedure avoids that. For a one-step path, keep the grants for one update after the VPC goes.
 */
export class LambdaVpcEniPermissionsAspect implements cdk.IAspect {
    private readonly vpcAccessPolicy = iam.ManagedPolicy.fromAwsManagedPolicyName(
        LAMBDA_VPC_ACCESS_POLICY_NAME
    );
    private readonly codeDenyPolicies = new WeakMap<cdk.Stack, iam.ManagedPolicy>();

    public visit(node: IConstruct): void {
        // Imported roles are managed outside VAMS; precreated roles (iamRoleConfig) record these in the
        // policy report through the same calls.
        if (!(node instanceof lambda.Function) || !(node.role instanceof iam.Role)) {
            return;
        }
        const role = node.role;
        role.addManagedPolicy(this.vpcAccessPolicy);
        if (!node.isBoundToVpc) {
            role.addManagedPolicy(this.codeDenyPolicy(cdk.Stack.of(role)));
        }
    }

    /**
     * One customer managed policy per stack, so the Deny does not count against a role's inline policy
     * quota (several API roles are close to it in the GovCloud and EU Sovereign partitions).
     */
    private codeDenyPolicy(stack: cdk.Stack): iam.ManagedPolicy {
        let policy = this.codeDenyPolicies.get(stack);
        if (!policy) {
            policy = new iam.ManagedPolicy(stack, LAMBDA_ENI_DENY_POLICY_ID, {
                description:
                    "Denies Lambda function code the network interface changes that " +
                    "AWSLambdaVPCAccessExecutionRole allows; Lambda's own calls are not affected.",
                statements: [
                    new iam.PolicyStatement({
                        sid: "DenyEniChangesByFunctionCode",
                        effect: iam.Effect.DENY,
                        actions: LAMBDA_ENI_CHANGE_ACTIONS,
                        resources: ["*"],
                        conditions: { Null: { "lambda:SourceFunctionArn": "false" } },
                    }),
                ],
            });
            this.codeDenyPolicies.set(stack, policy);
        }
        return policy;
    }
}
