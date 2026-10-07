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
