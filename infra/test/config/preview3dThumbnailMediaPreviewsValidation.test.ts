/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `getConfig()` rules for `app.pipelines.usePreview3dThumbnail.mediaPreviews` (#1550).
 *
 * A missing block or key is off, so an upgraded deployment keeps its behaviour; each key must be a
 * boolean; the trigger and the downscale option need `enabled`. The block deploys only with the pipeline,
 * and the shipped config turns it on beside a pipeline that is off by default, so enabling media with the
 * pipeline off is accepted and deploys nothing. The ConfigBuilder mirrors each rule (`validation.ts`),
 * which this suite also exercises so the two cannot drift apart unnoticed.
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

function config(mutate: (thumbnail: any) => void, pipelineEnabled = true): any {
    const c = JSON.parse(JSON.stringify(commercialTemplate));
    c.env.region = "us-east-1";
    c.env.account = "123456789012";
    c.app.baseStackName = "vamstest";
    c.app.useGlobalVpc.enabled = true;
    c.app.pipelines.usePreview3dThumbnail.enabled = pipelineEnabled;
    mutate(c.app.pipelines.usePreview3dThumbnail);
    return c;
}

function resolve(c: any): () => Config.Config {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (p: string, ...rest: unknown[]) => {
            if (typeof p === "string" && p.endsWith("config.json")) return JSON.stringify(c);
            return realReadFileSync(p, ...rest);
        }
    );
    return () => Config.getConfig(newTestApp());
}

const media = (c: any) => resolve(c)().app.pipelines.usePreview3dThumbnail.mediaPreviews;

const builderViolations = (c: any): string[] =>
    RULES.filter((r) => r.id.startsWith("preview3dthumbnail-media-"))
        .filter((r) => r.appliesWhen(c))
        .map((r) => r.id);

afterEach(() => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

describe("media previews defaults", () => {
    test("the shipped template turns media on, with its trigger on and the downscale option off", () => {
        const c = config(() => undefined);
        expect(media(c)).toEqual({
            enabled: true,
            autoRegisterAutoTriggerOnFileUpload: true,
            downscaleSelfPreviewableImages: false,
        });
        expect(builderViolations(c)).toEqual([]);
    });

    test("a missing block means off", () => {
        const c = config((t) => delete t.mediaPreviews);
        expect(media(c)).toEqual(Config.PREVIEW_MEDIA_PREVIEWS_DEFAULTS);
        expect(builderViolations(c)).toEqual([]);
    });

    test("a missing pipeline block means off", () => {
        const c = config(() => undefined);
        delete c.app.pipelines.usePreview3dThumbnail;
        expect(media(c)).toEqual(Config.PREVIEW_MEDIA_PREVIEWS_DEFAULTS);
    });

    test("a block that names only enabled keeps the other keys off", () => {
        const c = config((t) => (t.mediaPreviews = { enabled: true }));
        expect(media(c)).toEqual({
            enabled: true,
            autoRegisterAutoTriggerOnFileUpload: false,
            downscaleSelfPreviewableImages: false,
        });
    });

    test("an empty block means off", () => {
        const c = config((t) => (t.mediaPreviews = {}));
        expect(media(c)).toEqual(Config.PREVIEW_MEDIA_PREVIEWS_DEFAULTS);
    });

    test("media enabled with the pipeline off is accepted (the shipped config; nothing deploys)", () => {
        const c = config(() => undefined, false);
        expect(resolve(c)).not.toThrow();
        expect(media(c).enabled).toBe(true);
    });
});

describe("media previews validation", () => {
    test.each(["enabled", "autoRegisterAutoTriggerOnFileUpload", "downscaleSelfPreviewableImages"])(
        "%s must be a boolean",
        (key) => {
            for (const bad of ["true", 1, "yes", [], {}]) {
                const c = config((t) => (t.mediaPreviews = { enabled: true, [key]: bad }));
                expect(resolve(c)).toThrow(
                    new RegExp(`mediaPreviews\\.${key} must be true or false`)
                );
                expect(builderViolations(c)).toContain(`preview3dthumbnail-media-${key}-boolean`);
            }
        }
    );

    test("mediaPreviews must be an object", () => {
        const c = config((t) => (t.mediaPreviews = "on"));
        expect(resolve(c)).toThrow(/mediaPreviews must be an object/);
    });

    test.each(["autoRegisterAutoTriggerOnFileUpload", "downscaleSelfPreviewableImages"])(
        "%s needs media previews enabled",
        (key) => {
            const c = config((t) => (t.mediaPreviews = { enabled: false, [key]: true }));
            expect(resolve(c)).toThrow(
                new RegExp(`mediaPreviews\\.${key} requires .*mediaPreviews\\.enabled to be true`)
            );
            expect(builderViolations(c)).toEqual([`preview3dthumbnail-media-${key}-needs-media`]);
        }
    );

    test("the option and the trigger are accepted with media previews enabled", () => {
        const c = config(
            (t) =>
                (t.mediaPreviews = {
                    enabled: true,
                    autoRegisterAutoTriggerOnFileUpload: true,
                    downscaleSelfPreviewableImages: true,
                })
        );
        expect(resolve(c)).not.toThrow();
        expect(media(c).downscaleSelfPreviewableImages).toBe(true);
        expect(builderViolations(c)).toEqual([]);
    });
});
