/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A WAF policy file that would produce a Web ACL protecting nothing must fail the synthesis.
 *
 * The failure mode this guards is silent in a specific and dangerous way. `getConfig()` used to wrap
 * both the file read AND `JSON.parse` in one `try` whose comment only contemplated a missing file, so a
 * trailing comma in an edited policy was indistinguishable from no policy at all. The fallback is
 * `legacyDefaultRules` in `wafv2-basic-construct.ts` — a single Common Rule Set with
 * `overrideAction: count` — which blocks nothing. `cdk synth` and `cdk deploy` both exited 0, the
 * deployment still reported WAF as enabled, and the only visible symptom was a change in Amazon
 * CloudWatch WAF metrics.
 *
 * So the assertions here are about which inputs are DISTINGUISHED, not merely about which throw:
 *
 *   - absent file      → warns and falls back (a legitimate configuration)
 *   - unparseable file → throws (previously indistinguishable from absent)
 *   - empty file       → warns and falls back
 *   - no rules         → throws (deploys cleanly and inspects nothing)
 *   - duplicate/missing priority, missing vendorName → throws (AWS WAF rejects these mid-deploy,
 *     rolling back the whole core stack, so a synth-time message is strictly cheaper)
 *   - every group block:false → warns, does not throw (count-only is how a new rule group is
 *     evaluated before enforcing it)
 *
 * The shipped policy is asserted to pass, which is the positive control: without it every "throws"
 * case would be satisfied by a validator that rejects everything.
 *
 * `getConfig()` reads both `config.json` and `policy/wafPolicyConfig.json` from disk, so both reads are
 * intercepted. The `jest.resetModules()` warning from `configPartitionValidation.test.ts` applies here
 * too and is repeated in the comment at the mock.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import shippedWafPolicy from "../../config/policy/wafPolicyConfig.json";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** Sentinel for "this file does not exist", so a test can ask for the ENOENT path explicitly. */
const ABSENT = Symbol("absent");
/** Sentinel for "this file exists but cannot be read", which must NOT be treated as absent. */
const UNREADABLE = Symbol("unreadable");

function serve(configJson: unknown, wafPolicy: unknown) {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (path: string, ...rest: unknown[]) => {
            if (typeof path === "string" && path.endsWith("wafPolicyConfig.json")) {
                if (wafPolicy === ABSENT) {
                    const err: any = new Error("ENOENT: no such file or directory");
                    err.code = "ENOENT";
                    throw err;
                }
                if (wafPolicy === UNREADABLE) {
                    const err: any = new Error("EACCES: permission denied");
                    err.code = "EACCES";
                    throw err;
                }
                return typeof wafPolicy === "string" ? wafPolicy : JSON.stringify(wafPolicy);
            }
            if (typeof path === "string" && path.endsWith("config.json")) {
                return JSON.stringify(configJson);
            }
            return realReadFileSync(path, ...rest);
        }
    );
}

// config.ts is imported once at module scope and getConfig() re-reads its files on every call, so no
// jest.resetModules() is needed — and it must NOT be used. Resetting the registry re-runs the
// jest.mock("fs") factory, producing a SECOND mock instance: the freshly required config.ts binds to it
// while serve() keeps configuring the original, so getConfig() silently reads the real on-disk files and
// every "does not throw" assertion passes vacuously.
function withWafPolicy(wafPolicy: unknown) {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-west-2";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    config.app.useWaf = true;
    serve(config, wafPolicy);
    return () => Config.getConfig(newTestApp());
}

