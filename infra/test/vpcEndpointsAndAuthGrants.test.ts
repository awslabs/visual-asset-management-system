/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Least-privilege and VPC-endpoint gating checks for the shared security helper, the VPC
 * builder, and the core stack's nested-stack dependency graph.
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as logs from "aws-cdk-lib/aws-logs";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import { VPCBuilderNestedStack } from "../lib/nestedStacks/vpc/vpcBuilder-nestedStack";
import { setupSecurityAndLoggingEnvironmentAndPermissions } from "../lib/helper/security";
import { storageResources } from "../lib/nestedStacks/storage/storageBuilder-nestedStack";
import commercialTemplate from "../config/config.template.commercial.json";

/** Commercial-template config with a fixed synth environment. */
const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.env.loadContextIgnoreVPCStacks = false;
    config.app.baseStackName = "vams-test";
    config.app.useWaf = false;
    config.app.addStackCloudTrailLogs = false;
    config.app.openSearch.useServerless.enabled = false;
    config.app.openSearch.useProvisioned.enabled = false;
    config.app.useLocationService.enabled = false;
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.addVpcEndpoints = true;
    config.enableCdkNag = false;
    config.dockerDefaultPlatform = "";
    config.s3AdditionalBucketPolicyJSON = undefined;
    config.iamRoleCustomizationJSON = undefined;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

/** Synthesizes the VPC builder alone and returns its interface-endpoint service names. */
const synthVpcEndpointServices = (config: Config.Config): string[] => {
    Service.SetConfig(config);
    const app = new cdk.App();
    const parent = new cdk.Stack(app, "ParentStack", {
        env: { account: config.env.account, region: config.env.region },
    });
    const vpcStack = new VPCBuilderNestedStack(parent, "VPCBuilder", { config });
    const endpoints = Template.fromStack(vpcStack).findResources("AWS::EC2::VPCEndpoint");
    return Object.values(endpoints).map((e: any) => JSON.stringify(e.Properties.ServiceName));
};

describe("Deadline Cloud VPC interface endpoint gating", () => {
    // The job-callback lambda is the only in-VPC caller of the Deadline management API, and it
    // is placed in the VPC only under useForAllLambdas. Creating the endpoint otherwise bills
    // one ENI per AZ with no consumer.
    test("is not created when lambdas do not run in the VPC", () => {
        const config = createMockConfig();
        config.app.pipelines.deadlineCloudExecutionTypeEnabled = true;
        config.app.useGlobalVpc.useForAllLambdas = false;
        const services = synthVpcEndpointServices(config);
        expect(services.filter((s) => s.includes("deadline"))).toHaveLength(0);
    });

    test("is created when the execution type is enabled and lambdas run in the VPC", () => {
        const config = createMockConfig();
        config.app.pipelines.deadlineCloudExecutionTypeEnabled = true;
        config.app.useGlobalVpc.useForAllLambdas = true;
        const services = synthVpcEndpointServices(config);
        expect(services.filter((s) => s.includes("deadline.management"))).toHaveLength(1);
    });

    test("is not created when the execution type is disabled", () => {
        const config = createMockConfig();
        config.app.pipelines.deadlineCloudExecutionTypeEnabled = false;
        config.app.useGlobalVpc.useForAllLambdas = true;
        const services = synthVpcEndpointServices(config);
        expect(services.filter((s) => s.includes("deadline"))).toHaveLength(0);
    });
});

describe("Batch/ECS/Fargate pipeline VPC condition blocks", () => {
    // Every Batch/ECS/Fargate pipeline flag must appear in all three condition blocks:
    // subnet creation, the Batch/ECR endpoint block, and the ECS endpoint block.
    const source = fs.readFileSync(
        path.join(__dirname, "../lib/nestedStacks/vpc/vpcBuilder-nestedStack.ts"),
        "utf8"
    );

    const batchPipelineFlags = [
        "useConversionCoordinateTransform",
        "useRapidPipeline.useEcs",
        "useRapidPipeline.useEks",
        "useModelOps",
        "useSplatToolbox",
        "useNvidiaCosmos",
        "useNvidiaCosmos3",
        "useNvidiaGr00t",
    ];

    // The three blocks, keyed by an anchor unique to each.
    const blocks = (() => {
        const subnetStart = source.indexOf("subnetConfigurations.push(subnetPublicConfig)");
        const subnetBlock = source.slice(source.lastIndexOf("if (", subnetStart), subnetStart);
        const batchStart = source.indexOf('"BatchEndpoint"');
        const batchBlock = source.slice(source.lastIndexOf("if (", batchStart), batchStart);
        const ecsStart = source.indexOf("const needsEcsIsolated");
        const ecsBlock = source.slice(source.indexOf("const needsEcsPrivate"), ecsStart);
        return { subnetBlock, batchBlock, ecsBlock };
    })();

    test.each(Object.entries(blocks))("%s is non-empty", (_name, block) => {
        expect(block.length).toBeGreaterThan(0);
    });

    test.each(batchPipelineFlags)("%s appears in all three condition blocks", (flag) => {
        expect(blocks.subnetBlock).toContain(flag);
        expect(blocks.batchBlock).toContain(flag);
        expect(blocks.ecsBlock).toContain(flag);
    });

    // IsaacLab runs Batch on EC2 in isolated subnets, so it is gated separately in the ECS
    // block (needsEcsIsolated) rather than in needsEcsPrivate.
    test("useIsaacLabTraining appears in the subnet and Batch blocks", () => {
        expect(blocks.subnetBlock).toContain("useIsaacLabTraining");
        expect(blocks.batchBlock).toContain("useIsaacLabTraining");
        expect(source).toContain(
            "needsEcsIsolated = props.config.app.pipelines.useIsaacLabTraining"
        );
    });
});

