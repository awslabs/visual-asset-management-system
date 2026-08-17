/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as kms from "aws-cdk-lib/aws-kms";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sns from "aws-cdk-lib/aws-sns";
import { LayerVersion, Code, Runtime, Function } from "aws-cdk-lib/aws-lambda";
import { Template } from "aws-cdk-lib/assertions";
import { storageResources } from "../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { buildCreateTagFunction, buildTagService } from "../lib/lambdaBuilder/tagFunctions";
import { buildCreateTagTypeFunction } from "../lib/lambdaBuilder/tagTypeFunctions";
import { buildAssetService } from "../lib/lambdaBuilder/assetFunctions";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import * as fs from "fs";

/**
 * A database-scoped tag or tag type is rejected unless its database exists, so the create/update
 * handlers read the database table (`common/tagScope.verify_database_exists`). Without a matching IAM
 * grant that read fails with AccessDeniedException and every scoped create returns a 400 — a failure
 * no handler unit test sees, because those stub DynamoDB entirely. It only shows up against a real
 * deployment, so it is asserted here on the synthesized role.
 *
 * Statements are collected from BOTH AWS::IAM::Policy and AWS::IAM::ManagedPolicy: CDK spills into a
 * managed policy once an inline policy nears the size limit, and scanning only AWS::IAM::Policy
 * reports a correctly-granted permission as missing.
 */
const commercialTemplate = JSON.parse(
    fs.readFileSync(path.join(__dirname, "../config/config.template.commercial.json"), "utf8")
);

/** Commercial-template config with a fixed synth environment (mirrors vpcEndpointsAndAuthGrants). */
const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = false;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

const buildFixture = () => {
    const config = createMockConfig();
    Service.SetConfig(config);
    const app = new cdk.App();
    const stack = new cdk.Stack(app, "TagGrantStack", {
        env: { account: config.env.account, region: config.env.region },
    });

    const table = (id: string) =>
        new dynamodb.Table(stack, id, {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        });
    const logGroup = (id: string) => new logs.LogGroup(stack, id);

    // Builders touch a long and growing list of tables/buckets/log groups; only the tag and database
    // tables matter to these assertions. Each member is materialized on first access so adding a grant
    // to a builder never breaks this fixture, while repeated access returns the same instance so the
    // assertions can resolve a specific ARN.
    const lazy = <T>(make: (id: string) => T) => {
        const cache = new Map<string, T>();
        return new Proxy({} as Record<string, T>, {
            get: (_target, prop: string) => {
                if (!cache.has(prop)) {
                    cache.set(prop, make(prop));
                }
                return cache.get(prop);
            },
        });
    };

    const resources = {
        dynamo: lazy(table),
        s3: lazy((id: string) => new s3.Bucket(stack, id)),
        cloudWatchAuditLogGroups: lazy(logGroup),
        sns: lazy((id: string) => new sns.Topic(stack, id)),
        encryption: { kmsKey: new kms.Key(stack, "Key") },
    } as unknown as storageResources;

    // Lambda layers reject inline code, so this points at any directory on disk.
    const layer = new LayerVersion(stack, "Layer", {
        code: Code.fromAsset(path.join(__dirname, "fixtures")),
        compatibleRuntimes: [Runtime.PYTHON_3_12],
    });
    const vpc = new ec2.Vpc(stack, "Vpc");

    return { stack, config, resources, layer, vpc, subnets: vpc.privateSubnets };
};

/** Resource references on every dynamodb:GetItem statement, inline and managed policies alike. */
const dynamoGetItemResourceRefs = (template: Template): string => {
    const refs: string[] = [];
    for (const type of ["AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"]) {
        for (const policy of Object.values(template.findResources(type)) as any[]) {
            for (const statement of policy.Properties?.PolicyDocument?.Statement ?? []) {
                const actions = ([] as string[]).concat(statement.Action ?? []);
                if (!actions.includes("dynamodb:GetItem")) {
                    continue;
                }
                refs.push(JSON.stringify(statement.Resource));
            }
        }
    }
    return refs.join(" ");
};

describe("database-scoped tag creation IAM grants", () => {
    test("createTag can read the database table", () => {
        const { stack, config, resources, layer, vpc, subnets } = buildFixture();
        buildCreateTagFunction(stack, layer, resources, config, vpc, subnets);

        const refs = dynamoGetItemResourceRefs(Template.fromStack(stack));
        // Positive control: the scan finds SOME GetItem grant, so a miss below is a real miss.
        expect(refs).not.toEqual("");
        expect(refs).toContain(
            JSON.stringify(stack.resolve(resources.dynamo.databaseStorageTable.tableArn))
        );
    });

    test("createTagTypes can read the database table", () => {
        const { stack, config, resources, layer, vpc, subnets } = buildFixture();
        buildCreateTagTypeFunction(stack, layer, resources, config, vpc, subnets);

        const refs = dynamoGetItemResourceRefs(Template.fromStack(stack));
        expect(refs).not.toEqual("");
        expect(refs).toContain(
            JSON.stringify(stack.resolve(resources.dynamo.databaseStorageTable.tableArn))
        );
    });

    test("tagService is not granted the database table it never reads", () => {
        // GET/DELETE only — least privilege, and a guard against widening the grant by reflex.
        const { stack, config, resources, layer, vpc, subnets } = buildFixture();
        buildTagService(stack, layer, resources, config, vpc, subnets);

        const refs = dynamoGetItemResourceRefs(Template.fromStack(stack));
        expect(refs).not.toContain(
            JSON.stringify(stack.resolve(resources.dynamo.databaseStorageTable.tableArn))
        );
    });
    test("assetService can read the tag tables it validates edits against", () => {
        // An asset edit validates its tags via createAsset's scoped lookups, which query both tag
        // tables from THIS handler's role. Missing grants showed up only as a 500 on a live edit.
        const { stack, config, resources, layer, vpc, subnets } = buildFixture();
        const sendEmail = new Function(stack, "SendEmailStub", {
            runtime: Runtime.PYTHON_3_12,
            handler: "index.handler",
            code: Code.fromInline("def handler(e, c): pass"),
        });
        buildAssetService(stack, layer, resources, sendEmail, config, vpc, subnets);

        const refs = dynamoGetItemResourceRefs(Template.fromStack(stack));
        expect(refs).not.toEqual("");
        expect(refs).toContain(
            JSON.stringify(stack.resolve(resources.dynamo.tagStorageTable.tableArn))
        );
        expect(refs).toContain(
            JSON.stringify(stack.resolve(resources.dynamo.tagTypeStorageTable.tableArn))
        );
    });
});
