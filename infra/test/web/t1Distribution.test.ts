/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier — the web distribution layer: ALB TLS policy, the Content Security Policy, and the Cognito
 * app-client callback URLs.
 *
 * These three things share one property that makes them dangerous: each is emitted by TWO mutually
 * exclusive code paths, and a shipped config template only ever exercises ONE of them.
 *
 *   - `config.template.commercial.json` sets `useAlb.enabled: false`, so
 *     `AlbS3WebsiteAlbDeployConstruct` is NEVER synthesized from a shipped commercial config. An
 *     assertion of the form "the ALB listener in the commercial output has property X" finds no
 *     listener at all and passes while checking nothing.
 *   - `config.template.{govcloud,eusovereign}.json` set `useCloudFront.enabled: false`, so the
 *     CloudFront `ResponseHeadersPolicy` never exists there. The CSP arrives as an ALB listener
 *     attribute instead — an entirely separate delivery mechanism with its own 1 KB size cap.
 *   - No shipped template enables Cognito federation (`useSaml`/`useOidc` are `false` in all three),
 *     so `CustomCognitoConfigConstruct` — and therefore the post-deploy `updateUserPoolClient` call —
 *     is never emitted by any of them.
 *
 * Where a branch a template does not enable is needed, the config is mutated into a hybrid and that is
 * called out in the test. `mutateKey` is mandatory alongside `mutate` so two hybrids cannot share a
 * cache entry.
 *
 * `it.failing` is used for the post-fix state: Jest requires it to fail today and FAILS THE SUITE if it
 * starts passing, so applying the fix forces removal of `.failing`. Because `it.failing` is satisfied by
 * ANY throw — including a synth error or a missing property path — every `.failing` test here has a
 * non-failing companion in "harness controls" or alongside it that proves the resource and property path
 * exist. Without those companions a broken test would masquerade as a proven finding.
 *
 * Fixes covered: FIX-036 (S1-INFRA-027, ALB HTTPS listener SslPolicy), FIX-054 (S5-WEB-027,
 * `'wasm-unsafe-eval'` in script-src), FIX-028 (S1-INFRA-023, updateUserPoolClient parameter set),
 * FIX-012 / FIX-026 (S15-APPLIED-001 / S5-WEB-006, inline-script hashes vs `'unsafe-inline'`).
 */

