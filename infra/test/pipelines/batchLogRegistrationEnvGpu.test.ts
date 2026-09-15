/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * GPU-family counterpart of batchLogRegistrationEnvFargate.test.ts. The job definition NAME differs
 * by family: the splat toolbox names its CfnJobDefinition, so the env carries a plain string that
 * must equal the emitted JobDefinitionName; cosmos and gr00t leave theirs unnamed and derive the
 * name from the Ref; Isaac Lab's EcsJobDefinition already carries the derived name as
 * BATCH_JOB_DEFINITION on the lambda that submits the job.
 */

import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import { SplatToolboxConstruct } from "../../lib/nestedStacks/pipelines/3dRecon/splatToolbox/constructs/splatToolbox-construct";
import { Cosmos3Construct } from "../../lib/nestedStacks/pipelines/genAi/nvidia/cosmos/constructs/cosmos3-construct";
import { CosmosPredictConstruct } from "../../lib/nestedStacks/pipelines/genAi/nvidia/cosmos/constructs/cosmosPredict-construct";
import { CosmosReasonConstruct } from "../../lib/nestedStacks/pipelines/genAi/nvidia/cosmos/constructs/cosmosReason-construct";
import { CosmosTransferConstruct } from "../../lib/nestedStacks/pipelines/genAi/nvidia/cosmos/constructs/cosmosTransfer-construct";
import { Gr00tFinetuneConstruct } from "../../lib/nestedStacks/pipelines/genAi/nvidia/gr00t/constructs/gr00tFinetune-construct";
import { IsaacLabTrainingConstruct } from "../../lib/nestedStacks/pipelines/simulation/isaacLabTraining/constructs/isaacLabTraining-construct";
import {
    ACCOUNT,
    PipelineHarness,
    REGION,
    makePipelineHarness,
} from "../support/pipelineConstructHarness";
import {
    declaredStageNames,
    jobDefinitionRefOf,
    lambdaEnvironmentsByHandler,
    parseAsl,
    singleStateMachine,
} from "../support/asl";

const BATCH_LOG_GROUP_ARN = `arn:aws:logs:${REGION}:${ACCOUNT}:log-group:/aws/batch/job`;
const PRODUCERS = path.resolve(__dirname, "..", "..", "..", "backendPipelines");

/** The environment of the one lambda in the template whose Handler is `handler`. */
const registeringLambdaEnv = (template: Template, handler: string): Record<string, any> => {
    const envs = lambdaEnvironmentsByHandler(template, handler);
    expect(envs).toHaveLength(1);
    return envs[0];
};

/** The Batch default group name and colon-form ARN, exactly as the producer helper reads them. */
const expectBatchLogGroupEnv = (env: Record<string, any>) => {
    expect(env.BATCH_JOB_LOG_GROUP_NAME).toEqual("/aws/batch/job");
    expect(env.BATCH_JOB_LOG_GROUP_ARN).toEqual(BATCH_LOG_GROUP_ARN);
    expect(env.BATCH_JOB_LOG_GROUP_ARN).toContain(":log-group:/aws/batch/job");
};

/** `value` derives a job definition NAME from a job definition that exists in the template. */
const expectDerivedJobDefinitionName = (template: Template, value: any) => {
    const ref = jobDefinitionRefOf(value);
    expect(ref).toBeDefined();
    expect(template.findResources("AWS::Batch::JobDefinition")[ref!]).toBeDefined();
};

/** Every stage name the producer declares is a state of the synthesized machine. */
const expectDeclaredStagesInAsl = (template: Template, producerFile: string) => {
    const declared = declaredStageNames(producerFile);
    expect(declared.length).toBeGreaterThan(0);
    const states = Object.keys(parseAsl(singleStateMachine(template)).States);
    for (const name of declared) {
        expect(states).toContain(name);
    }
};

describe("3dRecon/splatToolbox openPipeline registration environment", () => {
    let template: Template;

    beforeAll(() => {
        const h = makePipelineHarness("SplatToolboxEnvStack", (c) => {
            c.app.pipelines.useSplatToolbox.autoRegisterWithVAMS = false;
        });
        // The constructor syncs the pinned upstream container sources over the network; mark the
        // pinned commit as already synced so only the CDK resources are exercised.
        (SplatToolboxConstruct as any).syncedCommit = SplatToolboxConstruct.GITHUB_REPO_COMMIT_HASH;
        new SplatToolboxConstruct(h.stack, "SplatToolboxPipeline", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
            codeBuildImage: h.codeBuildImage,
        });
        template = Template.fromStack(h.stack);
    });

    test("carries the Batch default log group and the GPU job definition's own name", () => {
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        expectBatchLogGroupEnv(env);
        const jobDefinitions = Object.values(
            template.findResources("AWS::Batch::JobDefinition")
        ) as any[];
        expect(jobDefinitions).toHaveLength(1);
        // The CfnJobDefinition is named, so the env is that literal name — never `.ref`, which is
        // the ARN with a revision.
        expect(typeof env.BATCH_JOB_DEFINITION_NAME).toBe("string");
        expect(env.BATCH_JOB_DEFINITION_NAME).toEqual(
            jobDefinitions[0].Properties.JobDefinitionName
        );
        expect(env.BATCH_JOB_DEFINITION_NAME).not.toContain(":");
    });

    test("every stage name the producer declares is a state of the machine", () => {
        expectDeclaredStagesInAsl(
            template,
            path.join(PRODUCERS, "3dRecon", "splatToolbox", "lambda", "openPipeline.py")
        );
    });
});

