/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { authResources } from "../../lib/nestedStacks/auth/authBuilder-nestedStack";
import { buildSubscriptionService } from "../../lib/lambdaBuilder/subscriptionFunctions";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

/**
 * subscriptionService reads a subscriber's email from the Cognito user pool when the user has no
 * VAMS profile yet (#1354). The function gets the pool id and an AdminGetUser grant on that one
 * pool only when Cognito is the identity provider and the function runs outside the VPC: VAMS
 * creates no Cognito interface endpoint, so in the VPC the read could only time out.
 */

const mockConfig = (opts: { cognito: boolean; inVpc: boolean }): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    config.app.authProvider.useCognito.enabled = opts.cognito;
    config.app.useGlobalVpc.enabled = opts.inVpc;
    config.app.useGlobalVpc.useForAllLambdas = opts.inVpc;
    return config;
};

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
            subscriptionsStorageTable: table("SubscriptionsTable"),
            assetStorageTable: table("AssetTable"),
            authEntitiesStorageTable: table("AuthEntitiesTable"),
            userStorageTable: table("UserTable"),
            constraintsStorageTable: table("ConstraintsTable"),
            userRolesStorageTable: table("UserRolesTable"),
            rolesStorageTable: table("RolesTable"),
        },
    } as unknown as storageResources;
};

const synth = (opts: { cognito: boolean; inVpc: boolean }) => {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "TestStack", {
        env: { account: "123456789012", region: "us-east-1" },
    });
    const config = mockConfig(opts);
    Service.SetConfig(config);
    const layer = lambda.LayerVersion.fromLayerVersionArn(
        stack,
        "CommonLayer",
        "arn:aws:lambda:us-east-1:123456789012:layer:vams-common:1"
    ) as lambda.LayerVersion;
    const userPool = new cognito.UserPool(stack, "UserPool");
    const auth = {
        cognito: { userPool, userPoolId: userPool.userPoolId },
    } as unknown as authResources;
    const vpc = opts.inVpc
        ? new ec2.Vpc(stack, "Vpc", {
              subnetConfiguration: [
                  { name: "isolated", subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
              ],
          })
        : (undefined as unknown as ec2.IVpc);

    buildSubscriptionService(
        stack,
        layer,
        buildStorageResources(stack),
        auth,
        config,
        vpc,
        opts.inVpc ? vpc.isolatedSubnets : []
    );

    const template = Template.fromStack(stack);
    const fn = Object.values(template.findResources("AWS::Lambda::Function")).find((r: any) =>
        String(r.Properties.Handler).includes("subscriptionService")
    ) as any;
    const env = fn.Properties.Environment?.Variables ?? {};
    const cognitoStatements = Object.values(template.findResources("AWS::IAM::Policy"))
        .flatMap((p: any) => p.Properties.PolicyDocument.Statement)
        .filter((s: any) => JSON.stringify(s.Action ?? "").includes("cognito-idp:"));
    return { env, cognitoStatements };
};

describe("subscriptionService Cognito email lookup", () => {
    test("Cognito outside the VPC: pool id and AdminGetUser on that one pool", () => {
        const { env, cognitoStatements } = synth({ cognito: true, inVpc: false });

        expect(env.USER_POOL_ID).toBeDefined();
        expect(cognitoStatements).toHaveLength(1);
        expect(cognitoStatements[0].Action).toBe("cognito-idp:AdminGetUser");
        expect(JSON.stringify(cognitoStatements[0].Resource)).toContain("UserPool");
        expect(JSON.stringify(cognitoStatements[0].Resource)).not.toContain("*");
    });

    test("Cognito with Lambda functions in the VPC: no pool id and no Cognito grant", () => {
        const { env, cognitoStatements } = synth({ cognito: true, inVpc: true });

        expect(env.USER_POOL_ID).toBeUndefined();
        expect(cognitoStatements).toHaveLength(0);
    });

    test("external identity provider: no pool id and no Cognito grant", () => {
        const { env, cognitoStatements } = synth({ cognito: false, inVpc: false });

        expect(env.USER_POOL_ID).toBeUndefined();
        expect(cognitoStatements).toHaveLength(0);
    });
});