import * as fs from "fs";
import * as path from "path";
import { INDEX_HTML_INLINE_SCRIPT_HASHES } from "../../lib/helper/cspInlineScriptHashes";
import {
    ALL_TEMPLATES,
    RESTRICTED_TEMPLATES,
    Resource,
    SynthResult,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";

// Six full-app synths at ~20 s each.
jest.setTimeout(600_000);

const LISTENER = "AWS::ElasticLoadBalancingV2::Listener";
const LISTENER_RULE = "AWS::ElasticLoadBalancingV2::ListenerRule";
const RESPONSE_HEADERS_POLICY = "AWS::CloudFront::ResponseHeadersPolicy";
const USER_POOL_CLIENT = "AWS::Cognito::UserPoolClient";
const CSP_LISTENER_ATTRIBUTE = "routing.http.response.content_security_policy.header_value";

/**
 * The ALB branch built from the COMMERCIAL template. The shipped commercial config has
 * `useAlb.enabled: false`, so this branch is otherwise never synthesized in the `aws` partition — which
 * is precisely the partition FIX-036's owner constraint scopes the SslPolicy to. `useGlobalVpc` has to
 * come up with it because the ALB, its subnets and the S3 interface endpoint all need a VPC.
 */
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

/** Cognito OIDC federation, the only shipped-config-representable way to get the custom resource. */
const oidcFederation = (c: any) => {
    c.app.authProvider.useCognito.enabled = true;
    c.app.authProvider.useCognito.useOidc = true;
    c.app.authProvider.useCognito.useSaml = false;
};

/** The six synth variants, each behind a distinct `mutateKey`. */
const S = {
    /** As shipped: CloudFront on, ALB off, no federation, allowUnsafeEvalFeatures false. */
    commercial: () => synthTemplate("commercial"),
    govcloud: () => synthTemplate("govcloud"),
    eusovereign: () => synthTemplate("eusovereign"),
    /** Commercial partition + ALB branch. */
    commercialAlb: () =>
        synthTemplate("commercial", { mutate: albHybrid, mutateKey: "alb-hybrid" }),
    /**
     * Commercial + OIDC federation + `allowUnsafeEvalFeatures: true`. Two unrelated switches share one
     * synth to keep the run under six: the federation flag drives the CloudFront-branch
     * `updateUserPoolClient` custom resource (FIX-028) and the unsafe-eval flag is the independence
     * control for FIX-054. Neither reads the other.
     */
    commercialOidcCf: () =>
        synthTemplate("commercial", {
            mutate: (c) => {
                oidcFederation(c);
                c.app.webUi.allowUnsafeEvalFeatures = true;
            },
            mutateKey: "oidc-cloudfront-unsafeeval",
        }),
    /** Commercial + OIDC federation + ALB branch — the SECOND callback-URL call site. */
    commercialOidcAlb: () =>
        synthTemplate("commercial", {
            mutate: (c) => {
                albHybrid(c);
                oidcFederation(c);
            },
            mutateKey: "oidc-alb",
        }),
};

// ---------------------------------------------------------------------------------------------
// Extraction helpers
// ---------------------------------------------------------------------------------------------

const listenersOnPort = (s: SynthResult, port: number): Resource[] =>
    s.where(LISTENER, (r) => r.properties.Port === port);

/** The CSP string as the ALB actually delivers it: a listener attribute on the HTTPS listener. */
function albCsp(s: SynthResult): string | undefined {
    for (const l of listenersOnPort(s, 443)) {
        const attrs: any[] = l.properties.ListenerAttributes ?? [];
        const hit = attrs.find((a) => a.Key === CSP_LISTENER_ATTRIBUTE);
        if (hit) return SynthResult.flatten(hit.Value);
    }
    return undefined;
}

/** The CSP string as CloudFront actually delivers it: a ResponseHeadersPolicy on the distribution. */
function cloudFrontCsp(s: SynthResult): string | undefined {
    for (const p of s.ofType(RESPONSE_HEADERS_POLICY)) {
        const v =
            p.properties.ResponseHeadersPolicyConfig?.SecurityHeadersConfig?.ContentSecurityPolicy
                ?.ContentSecurityPolicy;
        if (v !== undefined) return SynthResult.flatten(v);
    }
    return undefined;
}

/**
 * Whichever mechanism this deployment shape actually emits, plus which one it was. Asserting the
 * mechanism is part of the point: the two paths have different limits and different failure modes, and
 * a test that silently read the other one would prove nothing about the shape under test.
 */
function csp(s: SynthResult): { mechanism: "cloudfront" | "alb"; value: string } {
    const cf = cloudFrontCsp(s);
    const alb = albCsp(s);
    if (cf !== undefined && alb !== undefined) {
        throw new Error(
            "both CloudFront and ALB emitted a CSP; the test cannot say which one wins"
        );
    }
    if (cf !== undefined) return { mechanism: "cloudfront", value: cf };
    if (alb !== undefined) return { mechanism: "alb", value: alb };
    throw new Error(
        `no CSP found: ${s.countOfType(RESPONSE_HEADERS_POLICY)} response-headers policies, ` +
            `${s.countOfType(LISTENER)} listeners`
    );
}

/** One directive's source list, from the assembled policy string. */
function directive(cspValue: string, name: string): string[] {
    const found = cspValue
        .split(";")
        .map((d) => d.trim())
        .find((d) => d === name || d.startsWith(`${name} `));
    if (found === undefined) {
        throw new Error(`directive "${name}" not present in CSP: ${cspValue}`);
    }
    return found.slice(name.length).trim().split(/\s+/).filter(Boolean);
}

/** Directive names in emission order, for the structure guard. */
function directiveNames(cspValue: string): string[] {
    return cspValue
        .split(";")
        .map((d) => d.trim())
        .filter(Boolean)
        .map((d) => d.split(/\s+/)[0]);
}

/**
 * CSP length as the delivery mechanism will measure it, with CloudFormation tokens replaced by a
 * realistic value. The API Gateway hostname reaches the CSP as a cross-stack parameter Ref, so the
 * flattened `${logicalId}` placeholder is not the length that will be deployed; the substitute is the
 * longest plausible execute-api hostname.
 */
const EXECUTE_API_HOST_STANDIN = "abcdefghij.execute-api.us-gov-west-1.amazonaws.com"; // 49 chars
function deployedCspLength(cspValue: string): number {
    return cspValue.replace(/\$\{[^}]*\}/g, EXECUTE_API_HOST_STANDIN).length;
}

/**
 * The size cap each delivery mechanism enforces, in bytes. ALB listener attribute values "can not
 * exceed 1K bytes in size" (elasticloadbalancing/latest/application/header-modification.html,
 * Limitations); the CloudFront ResponseHeadersPolicy CSP quota is 1783 bytes. Neither is checked at
 * synth — an over-length value fails at CREATE/UPDATE on one path only.
 */
const CSP_DELIVERY_CAP = { alb: 1024, cloudfront: 1783 };

/**
 * `Custom::AWS` resources whose SDK call is `updateUserPoolClient`.
 *
 * Matched against the raw resource rather than a parsed property: `AwsCustomResource` renders the call
 * as a JSON *string* inside an `Fn::Join`, so the template text carries `\"action\":\"...\"` with escaped
 * quotes. A regex written against the unescaped form silently matches nothing.
 */
function updateUserPoolClientResources(s: SynthResult): Resource[] {
    return s.resources.filter(
        (r) => r.type === "Custom::AWS" && /updateUserPoolClient/.test(JSON.stringify(r.raw))
    );
}

/**
 * The parameters the custom resource passes to `updateUserPoolClient`.
 *
 * Flattening resolves the Fn::Join back into the JSON document the Lambda will receive; CloudFormation
 * tokens become `${logicalId}` placeholders, which are still valid inside JSON string values.
 */
function updateUserPoolClientParameters(r: Resource): Record<string, any> {
    const text = SynthResult.flatten(r.properties.Update ?? r.properties.Create);
    let payload: any;
    try {
        payload = JSON.parse(text);
    } catch (e) {
        throw new Error(`updateUserPoolClient payload did not parse as JSON: ${text}`);
    }
    return payload.parameters ?? {};
}

