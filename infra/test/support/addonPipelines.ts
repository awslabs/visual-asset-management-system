/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The processing pipelines an add-on builds.
 *
 * A core pipeline's CDK lives under `lib/nestedStacks/pipelines/`, mirroring its `backendPipelines/`
 * path. An add-on's pipeline lives under the add-on's nested stack instead, so a source guard that walks
 * only the pipelines tree does not see it. Every guard that scans pipeline CDK sources reads
 * `PIPELINE_SOURCE_DIRS` (or `ADDON_PIPELINES`) rather than the pipelines tree alone; add an add-on
 * pipeline here and every such guard covers it.
 */

import * as path from "path";

const NESTED_STACKS_DIR = path.join(__dirname, "..", "..", "lib", "nestedStacks");

/** The core pipelines tree, `lib/nestedStacks/pipelines`. */
export const CORE_PIPELINES_DIR = path.join(NESTED_STACKS_DIR, "pipelines");

export interface AddonPipeline {
    /** The pipeline's source directory under `backendPipelines/`, POSIX and relative. */
    backendPipelinesPath: string;
    /** Absolute path of the directory holding the pipeline's CDK (nested stack, constructs, builders). */
    dir: string;
    /** The nested stack class `addonBuilder-nestedStack.ts` instantiates for the pipeline. */
    nestedStackClass: string;
}

export const ADDON_PIPELINES: readonly AddonPipeline[] = [
    {
        backendPipelinesPath: "conversion/mirisUpload",
        dir: path.join(NESTED_STACKS_DIR, "addon", "miris", "upload"),
        nestedStackClass: "MirisUploadBuilderNestedStack",
    },
];

/** The CDK directories of the add-on pipelines. */
export const ADDON_PIPELINE_DIRS: readonly string[] = ADDON_PIPELINES.map((p) => p.dir);

/** Every directory holding pipeline CDK sources: the core pipelines tree, then each add-on pipeline's. */
export const PIPELINE_SOURCE_DIRS: readonly string[] = [CORE_PIPELINES_DIR, ...ADDON_PIPELINE_DIRS];

/**
 * A pipeline source file's name in test titles: relative to the core pipelines tree, or to
 * `lib/nestedStacks` for an add-on pipeline's file.
 */
export function pipelineSourceLabel(file: string): string {
    return file.startsWith(CORE_PIPELINES_DIR + path.sep)
        ? path.relative(CORE_PIPELINES_DIR, file)
        : path.relative(NESTED_STACKS_DIR, file);
}
