/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An external asset bucket ARN names exactly one bucket (#1650).
 *
 * The bucket grants and the CDK Nag suppression on the bucket's objects are built from
 * `externalAssetBuckets[].bucketArn`. A `*` or `?` in the name would widen the grant to every matching
 * bucket. A key prefix or trailing slash is dropped silently by the CDK bucket import while the bucket
 * registry keys on the full ARN, so the prefix belongs in `baseAssetsPrefix`. `getConfig()` (through
 * `validateExternalAssetBuckets()`) rejects these before synth, and the accepted names are the ones the
 * CDK import accepts, so an out-of-range name fails with the configuration message rather than a later
 * CDK one. The ConfigBuilder carries the same rule by hand (`configBuilderSync.test.ts` guards only the
 * schema and defaults), so both are checked here with the same cases.
 */

import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as fs from "fs";
import * as path from "path";
import {
    ConfigPublicAssetS3Buckets,
    EXTERNAL_BUCKET_ARN_PATTERN,
    validateExternalAssetBuckets,
} from "../../config/config";
import { newTestApp } from "../support/testApp";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const entry = (bucketArn: unknown): ConfigPublicAssetS3Buckets =>
    ({
        bucketArn,
        baseAssetsPrefix: "/",
        defaultSyncDatabaseId: "db",
    } as unknown as ConfigPublicAssetS3Buckets);

const FIX_HINT =
    "List the bucket as arn:<partition>:s3:::<bucket-name> and set the key prefix in baseAssetsPrefix.";

const REJECTED: [string, unknown][] = [
    ["a wildcard bucket", "arn:aws:s3:::*"],
    ["a wildcard in the name", "arn:aws:s3:::my-*"],
    ["a single-character wildcard", "arn:aws:s3:::my-?ucket"],
    ["a wildcard object ARN", "arn:aws:s3:::*/*"],
    ["a trailing key prefix", "arn:aws:s3:::my-bucket/prefix"],
    ["a trailing slash", "arn:aws:s3:::my-bucket/"],
    ["a missing ':::'", "arn:aws:s3:my-bucket"],
    ["an empty name", "arn:aws:s3:::"],
    ["a space in the name", "arn:aws:s3:::my bucket"],
    ["a trailing space", "arn:aws:s3:::my-bucket "],
    ["a trailing newline", "arn:aws:s3:::my-bucket\n"],
    ["a 2-character name", "arn:aws:s3:::ab"],
    ["a 64-character name", `arn:aws:s3:::${"a".repeat(64)}`],
    ["a name starting with '-'", "arn:aws:s3:::-my-bucket"],
    ["a name ending with '.'", "arn:aws:s3:::my-bucket."],
    ["consecutive periods", "arn:aws:s3:::my..bucket"],
    ["a period next to a dash", "arn:aws:s3:::my.-bucket"],
    ["an IP-shaped name", "arn:aws:s3:::192.168.1.1"],
    ["a non-S3 ARN", "arn:aws:iam::123456789012:role/my-bucket"],
    ["a bare bucket name", "my-bucket"],
    ["an undefined value", undefined],
    ["a null value", null],
    ["a number", 42],
    ["an object", { arn: "arn:aws:s3:::my-bucket" }],
];

const ACCEPTED: [string, string, string][] = [
    ["a modern name", "arn:aws:s3:::my-bucket.assets-1", "aws"],
    ["a legacy name with uppercase and underscore", "arn:aws:s3:::Legacy_Bucket", "aws"],
    ["a 3-character name", "arn:aws:s3:::abc", "aws"],
    ["a 63-character name", `arn:aws:s3:::${"a".repeat(63)}`, "aws"],
    ["a GovCloud bucket", "arn:aws-us-gov:s3:::gov-bucket", "aws-us-gov"],
    ["an EU Sovereign bucket", "arn:aws-eusc:s3:::eusc-bucket", "aws-eusc"],
];

describe("getConfig() external bucket ARN shape", () => {
    test.each(REJECTED)("rejects %s", (_label, bucketArn) => {
        expect(() =>
            validateExternalAssetBuckets([entry(bucketArn)], "aws", "123456789012")
        ).toThrow(
            `Configuration Error: external bucket ARN ${String(
                JSON.stringify(bucketArn)
            )} is not valid.`
        );
    });

    test("names the rule and the fix in the error", () => {
        expect(() =>
            validateExternalAssetBuckets(
                [entry("arn:aws:s3:::my-bucket/prefix/")],
                "aws",
                "123456789012"
            )
        ).toThrow(
            "It must name one bucket as arn:<partition>:s3:::<bucket-name>, with no wildcard (* or ?) and nothing after the name; the name is 3 to 63 characters of letters, digits, '.', '_' and '-', starts and ends with a letter or digit, has no '..', '.-' or '-.', and is not shaped like an IP address. " +
                FIX_HINT
        );
    });

    test("shows a trailing newline in the error", () => {
        expect(() =>
            validateExternalAssetBuckets([entry("arn:aws:s3:::my-bucket\n")], "aws", "123456789012")
        ).toThrow('external bucket ARN "arn:aws:s3:::my-bucket\\n" is not valid.');
    });

    test("is checked before the partition, so a malformed ARN gets one message", () => {
        expect(() =>
            validateExternalAssetBuckets([entry("arn:aws-us-gov:s3:::*")], "aws", "123456789012")
        ).toThrow(/is not valid/);
    });

    test.each(ACCEPTED)("accepts %s", (_label, bucketArn, partition) => {
        expect(() =>
            validateExternalAssetBuckets([entry(bucketArn)], partition, "123456789012")
        ).not.toThrow();
    });
});

describe("the accepted bucket names are the ones the CDK bucket import accepts", () => {
    // The import storageBuilder and the cross-Region notification stack use for every external bucket.
    const cdkImportAccepts = (bucketArn: string): boolean => {
        const stack = new cdk.Stack(newTestApp(), "ImportProbe");
        try {
            s3.Bucket.fromBucketAttributes(stack, "Bucket", { bucketArn });
            return true;
        } catch {
            return false;
        }
    };

    const names = [
        "abc",
        "ab",
        "a".repeat(63),
        "a".repeat(64),
        "Legacy_Bucket",
        "my.bucket-1",
        "-abc",
        "abc-",
        "_abc",
        "abc.",
        "a..b",
        "a.-b",
        "a-.b",
        "192.168.1.1",
        "1.2.3",
        "my bucket",
        "my*bucket",
        "my?bucket",
    ];

    test.each(names)("agrees on %s", (name) => {
        const bucketArn = `arn:aws:s3:::${name}`;
        expect(EXTERNAL_BUCKET_ARN_PATTERN.test(bucketArn)).toBe(cdkImportAccepts(bucketArn));
    });
});

describe("ConfigBuilder mirrors the external bucket ARN shape rule", () => {
    const issueIds = (bucketArn: unknown, region = "us-east-1"): string[] => {
        const cfg: any = makeDefaultConfig("commercial");
        cfg.env.region = region;
        cfg.app.assetBuckets.externalAssetBuckets = [entry(bucketArn)];
        return evaluateRules(cfg).map((issue) => String(issue.id));
    };
    const raised = (bucketArn: unknown, region = "us-east-1"): boolean =>
        issueIds(bucketArn, region).includes("externalbucket-arn-shape");

    test("carries the same pattern as config.ts", () => {
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation.ts"
            ),
            "utf-8"
        );
        expect(source).toContain(`/${EXTERNAL_BUCKET_ARN_PATTERN.source}/`);
    });

    test.each(REJECTED)("raises the rule for %s", (_label, bucketArn) => {
        expect(raised(bucketArn)).toBe(true);
    });

    test("ends with the fix", () => {
        const cfg: any = makeDefaultConfig("commercial");
        cfg.app.assetBuckets.externalAssetBuckets = [entry("arn:aws:s3:::my-bucket/")];
        const issue = evaluateRules(cfg).find((i) => i.id === "externalbucket-arn-shape");
        expect(String(issue?.message)).toContain(FIX_HINT);
    });

    test("a malformed ARN in another partition raises only the shape rule", () => {
        const ids = issueIds("arn:aws-us-gov:s3:::my-bucket/prefix", "us-east-1");
        expect(ids).toContain("externalbucket-arn-shape");
        expect(ids).not.toContain("externalbucket-arn-partition-matches-deployment");
    });

    test("a valid ARN in another partition still raises the partition rule", () => {
        const ids = issueIds("arn:aws-us-gov:s3:::my-bucket", "us-east-1");
        expect(ids).toContain("externalbucket-arn-partition-matches-deployment");
        expect(ids).not.toContain("externalbucket-arn-shape");
    });

    test.each([
        ["a modern name", "arn:aws:s3:::my-bucket.assets-1", "us-east-1"],
        ["a legacy name with uppercase and underscore", "arn:aws:s3:::Legacy_Bucket", "us-east-1"],
        ["a 63-character name", `arn:aws:s3:::${"a".repeat(63)}`, "us-east-1"],
        ["a GovCloud bucket", "arn:aws-us-gov:s3:::gov-bucket", "us-gov-west-1"],
        ["an EU Sovereign bucket", "arn:aws-eusc:s3:::eusc-bucket", "eusc-de-east-1"],
    ])("does not raise it for %s", (_label, bucketArn, region) => {
        expect(raised(bucketArn, region)).toBe(false);
    });

    test("the defaults raise none", () => {
        const cfg: any = makeDefaultConfig("commercial");
        expect(evaluateRules(cfg).map((issue) => issue.id)).not.toContain(
            "externalbucket-arn-shape"
        );
    });
});