const SOURCE = (...segments: string[]) =>
    fs.readFileSync(path.join(__dirname, "..", "..", ...segments), "utf-8");

// ---------------------------------------------------------------------------------------------
// Harness controls. These run FIRST and are the positive controls that stop every `it.failing`
// below from passing on a synth error or a mistyped property path.
// ---------------------------------------------------------------------------------------------

describe("harness controls: every variant synthesizes and exposes the property paths under test", () => {
    test.each(ALL_TEMPLATES)("%s (as shipped) is a non-trivial assembly", (name) => {
        const s = synthTemplate(name);
        expect(s.resources.length).toBeGreaterThan(100);
    });

    test("the commercial+ALB hybrid emits the ALB constructs the shipped commercial config never does", () => {
        const s = S.commercialAlb();
        expect(s.partition).toBe("aws");
        // The trap in one assertion: as shipped, commercial emits ZERO listeners.
        expect(S.commercial().countOfType(LISTENER)).toBe(0);
        expect(listenersOnPort(s, 443).length).toBe(1);
        expect(listenersOnPort(s, 80).length).toBe(1);
        expect(s.countOfType(RESPONSE_HEADERS_POLICY)).toBe(0);
    });

    test.each(RESTRICTED_TEMPLATES)(
        "%s emits exactly one HTTPS listener and no CloudFront",
        (name) => {
            const s = synthTemplate(name);
            expect(listenersOnPort(s, 443).length).toBe(1);
            expect(listenersOnPort(s, 80).length).toBe(1);
            expect(s.countOfType(RESPONSE_HEADERS_POLICY)).toBe(0);
            expect(s.countOfType("AWS::CloudFront::Distribution")).toBe(0);
        }
    );

    test.each(ALL_TEMPLATES)("%s: a CSP is extractable and non-trivial", (name) => {
        const { mechanism, value } = csp(synthTemplate(name));
        expect(mechanism).toBe(name === "commercial" ? "cloudfront" : "alb");
        expect(directive(value, "script-src")).toContain("'self'");
        // eslint-disable-next-line no-console
        console.log(
            `[T1 csp] ${name} via ${mechanism}: ${deployedCspLength(
                value
            )} bytes deployed\n${value}`
        );
    });

    test("the commercial+ALB hybrid delivers its CSP through the listener attribute", () => {
        // The half of the CSP code path that no shipped commercial config exercises.
        const { mechanism } = csp(S.commercialAlb());
        expect(mechanism).toBe("alb");
    });

    test("both federation variants emit exactly one updateUserPoolClient custom resource", () => {
        for (const s of [S.commercialOidcCf(), S.commercialOidcAlb()]) {
            const found = updateUserPoolClientResources(s);
            expect(found.length).toBe(1);
            const params = updateUserPoolClientParameters(found[0]);
            // Proves the payload parses and the parameter bag is reachable, so a `.failing`
            // assertion on its contents cannot be satisfied by a JSON.parse error instead.
            expect(Object.keys(params)).toContain("CallbackURLs");
            // eslint-disable-next-line no-console
            console.log(`[T1 cognito] parameters: ${Object.keys(params).sort().join(", ")}`);
        }
    });
});

// ---------------------------------------------------------------------------------------------
// FIX-036 — S1-INFRA-027: the ALB HTTPS listener has no SslPolicy, so the ELB default
// (ELBSecurityPolicy-2016-08, which still negotiates TLS 1.0/1.1) fronts the entire web app in the
// restricted partitions.
//
// Owner constraint: set an explicit policy ONLY in the commercial (`aws`) partition and leave the
// service default alone everywhere else, gated on `Partition() === "aws"` rather than on
// `config.app.govCloud.enabled` (an operator flag that can be false while deploying into a restricted
// partition).
// ---------------------------------------------------------------------------------------------

