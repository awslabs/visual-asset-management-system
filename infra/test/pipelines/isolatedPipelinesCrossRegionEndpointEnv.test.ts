/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The four Batch pipelines whose compute runs in the VPC's isolated subnets (Potree viewer, 3D
 * thumbnail, GenAI metadata labeling, coordinate transform) read an input in another Region only
 * through the cross-Region S3 interface endpoint the VPC builder creates in those subnets. Their
 * `vamsExecute` Lambdas therefore carry `CROSS_REGION_S3_ENDPOINT_REGIONS`, the Regions such an
 * endpoint exists for, and `manifestHelper.enforce_inputs_in_region` rejects an input anywhere else
 * before the job is submitted. The value and the endpoints come from one helper
 * (`Config.crossRegionS3EndpointRegions`), and the whole-app synth below holds the two to the same
 * list: a gate that admits a Region with no endpoint starts a job that fails on its first read, and
 * a gate that rejects a Region with an endpoint blocks a working path.
 */

import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../config/config";
import { Preview3dThumbnailConstruct } from "../../lib/nestedStacks/pipelines/preview/3dThumbnail/constructs/preview3dThumbnail-construct";
import { PcPotreeViewerConstruct } from "../../lib/nestedStacks/pipelines/preview/pcPotreeViewer/constructs/pcPotreeViewer-construct";
import { Metadata3dLabelingConstruct } from "../../lib/nestedStacks/pipelines/genAi/metadata3dLabeling/constructs/metadata3dLabeling-construct";
import { CoordinateTransformConstruct } from "../../lib/nestedStacks/pipelines/conversion/coordinateTransform/constructs/coordinateTransform-construct";
import { makePipelineHarness, PipelineHarness } from "../support/pipelineConstructHarness";
import { lambdaEnvironmentsByHandler } from "../support/asl";
import { synthTemplate, SynthResult } from "../support/templateSynth";

const ENV = "CROSS_REGION_S3_ENDPOINT_REGIONS";

/** The four isolated-subnet pipelines: config flag, construct, and the vamsExecute handler. */
const ISOLATED_PIPELINES: {
    name: string;
    handler: string;
    enable: (c: Config.Config) => void;
    build: (h: PipelineHarness) => Template;
}[] = [
    {
        name: "preview/3dThumbnail",
        handler: "vamsExecutePreview3dThumbnailPipeline.lambda_handler",
        enable: (c) => {
            c.app.pipelines.usePreview3dThumbnail.enabled = true;
            c.app.pipelines.usePreview3dThumbnail.autoRegisterWithVAMS = false;
        },
        build: (h) =>
            Template.fromStack(
                new Preview3dThumbnailConstruct(h.stack, "Preview3dThumbnailPipeline", {
                    config: h.config,
                    storageResources: h.storage,
                    vpc: h.vpc,
                    pipelineSubnets: h.subnets,
                    pipelineSecurityGroups: h.securityGroups,
                    lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
                    importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
                })
            ),
    },
    {
        name: "preview/pcPotreeViewer",
        handler: "vamsExecutePreviewPcPotreeViewerPipeline.lambda_handler",
        enable: (c) => {
            c.app.pipelines.usePreviewPcPotreeViewer.enabled = true;
            c.app.pipelines.usePreviewPcPotreeViewer.autoRegisterWithVAMS = false;
        },
        build: (h) =>
            Template.fromStack(
                new PcPotreeViewerConstruct(h.stack, "PcPotreeViewerPipeline", {
                    config: h.config,
                    storageResources: h.storage,
                    vpc: h.vpc,
                    pipelineSubnets: h.subnets,
                    pipelineSecurityGroups: h.securityGroups,
                    lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
                    importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
                })
            ),
    },
    {
        name: "genAi/metadata3dLabeling",
        handler: "vamsExecuteGenAiMetadata3dLabelingPipeline.lambda_handler",
        enable: (c) => {
            c.app.pipelines.useGenAiMetadata3dLabeling.enabled = true;
            c.app.pipelines.useGenAiMetadata3dLabeling.autoRegisterWithVAMS = false;
        },
        build: (h) =>
            Template.fromStack(
                new Metadata3dLabelingConstruct(h.stack, "Metadata3dLabelingPipeline", {
                    config: h.config,
                    storageResources: h.storage,
                    vpc: h.vpc,
                    pipelineSubnets: h.subnets,
                    pipelineSecurityGroups: h.securityGroups,
                    lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
                    importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
                })
            ),
    },
    {
        name: "conversion/coordinateTransform",
        handler: "vamsExecuteCoordinateTransformPipeline.lambda_handler",
        enable: (c) => {
            c.app.pipelines.useConversionCoordinateTransform.enabled = true;
            // Sources the container image from an ECR repository instead of a local Docker build.
            c.app.pipelines.useConversionCoordinateTransform.useCodeBuild = true;
            c.app.pipelines.useConversionCoordinateTransform.autoRegisterWithVAMS = false;
        },
        build: (h) => {
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
            return Template.fromStack(h.stack);
        },
    },
];

/** External asset buckets in `regions` (duplicates allowed), with the VPC endpoint flags as given. */
const withCrossRegionBuckets =
    (
        regions: string[],
        flags: { addVpcEndpoints?: boolean; addCrossRegionS3Endpoints?: boolean } = {}
    ) =>
    (c: Config.Config) => {
        c.app.useGlobalVpc.enabled = true;
        c.app.useGlobalVpc.addVpcEndpoints = flags.addVpcEndpoints ?? true;
        c.app.useGlobalVpc.addCrossRegionS3Endpoints = flags.addCrossRegionS3Endpoints ?? true;
        c.app.assetBuckets.externalAssetBuckets = regions.map((region, i) => ({
            bucketArn: `arn:${c.env.partition}:s3:::remote-assets-${i}`,
            baseAssetsPrefix: "/",
            defaultSyncDatabaseId: `remote${i}`,
            bucketRegion: region,
        })) as unknown as typeof c.app.assetBuckets.externalAssetBuckets;
    };

