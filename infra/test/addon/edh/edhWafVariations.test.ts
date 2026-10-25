/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The WAF stacks of a deployment, across the variations that decide which web ACLs exist and what
 * they carry, built through `buildWafStacks` as `bin/infra.ts` builds them.
 *
 * What is pinned here:
 *   * the regional web ACL alone carries the AMI recipe exemption, in every fronting mode
 *     (CloudFront, the ALB, API only) and partition `getConfig()` accepts with the add-on on, and
 *     only while the add-on's AMI builds are on;
 *   * for each owner policy shape, the re-block rule is present exactly when the Common Rule Set is
 *     in block mode and the policy neither counts nor allows one of the relaxed rules, with each
 *     label once;
 *   * the CloudFront ACL is always the one the policy alone produces;
 *   * the stack names, scopes and Regions are the deployment's existing ones, and each stack equals
 *     one built directly with the expected props, synthesizer included;
 *   * an unrelaxed rule the policy enforces on the recipe routes is reported at synth, on the
 *     regional stack only.
 */

import { readFileSync } from "fs";
import { join } from "path";
import * as cdk from "aws-cdk-lib";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import * as Config from "../../../config/config";
import { STACK_WAF_DESCRIPTION } from "../../../config/config";
import { buildWafStacks, CfWafStack, WafStacks } from "../../../lib/cf-waf-stack";
import { buildBootstrapSynthesizer } from "../../../lib/helper/iamRoleCustomization";
import {
    Wafv2BasicConstruct,
    WAFScope,
    WafPolicyConfig,
} from "../../../lib/constructs/wafv2-basic-construct";
import { buildEdhWafRuleExclusions } from "../../../lib/nestedStacks/addon/edhWorkstations/edhWafExclusions";
import { buildTemplateConfig, TemplateName } from "../../support/templateSynth";
import { enableEdhAmiBuilds } from "./support/edhSynth";
import { newTestApp } from "../../support/testApp";

const COMMON_RULE_SET = "AWSManagedRulesCommonRuleSet";
const SSRF_RULE = "EC2MetaDataSSRF_BODY";
const RFI_RULE = "GenericRFI_BODY";
const SSRF_LABEL = "awswaf:managed:aws:core-rule-set:EC2MetaDataSSRF_Body";
const RFI_LABEL = "awswaf:managed:aws:core-rule-set:GenericRFI_Body";
const RECIPE_PREFIX = "/api/addon/edh/admin/ami-recipes";
const REBLOCK_RULE = "VAMS-EdhAmiRecipeBodyRules";
/** Text every unrelaxed-rule warning carries, and no other synth warning of these stacks. */
const UNRELAXED = "does not relax";

/** The policy that ships with the deployment, as `wafRateLimit.test.ts` reads it. */
const shippedPolicy: WafPolicyConfig = JSON.parse(
    readFileSync(join(__dirname, "..", "..", "..", "config", "policy", "wafPolicyConfig.json"), {
        encoding: "utf8",
    })
);

/** The shipped policy with one edit applied to a copy. */
const policyWith = (edit: (policy: WafPolicyConfig) => void): WafPolicyConfig => {
    const policy: WafPolicyConfig = JSON.parse(JSON.stringify(shippedPolicy));
    edit(policy);
    return policy;
};

const groupsOf = (policy: WafPolicyConfig) => policy.managedRuleGroups || [];

const commonRuleSetGroupOf = (policy: WafPolicyConfig) => {
    const group = groupsOf(policy).find((g) => g.managedRuleGroupName === COMMON_RULE_SET);
    if (!group) throw new Error("the shipped policy has no Common Rule Set group");
    return group;
};

const overriding =
    (name: string, action: "count" | "block" | "allow") => (policy: WafPolicyConfig) => {
        const group = commonRuleSetGroupOf(policy);
        group.ruleActionOverrides = [...(group.ruleActionOverrides || []), { name, action }];
    };

const withGroup = (managedRuleGroupName: string, block: boolean) => (policy: WafPolicyConfig) => {
    groupsOf(policy).push({
        name: `AWS-${managedRuleGroupName}`,
        vendorName: "AWS",
        managedRuleGroupName,
        priority: 4,
        block,
    });
};

