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
import {
    ASSET_EXPORT_STAGING_PREFIX,
    buildAssetExportService,
} from "../lib/lambdaBuilder/assetFunctions";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import * as fs from "fs";
import { newTestApp } from "./support/testApp";

/**
 * An export response above the inline size limit is written to the auxiliary bucket and returned
 * as a presigned GET (`stage_export_payload` in
 * backend/backend/handlers/assets/assetExportService.py). That path needs three permissions on the
 * export function's own role, and every one of them is invisible to a handler unit test because
 * those stub S3 entirely:
 *
 *   - s3:PutObject on the staged object, or the write fails and every large export errors;
 *   - s3:GetObject on the same object, because a query-string presigned URL is authorized as the
 *     signing role — without it the URL is produced successfully and then 403s on use;
 *   - kms:Decrypt / kms:GenerateDataKey* on the VAMS key, for a CMK-encrypted auxiliary bucket.
 *
 * Statements are collected from BOTH AWS::IAM::Policy and AWS::IAM::ManagedPolicy: CDK spills a
 * role's grants into a managed policy once the inline document nears the size limit, and scanning
 * only AWS::IAM::Policy reports a correctly-granted permission as missing.
 */
const commercialTemplate = JSON.parse(
    fs.readFileSync(path.join(__dirname, "../config/config.template.commercial.json"), "utf8")
);

/** Commercial-template config with a fixed synth environment (mirrors tagDatabaseScopeGrants). */
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
    const app = newTestApp();
    const stack = new cdk.Stack(app, "AssetExportGrantStack", {
        env: { account: config.env.account, region: config.env.region },
    });

    const table = (id: string) =>
        new dynamodb.Table(stack, id, {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        });
    const logGroup = (id: string) => new logs.LogGroup(stack, id);

    // Each storageResources member is materialized on first access, so adding a grant to the
    // builder never breaks this fixture, while repeated access returns the same instance so the
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

    const kmsKey = new kms.Key(stack, "VamsKey");
    const resources = {
        dynamo: lazy(table),
        s3: lazy((id: string) => new s3.Bucket(stack, id)),
        cloudWatchAuditLogGroups: lazy(logGroup),
        sns: lazy((id: string) => new sns.Topic(stack, id)),
        encryption: { kmsKey },
    } as unknown as storageResources;

    // Lambda layers reject inline code, so the asset points at a real, TRACKED directory
    // (infra/common) — an untracked placeholder path fails a fresh CI checkout with
    // «CannotFindAsset».
    const layer = new LayerVersion(stack, "Layer", {
        code: Code.fromAsset(path.join(__dirname, "../common")),
        compatibleRuntimes: [Runtime.PYTHON_3_12],
    });
    const vpc = new ec2.Vpc(stack, "Vpc");
    const assetLinksFunction = new Function(stack, "AssetLinksStub", {
        runtime: Runtime.PYTHON_3_12,
        handler: "index.handler",
        code: Code.fromInline("def handler(e, c): pass"),
    });

    return {
        stack,
        config,
        resources,
        layer,
        vpc,
        subnets: vpc.privateSubnets,
        assetLinksFunction,
        kmsKey,
    };
};

interface RoleStatement {
    actions: string[];
    /** Rendered Resource element, so a Fn::Join/Fn::GetAtt reference can be matched verbatim. */
    resources: string;
}

const roleStatements = (template: Template): RoleStatement[] => {
    const statements: RoleStatement[] = [];
    for (const type of ["AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"]) {
        for (const policy of Object.values(template.findResources(type)) as any[]) {
            for (const statement of policy.Properties?.PolicyDocument?.Statement ?? []) {
                statements.push({
                    actions: ([] as string[]).concat(statement.Action ?? []),
                    resources: JSON.stringify(statement.Resource ?? ""),
                });
            }
        }
    }
    return statements;
};

/** True when one statement carries the action AND every resource fragment. */
const grants = (
    statements: RoleStatement[],
    action: string,
    resourceFragments: string[]
): boolean =>
    statements.some(
        (statement) =>
            statement.actions.includes(action) &&
            resourceFragments.every((fragment) => statement.resources.includes(fragment))
    );

describe("assetExportService large-export staging grants", () => {
    const fixture = () => {
        const f = buildFixture();
        buildAssetExportService(
            f.stack,
            f.layer,
            f.resources,
            f.assetLinksFunction,
            f.config,
            f.vpc,
            f.subnets
        );
        return {
            statements: roleStatements(Template.fromStack(f.stack)),
            // Exact rendered reference to the auxiliary bucket ARN, which appears verbatim inside
            // the object-ARN Fn::Join a substring check would otherwise match loosely.
            auxBucketRef: JSON.stringify(
                f.stack.resolve(f.resources.s3.assetAuxiliaryBucket.bucketArn)
            ),
            kmsKeyRef: JSON.stringify(f.stack.resolve(f.kmsKey.keyArn)),
            stagedObjects: `/${ASSET_EXPORT_STAGING_PREFIX}*`,
        };
    };

    test("can write the staged export payload to the auxiliary bucket", () => {
        const { statements, auxBucketRef, stagedObjects } = fixture();
        // Positive control: the scan sees S3 statements at all, so a miss below is a real miss.
        expect(statements.some((s) => s.actions.some((a) => a.startsWith("s3:")))).toBe(true);
        expect(grants(statements, "s3:PutObject", [auxBucketRef, stagedObjects])).toBe(true);
    });

    test("can read back the object its presigned URL is signed for", () => {
        const { statements, auxBucketRef, stagedObjects } = fixture();
        expect(grants(statements, "s3:GetObject", [auxBucketRef, stagedObjects])).toBe(true);
    });

    test("can use the VAMS key the auxiliary bucket is encrypted with", () => {
        const { statements, kmsKeyRef } = fixture();
        expect(grants(statements, "kms:Decrypt", [kmsKeyRef])).toBe(true);
        expect(grants(statements, "kms:GenerateDataKey*", [kmsKeyRef])).toBe(true);
    });

    test("holds no auxiliary-bucket access beyond the export staging prefix", () => {
        // Least privilege, and a guard against widening this to a whole-bucket grantReadWrite by
        // reflex: the handler only ever puts and gets its own staged payloads.
        const { statements, auxBucketRef, stagedObjects } = fixture();
        const auxStatements = statements.filter(
            (statement) =>
                statement.actions.some((action) => action.startsWith("s3:")) &&
                statement.resources.includes(auxBucketRef)
        );
        expect(auxStatements.length).toBeGreaterThan(0);
        for (const statement of auxStatements) {
            expect(statement.resources).toContain(stagedObjects);
            expect(statement.actions.sort()).toEqual(["s3:GetObject", "s3:PutObject"]);
        }
    });
});
