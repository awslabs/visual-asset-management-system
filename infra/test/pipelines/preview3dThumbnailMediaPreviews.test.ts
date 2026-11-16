/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Media previews on the 3D preview thumbnail container (#1550): images, TGA/TIFF and video as their own
 * "Media Preview Thumbnail" workflow, behind `usePreview3dThumbnail.mediaPreviews` (a missing block is
 * off, which must leave the deployed resources as they were).
 *
 * Asserted on the synthesized template: the openPipeline environment, the Batch states' container
 * environment and the registrations. The extension lists and the size limit the construct exports are
 * pinned to the backend's and the web's definitions, and the media bundle's filters to the list.
 */

import * as fs from "fs";
import * as path from "path";
import { Template } from "aws-cdk-lib/assertions";
import {
    Preview3dThumbnailConstruct,
    PREVIEW_DOWNSCALE_ENVIRONMENT_VARIABLE,
    PREVIEW_FILE_SIZE_LIMIT_BYTES,
    PREVIEW_MEDIA_INPUT_FILE_EXTENSIONS,
    PREVIEW_SELF_PREVIEWABLE_FILE_EXTENSIONS,
} from "../../lib/nestedStacks/pipelines/preview/3dThumbnail/constructs/preview3dThumbnail-construct";
import { makePipelineHarness } from "../support/pipelineConstructHarness";
import { lambdaEnvironmentsByHandler, parseAsl, singleStateMachine } from "../support/asl";

const REPO = path.resolve(__dirname, "../../..");
const THREE_D_EXTENSIONS =
    ".ply,.stl,.obj,.glb,.gltf,.fbx,.las,.laz,.e57,.ptx,.pts,.pcd,.drc,.off,.3mf,.dae,.stp,.step,.usd,.usda,.usdc,.usdz,.spz,.sog,.splat,.ksplat,.lcc,.lcc2";
const STATES = ["Preview3dThumbnailBatchJob", "Preview3dThumbnailLargeBatchJob"];

type Media = Partial<{
    enabled: boolean;
    autoRegisterAutoTriggerOnFileUpload: boolean;
    downscaleSelfPreviewableImages: boolean;
}>;

/** Synthesize the construct; `media` undefined deletes the block (a config that predates it). */
function synth(id: string, media: Media | undefined, register = false): Template {
    const h = makePipelineHarness(id, (c) => {
        const thumbnail = c.app.pipelines.usePreview3dThumbnail;
        thumbnail.enabled = true;
        thumbnail.autoRegisterWithVAMS = register;
        if (media === undefined) {
            delete (thumbnail as any).mediaPreviews;
        } else {
            thumbnail.mediaPreviews = {
                enabled: false,
                autoRegisterAutoTriggerOnFileUpload: false,
                downscaleSelfPreviewableImages: false,
                ...media,
            };
        }
    });
    const nested = new Preview3dThumbnailConstruct(h.stack, "Preview3dThumbnailPipeline", {
        config: h.config,
        storageResources: h.storage,
        vpc: h.vpc,
        pipelineSubnets: h.subnets,
        pipelineSecurityGroups: h.securityGroups,
        lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
    });
    return Template.fromStack(nested);
}

const openPipelineEnv = (template: Template): Record<string, string> => {
    const envs = lambdaEnvironmentsByHandler(template, "openPipeline.lambda_handler");
    expect(envs).toHaveLength(1);
    return envs[0];
};

const containerEnvironment = (state: any): Record<string, string> =>
    Object.fromEntries(
        (state.Parameters.ContainerOverrides.Environment as any[]).map((e) => [
            e.Name,
            e.Value ?? e["Value.$"],
        ])
    );

const registrations = (template: Template): any[] =>
    Object.values(template.findResources("AWS::CloudFormation::CustomResource")).filter((r: any) =>
        r.Properties.bundleS3Keys?.includes?.("pipeline.json")
    ) as any[];

const triggerByPipeline = (template: Template): Record<string, string> =>
    Object.fromEntries(
        registrations(template).map((r) => [
            JSON.parse(r.Properties.idOverrides).pipelineId,
            r.Properties.triggerEnabled,
        ])
    );

describe("media previews off (block missing, or enabled false)", () => {
    test.each([
        ["missing", undefined],
        ["disabled", { enabled: false }],
    ])("%s block: nothing about media is deployed", (name, media) => {
        const template = synth(`MediaOff${name}`, media as Media | undefined, true);
        const env = openPipelineEnv(template);
        expect(env.ALLOWED_INPUT_FILEEXTENSIONS).toBe(THREE_D_EXTENSIONS);
        expect(env).not.toHaveProperty("SELF_PREVIEWABLE_FILEEXTENSIONS");
        expect(env).not.toHaveProperty("PREVIEW_FILE_SIZE_LIMIT_BYTES");
        expect(env).not.toHaveProperty("DOWNSCALE_SELF_PREVIEWABLE_IMAGES");
        const asl = parseAsl(singleStateMachine(template));
        for (const state of STATES) {
            expect(containerEnvironment(asl.States[state])).not.toHaveProperty(
                PREVIEW_DOWNSCALE_ENVIRONMENT_VARIABLE
            );
        }
        expect(registrations(template)).toHaveLength(1); // the 3D bundle only
    });

    test("the Lambda environment, state machine and resource types are the same with the block missing or all false", () => {
        const missing = synth("MediaOffIdentityMissing", undefined);
        const allFalse = synth("MediaOffIdentityFalse", {});
        const strip = (value: unknown) =>
            JSON.stringify(value).replace(/MediaOffIdentity(Missing|False)\w*?(Ref|")/g, "X$2");
        expect(strip(openPipelineEnv(allFalse))).toEqual(strip(openPipelineEnv(missing)));
        const states = (t: Template) =>
            Object.fromEntries(
                Object.entries(parseAsl(singleStateMachine(t)).States as Record<string, any>).map(
                    ([name, state]) => [name, state.Parameters?.ContainerOverrides?.Environment]
                )
            );
        expect(states(allFalse)).toEqual(states(missing));
        const types = (t: Template) =>
            Object.values(t.toJSON().Resources as Record<string, any>)
                .map((r) => r.Type)
                .sort();
        expect(types(allFalse)).toEqual(types(missing));
    });
});

describe("media previews on", () => {
    let template: Template;

    beforeAll(() => {
        template = synth("MediaOnStack", { enabled: true }, true);
    });

    test("openPipeline accepts the media extensions beside the 3D ones", () => {
        const env = openPipelineEnv(template);
        expect(env.ALLOWED_INPUT_FILEEXTENSIONS).toBe(
            `${THREE_D_EXTENSIONS},${PREVIEW_MEDIA_INPUT_FILE_EXTENSIONS}`
        );
        const all = env.ALLOWED_INPUT_FILEEXTENSIONS.split(",");
        expect(new Set(all).size).toBe(all.length);
    });

    test("openPipeline receives the self-previewable list, the limit and the option (off)", () => {
        const env = openPipelineEnv(template);
        expect(env.SELF_PREVIEWABLE_FILEEXTENSIONS).toBe(PREVIEW_SELF_PREVIEWABLE_FILE_EXTENSIONS);
        expect(env.PREVIEW_FILE_SIZE_LIMIT_BYTES).toBe("5242880");
        expect(env.DOWNSCALE_SELF_PREVIEWABLE_IMAGES).toBe("false");
    });

    test("the container is told the option on both tiers", () => {
        const asl = parseAsl(singleStateMachine(template));
        for (const state of STATES) {
            expect(containerEnvironment(asl.States[state])[PREVIEW_DOWNSCALE_ENVIRONMENT_VARIABLE]).toBe(
                "false"
            );
        }
    });

    test("a second bundle is registered with its own pipeline and workflow ids", () => {
        const resources = registrations(template);
        expect(resources).toHaveLength(2);
        const ids = resources.map((r) => JSON.parse(r.Properties.idOverrides));
        expect(ids).toEqual(
            expect.arrayContaining([
                { pipelineId: "preview-3d-thumbnail", workflowId: "preview-3d-thumbnail" },
                { pipelineId: "preview-media-thumbnail", workflowId: "preview-media-thumbnail" },
            ])
        );
    });

    test("the media registration's trigger follows its own flag", () => {
        expect(triggerByPipeline(template)["preview-media-thumbnail"]).toBe("false");
        const on = synth(
            "MediaOnTrigger",
            { enabled: true, autoRegisterAutoTriggerOnFileUpload: true },
            true
        );
        expect(triggerByPipeline(on)["preview-media-thumbnail"]).toBe("true");
    });

    test("the option reaches openPipeline and the container when it is on", () => {
        const on = synth("MediaOnDownscale", { enabled: true, downscaleSelfPreviewableImages: true });
        expect(openPipelineEnv(on).DOWNSCALE_SELF_PREVIEWABLE_IMAGES).toBe("true");
        const asl = parseAsl(singleStateMachine(on));
        expect(
            containerEnvironment(asl.States.Preview3dThumbnailBatchJob)[
                PREVIEW_DOWNSCALE_ENVIRONMENT_VARIABLE
            ]
        ).toBe("true");
    });

    test("with auto-registration off no bundle is registered, and the env still follows", () => {
        const noRegister = synth("MediaOnNoRegister", { enabled: true }, false);
        expect(registrations(noRegister)).toHaveLength(0);
        expect(openPipelineEnv(noRegister).ALLOWED_INPUT_FILEEXTENSIONS).toContain(".tif");
    });
});

describe("the shared lists and the limit", () => {
    const read = (...parts: string[]) => fs.readFileSync(path.join(REPO, ...parts), "utf-8");
    const quoted = (text: string) => [...text.matchAll(/"(\.[a-z0-9]+)"/g)].map((m) => m[1]);
    const withoutSvg = (list: string[]) => list.filter((e) => e !== ".svg").sort();

    test("the self-previewable list is the backend's preview list less .svg", () => {
        const source = read("backend/backend/common/s3PathPatterns.py");
        const block = source.match(
            /ALLOWED_PREVIEW_FILE_EXTENSIONS: Tuple\[str, \.\.\.\] = \(([^)]*)\)/s
        )!;
        expect(PREVIEW_SELF_PREVIEWABLE_FILE_EXTENSIONS.split(",").sort()).toEqual(
            withoutSvg(quoted(block[1]))
        );
    });

    test("the self-previewable list is the web's previewFileFormats less .svg", () => {
        const block = read("web/src/common/constants/fileFormats.ts").match(
            /export const previewFileFormats = \[([^\]]*)\]/
        )!;
        expect(PREVIEW_SELF_PREVIEWABLE_FILE_EXTENSIONS.split(",").sort()).toEqual(
            withoutSvg(quoted(block[1]))
        );
    });

    test(".tga, .tif and .tiff are media inputs but in no preview list", () => {
        const web = read("web/src/common/constants/fileFormats.ts").match(
            /export const previewFileFormats = \[([^\]]*)\]/
        )![1];
        const backend = read("backend/backend/common/s3PathPatterns.py").match(
            /ALLOWED_PREVIEW_FILE_EXTENSIONS: Tuple\[str, \.\.\.\] = \(([^)]*)\)/s
        )![1];
        for (const ext of [".tga", ".tif", ".tiff"]) {
            expect(PREVIEW_SELF_PREVIEWABLE_FILE_EXTENSIONS.split(",")).not.toContain(ext);
            expect(quoted(web)).not.toContain(ext);
            expect(quoted(backend)).not.toContain(ext);
            expect(PREVIEW_MEDIA_INPUT_FILE_EXTENSIONS.split(",")).toContain(ext);
        }
    });

    test("the limit is 5 MiB in the construct, the backend and the web", () => {
        expect(PREVIEW_FILE_SIZE_LIMIT_BYTES).toBe(5 * 1024 * 1024);
        expect(read("backend/backend/common/s3PathPatterns.py")).toMatch(
            /PREVIEW_FILE_SIZE_LIMIT_BYTES: int = 5 \* 1024 \* 1024/
        );
        expect(read("web/src/common/constants/fileFormats.ts")).toMatch(
            /PREVIEW_FILE_SIZE_LIMIT_BYTES = 5 \* 1024 \* 1024/
        );
    });

    test("the media bundle's filters are the extension list in pipeline, workflow and trigger", () => {
        const expected = PREVIEW_MEDIA_INPUT_FILE_EXTENSIONS.split(",").sort();
        const filters = (patterns: string[]) => patterns.map((p) => p.replace(/^\*/, "")).sort();
        const dir = "backendPipelines/preview/3dThumbnail/vamsSchemaMedia";
        const pipeline = JSON.parse(read(dir, "pipeline.json"));
        const workflow = JSON.parse(read(dir, "workflow.json"));
        expect(filters(pipeline.systemConfig.inputFileFilters.allow)).toEqual(expected);
        expect(filters(workflow.systemConfig.inputFileFilters.allow)).toEqual(expected);
        expect(filters(workflow.triggers[0].inputFileFilters.allow)).toEqual(expected);
        expect(workflow.triggers[0].enabled).toBe(false);
    });

    test("the media list is disjoint from the 3D list", () => {
        const media = new Set(PREVIEW_MEDIA_INPUT_FILE_EXTENSIONS.split(","));
        expect(THREE_D_EXTENSIONS.split(",").filter((e) => media.has(e))).toEqual([]);
    });

    test("the variable names are the ones the Python code reads", () => {
        const lambdaSource = read("backendPipelines/preview/3dThumbnail/lambda/openPipeline.py");
        for (const name of [
            "SELF_PREVIEWABLE_FILEEXTENSIONS",
            "PREVIEW_FILE_SIZE_LIMIT_BYTES",
            "DOWNSCALE_SELF_PREVIEWABLE_IMAGES",
        ]) {
            expect(lambdaSource).toContain(`"${name}"`);
        }
        const container = read(
            "backendPipelines/preview/3dThumbnail/container/preview_pipeline/media_preview.py"
        );
        expect(container).toContain(`DOWNSCALE_ENV = "${PREVIEW_DOWNSCALE_ENVIRONMENT_VARIABLE}"`);
    });

    test("the shipped config and templates carry the same media block", () => {
        for (const name of [
            "config.json",
            "config.template.commercial.json",
            "config.template.govcloud.json",
            "config.template.eusovereign.json",
        ]) {
            const block = JSON.parse(read("infra/config", name)).app.pipelines.usePreview3dThumbnail;
            expect(block.mediaPreviews).toEqual({
                enabled: true,
                autoRegisterAutoTriggerOnFileUpload: block.autoRegisterAutoTriggerOnFileUpload,
                downscaleSelfPreviewableImages: false,
            });
        }
    });
});
