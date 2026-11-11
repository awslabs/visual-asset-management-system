/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * An add-on table built with addonDynamoDbTableDefaults() carries the properties the core tables
 * share: billing mode, point-in-time recovery, contributor insights, server-side encryption and the
 * deletion and update-replace policies. Compared against a core table the storage builder emits,
 * with the customer managed key off and on, so a change to the storage defaults that the add-on
 * helper does not follow fails here rather than leaving add-on tables on different settings.
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import commercialTemplate from "../../config/config.template.commercial.json";
import { s3AssetBucketRecords } from "../../lib/helper/s3AssetBuckets";
import { addonDynamoDbTableDefaults } from "../../lib/nestedStacks/addon/addonStackHelpers";
import { storageResourcesBuilder } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { ResourceNameRegistry } from "../../lib/nestedStacks/resourceNames/resourceNameRegistry";
import { newTestApp } from "../support/testApp";

const SHARED_PROPERTIES = [
    "BillingMode",
    "PointInTimeRecoverySpecification",
    "ContributorInsightsSpecification",
    "SSESpecification",
];
const SHARED_POLICIES = ["DeletionPolicy", "UpdateReplacePolicy"];

const mockConfig = (cmk: boolean): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    config.app.useKmsCmkEncryption.enabled = cmk;
    return config;
};

/** The shared properties of the core asset table and of an add-on table in the same stack. */
function sharedOf(cmk: boolean): { core: Record<string, unknown>; addon: Record<string, unknown> } {
    const config = mockConfig(cmk);
    Service.SetConfig(config);
    // The bucket registry is module-level, so a second storage build in this process would
    // otherwise re-register the first build's buckets (see test/support/templateSynth.ts).
    s3AssetBucketRecords.length = 0;

    const app = newTestApp();
    const stack = new cdk.Stack(app, "S", {
        env: { account: config.env.account, region: config.env.region },
    });
    const layer = lambda.LayerVersion.fromLayerVersionArn(
        stack,
        "CommonLayer",
        "arn:aws:lambda:us-east-1:123456789012:layer:vams-common:1"
    ) as LayerVersion;
    const resources = storageResourcesBuilder(
        stack,
        config,
        layer,
        undefined as unknown as ec2.IVpc,
        [],
        new ResourceNameRegistry()
    );

    const storageKey = resources.encryption.kmsKey;
    const addonTable = new dynamodb.Table(stack, "ProbeAddonTable", {
        ...addonDynamoDbTableDefaults(
            storageKey ? kms.Key.fromKeyArn(stack, "ProbeKeyRef", storageKey.keyArn) : undefined
        ),
        partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
    });

    const template = Template.fromStack(stack).toJSON();
    const pick = (table: dynamodb.Table): Record<string, unknown> => {
        const logicalId = stack.getLogicalId(table.node.defaultChild as dynamodb.CfnTable);
        const resource = template.Resources[logicalId];
        return Object.fromEntries([
            ...SHARED_PROPERTIES.map((p) => [p, resource.Properties[p]]),
            ...SHARED_POLICIES.map((p) => [p, resource[p]]),
        ]);
    };
    return { core: pick(resources.dynamo.assetStorageTable), addon: pick(addonTable) };
}

describe("addonDynamoDbTableDefaults", () => {
    test.each([
        ["off", false],
        ["on", true],
    ])("matches the core table properties with the customer managed key %s", (_label, cmk) => {
        const { core, addon } = sharedOf(cmk);
        expect(addon).toEqual(core);
        // Control: every compared value is present on the core table, so the equality is not two
        // undefined values agreeing.
        for (const key of [...SHARED_PROPERTIES, ...SHARED_POLICIES]) {
            expect({ key, value: core[key] }).toEqual({ key, value: expect.anything() });
        }
    });

    test("selects the encryption type from the key", () => {
        expect(addonDynamoDbTableDefaults(undefined).encryption).toBe(
            dynamodb.TableEncryption.AWS_MANAGED
        );
        const stack = new cdk.Stack(newTestApp(), "KeyStack");
        const key = kms.Key.fromKeyArn(
            stack,
            "Key",
            "arn:aws:kms:us-east-1:123456789012:key/00000000-0000-0000-0000-000000000000"
        );
        const withKey = addonDynamoDbTableDefaults(key);
        expect(withKey.encryption).toBe(dynamodb.TableEncryption.CUSTOMER_MANAGED);
        expect(withKey.encryptionKey).toBe(key);
    });
});