describe("FIX-036 ALB HTTPS listener TLS policy", () => {
    /** A policy name whose floor is TLS 1.2. Deliberately loose: several names satisfy the fix. */
    const isTls12Floor = (p: string) =>
        /^ELBSecurityPolicy-(TLS13-1-2|FS-1-2|TLS-1-2)/.test(p) && !/-1-0-|-1-1-/.test(p);

    it("FIX-036: the commercial-partition HTTPS listener pins an explicit TLS 1.2-floor SslPolicy", () => {
        // `alb.addListener()` in alb-s3-website-albDeploy-construct.ts passes
        // `elbv2.SslPolicy.RECOMMENDED_TLS` (ELBSecurityPolicy-TLS13-1-2-2021-06) here, in place of
        // the ELB default ELBSecurityPolicy-2016-08, which still negotiates TLS 1.0 and 1.1.
        const listener = listenersOnPort(S.commercialAlb(), 443)[0];
        expect(typeof listener.properties.SslPolicy).toBe("string");
        expect(isTls12Floor(listener.properties.SslPolicy)).toBe(true);
    });

    test.each(RESTRICTED_TEMPLATES)(
        "%s leaves the HTTPS listener on the service default SSL policy",
        (name) => {
            // Must keep passing AFTER the fix: the owner scoped the explicit policy to the commercial
            // partition because a named policy may not be published in every partition.
            const s = synthTemplate(name);
            const https = listenersOnPort(s, 443);
            expectAbsent(
                `SslPolicy on the ${name} HTTPS listener`,
                https.filter((l) => l.properties.SslPolicy !== undefined).map((l) => l.logicalId),
                { description: `${name} emits an HTTPS listener at all`, count: https.length }
            );
        }
    );

    test("the port-80 redirect listener is never given an SSL policy", () => {
        // `alb.addRedirect()` (line 323) creates a second, HTTP listener. Setting a policy there is
        // rejected by ELB, so this is the over-tightening catcher for the fix.
        const s = S.commercialAlb();
        const http = listenersOnPort(s, 80);
        expect(http.length).toBe(1);
        expect(http[0].properties.Protocol).toBe("HTTP");
        expectAbsent(
            "SslPolicy on the port-80 redirect listener",
            http.filter((l) => l.properties.SslPolicy !== undefined).map((l) => l.logicalId),
            {
                description: "the same synth emits an HTTPS listener that could carry one",
                count: listenersOnPort(s, 443).length,
            }
        );
    });

    it('FIX-036: the gate is Partition() === "aws", not config.app.govCloud.enabled', () => {
        // Asserted against the source the way restApiTlsPolicy.test.ts does, so a refactor cannot move
        // the gate to the operator-set govCloud flag without failing a test. `govCloud.enabled` can be
        // false while deploying into a restricted partition (a documented known gap), which would then
        // assert a policy name the partition may not publish.
        const src = SOURCE(
            "lib",
            "nestedStacks",
            "staticWebApp",
            "constructs",
            "alb-s3-website-albDeploy-construct.ts"
        );
        const flat = src.replace(/\s+/g, " ");
        expect(flat).toMatch(/sslPolicy:/);
        expect(flat).toMatch(/Partition\(\)\s*===\s*"aws"/);
        expect(flat).not.toMatch(/govCloud\.enabled[^;]{0,200}sslPolicy/);
    });

    test("no emitted template names an SSL policy weaker than TLS 1.2", () => {
        for (const name of ALL_TEMPLATES) {
            const s = synthTemplate(name);
            const weak = s.grep(/ELBSecurityPolicy-(2015-05|2016-08|TLS-1-0|TLS-1-1)/);
            expectAbsent(`weak ELB SSL policy in ${name}`, weak, {
                // Control: the templates contain listeners, i.e. the resource a weak policy would
                // appear on exists in the output being searched.
                description: `${name} emits listeners or a CloudFront distribution`,
                count: s.countOfType(LISTENER) + s.countOfType("AWS::CloudFront::Distribution"),
            });
        }
    });

    test("the CSP attribute and all six listener-rule priorities survive on the same listener", () => {
        // Co-existence fence: `sslPolicy` is set on the SAME listener object that carries the CSP
        // attribute and the six ApplicationListenerRules. If a fix rebuilt the listener rather than
        // adding a prop, this is what notices.
        const s = S.commercialAlb();
        expect(albCsp(s)).toContain("script-src");
        const priorities = s
            .ofType(LISTENER_RULE)
            .map((r) => r.properties.Priority)
            .sort((a, b) => a - b);
        expect(priorities).toEqual([1, 2, 3, 4, 5, 6]);
    });
});

// ---------------------------------------------------------------------------------------------
// FIX-054 — S5-WEB-027: without `'wasm-unsafe-eval'` in script-src, a WASM viewer is either hidden or
// fails unless the deployment turns on full `'unsafe-eval'`.
//
// Owner constraints: `'wasm-unsafe-eval'` is UNCONDITIONAL and nothing else moves — viewers keep
// depending on `allowUnsafeEvalFeatures` / `'unsafe-eval'`, and no viewer gating changes.
// ---------------------------------------------------------------------------------------------

