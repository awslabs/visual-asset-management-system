/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * How the Image to 3D jobs run: as the images' non-root user without `privileged`, with each role
 * carrying only what its holder uses, the Blender agent on a CPU queue without the HuggingFace token, and
 * the model cache generation reaching the job as both its prefix and the generation the container keys
 * its EFS directory on.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import {
    ImageTo3dConstruct,
    imageTo3dModelCachePrefix,
} from "../../lib/nestedStacks/pipelines/genAi/imageTo3d/constructs/imageTo3d-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

function synth(mutate: (c: Config.Config) => void): Template {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = true;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    const p = config.app.pipelines.useImageTo3d;
    p.enabled = true;
    p.huggingFaceToken = "hf_regressionTestTokenValue";
    p.autoRegisterWithVAMS = false;
    p.useCodeBuild = false;
    mutate(config);
    Service.SetConfig(config);

    const app = newTestApp();
    const stack = new cdk.Stack(app, "ImageTo3dHardeningStack", {
        env: { account: ACCOUNT, region: REGION },
    });
    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const assetBucket = new s3.Bucket(stack, "AssetBucket");
    s3AssetBuckets.getS3AssetBucketRecords().length = 0;
    s3AssetBuckets.addS3AssetBucket(assetBucket, "/", "db", undefined, undefined, true);
    const storage = {
        encryption: { kmsKey: new kms.Key(stack, "Key") },
        s3: {
            assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
            artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
        },
        eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
    } as unknown as storageResources;

    new ImageTo3dConstruct(stack, "ImageTo3dPipeline", {
        config,
        storageResources: storage,
        vpc,
        pipelineSubnets: vpc.privateSubnets,
        pipelineSecurityGroups: [new ec2.SecurityGroup(stack, "Sg", { vpc })],
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        modelCacheBucket: new s3.Bucket(stack, "ModelCacheBucket"),
        efsFileSystem: {
            fileSystemId: "fs-0123456789abcdef0",
            fileSystemArn: `arn:aws:elasticfilesystem:${REGION}:${ACCOUNT}:file-system/fs-0123456789abcdef0`,
        } as any,
        efsSecurityGroup: new ec2.SecurityGroup(stack, "EfsSg", { vpc }),
    });
    return Template.fromStack(stack);
}

/** Job definitions by the MODEL each one's environment names. */
function jobDefinitions(template: Template): Record<string, any> {
    const out: Record<string, any> = {};
    for (const resource of Object.values(template.findResources("AWS::Batch::JobDefinition"))) {
        const props = (resource as any).Properties.ContainerProperties;
        const model = props.Environment.find((e: any) => e.Name === "MODEL").Value;
        out[model] = props;
    }
    return out;
}

function env(props: any, name: string): unknown {
    return props.Environment.find((e: any) => e.Name === name)?.Value;
}

/** Logical id of the role a job definition names in `field`. */
function roleId(props: any, field: "JobRoleArn" | "ExecutionRoleArn"): string {
    return props[field]["Fn::GetAtt"][0];
}

/** The state machine definition as one string, each `Ref` written as `${Ref:<logical id>}`. */
function definitionText(template: Template): string {
    const machines = Object.values(template.findResources("AWS::StepFunctions::StateMachine"));
    expect(machines).toHaveLength(1);
    const render = (part: any): string =>
        typeof part === "string"
            ? part
            : part.Ref
            ? `\${Ref:${part.Ref}}`
            : part["Fn::GetAtt"]
            ? `\${GetAtt:${part["Fn::GetAtt"].join(".")}}`
            : JSON.stringify(part);
    const definition = (machines[0] as any).Properties.DefinitionString;
    return typeof definition === "string"
        ? definition
        : definition["Fn::Join"][1].map(render).join(definition["Fn::Join"][0]);
}

/** Logical id of the job queue the model's Batch state submits to. */
function queueOfState(template: Template, model: string): string {
    const text = definitionText(template);
    const match = new RegExp(
        `"ImageTo3dBatchJob-${model}":\\{.*?"JobQueue":"\\$\\{Ref:([^}]+)\\}"`
    ).exec(text);
    expect(match).not.toBeNull();
    return match![1];
}

