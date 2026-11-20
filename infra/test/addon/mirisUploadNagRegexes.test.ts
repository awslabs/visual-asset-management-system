/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Miris upload pipeline's IAM5 `appliesTo` patterns, compiled as cdk-nag compiles them, accept the
 * grants they were written for and no wildcard ARN.
 */

import {
    MIRIS_UPLOAD_BATCH_JOB_DEFINITION_REGEX,
    MIRIS_UPLOAD_BATCH_JOB_REGEX,
    MIRIS_UPLOAD_JOB_ROLE_OBJECT_REGEXES,
} from "../../lib/nestedStacks/addon/miris/upload/constructs/mirisUpload-construct";
import {
    MIRIS_AMBIGUOUS_ROOT_REPORT_REGEXES,
    MIRIS_CLAIM_PREFIX_OBJECT_REGEX,
    MIRIS_DEPENDENCY_MANIFEST_PREFIX_OBJECT_REGEX,
} from "../../lib/nestedStacks/addon/miris/upload/lambdaBuilder/mirisUploadFunctions";

/** cdk-nag's NagSuppressionHelper.toRegEx: the body between the first and last slash, then flags. */
const compile = (pattern: string): RegExp => {
    const m = pattern.match(/\/(.*)\/(.*)?/);
    expect(m).not.toBeNull();
    return new RegExp(m![1], m![2] || "");
};

const matches = (pattern: string, finding: string): boolean => compile(pattern).test(finding);

const ALL_PATTERNS: [string, string][] = [
    ...MIRIS_UPLOAD_JOB_ROLE_OBJECT_REGEXES.map(
        (p, i) => [`job role object ${i}`, p] as [string, string]
    ),
    ["claim prefix object", MIRIS_CLAIM_PREFIX_OBJECT_REGEX],
    ["manifest prefix object", MIRIS_DEPENDENCY_MANIFEST_PREFIX_OBJECT_REGEX],
    ...MIRIS_AMBIGUOUS_ROOT_REPORT_REGEXES.map(
        (p, i) => [`ambiguous_root report ${i}`, p] as [string, string]
    ),
    ["batch job definition", MIRIS_UPLOAD_BATCH_JOB_DEFINITION_REGEX],
    ["batch job", MIRIS_UPLOAD_BATCH_JOB_REGEX],
];

const WILDCARD_FINDINGS = [
    "arn:aws:s3:::*/*",
    "arn:*:s3:::*/*",
    "arn:aws:s3:::bucket*/*",
    "arn:aws:s3:::*/vams/*",
    "arn:aws:s3:::bucket/vams?/*",
    "arn:aws:s3:::bucket/?/*",
    "<AssetAuxiliaryBucketXYZ.Arn>/*",
    "<AssetAuxiliaryBucket1A2B3C4D.Arn>/*",
    "<AssetAuxiliaryBucket1A2B3C4D.Arn>/locks/*",
    "<AssetAuxiliaryBucket1A2B3C4D.Arn>/locks/miris-upload/../*",
    "<LoggingBucketPolicyXYZ.Arn>/*",
    "<AssetBucket1A2B3C4D.Arn>/locks/miris-upload/*",
    "<AssetBucket1A2B3C4D.Arn>/pipelines/miris-upload/manifests/*",
    "<AssetAuxiliaryBucket1A2B3C4D.Arn>/pipelines/*",
    "<AssetAuxiliaryBucket1A2B3C4D.Arn>/pipelines/miris-upload/*",
    "arn:aws:batch:*:*:job/*",
    "arn:aws:batch:*:*:job-definition/*",
    "arn:<AWS::Partition>:batch:*:123456789012:job/*",
    "*",
].map((arn) => `Resource::${arn}`);

