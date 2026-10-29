/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The `getConfig()` validations that depend on the resolved AWS partition.
 *
 * **Serverless availability.** Amazon OpenSearch Serverless is not offered in the AWS European
 * Sovereign Cloud, and `aoss` has no entry for the `aws-eusc` partition in SERVICE_LOOKUP. Without a
 * configuration check, enabling Serverless there fails mid-synth with `Service AOSS not found in
 * partition aws-eusc` — a message that names the service rather than the configuration field that
 * caused it, sending the operator to the wrong file. The check is keyed on the resolved partition,
 * NOT on `app.govCloud.enabled`: GovCloud does have an `aoss` entry, so gating on the shared
 * restricted-partition flag would wrongly block Serverless there. The GovCloud case is the control
 * that proves the distinction is real.
 *
 * **Restricted-partition flag agreement.** `app.govCloud.enabled` is the restricted-partition switch
 * (GovCloud, EU Sovereign, and ISO all set it), and every capability downgrade keyed on it is skipped
 * when it is left false — most consequentially stripping Tags from each
 * `AWS::Lambda::EventSourceMapping`, which those partitions reject. Nothing downstream detects the
 * mismatch, so the deployment synthesizes cleanly and then fails partway through creating the core
 * stack. `getConfig()` asserts the flag against the partition so the failure is a config error.
 *
 * **FIPS in the EU Sovereign Cloud.** The AWS European Sovereign Cloud offers FIPS endpoints for
 * only four services (AWS KMS, Amazon EFS, Amazon ElastiCache and AWS WAF), so `app.useFips` gives
 * the services VAMS calls no FIPS transport there. `getConfig()` warns rather than rejects, so the
 * positive case is paired with a does-not-throw case, and the GovCloud template, which ships
 * `useFips: true`, is the control that proves the rule is keyed on the partition. The
 * ConfigBuilder's hand-ported rule is asserted from the same cases, because
 * `configBuilderSync.test.ts` covers only `schema.ts` and `defaults.ts`.
 *
 * **Rekognition availability.** The GenAI metadata labeling pipeline calls Amazon Rekognition
 * `DetectLabels` on every rendered image, and Rekognition is not offered in the AWS European
 * Sovereign Cloud or in AWS GovCloud (US-East), so every execution there fails at that call. Under
 * `useGlobalVpc.useForAllLambdas` with `addVpcEndpoints` the VPC builder also requests a
 * Rekognition interface endpoint the Region does not offer. `getConfig()` rejects that combination
 * and warns for every other enabled one. The check is keyed on the `aws-eusc` partition and the
 * `us-gov-east-1` Region, NOT on `app.govCloud.enabled`: AWS GovCloud (US-West) offers Rekognition,
 * and it is the control. The ConfigBuilder's hand-ported rules are asserted against the same arms.
 *
 * `getConfig()` reads `config/config.json` from disk, so these tests mock `fs.readFileSync` to serve
 * a chosen template. Only the config filename is intercepted; every other read (the S3 policy and WAF
 * policy JSON that `getConfig()` also loads) falls through to the real implementation.
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

/** Serve `configJson` for config.json; delegate every other path to the real fs. */
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

/** A deployable config derived from a template, with the placeholders getConfig() requires filled. */
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

