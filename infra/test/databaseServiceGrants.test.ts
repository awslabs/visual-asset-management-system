/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import { storageResources } from "../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { buildDatabaseService } from "../lib/lambdaBuilder/databaseFunctions";
import commercialTemplate from "../config/config.template.commercial.json";

/**
 * databaseService is reachable by any authenticated caller of the /databases routes. Its
 * DynamoDB grants must cover only the tables the handler resolves
 * (backend/backend/handlers/databases/databaseService.py): databaseStorage, s3AssetBuckets,
 * assetStorage, and the V2 pipeline/workflow tables. The retained V1 PipelineStorageTable and
 * WorkflowStorageTable still hold pre-overhaul definitions on an upgraded deployment and are
 * not read by this handler.
 */

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

// Minimal storageResources with a distinctly named table per member the builder touches, so a
// grant can be traced back to its table by construct id in the synthesized policy.
const buildStorageResources = (stack: cdk.Stack): storageResources => {
    const table = (id: string) =>
        new dynamodb.Table(stack, id, {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        });
    const logGroup = (id: string) => new logs.LogGroup(stack, id);
    return {
        encryption: {},
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
            accessLogsBucket: new s3.Bucket(stack, "AccessLogsBucket"),
        },
        cloudWatchAuditLogGroups: {
            authentication: logGroup("AuditAuthentication"),
            authorization: logGroup("AuditAuthorization"),
            fileUpload: logGroup("AuditFileUpload"),
            fileDownload: logGroup("AuditFileDownload"),
            fileDownloadStreamed: logGroup("AuditFileDownloadStreamed"),
            authOther: logGroup("AuditAuthOther"),
            authChanges: logGroup("AuditAuthChanges"),
            actions: logGroup("AuditActions"),
            errors: logGroup("AuditErrors"),
        },
        dynamo: {
            s3AssetBucketsStorageTable: table("S3AssetBucketsTable"),
            databaseStorageTable: table("DatabaseTable"),
            assetStorageTable: table("AssetTable"),
            pipelineStorageTable: table("V1PipelineTable"),
            workflowStorageTable: table("V1WorkflowTable"),
            pipelineStorageTableV2: table("V2PipelineTable"),
            workflowStorageTableV2: table("V2WorkflowTable"),
            authEntitiesStorageTable: table("AuthEntitiesTable"),
            constraintsStorageTable: table("ConstraintsTable"),
            userRolesStorageTable: table("UserRolesTable"),
            rolesStorageTable: table("RolesTable"),
        },
    } as unknown as storageResources;
};

// Logical-id fragments of every table referenced by the Lambda role's DynamoDB statements.
const grantedTableIds = (template: Template, candidates: string[]): string[] => {
    const policies = template.findResources("AWS::IAM::Policy");
    const dynamoStatements = Object.values(policies)
        .flatMap((p: any) => p.Properties.PolicyDocument.Statement)
        .filter((s: any) => JSON.stringify(s.Action ?? "").includes("dynamodb:"));
    const rendered = JSON.stringify(dynamoStatements);
    return candidates.filter((id) => rendered.includes(id));
};

describe("buildDatabaseService DynamoDB grants", () => {
    test("grants the V2 pipeline/workflow tables and not the retained V1 tables", () => {
        const app = new cdk.App();
        const stack = new cdk.Stack(app, "TestStack", {
            env: { account: "123456789012", region: "us-east-1" },
        });
        const config = mockConfig();
        Service.SetConfig(config);

        const layer = lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "CommonLayer",
            "arn:aws:lambda:us-east-1:123456789012:layer:vams-common:1"
        ) as lambda.LayerVersion;

        // The commercial template leaves useGlobalVpc disabled, so the builder attaches no VPC.
        buildDatabaseService(
            stack,
            layer,
            buildStorageResources(stack),
            config,
            undefined as unknown as ec2.IVpc,
            []
        );

        const template = Template.fromStack(stack);
        const granted = grantedTableIds(template, [
            "DatabaseTable",
            "S3AssetBucketsTable",
            "AssetTable",
            "V2PipelineTable",
            "V2WorkflowTable",
            "V1PipelineTable",
            "V1WorkflowTable",
        ]);

        expect(granted).toContain("V2PipelineTable");
        expect(granted).toContain("V2WorkflowTable");
        expect(granted).toContain("DatabaseTable");
        expect(granted).toContain("S3AssetBucketsTable");
        expect(granted).toContain("AssetTable");
        expect(granted).not.toContain("V1PipelineTable");
        expect(granted).not.toContain("V1WorkflowTable");
    });
});