describe("FIX-054 wasm-unsafe-eval in script-src", () => {
    const scriptSrc = (s: SynthResult) => directive(csp(s).value, "script-src");

    it("FIX-054: commercial (CloudFront ResponseHeadersPolicy) script-src has 'wasm-unsafe-eval'", () => {
        // The token is in the base source list, so it reaches every deployment shape regardless of
        // allowUnsafeEvalFeatures, the auth provider or any add-on.
        expect(scriptSrc(S.commercial())).toContain("'wasm-unsafe-eval'");
    });

    it("FIX-054: govcloud (ALB listener attribute) script-src has 'wasm-unsafe-eval'", () => {
        // The ALB delivery path is the half that has never been asserted; the CloudFront test above is
        // what makes this one meaningful rather than a repeat.
        expect(scriptSrc(S.govcloud())).toContain("'wasm-unsafe-eval'");
    });

    it("FIX-054: eusovereign (ALB listener attribute) script-src has 'wasm-unsafe-eval'", () => {
        expect(scriptSrc(S.eusovereign())).toContain("'wasm-unsafe-eval'");
    });

    test("'unsafe-eval' stays gated on allowUnsafeEvalFeatures — the two tokens are independent", () => {
        // Independence control for the three assertions above, and the catcher for a fix that widens the
        // existing gate instead of adding a separate token. All three shipped templates set
        // allowUnsafeEvalFeatures false; the hybrid sets it true.
        for (const name of ALL_TEMPLATES) {
            expect(scriptSrc(synthTemplate(name))).not.toContain("'unsafe-eval'");
        }
        expect(scriptSrc(S.commercialOidcCf())).toContain("'unsafe-eval'");
    });

    test("the three inline-script SHA-256 hashes stay in script-src and 'unsafe-inline' stays out", () => {
        // `'wasm-unsafe-eval'` does not invalidate hash sources the way `'unsafe-inline'` does, but that
        // has to be asserted: adding the token in a way a browser treats as a keyword conflict throws
        // the whole hash mechanism away silently. The FIX-012/FIX-026 block below asserts the same pair
        // with the Physna add-on ON, the only add-on that touches the CSP at all.
        const {
            INDEX_HTML_INLINE_SCRIPT_HASHES,
        } = require("../../lib/helper/cspInlineScriptHashes"); // eslint-disable-line @typescript-eslint/no-var-requires
        expect(INDEX_HTML_INLINE_SCRIPT_HASHES.length).toBe(3);
        for (const name of ALL_TEMPLATES) {
            const sources = scriptSrc(synthTemplate(name));
            for (const hash of INDEX_HTML_INLINE_SCRIPT_HASHES) {
                expect(sources).toContain(hash);
            }
            expect(sources).toContain("'unsafe-hashes'");
            expect(sources).not.toContain("'unsafe-inline'");
        }
    });

    test("the emitted CSP fits the delivery mechanism's size cap in every partition", () => {
        // The caps differ (see CSP_DELIVERY_CAP) and the restricted partitions are the ones nearest the
        // tighter of the two — FIPS in GovCloud and `.amazonaws.eu` in EU Sovereign produce longer
        // endpoint hostnames — and they are the ones with no environment to fail in. `'wasm-unsafe-eval'`
        // and its separating space are 19 of the bytes measured here.
        const measured = ALL_TEMPLATES.map((name) => {
            const { mechanism, value } = csp(synthTemplate(name));
            return {
                name,
                mechanism,
                bytes: deployedCspLength(value),
                cap: CSP_DELIVERY_CAP[mechanism],
            };
        });
        // eslint-disable-next-line no-console
        console.log(`[T1 csp size] ${JSON.stringify(measured)}`);
        expect(measured.filter((m) => m.bytes >= m.cap)).toEqual([]);
    });

    test("the directive list and its order are pinned", () => {
        // Structure guard. generateContentSecurityPolicy() branches on useCognito, the federated auth
        // domain, allowUnsafeEvalFeatures, useLocationService and usePhysnaSync and then merges
        // cspAdditionalConfig.json over every list. A refactor that reorders or de-duplicates changes
        // the header for every deployment shape at once; this makes that a visible diff.
        expect(directiveNames(csp(S.commercial()).value)).toEqual([
            "base-uri",
            "default-src",
            "style-src",
            "upgrade-insecure-requests",
            "connect-src",
            "script-src",
            "worker-src",
            "img-src",
            "media-src",
            "frame-src",
            "object-src",
            "frame-ancestors",
            "font-src",
            "manifest-src",
        ]);
        expect(directive(csp(S.commercial()).value, "frame-ancestors")).toEqual(["'self'"]);
        expect(directive(csp(S.commercial()).value, "object-src")).toEqual(["'none'"]);
    });

    it("FIX-054: the documented script-src row lists 'wasm-unsafe-eval' as unconditional", () => {
        // The token is unconditional, so it belongs in architecture/security.md's BASE script-src row
        // rather than in the conditional-sources table below it.
        const doc = fs.readFileSync(
            path.join(
                __dirname,
                "..",
                "..",
                "..",
                "documentation",
                "docusaurus-site",
                "docs",
                "architecture",
                "security.md"
            ),
            "utf-8"
        );
        const row = doc.split("\n").find((l) => /^\|\s*`script-src`/.test(l));
        // Control: the row exists, so a rename of the table cannot make this pass by absence.
        expect(row).toBeDefined();
        expect(row).toContain("wasm-unsafe-eval");
    });
});

// ---------------------------------------------------------------------------------------------
// FIX-028 — S1-INFRA-023: the post-deploy `updateUserPoolClient` custom resource omits the
// token-validity and auth-flow parameters, so Cognito resets refresh-token validity from the intended
// 24 hours to its 30-day default whenever Cognito federation is enabled.
//
// The construct is built from TWO call sites with DIFFERENT callbackUrls (CloudFront distribution
// domain vs ALB endPointURL) and only the CloudFront one is ever exercised on a dev stack.
// ---------------------------------------------------------------------------------------------