const HF_TOKEN = "hf_regressionTestTokenValue";

describe.each([
    [
        "CosmosPredict",
        "text2world2B_v2",
        (h: PipelineHarness) =>
            new CosmosPredictConstruct(h.stack, "CosmosPredictPipeline", {
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
                codeBuildImageUri: "123456789012.dkr.ecr.us-east-1.amazonaws.com/predict:latest",
            }),
        (c: Config.Config) => {
            c.app.pipelines.useNvidiaCosmos.enabled = true;
            c.app.pipelines.useNvidiaCosmos.huggingFaceToken = HF_TOKEN;
            c.app.pipelines.useNvidiaCosmos.modelsPredict!.text2world2B_v2.enabled = true;
            c.app.pipelines.useNvidiaCosmos.modelsPredict!.text2world2B_v2.autoRegisterWithVAMS =
                false;
        },
    ],
    [
        "CosmosReason",
        "reason2B",
        (h: PipelineHarness) =>
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
        "transfer2B",
        (h: PipelineHarness) =>
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
        "Cosmos3",
        "nano16B",
        (h: PipelineHarness) =>
            new Cosmos3Construct(h.stack, "Cosmos3Pipeline", {
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
                codeBuildImageUri: "123456789012.dkr.ecr.us-east-1.amazonaws.com/cosmos3:latest",
            }),
        (c: Config.Config) => {
            c.app.pipelines.useNvidiaCosmos3.enabled = true;
            c.app.pipelines.useNvidiaCosmos3.huggingFaceToken = HF_TOKEN;
            c.app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B!.enabled = true;
            c.app.pipelines.useNvidiaCosmos3.modelsOmni.nano16B!.autoRegisterWithVAMS = false;
        },
    ],
])("%s openPipeline registration environment", (name, modelKey, build, mutate) => {
    let template: Template;

    beforeAll(() => {
        const h = makePipelineHarness(`${name}EnvStack`, mutate);
        build(h);
        template = Template.fromStack(h.stack);
    });

    test("carries the Batch default log group and a name derived from the model's job definition", () => {
        // One model enabled, so one openPipeline function.
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        expectBatchLogGroupEnv(env);
        expectDerivedJobDefinitionName(template, env.BATCH_JOB_DEFINITION_NAME);
    });

    test("names the model's Batch state, which is a state of the machine", () => {
        // The producer reads the stage name from this env var (no literal to read off the source),
        // so the env value itself is joined to the ASL.
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        expect(env.COSMOS_BATCH_STATE_NAME).toEqual(`CosmosBatchJob-${modelKey}`);
        const states = Object.keys(parseAsl(singleStateMachine(template)).States);
        expect(states).toContain(env.COSMOS_BATCH_STATE_NAME);
    });
});

describe("genAi/nvidia/gr00t openPipeline registration environment", () => {
    let template: Template;

    beforeAll(() => {
        const h = makePipelineHarness("Gr00tFinetuneEnvStack", (c) => {
            c.app.pipelines.useNvidiaGr00t.enabled = true;
            c.app.pipelines.useNvidiaGr00t.huggingFaceToken = HF_TOKEN;
            c.app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.enabled = true;
            c.app.pipelines.useNvidiaGr00t.modelsFinetune.gr00tN1_5_3B.autoRegisterWithVAMS = false;
        });
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
        });
        template = Template.fromStack(h.stack);
    });

    test("carries the Batch default log group and a derived job definition name", () => {
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        expectBatchLogGroupEnv(env);
        expectDerivedJobDefinitionName(template, env.BATCH_JOB_DEFINITION_NAME);
    });

    test("every stage name the producer declares is a state of the machine", () => {
        expectDeclaredStagesInAsl(
            template,
            path.join(PRODUCERS, "genAi", "nvidia", "gr00t", "lambda", "openPipeline.py")
        );
    });
});

describe("simulation/isaacLabTraining executeBatchJob registration environment", () => {
    let template: Template;

    beforeAll(() => {
        const h = makePipelineHarness("IsaacLabEnvStack", (c) => {
            c.app.pipelines.useIsaacLabTraining.autoRegisterWithVAMS = false;
        });
        new IsaacLabTrainingConstruct(h.stack, "IsaacLabTrainingConstruct", {
            config: h.config,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSubnetsIsolated: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            storageResources: h.storage,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
            codeBuildImage: h.codeBuildImage,
        });
        template = Template.fromStack(h.stack);
    });

    test("the job-submitting lambda carries the Batch default log group beside its job definition name", () => {
        // The job is submitted under WAIT_FOR_TASK_TOKEN by this lambda, so it — not vamsExecute —
        // is the one that knows the job and registers its container log source.
        const env = registeringLambdaEnv(template, "executeBatchJob.lambda_handler");
        expectBatchLogGroupEnv(env);
        expectDerivedJobDefinitionName(template, env.BATCH_JOB_DEFINITION);
    });

    test("the stage the producer declares is a state of the machine", () => {
        expectDeclaredStagesInAsl(
            template,
            path.join(PRODUCERS, "simulation", "isaacLabTraining", "lambda", "executeBatchJob.py")
        );
    });
});