describe("Image to 3D jobs with a GPU model and the Blender agent enabled", () => {
    let template: Template;
    let jobs: Record<string, any>;

    beforeAll(() => {
        template = synth((c) => {
            const m = c.app.pipelines.useImageTo3d.models;
            m.stable3dgen.enabled = true;
            m.triposr.enabled = true;
            m.blenderagent.enabled = true;
            m.blenderagent.bedrockModelId = "global.openai.gpt-6-astra";
        });
        jobs = jobDefinitions(template);
    });

    it("[control] synthesizes a job definition per enabled model", () => {
        expect(Object.keys(jobs).sort()).toEqual(["blenderagent", "stable3dgen", "triposr"]);
    });

    it("no job definition is privileged or maps host devices", () => {
        for (const props of Object.values(jobs)) {
            expect(props.Privileged).toBeUndefined();
            expect(props.LinuxParameters?.Devices).toBeUndefined();
            // The image's USER runs; an override here would replace it.
            expect(props.User).toBeUndefined();
        }
    });

    it("a GPU model reserves its GPU through the resource requirement and gets the token", () => {
        for (const model of ["stable3dgen", "triposr"]) {
            expect(jobs[model].ResourceRequirements).toEqual([{ Type: "GPU", Value: "1" }]);
            expect(jobs[model].Secrets.map((s: any) => s.Name)).toEqual(["HF_TOKEN"]);
        }
    });

    it("the Blender agent reserves no GPU and gets no HuggingFace token", () => {
        expect(jobs.blenderagent.ResourceRequirements).toBeUndefined();
        expect(jobs.blenderagent.Secrets).toBeUndefined();
    });

    it("the Blender agent is submitted to a CPU queue on the standard ECS AMI", () => {
        const environments = template.findResources("AWS::Batch::ComputeEnvironment");
        const cpu = Object.entries(environments).filter(([, r]) =>
            (r as any).Properties.ComputeResources.Ec2Configuration.some(
                (e: any) => e.ImageType === "ECS_AL2023"
            )
        );
        expect(cpu).toHaveLength(1);
        expect((cpu[0][1] as any).Properties.ComputeResources.InstanceTypes).toEqual(["optimal"]);
        expect((cpu[0][1] as any).Properties.ComputeResources.MinvCpus).toBe(0);

        const cpuQueues = Object.entries(template.findResources("AWS::Batch::JobQueue")).filter(
            ([, q]) =>
                (q as any).Properties.ComputeEnvironmentOrder.some(
                    (o: any) => o.ComputeEnvironment.Ref === cpu[0][0]
                )
        );
        expect(cpuQueues).toHaveLength(1);
        const cpuQueueId = cpuQueues[0][0];

        // Each model's Batch state names its queue; only the agent's names the CPU one.
        expect(queueOfState(template, "blenderagent")).toBe(cpuQueueId);
        expect(queueOfState(template, "triposr")).not.toBe(cpuQueueId);
        expect(queueOfState(template, "triposr")).toMatch(
            /^ImageTo3dPipelineImageTo3dBatchJobQueue/
        );
    });

    it("pipelineEnd can end a timed-out job on the CPU queue too", () => {
        const functions = Object.values(template.findResources("AWS::Lambda::Function")) as any[];
        const queues = functions
            .map((f) => f.Properties.Environment?.Variables?.BATCH_JOB_QUEUES)
            .filter(Boolean);
        expect(queues).toHaveLength(1);
        expect(JSON.stringify(queues[0])).toContain("ImageTo3dCpuBatchJobQueue");
    });

    it("the execution role holds only the ECS execution policy, no data access", () => {
        const executionRoleId = roleId(jobs.triposr, "ExecutionRoleArn");
        const role = template.findResources("AWS::IAM::Role")[executionRoleId] as any;
        expect(role.Properties.Policies).toBeUndefined();
        expect(JSON.stringify(role.Properties.ManagedPolicyArns)).toContain(
            "AmazonECSTaskExecutionRolePolicy"
        );
        expect(JSON.stringify(role.Properties.ManagedPolicyArns)).not.toContain("AWSXray");

        // Whatever is attached to it as a separate policy is the token read and the image pull.
        const attached = Object.values(template.findResources("AWS::IAM::Policy")).filter((p) =>
            JSON.stringify((p as any).Properties.Roles).includes(executionRoleId)
        );
        const actions = new Set<string>();
        for (const policy of attached) {
            for (const st of (policy as any).Properties.PolicyDocument.Statement) {
                for (const a of [].concat(st.Action)) actions.add(a);
            }
        }
        expect([...actions].filter((a) => a.startsWith("s3:"))).toEqual([]);
        expect(actions.has("secretsmanager:GetSecretValue")).toBe(true);
    });

    it("the job role carries no ECS execution policy and deletes nothing", () => {
        const jobRoleId = roleId(jobs.triposr, "JobRoleArn");
        const role = template.findResources("AWS::IAM::Role")[jobRoleId] as any;
        expect(JSON.stringify(role.Properties.ManagedPolicyArns)).not.toContain(
            "AmazonECSTaskExecutionRolePolicy"
        );
        expect(JSON.stringify(role.Properties.Policies)).not.toContain("s3:DeleteObject");
        // [control] the data policies did move here rather than vanish.
        expect(JSON.stringify(role.Properties.Policies)).toContain("s3:PutObject");
    });

    it("instance user data gives the model cache to the containers' uid", () => {
        const templates = Object.values(
            template.findResources("AWS::EC2::LaunchTemplate")
        ) as any[];
        expect(templates).toHaveLength(1);
        const userData = JSON.stringify(templates[0].Properties.LaunchTemplateData.UserData);
        expect(userData).toContain("chown -R 10000:10000 /mnt/efs/imageto3d-models");
    });

    it("generation 0 keeps the cache prefix every deployment has used", () => {
        for (const [model, props] of Object.entries(jobs)) {
            expect(env(props, "S3_MODEL_PREFIX")).toBe(`imageTo3d/hf_cache/${model}`);
            expect(env(props, "MODEL_CACHE_GENERATION")).toBe("0");
        }
    });
});

