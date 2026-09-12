/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Video SOP/BOM Fargate job's ephemeral storage must hold what the run downloads and derives.
 *
 * The container downloads every selected video (bounded by VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB),
 * extracts per-video FLAC audio, concatenates it, and writes key frames — about 1.5x the input bytes plus
 * 2 GiB of image, transcript and analysis artefacts. Two constants in `infra/config/config.ts` size the
 * pair, and this file holds them to each other: raising the byte cap without raising the volume would
 * surface as errno 28 inside a six-hour Batch job.
 *
 * Asserted on the emitted `AWS::Batch::JobDefinition`, because the volume the container gets is the one
 * AWS Batch receives, and on the construct source, so the figure is seen to come from the constant rather
 * than from the shared construct's default.
 */

import * as fs from "fs";
import * as path from "path";
import * as Config from "../../config/config";
import { SynthResult, synthTemplate } from "../support/templateSynth";

/** Fargate's own bounds, from the prop's doc comment on `batch-fargate-pipeline.ts`. */
const FARGATE_MIN_GIB = 21;
const FARGATE_MAX_GIB = 200;

const CONSTRUCT_SOURCE = path.resolve(
    __dirname,
    "../../lib/nestedStacks/pipelines/genAi/videoSopBom/constructs/videoSopBom-construct.ts"
);

/**
 * The working set one run can place on the volume, in GiB: the downloaded inputs at the total byte cap,
 * their extracted and concatenated audio (the 1.5x factor), and 2 GiB of frames, transcripts and analysis
 * artefacts. 16384 MB -> ceil(24) + 2 = 26 GiB at the shipped cap.
 */
function videoSopBomWorkingSetGib(): number {
    return Math.ceil((Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB * 1.5) / 1024) + 2;
}

/** Enable the video SOP/BOM pipeline alone. It ships disabled in all three templates. */
function videoSopBomOnly(c: any) {
    c.app.useGlobalVpc.enabled = true;
    c.app.useGlobalVpc.addVpcEndpoints = true;
    c.app.pipelines.useGenAiVideoSopBom.enabled = true;
    c.app.pipelines.useGenAiVideoSopBom.autoRegisterWithVAMS = false;
}

/** Fargate job definitions, identified by the platform capability Batch receives. */
function fargateJobDefinitions(synth: SynthResult) {
    return synth.ofType("AWS::Batch::JobDefinition").filter((jd) => {
        const capabilities = ((jd.properties as any).PlatformCapabilities ?? []) as string[];
        return capabilities.includes("FARGATE");
    });
}

let synth: SynthResult;

beforeAll(() => {
    synth = synthTemplate("commercial", {
        mutate: videoSopBomOnly,
        mutateKey: "video-sop-bom-ephemeral",
    });
});

describe("video SOP/BOM Fargate ephemeral storage", () => {
    test("[control] the video SOP/BOM job definition IS emitted in this synth", () => {
        // Only this pipeline is enabled, so exactly one Fargate job definition is expected — which also
        // means the size assertion cannot accidentally read a sibling pipeline's volume.
        expect(fargateJobDefinitions(synth)).toHaveLength(1);
    });

    test("the job declares the volume the config constant names", () => {
        const jd = fargateJobDefinitions(synth)[0];
        const sizeGiB = (jd.properties as any).ContainerProperties?.EphemeralStorage?.SizeInGiB;
        expect(sizeGiB).toBe(Config.VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB);
    });

    test("the value stays inside what Fargate accepts", () => {
        const jd = fargateJobDefinitions(synth)[0];
        const sizeGiB = (jd.properties as any).ContainerProperties?.EphemeralStorage?.SizeInGiB;
        expect(sizeGiB).toBeGreaterThanOrEqual(FARGATE_MIN_GIB);
        expect(sizeGiB).toBeLessThanOrEqual(FARGATE_MAX_GIB);
    });

    test("[control] the three sizing constants are positive integers", () => {
        // The inequality below is vacuous over zeros or NaN; pin the operands first.
        for (const value of [
            Config.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB,
            Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB,
            Config.VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB,
        ]) {
            expect(Number.isInteger(value)).toBe(true);
            expect(value).toBeGreaterThan(0);
        }
    });

    test("the working set at the shipped caps is 26 GiB", () => {
        // Pins the figure the volume is sized against, so a changed cap shows up as a changed number
        // here rather than only as a moved bound in the inequality below.
        expect(videoSopBomWorkingSetGib()).toBe(26);
    });

    test("the byte cap fits the volume: ceil(total x 1.5 / 1024) + 2 GiB <= ephemeral GiB", () => {
        expect(videoSopBomWorkingSetGib()).toBeLessThanOrEqual(
            Config.VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB
        );
        // A single video cannot exceed the total the run may download.
        expect(Config.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB).toBeLessThanOrEqual(
            Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB
        );
    });

    test("the construct supplies the figure from the constant, not the shared default", () => {
        // The shared `BatchFargatePipelineConstruct` default (60 GiB) is sized for pipelines that do not
        // download gigabytes of video. If this pipeline stopped passing the constant it would silently
        // inherit that default, and the template assertion above would then describe the default.
        const source = fs.readFileSync(CONSTRUCT_SOURCE, "utf-8");
        expect(source).toMatch(
            /ephemeralStorageGiB:\s*Config\.VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB\b/
        );
    });
});

