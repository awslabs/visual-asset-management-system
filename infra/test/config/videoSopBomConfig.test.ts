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
import { makeDefaultConfig } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/defaults";
import {
    RULES,
    evaluateRules,
} from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

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

/**
 * Everything a GovCloud-partition config needs in order to reach the rules under test, in the shape
 * configValidationHardening.test.ts uses. The REGION carries the partition: getConfig() derives
 * config.env.partition from it, so a partition set in config.json would be overwritten.
 */
function restrictedPartition(c: any) {
    c.env.region = "us-gov-west-1";
    c.app.govCloud.enabled = true;
    c.app.useGlobalVpc.enabled = true;
    c.app.useCloudFront.enabled = false;
    c.app.useAlb.enabled = true;
    c.app.useLocationService.enabled = false;
    c.app.openSearch.useServerless.enabled = false;
    c.app.openSearch.useServerless.nextGen = false;
    c.app.openSearch.useProvisioned.enabled = true;
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

describe("ConfigBuilder validation.ts mirrors the getConfig() rules for useGenAiVideoSopBom", () => {
    type Profile = "commercial" | "govcloud" | "eusovereign";
    const PIPELINE_RULE_IDS = [
        "vpc-required-genai-video-sop-bom",
        "video-sop-bom-bedrock-model-id-required",
        "video-sop-bom-bedrock-model-id-commercial-only-prefix",
        "video-sop-bom-commercial-or-govcloud-only",
        "video-sop-bom-max-video-files-range",
        "video-sop-bom-max-total-duration-range",
    ];
    const PARTITION_REASON =
        "not validated outside the commercial and GovCloud partitions: Amazon Transcribe endpoint " +
        "availability is unverified and the service-helper has no row for this partition; a missing " +
        "endpoint hangs a run until its timeout.";
    const US_GOV_ID = "us-gov.anthropic.claude-sonnet-4-20250514-v1:0";
    const EU_ID = "eu.anthropic.claude-sonnet-4-5-20250929-v1:0";

    /** The ids of this pipeline's rules that fire on `profile`'s preset after `mutate`. */
    const firing = (profile: Profile, mutate: (c: any) => void): string[] => {
        const config = makeDefaultConfig(profile);
        mutate(config);
        return evaluateRules(config)
            .map((rule) => rule.id)
            .filter((id) => PIPELINE_RULE_IDS.includes(id));
    };
    /**
     * The commercial preset ships env.region null, and the partition-keyed rules are silent while the
     * region is unset. Setting us-east-1 makes the "raises none" control prove that `aws` is allowed
     * rather than that nothing was evaluated.
     */
    const enable = (c: any) => {
        c.env.region = "us-east-1";
        c.app.useGlobalVpc.enabled = true;
        c.app.pipelines.useGenAiVideoSopBom.enabled = true;
    };
    /**
     * The GovCloud preset also ships env.region null (it deep-equals the govcloud template), so
     * isCommercialPartition() would report commercial and the prefix rule could never fire. The region
     * is what carries the partition, as configValidationHardening's restrictedPartition() notes.
     */
    const govcloud = (c: any) => {
        c.env.region = "us-gov-west-1";
        c.app.pipelines.useGenAiVideoSopBom.enabled = true;
    };

    test("[control] every rule id this test names exists exactly once", () => {
        // A renamed rule would otherwise make every "raises nothing" assertion below pass vacuously.
        const ids = RULES.map((rule) => rule.id);
        for (const id of PIPELINE_RULE_IDS) {
            expect(ids.filter((candidate) => candidate === id)).toHaveLength(1);
        }
    });

    test("[control] the commercial preset at us-east-1 with the pipeline enabled and a VPC raises none of them", () => {
        expect(firing("commercial", enable)).toEqual([]);
    });

    test("[control] with env.region unset the partition-keyed rules stay silent, as the labeling rule does", () => {
        // A commercial prefix on the GovCloud preset with no region: isCommercialPartition() reports
        // commercial and partitionForRegionName() reports undefined, so neither partition rule fires.
        // This is the builder's documented behaviour (the deploy-time region may come from CDK context),
        // pinned so the three GovCloud tests below are read as needing the region set, not as optional.
        expect(
            firing("govcloud", (c) => {
                c.app.pipelines.useGenAiVideoSopBom.enabled = true;
                c.app.pipelines.useGenAiVideoSopBom.bedrockModelId =
                    "global.anthropic.claude-sonnet-5";
            })
        ).toEqual([]);
    });

    test("enabling without a VPC raises the VPC rule under the label getConfig() uses", () => {
        const config = makeDefaultConfig("commercial");
        config.app.pipelines.useGenAiVideoSopBom.enabled = true;
        const rule = evaluateRules(config).find((r) => r.id === "vpc-required-genai-video-sop-bom");
        expect(rule).toBeDefined();
        expect(rule?.severity).toBe("error");
        expect(rule?.message).toContain("pipelines.useGenAiVideoSopBom");
    });

    test("the EU Sovereign preset refuses the pipeline, and only for the partition", () => {
        expect(
            firing("eusovereign", (c) => {
                c.app.pipelines.useGenAiVideoSopBom.enabled = true;
                c.app.pipelines.useGenAiVideoSopBom.bedrockModelId = EU_ID;
            })
        ).toEqual(["video-sop-bom-commercial-or-govcloud-only"]);
    });

    test("getConfig() and the builder state the partition reason in the same words", () => {
        const builder = makeDefaultConfig("eusovereign");
        builder.app.pipelines.useGenAiVideoSopBom.enabled = true;
        builder.app.pipelines.useGenAiVideoSopBom.bedrockModelId = EU_ID;
        const rule = evaluateRules(builder).find(
            (r) => r.id === "video-sop-bom-commercial-or-govcloud-only"
        );
        expect(rule?.message).toContain(PARTITION_REASON);

        let deployMessage = "";
        try {
            resolve((c) => {
                // Not enable(): that helper pins us-east-1, and the region set last wins.
                restrictedPartition(c);
                c.env.region = "eusc-de-east-1";
                c.app.pipelines.useGenAiVideoSopBom.enabled = true;
                c.app.pipelines.useGenAiVideoSopBom.bedrockModelId = EU_ID;
            })();
        } catch (e) {
            deployMessage = (e as Error).message;
        }
        expect(deployMessage).toContain(PARTITION_REASON);
    });

    test("the GovCloud preset at us-gov-west-1 with a model id offered there raises nothing", () => {
        // With the region set, partitionForRegionName() resolves aws-us-gov, so this proves the
        // partition rule allows GovCloud rather than that it never looked.
        expect(
            firing("govcloud", (c) => {
                govcloud(c);
                c.app.pipelines.useGenAiVideoSopBom.bedrockModelId = US_GOV_ID;
            })
        ).toEqual([]);
    });

    test("the GovCloud preset's empty model id raises the required rule, not the partition rule", () => {
        expect(firing("govcloud", govcloud)).toEqual(["video-sop-bom-bedrock-model-id-required"]);
    });

    test("a commercial prefix in GovCloud raises the prefix rule", () => {
        expect(
            firing("govcloud", (c) => {
                govcloud(c);
                c.app.pipelines.useGenAiVideoSopBom.bedrockModelId =
                    "global.anthropic.claude-sonnet-5";
            })
        ).toEqual(["video-sop-bom-bedrock-model-id-commercial-only-prefix"]);
    });

    test.each([0, 5, -1, 2.5, "4"])("maxVideoFiles %p raises the range rule", (value) => {
        expect(
            firing("commercial", (c) => {
                enable(c);
                c.app.pipelines.useGenAiVideoSopBom.limits.maxVideoFiles = value;
            })
        ).toEqual(["video-sop-bom-max-video-files-range"]);
    });

    test.each([0, 481, 1.5, "240"])("maxTotalDurationMinutes %p raises the range rule", (value) => {
        expect(
            firing("commercial", (c) => {
                enable(c);
                c.app.pipelines.useGenAiVideoSopBom.limits.maxTotalDurationMinutes = value;
            })
        ).toEqual(["video-sop-bom-max-total-duration-range"]);
    });

    test("an absent limits leaf raises nothing, mirroring the getConfig() backfill", () => {
        expect(
            firing("commercial", (c) => {
                enable(c);
                delete c.app.pipelines.useGenAiVideoSopBom.limits.maxVideoFiles;
            })
        ).toEqual([]);
    });

    test("a disabled pipeline raises none of its rules whatever the values", () => {
        expect(
            firing("eusovereign", (c) => {
                c.app.pipelines.useGenAiVideoSopBom.limits.maxVideoFiles = 99;
                c.app.pipelines.useGenAiVideoSopBom.limits.maxTotalDurationMinutes = 0;
                c.app.pipelines.useGenAiVideoSopBom.bedrockModelId =
                    "global.anthropic.claude-sonnet-5";
            })
        ).toEqual([]);
    });
});
