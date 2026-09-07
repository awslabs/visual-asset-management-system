/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shared Super compute environment may only launch instances EVERY enabled variant permits.
 *
 * The three Cosmos 3 Super (64B) variants share one AWS Batch compute environment and one job queue.
 * Batch cannot tell which variant a job came from, so any instance type in that environment's pool can
 * receive any Super job. The pool was taken from the FIRST enabled variant, so the other variants'
 * `instanceTypes` — values `getConfig()` validates per variant — were silently discarded.
 *
 * The shipped defaults make the consequence concrete rather than theoretical: `super64B` allows
 * `p4de.24xlarge` (8x A100) and `superText2Image64B` does not, and the configuration reference states
 * different GPU requirements for the two. With both enabled, first-wins placed text2image jobs on the
 * A100 family its own documentation excludes.
 *
 * A **union** would not have fixed it — the union still contains `p4de.24xlarge`. Only the
 * **intersection** gives every variant's list its documented meaning, so that is what is asserted here,
 * along with the `getConfig()` rejection of an empty intersection.
 *
 * Asserted on the emitted `AWS::Batch::ComputeEnvironment`, not on the construct: the pool that decides
 * where a job lands is the one CloudFormation receives.
 */

import * as path from "path";
import * as fs from "fs";
import { SynthResult, synthTemplate } from "../support/templateSynth";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** Enable Cosmos 3 with the named Super variants on, leaving each variant's shipped instanceTypes. */
function cosmos3Super(variants: string[], overrides: Record<string, string[]> = {}) {
    return (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        const cosmos3 = c.app.pipelines.useNvidiaCosmos3;
        cosmos3.enabled = true;
        for (const name of ["nano16B", "super64B", "superText2Image64B", "superImage2Video64B"]) {
            if (!cosmos3.modelsOmni?.[name]) continue;
            cosmos3.modelsOmni[name].enabled = variants.includes(name);
            cosmos3.modelsOmni[name].autoRegisterWithVAMS = false;
            if (overrides[name]) {
                cosmos3.modelsOmni[name].instanceTypes = overrides[name];
            }
        }
    };
}

/** Every Batch compute environment, with the instance pool CloudFormation received. */
function pools(synth: SynthResult) {
    return synth.ofType("AWS::Batch::ComputeEnvironment").map((e) => ({
        logicalId: e.logicalId,
        instanceTypes: ((e.properties as any).ComputeResources?.InstanceTypes ?? []) as string[],
    }));
}

/** The pool of the environment built for the Super tier, identified by its logical id. */
function superPool(synth: SynthResult): string[] {
    const matches = pools(synth).filter((p) => /Super/.test(p.logicalId));
    expect(matches.length).toBe(1);
    return matches[0].instanceTypes;
}

describe("intersectInstanceTypes", () => {
    // Unit-level, because the construct and getConfig() both depend on this one function and its edge
    // cases (a single list, a disabled variant contributing undefined, no overlap) are cheaper to pin
    // here than through a synth.
    test("a single list is returned unchanged, preserving order", () => {
        expect(Config.intersectInstanceTypes([["p5.48xlarge", "p5e.48xlarge"]])).toEqual([
            "p5.48xlarge",
            "p5e.48xlarge",
        ]);
    });

    test("undefined and empty entries are ignored, not treated as an empty intersection", () => {
        // A disabled variant contributes undefined. Treating that as "permits nothing" would empty the
        // pool the moment any variant was switched off.
        expect(Config.intersectInstanceTypes([undefined, ["p5.48xlarge"], [], undefined])).toEqual([
            "p5.48xlarge",
        ]);
    });

    test("order follows the first list, which is Batch's preference order", () => {
        expect(
            Config.intersectInstanceTypes([
                ["p5e.48xlarge", "p5.48xlarge"],
                ["p5.48xlarge", "p5e.48xlarge"],
            ])
        ).toEqual(["p5e.48xlarge", "p5.48xlarge"]);
    });

    test("no common type yields an empty array rather than throwing", () => {
        // getConfig() owns the rejection so the error names the config fields; a throw in here would
        // surface as a stack trace from a helper the operator has never heard of.
        expect(Config.intersectInstanceTypes([["p5.48xlarge"], ["p4de.24xlarge"]])).toEqual([]);
    });
});

