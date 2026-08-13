/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Locks in five properties of the GenAI pipelines that fail silently, or fail late, at runtime.
 *
 *   1. metadata3dLabeling: the Blender container image directory must resolve with EXACT case.
 *      `cdk synth` reads it from disk, so a mismatched segment blocks the whole deployment on any
 *      case-sensitive filesystem while appearing to exist on Windows/macOS.
 *   2. metadata3dLabeling: ConstructPipelineTask must catch to the pipelineEnd path. pipelineEnd is
 *      the only state that resolves the parent workflow's task token, so a failure at the first
 *      state otherwise leaves that task RUNNING until its own taskTimeout.
 *   3. Cosmos/Gr00t: the HuggingFace token must never appear in the synthesized template. The
 *      secret is created empty and populated by a custom resource carrying the value in its code
 *      asset.
 *   4. Cosmos Reason/Transfer + Gr00t: the state machine timeout must ENVELOPE the Batch attempt
 *      duration, and the role must be able to describe/terminate the `.sync` job it submitted.
 *   5. Cosmos CodeBuild: each family's opt-in is scoped to that family, so a family left on the
 *      local Docker build never receives a CodeBuild-built ECR image.
 */

import * as fs from "fs";
import * as path from "path";
import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as events from "aws-cdk-lib/aws-events";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import * as s3AssetBuckets from "../lib/helper/s3AssetBuckets";
import { storageResources } from "../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { Metadata3dLabelingConstruct } from "../lib/nestedStacks/pipelines/genAi/metadata3dLabeling/constructs/metadata3dLabeling-construct";
import { CosmosCodeBuildConstruct } from "../lib/nestedStacks/pipelines/genAi/nvidia/cosmos/constructs/cosmosCodeBuild-construct";
import { CosmosReasonConstruct } from "../lib/nestedStacks/pipelines/genAi/nvidia/cosmos/constructs/cosmosReason-construct";
import { CosmosTransferConstruct } from "../lib/nestedStacks/pipelines/genAi/nvidia/cosmos/constructs/cosmosTransfer-construct";
import { Gr00tFinetuneConstruct } from "../lib/nestedStacks/pipelines/genAi/nvidia/gr00t/constructs/gr00tFinetune-construct";
import commercialTemplate from "../config/config.template.commercial.json";

const ACCOUNT = "123456789012";
const REGION = "us-east-1";
const HF_TOKEN = "hf_regressionTestTokenValue";

const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = REGION;
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.app.useGlobalVpc.enabled = true;
    config.app.useGlobalVpc.useForAllLambdas = false;
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

interface Harness {
    stack: cdk.Stack;
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
    lambdaCommonBaseLayer: lambda.LayerVersion;
    storage: storageResources;
    modelCacheBucket: s3.Bucket;
    efsFileSystem: any;
    efsSecurityGroup: ec2.SecurityGroup;
}

const makeHarness = (id: string, mutate?: (c: Config.Config) => void): Harness => {
    const config = createMockConfig();
    mutate?.(config);
    Service.SetConfig(config);

    const app = new cdk.App();
    const stack = new cdk.Stack(app, id, { env: { account: ACCOUNT, region: REGION } });

    const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2 });
    const securityGroups = [new ec2.SecurityGroup(stack, "Sg", { vpc })];

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

    return {
        stack,
        config,
        vpc,
        subnets: vpc.privateSubnets,
        securityGroups,
        lambdaCommonBaseLayer: lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "Layer",
            `arn:aws:lambda:${REGION}:${ACCOUNT}:layer:vams-test-common:1`
        ) as lambda.LayerVersion,
        storage,
        modelCacheBucket: new s3.Bucket(stack, "ModelCacheBucket"),
        // Imported so the constructs do not stand up a real EFS mount target set.
        efsFileSystem: { fileSystemId: "fs-0123456789abcdef0" },
        efsSecurityGroup: new ec2.SecurityGroup(stack, "EfsSg", { vpc }),
    };
};

