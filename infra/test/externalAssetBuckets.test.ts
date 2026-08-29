/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { validateExternalAssetBuckets, ConfigPublicAssetS3Buckets } from "../config/config";

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

    // Amazon S3 requires an event-notification destination to be in the same region as the
    // bucket, and VAMS creates its notification topics in the deployment region. Without this
    // guard the mismatch surfaces only as a PutBucketNotificationConfiguration InvalidArgument
    // from a custom resource, well into the deploy.
    describe("bucketRegion must match the deployment region", () => {
        test("rejects a bucket in a different region than the deployment", () => {
            const buckets = [entry({ bucketAccountId: "222222222222", bucketRegion: "eu-west-1" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).toThrow(/does not match the deployment region/);
        });

        test("accepts a bucket in the deployment region", () => {
            const buckets = [entry({ bucketAccountId: "222222222222", bucketRegion: "us-east-1" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).not.toThrow();
        });

        test("accepts an omitted bucketRegion, which defaults to the deployment region", () => {
            const buckets = [entry({ bucketAccountId: "222222222222" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).not.toThrow();
        });

        test("treats UNDEFINED bucketRegion as omitted rather than as a mismatch", () => {
            const buckets = [entry({ bucketAccountId: "222222222222", bucketRegion: "UNDEFINED" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-east-1")
            ).not.toThrow();
        });

        test("skips the check when the deployment region is unknown at synth", () => {
            const buckets = [entry({ bucketAccountId: "222222222222", bucketRegion: "eu-west-1" })];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", undefined)
            ).not.toThrow();
        });

        test("names both regions so the operator can see which to change", () => {
            const buckets = [
                entry({ bucketAccountId: "222222222222", bucketRegion: "ap-southeast-2" }),
            ];
            expect(() =>
                validateExternalAssetBuckets(buckets, "aws", "111111111111", "us-west-2")
            ).toThrow(/ap-southeast-2[\s\S]*us-west-2/);
        });
    });
});
