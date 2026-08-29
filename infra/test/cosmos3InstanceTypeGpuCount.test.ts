/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `useNvidiaCosmos3` instance types must be able to hold the GPUs their tier's jobs reserve.
 *
 * Why this needs a guard at all: the failure it prevents is SILENT. A Cosmos 3 job definition reserves
 * a fixed number of GPUs (`Config.COSMOS3_NANO_GPU_COUNT` / `COSMOS3_SUPER_GPU_COUNT`), and AWS Batch
 * accepts a compute environment whose instance types cannot satisfy that — it simply never places the
 * job, which sits RUNNABLE indefinitely with no error on the execution, in CloudWatch, or on the
 * queue. Nothing downstream reports it, so `getConfig()` is the only place it can surface.
 *
 * The GPU count is not derivable from an instance type's size: g6e.16xlarge carries one GPU while the
 * nominally smaller g6e.12xlarge carries four. The table in config.ts is therefore explicit, and this
 * suite pins both halves of how an entry outside it is treated — a known-too-small type is an error,
 * an unknown type is a warning — because reversing either would be a behaviour change worth making
 * deliberately. Rejecting an unknown type would block a newly released accelerated family on a stale
 * table; accepting a known-too-small one restores the silent hang.
 *
 * `getConfig()` reads `config/config.json` from disk, so these tests mock `fs.readFileSync` to serve a
 * chosen template. Only the config filename is intercepted. The pattern — including the warning
 * against `jest.resetModules()` — is the one `configPartitionValidation.test.ts` documents.
 *
 * The govcloud template is the base because Cosmos 3 requires the global VPC, and govcloud is the
 * shipped template that already has it on. The rule itself is partition-independent.
 */

import * as fs from "fs";
import * as Config from "../config/config";
import govcloudTemplate from "../config/config.template.govcloud.json";
import { newTestApp } from "./support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const serveConfig = (configJson: unknown) => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (path: string, ...rest: unknown[]) => {
            if (typeof path === "string" && path.endsWith("config.json")) {
                return JSON.stringify(configJson);
            }
            return realReadFileSync(path, ...rest);
        }
    );
};