describe("core stack nested-stack dependencies", () => {
    // Every Lambda-bearing nested stack resolves resource names from the SSM parameters
    // published by ResourceNamesBuilder, so each must declare that dependency explicitly.
    const source = fs.readFileSync(path.join(__dirname, "../lib/core-stack.ts"), "utf8");

    // Source text is matched with the comment lines stripped, so a dependency that has been
    // commented out cannot satisfy the assertion.
    const activeLines: string[] = source
        .split(/\r?\n/)
        .filter((line: string) => !line.trim().startsWith("//"));
    const activeSource = activeLines.join("\n");

    test.each([
        "authBuilderNestedStack",
        "apiBuilderNestedStack",
        "apiBuilder2NestedStack",
        "searchBuilderNestedStack",
        "addonBuilderNestedStack",
    ])("%s depends on resourceNamesNestedStack", (stackVar) => {
        // Matched on the current spelling: Stack#addDependency is deprecated in favour of
        // addStackDependency, and asserting the old name would fail a correct migration.
        expect(activeSource).toContain(`${stackVar}.addStackDependency(resourceNamesNestedStack)`);
    });

    test("no deprecated Stack#addDependency call remains", () => {
        // The rename is only complete if nothing still uses the deprecated spelling. node-level
        // construct dependencies (`x.node.addDependency`) are a different, non-deprecated API.
        const deprecated = activeLines.filter((line: string) =>
            /(?<!node)\.addDependency\(/.test(line)
        );
        expect(deprecated).toEqual([]);
    });

    // The V2 vamsSchema registration custom resources invoke the import lambda built in
    // ApiBuilder2.
    test("pipelineBuilderNestedStack depends on apiBuilder2NestedStack", () => {
        expect(source).toContain(
            "pipelineBuilderNestedStack.addStackDependency(apiBuilder2NestedStack)"
        );
    });
});

describe("setupSecurityAndLoggingEnvironmentAndPermissions", () => {
    const buildLambdaWithAuthGrants = () => {
        const app = new cdk.App();
        const stack = new cdk.Stack(app, "GrantStack");
        const table = (id: string) =>
            new dynamodb.Table(stack, id, {
                partitionKey: { name: "pk", type: dynamodb.AttributeType.STRING },
            });
        const logGroup = (id: string) => new logs.LogGroup(stack, id);
        const fun = new lambda.Function(stack, "Fn", {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "index.handler",
            code: lambda.Code.fromInline("def handler(e, c): pass"),
        });
        const resources = {
            dynamo: {
                authEntitiesStorageTable: table("AuthEntitiesTable"),
                constraintsStorageTable: table("ConstraintsTable"),
                userRolesStorageTable: table("UserRolesTable"),
                rolesStorageTable: table("RolesTable"),
            },
            cloudWatchAuditLogGroups: {
                authentication: logGroup("AuthenticationAuditLogGroup"),
                authorization: logGroup("AuthorizationAuditLogGroup"),
                fileUpload: logGroup("FileUploadAuditLogGroup"),
                fileDownload: logGroup("FileDownloadAuditLogGroup"),
                fileDownloadStreamed: logGroup("FileDownloadStreamedAuditLogGroup"),
                authOther: logGroup("AuthOtherAuditLogGroup"),
                authChanges: logGroup("AuthChangesAuditLogGroup"),
                actions: logGroup("ActionsAuditLogGroup"),
                errors: logGroup("ErrorsAuditLogGroup"),
            },
        } as unknown as storageResources;

        setupSecurityAndLoggingEnvironmentAndPermissions(fun, resources);
        return { stack, resources };
    };

    // Collects the logical IDs referenced as Resource on every dynamodb:GetItem statement.
    const dynamoReadResourceRefs = (template: Template): string[] => {
        const policies = template.findResources("AWS::IAM::Policy");
        const refs: string[] = [];
        for (const policy of Object.values(policies) as any[]) {
            for (const statement of policy.Properties.PolicyDocument.Statement) {
                const actions = ([] as string[]).concat(statement.Action ?? []);
                if (!actions.some((a) => a.startsWith("dynamodb:"))) {
                    continue;
                }
                refs.push(JSON.stringify(statement.Resource));
            }
        }
        return refs;
    };

    test("grants read on the three auth tables the backend resolves", () => {
        const { stack, resources } = buildLambdaWithAuthGrants();
        const refs = dynamoReadResourceRefs(Template.fromStack(stack)).join(" ");
        for (const table of [
            resources.dynamo.constraintsStorageTable,
            resources.dynamo.userRolesStorageTable,
            resources.dynamo.rolesStorageTable,
        ]) {
            expect(refs).toContain(JSON.stringify(stack.resolve(table.tableArn)));
        }
    });

    // No backend handler resolves AUTH_ENTITIES_STORAGE_TABLE, so no VAMS lambda should hold a
    // grant on it.
    test("grants nothing on the auth entities table", () => {
        const { stack, resources } = buildLambdaWithAuthGrants();
        const refs = dynamoReadResourceRefs(Template.fromStack(stack)).join(" ");
        expect(refs).not.toContain(
            JSON.stringify(stack.resolve(resources.dynamo.authEntitiesStorageTable.tableArn))
        );
    });
});
