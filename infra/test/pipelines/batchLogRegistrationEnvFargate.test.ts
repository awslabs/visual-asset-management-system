/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Fargate Batch pipelines register their container log group as a per-stage log source, and four
 * facts have to agree for the execution log view to show container output: the registered group must
 * be the VAMS-owned vended group the job definition actually writes to through the `awslogs` driver
 * (not Batch's default `/aws/batch/job`, which these job definitions never touch); its ARN must be a
 * form the backend validator accepts; the job definition NAME the producer builds its
 * `<name>/default/` prefix from must be a name derived from that job definition (a raw Ref is the ARN
 * with a revision and matches nothing) AND must equal the job definition's `awslogs-stream-prefix`,
 * so the real stream `<prefix>/default/<task-id>` falls under the registered prefix and
 * `plan_source_read` reads it un-scoped; and the stage name attached must be a state of the
 * synthesized state machine. None of the four fails at synth or at registration — each surfaces only
 * as an empty log source.
 */

import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import { Preview3dThumbnailConstruct } from "../../lib/nestedStacks/pipelines/preview/3dThumbnail/constructs/preview3dThumbnail-construct";
import { PcPotreeViewerConstruct } from "../../lib/nestedStacks/pipelines/preview/pcPotreeViewer/constructs/pcPotreeViewer-construct";
import { Metadata3dLabelingConstruct } from "../../lib/nestedStacks/pipelines/genAi/metadata3dLabeling/constructs/metadata3dLabeling-construct";
import { CoordinateTransformConstruct } from "../../lib/nestedStacks/pipelines/conversion/coordinateTransform/constructs/coordinateTransform-construct";
import { VideoSopBomConstruct } from "../../lib/nestedStacks/pipelines/genAi/videoSopBom/constructs/videoSopBom-construct";
import { makePipelineHarness } from "../support/pipelineConstructHarness";
import {
    declaredStageNames,
    jobDefinitionRefOf,
    lambdaEnvironmentsByHandler,
    parseAsl,
    singleStateMachine,
} from "../support/asl";

const PIPELINE_LOG_GROUP_PREFIX = "/aws/vendedlogs/Pipelines/";
const PRODUCERS = path.resolve(__dirname, "..", "..", "..", "backendPipelines");

/** The environment of the one lambda in the template whose Handler is `handler`. */
export const registeringLambdaEnv = (template: Template, handler: string): Record<string, any> => {
    const envs = lambdaEnvironmentsByHandler(template, handler);
    expect(envs).toHaveLength(1);
    return envs[0];
};

/** `value` derives a job definition NAME from a job definition that exists in the template. */
export const expectDerivedJobDefinitionName = (template: Template, value: any): string => {
    const ref = jobDefinitionRefOf(value);
    expect(ref).toBeDefined();
    expect(template.findResources("AWS::Batch::JobDefinition")[ref!]).toBeDefined();
    return ref!;
};

/**
 * The registered group (`nameValue` / `arnValue`, the producer's `*_LOG_GROUP_NAME` / `_ARN` env) is
 * the vended group the job definition `jobDefinitionId` writes to, and that job definition's stream
 * prefix is its own name — so a stream `<JobDefinitionName>/default/<task-id>` starts with the
 * `<JobDefinitionName>/default/` prefix the producer registers from `nameValue`'s sibling env.
 */
export const expectVendedGroupRegistration = (
    template: Template,
    jobDefinitionId: string,
    nameValue: any,
    arnValue: any
) => {
    // A LogGroup resource's name renders as a Ref and its ARN as GetAtt Arn.
    const groupId = nameValue?.Ref;
    expect(groupId).toBeDefined();
    expect(arnValue).toEqual({ "Fn::GetAtt": [groupId, "Arn"] });

    const group = template.findResources("AWS::Logs::LogGroup")[groupId];
    expect(group).toBeDefined();
    const groupName = JSON.stringify(group.Properties.LogGroupName);
    expect(groupName).toContain(PIPELINE_LOG_GROUP_PREFIX);
    expect(groupName).not.toContain("/aws/batch/job");
    // KMS-encrypted, the property the relocation off Batch's default group was made for.
    expect(group.Properties.KmsKeyId).toBeDefined();

    const jd = template.findResources("AWS::Batch::JobDefinition")[jobDefinitionId].Properties;
    const lc = jd.ContainerProperties.LogConfiguration;
    expect(lc.LogDriver).toBe("awslogs");
    // The container writes to the SAME group the producer registers.
    expect(lc.Options["awslogs-group"]).toEqual({ Ref: groupId });
    // The stream prefix is the physical job definition name, which is what the producer's derived
    // `*_JOB_DEFINITION_NAME` resolves to at deploy time. With the unhashed base name here, the
    // resolved stream would sit outside the registered prefix and every container line be filtered out.
    expect(typeof jd.JobDefinitionName).toBe("string");
    expect(lc.Options["awslogs-stream-prefix"]).toBe(jd.JobDefinitionName);
};

/** Every stage name the producer declares is a state of the synthesized machine. */
export const expectDeclaredStagesInAsl = (template: Template, producerFile: string) => {
    const declared = declaredStageNames(producerFile);
    // Control: a producer that declares nothing would make the loop below assert nothing.
    expect(declared.length).toBeGreaterThan(0);
    const states = Object.keys(parseAsl(singleStateMachine(template)).States);
    for (const name of declared) {
        expect(states).toContain(name);
    }
};

describe("preview/3dThumbnail openPipeline registration environment", () => {
    let template: Template;

    beforeAll(() => {
        const h = makePipelineHarness("Preview3dThumbnailEnvStack", (c) => {
            c.app.pipelines.usePreview3dThumbnail.enabled = true;
            c.app.pipelines.usePreview3dThumbnail.autoRegisterWithVAMS = false;
        });
        // The construct IS a NestedStack, so its resources live in its own template.
        const nested = new Preview3dThumbnailConstruct(h.stack, "Preview3dThumbnailPipeline", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        template = Template.fromStack(nested);
    });

    test("registers the vended group its job definition writes to, under the job definition's own stream prefix", () => {
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        const jobDefinitionId = expectDerivedJobDefinitionName(
            template,
            env.BATCH_JOB_DEFINITION_NAME
        );
        expectVendedGroupRegistration(
            template,
            jobDefinitionId,
            env.BATCH_JOB_LOG_GROUP_NAME,
            env.BATCH_JOB_LOG_GROUP_ARN
        );
    });

    test("every stage name the producer declares is a state of the machine", () => {
        expectDeclaredStagesInAsl(
            template,
            path.join(PRODUCERS, "preview", "3dThumbnail", "lambda", "openPipeline.py")
        );
    });
});

describe("preview/pcPotreeViewer openPipeline registration environment", () => {
    let template: Template;

    beforeAll(() => {
        const h = makePipelineHarness("PcPotreeViewerEnvStack", (c) => {
            c.app.pipelines.usePreviewPcPotreeViewer.enabled = true;
            c.app.pipelines.usePreviewPcPotreeViewer.autoRegisterWithVAMS = false;
        });
        const nested = new PcPotreeViewerConstruct(h.stack, "PcPotreeViewerPipeline", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        template = Template.fromStack(nested);
    });

    test("registers one vended group per Batch job definition, each under that job's own stream prefix", () => {
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        // No shared Batch-default pair: the two jobs write to two groups.
        expect(env.BATCH_JOB_LOG_GROUP_NAME).toBeUndefined();
        expect(env.BATCH_JOB_LOG_GROUP_ARN).toBeUndefined();
        const pdal = expectDerivedJobDefinitionName(template, env.PDAL_JOB_DEFINITION_NAME);
        const potree = expectDerivedJobDefinitionName(template, env.POTREE_JOB_DEFINITION_NAME);
        // Two converters, two job definitions: the same name on both would attribute every PDAL
        // stream to the Potree stage as well.
        expect(pdal).not.toEqual(potree);
        expect(Object.keys(template.findResources("AWS::Batch::JobDefinition"))).toHaveLength(2);
        expectVendedGroupRegistration(
            template,
            pdal,
            env.PDAL_JOB_LOG_GROUP_NAME,
            env.PDAL_JOB_LOG_GROUP_ARN
        );
        expectVendedGroupRegistration(
            template,
            potree,
            env.POTREE_JOB_LOG_GROUP_NAME,
            env.POTREE_JOB_LOG_GROUP_ARN
        );
        expect(env.PDAL_JOB_LOG_GROUP_NAME).not.toEqual(env.POTREE_JOB_LOG_GROUP_NAME);
    });

    test("both stage names the producer declares are states of the machine", () => {
        expectDeclaredStagesInAsl(
            template,
            path.join(PRODUCERS, "preview", "pcPotreeViewer", "lambda", "openPipeline.py")
        );
    });
});

describe("genAi/metadata3dLabeling openPipeline registration environment", () => {
    let template: Template;

    beforeAll(() => {
        const h = makePipelineHarness("Metadata3dLabelingEnvStack", (c) => {
            c.app.pipelines.useGenAiMetadata3dLabeling.enabled = true;
            c.app.pipelines.useGenAiMetadata3dLabeling.autoRegisterWithVAMS = false;
        });
        const nested = new Metadata3dLabelingConstruct(h.stack, "Metadata3dLabelingPipeline", {
            config: h.config,
            storageResources: h.storage,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        template = Template.fromStack(nested);
    });

    test("registers the Blender job's vended group under the job definition's own stream prefix", () => {
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        const jobDefinitionId = expectDerivedJobDefinitionName(
            template,
            env.BATCH_JOB_DEFINITION_NAME
        );
        expectVendedGroupRegistration(
            template,
            jobDefinitionId,
            env.BATCH_JOB_LOG_GROUP_NAME,
            env.BATCH_JOB_LOG_GROUP_ARN
        );
    });

    test("names the metadata-generation function's log group without a LogRetention resource", () => {
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        const [metadataFunctionId] = Object.entries(
            template.findResources("AWS::Lambda::Function")
        ).find(
            ([, fn]) =>
                (fn as any).Properties.Handler === "metadataGenerationPipeline.lambda_handler"
        )!;
        // `/aws/lambda/<functionName>`, built from the function's name token.
        const name = JSON.stringify(env.METADATA_GENERATION_LOG_GROUP_NAME);
        expect(name).toContain("/aws/lambda/");
        expect(name).toContain(`{"Ref":"${metadataFunctionId}"}`);
        const arn = JSON.stringify(env.METADATA_GENERATION_LOG_GROUP_ARN);
        expect(arn).toContain(`:log-group:/aws/lambda/`);
        expect(arn).toContain(`{"Ref":"${metadataFunctionId}"}`);
        // Reading `fn.logGroup` would have synthesized this resource plus its singleton lambda.
        expect(template.findResources("Custom::LogRetention")).toEqual({});
    });

    test("both stage names the producer declares are states of the machine", () => {
        expectDeclaredStagesInAsl(
            template,
            path.join(PRODUCERS, "genAi", "metadata3dLabeling", "lambda", "openPipeline.py")
        );
    });
});

describe("conversion/coordinateTransform executeBatchJob registration environment", () => {
    let template: Template;

    beforeAll(() => {
        const h = makePipelineHarness("CoordTransformEnvStack", (c) => {
            c.app.pipelines.useConversionCoordinateTransform.enabled = true;
            // Sources the container image from an ECR repository instead of a local Docker build.
            c.app.pipelines.useConversionCoordinateTransform.useCodeBuild = true;
            c.app.pipelines.useConversionCoordinateTransform.autoRegisterWithVAMS = false;
        });
        new CoordinateTransformConstruct(h.stack, "CoordinateTransformPipeline", {
            config: h.config,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            assetAuxiliaryBucket: h.assetAuxiliaryBucket,
            storageResources: h.storage,
            kmsKey: h.kmsKey,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        template = Template.fromStack(h.stack);
    });

    test("the job-submitting lambda registers the vended group beside its job definition name", () => {
        const env = registeringLambdaEnv(template, "executeBatchJob.lambda_handler");
        const jobDefinitionId = expectDerivedJobDefinitionName(template, env.BATCH_JOB_DEFINITION);
        expectVendedGroupRegistration(
            template,
            jobDefinitionId,
            env.BATCH_JOB_LOG_GROUP_NAME,
            env.BATCH_JOB_LOG_GROUP_ARN
        );
    });

    test("the stage the producer declares is a state of the machine", () => {
        expectDeclaredStagesInAsl(
            template,
            path.join(
                PRODUCERS,
                "conversion",
                "coordinateTransform",
                "lambda",
                "executeBatchJob.py"
            )
        );
    });
});

describe("genAi/videoSopBom openPipeline registration environment", () => {
    let template: Template;

    beforeAll(() => {
        const h = makePipelineHarness("VideoSopBomEnvStack", (c) => {
            c.app.pipelines.useGenAiVideoSopBom.enabled = true;
            // Sources the container image from an ECR repository instead of a local Docker build.
            c.app.pipelines.useGenAiVideoSopBom.useCodeBuild = true;
            c.app.pipelines.useGenAiVideoSopBom.autoRegisterWithVAMS = false;
        });
        new VideoSopBomConstruct(h.stack, "VideoSopBomPipeline", {
            config: h.config,
            vpc: h.vpc,
            pipelineSubnets: h.subnets,
            pipelineSecurityGroups: h.securityGroups,
            lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
            assetAuxiliaryBucket: h.assetAuxiliaryBucket,
            storageResources: h.storage,
            kmsKey: h.kmsKey,
            importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
        });
        template = Template.fromStack(h.stack);
    });

    test("registers the vended group its job definition writes to, under the job definition's own stream prefix", () => {
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        const jobDefinitionId = expectDerivedJobDefinitionName(
            template,
            env.BATCH_JOB_DEFINITION_NAME
        );
        expectVendedGroupRegistration(
            template,
            jobDefinitionId,
            env.BATCH_JOB_LOG_GROUP_NAME,
            env.BATCH_JOB_LOG_GROUP_ARN
        );
    });

    test("the stage the producer declares is a state of the machine", () => {
        expectDeclaredStagesInAsl(
            template,
            path.join(PRODUCERS, "genAi", "videoSopBom", "lambda", "openPipeline.py")
        );
    });
});