describe("FIX-028 Cognito app-client repair preserves the declared client configuration", () => {
    /**
     * The properties `UpdateUserPoolClient` resets when they are omitted. Read off the emitted
     * UserPoolClient rather than hard-coded, so the required set tracks whatever the CDK client actually
     * declares — a hard-coded list would demand `PreventUserExistenceErrors`, which the client does not
     * set, and would over-constrain the fix.
     */
    const RESETTABLE = [
        "RefreshTokenValidity",
        "TokenValidityUnits",
        "AccessTokenValidity",
        "IdTokenValidity",
        "ExplicitAuthFlows",
        "PreventUserExistenceErrors",
        "ClientName",
        "ReadAttributes",
        "WriteAttributes",
        "EnableTokenRevocation",
    ];

    /**
     * The invariant, written so it holds for either accepted fix: the deployed client must not be left
     * on Cognito defaults. Either the post-deploy repair passes the declared configuration through, or
     * there is no repair because the OAuth settings moved onto the `UserPoolClient` itself.
     */
    function assertClientConfigurationSurvives(s: SynthResult) {
        const clients = s.ofType(USER_POOL_CLIENT);
        // Control: without this, a synth that emitted no Cognito resources satisfies everything below.
        expect(clients.length).toBeGreaterThan(0);
        const declared = clients.find((c) => c.properties.ClientName === "WebClient");
        expect(declared).toBeDefined();
        // 24 hours, expressed in minutes by TokenValidityUnits. Cognito's default is 30 days.
        expect(declared!.properties.RefreshTokenValidity).toBe(24 * 60);

        const repairs = updateUserPoolClientResources(s);
        if (repairs.length === 0) {
            // The "move it into the UserPoolClient oAuth prop" variant of the fix: no post-deploy
            // repair, so the client itself must carry the real callback URLs rather than the CDK
            // placeholder its default oAuth block supplies.
            expect(SynthResult.flatten(declared!.properties.CallbackURLs)).not.toContain(
                "https://example.com"
            );
            return;
        }
        expect(repairs.length).toBe(1);
        const params = updateUserPoolClientParameters(repairs[0]);
        const mustCarry = RESETTABLE.filter((k) => k in declared!.properties);
        expect(mustCarry.length).toBeGreaterThan(0);
        expect(mustCarry.filter((k) => !(k in params))).toEqual([]);

        // Present is not enough: the repair has to send back the SAME configuration the client was
        // created with, or the deployed client silently ends up on a third set of values that neither
        // the template nor the construct states. Comparing against the emitted UserPoolClient rather
        // than a hard-coded expectation is what stops the two drifting — including when a CDK upgrade
        // changes how it renders a duration or the explicit auth-flow list.
        for (const key of mustCarry) {
            expect({ [key]: params[key] }).toEqual({ [key]: declared!.properties[key] });
        }
    }

    it("FIX-028: the CloudFront-branch repair carries the token-validity and auth-flow parameters", () => {
        // `cognitoWebClientUpdateParameters()` in cognito-web-native-construct.ts renders the client
        // configuration as UpdateUserPoolClient parameters, and custom-cognito-config-construct.ts
        // spreads it into the call. Before that, the call passed only ClientId, UserPoolId,
        // SupportedIdentityProviders, AllowedOAuthFlows, AllowedOAuthScopes,
        // AllowedOAuthFlowsUserPoolClient, CallbackURLs and LogoutURLs — and UpdateUserPoolClient is a
        // full replace, so every omitted field reverted to its Cognito default: refresh-token validity
        // went from the declared 24 hours to 30 days.
        assertClientConfigurationSurvives(S.commercialOidcCf());
    });

    it("FIX-028: the ALB-branch repair carries them too — a fix applied to one call site is not enough", () => {
        // staticWebBuilder-nestedStack.ts builds CustomCognitoConfigConstruct at :253 (CloudFront)
        // and again at :358 (ALB). Only the CloudFront branch is exercised on a dev stack.
        assertClientConfigurationSurvives(S.commercialOidcAlb());
    });

    test("the two branches really are different call sites — their CallbackURLs differ", () => {
        // Without this, the pair of tests above could be synthesizing the same branch twice and would
        // prove nothing about the second call site.
        const cfUrls = updateUserPoolClientParameters(
            updateUserPoolClientResources(S.commercialOidcCf())[0]
        ).CallbackURLs;
        const albUrls = updateUserPoolClientParameters(
            updateUserPoolClientResources(S.commercialOidcAlb())[0]
        ).CallbackURLs;
        const flat = (v: any) => SynthResult.flatten(v);
        expect(flat(cfUrls)).not.toEqual(flat(albUrls));
        // The ALB branch uses `https://<useAlb.domainHost>`; CloudFront uses the distribution domain.
        expect(flat(albUrls)).toContain("vams-t1-alb.example.com");
        expect(flat(cfUrls)).not.toContain("vams-t1-alb.example.com");
        // Neither carries a localhost origin — `app.webUi.allowLocalhostAuthCallbacks` is off in every
        // shipped template — so the only thing that distinguishes the two lists is the deployed host,
        // which is what makes the inequality above a statement about the call sites.
        expect(flat(cfUrls)).not.toContain("localhost");
        expect(flat(albUrls)).not.toContain("localhost");
        expect((cfUrls as unknown[]).length).toBeGreaterThan(0);
        expect((albUrls as unknown[]).length).toBeGreaterThan(0);
    });

    test.each(RESTRICTED_TEMPLATES)("%s emits no updateUserPoolClient repair at all", (name) => {
        // NOTE ON WHAT THIS DOES AND DOES NOT PROVE: the restricted templates ship with
        // useSaml/useOidc false AND getConfig() rejects either outside the `aws` partition (the Cognito
        // hosted UI is unavailable there), so the construct is unreachable in these partitions by
        // construction. This pins that — a fix that starts emitting the custom resource, or the OAuth
        // settings, unconditionally would break these deploys — but it is not evidence about the
        // partition-conditional code inside the construct, because the construct never runs.
        const s = synthTemplate(name);
        expectAbsent(
            `updateUserPoolClient custom resource in ${name}`,
            updateUserPoolClientResources(s).map((r) => `${r.stack}/${r.logicalId}`),
            {
                description: `${name} emits Cognito user pool clients at all`,
                count: s.countOfType(USER_POOL_CLIENT),
            }
        );
    });

    test.each(ALL_TEMPLATES)(
        "%s (federation off, as shipped) creates no hosted-UI domain",
        (name) => {
            const s = synthTemplate(name);
            const clients = s.ofType(USER_POOL_CLIENT);
            expect(clients.length).toBeGreaterThan(0);
            expectAbsent(
                `Cognito hosted-UI domain in ${name}`,
                s.ofType("AWS::Cognito::UserPoolDomain").map((r) => r.logicalId),
                { description: `${name} emits user pool clients`, count: clients.length }
            );
        }
    );

    test("enabling OIDC federation DOES create the hosted-UI domain — the control for the guard above", () => {
        // Proves the absence assertion above is observable rather than structurally impossible here.
        expect(S.commercialOidcCf().countOfType("AWS::Cognito::UserPoolDomain")).toBeGreaterThan(0);
    });

    test.each(ALL_TEMPLATES)("%s: the web client declares no OAuth surface", (name) => {
        // This began as the shard's over-tightening catcher — "a fix that always declares oAuth on the
        // client sets AllowedOAuthFlowsUserPoolClient=true on a pool with no hosted-UI domain, which
        // Cognito rejects mid-deploy". The synth refutes that premise: `cognito.UserPoolClient` applies
        // a DEFAULT oAuth block when neither `oAuth` nor `disableOAuth` is passed, so the rejection
        // scenario never applied.
        //
        // The default block is what the construct now suppresses with `disableOAuth`, since a
        // non-federated deployment uses no OAuth flow at all: Amplify signs in with SRP and no user pool
        // domain is created. Held here as well as in cognitoWebClientOAuth.test.ts because this is the
        // tier that runs it against every shipped template, and all three are non-federated.
        const declared = synthTemplate(name)
            .ofType(USER_POOL_CLIENT)
            .find((c) => c.properties.ClientName === "WebClient");
        expect(declared).toBeDefined();
        expect(declared!.properties.AllowedOAuthFlowsUserPoolClient).toBe(false);
        expect(declared!.properties.AllowedOAuthFlows ?? []).not.toContain("implicit");
        expect(SynthResult.flatten(declared!.properties.CallbackURLs ?? [])).not.toContain(
            "https://example.com"
        );
        expect(declared!.properties.AllowedOAuthScopes ?? []).not.toContain(
            "aws.cognito.signin.user.admin"
        );
        // The paired positive: suppressing OAuth must not have disturbed how sign-in actually works.
        expect(declared!.properties.ExplicitAuthFlows).toContain("ALLOW_USER_SRP_AUTH");
    });
});