/** The ASL of the single state machine in the template, with tokens collapsed. */
const stateMachineDefinitions = (template: Template): Record<string, string> => {
    const out: Record<string, string> = {};
    for (const [logicalId, resource] of Object.entries(
        template.findResources("AWS::StepFunctions::StateMachine")
    )) {
        const definition = (resource as any).Properties.DefinitionString;
        const parts = definition?.["Fn::Join"]?.[1] ?? [definition];
        out[logicalId] = parts
            .map((p: unknown) => (typeof p === "string" ? p : "<<TOKEN>>"))
            .join("");
    }
    return out;
};

const actionsOf = (statement: any): string[] =>
    Array.isArray(statement.Action) ? statement.Action : [statement.Action];

/** Every action granted to roles whose policy logical id contains the fragment. */
const roleActions = (template: Template, roleIdFragment: string): string[] =>
    Object.entries(template.findResources("AWS::IAM::Policy"))
        .filter(([logicalId]) => logicalId.includes(roleIdFragment))
        .flatMap(([, policy]) => (policy as any).Properties.PolicyDocument.Statement)
        .flatMap(actionsOf);

describe("metadata3dLabeling container image path", () => {
    test("resolves to an exact-case directory on disk", () => {
        // The path the construct passes is resolved relative to the shared batch-fargate construct.
        const batchConstructDir = path.resolve(
            __dirname,
            "..",
            "lib",
            "nestedStacks",
            "pipelines",
            "constructs"
        );
        const source = fs.readFileSync(
            path.resolve(
                __dirname,
                "..",
                "lib/nestedStacks/pipelines/genAi/metadata3dLabeling/constructs/metadata3dLabeling-construct.ts"
            ),
            "utf-8"
        );
        const segments = /imageAssetPath:\s*path\.join\(([^)]*)\)/.exec(source);
        expect(segments).not.toBeNull();
        const parts = segments![1]
            .split(",")
            .map((s) => s.trim().replace(/^"|"$/g, ""))
            .filter((s) => s.length > 0);

        const resolved = path.resolve(batchConstructDir, path.join(...parts));

        // fs.existsSync is case-INSENSITIVE on Windows, so compare each segment against the real
        // directory listing — that is what a case-sensitive filesystem enforces at synth time.
        let cursor = path.parse(resolved).root;
        for (const segment of resolved.slice(cursor.length).split(path.sep)) {
            expect(fs.readdirSync(cursor)).toContain(segment);
            cursor = path.join(cursor, segment);
        }
    });
});