// config.ts is imported once at module scope and getConfig() re-reads config.json on every call, so
// no jest.resetModules() is needed here — and it must NOT be used. Resetting the registry re-runs the
// jest.mock("fs") factory, producing a SECOND mock instance: the freshly required config.ts binds to
// it while serveConfig() keeps configuring the original, so getConfig() silently reads the real
// on-disk config.json. Every "does not throw" assertion then passes vacuously.
const loadConfig = (base: unknown, region: string, mutate?: (c: any) => void) => {
    const config = templateFor(base, region);
    mutate?.(config);
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

const SERVERLESS_EUSC_MESSAGE =
    /openSearch.useServerless is not supported in the 'aws-eusc' partition/;

describe("OpenSearch Serverless partition availability", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("rejects Serverless in the EU Sovereign Cloud", () => {
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1", (c) => {
            // The combination that previously passed validation and died later at the AOSS lookup.
            c.app.openSearch.useServerless.enabled = true;
            c.app.openSearch.useServerless.nextGen = false;
            c.app.openSearch.useProvisioned.enabled = false;
        });
        expect(run).toThrow(SERVERLESS_EUSC_MESSAGE);
    });

    test("accepts the shipped EU Sovereign config, which uses Provisioned", () => {
        // Guards against the rule being written so broadly that it blocks the supported topology.
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1");
        expect(run).not.toThrow(SERVERLESS_EUSC_MESSAGE);
    });

    test("still allows Serverless in GovCloud, which does have an aoss endpoint", () => {
        // Control: proves the rule is keyed on the partition and not on app.govCloud.enabled,
        // which both restricted templates set to true.
        const run = loadConfig(govcloudTemplate, "us-gov-west-1", (c) => {
            c.app.openSearch.useServerless.enabled = true;
            c.app.openSearch.useServerless.nextGen = false; // NEXTGEN is separately barred here
            c.app.openSearch.useProvisioned.enabled = false;
        });
        expect(run).not.toThrow(SERVERLESS_EUSC_MESSAGE);
    });

    test("still allows Serverless in the commercial partition", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.openSearch.useServerless.enabled = true;
            c.app.openSearch.useProvisioned.enabled = false;
        });
        expect(run).not.toThrow(SERVERLESS_EUSC_MESSAGE);
    });
});

const FLAG_REQUIRED_MESSAGE = /requires app\.govCloud\.enabled to be true/;
const IL6_REQUIRED_MESSAGE = /requires app\.govCloud\.il6Compliant to be true/;

/** ISO has no shipped template; derive one from govcloud and satisfy the IL6 control set. */
const isoBase = () => {
    const config = JSON.parse(JSON.stringify(govcloudTemplate));
    config.app.govCloud.il6Compliant = true;
    config.app.authProvider.useCognito.enabled = false;
    config.app.useWaf = false;
    config.app.useKmsCmkEncryption.enabled = true;
    return config;
};

describe("restricted-partition flag agreement", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    // us-isob-east-1 resolves to aws-iso-b, so the suffixed ISO partitions are covered by the same
    // prefix check rather than only the bare aws-iso.
    const restrictedRegions: Array<[string, string, () => any]> = [
        ["us-gov-west-1", "aws-us-gov", () => govcloudTemplate],
        ["eusc-de-east-1", "aws-eusc", () => eusovereignTemplate],
        ["us-iso-east-1", "aws-iso", isoBase],
        ["us-isob-east-1", "aws-iso-b", isoBase],
    ];

    test.each(restrictedRegions)(
        "%s (%s) rejects app.govCloud.enabled = false",
        (region, _partition, base) => {
            const run = loadConfig(base(), region, (c) => {
                c.app.govCloud.enabled = false;
            });
            expect(run).toThrow(FLAG_REQUIRED_MESSAGE);
        }
    );

    test.each(restrictedRegions)(
        "%s (%s) rejects a missing app.govCloud.enabled",
        (region, _partition, base) => {
            // An older config.json predating the field leaves it undefined; the check compares
            // against true rather than truthiness so undefined is rejected too.
            const run = loadConfig(base(), region, (c) => {
                delete c.app.govCloud.enabled;
            });
            expect(run).toThrow(FLAG_REQUIRED_MESSAGE);
        }
    );

    test.each(restrictedRegions)(
        "%s (%s) accepts the flag when true",
        (region, _partition, base) => {
            const run = loadConfig(base(), region);
            expect(run).not.toThrow(FLAG_REQUIRED_MESSAGE);
        }
    );

    test("the commercial partition does not require the flag", () => {
        // Control: proves the rule is scoped to restricted partitions rather than always firing.
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.govCloud.enabled = false;
        });
        expect(run).not.toThrow(FLAG_REQUIRED_MESSAGE);
    });

    test("the China partition is out of scope for the flag requirement", () => {
        const run = loadConfig(commercialTemplate, "cn-north-1", (c) => {
            c.app.govCloud.enabled = false;
        });
        expect(run).not.toThrow(FLAG_REQUIRED_MESSAGE);
    });

    test.each([
        ["us-iso-east-1", "aws-iso"],
        ["us-isob-east-1", "aws-iso-b"],
    ])("%s (%s) additionally requires il6Compliant", (region) => {
        const run = loadConfig(isoBase(), region, (c) => {
            c.app.govCloud.il6Compliant = false;
        });
        expect(run).toThrow(IL6_REQUIRED_MESSAGE);
    });

    test("GovCloud does NOT require il6Compliant", () => {
        // Control: IL6 stays opt-in outside the ISO partitions.
        const run = loadConfig(govcloudTemplate, "us-gov-west-1", (c) => {
            c.app.govCloud.il6Compliant = false;
        });
        expect(run).not.toThrow(IL6_REQUIRED_MESSAGE);
    });

    test("EU Sovereign does NOT require il6Compliant", () => {
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1", (c) => {
            c.app.govCloud.il6Compliant = false;
        });
        expect(run).not.toThrow(IL6_REQUIRED_MESSAGE);
    });

    test("an unrecognized region does not raise a TypeError from the partition checks", () => {
        // region_info resolves an unknown region's partition to undefined, so the checks default it
        // to "" rather than calling startsWith on undefined. Whatever error surfaces must come from
        // the validation that owns the bad region, not from these checks.
        const run = loadConfig(commercialTemplate, "bogus-region-1", (c) => {
            c.app.govCloud.enabled = false;
        });
        expect(run).not.toThrow(TypeError);
        expect(run).not.toThrow(FLAG_REQUIRED_MESSAGE);
    });
});

