/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Every AWS WAF web ACL must have a LoggingConfiguration.
 *
 * `visibilityConfig` alone yields CloudWatch metrics and a rolling sample of requests, not a durable
 * record. That mattered less when the managed rule groups ran in count mode; this release moved them to
 * block mode, so WAF now answers a rejected request before any VAMS Lambda runs and nothing else logs it.
 *
 * Three properties are asserted because each fails differently:
 *
 *   - the destination name starts with `aws-waf-logs-`  — AWS WAF rejects any other name, at deploy time
 *   - the Authorization header is redacted              — otherwise every recorded request logs a bearer
 *                                                         token in cleartext
 *   - one configuration per ACL                         — the regional and CloudFront ACLs are separate
 *                                                         resources in separate Regions
 */

import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { WAFScope, Wafv2BasicConstruct } from "../../lib/constructs/wafv2-basic-construct";
import { newTestApp } from "../support/testApp";

function synthAcl(wafScope: WAFScope): Template {
    const app = newTestApp();
    const stack = new cdk.Stack(app, `waf-${wafScope}`, {
        env: {
            account: "111111111111",
            region: wafScope === WAFScope.CLOUDFRONT ? "us-east-1" : "us-west-2",
        },
    });
    new Wafv2BasicConstruct(stack, "Waf", {
        wafScope,
        stackName: "vams-test",
        env: { account: "111111111111", region: "us-west-2" },
    });
    return Template.fromStack(stack);
}

describe.each([WAFScope.REGIONAL, WAFScope.CLOUDFRONT])("%s web ACL logging", (wafScope) => {
    const template = synthAcl(wafScope);

    it("emits exactly one web ACL and one logging configuration for it", () => {
        // Control plus assertion in one: a template with no ACL would satisfy a bare "has logging" check.
        template.resourceCountIs("AWS::WAFv2::WebACL", 1);
        template.resourceCountIs("AWS::WAFv2::LoggingConfiguration", 1);
    });

    it("logs to a group whose name starts with aws-waf-logs-", () => {
        // AWS WAF enforces this prefix and rejects the stack otherwise, so it is checked here rather
        // than discovered during a deploy.
        const groups = template.findResources("AWS::Logs::LogGroup");
        const names = Object.values(groups).map((g) =>
            JSON.stringify(
                (g as { Properties?: { LogGroupName?: unknown } }).Properties?.LogGroupName
            )
        );
        expect(names.length).toBeGreaterThan(0);
        for (const name of names) {
            expect(name).toContain("aws-waf-logs-");
        }
    });

    it("redacts the Authorization header", () => {
        const configs = template.findResources("AWS::WAFv2::LoggingConfiguration");
        const redacted = Object.values(configs).map((c) =>
            JSON.stringify(
                (c as { Properties?: { RedactedFields?: unknown } }).Properties?.RedactedFields
            )
        );
        expect(redacted.length).toBe(1);
        expect(redacted[0].toLowerCase()).toContain("authorization");
    });

    it("points the configuration at the ACL it belongs to", () => {
        const configs = Object.values(template.findResources("AWS::WAFv2::LoggingConfiguration"));
        const resourceArn = JSON.stringify(
            (configs[0] as { Properties?: { ResourceArn?: unknown } }).Properties?.ResourceArn
        );
        // A LoggingConfiguration naming a different ACL silently logs nothing for this one.
        expect(resourceArn).toContain("webacl");
    });
});
