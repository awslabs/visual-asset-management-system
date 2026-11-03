/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier — how the CloudFront distribution forwards `/api/*` to the REST API.
 *
 * The anonymous bootstrap routes (`API_STAGE_PREFIXED_ROUTE_PATHS`) are registered under the stage's
 * own `/api/` prefix, so their exact paths are forwarded with the stage as origin path:
 * `<cf>/api/version` reaches stage route `/api/version`. Every other `/api/*` path is forwarded with
 * no origin path: `<cf>/api/{route}` reaches stage `api`, route `/{route}`, the same shape as the
 * execute-api invoke URL. CloudFront matches behaviors in list order, so the exact paths must come
 * before the catch-all, or the catch-all would send the bootstrap requests to `/amplify-config`.
 *
 * The catch-all uses the managed CachingDisabled and AllViewerExceptHostHeader policies, so it adds
 * no custom cache or origin request policy: an account holds a limited number of custom cache
 * policies, and a deployment must not need a quota increase to update. The exact paths keep the
 * existing `ApiCachePolicy` and `ApiOriginRequestPolicy` resources under their construct ids, so an
 * update modifies those resources in place rather than replacing them.
 *
 * The REST API side: no registered route other than the bootstrap routes may begin with `/api/`, since
 * the catch-all would forward `<cf>/api/{that route}` without the prefix. Synthesis fails on one
 * (`rest-api-gateway-construct.ts`); here the synthesized OpenAPI document is checked as well.
 *
 * Durable (root CLAUDE.md Rule 13): the behaviors, their order and the policies can be changed again.
 */

import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { assertNoStagePrefixedRoutes } from "../../lib/nestedStacks/apiLambda/constructs/rest-api-gateway-construct";
import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

jest.setTimeout(600_000);

const DISTRIBUTION = "AWS::CloudFront::Distribution";

/** The web distribution in the StaticWeb nested stack. */
function webDistribution(s: SynthResult): Resource {
    const found = s.ofType(DISTRIBUTION).filter((r) => /StaticWeb/.test(r.stack));
    expect(found).toHaveLength(1);
    return found[0];
}

function behaviors(s: SynthResult): any[] {
    return webDistribution(s).properties.DistributionConfig.CacheBehaviors ?? [];
}

function originOf(s: SynthResult, behavior: any): any {
    const origins: any[] = webDistribution(s).properties.DistributionConfig.Origins;
    const origin = origins.find((o) => o.Id === behavior.TargetOriginId);
    expect(origin).toBeDefined();
    return origin;
}

/** The logical id a `{Ref}` policy id points at, or the literal id of a managed policy. */
const policyRef = (value: any): string => (value && value.Ref ? value.Ref : String(value));

const ALL_METHODS = ["GET", "HEAD", "OPTIONS", "PUT", "PATCH", "POST", "DELETE"];