/** The one vamsExecute Lambda's environment in `template`. */
const vamsExecuteEnv = (template: Template, handler: string): Record<string, unknown> => {
    const envs = lambdaEnvironmentsByHandler(template, handler);
    expect(envs).toHaveLength(1);
    return envs[0];
};

describe("Config.crossRegionS3EndpointRegions", () => {
    const base = () => {
        const h = makePipelineHarness("CrossRegionHelperStack");
        return h.config;
    };

    test("lists each cross-Region bucket Region once, in configuration order, when the endpoints are created", () => {
        const c = base();
        withCrossRegionBuckets(["eu-west-1", "ap-southeast-2", "eu-west-1", "us-east-1"])(c);
        expect(Config.crossRegionS3EndpointRegions(c)).toEqual(["eu-west-1", "ap-southeast-2"]);
    });

    test.each([
        ["addCrossRegionS3Endpoints off", { addCrossRegionS3Endpoints: false }],
        ["addVpcEndpoints off", { addVpcEndpoints: false }],
    ])("is empty with %s: the VPC builder creates no endpoint then", (_label, flags) => {
        const c = base();
        withCrossRegionBuckets(["eu-west-1"], flags)(c);
        expect(Config.crossRegionS3EndpointRegions(c)).toEqual([]);
    });

    test("is empty when the VPC is off, on a context-only synth, and with same-Region buckets only", () => {
        const noVpc = base();
        withCrossRegionBuckets(["eu-west-1"])(noVpc);
        noVpc.app.useGlobalVpc.enabled = false;
        expect(Config.crossRegionS3EndpointRegions(noVpc)).toEqual([]);

        const contextOnly = base();
        withCrossRegionBuckets(["eu-west-1"])(contextOnly);
        contextOnly.env.loadContextIgnoreVPCStacks = true;
        expect(Config.crossRegionS3EndpointRegions(contextOnly)).toEqual([]);

        const sameRegion = base();
        withCrossRegionBuckets([sameRegion.env.region])(sameRegion);
        expect(Config.crossRegionS3EndpointRegions(sameRegion)).toEqual([]);
    });
});

describe.each(ISOLATED_PIPELINES)("$name vamsExecute carries the endpoint Regions", (pipeline) => {
    test("names the cross-Region bucket Regions when the VPC builder creates their endpoints", () => {
        const h = makePipelineHarness(`XRegionEnvOn${pipeline.name.replace(/\W/g, "")}`, (c) => {
            pipeline.enable(c);
            withCrossRegionBuckets(["eu-west-1", "ap-southeast-2", "eu-west-1"])(c);
        });
        const env = vamsExecuteEnv(pipeline.build(h), pipeline.handler);
        expect(env[ENV]).toBe("eu-west-1,ap-southeast-2");
        // The same helper the VPC builder iterates, so the two cannot drift.
        expect(env[ENV]).toBe(Config.crossRegionS3EndpointRegions(h.config).join(","));
    });

    test("is empty when addCrossRegionS3Endpoints is off: every cross-Region input is then rejected", () => {
        const h = makePipelineHarness(`XRegionEnvOff${pipeline.name.replace(/\W/g, "")}`, (c) => {
            pipeline.enable(c);
            withCrossRegionBuckets(["eu-west-1"], { addCrossRegionS3Endpoints: false })(c);
        });
        const env = vamsExecuteEnv(pipeline.build(h), pipeline.handler);
        expect(env[ENV]).toBe("");
    });

    test("is empty with no cross-Region bucket (the shipped template)", () => {
        const h = makePipelineHarness(`XRegionEnvNone${pipeline.name.replace(/\W/g, "")}`, (c) => {
            pipeline.enable(c);
        });
        const env = vamsExecuteEnv(pipeline.build(h), pipeline.handler);
        expect(env[ENV]).toBe("");
    });
});

describe("the whole-app synth: the Lambdas' list IS the VPC builder's endpoint list", () => {
    const synth = () =>
        synthTemplate("commercial", {
            mutateKey: "xregion-isolated-pipelines-endpoint-env",
            mutate: (c) => {
                for (const pipeline of ISOLATED_PIPELINES) pipeline.enable(c);
                withCrossRegionBuckets(["eu-west-1", "ap-southeast-2", "eu-west-1"])(c);
                // In-VPC synth of the commercial template: a public Serverless collection is
                // rejected with the VPC on (an unrelated rule).
                c.app.openSearch.useServerless.allowPublic = false;
            },
        });

    test("each of the four vamsExecute Lambdas names exactly the Regions that have a cross-Region S3 endpoint", () => {
        const s = synth();
        const endpointRegions = s
            .where("AWS::EC2::VPCEndpoint", (r) => "ServiceRegion" in r.properties)
            .map((r) => r.properties.ServiceRegion as string)
            .sort();
        // Control: the VPC builder emitted the endpoints this test joins against.
        expect(endpointRegions).toEqual(["ap-southeast-2", "eu-west-1"]);

        for (const pipeline of ISOLATED_PIPELINES) {
            const functions = s.where(
                "AWS::Lambda::Function",
                (r) => r.properties.Handler === pipeline.handler
            );
            expect(functions).toHaveLength(1);
            const value = SynthResult.flatten(functions[0].properties.Environment.Variables[ENV]);
            expect(value.split(",").sort()).toEqual(endpointRegions);
        }
    });
});
