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
 * The FIPS-compliant TLS 1.3 policy is used because it is available in every partition VAMS targets
 * (GovCloud requires FIPS endpoints), so it is applied unconditionally rather than gated per
 * partition. A non-FIPS commercial-only policy would have to be guarded, and an unrecognized value is
 * rejected at deploy time.
 *
 * Asserted against the source rather than a synthesized stack: building the API construct requires a
 * fully-populated config plus the cross-stack route registry, so a synth here would be slow and
 * brittle for a one-property contract. The property landing in the template was verified by an actual
 * `cdk synth`.
 */

import * as fs from "fs";
import * as path from "path";
import * as apigw from "aws-cdk-lib/aws-apigateway";

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

describe("REST API TLS policy", () => {
    it("sets SecurityPolicy on the underlying L1 RestApi", () => {
        // The L2 has no securityPolicy prop, so an addPropertyOverride on defaultChild is the only
        // way to express this.
        // Matched with whitespace collapsed so a prettier reflow of the call cannot
        // fail the test.
        expect(SOURCE.replace(/[\s]+/g, " ")).toContain('addPropertyOverride( "SecurityPolicy"');
        expect(SOURCE).toContain("defaultChild as apigw.CfnRestApi");
    });

    it("uses the TLS 1.3 policy from the CDK enum rather than a hardcoded string", () => {
        // A literal would not fail when AWS renames or retires the policy; the enum member would.
        expect(SOURCE).toContain("apigw.SecurityPolicy.TLS13_1_3_FIPS_2025_09");
    });

    it("the enum member resolves to the expected AWS policy name", () => {
        // Guards the assumption the assertion above rests on.
        expect(apigw.SecurityPolicy.TLS13_1_3_FIPS_2025_09).toBe(
            "SecurityPolicy_TLS13_1_3_FIPS_2025_09"
        );
    });

    it("applies the policy in EVERY partition, ungated", () => {
        // The FIPS TLS 1.3 policy is available in all partitions VAMS targets, so gating it would
        // leave GovCloud/ISO on the API Gateway default for no reason.
        expect(SOURCE).not.toMatch(/if \(Partition\(\) === "aws"\)[\s\S]{0,400}SecurityPolicy/);
    });

    it("uses the FIPS variant, which is the reason it needs no partition guard", () => {
        expect(SOURCE).toContain("FIPS");
        // The non-FIPS 2025-09 policy is NOT published everywhere; using it unguarded would break a
        // GovCloud deploy.
        expect(SOURCE).not.toContain("apigw.SecurityPolicy.TLS13_1_3_2025_09");
    });

    it("does not weaken other partitions", () => {
        // Nothing may downgrade below TLS 1.2 anywhere.
        expect(SOURCE).not.toMatch(/SecurityPolicy.*TLS_1_0/);
        expect(SOURCE).not.toContain("apigw.SecurityPolicy.TLS_1_0");
    });
});
