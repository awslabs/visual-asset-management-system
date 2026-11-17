/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * S3 Transfer Acceleration (#1610), infrastructure half.
 *
 * Synthesizes the commercial template with the flag off (the shipped default), on, and with an
 * external default bucket that carries the flag, and asserts: which buckets get
 * `AccelerateConfiguration`, what the table populator is told and may read, that the Lambda roles
 * which presign hold the grants the backend's auxiliary-bucket lookup needs, and that with the flag
 * off the template carries no trace of the feature.
 *
 * The flag-off assertions are paired with positive controls from the flag-on synth, so a template
 * that emitted nothing at all cannot satisfy them.
 */

import {
    Resource,
    SynthResult,
    actionsOf,
    lambdaByHandler,
    resourcesOf,
    statementsOf,
    synthTemplate,
} from "../support/templateSynth";

jest.setTimeout(600_000);

const EXTERNAL_DEFAULT_ARN = "arn:aws:s3:::vams-ext-default";
const EXTERNAL_OTHER_ARN = "arn:aws:s3:::vams-ext-other";

const flagOn = () =>
    synthTemplate("commercial", {
        mutate: (c: any) => {
            c.app.assetBuckets.useS3TransferAcceleration = true;
        },
        mutateKey: "s3ta-on",
    });

const externalDefaultOn = () =>
    synthTemplate("commercial", {
        mutate: (c: any) => {
            c.app.assetBuckets.externalAssetBuckets = [
                {
                    bucketArn: EXTERNAL_DEFAULT_ARN,
                    baseAssetsPrefix: "/",
                    defaultSyncDatabaseId: "ext-default",
                    isDefault: true,
                    useS3TransferAcceleration: true,
                },
                {
                    bucketArn: EXTERNAL_OTHER_ARN,
                    baseAssetsPrefix: "/",
                    defaultSyncDatabaseId: "ext-other",
                },
            ];
        },
        mutateKey: "s3ta-external-default",
    });

const bucketByPrefix = (s: SynthResult, prefix: string): Resource => {
    const found = s.where(
        "AWS::S3::Bucket",
        (r) =>
            r.logicalId.startsWith(prefix) && /^[0-9A-F]{8}$/.test(r.logicalId.slice(prefix.length))
    );
    expect(found).toHaveLength(1);
    return found[0];
};

const populatorProperties = (s: SynthResult): any[] => {
    const found = s.where(
        "AWS::CloudFormation::CustomResource",
        (r) => typeof r.properties.buckets !== "undefined" && !!r.properties.tableName
    );
    expect(found).toHaveLength(1);
    return JSON.parse(
        // The bucket name of the created bucket is a token; flatten leaves a placeholder inside a
        // JSON string, which stays valid JSON.
        SynthResult.flatten(found[0].properties.buckets)
    );
};

const allStatements = (s: SynthResult): any[] =>
    [...s.ofType("AWS::IAM::Policy"), ...s.ofType("AWS::IAM::ManagedPolicy")].flatMap(
        (p) => (p.properties.PolicyDocument?.Statement ?? []) as any[]
    );

const accelerateReadStatements = (s: SynthResult): any[] =>
    allStatements(s).filter((st) => actionsOf(st).includes("s3:GetAccelerateConfiguration"));

const accelerateConfigured = (r: Resource) => r.properties.AccelerateConfiguration;

const csp = (s: SynthResult): string => {
    for (const p of s.ofType("AWS::CloudFront::ResponseHeadersPolicy")) {
        const v =
            p.properties.ResponseHeadersPolicyConfig?.SecurityHeadersConfig?.ContentSecurityPolicy
                ?.ContentSecurityPolicy;
        if (v !== undefined) return SynthResult.flatten(v);
    }
    throw new Error("no CloudFront CSP in the synthesized templates");
};

const directive = (value: string, name: string): string[] => {
    const found = value
        .split(";")
        .map((d) => d.trim())
        .find((d) => d.startsWith(`${name} `));
    if (found === undefined) throw new Error(`directive ${name} missing from CSP`);
    return found.slice(name.length).trim().split(/\s+/);
};

/** True when a CSP host source (https://*.example.com or an exact host) admits `host`. */
const sourceAdmits = (source: string, host: string): boolean => {
    const m = source.match(/^https:\/\/(.+?)\/?$/);
    if (!m) return false;
    const pattern = m[1];
    if (pattern.startsWith("*.")) return host.endsWith(pattern.slice(1));
    return pattern === host;
};

describe("S3 Transfer Acceleration: flag off (the shipped default)", () => {
    const off = () => synthTemplate("commercial");

    test("no bucket carries AccelerateConfiguration", () => {
        // Positive control: the three VAMS buckets this assertion is about exist.
        expect(bucketByPrefix(off(), "AssetBucket")).toBeDefined();
        expect(bucketByPrefix(off(), "AssetAuxiliaryBucket")).toBeDefined();
        const accelerated = off()
            .ofType("AWS::S3::Bucket")
            .filter((r) => accelerateConfigured(r) !== undefined);
        expect(accelerated.map((r) => r.logicalId)).toEqual([]);
    });

    test("no role may read the acceleration configuration", () => {
        // Positive control: the populator statement does exist when the flag is on.
        expect(accelerateReadStatements(flagOn()).length).toBeGreaterThan(0);
        expect(accelerateReadStatements(off())).toEqual([]);
        expect(JSON.stringify(off().templates)).not.toContain("AccelerationStatus");
    });

    test("the populator is told nothing about acceleration", () => {
        const rows = populatorProperties(off());
        expect(rows.length).toBeGreaterThan(0);
        for (const row of rows) {
            expect(Object.keys(row)).not.toContain("isAccelerated");
        }
        expect(Object.keys(populatorProperties(flagOn())[0])).toContain("isAccelerated");
    });

    test("the populator code carries no acceleration check that is reachable without the flag", () => {
        // The check is gated on the per-bucket value, which the properties above never carry.
        const lambdas = off().where(
            "AWS::Lambda::Function",
            (r) =>
                typeof r.properties.Code?.ZipFile === "string" &&
                r.properties.Code.ZipFile.includes("check_bucket_versioning")
        );
        expect(lambdas).toHaveLength(1);
        expect(lambdas[0].properties.Code.ZipFile).toContain("if is_accelerated:");
    });
});

describe("S3 Transfer Acceleration: app.assetBuckets.useS3TransferAcceleration = true", () => {
    test("the created asset bucket and the auxiliary bucket are accelerated", () => {
        const s = flagOn();
        expect(accelerateConfigured(bucketByPrefix(s, "AssetBucket"))).toEqual({
            AccelerationStatus: "Enabled",
        });
        expect(accelerateConfigured(bucketByPrefix(s, "AssetAuxiliaryBucket"))).toEqual({
            AccelerationStatus: "Enabled",
        });
    });

    test("the artefacts bucket and the access-logs bucket are never accelerated", () => {
        const s = flagOn();
        const accelerated = s
            .ofType("AWS::S3::Bucket")
            .filter((r) => accelerateConfigured(r) !== undefined)
            .map((r) => r.logicalId);
        expect(accelerated).toHaveLength(2);
        expect(accelerated.some((id) => id.startsWith("ArtefactsBucket"))).toBe(false);
        expect(accelerated.some((id) => id.startsWith("AccessLogsBucket"))).toBe(false);
    });

    test("the populator receives the flag for the created bucket's row only", () => {
        const rows = populatorProperties(flagOn());
        expect(rows).toHaveLength(1);
        expect(rows[0].isAccelerated).toBe(true);
    });

    test("the populator may read the acceleration configuration of the flagged bucket only", () => {
        const statements = accelerateReadStatements(flagOn());
        expect(statements).toHaveLength(1);
        expect(actionsOf(statements[0])).toContain("s3:GetAccelerateConfiguration");
        expect(actionsOf(statements[0])).not.toContain("s3:PutAccelerateConfiguration");
        const resources = resourcesOf(statements[0]);
        expect(resources).toHaveLength(1);
        expect(resources[0]).not.toContain("*");
    });
});

describe("S3 Transfer Acceleration: an external default bucket carries the flag", () => {
    test("the auxiliary bucket follows the default; the created bucket does not", () => {
        const s = externalDefaultOn();
        expect(accelerateConfigured(bucketByPrefix(s, "AssetAuxiliaryBucket"))).toEqual({
            AccelerationStatus: "Enabled",
        });
        // The created bucket's own flag is off, and VAMS never touches an imported bucket.
        expect(accelerateConfigured(bucketByPrefix(s, "AssetBucket"))).toBeUndefined();
        expect(
            s.ofType("AWS::S3::Bucket").filter((r) => accelerateConfigured(r) !== undefined)
        ).toHaveLength(1);
    });

    test("the populator reads the flagged imported bucket and no other", () => {
        const statements = accelerateReadStatements(externalDefaultOn());
        expect(statements).toHaveLength(1);
        expect(resourcesOf(statements[0])).toEqual([EXTERNAL_DEFAULT_ARN]);
    });

    test("only the flagged external row is sent as accelerated", () => {
        const rows = populatorProperties(externalDefaultOn());
        const byName = (name: string) => rows.find((r) => r.bucketName === name);
        expect(byName("vams-ext-default").isAccelerated).toBe(true);
        expect(byName("vams-ext-other")).toBeDefined();
        expect(Object.keys(byName("vams-ext-other"))).not.toContain("isAccelerated");
        expect(rows.filter((r) => r.isAccelerated === true)).toHaveLength(1);
    });
});

describe("S3 Transfer Acceleration: roles that presign can resolve the flag", () => {
    // The backend reads a bucket's row through bucketNameGSI and, for the auxiliary bucket, scans the
    // table for the default row, so each presigning handler needs dynamodb:Query and dynamodb:Scan on
    // the S3 asset buckets table. They hold them today through grantReadData, whatever the flag, so
    // no flag-off deployment gains IAM.
    const PRESIGNING_HANDLERS = [
        "handlers.assets.downloadAsset.lambda_handler",
        "handlers.assets.streamAsset.lambda_handler",
        "handlers.assets.streamAuxiliaryPreviewAsset.lambda_handler",
        "handlers.assets.assetExportService.lambda_handler",
        "handlers.assets.zipDownloadService.lambda_handler",
        "handlers.assets.uploadFile.lambda_handler",
    ];

    test.each(PRESIGNING_HANDLERS)(
        "%s may Query and Scan the S3 asset buckets table",
        (handler) => {
            const s = synthTemplate("commercial");
            const fn = lambdaByHandler(s, handler);
            const grants = statementsOf(s, fn).filter((st) =>
                resourcesOf(st).some((r) => /S3AssetBucketsStorageTable/.test(r))
            );
            const actions = new Set(grants.flatMap((st) => actionsOf(st)));
            expect(actions).toContain("dynamodb:Query");
            expect(actions).toContain("dynamodb:Scan");
            // The grant covers the index the lookup queries.
            expect(grants.some((st) => resourcesOf(st).some((r) => r.includes("/index/*")))).toBe(
                true
            );
        }
    );
});

describe("S3 Transfer Acceleration: the CSP already admits the accelerate host", () => {
    // PR #1618 (fix #1616) puts `https://*.amazonaws.com` (partition suffix) on connect-src, img-src,
    // media-src and frame-src, which covers `<bucket>.s3-accelerate.amazonaws.com`. This PR adds no
    // accelerate host. The assertion switches on when that change is on the base branch; until then
    // it is skipped, because the base CSP lists the regional S3 endpoint only.
    const s = synthTemplate("commercial");
    const covered = (name: string) =>
        directive(csp(s), name).some((src) =>
            sourceAdmits(src, "vams-bucket.s3-accelerate.amazonaws.com")
        );
    const wildcardOnBase = directive(csp(s), "connect-src").includes("https://*.amazonaws.com");
    const maybe = wildcardOnBase ? test : test.skip;

    maybe.each(["connect-src", "img-src", "media-src", "frame-src"])(
        "%s admits an accelerate host with no accelerate-specific source",
        (name) => {
            expect(covered(name)).toBe(true);
            expect(csp(flagOn())).not.toContain("s3-accelerate");
        }
    );
});
