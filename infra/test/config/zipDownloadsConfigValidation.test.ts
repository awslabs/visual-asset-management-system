/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` rules for app.pipelines.useZipDownloads (Download as Zip), and their ConfigBuilder mirror.
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
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import defaultConfig from "../../config/config.json";
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
        (rule) =>
            rule.id.startsWith("zip-downloads-") ||
            rule.id === "vpc-required-pipelines-use-zip-downloads"
    )
        .filter((rule) => rule.appliesWhen(config))
        .map((rule) => rule.id);
}

afterEach(() => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("app.pipelines.useZipDownloads defaults", () => {
    test("the shipped block equals the defaults and validates", () => {
        const config = baseConfig();
        expect(resolve(config)().app.pipelines.useZipDownloads).toEqual(
            Config.ZIP_DOWNLOADS_DEFAULTS
        );
        expect(Config.ZIP_DOWNLOADS_DEFAULTS.maxTotalBytesPerRequest).toBe(5497558138880);
        expect(builderViolations(config)).toEqual([]);
    });

    test("an absent block is all defaults (an upgraded config.json)", () => {
        const config = baseConfig((c) => delete c.app.pipelines.useZipDownloads);
        expect(resolve(config)().app.pipelines.useZipDownloads).toEqual(
            Config.ZIP_DOWNLOADS_DEFAULTS
        );
        expect(builderViolations(config)).toEqual([]);
    });

    test("a partial block keeps its values and fills the rest", () => {
        const config = baseConfig((c) => {
            c.app.pipelines.useZipDownloads = { maxActiveRequestsPerUser: 3 };
        });
        expect(resolve(config)().app.pipelines.useZipDownloads).toEqual({
            ...Config.ZIP_DOWNLOADS_DEFAULTS,
            maxActiveRequestsPerUser: 3,
        });
    });
});

describe("the pre-release key app.zipDownloads", () => {
    // Renamed before its first release (owner decision 2026-11-13). A config that still carries it
    // would otherwise deploy with the feature off, so getConfig() names the new path instead.
    test.each([
        ["enabled", { enabled: true }],
        ["empty", {}],
    ])("an old %s block stops the deploy and names the new path", (_name, block) => {
        const config = baseConfig((c) => {
            c.app.zipDownloads = block;
        });
        expect(resolve(config)).toThrow(
            "Configuration Error: app.zipDownloads was renamed to app.pipelines.useZipDownloads"
        );
    });

    test.each([
        ["config.json", defaultConfig],
        ["config.template.commercial.json", commercialTemplate],
        ["config.template.govcloud.json", govcloudTemplate],
        ["config.template.eusovereign.json", eusovereignTemplate],
    ])("%s carries the block under app.pipelines, last, and not the old key", (_name, file) => {
        expect("zipDownloads" in (file as any).app).toBe(false);
        expect(Object.keys((file as any).app.pipelines).slice(-1)).toEqual(["useZipDownloads"]);
    });
});

describe("app.pipelines.useZipDownloads.retentionDays is not configuration (a fixed 7 days)", () => {
    test.each([
        ["config.json", defaultConfig],
        ["config.template.commercial.json", commercialTemplate],
        ["config.template.govcloud.json", govcloudTemplate],
        ["config.template.eusovereign.json", eusovereignTemplate],
    ])("%s does not carry the key", (_name, file) => {
        expect((file as any).app.pipelines.useZipDownloads).toBeDefined();
        expect("retentionDays" in (file as any).app.pipelines.useZipDownloads).toBe(false);
    });

    test("the defaults and the ConfigBuilder have no retentionDays", () => {
        expect("retentionDays" in Config.ZIP_DOWNLOADS_DEFAULTS).toBe(false);
        expect(RULES.some((rule) => rule.id === "zip-downloads-retentionDays-range")).toBe(false);
    });

    test.each([7, 3, 0, 31, 2.5, "x"])(
        "a config that still carries retentionDays = %p loads, and the key is dropped",
        (value) => {
            const config = baseConfig((c) => {
                c.app.pipelines.useZipDownloads.retentionDays = value;
            });
            const resolved = resolve(config)();
            expect(resolved.app.pipelines.useZipDownloads).toEqual(Config.ZIP_DOWNLOADS_DEFAULTS);
            expect("retentionDays" in resolved.app.pipelines.useZipDownloads).toBe(false);
            expect(builderViolations(config)).toEqual([]);
        }
    );
});

describe("app.pipelines.useZipDownloads validation (feature off: values are still checked)", () => {
    test.each([
        ["maxFilesPerRequest", 0],
        ["maxFilesPerRequest", 1000001],
        ["maxTotalBytesPerRequest", 5497558138881],
        ["maxActiveRequestsPerUser", 101],
    ])("%s = %p is refused", (key, value) => {
        const config = baseConfig((c) => {
            c.app.pipelines.useZipDownloads[key] = value;
        });
        expect(resolve(config)).toThrow(
            new RegExp(
                `Configuration Error: app\\.pipelines\\.useZipDownloads\\.${key} must be a whole number`
            )
        );
        expect(builderViolations(config)).toContain(`zip-downloads-${key}-range`);
    });

    test("a non-boolean enabled is refused", () => {
        const config = baseConfig((c) => {
            c.app.pipelines.useZipDownloads.enabled = "yes";
        });
        expect(resolve(config)).toThrow(
            /app\.pipelines\.useZipDownloads\.enabled must be true or false/
        );
        expect(builderViolations(config)).toContain("zip-downloads-enabled-boolean");
    });

    test("useCodeBuild defaults to false and a non-boolean is refused", () => {
        expect(Config.ZIP_DOWNLOADS_DEFAULTS.useCodeBuild).toBe(false);
        const config = baseConfig((c) => {
            c.app.pipelines.useZipDownloads.useCodeBuild = "true";
        });
        expect(resolve(config)).toThrow(
            /app\.pipelines\.useZipDownloads\.useCodeBuild must be true or false/
        );
        expect(builderViolations(config)).toContain("zip-downloads-use-codebuild-boolean");
    });

    test("useCodeBuild true validates", () => {
        const config = baseConfig((c) => {
            c.app.pipelines.useZipDownloads.useCodeBuild = true;
        });
        expect(resolve(config)().app.pipelines.useZipDownloads.useCodeBuild).toBe(true);
        expect(builderViolations(config)).toEqual([]);
    });

    test("a vCPU size other than 4, 8 or 16 is refused", () => {
        const config = baseConfig((c) => {
            c.app.pipelines.useZipDownloads.computeVcpu = 12;
        });
        expect(resolve(config)).toThrow(
            /app\.pipelines\.useZipDownloads\.computeVcpu must be 4, 8 or 16/
        );
        expect(builderViolations(config)).toContain("zip-downloads-compute-vcpu");
    });

    test.each([
        [16, 30],
        [8, 18],
        [4, 32],
    ])("%p vCPU with %p GiB is not a Fargate size", (vcpu, memory) => {
        const config = baseConfig((c) => {
            c.app.pipelines.useZipDownloads.computeVcpu = vcpu;
            c.app.pipelines.useZipDownloads.computeMemoryGiB = memory;
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
            c.app.pipelines.useZipDownloads.computeVcpu = vcpu;
            c.app.pipelines.useZipDownloads.computeMemoryGiB = memory;
        });
        expect(resolve(config)().app.pipelines.useZipDownloads.computeMemoryGiB).toBe(memory);
        expect(builderViolations(config)).toEqual([]);
    });
});

describe("app.pipelines.useZipDownloads.enabled requires the global VPC", () => {
    test("enabled without the VPC is refused, naming pipelines.useZipDownloads", () => {
        const config = baseConfig((c) => {
            c.app.pipelines.useZipDownloads.enabled = true;
            c.app.useGlobalVpc.enabled = false;
        });
        expect(resolve(config)).toThrow(
            /app\.useGlobalVpc\.enabled must be true because the following enabled feature\(s\) require a VPC: .*pipelines\.useZipDownloads/
        );
        expect(builderViolations(config)).toContain("vpc-required-pipelines-use-zip-downloads");
    });

    test("enabled with the VPC validates", () => {
        const config = baseConfig((c) => {
            c.app.pipelines.useZipDownloads.enabled = true;
            c.app.useGlobalVpc.enabled = true;
        });
        expect(resolve(config)().app.pipelines.useZipDownloads.enabled).toBe(true);
        expect(builderViolations(config)).toEqual([]);
    });
});
