/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Batch pipelines register their container log group as a per-stage log source, and three facts
 * have to agree for that registration to resolve a stream: BATCH_JOB_LOG_GROUP_ARN must be the
 * colon-separated form the backend validator accepts; the job definition NAME the stream prefix is
 * built from must be a name (a raw Ref is the ARN with a revision and matches nothing); and the stage
 * name attached must be a state of the synthesized state machine. None of the three fails at synth
 * or at registration — each surfaces only as an empty log source.
 */

import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import { Preview3dThumbnailConstruct } from "../../lib/nestedStacks/pipelines/preview/3dThumbnail/constructs/preview3dThumbnail-construct";
import { PcPotreeViewerConstruct } from "../../lib/nestedStacks/pipelines/preview/pcPotreeViewer/constructs/pcPotreeViewer-construct";
import { Metadata3dLabelingConstruct } from "../../lib/nestedStacks/pipelines/genAi/metadata3dLabeling/constructs/metadata3dLabeling-construct";
import { CoordinateTransformConstruct } from "../../lib/nestedStacks/pipelines/conversion/coordinateTransform/constructs/coordinateTransform-construct";
import { ACCOUNT, REGION, makePipelineHarness } from "../support/pipelineConstructHarness";
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
export const registeringLambdaEnv = (template: Template, handler: string): Record<string, any> => {
    const envs = lambdaEnvironmentsByHandler(template, handler);
    expect(envs).toHaveLength(1);
    return envs[0];
};

/** The Batch default group name and colon-form ARN, exactly as the producer helper reads them. */
export const expectBatchLogGroupEnv = (env: Record<string, any>) => {
    expect(env.BATCH_JOB_LOG_GROUP_NAME).toEqual("/aws/batch/job");
    expect(env.BATCH_JOB_LOG_GROUP_ARN).toEqual(BATCH_LOG_GROUP_ARN);
    expect(env.BATCH_JOB_LOG_GROUP_ARN).toContain(":log-group:/aws/batch/job");
};

/** `value` derives a job definition NAME from a job definition that exists in the template. */
export const expectDerivedJobDefinitionName = (template: Template, value: any): string => {
    const ref = jobDefinitionRefOf(value);
    expect(ref).toBeDefined();
    expect(template.findResources("AWS::Batch::JobDefinition")[ref!]).toBeDefined();
    return ref!;
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

    test("carries the Batch default log group and a derived job definition name", () => {
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        expectBatchLogGroupEnv(env);
        expectDerivedJobDefinitionName(template, env.BATCH_JOB_DEFINITION_NAME);
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

    test("carries the Batch default log group and one derived name per Batch job definition", () => {
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        expectBatchLogGroupEnv(env);
        const pdal = expectDerivedJobDefinitionName(template, env.PDAL_JOB_DEFINITION_NAME);
        const potree = expectDerivedJobDefinitionName(template, env.POTREE_JOB_DEFINITION_NAME);
        // Two converters, two job definitions: the same name on both would attribute every PDAL
        // stream to the Potree stage as well.
        expect(pdal).not.toEqual(potree);
        expect(Object.keys(template.findResources("AWS::Batch::JobDefinition"))).toHaveLength(2);
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

    test("carries the Batch default log group and a derived job definition name", () => {
        const env = registeringLambdaEnv(template, "openPipeline.lambda_handler");
        expectBatchLogGroupEnv(env);
        expectDerivedJobDefinitionName(template, env.BATCH_JOB_DEFINITION_NAME);
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

    test("the job-submitting lambda carries the Batch default log group beside its job definition name", () => {
        const env = registeringLambdaEnv(template, "executeBatchJob.lambda_handler");
        expectBatchLogGroupEnv(env);
        expectDerivedJobDefinitionName(template, env.BATCH_JOB_DEFINITION);
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
