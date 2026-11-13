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
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import {
    buildAssetExportService,
    buildDownloadAssetFunction,
    buildStreamAssetFunction,
    buildStreamAuxiliaryPreviewAssetFunction,
} from "../../lib/lambdaBuilder/assetFunctions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as fs from "fs";
import { newTestApp } from "../support/testApp";

/**
 * `app.compliance.quarantineBlocksDownload` gates the quarantine guard shared by every Lambda that
 * hands out asset file bytes (`backend/backend/common/compliance/quarantineGuard.py`). The guard
 * reads `COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD` at import and, when it is `true`, resolves and
 * reads the compliance asset-state table -- so each of the four Lambdas needs BOTH the env var and
 * `grantReadData` on that table, or a quarantined asset stays downloadable through the Lambda that
 * was missed (the export and auxiliary-preview routes were exactly that gap). With the flag off,
 * none of them carries either: the guard is a no-op and the grant would be a privilege nothing uses.
 *
 * The four builders are exercised directly against a lazy storageResources fixture (mirrors
 * assetExportServiceGrants) so the assertion is on the builder rather than on a full synth.
 */
const commercialTemplate = JSON.parse(
    fs.readFileSync(path.join(__dirname, "..", "../config/config.template.commercial.json"), "utf8")
);

const createMockConfig = (quarantineBlocksDownload: boolean): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = false;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    config.app.compliance.quarantineBlocksDownload = quarantineBlocksDownload;
    return config;
};

/** The four Lambdas the quarantine guard runs in, keyed by the CDK function id each builder uses. */
const GUARDED_FUNCTIONS = [
    "downloadAsset",
    "streamAsset",
    "assetExportService",
    "streamAuxiliaryPreviewAsset",
] as const;

const buildFixture = (quarantineBlocksDownload: boolean) => {
    const config = createMockConfig(quarantineBlocksDownload);
    Service.SetConfig(config);
    const app = newTestApp();
    const stack = new cdk.Stack(app, "QuarantineGuardStack", {
        env: { account: config.env.account, region: config.env.region },
    });

    const table = (id: string) =>
        new dynamodb.Table(stack, id, {
            partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
        });
    const logGroup = (id: string) => new logs.LogGroup(stack, id);

    // Each storageResources member is materialized on first access and cached, so the asset-state
    // table the assertions resolve is the same instance the builders granted on.
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

    // Lambda layers reject inline code, so the asset points at a real, TRACKED directory.
    const layer = new LayerVersion(stack, "Layer", {
        code: Code.fromAsset(path.join(__dirname, "..", "../common")),
        compatibleRuntimes: [Runtime.PYTHON_3_12],
    });
    const vpc = new ec2.Vpc(stack, "Vpc");
    const subnets = vpc.privateSubnets;
    const assetLinksFunction = new Function(stack, "AssetLinksStub", {
        runtime: Runtime.PYTHON_3_12,
        handler: "index.handler",
        code: Code.fromInline("def handler(e, c): pass"),
    });

    const functions = {
        downloadAsset: buildDownloadAssetFunction(stack, layer, resources, config, vpc, subnets),
        streamAsset: buildStreamAssetFunction(stack, layer, resources, config, vpc, subnets),
        assetExportService: buildAssetExportService(
            stack,
            layer,
            resources,
            assetLinksFunction,
            config,
            vpc,
            subnets
        ),
        streamAuxiliaryPreviewAsset: buildStreamAuxiliaryPreviewAssetFunction(
            stack,
            layer,
            resources,
            config,
            vpc,
            subnets
        ),
    };

    // Touch the asset-state table so it exists in the template for the negative case too.
    const stateTable = resources.dynamo.complianceAssetStateStorageTable;
    const template = Template.fromStack(stack);

    return {
        stack,
        template,
        functions,
        stateTableArnRef: JSON.stringify(stack.resolve(stateTable.tableArn)),
    };
};

type Fixture = ReturnType<typeof buildFixture>;

/** The rendered Environment.Variables of one Lambda, located by its CDK id. */
const environmentOf = (fixture: Fixture, id: (typeof GUARDED_FUNCTIONS)[number]) => {
    const logicalId = fixture.stack.getLogicalId(
        fixture.functions[id].node.defaultChild as cdk.CfnElement
    );
    const resource = fixture.template.findResources("AWS::Lambda::Function")[logicalId];
    expect(resource).toBeDefined();
    return (resource.Properties?.Environment?.Variables ?? {}) as Record<string, unknown>;
};

