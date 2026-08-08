/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * TLS policy on the REST API.
 *
 * `SecurityPolicy` is a property of `AWS::ApiGateway::RestApi` itself, not only of a custom
 * `DomainName` — which matters because VAMS serves from the default execute-api endpoint and has no
 * custom domain to attach a policy to. The L2 `SpecRestApi` does not surface the property, so it is
 * set on the underlying L1; that escape hatch is exactly the kind of thing a CDK upgrade or a
 * construct refactor drops silently, since nothing else observes it.
 *
 * The policy is `SecurityPolicy_TLS13_1_2_2021_06`: it raises the floor to TLS 1.2 while still
 * accepting TLS 1.3. A TLS 1.3-ONLY policy cannot be used, because CloudFront negotiates at most
 * TLS 1.2 to a custom origin and so could not reach the API at all — the reason the ungated TLS 1.3
 * policy was replaced. Being an enhanced policy (the `SecurityPolicy_` prefix), it also requires an
 * `EndpointAccessMode`; BASIC keeps CloudFront `/api/*` origin requests, the ALB-to-execute-api
 * redirect, and direct execute-api access working, all of which are cross-host by design.
 *
 * It is applied only outside the GovCloud mode. Those partitions do not offer TLS_1_0 for Regional
 * APIs and are FIPS-compliant by default, so their floor is already TLS 1.2 without VAMS asserting a
 * policy — and asserting one there risks naming a value the partition does not publish.
 *
 * Asserted against the source rather than a synthesized stack: building the API construct requires a
 * fully-populated config plus the cross-stack route registry, so a synth here would be slow and
 * brittle for a one-property contract. The property landing in the template was verified by an actual
 * `cdk synth`.
 */

import * as fs from "fs";
import * as path from "path";

const SOURCE = fs.readFileSync(
    path.join(
        __dirname,
        "..",
        "lib",
        "nestedStacks",
        "apiLambda",
        "constructs",
        "rest-api-gateway-construct.ts"
    ),
    "utf-8"
);

// Whitespace collapsed so a prettier reflow of a call cannot fail an assertion.
const FLAT = SOURCE.replace(/\s+/g, " ");

describe("REST API TLS policy", () => {
    it("sets SecurityPolicy on the underlying L1 RestApi", () => {
        // The L2 has no securityPolicy prop, so an addPropertyOverride on defaultChild is the only
        // way to express this.
        expect(FLAT).toContain('addPropertyOverride("SecurityPolicy"');
        expect(SOURCE).toContain("defaultChild as apigw.CfnRestApi");
    });

    it("uses the TLS 1.2 floor policy, which still accepts TLS 1.3", () => {
        expect(SOURCE).toContain("SecurityPolicy_TLS13_1_2_2021_06");
    });

    it("does NOT use a TLS 1.3-only policy, which CloudFront cannot reach as an origin", () => {
        // The regression this replaced: CloudFront negotiates at most TLS 1.2 to a custom origin, so
        // a 1.3-only API is unreachable through the default VAMS fronting.
        expect(SOURCE).not.toContain("TLS13_1_3_FIPS_2025_09");
        expect(SOURCE).not.toContain("TLS13_1_3_2025_09");
    });

    it("sets EndpointAccessMode, which an enhanced SecurityPolicy_ policy requires", () => {
        expect(FLAT).toContain('addPropertyOverride("EndpointAccessMode", "BASIC")');
        // STRICT would reject the cross-host requests VAMS makes by design (CloudFront origin, ALB
        // redirect, direct execute-api).
        expect(SOURCE).not.toContain('"STRICT"');
    });

    it("applies the policy only outside the GovCloud mode", () => {
        // Those partitions already floor at TLS 1.2 and may not publish this policy name.
        expect(FLAT).toMatch(/if \(!config\.app\.govCloud\.enabled\) \{[^}]*SecurityPolicy/);
    });

    it("does not weaken TLS anywhere", () => {
        // Nothing may drop the floor below TLS 1.2 in any partition.
        expect(SOURCE).not.toMatch(/SecurityPolicy.*TLS_1_0/);
        expect(SOURCE).not.toContain("apigw.SecurityPolicy.TLS_1_0");
    });
});
