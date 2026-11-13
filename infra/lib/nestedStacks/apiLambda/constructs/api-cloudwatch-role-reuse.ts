/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as apigw from "aws-cdk-lib/aws-apigateway";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../config/config";
import { IamRoleTransform } from "../../../aspects/iam-role-transform.aspect";
import { suppressCdkNagLambda } from "../../../helper/security";

/** Construct id of the decision custom resource in the core stack. Never rename it (see below). */
export const API_CLOUDWATCH_ROLE_DECISION_ID = "ApiCloudWatchRoleDecision";

/** The `Mode` the decision returns when the stack creates and owns the role. */
export const API_CLOUDWATCH_ROLE_MANAGED = "managed";

const HANDLER_SOURCE = path.join(__dirname, "apiCloudWatchRoleDecision", "index.py");

/**
 * Lets a fresh deployment reuse a retained API Gateway account CloudWatch role instead of failing.
 *
 * The REST API's `cloudWatchRole` (an `AWS::IAM::Role` plus the account + Region singleton
 * `AWS::ApiGateway::Account` that points at it) is retained on teardown and on rollback, and the
 * `IamRoleTransform` aspect gives the role a fixed name. A fresh deploy of the same configuration
 * after an uninstall or a rolled-back first deploy therefore asks for a role name that exists.
 *
 * A custom resource in `stack` (the REST API nested stack's parent) decides once, at its create:
 * - the parent stack is being UPDATED (every stack that existed before this change): `managed`;
 *   the role resource keeps its logical id, name and properties, so an upgrade does not replace,
 *   rename or recreate it;
 * - the parent stack is being CREATED and a role with the computed name exists: `reuse`; the role
 *   resource is not created, and the Account points at the existing role;
 * - otherwise (or when a lookup fails): `managed`, today's behaviour.
 * The decision is kept on later updates, so the role never moves in or out of the stack. Renaming
 * the decision resource would create it again during an update and answer `managed`, which on a
 * stack that reused a role would create the role with a name that exists; keep the id fixed.
 *
 * No-op when roles are not renamed (`renameRolesPrefix` undefined): CloudFormation then generates
 * the role name and a retained role cannot collide.
 */
export function configureApiCloudWatchRoleReuse(
    stack: cdk.Stack,
    restApi: apigw.RestApiBase,
    renameRolesPrefix: string | undefined
): cdk.CustomResource | undefined {
    if (renameRolesPrefix === undefined) return undefined;

    const role = restApi.node.tryFindChild("CloudWatchRole") as iam.Role | undefined;
    const account = restApi.node.tryFindChild("Account") as apigw.CfnAccount | undefined;
    if (!role || !account) {
        throw new Error(
            "configureApiCloudWatchRoleReuse: the REST API has no CloudWatch role; set cloudWatchRole: true."
        );
    }
    const cfnRole = role.node.defaultChild as iam.CfnRole;
    // The same formula the aspect stamps onto the role, so the lookup names exactly that role.
    const roleName = new IamRoleTransform(renameRolesPrefix, undefined).getRoleName(cfnRole);

    const scope = new Construct(stack, `${API_CLOUDWATCH_ROLE_DECISION_ID}Provider`);
    const handler = new lambda.Function(scope, "Handler", {
        runtime: LAMBDA_PYTHON_RUNTIME,
        handler: "index.on_event",
        timeout: cdk.Duration.minutes(2),
        code: lambda.Code.fromInline(fs.readFileSync(HANDLER_SOURCE, "utf8")),
    });
    // Read-only: the status of this stack, and the one role name the stack would create.
    handler.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["cloudformation:DescribeStacks"],
            resources: [cdk.Aws.STACK_ID],
        })
    );
    handler.addToRolePolicy(
        new iam.PolicyStatement({
            effect: iam.Effect.ALLOW,
            actions: ["iam:GetRole"],
            resources: [
                stack.formatArn({
                    service: "iam",
                    region: "",
                    resource: "role",
                    resourceName: roleName,
                }),
            ],
        })
    );
    suppressCdkNagLambda(handler);

    const provider = new cr.Provider(scope, "Provider", { onEventHandler: handler });
    NagSuppressions.addResourceSuppressions(
        provider,
        [
            {
                id: "AwsSolutions-L1",
                reason: "Runtime is managed by the CDK custom-resources framework (cr.Provider) that backs the API Gateway CloudWatch role decision; VAMS does not control this provider function's runtime version.",
            },
        ],
        true
    );

    const decision = new cdk.CustomResource(stack, API_CLOUDWATCH_ROLE_DECISION_ID, {
        serviceToken: provider.serviceToken,
        properties: {
            StackId: cdk.Aws.STACK_ID,
            RoleName: roleName,
        },
    });

    // Wire the decision into the REST API's stack. The condition only ever evaluates true on a
    // stack that existed before this change, so the role resource there is left as it was.
    const apiStack = cdk.Stack.of(restApi);
    const managed = new cdk.CfnCondition(apiStack, "ApiCloudWatchRoleManagedByStack", {
        expression: cdk.Fn.conditionEquals(
            decision.getAttString("Mode"),
            API_CLOUDWATCH_ROLE_MANAGED
        ),
    });
    cfnRole.cfnOptions.condition = managed;
    account.cloudWatchRoleArn = cdk.Fn.conditionIf(
        managed.logicalId,
        role.roleArn,
        decision.getAttString("RoleArn")
    ).toString();

    return decision;
}