describe("metadata3dLabeling processing state machine", () => {
    // The construct IS a NestedStack, so the state machine is in its own template — the parent
    // holds only an AWS::CloudFormation::Stack placeholder and Template.fromStack does not descend.
    let template: Template;

    beforeAll(() => {
        const h = makeHarness("Metadata3dLabelingTestStack", (c) => {
            c.app.pipelines.useGenAiMetadata3dLabeling.enabled = true;
            c.app.pipelines.useGenAiMetadata3dLabeling.autoRegisterWithVAMS = false;
        });
        const pipeline = new Metadata3dLabelingConstruct(h.stack, "Metadata3dLabelingPipeline", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        template = Template.fromStack(pipeline);
    });

    test("every task catches to a handler that reaches PipelineEndTask", () => {
        const definitions = Object.values(stateMachineDefinitions(template));
        expect(definitions).toHaveLength(1);
        const asl = JSON.parse(definitions[0]);

        const taskStates = Object.entries(asl.States).filter(
            ([name, state]: [string, any]) => state.Type === "Task" && name !== "PipelineEndTask"
        ) as [string, any][];

        // Each catch handler is a Pass that hands off to the state releasing the task token.
        const catchTargets = Object.fromEntries(
            taskStates.map(([name, state]) => [
                name,
                state.Catch === undefined ? undefined : asl.States[state.Catch[0].Next].Next,
            ])
        );
        expect(catchTargets).toEqual({
            ConstructPipelineTask: "PipelineEndTask",
            BlenderRendererBatchJob: "PipelineEndTask",
            MetadataGenerationLambdaFunctionTask: "PipelineEndTask",
        });
    });
});

describe.each([
    [
        "CosmosReason",
        (h: Harness) =>
            new CosmosReasonConstruct(h.stack, "CosmosReasonPipeline", {
                config: h.config,
                storageResources: h.storage,
                vpc: h.vpc,
                pipelineSubnets: h.subnets,
                pipelineSecurityGroups: h.securityGroups,
                lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
                importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
                modelCacheBucket: h.modelCacheBucket,
                efsFileSystem: h.efsFileSystem,
                efsSecurityGroup: h.efsSecurityGroup,
                codeBuildImageUri: "123456789012.dkr.ecr.us-east-1.amazonaws.com/reason:latest",
            }),
        (c: Config.Config) => {
            c.app.pipelines.useNvidiaCosmos.enabled = true;
            c.app.pipelines.useNvidiaCosmos.huggingFaceToken = HF_TOKEN;
            c.app.pipelines.useNvidiaCosmos.modelsReason!.reason2B.enabled = true;
            c.app.pipelines.useNvidiaCosmos.modelsReason!.reason2B.autoRegisterWithVAMS = false;
        },
    ],
    [
        "CosmosTransfer",
        (h: Harness) =>
            new CosmosTransferConstruct(h.stack, "CosmosTransferPipeline", {
                config: h.config,
                storageResources: h.storage,
                vpc: h.vpc,
                pipelineSubnets: h.subnets,
                pipelineSecurityGroups: h.securityGroups,
                lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
                importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
                modelCacheBucket: h.modelCacheBucket,
                efsFileSystem: h.efsFileSystem,
                efsSecurityGroup: h.efsSecurityGroup,
                codeBuildImageUri: "123456789012.dkr.ecr.us-east-1.amazonaws.com/transfer:latest",
            }),
        (c: Config.Config) => {
            c.app.pipelines.useNvidiaCosmos.enabled = true;
            c.app.pipelines.useNvidiaCosmos.huggingFaceToken = HF_TOKEN;
            c.app.pipelines.useNvidiaCosmos.modelsTransfer!.transfer2B.enabled = true;
            c.app.pipelines.useNvidiaCosmos.modelsTransfer!.transfer2B.autoRegisterWithVAMS = false;
        },
    ],
    [
        "Gr00tFinetune",
        (h: Harness) =>
            new Gr00tFinetuneConstruct(h.stack, "Gr00tFinetune", {
                config: h.config,
                storageResources: h.storage,
                vpc: h.vpc,
                pipelineSubnets: h.subnets,
                pipelineSecurityGroups: h.securityGroups,
                lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
                importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
                modelCacheBucket: h.modelCacheBucket,
                efsFileSystem: h.efsFileSystem,
                efsSecurityGroup: h.efsSecurityGroup,
                codeBuildImageUri: "123456789012.dkr.ecr.us-east-1.amazonaws.com/gr00t:latest",
            }),
        (c: Config.Config) => {
            c.app.pipelines.useNvidiaGr00t.enabled = true;
            c.app.pipelines.useNvidiaGr00t.huggingFaceToken = HF_TOKEN;
            c.app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.enabled = true;
            c.app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.autoRegisterWithVAMS = false;
        },
    ],
])("%s GPU pipeline", (name, build, mutate) => {
    let template: Template;
    let templateJson: string;

    beforeAll(() => {
        const h = makeHarness(`${name}TestStack`, mutate);
        build(h);
        template = Template.fromStack(h.stack);
        templateJson = JSON.stringify(template.toJSON());
    });

    test("state machine timeout envelopes the Batch attempt duration", () => {
        const jobDefinitions = Object.values(
            template.findResources("AWS::Batch::JobDefinition")
        ) as any[];
        expect(jobDefinitions.length).toBeGreaterThan(0);
        const attemptSeconds = Math.max(
            ...jobDefinitions.map((j) => j.Properties.Timeout.AttemptDurationSeconds)
        );

        const definitions = Object.values(stateMachineDefinitions(template));
        expect(definitions.length).toBeGreaterThan(0);
        for (const asl of definitions) {
            // The execution timeout is the trailing TimeoutSeconds, outside the "States" object.
            const executionTimeout = /"TimeoutSeconds":(\d+)\}$/.exec(asl);
            expect(executionTimeout).not.toBeNull();
            // Strictly greater: pipelineEnd releases the task token, and an execution-level
            // States.Timeout is not routed through any task's Catch.
            expect(Number(executionTimeout![1])).toBeGreaterThan(attemptSeconds);
        }
    });

    test("state machine role can describe and terminate the .sync Batch job", () => {
        const actions = roleActions(template, "StateMachineRoleDefaultPolicy");
        expect(actions).toContain("batch:SubmitJob");
        expect(actions).toContain("batch:DescribeJobs");
        expect(actions).toContain("batch:TerminateJob");
    });

    test("HuggingFace token is absent from the synthesized template", () => {
        const secrets = Object.values(
            template.findResources("AWS::SecretsManager::Secret")
        ) as any[];
        expect(secrets.length).toBeGreaterThan(0);
        for (const secret of secrets) {
            expect(secret.Properties.SecretString).toBeUndefined();
        }
        expect(templateJson).not.toContain(HF_TOKEN);

        // A custom resource populates the secret at deploy time from its code asset.
        const populators = Object.entries(
            template.findResources("AWS::CloudFormation::CustomResource")
        ).filter(([logicalId]) => logicalId.includes("HfTokenSecretPopulate"));
        expect(populators).toHaveLength(secrets.length);
        for (const [, resource] of populators) {
            const props = (resource as any).Properties;
            expect(props.SecretArn).toBeDefined();
            // A one-way digest, so a rotated token re-runs the populator without leaking the value.
            expect(props.tokenVersion).toMatch(/^[0-9a-f]{64}$/);
            expect(props.tokenVersion).not.toContain(HF_TOKEN);
        }
    });
});

