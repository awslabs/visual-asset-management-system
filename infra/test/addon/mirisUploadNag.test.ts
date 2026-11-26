/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Miris upload construct synthesizes with no unsuppressed AwsSolutions finding in each storage
 * shape it can meet: the created asset bucket, the customer managed key, and an imported external
 * bucket (whose ARN is a config literal) under a prefix and at its root.
 */

import * as cdk from "aws-cdk-lib";
import { Annotations, Match } from "aws-cdk-lib/assertions";
import { AwsSolutionsChecks } from "cdk-nag";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { MirisUploadHarnessOptions, makeMirisUploadHarness } from "./mirisUploadHarness";

test.each<[string, MirisUploadHarnessOptions]>([
    ["the created asset bucket", {}],
    ["a customer managed key", { kms: true }],
    ["an external bucket under a prefix", { externalBucketPrefix: "vams/data/" }],
    ["an external bucket at its root", { externalBucketPrefix: "/" }],
])("no unsuppressed AwsSolutions error with %s", (label, options) => {
    const h = makeMirisUploadHarness(`MirisUploadNag${label.replace(/\W/g, "")}`, options);
    // `logIgnores` records each applied suppression as an info annotation, for the control below.
    cdk.Aspects.of(h.stack).add(
        new AwsSolutionsChecks({ verbose: true, logIgnores: true, reports: false })
    );
    const annotations = Annotations.fromStack(h.stack);
    const inConstruct = (m: { id: string }) => m.id.includes("/MirisUploadPipeline/");

    // Controls: the storage shape reached the construct, and the aspect visited it.
    expect(h.storage.encryption.kmsKey !== undefined).toBe(options.kms === true);
    expect(s3AssetBuckets.getS3AssetBucketRecords()).toHaveLength(
        options.externalBucketPrefix === undefined ? 1 : 2
    );
    expect(
        annotations.findInfo("*", Match.stringLikeRegexp("AwsSolutions-")).filter(inConstruct)
            .length
    ).toBeGreaterThan(0);

    // AwsSolutions-L1 is left to the core stack, which suppresses it on every Python function.
    const errors = annotations
        .findError("*", Match.stringLikeRegexp("AwsSolutions-"))
        .filter(inConstruct)
        .map((m) => `${m.id}: ${String(m.entry.data).split("\n")[0]}`)
        .filter((line) => !line.includes(": AwsSolutions-L1:"));
    expect(errors).toEqual([]);
});