const withoutSizeRestrictionsBody = (policy: WafPolicyConfig) => {
    const group = commonRuleSetGroupOf(policy);
    group.ruleActionOverrides = (group.ruleActionOverrides || []).filter(
        (o) => o.name !== "SizeRestrictions_BODY"
    );
};

type Fronting = "cloudFront" | "alb" | "apiOnly";

/** How the web app and API are fronted; the commercial template ships with CloudFront. */
const FRONTING: Record<Fronting, (c: any) => void> = {
    cloudFront: () => undefined,
    alb: (c) => {
        c.app.useCloudFront.enabled = false;
        c.app.useAlb.enabled = true;
        c.app.useAlb.usePublicSubnet = false;
        // getConfig() requires the VPC with the ALB; the add-on turns it on too.
        c.app.useGlobalVpc.enabled = true;
    },
    apiOnly: (c) => {
        c.app.useCloudFront.enabled = false;
        c.app.useAlb.enabled = false;
    },
};

/** Every fronting mode each partition accepts with the add-on on; govcloud ships the ALB. */
const VARIATIONS: Array<[TemplateName, Fronting]> = [
    ["commercial", "cloudFront"],
    ["commercial", "alb"],
    ["commercial", "apiOnly"],
    ["govcloud", "alb"],
    ["govcloud", "apiOnly"],
];

/** A deployment config from a shipped template, with the WAF policy getConfig() would load. */
const cfg = (
    name: TemplateName,
    mutate: (c: any) => void,
    policy: WafPolicyConfig | undefined
): Config.Config => {
    const config = buildTemplateConfig(name, mutate);
    config.wafPolicyJSON = policy;
    return config;
};

interface Built {
    app: cdk.App;
    config: Config.Config;
    stacks: WafStacks | undefined;
}

const build = (
    name: TemplateName,
    fronting: Fronting,
    amiBuilds: boolean,
    // Required rather than defaulted: a default would replace the absent (legacy) policy.
    policy: WafPolicyConfig | undefined,
    extra?: (c: any) => void
): Built => {
    const config = cfg(
        name,
        (c) => {
            FRONTING[fronting](c);
            if (amiBuilds) enableEdhAmiBuilds(c);
            extra?.(c);
        },
        policy
    );
    const app = newTestApp();
    return { app, config, stacks: buildWafStacks(app, config) };
};

/** The stacks of a deployment with WAF on; fails when buildWafStacks returned none. */
const stacksOf = (built: Built): WafStacks => {
    expect(built.stacks).toBeDefined();
    return built.stacks as WafStacks;
};

const aclOf = (stack: cdk.Stack): any => {
    const acls = Object.values(Template.fromStack(stack).findResources("AWS::WAFv2::WebACL"));
    expect(acls).toHaveLength(1);
    return acls[0];
};

/** The web ACL a Wafv2BasicConstruct builds from the policy alone, with no exclusion. */
const baselineAcl = (wafScope: WAFScope, wafPolicy: WafPolicyConfig | undefined): any => {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "WafBaselineStack");
    new Wafv2BasicConstruct(stack, "Waf", { wafScope, wafPolicy });
    return aclOf(stack);
};

const rulesOf = (acl: any): any[] => acl.Properties.Rules;

const ruleNamed = (acl: any, name: string): any => rulesOf(acl).find((r) => r.Name === name);

const commonRuleSetOverridesOf = (acl: any): any[] =>
    rulesOf(acl).find((r) => r.Statement.ManagedRuleGroupStatement?.Name === COMMON_RULE_SET)
        .Statement.ManagedRuleGroupStatement.RuleActionOverrides || [];

/** The labels the re-block rule blocks on, in order, or undefined with no re-block rule. */
const reblockLabelsOf = (acl: any): string[] | undefined => {
    const rule = ruleNamed(acl, REBLOCK_RULE);
    if (!rule) return undefined;
    const [labels] = rule.Statement.AndStatement.Statements;
    const matches: any[] = labels.OrStatement ? labels.OrStatement.Statements : [labels];
    return matches.map((m) => m.LabelMatchStatement.Key);
};

const uriPathStartsWith = (prefix: string, textTransformations: any[]): any => ({
    ByteMatchStatement: {
        SearchString: prefix,
        FieldToMatch: { UriPath: {} },
        TextTransformations: textTransformations,
        PositionalConstraint: "STARTS_WITH",
    },
});