/** The logical id of a Lambda's execution role. */
const roleLogicalIdOf = (fixture: Fixture, id: (typeof GUARDED_FUNCTIONS)[number]) =>
    fixture.stack.getLogicalId(fixture.functions[id].role!.node.defaultChild as cdk.CfnElement);

/**
 * Whether a role holds a statement granting `dynamodb:GetItem` that names the asset-state table.
 * Statements are collected from BOTH AWS::IAM::Policy and AWS::IAM::ManagedPolicy: CDK spills a
 * role's grants into a managed policy once the inline document nears the size limit.
 */
const roleReadsStateTable = (fixture: Fixture, id: (typeof GUARDED_FUNCTIONS)[number]): boolean => {
    const roleId = roleLogicalIdOf(fixture, id);
    for (const type of ["AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"]) {
        for (const policy of Object.values(fixture.template.findResources(type)) as any[]) {
            const roles = JSON.stringify(policy.Properties?.Roles ?? []);
            if (!roles.includes(roleId)) {
                continue;
            }
            for (const statement of policy.Properties?.PolicyDocument?.Statement ?? []) {
                const actions = ([] as string[]).concat(statement.Action ?? []);
                const resources = JSON.stringify(statement.Resource ?? "");
                if (
                    actions.includes("dynamodb:GetItem") &&
                    resources.includes(fixture.stateTableArnRef)
                ) {
                    return true;
                }
            }
        }
    }
    return false;
};

describe("quarantineBlocksDownload wiring on the four asset download Lambdas", () => {
    describe("with app.compliance.quarantineBlocksDownload: true", () => {
        let fixture: Fixture;
        beforeAll(() => {
            fixture = buildFixture(true);
        });

        test.each(GUARDED_FUNCTIONS)(
            "%s carries COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD=true",
            (id) => {
                expect(environmentOf(fixture, id).COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD).toBe(
                    "true"
                );
            }
        );

        test.each(GUARDED_FUNCTIONS)("%s can read the compliance asset-state table", (id) => {
            expect(roleReadsStateTable(fixture, id)).toBe(true);
        });

        test("the state-table grant is read-only", () => {
            // The guard performs one GetItem; none of the four Lambdas writes compliance state.
            for (const type of ["AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"]) {
                for (const policy of Object.values(fixture.template.findResources(type)) as any[]) {
                    for (const statement of policy.Properties?.PolicyDocument?.Statement ?? []) {
                        const resources = JSON.stringify(statement.Resource ?? "");
                        if (!resources.includes(fixture.stateTableArnRef)) {
                            continue;
                        }
                        const actions = ([] as string[]).concat(statement.Action ?? []);
                        for (const action of actions) {
                            expect(action).not.toMatch(
                                /^dynamodb:(PutItem|UpdateItem|DeleteItem|BatchWriteItem)$/
                            );
                        }
                    }
                }
            }
        });
    });

    describe("with app.compliance.quarantineBlocksDownload: false", () => {
        let fixture: Fixture;
        beforeAll(() => {
            fixture = buildFixture(false);
        });

        test.each(GUARDED_FUNCTIONS)(
            "%s carries no COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD",
            (id) => {
                expect(environmentOf(fixture, id)).not.toHaveProperty(
                    "COMPLIANCE_QUARANTINE_BLOCKS_DOWNLOAD"
                );
            }
        );

        test.each(GUARDED_FUNCTIONS)(
            "%s holds no grant on the compliance asset-state table",
            (id) => {
                expect(roleReadsStateTable(fixture, id)).toBe(false);
            }
        );

        test("positive control: the env-var lookup sees the variables the builders always set", () => {
            // PRESIGNED_URL_TIMEOUT_SECONDS is set on all four regardless of the flag, so an empty
            // environment above would be a lookup failure rather than a passing negative.
            for (const id of GUARDED_FUNCTIONS) {
                expect(environmentOf(fixture, id)).toHaveProperty("PRESIGNED_URL_TIMEOUT_SECONDS");
            }
        });
    });
});
