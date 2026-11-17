/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * S3 Transfer Acceleration (#1610): configuration validation.
 *
 * Covers `validateS3TransferAcceleration()` directly, the same rules through `getConfig()` on each
 * shipped template (every non-commercial partition rejects a true value; false changes nothing), and
 * the ConfigBuilder rules hand-ported from it, which `configBuilderSync.test.ts` does not cover.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import { newTestApp } from "../support/testApp";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

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

const templateFor = (base: unknown, region: string) => {
    const config = JSON.parse(JSON.stringify(base));
    config.env.region = region;
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    if (config.app.useAlb?.enabled) {
        config.app.useAlb.domainHost = "vams.example.com";
        config.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    }
    return config;
};

const loadConfig = (base: unknown, region: string, mutate?: (c: any) => void) => {
    const config = templateFor(base, region);
    mutate?.(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

const external = (overrides: Partial<Config.ConfigPublicAssetS3Buckets> = {}) => ({
    bucketArn: "arn:aws:s3:::vams-ext-bucket",
    baseAssetsPrefix: "/",
    defaultSyncDatabaseId: "ext",
    ...overrides,
});

const assetBuckets = (overrides: Record<string, unknown> = {}): any => ({
    createNewBucket: true,
    useS3TransferAcceleration: false,
    presignedUrlNetworkRestrictions: { allowedIpRanges: [], allowedVpceIds: [] },
    externalAssetBuckets: null,
    ...overrides,
});

describe("validateS3TransferAcceleration", () => {
    test("accepts the default (off) in every partition", () => {
        for (const partition of ["aws", "aws-us-gov", "aws-eusc", "aws-cn", "aws-iso"]) {
            expect(() =>
                Config.validateS3TransferAcceleration(assetBuckets(), partition)
            ).not.toThrow();
        }
    });

    test("accepts the created bucket on in the commercial partition", () => {
        expect(() =>
            Config.validateS3TransferAcceleration(
                assetBuckets({ useS3TransferAcceleration: true }),
                "aws"
            )
        ).not.toThrow();
    });

    test.each(["aws-us-gov", "aws-eusc", "aws-cn", "aws-iso", "aws-iso-b"])(
        "rejects the top-level key outside the commercial partition (%s)",
        (partition) => {
            expect(() =>
                Config.validateS3TransferAcceleration(
                    assetBuckets({ useS3TransferAcceleration: true }),
                    partition
                )
            ).toThrow(/commercial partition only/);
        }
    );

    test.each(["aws-us-gov", "aws-eusc"])(
        "rejects an external entry's key outside the commercial partition (%s)",
        (partition) => {
            expect(() =>
                Config.validateS3TransferAcceleration(
                    assetBuckets({
                        externalAssetBuckets: [external({ useS3TransferAcceleration: true })],
                    }),
                    partition
                )
            ).toThrow(/commercial partition only/);
        }
    );

    test("rejects the top-level key when no bucket is created", () => {
        expect(() =>
            Config.validateS3TransferAcceleration(
                assetBuckets({
                    createNewBucket: false,
                    useS3TransferAcceleration: true,
                    externalAssetBuckets: [external({ isDefault: true })],
                }),
                "aws"
            )
        ).toThrow(/no VAMS-created asset bucket/);
    });

    test("accepts an external default with the key when no bucket is created", () => {
        expect(() =>
            Config.validateS3TransferAcceleration(
                assetBuckets({
                    createNewBucket: false,
                    externalAssetBuckets: [
                        external({ isDefault: true, useS3TransferAcceleration: true }),
                    ],
                }),
                "aws"
            )
        ).not.toThrow();
    });

    test.each([
        ["a period", "arn:aws:s3:::my.dotted.bucket"],
        ["uppercase", "arn:aws:s3:::MyBucket"],
        ["an underscore", "arn:aws:s3:::my_bucket"],
        ["fewer than 3 characters", "arn:aws:s3:::ab"],
        ["a leading hyphen", "arn:aws:s3:::-bucket"],
    ])("rejects a flagged external bucket name with %s", (_label, bucketArn) => {
        expect(() =>
            Config.validateS3TransferAcceleration(
                assetBuckets({
                    externalAssetBuckets: [
                        external({ bucketArn, useS3TransferAcceleration: true }),
                    ],
                }),
                "aws"
            )
        ).toThrow(/not valid for S3 Transfer Acceleration/);
    });

    test("does not check the name of an external bucket that is not flagged", () => {
        expect(() =>
            Config.validateS3TransferAcceleration(
                assetBuckets({
                    useS3TransferAcceleration: true,
                    externalAssetBuckets: [
                        external({ bucketArn: "arn:aws:s3:::my.dotted.bucket" }),
                    ],
                }),
                "aws"
            )
        ).not.toThrow();
    });

    test("rejects entries for one ARN that disagree, in either order", () => {
        const flagged = external({ baseAssetsPrefix: "a/", useS3TransferAcceleration: true });
        const plain = external({ baseAssetsPrefix: "b/" });
        for (const entries of [
            [flagged, plain],
            [plain, flagged],
        ]) {
            expect(() =>
                Config.validateS3TransferAcceleration(
                    assetBuckets({ externalAssetBuckets: entries }),
                    "aws"
                )
            ).toThrow(/inconsistent useS3TransferAcceleration/);
        }
    });

    test("accepts entries for one ARN that agree", () => {
        expect(() =>
            Config.validateS3TransferAcceleration(
                assetBuckets({
                    externalAssetBuckets: [
                        external({ baseAssetsPrefix: "a/", useS3TransferAcceleration: true }),
                        external({ baseAssetsPrefix: "b/", useS3TransferAcceleration: true }),
                    ],
                }),
                "aws"
            )
        ).not.toThrow();
    });

    test("rejects an accelerated bucket with a VPC-endpoint-only restriction", () => {
        const restrictions = { allowedIpRanges: [], allowedVpceIds: ["vpce-0123456789abcdef0"] };
        expect(() =>
            Config.validateS3TransferAcceleration(
                assetBuckets({
                    useS3TransferAcceleration: true,
                    presignedUrlNetworkRestrictions: restrictions,
                }),
                "aws"
            )
        ).toThrow(/allowedVpceIds/);
        expect(() =>
            Config.validateS3TransferAcceleration(
                assetBuckets({
                    presignedUrlNetworkRestrictions: restrictions,
                    externalAssetBuckets: [external({ useS3TransferAcceleration: true })],
                }),
                "aws"
            )
        ).toThrow(/allowedVpceIds/);
    });

    test("allows a VPC-endpoint restriction when nothing is accelerated (control)", () => {
        expect(() =>
            Config.validateS3TransferAcceleration(
                assetBuckets({
                    presignedUrlNetworkRestrictions: {
                        allowedIpRanges: [],
                        allowedVpceIds: ["vpce-0123456789abcdef0"],
                    },
                }),
                "aws"
            )
        ).not.toThrow();
    });

    test("allows an accelerated bucket with an IP-range restriction", () => {
        expect(() =>
            Config.validateS3TransferAcceleration(
                assetBuckets({
                    useS3TransferAcceleration: true,
                    presignedUrlNetworkRestrictions: {
                        allowedIpRanges: ["203.0.113.0/24"],
                        allowedVpceIds: [],
                    },
                }),
                "aws"
            )
        ).not.toThrow();
    });
});

describe("effectiveDefaultBucketAccelerated", () => {
    test("is the created bucket's flag when no external bucket is the default", () => {
        expect(
            Config.effectiveDefaultBucketAccelerated(
                assetBuckets({ useS3TransferAcceleration: true })
            )
        ).toBe(true);
        expect(Config.effectiveDefaultBucketAccelerated(assetBuckets())).toBe(false);
    });

    test("is the external default's flag, not the created bucket's, when one exists", () => {
        expect(
            Config.effectiveDefaultBucketAccelerated(
                assetBuckets({
                    useS3TransferAcceleration: true,
                    externalAssetBuckets: [external({ isDefault: true })],
                })
            )
        ).toBe(false);
        expect(
            Config.effectiveDefaultBucketAccelerated(
                assetBuckets({
                    externalAssetBuckets: [
                        external({ isDefault: true, useS3TransferAcceleration: true }),
                    ],
                })
            )
        ).toBe(true);
    });

    test("ignores a flagged external bucket that is not the default", () => {
        expect(
            Config.effectiveDefaultBucketAccelerated(
                assetBuckets({
                    externalAssetBuckets: [external({ useS3TransferAcceleration: true })],
                })
            )
        ).toBe(false);
    });
});

describe("getConfig(): S3 Transfer Acceleration", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    const MESSAGE = /S3 Transfer Acceleration/;

    test("a config written without the key loads with it false", () => {
        const config = templateFor(commercialTemplate, "us-east-1");
        delete config.app.assetBuckets.useS3TransferAcceleration;
        serveConfig(config);
        expect(Config.getConfig(newTestApp()).app.assetBuckets.useS3TransferAcceleration).toBe(
            false
        );
    });

    test("accepts true in the commercial partition", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.assetBuckets.useS3TransferAcceleration = true;
        });
        expect(run).not.toThrow();
    });

    test.each([
        ["GovCloud", govcloudTemplate, "us-gov-west-1"],
        ["EU Sovereign", eusovereignTemplate, "eusc-de-east-1"],
    ])("rejects true in %s", (_name, template, region) => {
        const run = loadConfig(template, region, (c) => {
            c.app.assetBuckets.useS3TransferAcceleration = true;
        });
        expect(run).toThrow(MESSAGE);
    });

    test.each([
        ["GovCloud", govcloudTemplate, "us-gov-west-1"],
        ["EU Sovereign", eusovereignTemplate, "eusc-de-east-1"],
    ])("rejects true on an external entry in %s", (_name, template, region) => {
        const run = loadConfig(template, region, (c) => {
            c.app.assetBuckets.externalAssetBuckets = [
                {
                    ...external({ useS3TransferAcceleration: true }),
                    bucketArn: `arn:${
                        c.env.partition ?? (region.startsWith("us-gov") ? "aws-us-gov" : "aws-eusc")
                    }:s3:::vams-ext-bucket`,
                },
            ];
        });
        expect(run).toThrow(MESSAGE);
    });

    test.each([
        ["GovCloud", govcloudTemplate, "us-gov-west-1"],
        ["EU Sovereign", eusovereignTemplate, "eusc-de-east-1"],
    ])(
        "still accepts the shipped %s config with the key false (control)",
        (_n, template, region) => {
            expect(loadConfig(template, region)).not.toThrow(MESSAGE);
        }
    );
});