/** The raw path and its decoded, normalized form must both start with the prefix. */
const prefixMatch = (prefix: string): any => ({
    AndStatement: {
        Statements: [
            uriPathStartsWith(prefix, [{ Priority: 0, Type: "NONE" }]),
            uriPathStartsWith(prefix, [
                { Priority: 0, Type: "URL_DECODE" },
                { Priority: 1, Type: "NORMALIZE_PATH" },
            ]),
        ],
    },
});

/** The unrelaxed-rule warnings of one stack, each with its acknowledgement id. */
const unrelaxedWarningsOf = (stack: cdk.Stack): string[] =>
    Annotations.fromStack(stack)
        .findWarning("*", Match.anyValue())
        .map((m) => String(m.entry.data))
        .filter((message) => message.includes(UNRELAXED));

const ackOf = (subject: string): string =>
    `[ack: VAMS:WafPolicy:${REBLOCK_RULE}:unrelaxed:${subject}]`;

/**
 * The WAF stacks built directly from the construct ids, stack names, envs, props and synthesizer
 * buildWafStacks is expected to pass, so it is compared with an independent statement of them
 * rather than with itself.
 */
const directStacks = (config: Config.Config): WafStacks => {
    const app = newTestApp();
    const base = config.app.baseStackName;
    const regionalName = config.app.useCloudFront.enabled
        ? `${config.name}-waf-regional-${base}`
        : `${config.name}-waf-${base}`;
    const regional = new CfWafStack(app, regionalName, {
        stackName: regionalName,
        env: { account: config.env.account, region: config.env.region },
        wafScope: WAFScope.REGIONAL,
        wafPolicy: config.wafPolicyJSON,
        scopedRuleExclusions: buildEdhWafRuleExclusions(config),
        description: STACK_WAF_DESCRIPTION,
        synthesizer: buildBootstrapSynthesizer(config),
    });
    if (!config.app.useCloudFront.enabled) return { regional };
    const cloudfrontName = `${config.name}-waf-${base}`;
    const cloudfront = new CfWafStack(app, cloudfrontName, {
        stackName: cloudfrontName,
        env: { account: config.env.account, region: "us-east-1" },
        wafScope: WAFScope.CLOUDFRONT,
        wafPolicy: config.wafPolicyJSON,
        description: STACK_WAF_DESCRIPTION,
        synthesizer: buildBootstrapSynthesizer(config),
    });
    return { regional, cloudfront };
};

