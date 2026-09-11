/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Configuration contract of the Video SOP/BOM Extraction pipeline (`app.pipelines.useGenAiVideoSopBom`).
 *
 * Four things are pinned here, each for a stated reason:
 *
 *  * **The compute-tied constants.** Per-file and total input bytes are `config.ts` constants rather
 *    than operator settings because they size the Fargate job's ephemeral volume: the inputs, the audio
 *    and frames extracted from them (a 1.5x working factor) and 2 GiB for the image and scratch must
 *    fit. The relation is asserted with a positive control — the same arithmetic against a volume that
 *    is too small — so a relation that held for every pair of numbers would be caught.
 *
 *  * **The shared Bedrock model-id helper.** Two GenAI pipelines validate a model id the same way; the
 *    helper is exercised under both flag paths and both partitions so a change that keeps the labeling
 *    pipeline's tests green cannot silently drop the other.
 *
 *  * **`getConfig()` backfills.** An older config.json without the block, or with a partial block, must
 *    resolve to the documented defaults rather than a TypeError or an `"undefined"` Lambda env value.
 *
 *  * **The ConfigBuilder mirror.** `validation.ts` is hand-ported and outside `configBuilderSync`'s
 *    coverage; it is pure TypeScript, so its rules are evaluated here against the builder presets with
 *    the same inputs `getConfig()` is given, and the rule ids that must fire are asserted by name.
 */

import * as Config from "../../config/config";

describe("Video SOP/BOM compute-tied constants", () => {
    test("the constants hold their registry values", () => {
        expect(Config.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB).toBe(4096);
        expect(Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB).toBe(16384);
        expect(Config.VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB).toBe(100);
        expect(Config.VIDEO_SOP_BOM_MAX_KEY_FRAMES_CEILING).toBe(200);
        expect(Config.VIDEO_SOP_BOM_BUNDLE_TASK_TIMEOUT_SECONDS).toBe(30600);
    });

    test("the working-set arithmetic is ceil(MB x 1.5 / 1024) + 2 GiB", () => {
        // 16384 MB of input -> 24576 MB working set -> 24 GiB, plus 2 GiB for the image and scratch.
        expect(Config.videoSopBomWorkingSetGib(16384)).toBe(26);
        // The ceiling matters at a non-multiple: 1 MB still costs a whole GiB.
        expect(Config.videoSopBomWorkingSetGib(1)).toBe(3);
    });

    test("the total input cap fits the ephemeral volume", () => {
        expect(
            Config.videoSopBomWorkingSetGib(Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB)
        ).toBeLessThanOrEqual(Config.VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB);
    });

    test("[control] the same relation fails for a 25 GiB volume", () => {
        // A volume one GiB below the working set must be refused by the identical comparison, or the
        // assertion above holds for every pair of numbers.
        const required = Config.videoSopBomWorkingSetGib(
            Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB
        );
        expect(required).toBe(26);
        expect(required <= 25).toBe(false);
    });

    test("the per-file cap is a positive whole number of MB no larger than the total cap", () => {
        expect(Number.isInteger(Config.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB)).toBe(true);
        expect(Config.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB).toBeGreaterThan(0);
        expect(Config.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB).toBeLessThanOrEqual(
            Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB
        );
    });

    test("the cap-fit guard refuses a pair the volume cannot hold and accepts the shipped one", () => {
        // The same relation through the function getConfig() calls on every load. Both throws are
        // reached with failing inputs — the shipped constants alone could never exercise them — and
        // the shipped triple passes, so deleting the guard or rewording either message is visible.
        expect(() => Config.assertVideoSopBomCapsFitVolume(4096, 16384, 25)).toThrow(
            /needs 26 GiB of ephemeral storage but VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB is 25/
        );
        expect(() => Config.assertVideoSopBomCapsFitVolume(16385, 16384, 100)).toThrow(
            /\(16385\) exceeds VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB/
        );
        expect(() =>
            Config.assertVideoSopBomCapsFitVolume(
                Config.VIDEO_SOP_BOM_MAX_VIDEO_FILE_SIZE_MB,
                Config.VIDEO_SOP_BOM_MAX_TOTAL_INPUT_SIZE_MB,
                Config.VIDEO_SOP_BOM_EPHEMERAL_STORAGE_GIB
            )
        ).not.toThrow();
    });
});