// ---------------------------------------------------------------------------------------------
// FIX-012 / FIX-026 - a CSP allows inline script by hash OR by 'unsafe-inline', never both
// ---------------------------------------------------------------------------------------------

/**
 * Per CSP Level 3, a hash-source in `script-src` makes browsers **ignore** `'unsafe-inline'` entirely.
 * The two are mutually exclusive, not additive, so emitting both is not belt-and-braces - it silently
 * disables the keyword.
 *
 * `generateContentSecurityPolicy()` therefore seeds the inline-script hashes unconditionally and emits
 * `'unsafe-inline'` in `script-src` for no configuration at all, including the Physna add-on. The add-on
 * does not need the keyword: `PhysnaViewerComponent.tsx` builds its `<iframe src>` from
 * `buildPhysnaViewerUrl()`, an absolute HTTPS URL on Physna's own origin (`physnaApiBase`, which
 * `getConfig()` validates as a URL). A cross-origin document loads under ITS OWN policy, so Physna's
 * inline scripts are outside this policy's reach - `'unsafe-inline'` would buy the viewer nothing while
 * throwing away hash protection for every VAMS page. No Physna content is a `blob:` or `srcdoc` document,
 * which are the two forms that WOULD inherit the parent CSP. What the add-on does need is its origin in
 * `frame-src` and `connect-src`; that is asserted alongside, so the directive the viewer actually depends
 * on is pinned independently of the inline-script question, and doubles as the control proving the
 * add-on branch ran at all.
 *
 * Both branches are asserted, because a one-sided test cannot distinguish this from its inverse - and
 * FIX-012's own constraints say so: "Needs the composition test [...] asserting BOTH branches, or this
 * can silently revert."
 *
 * These assert the CSP as the distribution actually DELIVERS it (a CloudFront ResponseHeadersPolicy
 * here) rather than calling the pure function, so the delivery path is covered too - a policy correct in
 * `security.ts` but truncated or dropped on the way to the header would still be a live defect.
 */