const FIPS_EUSC_WARNING = /app\.useFips is true while deploying to the 'aws-eusc' partition/;

/** The console.warn messages `run` emits that match `pattern`; console.warn is silenced meanwhile. */
const warningsMatching = (run: () => unknown, pattern: RegExp): string[] => {
    const captured: string[] = [];
    const spy = jest.spyOn(console, "warn").mockImplementation((...args: unknown[]) => {
        captured.push(args.map(String).join(" "));
    });
    try {
        run();
    } finally {
        spy.mockRestore();
    }
    return captured.filter((message) => pattern.test(message));
};

describe("useFips in the EU Sovereign Cloud warns and does not reject", () => {
    // getConfig() also reads AWS_USE_FIPS_ENDPOINT and the first true source wins, so a shell that
    // exports it would turn every "does not warn" case below into a false failure.
    const savedFipsEnv = process.env.AWS_USE_FIPS_ENDPOINT;

    beforeEach(() => {
        delete process.env.AWS_USE_FIPS_ENDPOINT;
    });

    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    afterAll(() => {
        if (savedFipsEnv === undefined) delete process.env.AWS_USE_FIPS_ENDPOINT;
        else process.env.AWS_USE_FIPS_ENDPOINT = savedFipsEnv;
    });

    test("warns when useFips is true in aws-eusc", () => {
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1", (c) => {
            c.app.useFips = true;
        });
        expect(warningsMatching(run, FIPS_EUSC_WARNING)).toHaveLength(1);
    });

    test("warns when AWS_USE_FIPS_ENDPOINT turns the flag on", () => {
        // The rule reads the resolved flag, not config.json alone.
        process.env.AWS_USE_FIPS_ENDPOINT = "true";
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1");
        expect(warningsMatching(run, FIPS_EUSC_WARNING)).toHaveLength(1);
    });

    test("does NOT throw — the rule warns, it does not reject", () => {
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1", (c) => {
            c.app.useFips = true;
        });
        const spy = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            expect(run).not.toThrow();
        } finally {
            spy.mockRestore();
        }
    });

    test("does not warn on the shipped EU Sovereign config, which sets useFips false", () => {
        expect(eusovereignTemplate.app.useFips).toBe(false);
        const run = loadConfig(eusovereignTemplate, "eusc-de-east-1");
        expect(warningsMatching(run, FIPS_EUSC_WARNING)).toEqual([]);
    });

    test("does not warn in GovCloud, whose shipped template sets useFips true", () => {
        // Control: proves the rule is keyed on the partition and not on app.govCloud.enabled, which
        // both restricted templates set.
        expect(govcloudTemplate.app.useFips).toBe(true);
        const run = loadConfig(govcloudTemplate, "us-gov-west-1");
        expect(warningsMatching(run, FIPS_EUSC_WARNING)).toEqual([]);
    });

    test("does not warn in the commercial partition", () => {
        const run = loadConfig(commercialTemplate, "us-east-1", (c) => {
            c.app.useFips = true;
        });
        expect(warningsMatching(run, FIPS_EUSC_WARNING)).toEqual([]);
    });
});