describe("Cosmos CodeBuild opt-in is scoped per family", () => {
    const cosmosRepoKeys = ["predict-v2", "reason", "transfer"];

    const buildTemplate = (
        id: string,
        buildCosmosRepos: boolean,
        buildCosmos3Repos: boolean
    ): Template => {
        const h = makeHarness(id, (c) => {
            c.app.pipelines.useNvidiaCosmos.enabled = true;
            c.app.pipelines.useNvidiaCosmos.huggingFaceToken = HF_TOKEN;
            c.app.pipelines.useNvidiaCosmos.useCodeBuild = buildCosmosRepos;
            c.app.pipelines.useNvidiaCosmos.modelsPredict.text2world2B_v2.enabled = true;
            c.app.pipelines.useNvidiaCosmos.modelsReason!.reason2B.enabled = true;
            c.app.pipelines.useNvidiaCosmos.modelsTransfer!.transfer2B.enabled = true;
            c.app.pipelines.useNvidiaCosmos3.enabled = true;
            c.app.pipelines.useNvidiaCosmos3.huggingFaceToken = HF_TOKEN;
            c.app.pipelines.useNvidiaCosmos3.useCodeBuild = buildCosmos3Repos;
            c.app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B.enabled = true;
        });

        new CosmosCodeBuildConstruct(h.stack, "CosmosCodeBuild", {
            config: h.config,
            modelCacheBucket: h.modelCacheBucket,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            buildCosmosRepos,
            buildCosmos3Repos,
        });
        return Template.fromStack(h.stack);
    };

    /** The pipelineKey of every CodeBuild project created, read off its description. */
    const builtKeys = (template: Template): string[] =>
        (Object.values(template.findResources("AWS::CodeBuild::Project")) as any[])
            .map((p) => /^Build Cosmos (\S+) container image/.exec(p.Properties.Description)?.[1])
            .filter((k): k is string => k !== undefined)
            .sort();

    test("Cosmos3-only opt-in builds only the cosmos3 repo", () => {
        const template = buildTemplate("Cosmos3OnlyCodeBuildStack", false, true);
        expect(builtKeys(template)).toEqual(["cosmos3"]);
        // No ECR repo, and therefore no `<repo>:latest` image, for the families that opted out.
        expect(Object.keys(template.findResources("AWS::ECR::Repository"))).toHaveLength(1);
    });

    test("Cosmos-only opt-in builds only the Predict/Reason/Transfer repos", () => {
        const template = buildTemplate("CosmosOnlyCodeBuildStack", true, false);
        expect(builtKeys(template)).toEqual([...cosmosRepoKeys].sort());
        expect(Object.keys(template.findResources("AWS::ECR::Repository"))).toHaveLength(3);
    });

    test("both families opting in builds every enabled repo", () => {
        const template = buildTemplate("BothCodeBuildStack", true, true);
        expect(builtKeys(template)).toEqual([...cosmosRepoKeys, "cosmos3"].sort());
    });
});