describe("ConfigBuilder rules mirror the getConfig() validation", () => {
    const ruleIds = (mutate: (c: any) => void, region = "us-east-1"): string[] => {
        const cfg: any = makeDefaultConfig("commercial");
        cfg.env.region = region;
        mutate(cfg);
        return evaluateRules(cfg)
            .filter((issue: any) => String(issue.id).startsWith("s3ta-"))
            .map((issue: any) => issue.id);
    };

    test("the defaults raise none", () => {
        expect(ruleIds(() => undefined)).toEqual([]);
    });

    test("true in the commercial partition raises none", () => {
        expect(
            ruleIds((c) => {
                c.app.assetBuckets.useS3TransferAcceleration = true;
            })
        ).toEqual([]);
    });

    test.each(["us-gov-west-1", "eusc-de-east-1"])("true in %s is rejected", (region) => {
        expect(
            ruleIds((c) => {
                c.app.assetBuckets.useS3TransferAcceleration = true;
            }, region)
        ).toContain("s3ta-commercial-only");
    });

    test("true with no created bucket is rejected", () => {
        expect(
            ruleIds((c) => {
                c.app.assetBuckets.createNewBucket = false;
                c.app.assetBuckets.useS3TransferAcceleration = true;
                c.app.assetBuckets.externalAssetBuckets = [external({ isDefault: true })];
            })
        ).toContain("s3ta-needs-created-bucket");
    });

    test("a dotted external bucket name is rejected", () => {
        expect(
            ruleIds((c) => {
                c.app.assetBuckets.externalAssetBuckets = [
                    external({
                        bucketArn: "arn:aws:s3:::my.dotted.bucket",
                        useS3TransferAcceleration: true,
                    }),
                ];
            })
        ).toContain("s3ta-external-bucket-name");
    });

    test("entries for one ARN that disagree are rejected", () => {
        expect(
            ruleIds((c) => {
                c.app.assetBuckets.externalAssetBuckets = [
                    external({ baseAssetsPrefix: "a/", useS3TransferAcceleration: true }),
                    external({ baseAssetsPrefix: "b/" }),
                ];
            })
        ).toContain("s3ta-external-inconsistent");
    });

    test("an accelerated bucket with allowedVpceIds is rejected; IP ranges are not", () => {
        expect(
            ruleIds((c) => {
                c.app.assetBuckets.useS3TransferAcceleration = true;
                c.app.assetBuckets.presignedUrlNetworkRestrictions.allowedVpceIds = [
                    "vpce-0123456789abcdef0",
                ];
            })
        ).toContain("s3ta-vpce-restriction");
        expect(
            ruleIds((c) => {
                c.app.assetBuckets.useS3TransferAcceleration = true;
                c.app.assetBuckets.presignedUrlNetworkRestrictions.allowedIpRanges = [
                    "203.0.113.0/24",
                ];
            })
        ).toEqual([]);
    });
});
