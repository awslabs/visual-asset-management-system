/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Guards FIX-035 (S1-INFRA-017): a rate-based rule that aggregates on the client-supplied
 * X-Forwarded-For header never fires for a request that omits the header.
 */

import { readFileSync } from "fs";
import { join } from "path";
import * as cdk from "aws-cdk-lib";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import {
    Wafv2BasicConstruct,
    WAFScope,
    WafPolicyConfig,
} from "../lib/constructs/wafv2-basic-construct";
import { newTestApp } from "./support/testApp";

const buildStack = (policy?: WafPolicyConfig, scope = WAFScope.REGIONAL): cdk.Stack => {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "WafTestStack");
    new Wafv2BasicConstruct(stack, "Waf", { wafScope: scope, wafPolicy: policy });
    return stack;
};

const buildTemplate = (policy?: WafPolicyConfig, scope = WAFScope.REGIONAL): Template =>
    Template.fromStack(buildStack(policy, scope));

const getWebAcl = (template: Template): any =>
    Object.values(template.findResources("AWS::WAFv2::WebACL"))[0];

const getRule = (template: Template, name: string): any =>
    getWebAcl(template).Properties.Rules.find((r: any) => r.Name === name);

/**
 * The policy that ships with the deployment. `getConfig()` reads this exact file into
 * `config.wafPolicyJSON` and `bin/infra.ts` hands the same object to BOTH CfWafStack
 * instances — the REGIONAL ACL (REST API stage always, plus the ALB when `useAlb`) and the
 * CLOUDFRONT ACL (the distribution, when `useCloudFront`). Driving the tests from the file
 * rather than a hand-written copy is what makes them cover the shipped configuration.
 */
const shippedPolicy: WafPolicyConfig = JSON.parse(
    readFileSync(join(__dirname, "..", "config", "policy", "wafPolicyConfig.json"), {
        encoding: "utf8",
    })
);

const shippedRateRules = shippedPolicy.rateBasedRules || [];

describe("shipped WAF policy rate-based rules", () => {
    /**
     * Positive control for every assertion below: the shipped policy must actually define a
     * rate-based rule. Without this, deleting the rule outright would satisfy all of the
     * "does not key on a header" assertions vacuously.
     */
    test("the shipped policy defines at least one rate-based rule with a limit", () => {
        expect(shippedRateRules.length).toBeGreaterThan(0);
        shippedRateRules.forEach((rule) => {
            expect(typeof rule.limit).toBe("number");
            expect(rule.limit).toBeGreaterThan(0);
        });
    });

    test("the shipped policy does not configure a forwarded-IP aggregation", () => {
        const policyJson = JSON.stringify(shippedPolicy);
        expect(policyJson).not.toContain("FORWARDED_IP");
        expect(policyJson).not.toContain("forwardedIPConfig");
    });
});

/**
 * Paired per-scope assertions. VAMS builds two ACLs from the one policy and the immediate
 * client differs between them, so a single-scope assertion cannot show the rule is correct for
 * the deployment that is not being asserted. The ALB deployment is covered here: it uses the
 * REGIONAL ACL, which needs no live deployment to assert at synth.
 */
describe.each([WAFScope.CLOUDFRONT, WAFScope.REGIONAL])("%s-scoped web ACL", (wafScope) => {
    const template = buildTemplate(shippedPolicy, wafScope);

    test("is emitted at the scope under test", () => {
        // Makes the pairing load-bearing: without this, building the same scope twice by
        // mistake would still pass every assertion in this block.
        expect(getWebAcl(template).Properties.Scope).toBe(wafScope.toString());
    });

    test("rate rules aggregate on the request-origin IP address", () => {
        shippedRateRules.forEach((policyRule) => {
            const statement = getRule(template, policyRule.name).Statement.RateBasedStatement;
            expect(statement.AggregateKeyType).toBe("IP");
            expect(statement.Limit).toBe(policyRule.limit);
            // The origin address comes from the connection, so it is present on every request
            // and a caller cannot vary it to reset their own counter.
            expect(statement.ForwardedIPConfig).toBeUndefined();
        });
    });

    test("rate rules block with a 429 custom response referencing the registered body", () => {
        const acl = getWebAcl(template);
        shippedRateRules.forEach((policyRule) => {
            const action = getRule(template, policyRule.name).Action;
            expect(action.Block.CustomResponse.ResponseCode).toBe(429);
            expect(action.Block.CustomResponse.CustomResponseBodyKey).toBe("VamsRateLimitBody");
        });
        expect(acl.Properties.CustomResponseBodies.VamsRateLimitBody).toBeDefined();
        expect(acl.Properties.CustomResponseBodies.VamsRateLimitBody.ContentType).toBe(
            "APPLICATION_JSON"
        );
    });

    /**
     * No emitted rule may key on a request header. Neither fallback behavior is a way out:
     * WAF skips a forwarded-IP rule entirely for a request that carries no such header (a
     * missing header never reaches the fallback), so NO_MATCH exempts every direct execute-api
     * caller from the limit, while MATCH blocks each of them outright on the first request —
     * VamsCLI, VamsMCP and the connectors all call the stage without an X-Forwarded-For header.
     */
    test("no rule keys on a request header or sets a MATCH fallback", () => {
        const rules = getWebAcl(template).Properties.Rules;
        rules.forEach((rule: any) => {
            expect(rule.Statement.RateBasedStatement?.ForwardedIPConfig).toBeUndefined();
        });
        expect(JSON.stringify(rules)).not.toContain("FallbackBehavior");
    });
});

describe("Wafv2BasicConstruct rate-based rule construction", () => {
    test("a rule with no aggregateKeyType defaults to the request-origin IP address", () => {
        const template = buildTemplate({
            rateBasedRules: [{ name: "R", priority: 10, limit: 2000 }],
        });
        const statement = getRule(template, "R").Statement.RateBasedStatement;
        expect(statement.AggregateKeyType).toBe("IP");
        expect(statement.ForwardedIPConfig).toBeUndefined();
        // Defaults to a 429 throttle response even without an explicit blockResponseCode.
        expect(getRule(template, "R").Action.Block.CustomResponse.ResponseCode).toBe(429);
    });

    describe.each([WAFScope.CLOUDFRONT, WAFScope.REGIONAL])(
        "a policy requesting FORWARDED_IP (%s scope)",
        (wafScope) => {
            const forwardedPolicy: WafPolicyConfig = {
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

            test("is still built on the request-origin IP address", () => {
                const statement = getRule(
                    buildTemplate(forwardedPolicy, wafScope),
                    "VAMS-RateLimit"
                ).Statement.RateBasedStatement;
                expect(statement.AggregateKeyType).toBe("IP");
                expect(statement.ForwardedIPConfig).toBeUndefined();
            });

            test("reports the substitution as a synth warning", () => {
                Annotations.fromStack(buildStack(forwardedPolicy, wafScope)).hasWarning(
                    "*",
                    Match.stringLikeRegexp(
                        `aggregateKeyType "FORWARDED_IP" for the ${wafScope.toString()} web ACL`
                    )
                );
            });
        }
    );

    test("blockResponseCode is honored so the throttle status stays configurable", () => {
        const template = buildTemplate({
            rateBasedRules: [{ name: "R", priority: 10, limit: 2000, blockResponseCode: 503 }],
        });
        expect(getRule(template, "R").Action.Block.CustomResponse.ResponseCode).toBe(503);
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
        expect(getWebAcl(template).Properties.CustomResponseBodies).toBeUndefined();
    });
});