describe("the ConfigBuilder mirrors the useFips EU Sovereign warning", () => {
    const fired = (cfg: ReturnType<typeof makeDefaultConfig>) =>
        evaluateRules(cfg).filter((rule) => rule.id === "fips-not-in-eusovereign");

    test("raises it as a warning for useFips true in eusc-de-east-1", () => {
        const cfg = makeDefaultConfig("eusovereign");
        cfg.app.useFips = true;
        const rules = fired(cfg);
        expect(rules).toHaveLength(1);
        expect(rules[0].severity).toBe("warning");
    });

    test("is silent on the EU Sovereign preset, which sets useFips false", () => {
        expect(fired(makeDefaultConfig("eusovereign"))).toEqual([]);
    });

    test("is silent on the GovCloud preset with a GovCloud region", () => {
        const cfg = makeDefaultConfig("govcloud");
        cfg.env.region = "us-gov-west-1";
        expect(cfg.app.useFips).toBe(true);
        expect(fired(cfg)).toEqual([]);
    });

    test("is silent while env.region is unset, like every partition rule", () => {
        // The deploy-time region can come from CDK context or the environment, which the builder
        // cannot read, so the partition is unknown.
        const cfg = makeDefaultConfig("eusovereign");
        cfg.app.useFips = true;
        cfg.env.region = null;
        expect(fired(cfg)).toEqual([]);
    });
});

/**
 * **Cross-Region Amazon S3 interface endpoints.** An external asset bucket may be in another Region
 * of the deployment's partition. When Lambdas run in the VPC, VAMS reaches that Region through a
 * cross-Region AWS PrivateLink interface endpoint, which AWS offers in the commercial partition only.
 * `getConfig()` therefore rejects `app.useGlobalVpc.addCrossRegionS3Endpoints: true` together with a
 * cross-Region bucket outside the `aws` partition, naming the flag and both Regions; with the flag
 * `false` the operator supplies the network path and the configuration is accepted. The check is
 * keyed on the resolved partition, not `app.govCloud.enabled`. The commercial case is the control;
 * the no-cross-Region-bucket case proves the flag is inert until such a bucket appears, which is why
 * every shipped template carries it `true`. A cross-Region DEFAULT bucket is rejected in every
 * partition. The ConfigBuilder's hand-ported rules are asserted from the same arms.
 */
const CROSS_REGION_ENDPOINT_MESSAGE =
    /app\.useGlobalVpc\.addCrossRegionS3Endpoints is true, but cross-Region AWS PrivateLink to Amazon S3 is available in the commercial partition only/;
const CROSS_REGION_ENDPOINT_RULE_ID = "cross-region-s3-endpoints-commercial-only";
const CROSS_REGION_DEFAULT_RULE_ID = "external-bucket-default-must-be-deployment-region";
const CROSS_REGION_NO_ENDPOINT_WARNING_RULE_ID = "cross-region-bucket-isolated-lambdas-no-endpoint";

