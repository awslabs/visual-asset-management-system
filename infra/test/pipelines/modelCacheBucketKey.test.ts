/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Cosmos and GR00T model-cache buckets use an S3 Bucket Key when they are encrypted with the
 * shared customer managed key. Without one, every object request against a model checkpoint makes
 * its own AWS KMS request. With the key off the buckets use SSE-S3, where a Bucket Key does not
 * apply, so the encryption rule is left exactly as it was.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Template } from "aws-cdk-lib/assertions";
import { CosmosCommonConstruct } from "../../lib/nestedStacks/pipelines/genAi/nvidia/cosmos/constructs/cosmosCommon-construct";
import { Gr00tCommonConstruct } from "../../lib/nestedStacks/pipelines/genAi/nvidia/gr00t/constructs/gr00tCommon-construct";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { makePipelineHarness } from "../support/pipelineConstructHarness";

const FAMILIES = [
    { name: "cosmos", Construct: CosmosCommonConstruct },
    { name: "gr00t", Construct: Gr00tCommonConstruct },
];

function modelCacheEncryption(name: string, withKmsKey: boolean): any[] {
    const h = makePipelineHarness(`ModelCache-${name}-${withKmsKey ? "cmk" : "sse"}`);
    const storage = withKmsKey
        ? h.storage
        : ({ ...h.storage, encryption: { kmsKey: undefined } } as unknown as storageResources);
    const family = FAMILIES.find((f) => f.name === name)!;
    new family.Construct(h.stack, "Common", {
        config: h.config,
        vpc: h.vpc as ec2.IVpc,
        subnets: h.subnets,
        securityGroups: h.securityGroups,
        storageResources: storage,
    } as any);
    const buckets = Template.fromStack(h.stack as cdk.Stack).findResources("AWS::S3::Bucket");
    return Object.entries(buckets)
        .filter(([logicalId]) => logicalId.startsWith("Common"))
        .map(([, bucket]: [string, any]) => bucket.Properties.BucketEncryption);
}

describe.each(FAMILIES.map((f) => f.name))("%s model-cache bucket", (name) => {
    test("uses an S3 Bucket Key with the customer managed key", () => {
        const encryption = modelCacheEncryption(name, true);
        expect(encryption).toHaveLength(1);
        const rule = encryption[0].ServerSideEncryptionConfiguration[0];
        expect(rule.ServerSideEncryptionByDefault.SSEAlgorithm).toBe("aws:kms");
        expect(rule.BucketKeyEnabled).toBe(true);
    });

    test("keeps SSE-S3 with no Bucket Key setting when the key is off", () => {
        const encryption = modelCacheEncryption(name, false);
        expect(encryption).toHaveLength(1);
        const rule = encryption[0].ServerSideEncryptionConfiguration[0];
        expect(rule.ServerSideEncryptionByDefault.SSEAlgorithm).toBe("AES256");
        expect(rule).not.toHaveProperty("BucketKeyEnabled");
    });
});
