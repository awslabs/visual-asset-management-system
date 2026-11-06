/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The WAF stacks of a deployment, across the variations that decide which web ACLs exist and what
 * they carry, built through `buildWafStacks` as `bin/infra.ts` builds them (the origin verification
 * stack included, for a CloudFront deployment).
 *
 * What is pinned here:
 *   * every web ACL of the deployment carries the AMI recipe exemption: both web ACLs in a
 *     CloudFront deployment, the regional one alone with CloudFront off (the ALB, API only), in
 *     every partition `getConfig()` accepts with the add-on on, and only while the add-on's AMI
 *     builds are on;
 *   * with the shipped policy only the EC2MetaDataSSRF_BODY label is re-blocked: GenericRFI_BODY
 *     keeps the policy's own count;
 *   * for each owner policy shape, the re-block rule is present exactly when the Common Rule Set is
 *     in block mode and the policy neither counts nor allows one of the relaxed rules, with each
 *     label once, and is the same in both web ACLs;
 *   * with the add-on's AMI builds off, or a policy shape that needs no re-block, each web ACL is the
 *     one the policy alone produces with the same origin verification wiring;
 *   * the stack names, scopes and Regions are the deployment's existing ones, and each stack equals
 *     one built directly with the expected props, synthesizer included;
 *   * an unrelaxed rule the policy enforces on the recipe routes is reported at synth, once on each
 *     web ACL stack.
 */

import { readFileSync } from "fs";
import { join } from "path";
import * as cdk from "aws-cdk-lib";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import * as Config from "../../../config/config";
import { STACK_WAF_DESCRIPTION } from "../../../config/config";
import {
    ApiOriginVerifyStack,
    buildApiOriginVerifyStack,
} from "../../../lib/apiOriginVerify-stack";
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

/** Drops the policy's own override of one Common Rule Set rule, so the group's block applies. */
const withoutOverride = (name: string) => (policy: WafPolicyConfig) => {
    const group = commonRuleSetGroupOf(policy);
    group.ruleActionOverrides = (group.ruleActionOverrides || []).filter((o) => o.name !== name);
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

const withoutSizeRestrictionsBody = withoutOverride("SizeRestrictions_BODY");

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
    originVerify: ApiOriginVerifyStack | undefined;
    stacks: WafStacks | undefined;
}

/** The origin verification stack and WAF stacks of `config` in `app`, wired as bin/infra.ts wires them. */
const buildLikeTheApp = (app: cdk.App, config: Config.Config) => {
    const originVerify = buildApiOriginVerifyStack(app, config, buildBootstrapSynthesizer(config));
    return { originVerify, stacks: buildWafStacks(app, config, originVerify) };
};

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
    return { app, config, ...buildLikeTheApp(app, config) };
};

/** The stacks of a deployment with WAF on; fails when buildWafStacks returned none. */
const stacksOf = (built: Built): WafStacks => {
    expect(built.stacks).toBeDefined();
    return built.stacks as WafStacks;
};

/** Each web ACL stack of the deployment: the regional one, then the CloudFront one if any. */
const aclStacksOf = (built: Built): CfWafStack[] => {
    const { regional, cloudfront } = stacksOf(built);
    return [regional, ...(cloudfront ? [cloudfront] : [])];
};

const aclOf = (stack: cdk.Stack): any => {
    const acls = Object.values(Template.fromStack(stack).findResources("AWS::WAFv2::WebACL"));
    expect(acls).toHaveLength(1);
    return acls[0];
};

/**
 * The web ACL a Wafv2BasicConstruct builds from the policy alone, with no exclusion, for the scope
 * of `stack`: the regional one takes the origin verification secret ARN of a CloudFront deployment
 * of `config`, built the same way in an app of its own, so its references render as the
 * deployment's do.
 */
