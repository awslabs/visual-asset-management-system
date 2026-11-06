/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` rules for app.zipDownloads (Download as Zip), and their ConfigBuilder mirror.
 *
 * An absent block resolves to the defaults (a deployment upgraded from a config.json without it), each
 * key is checked for type and bounds even while the feature is off, the zipArchive job size must be one
 * AWS Fargate runs (Batch checks it only when a job is submitted), and enabling the feature requires
 * the global VPC (the zipArchive job runs in the isolated pipeline subnets). Each rejection asserts on
 * the message, so a different rule tripping cannot pass the test.
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

function baseConfig(mutate: (config: any) => void = () => undefined): any {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    mutate(config);
    return config;
}

function resolve(config: any): () => Config.Config {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

function builderViolations(config: any): string[] {
    return RULES.filter(
        (rule) => rule.id.startsWith("zip-downloads-") || rule.id === "vpc-required-zip-downloads"
    )
        .filter((rule) => rule.appliesWhen(config))
        .map((rule) => rule.id);
}

afterEach(() => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("app.zipDownloads defaults", () => {
    test("the shipped block equals the defaults and validates", () => {
        const config = baseConfig();
        expect(resolve(config)().app.zipDownloads).toEqual(Config.ZIP_DOWNLOADS_DEFAULTS);
        expect(Config.ZIP_DOWNLOADS_DEFAULTS.maxTotalBytesPerRequest).toBe(5497558138880);
        expect(builderViolations(config)).toEqual([]);
    });

    test("an absent block is all defaults (an upgraded config.json)", () => {
        const config = baseConfig((c) => delete c.app.zipDownloads);
        expect(resolve(config)().app.zipDownloads).toEqual(Config.ZIP_DOWNLOADS_DEFAULTS);
        expect(builderViolations(config)).toEqual([]);
    });

    test("a partial block keeps its values and fills the rest", () => {
        const config = baseConfig((c) => {
            c.app.zipDownloads = { retentionDays: 3 };
        });
        expect(resolve(config)().app.zipDownloads).toEqual({
            ...Config.ZIP_DOWNLOADS_DEFAULTS,
            retentionDays: 3,
        });
    });
});

describe("app.zipDownloads validation (feature off: values are still checked)", () => {
    test.each([
        ["retentionDays", 0],
        ["retentionDays", 31],
        ["retentionDays", 2.5],
        ["maxFilesPerRequest", 0],
        ["maxFilesPerRequest", 1000001],
        ["maxTotalBytesPerRequest", 5497558138881],
        ["maxActiveRequestsPerUser", 101],
    ])("%s = %p is refused", (key, value) => {
        const config = baseConfig((c) => {
            c.app.zipDownloads[key] = value;
        });
        expect(resolve(config)).toThrow(
            new RegExp(`Configuration Error: app\\.zipDownloads\\.${key} must be a whole number`)
        );
        expect(builderViolations(config)).toContain(`zip-downloads-${key}-range`);
    });

    test("a non-boolean enabled is refused", () => {
        const config = baseConfig((c) => {
            c.app.zipDownloads.enabled = "yes";
        });
        expect(resolve(config)).toThrow(/app\.zipDownloads\.enabled must be true or false/);
        expect(builderViolations(config)).toContain("zip-downloads-enabled-boolean");
    });

    test("useCodeBuild defaults to false and a non-boolean is refused", () => {
        expect(Config.ZIP_DOWNLOADS_DEFAULTS.useCodeBuild).toBe(false);
        const config = baseConfig((c) => {
            c.app.zipDownloads.useCodeBuild = "true";
        });
        expect(resolve(config)).toThrow(/app\.zipDownloads\.useCodeBuild must be true or false/);
        expect(builderViolations(config)).toContain("zip-downloads-use-codebuild-boolean");
    });

    test("useCodeBuild true validates", () => {
        const config = baseConfig((c) => {
            c.app.zipDownloads.useCodeBuild = true;
        });
        expect(resolve(config)().app.zipDownloads.useCodeBuild).toBe(true);
        expect(builderViolations(config)).toEqual([]);
    });

    test("a vCPU size other than 4, 8 or 16 is refused", () => {
        const config = baseConfig((c) => {
            c.app.zipDownloads.computeVcpu = 12;
        });
        expect(resolve(config)).toThrow(/app\.zipDownloads\.computeVcpu must be 4, 8 or 16/);
        expect(builderViolations(config)).toContain("zip-downloads-compute-vcpu");
    });

    test.each([
        [16, 30],
        [8, 18],
        [4, 32],
    ])("%p vCPU with %p GiB is not a Fargate size", (vcpu, memory) => {
        const config = baseConfig((c) => {
            c.app.zipDownloads.computeVcpu = vcpu;
            c.app.zipDownloads.computeMemoryGiB = memory;
        });
        expect(resolve(config)).toThrow(/is not an AWS Fargate memory size/);
        expect(builderViolations(config)).toContain("zip-downloads-compute-fargate-size");
    });

    test.each([
        [4, 8],
        [4, 30],
        [8, 16],
        [8, 60],
        [16, 32],
        [16, 120],
    ])("%p vCPU with %p GiB validates", (vcpu, memory) => {
        const config = baseConfig((c) => {
            c.app.zipDownloads.computeVcpu = vcpu;
            c.app.zipDownloads.computeMemoryGiB = memory;
        });
        expect(resolve(config)().app.zipDownloads.computeMemoryGiB).toBe(memory);
        expect(builderViolations(config)).toEqual([]);
    });
});

describe("app.zipDownloads.enabled requires the global VPC", () => {
    test("enabled without the VPC is refused, naming zipDownloads", () => {
        const config = baseConfig((c) => {
            c.app.zipDownloads.enabled = true;
            c.app.useGlobalVpc.enabled = false;
        });
        expect(resolve(config)).toThrow(
            /app\.useGlobalVpc\.enabled must be true because the following enabled feature\(s\) require a VPC: .*zipDownloads/
        );
        expect(builderViolations(config)).toContain("vpc-required-zip-downloads");
    });

    test("enabled with the VPC validates", () => {
        const config = baseConfig((c) => {
            c.app.zipDownloads.enabled = true;
            c.app.useGlobalVpc.enabled = true;
        });
        expect(resolve(config)().app.zipDownloads.enabled).toBe(true);
        expect(builderViolations(config)).toEqual([]);
    });
});
