/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes` must not default to a GPU too small for the
 * default fine-tune, and naming one must be reported (issue #1836).
 *
 * A fine-tune job reserves one GPU. The default fine-tune template (loraRank 0, projector and diffusion
 * model tuned, AdamW) ran out of memory at step 0 on the 22.06 GiB A10G of a g5.12xlarge at batch sizes
 * 16, 4 and 2, and ran on the 48 GB L40S of a g6e.4xlarge. The compute environment is
 * BEST_FIT_PROGRESSIVE, so AWS Batch can place a job on any listed type and prefers the type with the
 * lowest cost per vCPU, which can be the small-GPU one (g5.12xlarge costs less per vCPU than
 * g6e.4xlarge). Each job placed there fails after its instance start and model sync.
 *
 * The check is a warning, not an error: a LoRA run or a smaller set of tuned components may fit a
 * 24 GB GPU, and an existing configuration that names one must keep deploying.
 *
 * `getConfig()` reads `config/config.json` from disk, so these tests mock `fs.readFileSync` to serve a
 * chosen template, as `cosmos3InstanceTypeGpuCount.test.ts` does. Do not call `jest.resetModules()`:
 * it re-runs the `jest.mock("fs")` factory and getConfig() would read the real config.json.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const DEFAULT_INSTANCE_TYPES = ["g6e.4xlarge", "g6e.12xlarge"];
const WARNING =
    /Configuration Warning: useNvidiaGr00t\.modelsFinetune\.gr00tN1_5_3B\.instanceTypes/;
const RULE_ID = "gr00t-instancetypes-gpu-memory";
const CONFIG_DIR = path.join(__dirname, "../../config");
const SHIPPED_CONFIG_FILES = [
    "config.json",
    "config.template.commercial.json",
    "config.template.govcloud.json",
    "config.template.eusovereign.json",
];

function resolve(mutate: (c: any) => void): () => Config.Config {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    mutate(config);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

/** GR00T ships disabled; every enabled case also needs the global VPC and a token. */
function withGr00tInstanceTypes(instanceTypes: unknown) {
    return resolve((c) => {
        c.app.useGlobalVpc.enabled = true;
        const gr00t = c.app.pipelines.useNvidiaGr00t;
        gr00t.enabled = true;
        gr00t.huggingFaceToken = "hf_testtoken";
        gr00t.modelsFinetune.gr00tN1_5_3B.enabled = true;
        gr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes = instanceTypes;
    });
}

function gr00tWarnings(warnSpy: jest.SpyInstance): string[] {
    return warnSpy.mock.calls.map((args) => String(args[0])).filter((m) => WARNING.test(m));
}

function builderConfig(
    profile: "commercial" | "govcloud" | "eusovereign",
    instanceTypes?: string[]
) {
    const cfg: any = makeDefaultConfig(profile);
    const gr00t = cfg.app.pipelines.useNvidiaGr00t;
    gr00t.enabled = true;
    gr00t.huggingFaceToken = "hf_testtoken";
    gr00t.modelsFinetune.gr00tN1_5_3B.enabled = true;
    if (instanceTypes) gr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes = instanceTypes;
    return cfg;
}

const builderRuleFires = (cfg: any) => evaluateRules(cfg).some((rule) => rule.id === RULE_ID);

describe("GR00T fine-tune instance types and GPU memory", () => {
    let warnSpy: jest.SpyInstance;

    beforeEach(() => {
        warnSpy = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    });

    afterEach(() => {
        warnSpy.mockRestore();
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    describe("defaults", () => {
        test("getConfig() defaults the list to the two g6e types when the block is absent", () => {
            const config = resolve((c) => {
                delete c.app.pipelines.useNvidiaGr00t;
            })();
            expect(
                config.app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes
            ).toEqual(DEFAULT_INSTANCE_TYPES);
        });

        test.each(SHIPPED_CONFIG_FILES)("%s ships the two g6e types", (file) => {
            const shipped = JSON.parse(realReadFileSync(path.join(CONFIG_DIR, file), "utf8"));
            expect(
                shipped.app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes
            ).toEqual(DEFAULT_INSTANCE_TYPES);
        });

        test.each(["commercial", "govcloud", "eusovereign"] as const)(
            "the ConfigBuilder %s preset ships the two g6e types",
            (profile) => {
                const cfg: any = makeDefaultConfig(profile);
                expect(
                    cfg.app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes
                ).toEqual(DEFAULT_INSTANCE_TYPES);
            }
        );

        test("no default type is in a family the warning names", () => {
            for (const instanceType of DEFAULT_INSTANCE_TYPES) {
                expect(
                    Config.GR00T_FINETUNE_UNDERSIZED_GPU_FAMILIES[instanceType.split(".")[0]]
                ).toBeUndefined();
            }
        });
    });

    describe("getConfig() validation", () => {
        test("enabling GR00T with the default list deploys without the warning", () => {
            expect(withGr00tInstanceTypes(DEFAULT_INSTANCE_TYPES)).not.toThrow();
            expect(gr00tWarnings(warnSpy)).toEqual([]);
        });

        test("a list that keeps g5.12xlarge deploys and warns, naming the type and its GPU", () => {
            expect(
                withGr00tInstanceTypes(["g6e.4xlarge", "g6e.12xlarge", "g5.12xlarge"])
            ).not.toThrow();
            const warnings = gr00tWarnings(warnSpy);
            expect(warnings).toHaveLength(1);
            expect(warnings[0]).toContain("g5.12xlarge (NVIDIA A10G, 24 GB)");
            // Only the small-GPU entry is named (the message's own example is g6e.4xlarge).
            expect(warnings[0]).not.toContain("g6e.12xlarge");
            expect(warnings[0]).toMatch(/runs out of GPU memory at step 0/);
        });

        test.each([
            ["g4dn.12xlarge", "NVIDIA T4, 16 GB"],
            ["g6.xlarge", "NVIDIA L4, 24 GB"],
            ["gr6.4xlarge", "NVIDIA L4, 24 GB"],
            ["p3.2xlarge", "NVIDIA V100, 16 GB"],
        ])("%s is warned about as %s", (instanceType, gpu) => {
            expect(withGr00tInstanceTypes([instanceType])).not.toThrow();
            expect(gr00tWarnings(warnSpy).join("\n")).toContain(`${instanceType} (${gpu})`);
        });

        test("a string instead of a list gets the configured error, not a TypeError", () => {
            // The ConfigBuilder treats a non-array the same way (modelInstanceTypesEmpty).
            expect(withGr00tInstanceTypes("g5.12xlarge")).toThrow(
                /Configuration Error: useNvidiaGr00t\.modelsFinetune\.gr00tN1_5_3B\.instanceTypes must be a non-empty array/
            );
        });

        test("a type outside the table (g6e.48xlarge, p4d.24xlarge) is not warned about", () => {
            expect(withGr00tInstanceTypes(["g6e.48xlarge", "p4d.24xlarge"])).not.toThrow();
            expect(gr00tWarnings(warnSpy)).toEqual([]);
        });

        test("a disabled GR00T pipeline is not checked", () => {
            expect(
                resolve((c) => {
                    c.app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.instanceTypes = [
                        "g5.12xlarge",
                    ];
                })
            ).not.toThrow();
            expect(gr00tWarnings(warnSpy)).toEqual([]);
        });
    });

    describe("ConfigBuilder mirror", () => {
        test.each(["commercial", "govcloud", "eusovereign"] as const)(
            "the %s preset with GR00T enabled raises no GPU memory warning",
            (profile) => {
                expect(builderRuleFires(builderConfig(profile))).toBe(false);
            }
        );

        test("a list with g5.12xlarge raises the warning, as a warning", () => {
            const rules = evaluateRules(
                builderConfig("commercial", ["g6e.4xlarge", "g5.12xlarge"])
            );
            const rule = rules.find((r) => r.id === RULE_ID);
            expect(rule).toBeDefined();
            expect(rule!.severity).toBe("warning");
        });

        test("it fires for exactly the instance types getConfig() warns about", () => {
            // Every accelerated type VAMS knows, so the two family tables cannot drift apart.
            for (const instanceType of Object.keys(Config.GPU_COUNT_BY_INSTANCE_TYPE)) {
                const expected =
                    Config.GR00T_FINETUNE_UNDERSIZED_GPU_FAMILIES[instanceType.split(".")[0]] !==
                    undefined;
                expect([
                    instanceType,
                    builderRuleFires(builderConfig("commercial", [instanceType])),
                ]).toEqual([instanceType, expected]);
            }
        });
    });
});
