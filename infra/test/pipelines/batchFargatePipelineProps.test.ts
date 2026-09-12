/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shared Fargate construct sizes and logs a container the way its caller asks, and every Fargate
 * job definition VAMS deploys writes its container output to a VAMS-owned log group.
 *
 * Part one exercises `BatchFargatePipelineConstruct` directly: `cpu` / `memoryMiB` size the container,
 * the defaults are the 16 vCPU / 64 GiB every existing caller was built against, and `logGroup` routes
 * the container stream through the `awslogs` driver. Asserted on the emitted `AWS::Batch::JobDefinition`,
 * because the size and the log group AWS Batch receives are what run.
 *
 * Part two synthesizes the whole application with every Fargate pipeline enabled and the customer
 * managed key on. Without a `logGroup` AWS Batch writes to its default `/aws/batch/job` group, which
 * sits outside the `/aws/vendedlogs/Pipelines/*` prefix the workflow Lambdas are granted to read, outside
 * the retention aspect, and outside the KMS key — so every Fargate job definition must name a group under
 * that prefix, and that group must carry the key.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as iam from "aws-cdk-lib/aws-iam";
import * as logs from "aws-cdk-lib/aws-logs";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import { BatchFargatePipelineConstruct } from "../../lib/nestedStacks/pipelines/constructs/batch-fargate-pipeline";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

/** The prefix `workflowFunctions.ts` grants the execution-service Lambdas read access to. */
const PIPELINE_LOG_GROUP_PREFIX = "/aws/vendedlogs/Pipelines/";

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    return config;
};

describe("BatchFargatePipelineConstruct sizing and logging props", () => {
    let stack: cdk.Stack;
    let template: Template;
    let logGroup: logs.LogGroup;

    beforeAll(() => {
        const config = createMockConfig();
        const app = newTestApp();
        stack = new cdk.Stack(app, "FargatePropsTestStack", {
            env: { account: ACCOUNT, region: REGION },
        });
        const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
        const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];
        const jobRole = new iam.Role(stack, "JobRole", {
            assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        });
        const executionRole = new iam.Role(stack, "ExecutionRole", {
            assumedBy: new iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        });
        const repository = ecr.Repository.fromRepositoryName(stack, "Repo", "vams-test-repo");
        logGroup = new logs.LogGroup(stack, "ContainerLogGroup");

        // An ECR image so no Dockerfile is read; imageAssetPath is ignored when ecrImage is set.
        const common = {
            config,
            vpc,
            subnets: vpc.privateSubnets,
            securityGroups,
            jobRole,
            executionRole,
            imageAssetPath: "unused-when-ecrImage-is-set",
            dockerfileName: "Dockerfile",
            attemptDuration: cdk.Duration.hours(1),
            ecrImage: { repository, tag: "0123456789abcdef0123456789abcdef" },
        };
        new BatchFargatePipelineConstruct(stack, "Sized", {
            ...common,
            batchJobDefinitionName: "SizedJob_vams-test",
            cpu: 4,
            memoryMiB: 16384,
            logGroup,
        });
        new BatchFargatePipelineConstruct(stack, "Defaulted", {
            ...common,
            batchJobDefinitionName: "DefaultedJob_vams-test",
        });
        template = Template.fromStack(stack);
    });

    /** Properties of the one job definition whose name starts with the prefix. */
    const jobDefinitionNamed = (prefix: string): any => {
        const matches = Object.values(template.findResources("AWS::Batch::JobDefinition")).filter(
            (jd: any) => String(jd.Properties.JobDefinitionName).startsWith(prefix)
        );
        expect(matches).toHaveLength(1);
        return (matches[0] as any).Properties;
    };

    const requirement = (jd: any, type: string): string =>
        jd.ContainerProperties.ResourceRequirements.find((r: any) => r.Type === type).Value;

    test("[control] both job definitions are emitted as Fargate jobs", () => {
        for (const prefix of ["SizedJob_", "DefaultedJob_"]) {
            expect(jobDefinitionNamed(prefix).PlatformCapabilities).toEqual(["FARGATE"]);
        }
    });

    test("cpu and memoryMiB size the container", () => {
        const jd = jobDefinitionNamed("SizedJob_");
        expect(requirement(jd, "VCPU")).toBe("4");
        expect(requirement(jd, "MEMORY")).toBe("16384");
    });

    test("the defaults are the sizing every existing caller receives", () => {
        // 16 vCPU / 64 GiB is what the existing job definitions were built with; a changed default
        // would resize all of them in one deploy.
        const jd = jobDefinitionNamed("DefaultedJob_");
        expect(requirement(jd, "VCPU")).toBe("16");
        expect(requirement(jd, "MEMORY")).toBe("65536");
    });

    test("logGroup routes the container log stream through the awslogs driver", () => {
        const lc = jobDefinitionNamed("SizedJob_").ContainerProperties.LogConfiguration;
        expect(lc.LogDriver).toBe("awslogs");
        expect(lc.Options["awslogs-group"]).toEqual(stack.resolve(logGroup.logGroupName));
        expect(lc.Options["awslogs-stream-prefix"]).toBe("SizedJob_vams-test");
    });

    test("without logGroup no LogConfiguration is rendered, so Batch keeps its default", () => {
        const jd = jobDefinitionNamed("DefaultedJob_");
        expect(jd.ContainerProperties.LogConfiguration).toBeUndefined();
    });
});