describe("Image to 3D model cache generation", () => {
    it("a later generation moves every model to its own prefix", () => {
        const jobs = jobDefinitions(
            synth((c) => {
                c.app.pipelines.useImageTo3d.modelCacheGeneration = 2;
            })
        );
        expect(env(jobs.stable3dgen, "S3_MODEL_PREFIX")).toBe("imageTo3d/hf_cache/g2/stable3dgen");
        expect(env(jobs.stable3dgen, "MODEL_CACHE_GENERATION")).toBe("2");
    });

    it("the prefix rule matches the container's model_cache_path", () => {
        // model_manager.model_cache_path: hf_cache/<model> at 0, hf_cache/g<N>/<model> above it.
        expect(imageTo3dModelCachePrefix("triposr", 0)).toBe("imageTo3d/hf_cache/triposr");
        expect(imageTo3dModelCachePrefix("triposr", 5)).toBe("imageTo3d/hf_cache/g5/triposr");
    });

    it("no CPU environment exists unless the Blender agent is enabled", () => {
        const template = synth(() => undefined);
        const cpu = Object.values(template.findResources("AWS::Batch::ComputeEnvironment")).filter(
            (r) =>
                (r as any).Properties.ComputeResources.Ec2Configuration.some(
                    (e: any) => e.ImageType === "ECS_AL2023"
                )
        );
        expect(cpu).toEqual([]);
    });

    it.each([-1, 1.5, "1"])("the configuration refuses a generation of %p", (value) => {
        const imageTo3d = JSON.parse(
            JSON.stringify(commercialTemplate.app.pipelines.useImageTo3d)
        ) as Config.Config["app"]["pipelines"]["useImageTo3d"];
        imageTo3d.modelCacheGeneration = value as any;
        expect(() => Config.validateImageTo3dModelCacheGeneration(imageTo3d)).toThrow(
            /modelCacheGeneration must be a whole number of 0 or more/
        );
    });

    it.each([0, 3])("the configuration accepts a generation of %p", (value) => {
        const imageTo3d = JSON.parse(
            JSON.stringify(commercialTemplate.app.pipelines.useImageTo3d)
        ) as Config.Config["app"]["pipelines"]["useImageTo3d"];
        imageTo3d.modelCacheGeneration = value;
        expect(() => Config.validateImageTo3dModelCacheGeneration(imageTo3d)).not.toThrow();
    });
});
