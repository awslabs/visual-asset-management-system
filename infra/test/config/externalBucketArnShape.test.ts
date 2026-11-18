/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An external asset bucket ARN names exactly one bucket (#1650).
 *
 * The bucket grants and the CDK Nag suppression on the bucket's objects are built from
 * `externalAssetBuckets[].bucketArn`. A `*` or `?` in the name would widen the grant to every matching
 * bucket, and a key prefix or trailing path is not a bucket ARN at all, so `getConfig()` (through
 * `validateExternalAssetBuckets()`) rejects them before synth. The ConfigBuilder carries the same rule
 * by hand (`configBuilderSync.test.ts` guards only the schema and defaults), so both are checked here
 * with the same cases.
 */

import { ConfigPublicAssetS3Buckets, validateExternalAssetBuckets } from "../../config/config";
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import { evaluateRules } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const entry = (bucketArn: string): ConfigPublicAssetS3Buckets => ({
    bucketArn,
    baseAssetsPrefix: "/",
    defaultSyncDatabaseId: "db",
});

const REJECTED: [string, string][] = [
    ["a wildcard bucket", "arn:aws:s3:::*"],
    ["a wildcard in the name", "arn:aws:s3:::my-*"],
    ["a single-character wildcard", "arn:aws:s3:::my-?ucket"],
    ["a wildcard object ARN", "arn:aws:s3:::*/*"],
    ["a trailing key prefix", "arn:aws:s3:::my-bucket/prefix"],
    ["a trailing slash", "arn:aws:s3:::my-bucket/"],
    ["a missing ':::'", "arn:aws:s3:my-bucket"],
    ["an empty name", "arn:aws:s3:::"],
    ["a space in the name", "arn:aws:s3:::my bucket"],
    ["a name over 255 characters", `arn:aws:s3:::${"a".repeat(256)}`],
    ["a non-S3 ARN", "arn:aws:iam::123456789012:role/my-bucket"],
    ["a bare bucket name", "my-bucket"],
];

const ACCEPTED: [string, string, string][] = [
    ["a modern name", "arn:aws:s3:::my-bucket.assets-1", "aws"],
    ["a legacy name with uppercase and underscore", "arn:aws:s3:::Legacy_Bucket", "aws"],
    ["a 255-character name", `arn:aws:s3:::${"a".repeat(255)}`, "aws"],
    ["a GovCloud bucket", "arn:aws-us-gov:s3:::gov-bucket", "aws-us-gov"],
    ["an EU Sovereign bucket", "arn:aws-eusc:s3:::eusc-bucket", "aws-eusc"],
];

describe("getConfig() external bucket ARN shape", () => {
    test.each(REJECTED)("rejects %s", (_label, bucketArn) => {
        expect(() =>
            validateExternalAssetBuckets([entry(bucketArn)], "aws", "123456789012")
        ).toThrow(`Configuration Error: external bucket ARN ${bucketArn} is not valid.`);
    });

    test("names the rule in the error", () => {
        expect(() =>
            validateExternalAssetBuckets([entry("arn:aws:s3:::*")], "aws", "123456789012")
        ).toThrow(
            "It must be arn:<partition>:s3:::<bucket-name> naming one bucket, with no wildcard (* or ?), no key prefix and no trailing path; the name is 1 to 255 characters of letters, digits, '.', '_' and '-'. List each bucket by name."
        );
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

describe("ConfigBuilder mirrors the external bucket ARN shape rule", () => {
    const raised = (bucketArn: string, region = "us-east-1"): boolean => {
        const cfg: any = makeDefaultConfig("commercial");
        cfg.env.region = region;
        cfg.app.assetBuckets.externalAssetBuckets = [entry(bucketArn)];
        return evaluateRules(cfg).some((issue) => issue.id === "externalbucket-arn-shape");
    };

    test.each(REJECTED)("raises the rule for %s", (_label, bucketArn) => {
        expect(raised(bucketArn)).toBe(true);
    });

    test.each([
        ["a modern name", "arn:aws:s3:::my-bucket.assets-1", "us-east-1"],
        ["a legacy name with uppercase and underscore", "arn:aws:s3:::Legacy_Bucket", "us-east-1"],
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
