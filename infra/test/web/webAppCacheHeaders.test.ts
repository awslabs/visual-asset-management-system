/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier — how the web bundle is cached and compressed on its way to the browser.
 *
 * `deployWebAppBundle` (lib/helper/webAppDeployment.ts) uploads the bundle as three BucketDeployments,
 * because one deployment applies a single Content-Type and Cache-Control to every object it writes. This
 * suite pins, for BOTH web shapes (CloudFront and ALB):
 *
 * - the key-set split — content-hashed `assets/*`, `*.mjs`, everything else — and that each deployment's
 *   filters keep its upload and its prune away from the other two;
 * - the Cache-Control each set carries (immutable for hashed files, revalidate for stable names);
 * - the order the three run in, which puts `index.html` and the CloudFront invalidation last;
 * - gzip and Brotli on the CloudFront web cache policy, which `compress: true` needs before CloudFront
 *   compresses anything;
 * - that the built bundle has no `.mjs` under `assets/`, the condition the split depends on.
 */

import * as fs from "fs";
import * as path from "path";
import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

jest.setTimeout(600_000);

const BUCKET_DEPLOYMENT = "Custom::CDKBucketDeployment";
const CACHE_POLICY = "AWS::CloudFront::CachePolicy";
const DISTRIBUTION = "AWS::CloudFront::Distribution";

const IMMUTABLE = "public, max-age=31536000, immutable";
const REVALIDATE = "public, max-age=0, must-revalidate, s-maxage=3600";

/** Same hybrid as webDeploymentCapacity.test.ts: the commercial template with the ALB web branch. */
const albHybrid = (c: any) => {
    c.app.useCloudFront.enabled = false;
    c.app.useAlb.enabled = true;
    c.app.useAlb.usePublicSubnet = false;
    c.app.useAlb.addAlbS3SpecialVpcEndpoint = true;
    c.app.useAlb.domainHost = "vams-t1-alb.example.com";
    c.app.useAlb.certificateArn =
        "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
    c.app.useAlb.optionalHostedZoneId = "";
    c.app.useGlobalVpc.enabled = true;
};

const SHAPES: Array<[string, () => SynthResult]> = [
    ["CloudFront", () => synthTemplate("commercial")],
    ["ALB", () => synthTemplate("commercial", { mutate: albHybrid, mutateKey: "web-deploy-alb" })],
];

interface WebDeployments {
    hashedAssets: Resource;
    esModules: Resource;
    main: Resource;
}

/** The three web-bundle deployments of a shape, located by their construct ids. */
function webDeployments(s: SynthResult): WebDeployments {
    const inStaticWeb = s.ofType(BUCKET_DEPLOYMENT).filter((r) => /StaticWeb/.test(r.stack));
    const byId = (id: string): Resource => {
        const found = inStaticWeb.filter((r) => r.logicalId.includes(id));
        if (found.length !== 1) {
            throw new Error(
                `expected exactly one ${id} bucket deployment in the StaticWeb stack, found ` +
                    `${found.length} of ${inStaticWeb.length}`
            );
        }
        return found[0];
    };
    return {
        hashedAssets: byId("DeployHashedAssets"),
        esModules: byId("DeployEsModules"),
        main: byId("DeployWithInvalidation"),
    };
}

const metadata = (r: Resource): Record<string, string> => r.properties.SystemMetadata ?? {};

/** DependsOn of a resource, normalized to an array of logical ids. */
const dependsOn = (r: Resource): string[] => {
    const d = r.raw.DependsOn;
    return d === undefined ? [] : Array.isArray(d) ? d : [d];
};

/** The logical ids of a deployment's custom resource and its policy/role resources share one prefix. */
const dependsOnDeployment = (r: Resource, on: Resource): boolean =>
    dependsOn(r).some((id) => id === on.logicalId || id.startsWith(on.logicalId.slice(0, 20)));

