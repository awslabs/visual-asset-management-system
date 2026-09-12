/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The shared Fargate construct sizes and logs a container the way its caller asks, and every caller that
 * asks for nothing keeps the sizing it always had.
 *
 * `BatchFargatePipelineConstruct` hardcoded 16 vCPU / 64 GiB and set no `logging`, so every Fargate job
 * paid the point-cloud sizing and wrote its stdout to AWS Batch's default `aws/batch/job` group — a group
 * with no KMS key and no retention. The video SOP/BOM pipeline needs 4 vCPU / 16 GiB and a KMS-encrypted
 * log group; the other five job definitions must not move. Asserted on the emitted
 * `AWS::Batch::JobDefinition`, because the size and the log group AWS Batch receives are what run.
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

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

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
        stack = new cdk.Stack(app, "FargateSizingTestStack", {
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
        // 16 vCPU / 64 GiB is what the five existing job definitions were built with; a changed default
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