describe("cross-Region S3 interface endpoint partition gate", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    const withCrossRegionBucket =
        (bucketRegion: string, partition: string, extra?: (c: any) => void) => (c: any) => {
            c.app.assetBuckets.externalAssetBuckets = [
                {
                    bucketArn: `arn:${partition}:s3:::remote-assets`,
                    baseAssetsPrefix: "/",
                    defaultSyncDatabaseId: "remote",
                    bucketRegion,
                },
            ];
            extra?.(c);
        };

    const restrictedArms: [string, string, string, () => unknown][] = [
        ["us-gov-west-1", "aws-us-gov", "us-gov-east-1", () => govcloudTemplate],
        ["eusc-de-east-1", "aws-eusc", "eusc-de-west-1", () => eusovereignTemplate],
    ];

    test.each(restrictedArms)(
        "%s (%s) rejects the flag true with a bucket in %s",
        (region, partition, bucketRegion, base) => {
            const run = loadConfig(
                base(),
                region,
                withCrossRegionBucket(bucketRegion, partition, (c) => {
                    c.app.useGlobalVpc.addCrossRegionS3Endpoints = true;
                })
            );
            expect(run).toThrow(CROSS_REGION_ENDPOINT_MESSAGE);
        }
    );

    test.each(restrictedArms)(
        "%s (%s) names the flag and both Regions in the error",
        (region, partition, bucketRegion, base) => {
            const run = loadConfig(base(), region, withCrossRegionBucket(bucketRegion, partition));
            expect(run).toThrow(
                new RegExp(
                    `addCrossRegionS3Endpoints[\\s\\S]*'${bucketRegion}'[\\s\\S]*'${region}'[\\s\\S]*\\(${partition}\\)[\\s\\S]*Set app\\.useGlobalVpc\\.addCrossRegionS3Endpoints to false`
                )
            );
        }
    );

    test.each(restrictedArms)(
        "%s (%s) treats an omitted flag as true (the backward-compatible default)",
        (region, partition, bucketRegion, base) => {
            const run = loadConfig(
                base(),
                region,
                withCrossRegionBucket(bucketRegion, partition, (c) => {
                    delete c.app.useGlobalVpc.addCrossRegionS3Endpoints;
                })
            );
            expect(run).toThrow(CROSS_REGION_ENDPOINT_MESSAGE);
        }
    );

    test.each(restrictedArms)(
        "%s (%s) accepts the flag false with a bucket in %s",
        (region, partition, bucketRegion, base) => {
            const run = loadConfig(
                base(),
                region,
                withCrossRegionBucket(bucketRegion, partition, (c) => {
                    c.app.useGlobalVpc.addCrossRegionS3Endpoints = false;
                })
            );
            expect(run).not.toThrow();
        }
    );

    test.each(restrictedArms)(
        "%s (%s) accepts the shipped template, whose flag is inert without a cross-Region bucket",
        (region, _partition, _bucketRegion, base) => {
            const run = loadConfig(base(), region);
            expect(run).not.toThrow(CROSS_REGION_ENDPOINT_MESSAGE);
        }
    );

    test("the commercial partition accepts the flag true with a cross-Region bucket", () => {
        // Control: proves the rule is scoped to restricted partitions rather than always firing.
        const run = loadConfig(
            commercialTemplate,
            "us-west-2",
            withCrossRegionBucket("us-east-1", "aws", (c) => {
                c.app.useGlobalVpc.addCrossRegionS3Endpoints = true;
            })
        );
        expect(run).not.toThrow();
    });

    test("the commercial partition resolves an omitted flag to true", () => {
        const run = loadConfig(commercialTemplate, "us-west-2", (c) => {
            delete c.app.useGlobalVpc.addCrossRegionS3Endpoints;
        });
        expect(run().app.useGlobalVpc.addCrossRegionS3Endpoints).toBe(true);
    });

    test("a cross-Region default bucket is rejected in the commercial partition too", () => {
        const run = loadConfig(
            commercialTemplate,
            "us-west-2",
            withCrossRegionBucket("us-east-1", "aws", (c) => {
                c.app.assetBuckets.externalAssetBuckets[0].isDefault = true;
            })
        );
        expect(run).toThrow(
            /is marked isDefault but is in 'us-east-1' while the deployment is in 'us-west-2'/
        );
    });

    test("warns, rather than rejects, for in-VPC Lambdas with the flag false and a cross-Region bucket", () => {
        const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            const run = loadConfig(
                commercialTemplate,
                "us-west-2",
                withCrossRegionBucket("us-east-1", "aws", (c) => {
                    c.app.useGlobalVpc.enabled = true;
                    c.app.useGlobalVpc.useForAllLambdas = true;
                    c.app.useGlobalVpc.addCrossRegionS3Endpoints = false;
                    // In-VPC Lambdas cannot use a public Serverless collection (an unrelated rule).
                    c.app.openSearch.useServerless.allowPublic = false;
                })
            );
            expect(run).not.toThrow();
            expect(
                warn.mock.calls.some((call) =>
                    /addCrossRegionS3Endpoints is false, but external asset buckets are configured in 'us-east-1'/.test(
                        String(call[0])
                    )
                )
            ).toBe(true);
        } finally {
            warn.mockRestore();
        }
    });

    test("does not warn when the flag is true", () => {
        const warn = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            loadConfig(
                commercialTemplate,
                "us-west-2",
                withCrossRegionBucket("us-east-1", "aws", (c) => {
                    c.app.useGlobalVpc.enabled = true;
                    c.app.useGlobalVpc.useForAllLambdas = true;
                    c.app.openSearch.useServerless.allowPublic = false;
                })
            )();
            expect(
                warn.mock.calls.some((call) =>
                    /addCrossRegionS3Endpoints is false/.test(String(call[0]))
                )
            ).toBe(false);
        } finally {
            warn.mockRestore();
        }
    });
});