describe("FIX-012/FIX-026: inline-script hashes and 'unsafe-inline' are mutually exclusive", () => {
    const PHYSNA_TENANT_ID = "11111111-2222-3333-4444-555555555555";
    /**
     * Scheme + host of `usePhysnaSync.apiBaseEndpoint` as shipped in every config template
     * (`https://app-api.physna.com/v3/`). The CSP carries the origin only — a source expression matches
     * on scheme, host and port, so the path is dropped.
     */
    const PHYSNA_ORIGIN = "https://app-api.physna.com";
    const physnaCsp = () =>
        csp(
            synthTemplate("commercial", {
                mutate: (c: any) => {
                    c.app.addons.usePhysnaSync.enabled = true;
                    c.app.addons.usePhysnaSync.tenantId = PHYSNA_TENANT_ID;
                },
                mutateKey: "physna-enabled",
            })
        );

    const hashSources = (sources: string[]) => sources.filter((x) => x.includes("sha256-"));

    it("the generated hash constant is non-empty, so the hash assertions are meaningful", () => {
        // Control. Every branch below reasons about the presence or absence of hash sources; if the
        // generated constant were empty both branches would agree trivially and the ratchet would go
        // green for the wrong reason.
        expect(INDEX_HTML_INLINE_SCRIPT_HASHES.length).toBeGreaterThan(0);
    });

    it("Physna OFF: script-src carries every inline hash and no 'unsafe-inline'", () => {
        const { value } = csp(synthTemplate("commercial"));
        const sources = directive(value, "script-src");
        for (const h of INDEX_HTML_INLINE_SCRIPT_HASHES) {
            expect(sources.join(" ")).toContain(h.replace(/^'|'$/g, ""));
        }
        expect(sources).not.toContain("'unsafe-inline'");
    });

    it("frame-src admits the Amazon S3 endpoint, as img-src and media-src already do", () => {
        // The HTML viewer frames an asset file by its presigned Amazon S3 URL. Without the origin on
        // frame-src the browser blocks the frame and the panel renders empty, with the failure visible
        // only as a securitypolicyviolation event — nothing fails at build or deploy time.
        //
        // Asserted against img-src and media-src rather than against a hard-coded hostname: those two
        // already carry the endpoint for the image and media viewers, so comparing to them states the
        // invariant (all three viewer directives reach the same origin) and cannot drift with the
        // partition, which changes the endpoint but not the relationship.
        const { value } = csp(synthTemplate("commercial"));
        const s3Source = directive(value, "img-src").find((s) => s.includes("s3"));
        // Positive control: if img-src stopped carrying it, the assertion below would pass vacuously
        // against `undefined`.
        expect(s3Source).toBeDefined();
        expect(directive(value, "media-src")).toContain(s3Source);
        expect(directive(value, "frame-src")).toContain(s3Source);
    });

    it("Physna ON: the add-on's origin reaches frame-src and connect-src", () => {
        // The directive the viewer genuinely depends on, and the positive control for the two
        // assertions below: without it, "script-src is unchanged" could hold because the mutated
        // config never enabled the add-on rather than because the branch leaves script-src alone.
        const { value } = physnaCsp();
        expect(directive(value, "frame-src")).toContain(PHYSNA_ORIGIN);
        expect(directive(value, "connect-src")).toContain(PHYSNA_ORIGIN);
        // Paired negative: the shipped config has the add-on off, so the origin is absent there.
        const off = csp(synthTemplate("commercial")).value;
        expect(directive(off, "frame-src")).not.toContain(PHYSNA_ORIGIN);
        expect(directive(off, "connect-src")).not.toContain(PHYSNA_ORIGIN);
    });

    it("Physna ON: the CSP is still delivered by the same mechanism", () => {
        // Guards the comparison itself. Enabling an add-on must not change the distribution shape; if it
        // did, the two branches would be reading different delivery paths and the pairing would be
        // meaningless.
        expect(physnaCsp().mechanism).toBe(csp(synthTemplate("commercial")).mechanism);
    });

    it("Physna ON: script-src keeps every hash source and still has no 'unsafe-inline'", () => {
        // The whole point of the pairing: the add-on relaxes nothing in script-src, so the hash sources
        // stay effective for every deployment. `'unsafe-inline'` in a policy that carries hashes is
        // ignored by browsers, so emitting it would be a no-op for Physna's cross-origin iframe and a
        // real loss of protection for VAMS pages if the hashes were dropped to make it take effect.
        const sources = directive(physnaCsp().value, "script-src");
        expect(hashSources(sources).sort()).toEqual([...INDEX_HTML_INLINE_SCRIPT_HASHES].sort());
        expect(sources).not.toContain("'unsafe-inline'");
    });

    it("Physna ON: the script-src source list is identical to the add-on-off policy", () => {
        // Catches a partial revert that adds some other script-src source in the add-on branch, which
        // the two assertions above would not notice.
        expect(directive(physnaCsp().value, "script-src")).toEqual(
            directive(csp(synthTemplate("commercial")).value, "script-src")
        );
    });

    it("Physna ON: the longer policy still fits the delivery mechanism's size cap", () => {
        // Keeping the hashes AND adding the add-on's two origins produces the longest policy this
        // configuration can emit, so the cap is checked on the worst case rather than only on the
        // shipped templates.
        const { mechanism, value } = physnaCsp();
        const bytes = deployedCspLength(value);
        // eslint-disable-next-line no-console
        console.log(`[T1 csp size] physna-on via ${mechanism}: ${bytes} bytes deployed`);
        expect(bytes).toBeLessThan(CSP_DELIVERY_CAP[mechanism]);
    });
});
