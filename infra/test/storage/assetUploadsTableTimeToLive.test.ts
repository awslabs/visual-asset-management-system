/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The asset uploads table expires its records through DynamoDB time to live on `expiresAt`.
 *
 * Every writer of an upload record (upload initialize, workflow output) sets `expiresAt` in epoch
 * seconds, so the table's TTL removes a record once it expires, whether or not the upload was ever
 * completed. The storage builder is synthesized on its own, as artefactsBucketPrune.test.ts does.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import commercialTemplate from "../../config/config.template.commercial.json";
import { storageResourcesBuilder } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { ResourceNameRegistry } from "../../lib/nestedStacks/resourceNames/resourceNameRegistry";
import { newTestApp } from "../support/testApp";

const mockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

describe("asset uploads table time to live", () => {
    let uploadsTables: Record<string, any>[];
    let tableCount: number;

    beforeAll(() => {
        const config = mockConfig();
        Service.SetConfig(config);

        const app = newTestApp();
        const stack = new cdk.Stack(app, "S", {
            env: { account: config.env.account, region: config.env.region },
        });
        const layer = lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "CommonLayer",
            "arn:aws:lambda:us-east-1:123456789012:layer:vams-common:1"
        ) as LayerVersion;

        storageResourcesBuilder(
            stack,
            config,
            layer,
            undefined as unknown as ec2.IVpc,
            [],
            new ResourceNameRegistry()
        );
        const tables = Template.fromStack(stack).findResources("AWS::DynamoDB::Table");
        tableCount = Object.keys(tables).length;
        uploadsTables = Object.entries(tables)
            .filter(([logicalId]) => logicalId.startsWith("AssetUploadsStorageTable"))
            .map(([, resource]) => resource);
    });

    test("the storage builder emits exactly one asset uploads table among its tables", () => {
        expect(tableCount).toBeGreaterThan(1);
        expect(uploadsTables).toHaveLength(1);
    });

    test("the asset uploads table expires records on expiresAt", () => {
        expect(uploadsTables[0].Properties.TimeToLiveSpecification).toEqual({
            AttributeName: "expiresAt",
            Enabled: true,
        });
    });

    test("the asset uploads table keeps its key schema", () => {
        // TimeToLiveSpecification updates a table in place; a key-schema change would replace it.
        expect(uploadsTables[0].Properties.KeySchema).toEqual([
            { AttributeName: "uploadId", KeyType: "HASH" },
            { AttributeName: "assetId", KeyType: "RANGE" },
        ]);
    });
});
