/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Miris upload construct on the per-pipeline harness, with the asset table it reads and the
 * auxiliary bucket under the storage-stack construct id its claim-prefix suppression names. The KMS key
 * is absent unless asked for, as in the commercial template.
 */

import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as Config from "../../config/config";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import {
    MirisUploadConstruct,
    MirisUploadConstructProps,
} from "../../lib/nestedStacks/addon/miris/upload/constructs/mirisUpload-construct";
import { PipelineHarness, makePipelineHarness } from "../support/pipelineConstructHarness";

export const MIRIS_TEST_SECRET_ARN =
    "arn:aws:secretsmanager:us-east-1:123456789012:secret:test-miris-api-key-AbCdEf";

export const MIRIS_TEST_VIEWER_KEY = "test-placeholder-viewer-key-000000";

export const EXTERNAL_BUCKET_ARN = "arn:aws:s3:::ext-vams-assets";

export interface MirisUploadHarnessOptions {
    /** Give the storage the deployment's customer managed key (`useKmsCmkEncryption`). */
    kms?: boolean;
    /** Register an imported external asset bucket under this `baseAssetsPrefix`. */
    externalBucketPrefix?: string;
    mutate?: (c: Config.Config) => void;
}

export interface MirisUploadHarness extends PipelineHarness {
    construct: MirisUploadConstruct;
    assetAuxiliaryBucket: s3.Bucket;
}

export const makeMirisUploadHarness = (
    id: string,
    options: MirisUploadHarnessOptions = {}
): MirisUploadHarness => {
    const h = makePipelineHarness(id, (c) => {
        c.app.webUi.allowUnsafeEvalFeatures = true;
        c.app.addons.useMirisStreaming.enabled = true;
        c.app.addons.useMirisStreaming.viewerKey = MIRIS_TEST_VIEWER_KEY;
        c.app.addons.useMirisStreaming.apiKeySecretArn = MIRIS_TEST_SECRET_ARN;
        c.app.addons.useMirisStreaming.upload.enabled = true;
        c.app.addons.useMirisStreaming.upload.autoRegisterWithVAMS = false;
        c.app.useKmsCmkEncryption.enabled = options.kms === true;
        options.mutate?.(c);
    });

    const assetAuxiliaryBucket = new s3.Bucket(h.stack, "AssetAuxiliaryBucket");
    const storage = h.storage as any;
    storage.s3.assetAuxiliaryBucket = assetAuxiliaryBucket;
    storage.dynamo = {
        assetStorageTable: new dynamodb.Table(h.stack, "AssetStorageTable", {
            partitionKey: { name: "databaseId", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "assetId", type: dynamodb.AttributeType.STRING },
        }),
    };
    if (options.kms !== true) {
        storage.encryption = { kmsKey: undefined };
    }

    if (options.externalBucketPrefix !== undefined) {
        s3AssetBuckets.addS3AssetBucket(
            s3.Bucket.fromBucketAttributes(h.stack, "ExternalAssetBucket", {
                bucketArn: EXTERNAL_BUCKET_ARN,
            }),
            options.externalBucketPrefix,
            "ext-db"
        );
    }

    const props: MirisUploadConstructProps = {
        config: h.config,
        storageResources: h.storage,
        vpc: h.vpc,
        pipelineSubnets: h.subnets,
        pipelineSecurityGroups: h.securityGroups,
        lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
    };
    const construct = new MirisUploadConstruct(h.stack, "MirisUploadPipeline", props);

    return { ...h, construct, assetAuxiliaryBucket };
};

/**
 * Full-app mutation: the add-on in static mode with its upload pipeline and no other pipeline, shared
 * by the add-on and VPC placement suites so the synth cache builds it once.
 */
export function mirisStaticWithUpload(c: any) {
    c.app.webUi.allowUnsafeEvalFeatures = true;
    Object.assign(c.app.addons.useMirisStreaming, {
        enabled: true,
        viewerKeyMode: "static",
        viewerKey: MIRIS_TEST_VIEWER_KEY,
        viewerKeyTtlMinutes: 7,
        apiKeySecretArn: MIRIS_TEST_SECRET_ARN,
    });
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.addVpcEndpoints = true;
    c.app.useAlb.enabled = false;
    c.app.useCloudFront.enabled = true;
    for (const entry of Object.values<any>(c.app.pipelines)) {
        if (entry && typeof entry === "object" && "enabled" in entry) {
            entry.enabled = false;
            if (entry.autoRegisterWithVAMS !== undefined) entry.autoRegisterWithVAMS = false;
        }
    }
    c.app.pipelines.useRapidPipeline.useEcs.enabled = false;
    c.app.pipelines.useRapidPipeline.useEks.enabled = false;
    c.app.addons.useMirisStreaming.upload.enabled = true;
    c.app.addons.useMirisStreaming.upload.autoRegisterWithVAMS = false;
}