const baselineAclFor = (
    config: Config.Config,
    stack: CfWafStack,
    wafPolicy: WafPolicyConfig | undefined
): any => {
    const wafScope =
        aclOf(stack).Properties.Scope === "REGIONAL" ? WAFScope.REGIONAL : WAFScope.CLOUDFRONT;
    const app = newTestApp();
    const originVerify =
        wafScope === WAFScope.REGIONAL
            ? buildApiOriginVerifyStack(app, config, buildBootstrapSynthesizer(config))
            : undefined;
    const baseline = new cdk.Stack(app, "WafBaselineStack", {
        env: { account: stack.account, region: stack.region },
    });
    new Wafv2BasicConstruct(baseline, "Waf", {
        wafScope,
        wafPolicy,
        originVerifySecretArn: originVerify?.secretRef.secretArn,
    });
    return aclOf(baseline);
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

const RAW_PATH = [{ Priority: 0, Type: "NONE" }];
const NORMALIZED_PATH = [
    { Priority: 0, Type: "URL_DECODE" },
    { Priority: 1, Type: "NORMALIZE_PATH" },
];

const uriPathMatch = (searchString: string, positionalConstraint: string, transforms: any[]) => ({
    ByteMatchStatement: {
        SearchString: searchString,
        FieldToMatch: { UriPath: {} },
        TextTransformations: transforms,
        PositionalConstraint: positionalConstraint,
    },
});

/** The path, in one form, is the prefix itself or a path below it (a segment boundary). */
const atOrBelow = (prefix: string, transforms: any[]): any => ({
    OrStatement: {
        Statements: [
            uriPathMatch(prefix, "EXACTLY", transforms),
            uriPathMatch(`${prefix}/`, "STARTS_WITH", transforms),
        ],
    },
});

/** The raw path and its decoded, normalized form must both be at or below the prefix. */
const prefixMatch = (prefix: string): any => ({
    AndStatement: {
        Statements: [atOrBelow(prefix, RAW_PATH), atOrBelow(prefix, NORMALIZED_PATH)],
    },
});

/** The re-block rule's path clause: the request is not at or below the recipe prefix. */
const offRecipeRoutes = { NotStatement: { Statement: prefixMatch(RECIPE_PREFIX) } };

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
 * rather than with itself. The origin verification stack is built as bin/infra.ts builds it.
 */
const directStacks = (config: Config.Config): WafStacks => {
    const app = newTestApp();
    const originVerify = buildApiOriginVerifyStack(app, config, buildBootstrapSynthesizer(config));
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
        originVerifySecretArn: originVerify?.secretRef.secretArn,
        description: STACK_WAF_DESCRIPTION,
        synthesizer: buildBootstrapSynthesizer(config),
    });
    if (originVerify) regional.addStackDependency(originVerify);
    if (!config.app.useCloudFront.enabled) return { regional };
    const cloudfrontName = `${config.name}-waf-${base}`;
    const cloudfront = new CfWafStack(app, cloudfrontName, {
        stackName: cloudfrontName,
        env: { account: config.env.account, region: "us-east-1" },
        wafScope: WAFScope.CLOUDFRONT,
        wafPolicy: config.wafPolicyJSON,
        scopedRuleExclusions: buildEdhWafRuleExclusions(config),
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
            const stacks = aclStacksOf(built);
            expect(stacks.map((s) => s.stackName)).toEqual(expected);
            // The construct id is the stack name, as for every top-level stack of the app.
            expect(stacks.map((s) => s.node.id)).toEqual(expected);
            // The app holds these stacks, after the origin verification stack of a CloudFront
            // deployment, and no other, in this order.
            expect(Boolean(built.originVerify)).toBe(withCloudFront);
            const expectedStacks = [...(built.originVerify ? [built.originVerify] : []), ...stacks];
            const appStacks = built.app.node.children.filter((c) => c instanceof cdk.Stack);
            expect(appStacks).toHaveLength(expectedStacks.length);
            appStacks.forEach((s, i) => expect(s).toBe(expectedStacks[i]));
        });

        if (amiBuilds) {
            test(
                withCloudFront
                    ? "both web ACLs exempt the recipe routes from EC2MetaDataSSRF_BODY alone"
                    : "the regional ACL exempts the recipe routes from EC2MetaDataSSRF_BODY alone",
                () => {
                    const stacks = aclStacksOf(built);
                    expect(stacks).toHaveLength(withCloudFront ? 2 : 1);
                    for (const stack of stacks) {
                        const rule = ruleNamed(aclOf(stack), REBLOCK_RULE);
                        expect(rule).toBeDefined();
                        expect(rule.Statement.AndStatement.Statements[1]).toEqual(offRecipeRoutes);
                        // GenericRFI_BODY keeps the shipped policy's count and is not re-blocked.
                        expect(reblockLabelsOf(aclOf(stack))).toEqual([SSRF_LABEL]);
                    }
                }
            );
        } else {
            test("each web ACL is the one the policy alone produces", () => {
                for (const stack of aclStacksOf(built)) {
                    expect(ruleNamed(aclOf(stack), REBLOCK_RULE)).toBeUndefined();
                    expect(aclOf(stack).Properties).toEqual(
                        baselineAclFor(built.config, stack, shippedPolicy).Properties
                    );
                }
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
            for (const stack of aclStacksOf(built)) {
                expect(unrelaxedWarningsOf(stack)).toEqual([]);
            }
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
        ["the shipped policy", shippedPolicy, [SSRF_LABEL]],
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
            [SSRF_LABEL],
        ],
        [`${SSRF_RULE} overridden to count`, policyWith(overriding(SSRF_RULE, "count")), undefined],
        [`${SSRF_RULE} overridden to allow`, policyWith(overriding(SSRF_RULE, "allow")), undefined],
        [
            `no ${RFI_RULE} override (a policy that blocks it)`,
            policyWith(withoutOverride(RFI_RULE)),
            [SSRF_LABEL, RFI_LABEL],
        ],
        [
            `${RFI_RULE} overridden to block`,
            policyWith((p) => {
                withoutOverride(RFI_RULE)(p);
                overriding(RFI_RULE, "block")(p);
            }),
            [SSRF_LABEL, RFI_LABEL],
        ],
        [
            `${RFI_RULE} blocked and ${SSRF_RULE} overridden to count`,
            policyWith((p) => {
                withoutOverride(RFI_RULE)(p);
                overriding(SSRF_RULE, "count")(p);
            }),
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
            [SSRF_LABEL],
        ],
    ];

    describe.each(VARIANTS)("%s", (_variant, policy, labels) => {
        const built = build("commercial", "cloudFront", true, policy);
        const { regional, cloudfront } = stacksOf(built);

        test("builds both web ACLs", () => {
            expect(cloudfront).toBeDefined();
            expect(aclStacksOf(built)).toHaveLength(2);
        });

        if (labels) {
            test(`re-blocks exactly ${labels.length} label(s) off the recipe routes in both web ACLs`, () => {
                for (const stack of aclStacksOf(built)) {
                    expect(reblockLabelsOf(aclOf(stack))).toEqual(labels);
                    expect(
                        ruleNamed(aclOf(stack), REBLOCK_RULE).Statement.AndStatement.Statements[1]
                    ).toEqual(offRecipeRoutes);
                }
                // One prefix form serves both web ACLs, so the re-block statements are the same.
                expect(ruleNamed(aclOf(cloudfront as CfWafStack), REBLOCK_RULE).Statement).toEqual(
                    ruleNamed(aclOf(regional), REBLOCK_RULE).Statement
                );
            });
        } else {
            test("has no re-block rule and each web ACL is the one the policy alone produces", () => {
                for (const stack of aclStacksOf(built)) {
                    expect(reblockLabelsOf(aclOf(stack))).toBeUndefined();
                    expect(aclOf(stack).Properties).toEqual(
                        baselineAclFor(built.config, stack, policy).Properties
                    );
                }
            });
        }
    });

    test(`a policy block override on ${SSRF_RULE} becomes a count override in both web ACLs`, () => {
        const built = build(
            "commercial",
            "cloudFront",
            true,
            policyWith(overriding(SSRF_RULE, "block"))
        );
        for (const stack of aclStacksOf(built)) {
            expect(
                commonRuleSetOverridesOf(aclOf(stack)).filter((o) => o.Name === SSRF_RULE)
            ).toEqual([{ Name: SSRF_RULE, ActionToUse: { Count: {} } }]);
        }
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
                ? "warns once on each web ACL stack, naming the rule, the web ACL and the recipe routes"
                : "raises no unrelaxed-rule warning",
            () => {
                const built = build("commercial", "cloudFront", true, policy);
                const stacks = aclStacksOf(built);
                expect(stacks).toHaveLength(2);
                for (const stack of stacks) {
                    const warnings = unrelaxedWarningsOf(stack);
                    if (!subject) {
                        expect(warnings).toEqual([]);
                        continue;
                    }
                    const scope = aclOf(stack).Properties.Scope;
                    expect(warnings).toHaveLength(1);
                    expect(warnings[0]).toContain(
                        `WAF policy enforces ${subject} in the ${scope} web ACL`
                    );
                    expect(warnings[0]).toContain(`"${REBLOCK_RULE}" does not relax`);
                    expect(warnings[0]).toContain(RECIPE_PREFIX);
                    expect(warnings[0]).toContain(ackOf(ackSubject as string));
                }
            }
        );

        test("with AMI builds off, neither stack carries an unrelaxed-rule warning", () => {
            for (const stack of aclStacksOf(build("commercial", "cloudFront", false, policy))) {
                expect(unrelaxedWarningsOf(stack)).toEqual([]);
            }
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
            baselineAclFor(built.config, regional, shippedPolicy).Properties
        );
    });
});
