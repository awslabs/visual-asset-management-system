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
import * as Config from "../../config/config";
import { NO_PREVIEW_RESULT_FILENAME } from "../../lib/nestedStacks/pipelines/preview/3dThumbnail/lambdaBuilder/preview3dThumbnailFunctions";
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

/**
 * Synthesize the construct as the pipeline builder deploys it: `threeD` is
 * `usePreview3dThumbnail.enabled`, `media` the `useMediaPreviews` block (undefined deletes it, as in a
 * config that predates it).
 */
function synth(id: string, threeD: boolean, media: Media | undefined, register = true): Template {
    const h = makePipelineHarness(id, (c) => {
        const thumbnail = c.app.pipelines.usePreview3dThumbnail;
        thumbnail.enabled = threeD;
        thumbnail.autoRegisterWithVAMS = register;
        if (media === undefined) {
            delete (c.app.pipelines as any).useMediaPreviews;
        } else {
            c.app.pipelines.useMediaPreviews = {
                enabled: false,
                autoRegisterAutoTriggerOnFileUpload: false,
                downscaleSelfPreviewableImages: false,
                ...media,
            };
        }
    });
    return Template.fromStack(construct(h));
}

function construct(h: ReturnType<typeof makePipelineHarness>) {
    return new Preview3dThumbnailConstruct(h.stack, "Preview3dThumbnailPipeline", {
        config: h.config,
        storageResources: h.storage,
        vpc: h.vpc,
        pipelineSubnets: h.subnets,
        pipelineSecurityGroups: h.securityGroups,
        lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
    });
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

const pipelineIds = (template: Template): string[] =>
    registrations(template)
        .map((r) => JSON.parse(r.Properties.idOverrides).pipelineId as string)
        .sort();

const triggerByPipeline = (template: Template): Record<string, string> =>
    Object.fromEntries(
        registrations(template).map((r) => [
            JSON.parse(r.Properties.idOverrides).pipelineId,
            r.Properties.triggerEnabled,
        ])
    );

const BOTH_EXTENSIONS = `${THREE_D_EXTENSIONS},${PREVIEW_MEDIA_INPUT_FILE_EXTENSIONS}`;

describe("one deployment serves both workflows", () => {
    test.each([
        ["3D only", true, { enabled: false }],
        ["3D only, block missing", true, undefined],
        ["media only", false, { enabled: true }],
        ["both", true, { enabled: true }],
    ])("%s: the container carries the 3D and the media paths", (name, threeD, media) => {
        const template = synth(`Both${name.replace(/\W/g, "")}`, threeD as boolean, media as Media);
        const env = openPipelineEnv(template);
        expect(env.ALLOWED_INPUT_FILEEXTENSIONS).toBe(BOTH_EXTENSIONS);
        expect(env.SELF_PREVIEWABLE_FILEEXTENSIONS).toBe(PREVIEW_SELF_PREVIEWABLE_FILE_EXTENSIONS);
        expect(env.PREVIEW_FILE_SIZE_LIMIT_BYTES).toBe("5242880");
        const all = env.ALLOWED_INPUT_FILEEXTENSIONS.split(",");
        expect(new Set(all).size).toBe(all.length);
        const asl = parseAsl(singleStateMachine(template));
        for (const state of STATES) {
            expect(
                containerEnvironment(asl.States[state])[PREVIEW_DOWNSCALE_ENVIRONMENT_VARIABLE]
            ).toBe("false");
        }
    });

    test("the flags choose only the registrations", () => {
        expect(pipelineIds(synth("RegThreeD", true, { enabled: false }))).toEqual([
            "preview-3d-thumbnail",
        ]);
        expect(pipelineIds(synth("RegThreeDMissing", true, undefined))).toEqual([
            "preview-3d-thumbnail",
        ]);
        expect(pipelineIds(synth("RegMedia", false, { enabled: true }))).toEqual([
            "preview-media-thumbnail",
        ]);
        expect(pipelineIds(synth("RegBoth", true, { enabled: true }))).toEqual([
            "preview-3d-thumbnail",
            "preview-media-thumbnail",
        ]);
    });

    test("the media workflow registers whatever the 3D autoRegisterWithVAMS says", () => {
        expect(pipelineIds(synth("RegMediaNo3dReg", false, { enabled: true }, false))).toEqual([
            "preview-media-thumbnail",
        ]);
        expect(pipelineIds(synth("RegBothNo3dReg", true, { enabled: true }, false))).toEqual([
            "preview-media-thumbnail",
        ]);
    });

    test("the 3D bundle and the media bundle keep their own ids", () => {
        const ids = registrations(synth("RegIds", true, { enabled: true })).map((r) =>
            JSON.parse(r.Properties.idOverrides)
        );
        expect(ids).toEqual(
            expect.arrayContaining([
                { pipelineId: "preview-3d-thumbnail", workflowId: "preview-3d-thumbnail" },
                { pipelineId: "preview-media-thumbnail", workflowId: "preview-media-thumbnail" },
            ])
        );
    });

    test("each registration's trigger follows its own flag", () => {
        const off = triggerByPipeline(synth("TrigOff", true, { enabled: true }));
        expect(off["preview-media-thumbnail"]).toBe("false");
        expect(off["preview-3d-thumbnail"]).toBe("true");
        const on = triggerByPipeline(
            synth("TrigOn", false, { enabled: true, autoRegisterAutoTriggerOnFileUpload: true })
        );
        expect(on["preview-media-thumbnail"]).toBe("true");
    });

    test("the downscale option reaches openPipeline and the container on both tiers", () => {
        const on = synth("Downscale", false, {
            enabled: true,
            downscaleSelfPreviewableImages: true,
        });
        expect(openPipelineEnv(on).DOWNSCALE_SELF_PREVIEWABLE_IMAGES).toBe("true");
        const asl = parseAsl(singleStateMachine(on));
        for (const state of STATES) {
            expect(
                containerEnvironment(asl.States[state])[PREVIEW_DOWNSCALE_ENVIRONMENT_VARIABLE]
            ).toBe("true");
        }
        const off = synth("DownscaleOff", true, { enabled: true });
        expect(openPipelineEnv(off).DOWNSCALE_SELF_PREVIEWABLE_IMAGES).toBe("false");
    });

    test("openPipeline may put only the no-preview result document, in the asset bucket's results folders", () => {
        const template = synth("NoWrite", false, { enabled: true });
        const policies = Object.values(template.findResources("AWS::IAM::Policy")) as any[];
        const open = policies.filter((p) =>
            JSON.stringify(p.Properties.Roles ?? []).includes("openPipelineServiceRole")
        );
        expect(open.length).toBeGreaterThan(0);
        const statements = open.flatMap((p) => p.Properties.PolicyDocument.Statement);
        const actionsOf = (st: any): string[] =>
            Array.isArray(st.Action) ? st.Action : [st.Action];
        const allActions = statements.flatMap(actionsOf);
        expect(allActions).toContain("states:SendTaskSuccess");
        // no delete, abort, legal hold, retention or tagging anywhere in the role
        expect(
            allActions.filter((a: string) =>
                /^s3:(Delete|Abort|PutObject[A-Z]|PutObject\*)/.test(a)
            )
        ).toEqual([]);
        const writers = statements.filter((st: any) =>
            actionsOf(st).some((a) => /^s3:Put/.test(a))
        );
        expect(writers).toHaveLength(1);
        expect(actionsOf(writers[0])).toEqual(["s3:PutObject"]);
        // the resource is the asset (run) bucket, NOT the auxiliary bucket, and only the results document
        const resource = JSON.stringify(writers[0].Resource);
        expect(resource).toContain("AssetBucket");
        expect(resource).not.toContain("AssetAuxiliaryBucket");
        expect(resource).toContain("/pipelines/*/output/*/results/preview-not-generated.json");
    });

    test("the granted key pattern matches the results key a real run writes, and nothing else", () => {
        const glob = (pattern: string) =>
            new RegExp(
                "^" + pattern.replace(/[.+?^${}()|[\]\\]/g, "\\$&").replace(/\*/g, ".*") + "$"
            );
        const pattern = `pipelines/*/output/*/results/${NO_PREVIEW_RESULT_FILENAME}`;
        // executeWorkflow.py: <baseAssetsPrefix>pipelines/<pipeline>/<job>/output/<execution>/results/
        const runKey = `pipelines/preview-media-thumbnail/Preview_1/output/E1a2b3/results/${NO_PREVIEW_RESULT_FILENAME}`;
        expect(glob(pattern).test(runKey)).toBe(true);
        expect(glob(`base/${pattern}`).test(`base/${runKey}`)).toBe(true);
        // not an asset file, a preview, or another results document
        for (const other of [
            "xid/pics/photo.png",
            "xid/photo.png.previewFile.jpg",
            "pipelines/p/J/output/E1/files/preview-not-generated.json",
            "pipelines/p/J/output/E1/results/execution.status.json",
        ]) {
            expect(glob(pattern).test(other)).toBe(false);
        }
    });

    test("the results document name is the one the Lambda and the container write", () => {
        const read = (p: string) => fs.readFileSync(path.join(REPO, p), "utf-8");
        for (const file of [
            "backendPipelines/preview/3dThumbnail/lambda/openPipeline.py",
            "backendPipelines/preview/3dThumbnail/container/preview_pipeline/media_preview.py",
        ]) {
            expect(read(file)).toContain(
                `NO_PREVIEW_RESULT_FILENAME = "${NO_PREVIEW_RESULT_FILENAME}"`
            );
        }
    });

    test("the compute tiers come from the 3D block in a media-only deployment", () => {
        const h = makePipelineHarness("MediaOnlyTiers", (c) => {
            c.app.pipelines.usePreview3dThumbnail.enabled = false;
            c.app.pipelines.usePreview3dThumbnail.computeTiers = {
                standard: { vcpu: 2, memoryGiB: 8 },
                large: { vcpu: 8, memoryGiB: 60 },
            };
            c.app.pipelines.useMediaPreviews = {
                ...Config.PREVIEW_MEDIA_PREVIEWS_DEFAULTS,
                enabled: true,
            };
        });
        const asl = parseAsl(singleStateMachine(Template.fromStack(construct(h))));
        const memory = (state: any) =>
            (state.Parameters.ContainerOverrides.ResourceRequirements as any[]).find(
                (r) => r.Type === "MEMORY"
            ).Value;
        expect(memory(asl.States[STATES[0]])).toBe(String(8 * 1024));
        expect(memory(asl.States[STATES[1]])).toBe(String(60 * 1024));
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

    test("the shipped config and templates carry the same sibling media block", () => {
        for (const name of [
            "config.json",
            "config.template.commercial.json",
            "config.template.govcloud.json",
            "config.template.eusovereign.json",
        ]) {
            const pipelines = JSON.parse(read("infra/config", name)).app.pipelines;
            expect(pipelines.usePreview3dThumbnail.mediaPreviews).toBeUndefined();
            expect(pipelines.useMediaPreviews).toEqual({
                enabled: false,
                autoRegisterAutoTriggerOnFileUpload: false,
                downscaleSelfPreviewableImages: false,
            });
        }
    });
});