/**
 * The run's second scratch volume is the auxiliary bucket's `pipelines/<pipelineName>/<executionId>/`
 * prefix: the definition document, FLAC audio (gigabytes), Transcribe output and analysis artefacts. The
 * container deletes the audio once Transcribe is terminal and the whole prefix on success, and keeps the
 * transcript and analysis on a handled failure for diagnosis — but a crashed or aborted run deletes
 * nothing, and the bucket carried no expiration at all. This rule is the backstop for those leftovers.
 *
 * The rule covers THIS pipeline's prefix root only — `pipelines/genai-video-sop-bom/`, the folder the
 * bundle-registered workflow's execution writes under (executionRecords.aux_pipeline_prefix with the
 * pipeline id as its name) — and exists only when the pipeline is enabled, so a deployment without it
 * keeps the bucket's lifecycle exactly as it was.
 */
describe("auxiliary bucket scratch-prefix expiry", () => {
    const VIDEO_SOP_BOM_AUX_PREFIX = "pipelines/genai-video-sop-bom/";

    /** Lifecycle rules of the one auxiliary bucket in the assembly. */
    const auxBucketRules = (from: SynthResult): any[] => {
        const buckets = from
            .ofType("AWS::S3::Bucket")
            .filter((b) => /AssetAuxiliaryBucket/.test(b.logicalId));
        expect(buckets).toHaveLength(1);
        return ((buckets[0].properties as any).LifecycleConfiguration?.Rules ?? []) as any[];
    };

    test("[control] the pre-existing incomplete-multipart abort rule is still there", () => {
        // Without this, a change that dropped `lifecycleRules` entirely would make "no rule expires
        // outside the prefix" pass over an empty list.
        const abort = auxBucketRules(synth).filter(
            (rule) => rule.AbortIncompleteMultipartUpload?.DaysAfterInitiation === 14
        );
        expect(abort).toHaveLength(1);
    });

    test("expires objects under this pipeline's aux prefix after 30 days when the pipeline is enabled", () => {
        const expiry = auxBucketRules(synth).filter(
            (rule) => rule.Prefix === VIDEO_SOP_BOM_AUX_PREFIX
        );
        expect(expiry).toHaveLength(1);
        expect(expiry[0].ExpirationInDays).toBe(30);
        expect(expiry[0].Status).toBe("Enabled");
    });

    test("expires nothing outside this pipeline's aux prefix", () => {
        // Viewer data, other pipelines' working files and the non-versioned auxiliary objects live under
        // other prefixes and are not this pipeline's to expire.
        const unscoped = auxBucketRules(synth).filter(
            (rule) =>
                rule.ExpirationInDays !== undefined && rule.Prefix !== VIDEO_SOP_BOM_AUX_PREFIX
        );
        expect(unscoped).toEqual([]);
    });

    test("[control] the prefix the rule names is the one the platform derives for this pipeline id", () => {
        // executionRecords.aux_pipeline_prefix(pipeline_name, execution_id) renders
        // `pipelines/{pipelineName}/{executionId}/`, and the bundle-registered workflow reference carries
        // no jobName, so pipelineName is the pipeline id the construct registers.
        const executionRecords = fs.readFileSync(
            path.resolve(
                __dirname,
                "../../../backend/backend/common/workflows/executionRecords.py"
            ),
            "utf-8"
        );
        expect(executionRecords).toContain('_PIPELINES_PREFIX = "pipelines/"');
        expect(executionRecords).toContain(
            'return f"{_PIPELINES_PREFIX}{pipeline_name}/{execution_id}/"'
        );
        const construct = fs.readFileSync(CONSTRUCT_SOURCE, "utf-8");
        expect(construct).toContain('pipelineId: "genai-video-sop-bom"');
        expect(VIDEO_SOP_BOM_AUX_PREFIX).toBe("pipelines/" + "genai-video-sop-bom" + "/");
    });

    describe("with the pipeline disabled", () => {
        let disabledSynth: SynthResult;

        beforeAll(() => {
            disabledSynth = synthTemplate("commercial", {
                mutate: (c: any) => {
                    c.app.useGlobalVpc.enabled = true;
                    c.app.useGlobalVpc.addVpcEndpoints = true;
                    c.app.pipelines.useGenAiVideoSopBom.enabled = false;
                },
                mutateKey: "video-sop-bom-aux-expiry-disabled",
            });
        });

        test("[control] the abort rule is still there, so the bucket's lifecycle was read", () => {
            const abort = auxBucketRules(disabledSynth).filter(
                (rule) => rule.AbortIncompleteMultipartUpload?.DaysAfterInitiation === 14
            );
            expect(abort).toHaveLength(1);
        });

        test("no expiration rule is added", () => {
            const expirations = auxBucketRules(disabledSynth).filter(
                (rule) => rule.ExpirationInDays !== undefined
            );
            expect(expirations).toEqual([]);
        });
    });
});