describe.each(VARIATIONS)("%s deployment fronted by %s", (name, fronting) => {
    describe.each([
        ["on", true],
        ["off", false],
    ] as const)("with the add-on's AMI builds %s", (_label, amiBuilds) => {
        const built = build(name, fronting, amiBuilds, shippedPolicy);
        const { regional, cloudfront } = stacksOf(built);
        const withCloudFront = fronting === "cloudFront";

        test("builds a REGIONAL ACL in the deployment's Region, and a CLOUDFRONT ACL in us-east-1 only with CloudFront", () => {
            expect(aclOf(regional).Properties.Scope).toBe("REGIONAL");
            expect(regional.region).toBe(built.config.env.region);
            expect(regional.account).toBe(built.config.env.account);
            if (withCloudFront) {
                expect(cloudfront).toBeDefined();
                expect(aclOf(cloudfront as CfWafStack).Properties.Scope).toBe("CLOUDFRONT");
                expect((cloudfront as CfWafStack).region).toBe("us-east-1");
            } else {
                expect(cloudfront).toBeUndefined();
            }
        });

        test("names the stacks with the deployment's existing stack names", () => {
            const base = `${built.config.name}-waf`;
            const suffix = built.config.app.baseStackName;
            const expected = withCloudFront
                ? [`${base}-regional-${suffix}`, `${base}-${suffix}`]
                : [`${base}-${suffix}`];
            const stacks = [regional, ...(cloudfront ? [cloudfront] : [])];
            expect(stacks.map((s) => s.stackName)).toEqual(expected);
            // The construct id is the stack name, as for every top-level stack of the app.
            expect(stacks.map((s) => s.node.id)).toEqual(expected);
            // The app holds these stacks and no other, in this order.
            const appStacks = built.app.node.children.filter((c) => c instanceof cdk.Stack);
            expect(appStacks).toHaveLength(stacks.length);
            appStacks.forEach((s, i) => expect(s).toBe(stacks[i]));
        });

        if (amiBuilds) {
            test("the regional ACL exempts the recipe routes from both relaxed rules", () => {
                const rule = ruleNamed(aclOf(regional), REBLOCK_RULE);
                expect(rule).toBeDefined();
                expect(rule.Statement.AndStatement.Statements[1]).toEqual({
                    NotStatement: { Statement: prefixMatch(RECIPE_PREFIX) },
                });
                expect(reblockLabelsOf(aclOf(regional))).toEqual([SSRF_LABEL, RFI_LABEL]);
            });
        } else {
            test("the regional ACL is the one the policy alone produces", () => {
                expect(ruleNamed(aclOf(regional), REBLOCK_RULE)).toBeUndefined();
                expect(aclOf(regional).Properties).toEqual(
                    baselineAcl(WAFScope.REGIONAL, shippedPolicy).Properties
                );
            });
        }

        if (withCloudFront) {
            test("the CloudFront ACL is the one the policy alone produces", () => {
                expect(aclOf(cloudfront as CfWafStack).Properties).toEqual(
                    baselineAcl(WAFScope.CLOUDFRONT, shippedPolicy).Properties
                );
            });
        }

        test("each stack synthesizes as a CfWafStack built directly with the expected props", () => {
            const direct = directStacks(built.config);
            expect(Template.fromStack(regional).toJSON()).toEqual(
                Template.fromStack(direct.regional).toJSON()
            );
            expect(Boolean(cloudfront)).toBe(Boolean(direct.cloudfront));
            if (cloudfront && direct.cloudfront) {
                expect(Template.fromStack(cloudfront).toJSON()).toEqual(
                    Template.fromStack(direct.cloudfront).toJSON()
                );
            }
        });

        test("the shipped policy raises no unrelaxed-rule warning", () => {
            expect(unrelaxedWarningsOf(regional)).toEqual([]);
            if (cloudfront) expect(unrelaxedWarningsOf(cloudfront)).toEqual([]);
        });
    });
});

describe.each(["commercial", "govcloud", "eusovereign"] as const)(
    "%s deployment with WAF off",
    (name) => {
        test("has no web ACL stack", () => {
            const config = cfg(
                name,
                (c) => {
                    c.app.useWaf = false;
                },
                shippedPolicy
            );
            const app = newTestApp();
            expect(buildWafStacks(app, config)).toBeUndefined();
            expect(app.node.children.filter((c) => c instanceof cdk.Stack)).toHaveLength(0);
        });
    }
);

describe("custom bootstrap roles, commercial with CloudFront and AMI builds on", () => {
    const QUALIFIER = "t1qual";
    const built = build("commercial", "cloudFront", true, shippedPolicy, (c) => {
        c.app.iamRoleConfig.useCustomBootstrapRoles = true;
        c.iamRoleCustomizationJSON = { bootstrap: { qualifier: QUALIFIER } };
    });
    const { regional, cloudfront } = stacksOf(built);
    const bootstrapVersionOf = (stack: cdk.Stack): unknown =>
        Template.fromStack(stack).toJSON().Parameters?.BootstrapVersion?.Default;

    test("both stacks synthesize against the deployment's bootstrap qualifier", () => {
        expect(bootstrapVersionOf(regional)).toBe(`/cdk-bootstrap/${QUALIFIER}/version`);
        expect(cloudfront).toBeDefined();
        expect(bootstrapVersionOf(cloudfront as CfWafStack)).toBe(
            `/cdk-bootstrap/${QUALIFIER}/version`
        );
    });

    test("each stack synthesizes as a CfWafStack built directly with the expected props", () => {
        const direct = directStacks(built.config);
        // The direct stacks carry the custom synthesizer too, so the equality is not two stacks on
        // the default one.
        expect(bootstrapVersionOf(direct.regional)).toBe(`/cdk-bootstrap/${QUALIFIER}/version`);
        expect(Template.fromStack(regional).toJSON()).toEqual(
            Template.fromStack(direct.regional).toJSON()
        );
        expect(Template.fromStack(cloudfront as CfWafStack).toJSON()).toEqual(
            Template.fromStack(direct.cloudfront as CfWafStack).toJSON()
        );
    });
});

