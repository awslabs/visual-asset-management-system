/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` rules for `app.pipelines.useMediaPreviews` (#1550).
 *
 * The block is a sibling of `usePreview3dThumbnail`. A missing block or key is off, so an upgraded
 * deployment keeps its behaviour; each key must be a boolean; the trigger and the downscale option need
 * `enabled`. The 3D thumbnail container deploys when either block is enabled, so the Fargate size rules
 * apply for either flag, and the nested key the feature briefly had under `usePreview3dThumbnail` is
 * refused with a rename message. The ConfigBuilder mirrors each rule (`validation.ts`), which this suite
 * also exercises so the two cannot drift apart unnoticed.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { RULES } from "../../../documentation/docusaurus-site/src/components/ConfigBuilder/validation";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

/** The commercial template with `mutate` applied to the whole config. */
function config(mutate: (c: any) => void = () => undefined): any {
    const c = JSON.parse(JSON.stringify(commercialTemplate));
    c.env.region = "us-east-1";
    c.env.account = "123456789012";
    c.app.baseStackName = "vamstest";
    c.app.useGlobalVpc.enabled = true;
    mutate(c);
    return c;
}

const pipelines = (c: any) => c.app.pipelines;

function resolve(c: any): () => Config.Config {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(c);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

const media = (c: any) => resolve(c)().app.pipelines.useMediaPreviews;

const builderViolations = (c: any): string[] =>
    RULES.filter((r) => /^(media-previews|preview3dthumbnail|vpc-required-media)/.test(r.id))
        .filter((r) => r.appliesWhen(c))
        .map((r) => r.id);

afterEach(() => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("media previews defaults", () => {
    test("the shipped template has media previews off (they need the Global VPC the template leaves off)", () => {
        const c = config((x) => (x.app.useGlobalVpc.enabled = false));
        expect(media(c)).toEqual({
            enabled: false,
            autoRegisterAutoTriggerOnFileUpload: false,
            downscaleSelfPreviewableImages: false,
        });
        expect(builderViolations(c)).toEqual([]);
        expect(resolve(c)).not.toThrow();
    });

    test("the block sits beside usePreview3dThumbnail, not inside it", () => {
        const c = config();
        expect(pipelines(c).useMediaPreviews).toBeDefined();
        expect(pipelines(c).usePreview3dThumbnail.mediaPreviews).toBeUndefined();
    });

    test("a missing block means off", () => {
        const c = config((x) => delete pipelines(x).useMediaPreviews);
        expect(media(c)).toEqual(Config.PREVIEW_MEDIA_PREVIEWS_DEFAULTS);
        expect(builderViolations(c)).toEqual([]);
    });

    test("a block that names only enabled keeps the other keys off", () => {
        const c = config((x) => (pipelines(x).useMediaPreviews = { enabled: true }));
        expect(media(c)).toEqual({
            enabled: true,
            autoRegisterAutoTriggerOnFileUpload: false,
            downscaleSelfPreviewableImages: false,
        });
    });

    test("an empty block means off", () => {
        const c = config((x) => (pipelines(x).useMediaPreviews = {}));
        expect(media(c)).toEqual(Config.PREVIEW_MEDIA_PREVIEWS_DEFAULTS);
    });

    test("a missing 3D pipeline block takes its defaults and media still validates", () => {
        const c = config((x) => delete pipelines(x).usePreview3dThumbnail);
        const resolved = resolve(c)().app.pipelines;
        expect(resolved.usePreview3dThumbnail.enabled).toBe(false);
        expect(resolved.usePreview3dThumbnail.computeTiers).toEqual(
            Config.PREVIEW_3D_THUMBNAIL_DEFAULT_COMPUTE_TIERS
        );
        expect(resolved.useMediaPreviews.enabled).toBe(false);
    });
});

describe("media previews validation", () => {
    test.each(["enabled", "autoRegisterAutoTriggerOnFileUpload", "downscaleSelfPreviewableImages"])(
        "%s must be a boolean",
        (key) => {
            for (const bad of ["true", 1, "yes", [], {}]) {
                const c = config(
                    (x) => (pipelines(x).useMediaPreviews = { enabled: true, [key]: bad })
                );
                expect(resolve(c)).toThrow(
                    new RegExp(`useMediaPreviews\\.${key} must be true or false`)
                );
                expect(builderViolations(c)).toContain(`media-previews-${key}-boolean`);
            }
        }
    );

    test("useMediaPreviews must be an object", () => {
        const c = config((x) => (pipelines(x).useMediaPreviews = "on"));
        expect(resolve(c)).toThrow(/useMediaPreviews must be an object/);
    });

    test.each(["autoRegisterAutoTriggerOnFileUpload", "downscaleSelfPreviewableImages"])(
        "%s needs media previews enabled",
        (key) => {
            const c = config(
                (x) => (pipelines(x).useMediaPreviews = { enabled: false, [key]: true })
            );
            expect(resolve(c)).toThrow(
                new RegExp(
                    `useMediaPreviews\\.${key} requires .*useMediaPreviews\\.enabled to be true`
                )
            );
            expect(builderViolations(c)).toEqual([`media-previews-${key}-needs-media`]);
        }
    );

    test("the option and the trigger are accepted with media previews enabled", () => {
        const c = config(
            (x) =>
                (pipelines(x).useMediaPreviews = {
                    enabled: true,
                    autoRegisterAutoTriggerOnFileUpload: true,
                    downscaleSelfPreviewableImages: true,
                })
        );
        expect(media(c).downscaleSelfPreviewableImages).toBe(true);
        expect(builderViolations(c)).toEqual([]);
    });

    test("the nested key from the unreleased design is refused with a rename message", () => {
        const c = config(
            (x) => (pipelines(x).usePreview3dThumbnail.mediaPreviews = { enabled: true })
        );
        expect(resolve(c)).toThrow(
            /usePreview3dThumbnail\.mediaPreviews has been renamed to pipelines\.useMediaPreviews/
        );
        expect(builderViolations(c)).toEqual(["media-previews-renamed"]);
    });

    test("media previews need the Global VPC, as the 3D pipeline does", () => {
        const c = config((x) => {
            pipelines(x).useMediaPreviews = { enabled: true };
            x.app.useGlobalVpc.enabled = false;
        });
        expect(resolve(c)).toThrow(/pipelines\.useMediaPreviews/);
        expect(builderViolations(c)).toContain("vpc-required-media-previews");
    });
});

describe("the container's Fargate sizes apply when either flag deploys it", () => {
    const badLarge = { large: { vcpu: 16, memoryGiB: 124 } };

    test("media only (3D off): a bad tier is rejected", () => {
        const c = config((x) => {
            pipelines(x).useMediaPreviews = { enabled: true };
            pipelines(x).usePreview3dThumbnail.enabled = false;
            pipelines(x).usePreview3dThumbnail.computeTiers = badLarge;
        });
        expect(resolve(c)).toThrow(/usePreview3dThumbnail\.computeTiers\.large/);
        expect(builderViolations(c)).toContain("preview3dthumbnail-large-tier-fargate-size");
    });

    test("3D only (media off): a bad tier is rejected", () => {
        const c = config((x) => {
            pipelines(x).useMediaPreviews = { enabled: false };
            pipelines(x).usePreview3dThumbnail.enabled = true;
            pipelines(x).usePreview3dThumbnail.computeTiers = badLarge;
        });
        expect(resolve(c)).toThrow(/usePreview3dThumbnail\.computeTiers\.large/);
    });

    test("neither flag: a bad tier is not read", () => {
        const c = config((x) => {
            pipelines(x).useMediaPreviews = { enabled: false };
            pipelines(x).usePreview3dThumbnail.enabled = false;
            pipelines(x).usePreview3dThumbnail.computeTiers = badLarge;
        });
        expect(resolve(c)).not.toThrow();
        expect(builderViolations(c)).toEqual([]);
    });

    test("media only: a non-positive large-tier threshold is rejected", () => {
        const c = config((x) => {
            pipelines(x).useMediaPreviews = { enabled: true };
            pipelines(x).usePreview3dThumbnail.enabled = false;
            pipelines(x).usePreview3dThumbnail.largeTierThresholdGiB = 0;
        });
        expect(resolve(c)).toThrow(/largeTierThresholdGiB must be a positive number/);
        expect(builderViolations(c)).toContain("preview3dthumbnail-large-tier-threshold");
    });

    test("media only with the default tiers is accepted and takes the 3D defaults", () => {
        const c = config((x) => {
            pipelines(x).useMediaPreviews = { enabled: true };
            pipelines(x).usePreview3dThumbnail.enabled = false;
            delete pipelines(x).usePreview3dThumbnail.computeTiers;
            delete pipelines(x).usePreview3dThumbnail.largeTierThresholdGiB;
        });
        const resolved = resolve(c)().app.pipelines;
        expect(resolved.usePreview3dThumbnail.computeTiers).toEqual(
            Config.PREVIEW_3D_THUMBNAIL_DEFAULT_COMPUTE_TIERS
        );
        expect(resolved.usePreview3dThumbnail.largeTierThresholdGiB).toBe(
            Config.PREVIEW_3D_THUMBNAIL_DEFAULT_LARGE_TIER_THRESHOLD_GIB
        );
        expect(builderViolations(c)).toEqual([]);
    });
});
