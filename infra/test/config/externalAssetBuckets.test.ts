/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import {
    validateExternalAssetBuckets,
    crossRegionExternalBuckets,
    crossRegionExternalBucketRegions,
    ConfigPublicAssetS3Buckets,
} from "../../config/config";

// Helper to build a bucket entry with sensible defaults.
const entry = (overrides: Partial<ConfigPublicAssetS3Buckets>): ConfigPublicAssetS3Buckets => ({
    bucketArn: "arn:aws:s3:::my-bucket",
    baseAssetsPrefix: "/",
    defaultSyncDatabaseId: "db",
    ...overrides,
});

describe("validateExternalAssetBuckets", () => {
    test("accepts an empty list", () => {
        expect(() => validateExternalAssetBuckets([], "aws", "123456789012")).not.toThrow();
    });

    test("accepts a single bucket at root", () => {
        expect(() =>
            validateExternalAssetBuckets([entry({ baseAssetsPrefix: "/" })], "aws", "123456789012")
        ).not.toThrow();
    });

    test("accepts the same bucket under multiple non-overlapping prefixes", () => {
        const buckets = [
            entry({ baseAssetsPrefix: "teamA/", defaultSyncDatabaseId: "a" }),
            entry({ baseAssetsPrefix: "teamB/", defaultSyncDatabaseId: "b" }),
        ];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "123456789012")).not.toThrow();
    });

    test("accepts the same bucket with consistent cross-account attributes", () => {
        const buckets = [
            entry({
                baseAssetsPrefix: "teamA/",
                bucketAccountId: "222222222222",
                bucketRegion: "us-east-1",
                bucketKmsKeyArn: "arn:aws:kms:us-east-1:222222222222:key/abc",
            }),
            entry({
                baseAssetsPrefix: "teamB/",
                bucketAccountId: "222222222222",
                bucketRegion: "us-east-1",
                bucketKmsKeyArn: "arn:aws:kms:us-east-1:222222222222:key/abc",
            }),
        ];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "111111111111")).not.toThrow();
    });

    test("rejects exact duplicate (same bucket, same prefix)", () => {
        const buckets = [
            entry({ baseAssetsPrefix: "teamA/" }),
            entry({ baseAssetsPrefix: "teamA/" }),
        ];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "123456789012")).toThrow(
            /overlapping baseAssetsPrefix/
        );
    });

    test("rejects nested overlapping prefixes on the same bucket", () => {
        const buckets = [
            entry({ baseAssetsPrefix: "data/" }),
            entry({ baseAssetsPrefix: "data/sub/" }),
        ];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "123456789012")).toThrow(
            /overlapping baseAssetsPrefix/
        );
    });

    test("rejects root prefix combined with any other prefix on the same bucket", () => {
        const buckets = [entry({ baseAssetsPrefix: "/" }), entry({ baseAssetsPrefix: "teamA/" })];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "123456789012")).toThrow(
            /overlapping baseAssetsPrefix/
        );
    });

    test("allows overlapping-looking prefixes on DIFFERENT buckets", () => {
        const buckets = [
            entry({ bucketArn: "arn:aws:s3:::bucket-one", baseAssetsPrefix: "data/" }),
            entry({ bucketArn: "arn:aws:s3:::bucket-two", baseAssetsPrefix: "data/sub/" }),
        ];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "123456789012")).not.toThrow();
    });

    test("treats sibling prefixes that share a string prefix as non-overlapping", () => {
        // "team/" and "teams/" — neither is a path-prefix of the other once the
        // trailing slash is considered, so they should be allowed.
        const buckets = [
            entry({ baseAssetsPrefix: "team/" }),
            entry({ baseAssetsPrefix: "teams/" }),
        ];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "123456789012")).not.toThrow();
    });

    test("rejects a bucket ARN whose partition does not match the deployment", () => {
        const buckets = [entry({ bucketArn: "arn:aws-us-gov:s3:::gov-bucket" })];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "123456789012")).toThrow(
            /does not match the deployment partition/
        );
    });

    test("rejects a malformed bucketAccountId", () => {
        const buckets = [entry({ bucketAccountId: "12345" })];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "123456789012")).toThrow(
            /must be a 12-digit AWS account ID/
        );
    });

    test("rejects inconsistent bucketAccountId across entries for the same bucket", () => {
        const buckets = [
            entry({ baseAssetsPrefix: "teamA/", bucketAccountId: "222222222222" }),
            entry({ baseAssetsPrefix: "teamB/", bucketAccountId: "333333333333" }),
        ];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "111111111111")).toThrow(
            /inconsistent bucketAccountId/
        );
    });

    test("rejects inconsistent bucketKmsKeyArn across entries for the same bucket", () => {
        const buckets = [
            entry({
                baseAssetsPrefix: "teamA/",
                bucketKmsKeyArn: "arn:aws:kms:us-east-1:222222222222:key/abc",
            }),
            entry({
                baseAssetsPrefix: "teamB/",
                bucketKmsKeyArn: "arn:aws:kms:us-east-1:222222222222:key/def",
            }),
        ];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "111111111111")).toThrow(
            /inconsistent bucketKmsKeyArn/
        );
    });

    test("treats empty/UNDEFINED prefix the same as root for overlap purposes", () => {
        const buckets = [entry({ baseAssetsPrefix: "" }), entry({ baseAssetsPrefix: "teamA/" })];
        expect(() => validateExternalAssetBuckets(buckets, "aws", "123456789012")).toThrow(
            /overlapping baseAssetsPrefix/
        );
    });

    // A bucket may be in another Region of the deployment's partition: its notification topics are
    // created there by a per-Region stack. What is rejected is a malformed Region, a Region in
    // another partition, and a cross-Region DEFAULT bucket, whose pipeline template data and run
    // I/O the deployment reads and writes from its own Region.
    describe("bucketRegion", () => {
        test("accepts a bucket in a different Region than the deployment", () => {
            const buckets = [entry({ bucketAccountId: "222222222222", bucketRegion: "eu-west-1" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).not.toThrow();
        });

        test("accepts a same-account bucket in a different Region", () => {
            const buckets = [entry({ bucketRegion: "us-west-2" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).not.toThrow();
        });

        test("accepts a bucket in the deployment Region", () => {
            const buckets = [entry({ bucketAccountId: "222222222222", bucketRegion: "us-east-1" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).not.toThrow();
        });

        test("accepts an omitted bucketRegion, which defaults to the deployment Region", () => {
            const buckets = [entry({ bucketAccountId: "222222222222" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).not.toThrow();
        });

        test("treats UNDEFINED bucketRegion as omitted", () => {
            const buckets = [entry({ bucketAccountId: "222222222222", bucketRegion: "UNDEFINED" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).not.toThrow();
        });

        test.each([
            ["us-east-1a", "an Availability Zone"],
            ["US-EAST-1", "upper case"],
            ["useast1", "no separators"],
            ["us-east", "no number"],
            ["us east 1", "spaces"],
        ])("rejects a malformed bucketRegion %s (%s)", (region) => {
            const buckets = [entry({ bucketRegion: region })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).toThrow(/is not a valid AWS Region name/);
        });

        test.each(["us-gov-west-1", "eusc-de-east-1", "cn-north-1", "us-iso-east-1"])(
            "accepts the well-formed restricted-partition Region %s in its own partition",
            (region) => {
                const partition = region.startsWith("us-gov")
                    ? "aws-us-gov"
                    : region.startsWith("eusc")
                    ? "aws-eusc"
                    : region.startsWith("cn")
                    ? "aws-cn"
                    : "aws-iso";
                const buckets = [
                    entry({ bucketArn: `arn:${partition}:s3:::my-bucket`, bucketRegion: region }),
                ];
                expect(() =>
                    validateExternalAssetBuckets(buckets, partition, "111111111111", region)
                ).not.toThrow();
            }
        );

        test("rejects a bucketRegion in another partition than the deployment", () => {
            const buckets = [entry({ bucketRegion: "us-gov-west-1" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).toThrow(
                /is in partition 'aws-us-gov' which does not match the deployment partition 'aws'/
            );
        });

        test("rejects a cross-Region bucket marked isDefault, naming both Regions", () => {
            const buckets = [entry({ bucketRegion: "ap-southeast-2", isDefault: true })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-west-2")
            ).toThrow(
                /is marked isDefault but is in 'ap-southeast-2' while the deployment is in 'us-west-2'[\s\S]*must be in the deployment Region/
            );
        });

        test("accepts a same-Region bucket marked isDefault", () => {
            const buckets = [entry({ bucketRegion: "us-west-2", isDefault: true })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-west-2")
            ).not.toThrow();
        });

        test("accepts a default bucket with no bucketRegion alongside a cross-Region non-default one", () => {
            const buckets = [
                entry({ bucketArn: "arn:aws:s3:::default-bucket", isDefault: true }),
                entry({
                    bucketArn: "arn:aws:s3:::remote-bucket",
                    bucketRegion: "eu-central-1",
                    defaultSyncDatabaseId: "remote",
                }),
            ];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-west-2")
            ).not.toThrow();
        });

        test("skips the default-bucket Region check when the deployment Region is unknown at synth", () => {
            const buckets = [entry({ bucketRegion: "eu-west-1", isDefault: true })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", undefined)
            ).not.toThrow();
        });
    });
});

describe("crossRegionExternalBuckets", () => {
    test("returns only entries whose Region is set and differs from the deployment Region", () => {
        const buckets = [
            entry({ bucketArn: "arn:aws:s3:::a" }),
            entry({ bucketArn: "arn:aws:s3:::b", bucketRegion: "us-east-1" }),
            entry({ bucketArn: "arn:aws:s3:::c", bucketRegion: "UNDEFINED" }),
            entry({ bucketArn: "arn:aws:s3:::d", bucketRegion: "eu-west-1" }),
            entry({
                bucketArn: "arn:aws:s3:::e",
                bucketRegion: "eu-west-1",
                baseAssetsPrefix: "x/",
            }),
            entry({ bucketArn: "arn:aws:s3:::f", bucketRegion: "ap-south-1" }),
        ];
        expect(crossRegionExternalBuckets(buckets, "us-east-1").map((b) => b.bucketArn)).toEqual([
            "arn:aws:s3:::d",
            "arn:aws:s3:::e",
            "arn:aws:s3:::f",
        ]);
        expect(crossRegionExternalBucketRegions(buckets, "us-east-1")).toEqual([
            "eu-west-1",
            "ap-south-1",
        ]);
    });

    test("handles an undefined list", () => {
        expect(crossRegionExternalBuckets(undefined, "us-east-1")).toEqual([]);
        expect(crossRegionExternalBucketRegions(undefined, "us-east-1")).toEqual([]);
    });
});