describe("wafPolicyConfig.json validation", () => {
    let warnSpy: jest.SpyInstance;

    beforeEach(() => {
        warnSpy = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    });

    afterEach(() => {
        warnSpy.mockRestore();
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    const gpuWarnings = () =>
        warnSpy.mock.calls
            .map((c) => String(c[0]))
            .filter((m) => m.includes("wafPolicyConfig.json"));

    describe("the positive control", () => {
        test("the shipped policy is accepted and reaches the construct intact", () => {
            const run = withWafPolicy(shippedWafPolicy);
            expect(run).not.toThrow();
            // Not just "did not throw": the parsed policy must actually be handed on, or the Web ACL
            // would fall back to count-only rules while this suite reported success.
            const resolved = run();
            expect(resolved.wafPolicyJSON.managedRuleGroups).toHaveLength(
                (shippedWafPolicy as any).managedRuleGroups.length
            );
            expect(resolved.wafPolicyJSON.rateBasedRules).toHaveLength(
                (shippedWafPolicy as any).rateBasedRules.length
            );
            expect(gpuWarnings()).toEqual([]);
        });
    });

    describe("inputs that must be distinguished from each other", () => {
        test("an ABSENT file warns and falls back rather than throwing", () => {
            const run = withWafPolicy(ABSENT);
            expect(run).not.toThrow();
            expect(run().wafPolicyJSON).toBeUndefined();
            expect(gpuWarnings().join(" ")).toMatch(/is absent.*BLOCKS NOTHING/s);
        });

        test("an UNPARSEABLE file throws instead of reading as absent", () => {
            // The defect: a trailing comma took the same code path as a missing file.
            expect(withWafPolicy('{ "managedRuleGroups": [], }')).toThrow(/is not valid JSON/);
        });

        test("a file that exists but cannot be READ throws instead of reading as absent", () => {
            // ENOENT is the only error the fallback is for. EACCES means a policy exists and is being
            // ignored, which is the silent case.
            expect(withWafPolicy(UNREADABLE)).toThrow(/could not be read/);
        });

        test("an EMPTY file warns and falls back", () => {
            const run = withWafPolicy("   \n");
            expect(run).not.toThrow();
            expect(run().wafPolicyJSON).toBeUndefined();
            expect(gpuWarnings().join(" ")).toMatch(/is empty.*BLOCKS NOTHING/s);
        });
    });

    describe("structural errors AWS WAF would otherwise reject mid-deploy", () => {
        test("a policy with no rules at all throws", () => {
            expect(withWafPolicy({ managedRuleGroups: [], rateBasedRules: [] })).toThrow(
                /declares no rules/
            );
        });

        test("a non-object policy throws", () => {
            expect(withWafPolicy([1, 2, 3])).toThrow(/must contain a JSON object/);
        });

        test("managedRuleGroups that is not an array throws", () => {
            expect(withWafPolicy({ managedRuleGroups: {} })).toThrow(/must be arrays/);
        });

        test("a managed rule group missing vendorName throws and names the group", () => {
            expect(
                withWafPolicy({
                    managedRuleGroups: [
                        {
                            name: "G1",
                            managedRuleGroupName: "AWSManagedRulesCommonRuleSet",
                            priority: 1,
                        },
                    ],
                })
            ).toThrow(/managed rule group 'G1' is missing 'vendorName'/);
        });

        test("two rules sharing a priority throw and name both", () => {
            expect(
                withWafPolicy({
                    managedRuleGroups: [
                        { name: "G1", vendorName: "AWS", managedRuleGroupName: "A", priority: 1 },
                        { name: "G2", vendorName: "AWS", managedRuleGroupName: "B", priority: 1 },
                    ],
                })
            ).toThrow(/'G1' and 'G2' both use priority 1/);
        });

        test("a rate rule colliding with a managed group's priority throws", () => {
            // The priority space is shared across both rule kinds, which is easy to miss when editing.
            expect(
                withWafPolicy({
                    managedRuleGroups: [
                        { name: "G1", vendorName: "AWS", managedRuleGroupName: "A", priority: 10 },
                    ],
                    rateBasedRules: [{ name: "R1", priority: 10, limit: 10000 }],
                })
            ).toThrow(/both use priority 10/);
        });

        test("a rate rule with a non-positive limit throws", () => {
            expect(
                withWafPolicy({ rateBasedRules: [{ name: "R1", priority: 10, limit: 0 }] })
            ).toThrow(/needs a positive integer 'limit'/);
        });

        test("an unknown ruleActionOverride action throws", () => {
            expect(
                withWafPolicy({
                    managedRuleGroups: [
                        {
                            name: "G1",
                            vendorName: "AWS",
                            managedRuleGroupName: "A",
                            priority: 1,
                            ruleActionOverrides: [
                                { name: "SizeRestrictions_BODY", action: "counted" },
                            ],
                        },
                    ],
                })
            ).toThrow(/action 'counted'; expected count, block, or allow/);
        });

        test("the shipped policy's own overrides are accepted", () => {
            // Positive control for the override checks: the real file uses action "count".
            expect(withWafPolicy(shippedWafPolicy)).not.toThrow();
        });
    });

    describe("non-blocking but valid", () => {
        test("every group set to block:false warns and does NOT throw", () => {
            // Count-only is how a new managed rule group is evaluated against real traffic before it is
            // enforced, so rejecting it would break a legitimate workflow.
            const run = withWafPolicy({
                managedRuleGroups: [
                    {
                        name: "G1",
                        vendorName: "AWS",
                        managedRuleGroupName: "AWSManagedRulesCommonRuleSet",
                        priority: 1,
                        block: false,
                    },
                ],
            });
            expect(run).not.toThrow();
            run();
            expect(gpuWarnings().join(" ")).toMatch(/counts matches instead of blocking/);
        });

        test("a policy with only rate rules and no managed groups does not warn about blocking", () => {
            // The block warning must key on "groups exist and none block", not on "no groups".
            const run = withWafPolicy({
                rateBasedRules: [{ name: "R1", priority: 10, limit: 500 }],
            });
            expect(run).not.toThrow();
            run();
            expect(gpuWarnings().join(" ")).not.toMatch(/counts matches instead of blocking/);
        });
    });

    describe("the guard is scoped to app.useWaf", () => {
        test("a broken policy file is ignored entirely when useWaf is false", () => {
            // Backwards compatibility: an operator who has WAF off must not be blocked from deploying
            // by a policy file they are not using.
            const config = JSON.parse(JSON.stringify(commercialTemplate));
            config.env.region = "us-west-2";
            config.env.account = "123456789012";
            config.app.baseStackName = "vamstest";
            config.app.useWaf = false;
            serve(config, '{ "broken": ,,, }');
            expect(() => Config.getConfig(newTestApp())).not.toThrow(/wafPolicyConfig/);
        });
    });
});