/** Enable every pipeline that builds a Fargate job definition, in a VPC, with the CMK on. */
function allFargatePipelinesWithCmk(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.addVpcEndpoints = true;
    c.app.useKmsCmkEncryption.enabled = true;
    for (const flag of [
        "useConversionCoordinateTransform",
        "useGenAiMetadata3dLabeling",
        "usePreview3dThumbnail",
        "usePreviewPcPotreeViewer",
    ]) {
        c.app.pipelines[flag].enabled = true;
        if (c.app.pipelines[flag].autoRegisterWithVAMS !== undefined) {
            c.app.pipelines[flag].autoRegisterWithVAMS = false;
        }
    }
}

/** Fargate job definitions, identified by the platform capability Batch receives. */
function fargateJobDefinitions(synth: SynthResult): Resource[] {
    return synth.ofType("AWS::Batch::JobDefinition").filter((jd) => {
        const capabilities = ((jd.properties as any).PlatformCapabilities ?? []) as string[];
        return capabilities.includes("FARGATE");
    });
}

/**
 * The log group a job definition's `awslogs-group` option refers to, resolved within the job
 * definition's own nested template — the construct passes the group's name as a `Ref`.
 */
function logGroupOf(synth: SynthResult, jd: Resource): Resource {
    const option = (jd.properties as any).ContainerProperties?.LogConfiguration?.Options?.[
        "awslogs-group"
    ];
    expect(option).toBeDefined();
    const logicalId = option.Ref;
    expect(typeof logicalId).toBe("string");
    const group = synth.resources.find(
        (r) => r.stack === jd.stack && r.logicalId === logicalId && r.type === "AWS::Logs::LogGroup"
    );
    expect(group).toBeDefined();
    return group!;
}

describe("every Fargate job definition logs to a VAMS-owned group", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial", {
            mutate: allFargatePipelinesWithCmk,
            mutateKey: "fargate-log-groups-cmk",
        });
    });

    test("[control] the five Fargate job definitions are emitted in this synth", () => {
        // Coordinate transform, Blender renderer, 3D thumbnail, PDAL and Potree. A lower count means a
        // pipeline was left out of the mutate, and the loop below would then assert on nothing for it.
        expect(fargateJobDefinitions(synth)).toHaveLength(5);
    });

    test("each job definition names a group under the prefix the workflow Lambdas can read", () => {
        for (const jd of fargateJobDefinitions(synth)) {
            const lc = (jd.properties as any).ContainerProperties.LogConfiguration;
            expect(lc.LogDriver).toBe("awslogs");
            const group = logGroupOf(synth, jd);
            const name = SynthResult.flatten((group.properties as any).LogGroupName);
            expect(name.startsWith(PIPELINE_LOG_GROUP_PREFIX)).toBe(true);
        }
    });

    test("each group is encrypted with the customer managed key", () => {
        for (const jd of fargateJobDefinitions(synth)) {
            const group = logGroupOf(synth, jd);
            expect((group.properties as any).KmsKeyId).toBeDefined();
        }
    });

    test("no two job definitions share a stream prefix", () => {
        // The stream prefix is what separates one job's output from another's inside a group.
        const prefixes = fargateJobDefinitions(synth).map(
            (jd) =>
                (jd.properties as any).ContainerProperties.LogConfiguration.Options[
                    "awslogs-stream-prefix"
                ]
        );
        expect(new Set(prefixes).size).toBe(prefixes.length);
    });
});
