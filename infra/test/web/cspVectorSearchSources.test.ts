/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The browser hosts the search, preview and viewer features reach are allowed by the web CSP in each
 * shipped partition, with the shipped template configuration (Cognito as shipped, unlike
 * cspPartitionWildcard.test.ts, which turns it off to cover partitions without Cognito):
 *
 *  - presigned GET URLs of the auxiliary bucket (search and file thumbnails, generated media previews)
 *    and of asset buckets (viewers, the TIFF/TGA raster decode, video), path-style in the deployment
 *    Region and in a second Region, in connect-src, img-src and media-src;
 *  - S3 Transfer Acceleration hosts (#1617, commercial only) in connect-src and img-src;
 *  - the Amazon Location Service maps host of the search map view in connect-src (commercial ships
 *    Location on; GovCloud refuses it and EU Sovereign has no Location endpoint);
 *  - workers from 'self' and blob: (the raster decode worker, MapLibre).
 *
 * It also measures the GovCloud and EU Sovereign policies, as shipped and with unsafe-eval turned on,
 * against the 1 KB ALB listener-attribute cap.
 */

import * as Config from "../../config/config";
import { SetConfig } from "../../lib/helper/service-helper";
import { generateContentSecurityPolicy } from "../../lib/helper/security";
import { buildTemplateConfig, TemplateName } from "../support/templateSynth";

const API_HOST = "abcdefghij.execute-api.us-gov-west-1.amazonaws.com"; // longest plausible, 49 chars
const ALB_ATTRIBUTE_CAP_BYTES = 1024;

const CASES: { name: TemplateName; region: string; second: string; suffix: string }[] = [
    { name: "commercial", region: "us-east-1", second: "eu-west-1", suffix: "amazonaws.com" },
    { name: "govcloud", region: "us-gov-west-1", second: "us-gov-east-1", suffix: "amazonaws.com" },
    {
        name: "eusovereign",
        region: "eusc-de-east-1",
        second: "eusc-de-west-1",
        suffix: "amazonaws.eu",
    },
];

function policy(name: TemplateName, mutate?: (c: Config.Config) => void) {
    const config = buildTemplateConfig(name, mutate);
    SetConfig(config);
    const storage = {} as Parameters<typeof generateContentSecurityPolicy>[0];
    return generateContentSecurityPolicy(storage, "", API_HOST, config);
}

function directive(csp: string, name: string): string[] {
    const found = csp
        .split(";")
        .map((d) => d.trim())
        .find((d) => d.startsWith(`${name} `));
    return found ? found.slice(name.length).trim().split(/\s+/) : [];
}

/** CSP host-source matching for an https URL: `https://*.sfx` and `https://host[/path]`. */
function allows(sources: string[], url: string): boolean {
    const host = new URL(url).host;
    return sources.some((source) => {
        const wildcard = /^https:\/\/\*\.([^/]+)\/?$/.exec(source);
        if (wildcard) return host.endsWith(`.${wildcard[1]}`);
        const exact = /^https:\/\/([^/]+)(\/.*)?$/.exec(source);
        return !!exact && exact[1] === host;
    });
}

describe.each(CASES)("CSP sources of the search, preview and viewer features: $name", (c) => {
    const csp = policy(c.name);
    const presigned = [
        `https://s3.${c.region}.${c.suffix}/vams-auxiliary-bucket/preview/db1/x1/a.glb.previewFile.gif?X-Amz-Signature=1`,
        `https://s3.${c.second}.${c.suffix}/external-assets/x1/scan.tif?X-Amz-Signature=1`,
        `https://external-assets.s3.${c.second}.${c.suffix}/x1/clip.mp4?X-Amz-Signature=1`,
    ];

    test("presigned asset and auxiliary bucket URLs in connect-src, img-src and media-src", () => {
        for (const d of ["connect-src", "img-src", "media-src"]) {
            for (const url of presigned) {
                expect(allows(directive(csp, d), url)).toBe(true);
            }
        }
    });

    test("Transfer Acceleration hosts in connect-src and img-src", () => {
        const accelerated = `https://assets-bucket.s3-accelerate.${c.suffix}/x1/a.glb?X-Amz-Signature=1`;
        for (const d of ["connect-src", "img-src"]) {
            expect(allows(directive(csp, d), accelerated)).toBe(true);
        }
    });

    test("the API, and workers from 'self' and blob:", () => {
        expect(allows(directive(csp, "connect-src"), `https://${API_HOST}/search/nlp`)).toBe(true);
        expect(directive(csp, "worker-src")).toEqual(expect.arrayContaining(["'self'", "blob:"]));
    });

    test("no host outside the partition's service suffix is allowed", () => {
        for (const d of ["connect-src", "img-src", "media-src"]) {
            expect(allows(directive(csp, d), "https://s3.example.com/x")).toBe(false);
        }
    });
});

describe("the search map view's Amazon Location Service host", () => {
    test("commercial ships Location on, and its maps host is in connect-src", () => {
        const csp = policy("commercial");
        expect(
            allows(
                directive(csp, "connect-src"),
                "https://maps.geo.us-east-1.amazonaws.com/maps/v0/maps/vams/style-descriptor"
            )
        ).toBe(true);
    });

    test.each(["govcloud", "eusovereign"] as TemplateName[])(
        "%s: no Location host (GovCloud refuses Location; EU Sovereign has no endpoint)",
        (name) => {
            expect(policy(name)).not.toContain("maps.geo.");
        }
    );
});

describe("ALB policy size with the switches these features can add", () => {
    test.each(["govcloud", "eusovereign"] as TemplateName[])(
        "%s: shipped, and with unsafe-eval on, under the 1 KB listener attribute",
        (name) => {
            const shipped = policy(name);
            const widest = policy(
                name,
                (cfg: Config.Config) => (cfg.app.webUi.allowUnsafeEvalFeatures = true)
            );
            // Control: the switch adds a source, so the comparison below is not vacuous.
            expect(widest.length).toBeGreaterThan(shipped.length);
            expect(shipped.length).toBeLessThan(ALB_ATTRIBUTE_CAP_BYTES);
            expect(widest.length).toBeLessThan(ALB_ATTRIBUTE_CAP_BYTES);
        }
    );
});
