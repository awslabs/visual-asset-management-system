/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Amazon Location Service API key accepts map requests only from the deployment's own web
 * origins. Its value reaches every authenticated browser, so the referer restriction is what stops a
 * copied key from serving maps to another site. A map request's Referer is the origin followed by
 * `/`, so each entry is exactly that, with no wildcard. On CloudFront the origin is the distribution's
 * domain name; on an ALB it is the configured domain host. The local development origin is admitted only
 * when `app.webUi.allowLocalhostAuthCallbacks` is on, and the nested stack declares its dependency on
 * the static web stack it reads the origins from.
 */

import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

const LOCATION_TEMPLATE = "commercial" as const;

const apiKey = (synth: SynthResult) => {
    const keys = synth.ofType("AWS::Location::APIKey");
    expect(keys).toHaveLength(1);
    return keys[0];
};

const referers = (synth: SynthResult): string[] =>
    (apiKey(synth).properties.Restrictions.AllowReferers ?? []).map((r: any) =>
        SynthResult.flatten(r)
    );

const nestedStack = (synth: SynthResult, idPattern: RegExp): Resource => {
    const stack = synth
        .ofType("AWS::CloudFormation::Stack")
        .find((s) => idPattern.test(s.logicalId));
    if (!stack) throw new Error(`no nested stack matches ${idPattern}`);
    return stack;
};

const ALB_DOMAIN_HOST = "maps-referer-alb.example.com";

describe("Location Service API key referer restriction", () => {
    test("the key accepts only the deployment's web origins", () => {
        const allowed = referers(synthTemplate(LOCATION_TEMPLATE));
        expect(allowed.length).toBeGreaterThan(0);
        allowed.forEach((entry) => {
            expect(entry).toMatch(/^https:\/\/.+\/$/);
            expect(entry).not.toContain("*");
            expect(entry).not.toContain("localhost");
        });
    });

    test("on CloudFront the one referer is the distribution's domain name", () => {
        const synth = synthTemplate(LOCATION_TEMPLATE);
        const allowed = referers(synth);
        expect(allowed).toHaveLength(1);

        // The LocationService template receives the domain name as a nested stack parameter.
        const parameter = /^https:\/\/\$\{(\w+)\}\/$/.exec(allowed[0])?.[1] ?? "";
        expect(parameter).not.toBe("");
        const passed = nestedStack(synth, /^LocationService/).properties.Parameters[parameter];
        const [source, attribute] = passed["Fn::GetAtt"];
        expect(source).toBe(nestedStack(synth, /^StaticWeb/).logicalId);

        // The static web stack fills that parameter from its distribution's DomainName.
        const distributions = synth.ofType("AWS::CloudFront::Distribution");
        expect(distributions).toHaveLength(1);
        const outputs = synth.templates[distributions[0].stack].Outputs ?? {};
        expect(outputs[attribute.replace(/^Outputs\./, "")]?.Value).toEqual({
            "Fn::GetAtt": [distributions[0].logicalId, "DomainName"],
        });
    });

    test("on an ALB the one referer is the configured domain host", () => {
        const synth = synthTemplate(LOCATION_TEMPLATE, {
            mutate: (c) => {
                c.app.useGlobalVpc.enabled = true;
                c.app.useCloudFront.enabled = false;
                c.app.useAlb.enabled = true;
                c.app.useAlb.domainHost = ALB_DOMAIN_HOST;
                c.app.useAlb.certificateArn =
                    "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
                c.app.useAlb.optionalHostedZoneId = "";
            },
            mutateKey: "location-referers-alb",
        });
        expect(referers(synth)).toEqual([`https://${ALB_DOMAIN_HOST}/`]);
    });

    test("the action and resource limits are kept alongside the referers", () => {
        const restrictions = apiKey(synthTemplate(LOCATION_TEMPLATE)).properties.Restrictions;
        expect(restrictions.AllowActions).toEqual(["geo-maps:GetTile", "geo-maps:GetStaticMap"]);
        expect(restrictions.AllowResources).toHaveLength(1);
    });

    test("allowLocalhostAuthCallbacks adds the local development origin", () => {
        const synth = synthTemplate(LOCATION_TEMPLATE, {
            mutate: (c) => {
                c.app.webUi.allowLocalhostAuthCallbacks = true;
            },
            mutateKey: "location-referers-localhost",
        });
        expect(referers(synth)).toContain("http://localhost:3001/");
    });

    test("the LocationService nested stack depends on the static web stack", () => {
        const synth = synthTemplate(LOCATION_TEMPLATE);
        const nested = synth.ofType("AWS::CloudFormation::Stack");
        const location = nested.find((s) => /^LocationService/.test(s.logicalId));
        const staticWeb = nested.find((s) => /^StaticWeb/.test(s.logicalId));
        expect(location).toBeDefined();
        expect(staticWeb).toBeDefined();
        expect(location?.raw.DependsOn ?? []).toContain(staticWeb?.logicalId);
    });

    test("the GEO1 reason names the referer limit and where the key value goes", () => {
        const synth = synthTemplate(LOCATION_TEMPLATE);
        const metadata = JSON.stringify(apiKey(synth).raw.Metadata ?? {});
        expect(metadata).toContain("AwsSolutions-GEO1");
        expect(metadata).toContain("Referer");
        expect(metadata).toContain("/secure-config");
        expect(metadata).not.toContain("stored securely in SSM");
    });
});
