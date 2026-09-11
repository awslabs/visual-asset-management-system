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

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/**
 * Builds a config.json from the commercial template, applies `mutate`, and returns a thunk that calls
 * getConfig() on it — the shape configValidationHardening.test.ts uses. Only the config filename is
 * intercepted; the policy JSON reads getConfig() also performs fall through to the real fs.
 */
function resolve(mutate: (c: any) => void): () => Config.Config {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-east-1";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    mutate(config);
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(config);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

afterEach(() => {
    // Restored to delegating rather than cleared, or a later synth in this process reads nothing.
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

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

describe("validateBedrockModelId is one helper for both GenAI pipelines", () => {
    const FLAG_PATHS = ["pipelines.useGenAiMetadata3dLabeling", "pipelines.useGenAiVideoSopBom"];
    const GLOBAL_ID = "global.anthropic.claude-sonnet-4-5-20250929-v1:0";
    const US_ID = "us.anthropic.claude-sonnet-4-20250514-v1:0";
    const US_GOV_ID = "us-gov.anthropic.claude-sonnet-4-20250514-v1:0";

    test("[control] the labeling pipeline's two messages are the sentences its tests pin", () => {
        // configValidationHardening.test.ts asserts /bedrockModelId is empty/ and /exists only in the
        // commercial partition/; the full sentences are pinned here so a reworded helper is a visible
        // change rather than a silently weaker match. An Error argument makes toThrow compare the
        // whole message for equality — a string argument would be a substring match, which a helper
        // that appended a clause would still satisfy.
        expect(() => Config.validateBedrockModelId(FLAG_PATHS[0], "", "aws")).toThrow(
            new Error(
                "Configuration Error: pipelines.useGenAiMetadata3dLabeling is enabled but " +
                    "bedrockModelId is empty. Set a model id available in this partition and Region " +
                    "(the restricted-partition templates ship it empty because the commercial " +
                    "cross-Region inference profiles do not exist there)."
            )
        );
        expect(() => Config.validateBedrockModelId(FLAG_PATHS[0], GLOBAL_ID, "aws-us-gov")).toThrow(
            new Error(
                `Configuration Error: pipelines.useGenAiMetadata3dLabeling.bedrockModelId is ` +
                    `"${GLOBAL_ID}", whose "global." cross-Region inference-profile prefix exists ` +
                    "only in the commercial partition. This deployment targets aws-us-gov. Use a " +
                    'model id or inference profile offered there (GovCloud uses the "us-gov." prefix).'
            )
        );
    });

    test.each(FLAG_PATHS)("an empty id is rejected under %s, naming that flag", (flagPath) => {
        expect(() => Config.validateBedrockModelId(flagPath, "", "aws")).toThrow(
            `Configuration Error: ${flagPath} is enabled but bedrockModelId is empty`
        );
    });

    test.each(FLAG_PATHS)("a whitespace-only id counts as empty under %s", (flagPath) => {
        expect(() => Config.validateBedrockModelId(flagPath, "   ", "aws-us-gov")).toThrow(
            /bedrockModelId is empty/
        );
    });

    test.each(FLAG_PATHS)('a "global." id is rejected in GovCloud under %s', (flagPath) => {
        expect(() => Config.validateBedrockModelId(flagPath, GLOBAL_ID, "aws-us-gov")).toThrow(
            `${flagPath}.bedrockModelId is "${GLOBAL_ID}", whose "global." cross-Region ` +
                "inference-profile prefix exists only in the commercial partition. This deployment " +
                "targets aws-us-gov."
        );
    });

    test.each(FLAG_PATHS)(
        'a "us." id is rejected in the EU Sovereign Cloud under %s',
        (flagPath) => {
            expect(() => Config.validateBedrockModelId(flagPath, US_ID, "aws-eusc")).toThrow(
                `${flagPath}.bedrockModelId is "${US_ID}", whose "us." cross-Region inference-profile ` +
                    "prefix exists only in the commercial partition. This deployment targets aws-eusc."
            );
        }
    );

    test.each(FLAG_PATHS)(
        'a "global." id is accepted in the commercial partition under %s',
        (flagPath) => {
            expect(() => Config.validateBedrockModelId(flagPath, GLOBAL_ID, "aws")).not.toThrow();
        }
    );

    test.each(FLAG_PATHS)('a "us-gov." id is accepted in GovCloud under %s', (flagPath) => {
        expect(() =>
            Config.validateBedrockModelId(flagPath, US_GOV_ID, "aws-us-gov")
        ).not.toThrow();
    });
});

describe("getConfig() backfills and enumerations for useGenAiVideoSopBom", () => {
    const DEFAULT_LIMITS = { maxVideoFiles: 4, maxTotalDurationMinutes: 240 };

    test("an absent block resolves to the disabled defaults", () => {
        const config = resolve((c) => {
            delete c.app.pipelines.useGenAiVideoSopBom;
        })();
        expect(config.app.pipelines.useGenAiVideoSopBom).toEqual({
            enabled: false,
            useCodeBuild: false,
            autoRegisterWithVAMS: false,
            bedrockModelId: "",
            limits: DEFAULT_LIMITS,
        });
    });

    test("a block holding only `enabled` is completed leaf by leaf", () => {
        // autoRegisterWithVAMS comes from defaultAutoRegisterFlags (true for a present block, as the
        // templates ship); the rest are the per-leaf backfills.
        const config = resolve((c) => {
            c.app.pipelines.useGenAiVideoSopBom = { enabled: false };
        })();
        expect(config.app.pipelines.useGenAiVideoSopBom).toEqual({
            enabled: false,
            useCodeBuild: false,
            autoRegisterWithVAMS: true,
            bedrockModelId: "",
            limits: DEFAULT_LIMITS,
        });
    });

    test("a limits block missing one leaf gets that leaf and keeps the other", () => {
        const config = resolve((c) => {
            c.app.pipelines.useGenAiVideoSopBom.limits = { maxVideoFiles: 2 };
        })();
        expect(config.app.pipelines.useGenAiVideoSopBom.limits).toEqual({
            maxVideoFiles: 2,
            maxTotalDurationMinutes: 240,
        });
    });

    test("the shipped commercial block survives resolution unchanged", () => {
        const config = resolve(() => undefined)();
        expect(config.app.pipelines.useGenAiVideoSopBom).toEqual({
            enabled: false,
            useCodeBuild: false,
            autoRegisterWithVAMS: true,
            bedrockModelId: "global.anthropic.claude-sonnet-5",
            limits: DEFAULT_LIMITS,
        });
    });

    test("enabling the pipeline without a VPC is rejected, naming it", () => {
        expect(
            resolve((c) => {
                c.app.pipelines.useGenAiVideoSopBom.enabled = true;
            })
        ).toThrow(/require a VPC: pipelines\.useGenAiVideoSopBom\./);
    });

    test("[control] the same configuration with the VPC on is accepted", () => {
        expect(
            resolve((c) => {
                c.app.useGlobalVpc.enabled = true;
                c.app.pipelines.useGenAiVideoSopBom.enabled = true;
            })
        ).not.toThrow();
    });
});
