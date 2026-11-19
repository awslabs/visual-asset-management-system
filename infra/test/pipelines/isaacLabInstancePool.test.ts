/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Isaac Lab compute environment's instance pool (issue #1308).
 *
 * One Isaac Lab job asks AWS Batch for 8 vCPUs, 32,768 MiB and 1 GPU. Batch accepts a compute
 * environment whose instance types cannot hold that job and then leaves the job RUNNABLE with no error,
 * so the pool has to be right at synthesis:
 *
 *   - every type fits the job after the ECS agent's registered-memory overhead (g5.2xlarge and
 *     g6.2xlarge have 32 GiB installed and register less, so they never fit);
 *   - every type has one GPU with RT cores and 16 GB or more of video memory (Isaac Sim needs both);
 *   - no CPU-only "optimal" class is added;
 *   - a warm instance (`keepWarmInstance`) is sized from the job, so the instance that stays up can run it.
 *
 * `getConfig()` reads `config/config.json` from disk, so the validation cases mock `fs.readFileSync` to
 * serve a chosen template; the pattern, including the warning against `jest.resetModules()`, is the one
 * `cosmos3InstanceTypeGpuCount.test.ts` documents.
 */

import * as fs from "fs";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as ecr from "aws-cdk-lib/aws-ecr";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import * as Service from "../../lib/helper/service-helper";
import * as s3AssetBuckets from "../../lib/helper/s3AssetBuckets";
import { storageResources } from "../../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { IsaacLabTrainingConstruct } from "../../lib/nestedStacks/pipelines/simulation/isaacLabTraining/constructs/isaacLabTraining-construct";
import commercialTemplate from "../../config/config.template.commercial.json";
import govcloudTemplate from "../../config/config.template.govcloud.json";
import eusovereignTemplate from "../../config/config.template.eusovereign.json";
import configJson from "../../config/config.json";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

const ACCOUNT = "123456789012";
const REGION = "us-east-1";

// What the job definition asks for, written out here rather than imported so that a change to the
// constants in config.ts fails this suite and gets looked at.
const JOB_VCPUS = 8;
const JOB_MEMORY_MIB = 32768;

