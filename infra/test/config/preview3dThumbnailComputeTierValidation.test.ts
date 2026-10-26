/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` rules for the 3D preview thumbnail compute tiers.
 *
 * Each tier's vCPU and memory become the resource overrides of its Batch state, and AWS Batch checks an
 * override against the Fargate task sizes only when a job is submitted, so an unsupported pair deploys
 * and fails every run routed to that tier. The defaults fill a block that names no tiers, and the
 * ConfigBuilder mirrors the same rules (`validation.ts`), which this suite also exercises so the two
 * cannot drift apart unnoticed.
 *
 * Every rejection asserts on the MESSAGE: enabling the pipeline can trip other rules (the VPC one),
 * and "it throws" would pass on the wrong error.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { RULES } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** The commercial template with the thumbnail pipeline enabled and `mutate` applied. */
function thumbnailConfig(mutate: (thumbnail: any) => void): any {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    config.app.useGlobalVpc.enabled = true;
    config.app.pipelines.usePreview3dThumbnail.enabled = true;
    mutate(config.app.pipelines.usePreview3dThumbnail);
    return config;
}

/** getConfig() over `config` as the deployment's config.json. */
function resolve(config: any): () => Config.Config {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

/** Ids of the ConfigBuilder thumbnail compute-tier rules `config` violates. */
function builderViolations(config: any): string[] {
    return RULES.filter((rule) => rule.id.startsWith("preview3dthumbnail-"))
        .filter((rule) => rule.appliesWhen(config))
        .map((rule) => rule.id);
}

afterEach(() => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("3D preview thumbnail compute tier defaults", () => {
    test("the shipped tiers validate and match the getConfig() defaults", () => {
        const config = thumbnailConfig(() => undefined);
        const thumbnail = resolve(config)().app.pipelines.usePreview3dThumbnail;
        expect(thumbnail.computeTiers).toEqual(Config.PREVIEW_3D_THUMBNAIL_DEFAULT_COMPUTE_TIERS);
        expect(thumbnail.largeTierThresholdGiB).toBe(
            Config.PREVIEW_3D_THUMBNAIL_DEFAULT_LARGE_TIER_THRESHOLD_GIB
        );
        expect(builderViolations(config)).toEqual([]);
    });

    test("a block that names no tiers gets the defaults", () => {
        const config = thumbnailConfig((t) => {
            delete t.computeTiers;
            delete t.largeTierThresholdGiB;
        });
        const thumbnail = resolve(config)().app.pipelines.usePreview3dThumbnail;
        expect(thumbnail.computeTiers).toEqual({
            standard: { vcpu: 4, memoryGiB: 16 },
            large: { vcpu: 16, memoryGiB: 120 },
        });
        expect(thumbnail.largeTierThresholdGiB).toBe(2);
        expect(builderViolations(config)).toEqual([]);
    });

    test("a tier that names one field keeps the default for the other", () => {
        const config = thumbnailConfig((t) => {
            t.computeTiers = { large: { memoryGiB: 64 } };
        });
        const thumbnail = resolve(config)().app.pipelines.usePreview3dThumbnail;
        expect(thumbnail.computeTiers.standard).toEqual({ vcpu: 4, memoryGiB: 16 });
        expect(thumbnail.computeTiers.large).toEqual({ vcpu: 16, memoryGiB: 64 });
    });

    test("an absent pipeline block gets the defaults", () => {
        const config = thumbnailConfig(() => undefined);
        delete config.app.pipelines.usePreview3dThumbnail;
        const thumbnail = resolve(config)().app.pipelines.usePreview3dThumbnail;
        expect(thumbnail.enabled).toBe(false);
        expect(thumbnail.computeTiers).toEqual(Config.PREVIEW_3D_THUMBNAIL_DEFAULT_COMPUTE_TIERS);
    });
});

describe("3D preview thumbnail compute tier validation", () => {
    test.each([
        ["standard", 0.25, 2],
        ["standard", 1, 8],
        ["standard", 4, 30],
        ["large", 8, 16],
        ["large", 8, 60],
        ["large", 16, 32],
        ["large", 16, 72],
    ])("%s at %s vCPU / %s GiB is accepted", (tier, vcpu, memoryGiB) => {
        const config = thumbnailConfig((t) => {
            t.computeTiers[tier] = { vcpu, memoryGiB };
        });
        expect(resolve(config)).not.toThrow();
        expect(builderViolations(config)).toEqual([]);
    });

    test.each([
        ["standard", 4, 31, /With 4 vCPU, memoryGiB must be one of 8, 9, .*, 30\./],
        ["standard", 4, 7, /With 4 vCPU/],
        ["large", 16, 64.5, /With 16 vCPU, memoryGiB must be one of 32, 40, .*, 120\./],
        ["large", 16, 124, /With 16 vCPU/],
        ["large", 8, 20.5, /With 8 vCPU/],
        ["large", 8, 18, /With 8 vCPU, memoryGiB must be one of 16, 20, 24/],
        ["large", 32, 120, /vcpu must be one of 0\.25, 0\.5, 1, 2, 4, 8, 16\./],
        ["standard", 3, 12, /vcpu must be one of/],
        ["standard", "4", 16, /vcpu must be one of/],
    ])("%s at %s vCPU / %s GiB is rejected", (tier, vcpu, memoryGiB, message) => {
        const config = thumbnailConfig((t) => {
            t.computeTiers[tier] = { vcpu, memoryGiB };
        });
        expect(resolve(config)).toThrow(
            new RegExp(`computeTiers\\.${tier} .*is not an AWS Fargate task size`)
        );
        expect(resolve(config)).toThrow(message);
        expect(builderViolations(config)).toEqual([`preview3dthumbnail-${tier}-tier-fargate-size`]);
    });

    test.each([0, -1, "2", false])("largeTierThresholdGiB %p is rejected", (threshold) => {
        const config = thumbnailConfig((t) => {
            t.largeTierThresholdGiB = threshold;
        });
        expect(resolve(config)).toThrow(/largeTierThresholdGiB must be a positive number of GiB/);
        expect(builderViolations(config)).toEqual(["preview3dthumbnail-large-tier-threshold"]);
    });

    test("a null threshold takes the default, as an absent one does", () => {
        const config = thumbnailConfig((t) => {
            t.largeTierThresholdGiB = null;
        });
        expect(resolve(config)().app.pipelines.usePreview3dThumbnail.largeTierThresholdGiB).toBe(2);
        expect(builderViolations(config)).toEqual([]);
    });

    test("a fractional threshold is accepted", () => {
        const config = thumbnailConfig((t) => {
            t.largeTierThresholdGiB = 0.5;
        });
        expect(resolve(config)).not.toThrow();
        expect(builderViolations(config)).toEqual([]);
    });

    test("an invalid tier on a disabled pipeline does not block the deployment", () => {
        const config = thumbnailConfig((t) => {
            t.enabled = false;
            t.computeTiers.large = { vcpu: 16, memoryGiB: 16 };
        });
        expect(resolve(config)).not.toThrow();
        expect(builderViolations(config)).toEqual([]);
    });
});

describe("the Fargate task size table", () => {
    test("matches the published Linux vCPU/memory combinations", () => {
        const gib = (vcpu: number) =>
            (Config.FARGATE_MEMORY_MIB_BY_VCPU.get(vcpu) ?? []).map((mib) => mib / 1024);
        expect(Array.from(Config.FARGATE_MEMORY_MIB_BY_VCPU.keys())).toEqual([
            0.25, 0.5, 1, 2, 4, 8, 16,
        ]);
        expect(gib(0.25)).toEqual([0.5, 1, 2]);
        expect(gib(0.5)).toEqual([1, 2, 3, 4]);
        expect(gib(1)).toEqual([2, 3, 4, 5, 6, 7, 8]);
        expect(gib(2)).toEqual([4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16]);
        expect(gib(4)).toEqual(Array.from({ length: 23 }, (_, i) => 8 + i));
        expect(gib(8)).toEqual([16, 20, 24, 28, 32, 36, 40, 44, 48, 52, 56, 60]);
        expect(gib(16)).toEqual([32, 40, 48, 56, 64, 72, 80, 88, 96, 104, 112, 120]);
    });

    test("fargateMemoryMiB converts GiB to the MiB a resource requirement carries", () => {
        expect(Config.fargateMemoryMiB(16)).toBe(16384);
        expect(Config.fargateMemoryMiB(0.5)).toBe(512);
    });
});