// config.ts is imported once at module scope and getConfig() re-reads config.json on every call, so no
// jest.resetModules() is needed — and it must NOT be used. Resetting the registry re-runs the
// jest.mock("fs") factory, producing a SECOND mock instance: the freshly required config.ts binds to it
// while serveConfig() keeps configuring the original, so getConfig() silently reads the real on-disk
// config.json and every "does not throw" assertion passes vacuously.
const loadConfig = (mutate?: (c: any) => void) => {
    const config = JSON.parse(JSON.stringify(govcloudTemplate));
    config.env.region = "us-gov-west-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    }
    mutate?.(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

/** Cosmos 3 ships disabled, so every case switches the Nano tier on and supplies a token. */
const withNanoInstanceTypes = (instanceTypes: string[]) =>
    loadConfig((c) => {
        const c3 = c.app.pipelines.useNvidiaCosmos3;
        c3.enabled = true;
        c3.huggingFaceToken = "hf_testtoken";
        c3.modelsOmni.nano16B.enabled = true;
        c3.modelsOmni.nano16B.instanceTypes = instanceTypes;
    });

const withSuperInstanceTypes = (model: string, instanceTypes: string[]) =>
    loadConfig((c) => {
        const c3 = c.app.pipelines.useNvidiaCosmos3;
        c3.enabled = true;
        c3.huggingFaceToken = "hf_testtoken";
        c3.modelsOmni[model].enabled = true;
        c3.modelsOmni[model].instanceTypes = instanceTypes;
    });

const GPU_MESSAGE = /which has \d+ GPU\(s\)/;

describe("Cosmos 3 instance-type GPU capacity validation", () => {
    let warnSpy: jest.SpyInstance;

    beforeEach(() => {
        warnSpy = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    });

    afterEach(() => {
        warnSpy.mockRestore();
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    describe("the reservation the rule is derived from", () => {
        test("Nano reserves 4 GPUs and Super reserves 8", () => {
            // The constants the construct passes to createModelResources. If a tier's reservation
            // changes, this suite's expectations change with it rather than silently drifting — and the
            // instance types shipped in the templates have to be revisited.
            expect(Config.COSMOS3_NANO_GPU_COUNT).toBe(4);
            expect(Config.COSMOS3_SUPER_GPU_COUNT).toBe(8);
        });

        test("the GPU table records the non-monotonic g6e sizes that motivate it", () => {
            // The single fact that makes a name-derived rule wrong.
            expect(Config.GPU_COUNT_BY_INSTANCE_TYPE["g6e.12xlarge"]).toBe(4);
            expect(Config.GPU_COUNT_BY_INSTANCE_TYPE["g6e.16xlarge"]).toBe(1);
            expect(Config.GPU_COUNT_BY_INSTANCE_TYPE["g6e.16xlarge"]).toBeLessThan(
                Config.GPU_COUNT_BY_INSTANCE_TYPE["g6e.12xlarge"]
            );
        });
    });

    describe("Nano tier", () => {
        test("the shipped instance types are accepted and survive getConfig() unchanged", () => {
            // Two claims: the rule does not reject what the templates ship, and getConfig() does not
            // rewrite the list before the construct reads it. Without the second half the rule could
            // pass here while the resolved config carried something else.
            const shipped = govcloudTemplate.app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B
                .instanceTypes as string[];
            const run = withNanoInstanceTypes(shipped);
            expect(run).not.toThrow(GPU_MESSAGE);
            expect(run().app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.instanceTypes).toEqual(
                shipped
            );
        });

        test("every shipped instance type carries at least the reserved GPU count", () => {
            // The assertion that would have caught the defect at source: the templates shipped
            // g6e.4xlarge, which has one GPU, for a tier that now reserves four.
            const shipped = govcloudTemplate.app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B
                .instanceTypes as string[];
            expect(shipped.length).toBeGreaterThan(0);
            for (const instanceType of shipped) {
                expect(Config.GPU_COUNT_BY_INSTANCE_TYPE[instanceType]).toBeGreaterThanOrEqual(
                    Config.COSMOS3_NANO_GPU_COUNT
                );
            }
        });

        test("a single-GPU instance type is rejected", () => {
            // g6e.4xlarge is the exact value the templates used to ship, and the one that made every
            // Nano job unplaceable once the tier began reserving four GPUs.
            expect(withNanoInstanceTypes(["g6e.4xlarge"])).toThrow(
                /nano16B\.instanceTypes includes g6e\.4xlarge, which has 1 GPU\(s\)/
            );
        });

        test("one bad entry in an otherwise valid list is still rejected", () => {
            // Batch would place jobs on the valid types and simply never use the invalid one, so the
            // configuration looks like it works until capacity for the good types runs out.
            expect(withNanoInstanceTypes(["g6e.12xlarge", "g6e.4xlarge"])).toThrow(GPU_MESSAGE);
        });

        test("a 4-GPU instance type from another family is accepted", () => {
            // The rule is about GPU count, not about the g6e family.
            expect(withNanoInstanceTypes(["g5.12xlarge"])).not.toThrow(GPU_MESSAGE);
        });

        test("an unknown instance type is warned about, not rejected", () => {
            const run = withNanoInstanceTypes(["g9e.12xlarge"]);
            expect(run).not.toThrow(GPU_MESSAGE);
            run();
            expect(warnSpy).toHaveBeenCalledWith(
                expect.stringContaining("g9e.12xlarge, whose GPU count VAMS cannot verify")
            );
        });

        test("a valid list produces no warning", () => {
            // The positive control for the case above: without it, a warning assertion could pass
            // because every configuration warns.
            withNanoInstanceTypes(["g6e.12xlarge"])();
            const gpuWarnings = warnSpy.mock.calls.filter((call) =>
                String(call[0]).includes("GPU count")
            );
            expect(gpuWarnings).toEqual([]);
        });

        test("the rule does not fire when the Nano tier is disabled", () => {
            // A tier that is off creates no job definition and no compute environment, so its instance
            // types cannot strand anything.
            const run = loadConfig((c) => {
                const c3 = c.app.pipelines.useNvidiaCosmos3;
                c3.enabled = true;
                c3.huggingFaceToken = "hf_testtoken";
                c3.modelsOmni.nano16B.enabled = false;
                c3.modelsOmni.nano16B.instanceTypes = ["g6e.4xlarge"];
                c3.modelsOmni.super64B.enabled = true;
            });
            expect(run).not.toThrow(GPU_MESSAGE);
        });
    });

    describe("Super tiers", () => {
        test.each(["super64B", "superText2Image64B", "superImage2Video64B"])(
            "%s rejects an instance type below the 8-GPU reservation",
            (model) => {
                // g6e.12xlarge is valid for Nano and invalid here, which is what makes the per-tier
                // threshold load-bearing rather than a single global minimum.
                expect(withSuperInstanceTypes(model, ["g6e.12xlarge"])).toThrow(
                    /which has 4 GPU\(s\)/
                );
            }
        );

        test.each(["super64B", "superText2Image64B", "superImage2Video64B"])(
            "%s accepts its shipped instance types",
            (model) => {
                const shipped = (govcloudTemplate.app.pipelines.useNvidiaCosmos3.modelsOmni as any)[
                    model
                ].instanceTypes as string[];
                expect(withSuperInstanceTypes(model, shipped)).not.toThrow(GPU_MESSAGE);
                for (const instanceType of shipped) {
                    expect(Config.GPU_COUNT_BY_INSTANCE_TYPE[instanceType]).toBeGreaterThanOrEqual(
                        Config.COSMOS3_SUPER_GPU_COUNT
                    );
                }
            }
        );
    });
});
