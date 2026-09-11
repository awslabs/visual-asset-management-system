/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The ALB web distribution must not be the weaker of the two delivery paths.
 *
 * Three findings, all in `alb-s3-website-albDeploy-construct.ts`, and all specific to the only
 * distribution a restricted partition can use:
 *
 *  * **S1-INFRA-093** — the listener set the Content-Security-Policy and nothing else, while the
 *    CloudFront path's response-headers policy also sets HSTS, `X-Content-Type-Options` and
 *    `X-Frame-Options`. The ALB supports all three as `routing.http.response.*` listener attributes,
 *    so the gap was an omission rather than a platform limit.
 *
 *  * **S16-FLIP-002** — the whole policy travels as ONE listener attribute, and Elastic Load
 *    Balancing caps an attribute value at 1 KB. CloudFront allows 1783 bytes, so the ceiling exists
 *    only here. Over the cap the deploy fails; the point of checking at synth is that the message
 *    names the policy and its measured size instead.
 *
 *  * **S16-FLIP-003** — the S3 interface endpoint was created with the load balancer's own security
 *    group while the two ingress rules meant to scope it were added to a separate group that nothing
 *    attached. The rules governed nothing and the unused group read as an active restriction.
 *
 * Asserted against the govcloud and eusovereign templates because they are the ones that ship with
 * `useAlb` enabled; the commercial template emits no listener at all, which is why the parity test
 * below compares across templates rather than within one.
 *
 * Header VALUES are compared to the CloudFront policy's rather than hard-coded twice, so the two paths
 * cannot drift apart silently — a change to one becomes a failure here rather than a difference nobody
 * looks for.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

/** The templates that ship with the ALB web distribution. */
const ALB_TEMPLATES: TemplateName[] = ["govcloud", "eusovereign"];

/** Elastic Load Balancing: "The value for the attribute can not exceed 1K bytes in size". */
const ALB_ATTRIBUTE_CAP_BYTES = 1024;

const ATTR = {
    csp: "routing.http.response.content_security_policy.header_value",
    hsts: "routing.http.response.strict_transport_security.header_value",
    contentType: "routing.http.response.x_content_type_options.header_value",
    frame: "routing.http.response.x_frame_options.header_value",
    server: "routing.http.response.server.enabled",
};

/** The listener carrying response headers — the HTTPS one, not the port-80 redirect. */
function headerListener(synth: SynthResult) {
    const listeners = synth
        .ofType("AWS::ElasticLoadBalancingV2::Listener")
        .filter((l) => ((l.properties as any).ListenerAttributes ?? []).length > 0);
    return listeners[0];
}

function attributesOf(synth: SynthResult): Record<string, string> {
    const raw = ((headerListener(synth)?.properties as any)?.ListenerAttributes ?? []) as any[];
    return Object.fromEntries(raw.map((a) => [a.Key, SynthResult.flatten(a.Value)]));
}

/**
 * The deployed byte length, modelling each unresolved CDK token as a representative resolved width.
 *
 * The policy references the REST API id across a nested-stack boundary, so the emitted template holds
 * a reference name well over 100 characters where the deployed value is about ten. Measuring the raw
 * emitted string reports ~130 bytes more than ALB ever receives — enough to make a passing
 * configuration look like a failing one. The construct models it the same way.
 */
const RESOLVED_TOKEN_WIDTH = 10;
const deployedBytes = (value: string): number =>
    Buffer.byteLength(value.replace(/\$\{[^}]*\}/g, "x".repeat(RESOLVED_TOKEN_WIDTH)), "utf8");