describe("Isaac Lab instance types: configuration validation", () => {
    const serveConfig = (json: unknown) => {
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(
            (path: string, ...rest: unknown[]) => {
                if (typeof path === "string" && path.endsWith("config.json")) {
                    return JSON.stringify(json);
                }
                return realReadFileSync(path, ...rest);
            }
        );
    };

    // Isaac Lab needs the global VPC, and govcloud is the shipped template that already has it on.
    const loadConfig = (mutate?: (c: any) => void) => {
        const config = JSON.parse(JSON.stringify(govcloudTemplate));
        config.env.region = "us-gov-west-1";
        config.env.account = ACCOUNT;
        config.app.baseStackName = "vamstest";
        if (config.app.useAlb?.enabled) {
            config.app.useAlb.domainHost = "vams.example.com";
            config.app.useAlb.certificateArn =
                "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
        }
        config.app.pipelines.useIsaacLabTraining.enabled = true;
        config.app.pipelines.useIsaacLabTraining.acceptNvidiaEula = true;
        mutate?.(config);
        serveConfig(config);
        return () => Config.getConfig(newTestApp());
    };

    const withInstanceTypes = (instanceTypes: unknown) =>
        loadConfig((c) => {
            c.app.pipelines.useIsaacLabTraining.instanceTypes = instanceTypes;
        });

    let warnSpy: jest.SpyInstance;

    beforeEach(() => {
        warnSpy = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    });

    afterEach(() => {
        warnSpy.mockRestore();
        // Put the real reader back rather than resetting the mock: the synthesis cases below hash
        // real files, and a reset mock returns undefined for them.
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    test.each([
        ["commercial", commercialTemplate],
        ["govcloud", govcloudTemplate],
        ["eusovereign", eusovereignTemplate],
        ["config.json", configJson],
    ])("the %s file ships the default instance types", (_name, template) => {
        expect((template as any).app.pipelines.useIsaacLabTraining.instanceTypes).toEqual(
            Config.DEFAULT_ISAAC_LAB_INSTANCE_TYPES
        );
    });

    test("the default list is accepted and survives getConfig() unchanged", () => {
        const run = loadConfig();
        expect(run).not.toThrow();
        expect(run().app.pipelines.useIsaacLabTraining.instanceTypes).toEqual(
            Config.DEFAULT_ISAAC_LAB_INSTANCE_TYPES
        );
        expect(warnSpy).not.toHaveBeenCalledWith(expect.stringContaining("Isaac"));
    });

    test("a config with no instanceTypes gets the default list", () => {
        const run = loadConfig((c) => {
            delete c.app.pipelines.useIsaacLabTraining.instanceTypes;
        });
        expect(run().app.pipelines.useIsaacLabTraining.instanceTypes).toEqual(
            Config.DEFAULT_ISAAC_LAB_INSTANCE_TYPES
        );
    });

    test("a defaulted list is a copy, so one config cannot change another's", () => {
        const run = loadConfig((c) => {
            delete c.app.pipelines.useIsaacLabTraining.instanceTypes;
        });
        run().app.pipelines.useIsaacLabTraining.instanceTypes.push("g6e.8xlarge");
        expect(Config.DEFAULT_ISAAC_LAB_INSTANCE_TYPES).toHaveLength(11);
    });

    test("every default type fits the job, has one GPU and clears the registered-memory overhead", () => {
        // The assertion that would have caught #1308 at source: g5.2xlarge and g6.2xlarge were listed.
        for (const instanceType of Config.DEFAULT_ISAAC_LAB_INSTANCE_TYPES) {
            expect(Config.GPU_COUNT_BY_INSTANCE_TYPE[instanceType]).toBe(1);
            expect(instanceType).toMatch(/^(g5|g6|gr6|g6e)\./);
        }
        expect(() =>
            Config.validateIsaacLabInstanceTypes([...Config.DEFAULT_ISAAC_LAB_INSTANCE_TYPES], "x")
        ).not.toThrow();
    });

    test("the default list has no instance type that registers less memory than the job asks for", () => {
        expect(Config.estimatedRegisteredMemoryMiB(32768)).toBeLessThan(JOB_MEMORY_MIB);
        expect(Config.estimatedRegisteredMemoryMiB(65536)).toBeGreaterThanOrEqual(JOB_MEMORY_MIB);
    });

    test.each(["g5.2xlarge", "g6.2xlarge"])(
        "%s is rejected: 32 GiB installed registers below the job's memory",
        (instanceType) => {
            // These two were in the shipped list, and Batch never placed a job on them.
            expect(withInstanceTypes([instanceType])).toThrow(
                /registers about \d+ MiB with Amazon ECS/
            );
        }
    );

    test("a type with fewer vCPUs than the job is rejected", () => {
        expect(withInstanceTypes(["g6.xlarge"])).toThrow(/has 4 vCPUs/);
        // g6e.xlarge has the memory (32 GiB registers short too) but only 4 vCPUs; vCPUs are read first.
        expect(withInstanceTypes(["g6e.xlarge"])).toThrow(/has 4 vCPUs/);
    });

    test.each(["g5.12xlarge", "g6.12xlarge", "g6e.12xlarge", "g6e.48xlarge", "g5.48xlarge"])(
        "the multi-GPU type %s is rejected",
        (instanceType) => {
            // The four-GPU sizes the old list carried bill for GPUs the job never uses.
            expect(withInstanceTypes([instanceType])).toThrow(/which has \d+ GPUs/);
        }
    );

    test("g6e.16xlarge has one GPU despite its name and is accepted", () => {
        // The size is not a guide to the GPU count: g6e.12xlarge has four, g6e.16xlarge one.
        expect(withInstanceTypes(["g6e.16xlarge"])).not.toThrow();
    });

    test.each([
        ["g4dn.4xlarge", /T4/],
        ["p4d.24xlarge", /no RT cores/],
        ["p5.48xlarge", /no RT cores/],
        ["p3.2xlarge", /no RT cores/],
    ])("%s is rejected: Isaac Sim needs RT cores and 16 GB of video memory", (type, reason) => {
        expect(withInstanceTypes([type])).toThrow(reason);
    });

    test("one bad entry in an otherwise valid list is still rejected", () => {
        // Batch would place jobs on the valid types and never use the bad one, so the configuration
        // looks fine until capacity for the good types runs out.
        expect(withInstanceTypes(["g6e.2xlarge", "g6.2xlarge"])).toThrow(/g6\.2xlarge/);
    });

    test("an unknown instance type is warned about, not rejected", () => {
        const run = withInstanceTypes(["g7e.2xlarge"]);
        expect(run).not.toThrow();
        run();
        expect(warnSpy).toHaveBeenCalledWith(
            expect.stringContaining("g7e.2xlarge, whose size and GPU VAMS cannot verify")
        );
    });

    test.each([[[]], [[""]], ["g6e.2xlarge"], [[1]], [[null]]])(
        "%j is rejected as not a list of instance type names",
        (value) => {
            expect(withInstanceTypes(value)).toThrow(/non-empty list of EC2 instance type names/);
        }
    );

    test("the rule does not fire when the pipeline is disabled", () => {
        // A disabled pipeline creates no compute environment, so its instance types cannot strand a job.
        const run = loadConfig((c) => {
            c.app.pipelines.useIsaacLabTraining.enabled = false;
            c.app.pipelines.useIsaacLabTraining.instanceTypes = ["g6.2xlarge"];
        });
        expect(run).not.toThrow();
    });
});

describe("Isaac Lab instance types: synthesized compute environment", () => {
    const createMockConfig = (mutate?: (c: Config.Config) => void): Config.Config => {
        const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
        config.env.account = ACCOUNT;
        config.env.region = REGION;
        config.env.partition = "aws";
        config.env.coreStackName = "vams-test-us-east-1";
        config.app.baseStackName = "vams-test";
        config.app.useGlobalVpc.enabled = true;
        config.app.useGlobalVpc.useForAllLambdas = false;
        config.app.pipelines.useIsaacLabTraining.enabled = true;
        config.app.pipelines.useIsaacLabTraining.acceptNvidiaEula = true;
        config.app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS = false;
        config.enableCdkNag = false;
        config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
        mutate?.(config);
        return config;
    };

    const synth = (mutate?: (c: Config.Config) => void): Template => {
        const config = createMockConfig(mutate);
        Service.SetConfig(config);

        const app = newTestApp();
        const stack = new cdk.Stack(app, "IsaacLabInstancePoolTestStack", {
            env: { account: ACCOUNT, region: REGION },
        });

        const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
        const kmsKey = new kms.Key(stack, "Key");

        s3AssetBuckets.getS3AssetBucketRecords().length = 0;
        s3AssetBuckets.addS3AssetBucket(new s3.Bucket(stack, "AssetBucket"), "/", "db");

        const storage = {
            encryption: { kmsKey },
            s3: {
                assetAuxiliaryBucket: new s3.Bucket(stack, "AuxBucket"),
                artefactsBucket: new s3.Bucket(stack, "ArtefactsBucket"),
            },
            eventBridge: { orchestrationBus: new events.EventBus(stack, "Bus") },
        } as unknown as storageResources;

        new IsaacLabTrainingConstruct(stack, "IsaacLabTrainingPipeline", {
            config,
            vpc,
            pipelineSubnets: vpc.privateSubnets,
            pipelineSubnetsIsolated: vpc.isolatedSubnets,
            pipelineSecurityGroups: [new ec2.SecurityGroup(stack, "Sg", { vpc })],
            storageResources: storage,
            lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
                stack,
                "Layer",
                `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
            ) as lambda.LayerVersion,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflowV2",
            // Sources the container image from ECR so synth does not run a local Docker build.
            codeBuildImage: {
                repository: ecr.Repository.fromRepositoryName(stack, "Repo", "isaaclab"),
                tag: "0123456789abcdef0123456789abcdef01234567", // pragma: allowlist secret
            },
        });
        return Template.fromStack(stack);
    };

    const computeResources = (template: Template): any => {
        const envs = Object.values(
            template.findResources("AWS::Batch::ComputeEnvironment")
        ) as any[];
        expect(envs).toHaveLength(1);
        return envs[0].Properties.ComputeResources;
    };

    const jobDefinitionContainer = (template: Template): any => {
        const defs = Object.values(template.findResources("AWS::Batch::JobDefinition")) as any[];
        expect(defs).toHaveLength(1);
        return defs[0].Properties.ContainerProperties;
    };

    test("the job definition asks for the vCPUs, memory and GPU the pool is checked against", () => {
        const container = jobDefinitionContainer(synth());
        const requirements = Object.fromEntries(
            (container.ResourceRequirements as any[]).map((r) => [r.Type, Number(r.Value)])
        );
        expect(requirements.VCPU).toBe(JOB_VCPUS);
        expect(requirements.MEMORY).toBe(JOB_MEMORY_MIB);
        expect(requirements.GPU).toBe(1);
        expect(Config.ISAAC_LAB_JOB_GPUS).toBe(requirements.GPU);
        expect(Config.ISAAC_LAB_JOB_VCPUS).toBe(requirements.VCPU);
        expect(Config.ISAAC_LAB_JOB_MEMORY_MIB).toBe(requirements.MEMORY);
    });

    test("the compute environment lists exactly the configured types and no CPU-only class", () => {
        const resources = computeResources(synth());
        expect(resources.InstanceTypes).toEqual(Config.DEFAULT_ISAAC_LAB_INSTANCE_TYPES);
        // `optimal` is the CPU-only class the CDK adds when useOptimalInstanceClasses is left at its
        // default of true.
        expect(resources.InstanceTypes).not.toContain("optimal");
    });

    test("every synthesized instance type can run the job", () => {
        const resources = computeResources(synth());
        for (const instanceType of resources.InstanceTypes as string[]) {
            expect(Config.GPU_COUNT_BY_INSTANCE_TYPE[instanceType]).toBe(1);
        }
        expect(() =>
            Config.validateIsaacLabInstanceTypes(resources.InstanceTypes, "x")
        ).not.toThrow();
    });

    test("a configured list reaches the compute environment", () => {
        const resources = computeResources(
            synth((c) => {
                c.app.pipelines.useIsaacLabTraining.instanceTypes = ["g6e.2xlarge", "g6e.4xlarge"];
            })
        );
        expect(resources.InstanceTypes).toEqual(["g6e.2xlarge", "g6e.4xlarge"]);
    });

    test("without keepWarmInstance the compute environment scales to zero", () => {
        expect(computeResources(synth()).MinvCpus).toBe(0);
    });

    test("with keepWarmInstance the floor is one job's vCPUs, which every listed type can hold", () => {
        const resources = computeResources(
            synth((c) => {
                c.app.pipelines.useIsaacLabTraining.keepWarmInstance = true;
            })
        );
        expect(resources.MinvCpus).toBe(JOB_VCPUS);
        // The warm instance is whichever listed type Batch starts for the floor. It can run the job
        // only if that type has at least the job's vCPUs and registers at least its memory.
        for (const instanceType of resources.InstanceTypes as string[]) {
            const spec = Config.ISAAC_LAB_CAPABLE_INSTANCE_SPECS[instanceType];
            expect(spec.vcpus).toBeGreaterThanOrEqual(resources.MinvCpus);
            expect(Config.estimatedRegisteredMemoryMiB(spec.memoryMiB)).toBeGreaterThanOrEqual(
                JOB_MEMORY_MIB
            );
        }
    });
});