describe("Cosmos 3 shared Super compute environment", () => {
    test("[control] a Super compute environment IS emitted in this synth", () => {
        // Every assertion below is satisfied by a synth containing no Super environment, and Cosmos 3
        // ships disabled. This control is what makes the rest non-vacuous.
        const synth = synthTemplate("commercial", {
            mutate: cosmos3Super(["super64B"]),
            mutateKey: "cosmos3-super-only-super64b",
        });
        expect(pools(synth).filter((p) => /Super/.test(p.logicalId)).length).toBe(1);
    });

    test("one enabled variant gets exactly its own list", () => {
        // The positive control for the intersection: a fix that always narrowed the pool would break
        // the single-variant case, which is the common deployment.
        const synth = synthTemplate("commercial", {
            mutate: cosmos3Super(["super64B"]),
            mutateKey: "cosmos3-super-only-super64b",
        });
        expect(superPool(synth)).toEqual(["p5.48xlarge", "p5e.48xlarge", "p4de.24xlarge"]);
    });

    test("the shipped defaults intersect to the types both variants document", () => {
        // super64B allows p4de.24xlarge (A100); superText2Image64B documents 8x H100/H200 and omits it.
        // With both enabled the pool must exclude it — this is the defect, using only shipped values.
        const synth = synthTemplate("commercial", {
            mutate: cosmos3Super(["super64B", "superText2Image64B"]),
            mutateKey: "cosmos3-super-64b-and-text2image",
        });
        const pool = superPool(synth);
        expect(pool).not.toContain("p4de.24xlarge");
        expect(pool).toEqual(["p5.48xlarge", "p5e.48xlarge"]);
    });

    test("an operator narrowing one variant narrows the shared pool", () => {
        // The finding's scenario: text2image restricted to H200 only. Under first-wins the pool was
        // super64B's full list and the restriction was discarded silently.
        const synth = synthTemplate("commercial", {
            mutate: cosmos3Super(["super64B", "superText2Image64B"], {
                superText2Image64B: ["p5e.48xlarge"],
            }),
            mutateKey: "cosmos3-super-text2image-narrowed",
        });
        expect(superPool(synth)).toEqual(["p5e.48xlarge"]);
    });

    test("all three enabled still leaves a usable pool", () => {
        const synth = synthTemplate("commercial", {
            mutate: cosmos3Super(["super64B", "superText2Image64B", "superImage2Video64B"]),
            mutateKey: "cosmos3-super-all-three",
        });
        expect(superPool(synth)).toEqual(["p5.48xlarge", "p5e.48xlarge"]);
    });

    test("the construct takes the pool from the shared helper, not a first-wins chain", () => {
        // Guards the shape as well as the output: a future edit reintroducing a ternary would satisfy
        // the single-variant assertions above while silently restoring the defect for multi-variant
        // deployments, which no shipped config template exercises.
        const source = realReadFileSync(
            path.resolve(
                __dirname,
                "../../lib/nestedStacks/pipelines/genAi/nvidia/cosmos/constructs/cosmos3-construct.ts"
            ),
            "utf-8"
        );
        expect(source).toMatch(/instanceTypesSuper\s*=\s*Config\.intersectInstanceTypes\(/);
        expect(source).not.toMatch(/instanceTypesSuper\s*=\s*cosmosConfig\.modelsOmni\.super64B/);
    });
});

/**
 * The `getConfig()` rejection, exercised through `getConfig()` itself.
 *
 * It cannot be reached through `synthTemplate`: that harness builds a Config by hand from the raw
 * template and fills in the fields `getConfig()` would derive, so it never calls `getConfig()` and no
 * validation rule runs. Asserting a rejection there passes for a rule that does not exist.
 */
describe("getConfig: Super variants sharing one compute environment", () => {
    /** Builds a config.json from the commercial template, applies `mutate`, and calls getConfig(). */
    function resolve(mutate: (c: any) => void): () => Config.Config {
        const config = JSON.parse(JSON.stringify(commercialTemplate));
        config.env.region = "us-east-1";
        config.env.account = "123456789012";
        config.app.baseStackName = "vamstest";
        // Cosmos 3 requires a VPC, which is a separate rule — enabled so the failure under test is the
        // one that fires rather than the VPC one.
        config.app.useGlobalVpc.enabled = true;
        mutate(config);
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(
            (p: string, ...rest: unknown[]) => {
                if (typeof p === "string" && p.endsWith("config.json")) {
                    return JSON.stringify(config);
                }
                return realReadFileSync(p, ...rest);
            }
        );
        return () => Config.getConfig(newTestApp());
    }

    /** Enables Cosmos 3 with the named Super variants and the given instanceTypes. */
    function superConfig(overrides: Record<string, string[]>) {
        return (c: any) => {
            const cosmos3 = c.app.pipelines.useNvidiaCosmos3;
            cosmos3.enabled = true;
            // A separate earlier rule requires this when the pipeline is enabled. Set so the failure
            // under test is the one that fires — asserting only "it throws" would have passed here on
            // the huggingFaceToken rule instead, which is why every case below matches the MESSAGE.
            cosmos3.huggingFaceToken = "hf_synthOnlyTokenValueNotReal";
            for (const name of [
                "nano16B",
                "super64B",
                "superText2Image64B",
                "superImage2Video64B",
            ]) {
                if (!cosmos3.modelsOmni?.[name]) continue;
                cosmos3.modelsOmni[name].enabled = name in overrides;
                if (overrides[name]) {
                    cosmos3.modelsOmni[name].instanceTypes = overrides[name];
                }
            }
        };
    }

    test("[control] the commercial template passes getConfig() unchanged", () => {
        // Without this, every rejection below could be caused by the harness rather than the rule.
        expect(resolve(() => undefined)).not.toThrow();
    });

    test("disjoint instanceTypes across two enabled Super variants are rejected", () => {
        // Both are 8-GPU types, so the pre-existing GPU-count rule passes and this rule is what fires.
        // Left unrejected, the compute environment would be rendered with an empty instance pool: it can
        // launch nothing, and its jobs sit RUNNABLE indefinitely with no error reported anywhere.
        expect(
            resolve(
                superConfig({
                    super64B: ["p5.48xlarge"],
                    superText2Image64B: ["p4de.24xlarge"],
                })
            )
        ).toThrow(/no type in common/);
    });

    test("the rejection names both variants and their lists", () => {
        // The symptom gives no hint that two unrelated config blocks are what disagree, so the message
        // has to carry the diagnosis.
        expect(
            resolve(
                superConfig({
                    super64B: ["p5.48xlarge"],
                    superText2Image64B: ["p4de.24xlarge"],
                })
            )
        ).toThrow(/super64B=\[p5\.48xlarge\].*superText2Image64B=\[p4de\.24xlarge\]/);
    });

    test("overlapping lists are accepted", () => {
        // The positive control: a rule that rejected every multi-variant deployment would satisfy the
        // two tests above.
        expect(
            resolve(
                superConfig({
                    super64B: ["p5.48xlarge", "p4de.24xlarge"],
                    superText2Image64B: ["p5.48xlarge"],
                })
            )
        ).not.toThrow();
    });

    test("a single enabled variant is never rejected, whatever its list", () => {
        // With one variant there is nothing to disagree with, and the rule must not fire.
        expect(resolve(superConfig({ super64B: ["p4de.24xlarge"] }))).not.toThrow();
    });
});
