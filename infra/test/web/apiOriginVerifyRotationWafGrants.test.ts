/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The API origin verification rotation function's grants when the deployment has a regional web
 * ACL (`app.useWaf`). The T1 harness builds no WAF stack, so this builds the function directly.
 *
 * UpdateWebACL is authorized against the web ACL AND against every AWS managed rule group the web
 * ACL references, so the role needs the action on both or the update is denied.
 */
import * as cdk from "aws-cdk-lib";
import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Template } from "aws-cdk-lib/assertions";
import { newTestApp } from "../support/testApp";
import { createMockConfig, ACCOUNT, REGION } from "../support/pipelineConstructHarness";
import * as Service from "../../lib/helper/service-helper";

jest.mock("../../lib/helper/security");

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { buildApiOriginVerifyRotationFunction } = require("../../lib/lambdaBuilder/authFunctions");

const REGIONAL_ACL = `arn:aws:wafv2:${REGION}:${ACCOUNT}:regional/webacl/Wafv2CFwebacl-x/11111111-2222-3333-4444-555555555555`;
const CLOUDFRONT_ACL = `arn:aws:wafv2:us-east-1:${ACCOUNT}:global/webacl/Wafv2CFwebacl-y/66666666-7777-8888-9999-000000000000`;

function rotationStatements(withRegionalAcl: boolean): any[] {
    const config = createMockConfig();
    Service.SetConfig(config);
    const app = newTestApp();
    const stack = new cdk.Stack(app, "RotationGrants", {
        env: { account: ACCOUNT, region: REGION },
    });
    buildApiOriginVerifyRotationFunction(
        stack,
        lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ),
        { encryption: {} },
        config,
        {
            secret: {
                // pragma: allowlist nextline secret
                secretArn: `arn:aws:secretsmanager:${REGION}:${ACCOUNT}:secret:origin-verify-AbCdEf`,
            },
            distribution: cloudfront.Distribution.fromDistributionAttributes(stack, "Dist", {
                distributionId: "E1234567890ABC", // pragma: allowlist secret
                domainName: "d111111abcdef8.cloudfront.net",
            }),
            regionalWebAclArn: withRegionalAcl ? REGIONAL_ACL : undefined,
            cloudFrontWebAclArn: withRegionalAcl ? CLOUDFRONT_ACL : undefined,
        }
    );
    const policies = Template.fromStack(stack).findResources("AWS::IAM::Policy");
    return Object.values(policies).flatMap((p: any) => p.Properties.PolicyDocument.Statement);
}

const actions = (st: any): string[] => [].concat(st.Action);
const resources = (st: any): string[] =>
    [].concat(st.Resource).map((r: any) => (typeof r === "string" ? r : JSON.stringify(r)));

describe("API origin verification rotation function, regional web ACL present", () => {
    let statements: any[];
    beforeAll(() => {
        statements = rotationStatements(true);
    });

    test("UpdateWebACL is granted on the regional web ACL", () => {
        const onAcl = statements.filter((st) => resources(st).includes(REGIONAL_ACL));
        expect(onAcl.flatMap(actions).sort()).toEqual(["wafv2:GetWebACL", "wafv2:UpdateWebACL"]);
    });

    test("UpdateWebACL, and only it, is granted on the regional AWS managed rule groups", () => {
        const onManaged = statements.filter((st) =>
            resources(st).some((r) => r.includes(":regional/managedruleset/*/*"))
        );
        expect(onManaged).toHaveLength(1);
        expect(actions(onManaged[0])).toEqual(["wafv2:UpdateWebACL"]);
        expect(resources(onManaged[0])).toHaveLength(1);
        expect(resources(onManaged[0])[0]).not.toContain("webacl");
    });

    test("the CLOUDFRONT-scoped web ACL is only read", () => {
        const onCf = statements.filter((st) => resources(st).includes(CLOUDFRONT_ACL));
        expect(onCf.flatMap(actions)).toEqual(["wafv2:GetWebACL"]);
    });
});

describe("API origin verification rotation function, no web ACL", () => {
    test("no wafv2 action is granted", () => {
        const statements = rotationStatements(false);
        expect(statements.flatMap(actions).filter((a) => a.startsWith("wafv2:"))).toEqual([]);
    });
});
