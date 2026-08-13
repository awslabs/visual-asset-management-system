/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import {
    Wafv2BasicConstruct,
    WAFScope,
    WafPolicyConfig,
} from "../lib/constructs/wafv2-basic-construct";

const buildTemplate = (policy?: WafPolicyConfig, scope = WAFScope.REGIONAL): Template => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, "WafTestStack");
    new Wafv2BasicConstruct(stack, "Waf", { wafScope: scope, wafPolicy: policy });
    return Template.fromStack(stack);
};

const getWebAcl = (template: Template): any =>
    Object.values(template.findResources("AWS::WAFv2::WebACL"))[0];

const getRule = (template: Template, name: string): any =>
    getWebAcl(template).Properties.Rules.find((r: any) => r.Name === name);

describe("Wafv2BasicConstruct rate-based rules", () => {
    const policy: WafPolicyConfig = {
        managedRuleGroups: [
            {
                name: "AWS-AWSManagedRulesCommonRuleSet",
                vendorName: "AWS",
                managedRuleGroupName: "AWSManagedRulesCommonRuleSet",
                priority: 1,
                block: true,
            },
        ],
        rateBasedRules: [
            {
                name: "VAMS-RateLimit",
                priority: 10,
                limit: 10000,
                aggregateKeyType: "FORWARDED_IP",
                forwardedIPConfig: {
                    headerName: "X-Forwarded-For",
                    fallbackBehavior: "NO_MATCH",
                },
                blockResponseCode: 429,
            },
        ],
    };

    test("rate rule blocks with a 429 custom response referencing the registered body", () => {
        const template = buildTemplate(policy);
        const rule = getRule(template, "VAMS-RateLimit");
        expect(rule.Action.Block.CustomResponse.ResponseCode).toBe(429);
        expect(rule.Action.Block.CustomResponse.CustomResponseBodyKey).toBe("VamsRateLimitBody");

        // The referenced body must be registered on the ACL.
        const acl = getWebAcl(template);
        expect(acl.Properties.CustomResponseBodies.VamsRateLimitBody).toBeDefined();
        expect(acl.Properties.CustomResponseBodies.VamsRateLimitBody.ContentType).toBe(
            "APPLICATION_JSON"
        );
    });

    test("FORWARDED_IP sets the ForwardedIPConfig (real client IP behind CloudFront/ALB)", () => {
        const template = buildTemplate(policy);
        const rule = getRule(template, "VAMS-RateLimit");
        expect(rule.Statement.RateBasedStatement.AggregateKeyType).toBe("FORWARDED_IP");
        expect(rule.Statement.RateBasedStatement.ForwardedIPConfig).toEqual({
            HeaderName: "X-Forwarded-For",
            FallbackBehavior: "NO_MATCH",
        });
        expect(rule.Statement.RateBasedStatement.Limit).toBe(10000);
    });

    test("works identically for the CLOUDFRONT-scope ACL", () => {
        const template = buildTemplate(policy, WAFScope.CLOUDFRONT);
        const rule = getRule(template, "VAMS-RateLimit");
        expect(rule.Action.Block.CustomResponse.ResponseCode).toBe(429);
        expect(rule.Statement.RateBasedStatement.ForwardedIPConfig.HeaderName).toBe(
            "X-Forwarded-For"
        );
    });

    test("plain IP aggregate omits ForwardedIPConfig", () => {
        const ipPolicy: WafPolicyConfig = {
            rateBasedRules: [{ name: "R", priority: 10, limit: 2000, aggregateKeyType: "IP" }],
        };
        const template = buildTemplate(ipPolicy);
        const rule = getRule(template, "R");
        expect(rule.Statement.RateBasedStatement.AggregateKeyType).toBe("IP");
        expect(rule.Statement.RateBasedStatement.ForwardedIPConfig).toBeUndefined();
        // Still defaults to a 429 throttle response.
        expect(rule.Action.Block.CustomResponse.ResponseCode).toBe(429);
    });

    test("no custom-response body is registered when there are no rate rules", () => {
        const template = buildTemplate({
            managedRuleGroups: [
                {
                    name: "AWS-AWSManagedRulesCommonRuleSet",
                    vendorName: "AWS",
                    managedRuleGroupName: "AWSManagedRulesCommonRuleSet",
                    priority: 1,
                    block: true,
                },
            ],
        });
        const acl = getWebAcl(template);
        expect(acl.Properties.CustomResponseBodies).toBeUndefined();
    });
});
