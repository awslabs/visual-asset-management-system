/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Miris add-on nested stack has no unsuppressed AwsSolutions finding in perView mode with the
 * deployment CMK, which builds every resource it can hold. It is built under a parent stack that
 * supplies its storage, with the Nag pack and framework suppressions applied as the app applies them;
 * AwsSolutions-L1 is left to the core stack.
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import { AwsSolutionsChecks } from "cdk-nag";
import * as Service from "../../lib/helper/service-helper";
import { suppressCdkNagLambdaFrameworkResources } from "../../lib/helper/security";
import { RouteRegistry } from "../../lib/nestedStacks/apiLambda/apiRouteRegistry";
import { MirisBuilderNestedStack } from "../../lib/nestedStacks/addon/miris/mirisBuilder-nestedStack";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { ACCOUNT, REGION, createMockConfig } from "../support/pipelineConstructHarness";
import { newTestApp } from "../support/testApp";

const AUDIT_LOG_GROUPS = (
    "authentication authorization fileUpload fileDownload fileDownloadStreamed " +
    "authOther authChanges actions errors"
).split(" ");

test("the perView add-on stack has no unsuppressed AwsSolutions error", () => {
    const config = createMockConfig();
    config.app.useKmsCmkEncryption.enabled = true;
    config.app.webUi.allowUnsafeEvalFeatures = true;
    Object.assign(config.app.addons.useMirisStreaming, {
        enabled: true,
        viewerKeyMode: "perView",
        apiKeySecretArn: "arn:aws:secretsmanager:us-east-1:123456789012:secret:miris-AbCdEf",
    });
    Service.SetConfig(config);

    const app = newTestApp();
    // `logIgnores` records each applied suppression as an info annotation, for the controls below.
    cdk.Aspects.of(app).add(
        new AwsSolutionsChecks({ verbose: true, logIgnores: true, reports: false })
    );
    const parent = new cdk.Stack(app, "MirisNagParent", {
        env: { account: ACCOUNT, region: REGION },
    });
    const kmsKey = new kms.Key(parent, "Key");
    const table = (id: string) =>
        new dynamodb.Table(parent, id, {
            partitionKey: { name: "id", type: dynamodb.AttributeType.STRING },
            encryption: dynamodb.TableEncryption.CUSTOMER_MANAGED,
            encryptionKey: kmsKey,
        });
    const storage = {
        encryption: { kmsKey },
        dynamo: {
            assetStorageTable: table("AssetStorageTable"),
            assetFileMetadataStorageTable: table("AssetFileMetadataStorageTableV2"),
            metadataSchemaStorageTableV2: table("MetadataSchemaStorageTableV2"),
            constraintsStorageTable: table("ConstraintsStorageTable"),
            userRolesStorageTable: table("UserRolesStorageTable"),
            rolesStorageTable: table("RolesStorageTable"),
            s3AssetBucketsStorageTable: table("S3AssetBucketsStorageTable"),
        },
        s3: { assetAuxiliaryBucket: new s3.Bucket(parent, "AssetAuxiliaryBucket") },
        cloudWatchAuditLogGroups: Object.fromEntries(
            AUDIT_LOG_GROUPS.map((name) => [
                name,
                new logs.LogGroup(parent, `${name}AuditLogGroup`),
            ])
        ),
    } as unknown as storageResources;

    const nested = new MirisBuilderNestedStack(parent, "MirisBuilderNestedStack", {
        config,
        vpc: {} as ec2.IVpc,
        isolatedSubnets: [],
        storageResources: storage,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            parent,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        registry: new RouteRegistry(),
    });
    suppressCdkNagLambdaFrameworkResources(parent);

    app.synth();

    const nodes = nested.node.findAll();
    const paths = nodes.map((node) => node.node.path);
    const root = nested.node.path;

    // Control: every resource the stack can hold was built and walked, or "no errors" is vacuous.
    for (const id of [
        "MirisViewerKeyStorageTable",
        "MirisGetAssetStatusFunction",
        "MirisMintViewerKeyFunction",
        "MirisViewerKeyCleanupFunction",
        "MirisViewerKeyCleanupStream",
        "MirisViewerKeyCleanupDLQ",
        "MirisStreamSchemaCustomResourceRole",
        "MetadataSchemaMiris",
    ]) {
        expect(paths).toContain(`${root}/${id}`);
    }

    const entries = nodes.flatMap((node) =>
        node.node.metadata.map((entry) => ({ path: node.node.path, entry }))
    );

    // Control: the aspect visited the stack and applied its suppressions there. A Nag pack that
    // never reached it would also report no errors.
    const applied = entries
        .filter(
            ({ entry }) =>
                entry.type === "aws:cdk:info" && String(entry.data).includes("AwsSolutions-")
        )
        .map(({ entry }) => String(entry.data));
    expect(applied.some((info) => info.includes("ListStreams"))).toBe(true);
    expect(applied.some((info) => info.includes("AwsSolutions-SQS3"))).toBe(true);
    expect(applied.some((info) => info.includes("AwsSolutions-IAM4"))).toBe(true);
    expect(applied.some((info) => info.includes("/pipelines/miris-upload/manifests/*"))).toBe(true);

    const errors = entries
        .filter(
            ({ entry }) =>
                entry.type === "aws:cdk:error" && String(entry.data).includes("AwsSolutions-")
        )
        .map(({ path, entry }) => `${path}: ${String(entry.data).split("\n")[0]}`)
        .filter((line) => !line.includes(": AwsSolutions-L1:"));
    expect(errors).toEqual([]);
});