describe.each(ALB_TEMPLATES)("%s: ALB web response headers", (templateName) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(templateName);
    });

    test("this template DOES deploy the ALB web distribution", () => {
        // The control. Every assertion below is satisfied by a template with no listener, and the
        // commercial template is exactly such a template — so without this the suite would silently
        // check nothing on the path it exists to cover.
        expect(headerListener(synth)).toBeDefined();
        expect(synth.ofType("AWS::ElasticLoadBalancingV2::LoadBalancer").length).toBeGreaterThan(0);
    });

    test("the CSP is delivered, and HSTS accompanies it", () => {
        const attrs = attributesOf(synth);
        expect(attrs[ATTR.csp]).toBeTruthy();
        expect(attrs[ATTR.hsts]).toMatch(/^max-age=\d+; includeSubDomains$/);
        // A max-age short enough to be decorative is the failure mode worth naming: anything under a
        // year does not survive a browser's own pruning between visits for an app used occasionally.
        const maxAge = Number(/max-age=(\d+)/.exec(attrs[ATTR.hsts])![1]);
        expect(maxAge).toBeGreaterThanOrEqual(60 * 60 * 24 * 365);
    });

    test("X-Content-Type-Options and X-Frame-Options are set", () => {
        const attrs = attributesOf(synth);
        expect(attrs[ATTR.contentType]).toBe("nosniff");
        // SAMEORIGIN, not DENY: the iframe-embedded viewers frame VAMS's own pages, and DENY breaks
        // them. Asserted as an equality so a well-meant tightening to DENY fails here rather than in a
        // viewer.
        expect(attrs[ATTR.frame]).toBe("SAMEORIGIN");
    });

    test("the load balancer does not announce itself in the Server header", () => {
        expect(attributesOf(synth)[ATTR.server]).toBe("false");
    });

    test("every listener attribute fits the 1 KB cap, CSP included", () => {
        const attrs = attributesOf(synth);
        const oversized = Object.entries(attrs)
            .map(([k, v]) => ({ key: k, bytes: deployedBytes(v) }))
            .filter((a) => a.bytes > ALB_ATTRIBUTE_CAP_BYTES);
        expect(oversized).toEqual([]);

        // Logged so the remaining margin is visible in test output rather than implicit — this is the
        // figure S16-FLIP-002 exists to keep an eye on.
        const cspBytes = deployedBytes(attrs[ATTR.csp]);
        // eslint-disable-next-line no-console
        console.log(
            `[alb csp] ${templateName}: ${cspBytes} bytes of ${ALB_ATTRIBUTE_CAP_BYTES} ` +
                `(${ALB_ATTRIBUTE_CAP_BYTES - cspBytes} remaining)`
        );
        expect(cspBytes).toBeLessThan(ALB_ATTRIBUTE_CAP_BYTES);
    });

    test("the S3 interface endpoint carries the group that holds its ingress rules", () => {
        const endpoints = synth
            .ofType("AWS::EC2::VPCEndpoint")
            .filter((e) => /S3Interface/i.test(e.logicalId));
        // Control: both the endpoint and more than one candidate group must exist, or "the right group
        // is attached" is satisfied by there being only one group to choose from.
        expect(endpoints.length).toBe(1);
        const groups = synth
            .ofType("AWS::EC2::SecurityGroup")
            .filter((g) => /WepAppDistro/i.test(g.logicalId));
        expect(groups.length).toBeGreaterThan(1);

        const attached = ((endpoints[0].properties as any).SecurityGroupIds ?? []).map(
            (g: unknown) => SynthResult.flatten(g)
        );
        expect(attached.length).toBe(1);

        // The group that actually carries the ALB ingress rules, found from the emitted rules rather
        // than by name: the property is that the rules and the attachment agree.
        const ingress = synth.ofType("AWS::EC2::SecurityGroupIngress").filter((r) => {
            const p = r.properties as any;
            return (
                p.SourceSecurityGroupId !== undefined && (p.FromPort === 443 || p.FromPort === 80)
            );
        });
        const ruleTargets = new Set(
            ingress.map((r) => SynthResult.flatten((r.properties as any).GroupId))
        );
        expect(ruleTargets.size).toBeGreaterThan(0);
        expect(ruleTargets).toContain(attached[0]);

        // And the group attached must NOT be the load balancer's own, which is what was attached
        // before: that group admits the whole internet on 443, so the endpoint inherited it.
        expect(attached[0]).not.toMatch(/ALBSecurityGroup/i);
    });
});

describe("the two delivery paths declare the same security headers", () => {
    /**
     * Read off the CloudFront construct rather than restated, so the ALB values cannot drift from the
     * policy they are supposed to match. A source read is the right tool here: the CloudFront path is
     * absent from the ALB templates and vice versa, so no single synth contains both to compare.
     */
    const cloudFrontSource = fs.readFileSync(
        path.resolve(
            __dirname,
            "..",
            "../lib/nestedStacks/staticWebApp/constructs/cloudfront-s3-website-construct.ts"
        ),
        "utf-8"
    );

    test("CloudFront still declares the three headers the ALB path now mirrors", () => {
        // The premise of the parity claim. If CloudFront stopped setting these, the ALB assertions
        // above would be enforcing a standard nothing else holds to.
        expect(cloudFrontSource).toContain("strictTransportSecurity");
        expect(cloudFrontSource).toContain("contentTypeOptions");
        expect(cloudFrontSource).toContain("frameOptions");
        expect(cloudFrontSource).toContain("HeadersFrameOption.SAMEORIGIN");
    });

    test("the HSTS max-age matches the CloudFront policy's two years", () => {
        // CloudFront expresses it as Duration.days(365 * 2); the ALB attribute is seconds.
        expect(cloudFrontSource).toMatch(/accessControlMaxAge:\s*Duration\.days\(365\s*\*\s*2\)/);
        const attrs = attributesOf(synthTemplate("govcloud"));
        expect(attrs[ATTR.hsts]).toContain(`max-age=${60 * 60 * 24 * 365 * 2}`);
    });

    test("the headers CloudFront sets that an ALB cannot are recorded as such", () => {
        // Cross-Origin-Embedder-Policy and Cross-Origin-Opener-Policy are custom headers, and an ALB
        // emits only its documented routing.http.response.* set. Asserting the gap is documented keeps
        // it a known limitation rather than something a later reader reports again as a defect.
        expect(cloudFrontSource).toContain("Cross-Origin-Embedder-Policy");
        const albSource = fs.readFileSync(
            path.resolve(
                __dirname,
                "..",
                "../lib/nestedStacks/staticWebApp/constructs/alb-s3-website-albDeploy-construct.ts"
            ),
            "utf-8"
        );
        expect(albSource).toContain("Cross-Origin-Embedder-Policy");
        expect(albSource).toMatch(/no ALB equivalent|platform limitation/);
    });
});