describe("Miris upload IAM5 suppression patterns", () => {
    test.each(ALL_PATTERNS)("%s is anchored and accepts no wildcard ARN", (_, pattern) => {
        // A regex string without its leading slash parses as a catch-all.
        expect(pattern.startsWith("/^Resource::")).toBe(true);
        expect(pattern).toMatch(/\$\/g$/);
        for (const finding of WILDCARD_FINDINGS) {
            expect([finding, matches(pattern, finding)]).toEqual([finding, false]);
        }
    });

    test.each([
        ...[
            "<AssetBucket1D025086.Arn>/*",
            "<AssetBucket1D025086.Arn>/pipelines/*",
            "arn:aws:s3:::ext-vams-assets/*",
            "arn:aws:s3:::ext-vams-assets/pipelines/*",
            "arn:aws:s3:::ext-vams-assets/vams/data/*",
            "arn:aws:s3:::ext-vams-assets/vams/data/pipelines/*",
            "arn:aws-us-gov:s3:::ext.vams-assets/vams/data/*",
        ].map((arn) => [MIRIS_UPLOAD_JOB_ROLE_OBJECT_REGEXES, arn, true]),
        // The storage nested stack's bucket keeps its construct id as the logical-id stem.
        [
            [MIRIS_CLAIM_PREFIX_OBJECT_REGEX],
            "<AssetAuxiliaryBucket81B76230.Arn>/locks/miris-upload/*",
            true,
        ],
        [
            [MIRIS_CLAIM_PREFIX_OBJECT_REGEX],
            "<StorageAssetAuxiliaryBucketA1B2C3D4.Arn>/locks/miris-upload/*",
            true,
        ],
        [
            [MIRIS_CLAIM_PREFIX_OBJECT_REGEX],
            "<AssetAuxiliaryBucket81B76230.Arn>/locks/other/*",
            false,
        ],
        [
            [MIRIS_DEPENDENCY_MANIFEST_PREFIX_OBJECT_REGEX],
            "<AssetAuxiliaryBucket81B76230.Arn>/pipelines/miris-upload/manifests/*",
            true,
        ],
        [
            [MIRIS_DEPENDENCY_MANIFEST_PREFIX_OBJECT_REGEX],
            "<AssetAuxiliaryBucket81B76230.Arn>/pipelines/miris-upload/manifests/../*",
            false,
        ],
        ...[
            ["<AssetBucket1D025086.Arn>/pipelines/*/ambiguous_root.json", true],
            ["arn:aws:s3:::ext-vams-assets/vams/data/pipelines/*/ambiguous_root.json", true],
            ["<AssetBucket1D025086.Arn>/pipelines/*", false],
            ["<AssetBucket1D025086.Arn>/*/ambiguous_root.json", false],
            ["arn:aws:s3:::ext-vams-assets/pipelines/*/other.json", false],
        ].map(([arn, accepted]) => [MIRIS_AMBIGUOUS_ROOT_REPORT_REGEXES, arn, accepted]),
        [[MIRIS_UPLOAD_BATCH_JOB_REGEX], "arn:aws:batch:us-east-1:123456789012:job/*", true],
        [
            [MIRIS_UPLOAD_BATCH_JOB_REGEX],
            "arn:aws-us-gov:batch:us-gov-west-1:123456789012:job/*",
            true,
        ],
        [
            [MIRIS_UPLOAD_BATCH_JOB_DEFINITION_REGEX],
            "arn:<AWS::Partition>:batch:us-east-1:123456789012:job-definition/*",
            true,
        ],
        [
            [MIRIS_UPLOAD_BATCH_JOB_DEFINITION_REGEX],
            "arn:<AWS::Partition>:batch:<AWS::Region>:<AWS::AccountId>:job-definition/*",
            true,
        ],
    ] as [string[], string, boolean][])(
        "the patterns judge the synthesized grant %#: %s",
        (patterns, arn, accepted) => {
            expect(patterns.some((p) => matches(p, `Resource::${arn}`))).toBe(accepted);
        }
    );
});