describe("ConfigBuilder mirror of the cross-Region S3 endpoint rules", () => {
    const ruleIds = (cfg: ReturnType<typeof makeDefaultConfig>) =>
        evaluateRules(cfg)
            .map((rule) => rule.id)
            .filter((id) =>
                [
                    CROSS_REGION_ENDPOINT_RULE_ID,
                    CROSS_REGION_DEFAULT_RULE_ID,
                    CROSS_REGION_NO_ENDPOINT_WARNING_RULE_ID,
                ].includes(id)
            );

    const withBucket = (
        cfg: ReturnType<typeof makeDefaultConfig>,
        bucketRegion: string,
        partition: string,
        isDefault = false
    ) => {
        (cfg.app.assetBuckets as any).externalAssetBuckets = [
            {
                bucketArn: `arn:${partition}:s3:::remote-assets`,
                baseAssetsPrefix: "/",
                defaultSyncDatabaseId: "remote",
                bucketRegion,
                isDefault,
            },
        ];
        return cfg;
    };

    test.each([
        ["govcloud", "us-gov-west-1", "us-gov-east-1", "aws-us-gov"],
        ["eusovereign", "eusc-de-east-1", "eusc-de-west-1", "aws-eusc"],
    ])(
        "%s preset reports the endpoint rule with the flag true",
        (profile, region, bucketRegion, partition) => {
            const cfg = makeDefaultConfig(profile as any);
            cfg.env.region = region;
            withBucket(cfg, bucketRegion, partition);
            expect(ruleIds(cfg)).toEqual([CROSS_REGION_ENDPOINT_RULE_ID]);
            const rule = evaluateRules(cfg).find((r) => r.id === CROSS_REGION_ENDPOINT_RULE_ID);
            expect(rule?.severity).toBe("error");
        }
    );

    test("govcloud preset clears the endpoint rule with the flag false", () => {
        const cfg = makeDefaultConfig("govcloud");
        cfg.env.region = "us-gov-west-1";
        withBucket(cfg, "us-gov-east-1", "aws-us-gov");
        cfg.app.useGlobalVpc.addCrossRegionS3Endpoints = false;
        expect(ruleIds(cfg)).toEqual([]);
    });

    test("govcloud preset with in-VPC Lambdas and the flag false reports the warning", () => {
        const cfg = makeDefaultConfig("govcloud");
        cfg.env.region = "us-gov-west-1";
        withBucket(cfg, "us-gov-east-1", "aws-us-gov");
        cfg.app.useGlobalVpc.useForAllLambdas = true;
        cfg.app.useGlobalVpc.addCrossRegionS3Endpoints = false;
        expect(ruleIds(cfg)).toEqual([CROSS_REGION_NO_ENDPOINT_WARNING_RULE_ID]);
        const rule = evaluateRules(cfg).find(
            (r) => r.id === CROSS_REGION_NO_ENDPOINT_WARNING_RULE_ID
        );
        expect(rule?.severity).toBe("warning");
    });

    test("commercial preset reports nothing for a cross-Region bucket with the flag true", () => {
        const cfg = makeDefaultConfig("commercial");
        cfg.env.region = "us-west-2";
        withBucket(cfg, "us-east-1", "aws");
        expect(ruleIds(cfg)).toEqual([]);
    });

    test("commercial preset reports the default-bucket rule for a cross-Region default bucket", () => {
        const cfg = makeDefaultConfig("commercial");
        cfg.env.region = "us-west-2";
        withBucket(cfg, "us-east-1", "aws", true);
        expect(ruleIds(cfg)).toEqual([CROSS_REGION_DEFAULT_RULE_ID]);
    });

    test("stays silent without a cross-Region bucket", () => {
        const cfg = makeDefaultConfig("govcloud");
        cfg.env.region = "us-gov-west-1";
        withBucket(cfg, "us-gov-west-1", "aws-us-gov");
        expect(ruleIds(cfg)).toEqual([]);
    });

    test("stays silent while the Region is unset", () => {
        const cfg = makeDefaultConfig("govcloud");
        (cfg.env as any).region = null;
        withBucket(cfg, "us-gov-east-1", "aws-us-gov", true);
        expect(ruleIds(cfg)).toEqual([]);
    });
});