describe("CloudFront /api/* behaviors (commercial)", () => {
    const s = () => synthTemplate("commercial");

    test("the commercial template deploys CloudFront with /api behaviors (control)", () => {
        expect(behaviors(s()).length).toBeGreaterThan(0);
    });

    test("the bootstrap paths come first, in order, followed by the /api/* catch-all", () => {
        const apiPatterns = behaviors(s())
            .map((b) => b.PathPattern as string)
            .filter((p) => p.startsWith("/api"));
        expect(apiPatterns).toEqual([...Config.API_STAGE_PREFIXED_ROUTE_PATHS, "/api/*"]);
        expect(Config.API_STAGE_PREFIXED_ROUTE_PATHS).toEqual([
            "/api/amplify-config",
            "/api/version",
        ]);
    });

    test.each(Config.API_STAGE_PREFIXED_ROUTE_PATHS)(
        "%s keeps the stage origin path and the existing API policies",
        (routePath) => {
            const synth = s();
            const behavior = behaviors(synth).find((b) => b.PathPattern === routePath);
            expect(behavior).toBeDefined();
            expect(originOf(synth, behavior).OriginPath).toBe(`/${Config.API_GATEWAY_STAGE_NAME}`);
            expect(policyRef(behavior.CachePolicyId)).toMatch(/ApiCachePolicy/);
            expect(policyRef(behavior.OriginRequestPolicyId)).toMatch(/ApiOriginRequestPolicy/);
            expect(behavior.AllowedMethods).toEqual(expect.arrayContaining(ALL_METHODS));
            expect(behavior.ViewerProtocolPolicy).toBe("redirect-to-https");
        }
    );

    test("the catch-all has no origin path and uses the managed policies", () => {
        const synth = s();
        const behavior = behaviors(synth).find((b) => b.PathPattern === "/api/*");
        expect(behavior).toBeDefined();
        const origin = originOf(synth, behavior);
        expect(origin.OriginPath ?? "").toBe("");
        expect(behavior.CachePolicyId).toBe(cloudfront.CachePolicy.CACHING_DISABLED.cachePolicyId);
        expect(behavior.OriginRequestPolicyId).toBe(
            cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER.originRequestPolicyId
        );
        expect(behavior.AllowedMethods).toEqual(expect.arrayContaining(ALL_METHODS));
        expect(behavior.ViewerProtocolPolicy).toBe("redirect-to-https");
        expect(origin.CustomOriginConfig.OriginProtocolPolicy).toBe("https-only");
        expect(origin.CustomOriginConfig.OriginSSLProtocols).toEqual(["TLSv1.2"]);
    });

    test("both API origins point at the same REST API host", () => {
        const synth = s();
        const [first, , catchAll] = ["/api/amplify-config", "/api/version", "/api/*"].map((p) =>
            originOf(
                synth,
                behaviors(synth).find((b) => b.PathPattern === p)
            )
        );
        expect(first.Id).not.toBe(catchAll.Id);
        expect(SynthResult.flatten(catchAll.DomainName)).toBe(
            SynthResult.flatten(first.DomainName)
        );
    });

    test("the catch-all read timeout is one second above the integration timeout", () => {
        const synth = s();
        const behavior = behaviors(synth).find((b) => b.PathPattern === "/api/*");
        const timeout = commercialTemplate.app.api.apiGatewayRest.apiGatewayTimeoutTime;
        expect(timeout).toBe(29);
        expect(originOf(synth, behavior).CustomOriginConfig.OriginReadTimeout).toBe(timeout + 1);
    });

    test("an integration timeout above 60 seconds caps the read timeout at 60", () => {
        const synth = synthTemplate("commercial", {
            mutate: (c) => {
                c.app.api.apiGatewayRest.apiGatewayTimeoutTime = 120;
            },
            mutateKey: "cf-api-timeout-120",
        });
        const behavior = behaviors(synth).find((b) => b.PathPattern === "/api/*");
        expect(originOf(synth, behavior).CustomOriginConfig.OriginReadTimeout).toBe(60);
    });

    test("no custom cache or origin request policy beyond the web and bootstrap ones", () => {
        const synth = s();
        const cachePolicies = synth.ofType("AWS::CloudFront::CachePolicy").map((r) => r.logicalId);
        const originRequestPolicies = synth
            .ofType("AWS::CloudFront::OriginRequestPolicy")
            .map((r) => r.logicalId);
        // The web bundle's cache policy and the bootstrap paths' API cache policy.
        expect(cachePolicies).toHaveLength(2);
        expect(cachePolicies.filter((id) => /ApiCachePolicy/.test(id))).toHaveLength(1);
        expect(originRequestPolicies).toHaveLength(1);
        expect(originRequestPolicies[0]).toMatch(/ApiOriginRequestPolicy/);
    });
});

describe("REST API routes under the stage prefix", () => {
    const stage = Config.API_GATEWAY_STAGE_NAME;

    test("the guard accepts the bootstrap routes and ordinary routes (control)", () => {
        expect(() =>
            assertNoStagePrefixedRoutes(
                [...Config.API_STAGE_PREFIXED_ROUTE_PATHS, "/secure-config", "/apiKeys", "/apis"],
                stage
            )
        ).not.toThrow();
    });

    test.each([`/${stage}/database`, `/${stage}`, `/${stage}/version/extra`])(
        "the guard rejects %s, naming it",
        (routePath) => {
            expect(() =>
                assertNoStagePrefixedRoutes(
                    [...Config.API_STAGE_PREFIXED_ROUTE_PATHS, routePath],
                    stage
                )
            ).toThrow(new RegExp(`Route\\(s\\) ${routePath.replace(/\//g, "\\/")} begin with`));
        }
    );
    /** The paths of the inline OpenAPI document of the REST API. */
    function openApiPaths(s: SynthResult): string[] {
        const apis = s.ofType("AWS::ApiGateway::RestApi");
        expect(apis).toHaveLength(1);
        return Object.keys(apis[0].properties.Body.paths);
    }

    test.each(["commercial", "govcloud", "eusovereign"] as const)(
        "%s registers only the bootstrap routes under /api/",
        (name) => {
            const paths = openApiPaths(synthTemplate(name));
            // Control: the document carries the full route set.
            expect(paths.length).toBeGreaterThan(50);
            const prefixed = paths.filter((p) => p === `/${stage}` || p.startsWith(`/${stage}/`));
            expect(prefixed.sort()).toEqual([...Config.API_STAGE_PREFIXED_ROUTE_PATHS].sort());
        }
    );
});