describe.each(SHAPES)("%s web bundle upload", (_name, synth) => {
    test("hashed assets are uploaded alone, as immutable, and pruned within assets/", () => {
        const { hashedAssets } = webDeployments(synth());
        expect(hashedAssets.properties.Exclude).toEqual(["*"]);
        expect(hashedAssets.properties.Include).toEqual(["assets/*"]);
        expect(metadata(hashedAssets)["cache-control"]).toBe(IMMUTABLE);
        expect(metadata(hashedAssets)["content-type"]).toBeUndefined();
        // Prune is the BucketDeployment default; CDK emits Prune only when it is false.
        expect(hashedAssets.properties.Prune).not.toBe(false);
    });

    test("*.mjs keeps its own text/javascript upload, revalidated and never pruned", () => {
        const { esModules } = webDeployments(synth());
        expect(esModules.properties.Exclude).toEqual(["*"]);
        expect(esModules.properties.Include).toEqual(["*.mjs"]);
        expect(metadata(esModules)["content-type"]).toBe("text/javascript");
        expect(metadata(esModules)["cache-control"]).toBe(REVALIDATE);
        expect(esModules.properties.Prune).toBe(false);
    });

    test("everything else is revalidated and its prune leaves assets/ and *.mjs alone", () => {
        const { main } = webDeployments(synth());
        expect(main.properties.Exclude).toEqual(["assets/*", "*.mjs"]);
        expect(main.properties.Include).toBeUndefined();
        expect(metadata(main)["cache-control"]).toBe(REVALIDATE);
        expect(metadata(main)["content-type"]).toBeUndefined();
        expect(main.properties.Prune).not.toBe(false);
    });

    test("assets upload first and index.html last", () => {
        const { hashedAssets, esModules, main } = webDeployments(synth());
        expect(dependsOnDeployment(esModules, hashedAssets)).toBe(true);
        expect(dependsOnDeployment(main, esModules)).toBe(true);
        expect(dependsOnDeployment(hashedAssets, main)).toBe(false);
    });
});

describe("CloudFront delivery", () => {
    const s = () => synthTemplate("commercial");

    test("only the last upload invalidates the distribution", () => {
        const { hashedAssets, esModules, main } = webDeployments(s());
        expect(main.properties.DistributionId).toBeDefined();
        expect(main.properties.DistributionPaths).toEqual(["/*"]);
        expect(hashedAssets.properties.DistributionId).toBeUndefined();
        expect(esModules.properties.DistributionId).toBeUndefined();
    });

    test("the web behavior compresses, and its cache policy enables gzip and Brotli", () => {
        const synth = s();
        const distributions = synth.ofType(DISTRIBUTION).filter((r) => /StaticWeb/.test(r.stack));
        expect(distributions).toHaveLength(1);
        const behavior = distributions[0].properties.DistributionConfig.DefaultCacheBehavior;
        expect(behavior.Compress).toBe(true);

        const policyId = behavior.CachePolicyId?.Ref;
        expect(typeof policyId).toBe("string");
        const policy = synth
            .ofType(CACHE_POLICY)
            .find((r) => r.stack === distributions[0].stack && r.logicalId === policyId);
        expect(policy).toBeDefined();
        const params =
            policy!.properties.CachePolicyConfig.ParametersInCacheKeyAndForwardedToOrigin;
        expect(params.EnableAcceptEncodingGzip).toBe(true);
        expect(params.EnableAcceptEncodingBrotli).toBe(true);
    });
});

describe("the built bundle", () => {
    test("emits no .mjs under assets/, so the hashed and ES-module uploads stay disjoint", () => {
        const assetsDir = path.join(__dirname, "..", "..", "..", "web", "dist", "assets");
        if (!fs.existsSync(assetsDir)) {
            throw new Error(`${assetsDir} does not exist — run the web build before this suite.`);
        }
        const files = fs.readdirSync(assetsDir);
        expect(files.length).toBeGreaterThan(0);
        expect(files.filter((f) => f.endsWith(".mjs"))).toEqual([]);
    });
});