/**
 * **The Blender agent's Bedrock model.** The Image to 3D Blender agent reasons with a Bedrock model, and
 * which models an account may invoke differs by partition and by entitlement, so the model id follows the
 * labeling pipeline's rule: the commercial template carries one, the restricted-partition templates ship it
 * empty, `getConfig()` rejects an enabled agent with no model id in every partition, and rejects a
 * commercial-only `global.` or `us.` inference-profile prefix outside the `aws` partition. The model id is
 * deployment configuration only; no template offers it per run. The ConfigBuilder's hand-ported rules are
 * asserted from the same arms.
 */
const AGENT_MODEL_REQUIRED_MESSAGE =
    /useImageTo3d\.models\.blenderagent\.bedrockModelId must be set when the Blender agent is enabled/;
const AGENT_MODEL_PREFIX_MESSAGE =
    /useImageTo3d\.models\.blenderagent\.bedrockModelId is "[^"]+", whose "(global|us)\." cross-Region inference-profile prefix exists only in the commercial partition/;
const AGENT_MODEL_RULE_ID = "imageto3d-blenderagent-model-id";
const AGENT_MODEL_PREFIX_RULE_ID = "imageto3d-blenderagent-model-id-commercial-only-prefix";

/** Enable the Blender agent; `modelId` replaces the template's value when given. */
const enableBlenderAgent = (modelId?: string) => (c: any) => {
    const imageTo3d = c.app.pipelines.useImageTo3d;
    c.app.useGlobalVpc.enabled = true;
    imageTo3d.enabled = true;
    imageTo3d.huggingFaceToken = "hf_test_token";
    imageTo3d.models.blenderagent.enabled = true;
    if (modelId !== undefined) {
        imageTo3d.models.blenderagent.bedrockModelId = modelId;
    }
};