describe("policy variants, commercial with CloudFront and AMI builds on", () => {
    const VARIANTS: Array<[string, WafPolicyConfig | undefined, string[] | undefined]> = [
        ["the shipped policy", shippedPolicy, [SSRF_LABEL, RFI_LABEL]],
        ["no policy (the legacy count-only Common Rule Set)", undefined, undefined],
        [
            "the Common Rule Set in count mode",
            policyWith((p) => (commonRuleSetGroupOf(p).block = false)),
            undefined,
        ],
        [
            "no Common Rule Set",
            policyWith((p) => {
                p.managedRuleGroups = groupsOf(p).filter(
                    (g) => g.managedRuleGroupName !== COMMON_RULE_SET
                );
            }),
            undefined,
        ],
        [
            `${SSRF_RULE} overridden to block`,
            policyWith(overriding(SSRF_RULE, "block")),
            [SSRF_LABEL, RFI_LABEL],
        ],
        [
            `${SSRF_RULE} overridden to count`,
            policyWith(overriding(SSRF_RULE, "count")),
            [RFI_LABEL],
        ],
        [
            `${SSRF_RULE} overridden to allow`,
            policyWith(overriding(SSRF_RULE, "allow")),
            [RFI_LABEL],
        ],
        [
            "both relaxed rules overridden to count",
            policyWith((p) => {
                overriding(SSRF_RULE, "count")(p);
                overriding(RFI_RULE, "count")(p);
            }),
            undefined,
        ],
        [
            "a second Common Rule Set group",
            policyWith((p) => {
                groupsOf(p).push({
                    ...JSON.parse(JSON.stringify(commonRuleSetGroupOf(p))),
                    name: "AWS-AWSManagedRulesCommonRuleSet-2",
                    priority: 4,
                });
            }),
            [SSRF_LABEL, RFI_LABEL],
        ],
    ];

    describe.each(VARIANTS)("%s", (_variant, policy, labels) => {
        const { regional, cloudfront } = stacksOf(build("commercial", "cloudFront", true, policy));

        if (labels) {
            test(`re-blocks exactly ${labels.length} label(s) off the recipe routes`, () => {
                expect(reblockLabelsOf(aclOf(regional))).toEqual(labels);
                expect(
                    ruleNamed(aclOf(regional), REBLOCK_RULE).Statement.AndStatement.Statements[1]
                ).toEqual({ NotStatement: { Statement: prefixMatch(RECIPE_PREFIX) } });
            });
        } else {
            test("has no re-block rule and is the ACL the policy alone produces", () => {
                expect(reblockLabelsOf(aclOf(regional))).toBeUndefined();
                expect(aclOf(regional).Properties).toEqual(
                    baselineAcl(WAFScope.REGIONAL, policy).Properties
                );
            });
        }

        test("the CloudFront ACL is the one the policy alone produces", () => {
            expect(cloudfront).toBeDefined();
            expect(aclOf(cloudfront as CfWafStack).Properties).toEqual(
                baselineAcl(WAFScope.CLOUDFRONT, policy).Properties
            );
        });
    });

    test(`a policy block override on ${SSRF_RULE} becomes a count override`, () => {
        const { regional } = stacksOf(
            build("commercial", "cloudFront", true, policyWith(overriding(SSRF_RULE, "block")))
        );
        expect(
            commonRuleSetOverridesOf(aclOf(regional)).filter((o) => o.Name === SSRF_RULE)
        ).toEqual([{ Name: SSRF_RULE, ActionToUse: { Count: {} } }]);
    });
});