describe("the Blender agent's Bedrock model id", () => {
    afterEach(() => {
        (fs.readFileSync as unknown as jest.Mock).mockReset();
    });

    test("accepts the commercial template's model id in the commercial partition", () => {
        // The whole call must pass, so the shipped value is a deployable one.
        const run = loadConfig(commercialTemplate, "us-west-2", enableBlenderAgent());
        expect(run).not.toThrow();
    });

    test("ships a model id in the commercial template and none in the restricted ones", () => {
        const agentModel = (t: any) =>
            t.app.pipelines.useImageTo3d.models.blenderagent.bedrockModelId;
        expect(agentModel(commercialTemplate)).toMatch(/^global\./);
        expect(agentModel(govcloudTemplate)).toBe("");
        expect(agentModel(eusovereignTemplate)).toBe("");
    });

    test("rejects an enabled agent with no model id in the commercial partition", () => {
        // No default is assigned: the operator sees the field rather than inheriting a model.
        const run = loadConfig(commercialTemplate, "us-west-2", enableBlenderAgent(""));
        expect(run).toThrow(AGENT_MODEL_REQUIRED_MESSAGE);
    });

    test.each([
        [govcloudTemplate, "us-gov-west-1"],
        [eusovereignTemplate, "eusc-de-east-1"],
    ])(
        "rejects an enabled agent with the restricted template's blank model id (%#)",
        (base, region) => {
            const run = loadConfig(base, region, enableBlenderAgent());
            expect(run).toThrow(AGENT_MODEL_REQUIRED_MESSAGE);
        }
    );

    test("treats the UNDEFINED placeholder as no model id", () => {
        const run = loadConfig(govcloudTemplate, "us-gov-west-1", enableBlenderAgent("UNDEFINED"));
        expect(run).toThrow(AGENT_MODEL_REQUIRED_MESSAGE);
    });

    test.each(["global.openai.gpt-6-astra", "us.openai.gpt-6-astra"])(
        "rejects the commercial-only profile %s in GovCloud",
        (modelId) => {
            const run = loadConfig(govcloudTemplate, "us-gov-west-1", enableBlenderAgent(modelId));
            expect(run).toThrow(AGENT_MODEL_PREFIX_MESSAGE);
        }
    );

    test("accepts a model id offered in the partition", () => {
        const run = loadConfig(
            govcloudTemplate,
            "us-gov-west-1",
            enableBlenderAgent("us-gov.example.model-v1")
        );
        expect(run).not.toThrow(AGENT_MODEL_REQUIRED_MESSAGE);
        expect(run).not.toThrow(AGENT_MODEL_PREFIX_MESSAGE);
    });

    test("stays silent while the agent is disabled", () => {
        const run = loadConfig(govcloudTemplate, "us-gov-west-1", (c) => {
            enableBlenderAgent()(c);
            c.app.pipelines.useImageTo3d.models.blenderagent.enabled = false;
        });
        expect(run).not.toThrow(AGENT_MODEL_REQUIRED_MESSAGE);
    });
});

describe("ConfigBuilder mirror of the Blender agent model id rules", () => {
    /** The agent model-id rules the ConfigBuilder reports for a preset in `region`. */
    const agentModelRuleIds = (
        profile: "commercial" | "govcloud" | "eusovereign",
        region: string,
        modelId?: string,
        enabled = true
    ) => {
        const cfg = makeDefaultConfig(profile);
        cfg.env.region = region;
        enableBlenderAgent(modelId)(cfg);
        cfg.app.pipelines.useImageTo3d.models.blenderagent.enabled = enabled;
        return evaluateRules(cfg)
            .map((rule) => rule.id)
            .filter((id) => id === AGENT_MODEL_RULE_ID || id === AGENT_MODEL_PREFIX_RULE_ID);
    };

    test.each([
        ["commercial", "us-west-2", undefined, []],
        ["commercial", "us-west-2", "", [AGENT_MODEL_RULE_ID]],
        ["govcloud", "us-gov-west-1", undefined, [AGENT_MODEL_RULE_ID]],
        ["eusovereign", "eusc-de-east-1", undefined, [AGENT_MODEL_RULE_ID]],
        ["govcloud", "us-gov-west-1", "global.openai.gpt-6-astra", [AGENT_MODEL_PREFIX_RULE_ID]],
        ["govcloud", "us-gov-west-1", "us-gov.example.model-v1", []],
    ] as const)(
        "%s preset in %s with model id %j reports %j",
        (profile, region, modelId, expected) => {
            expect(agentModelRuleIds(profile, region, modelId)).toEqual(expected);
        }
    );

    test("stays silent while the agent is disabled", () => {
        expect(agentModelRuleIds("govcloud", "us-gov-west-1", undefined, false)).toEqual([]);
    });
});