describe("unrelaxed-rule warnings, commercial with CloudFront", () => {
    const VARIANTS: Array<[string, WafPolicyConfig, string | undefined, string | undefined]> = [
        [
            "a Common Rule Set without the SizeRestrictions_BODY override",
            policyWith(withoutSizeRestrictionsBody),
            `the SizeRestrictions_BODY rule of ${COMMON_RULE_SET}`,
            `${COMMON_RULE_SET}:SizeRestrictions_BODY`,
        ],
        [
            "SizeRestrictions_BODY overridden to block",
            policyWith((p) => {
                withoutSizeRestrictionsBody(p);
                overriding("SizeRestrictions_BODY", "block")(p);
            }),
            `the SizeRestrictions_BODY rule of ${COMMON_RULE_SET}`,
            `${COMMON_RULE_SET}:SizeRestrictions_BODY`,
        ],
        [
            "SizeRestrictions_BODY overridden to allow",
            policyWith((p) => {
                withoutSizeRestrictionsBody(p);
                overriding("SizeRestrictions_BODY", "allow")(p);
            }),
            undefined,
            undefined,
        ],
        [
            "a Common Rule Set in count mode without the SizeRestrictions_BODY override",
            policyWith((p) => {
                withoutSizeRestrictionsBody(p);
                commonRuleSetGroupOf(p).block = false;
            }),
            undefined,
            undefined,
        ],
        ...[
            "AWSManagedRulesLinuxRuleSet",
            "AWSManagedRulesUnixRuleSet",
            "AWSManagedRulesWindowsRuleSet",
            "AWSManagedRulesSQLiRuleSet",
            "AWSManagedRulesPHPRuleSet",
        ].flatMap(
            (group): Array<[string, WafPolicyConfig, string | undefined, string | undefined]> => [
                [
                    `an added ${group} group`,
                    policyWith(withGroup(group, true)),
                    `the ${group} rule group`,
                    group,
                ],
                [
                    `an added ${group} group in count mode`,
                    policyWith(withGroup(group, false)),
                    undefined,
                    undefined,
                ],
            ]
        ),
    ];

    describe.each(VARIANTS)("%s", (_variant, policy, subject, ackSubject) => {
        test(
            subject
                ? "warns once on the regional stack, naming the rule and the recipe routes"
                : "raises no unrelaxed-rule warning",
            () => {
                const { regional } = stacksOf(build("commercial", "cloudFront", true, policy));
                const warnings = unrelaxedWarningsOf(regional);
                if (!subject) {
                    expect(warnings).toEqual([]);
                    return;
                }
                expect(warnings).toHaveLength(1);
                expect(warnings[0]).toContain(
                    `WAF policy enforces ${subject} in the REGIONAL web ACL`
                );
                expect(warnings[0]).toContain(`"${REBLOCK_RULE}" does not relax`);
                expect(warnings[0]).toContain(RECIPE_PREFIX);
                expect(warnings[0]).toContain(ackOf(ackSubject as string));
            }
        );

        test("the CloudFront stack carries no unrelaxed-rule warning", () => {
            const { cloudfront } = stacksOf(build("commercial", "cloudFront", true, policy));
            expect(cloudfront).toBeDefined();
            expect(unrelaxedWarningsOf(cloudfront as CfWafStack)).toEqual([]);
        });

        test("with AMI builds off, neither stack carries an unrelaxed-rule warning", () => {
            const { regional, cloudfront } = stacksOf(
                build("commercial", "cloudFront", false, policy)
            );
            expect(unrelaxedWarningsOf(regional)).toEqual([]);
            expect(unrelaxedWarningsOf(cloudfront as CfWafStack)).toEqual([]);
        });
    });

    test("the warning reader sees the construct's other policy warnings", () => {
        // Positive control for every empty result above: the same reader returns the rate-rule
        // aggregation warning the construct raises on the same stack, so an empty list is the
        // absence of the warning and not a reader that sees nothing.
        const policy = policyWith((p) => {
            withoutSizeRestrictionsBody(p);
            (p.rateBasedRules || [])[0].aggregateKeyType = "FORWARDED_IP";
        });
        const { regional } = stacksOf(build("commercial", "cloudFront", true, policy));
        const messages = Annotations.fromStack(regional)
            .findWarning("*", Match.anyValue())
            .map((m) => String(m.entry.data));
        expect(messages.filter((m) => m.includes("aggregateKeyType"))).toHaveLength(1);
        expect(messages.filter((m) => m.includes(UNRELAXED))).toHaveLength(1);
    });
});

describe("eusovereign deployment, where getConfig() rejects the add-on", () => {
    test("builds the regional ACL alone, with no re-block rule", () => {
        const built = build("eusovereign", "alb", false, shippedPolicy);
        const { regional, cloudfront } = stacksOf(built);
        expect(cloudfront).toBeUndefined();
        expect(regional.region).toBe(built.config.env.region);
        expect(aclOf(regional).Properties.Scope).toBe("REGIONAL");
        expect(ruleNamed(aclOf(regional), REBLOCK_RULE)).toBeUndefined();
        expect(aclOf(regional).Properties).toEqual(
            baselineAcl(WAFScope.REGIONAL, shippedPolicy).Properties
        );
    });
});
