/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * T1 tier — the web Amazon CloudFront distribution's viewer TLS floor (#434).
 *
 * CloudFront applies `minimumProtocolVersion` only with a custom certificate. On the default
 * `*.cloudfront.net` domain the service sets the `TLSv1` policy whatever the distribution says, and CDK
 * then warns at synth that the property is ignored. The construct therefore sets `TLSv1.2_2021` only in
 * the custom-domain branch, and this suite pins both sides:
 *
 *   - custom domain: the distribution's ViewerCertificate carries the ACM certificate, `TLSv1.2_2021`
 *     and `sni-only`;
 *   - default domain (the commercial template as shipped): no ViewerCertificate is emitted (CloudFront's
 *     own certificate), and synth raises no "minimumProtocolVersion without certificate" warning;
 *   - GovCloud and EU Sovereign: `getConfig()` refuses CloudFront there, so no distribution is emitted;
 *     the ALB HTTPS listener is the positive control.
 */

import {
    RESTRICTED_TEMPLATES,
    Resource,
    SynthResult,
    expectAbsent,
    synthTemplate,
} from "../support/templateSynth";

// Four full-app synths at ~20 s each.
jest.setTimeout(600_000);

const DISTRIBUTION = "AWS::CloudFront::Distribution";
const LISTENER = "AWS::ElasticLoadBalancingV2::Listener";
const CERTIFICATE_ARN =
    "arn:aws:acm:us-east-1:123456789012:certificate/0123abcd-0123-abcd-0123-0123456789ab";
const IGNORED_WARNING = /minimumProtocolVersion/;

const customDomain = (c: any) => {
    c.app.useCloudFront.enabled = true;
    c.app.useAlb.enabled = false;
    c.app.useCloudFront.customDomain = {
        enabled: true,
        domainHost: "vams-t1.example.com",
        certificateArn: CERTIFICATE_ARN,
        optionalHostedZoneId: "",
    };
};

const S = {
    /** As shipped: CloudFront on, custom domain off. */
    commercial: () => synthTemplate("commercial"),
    commercialCustomDomain: () =>
        synthTemplate("commercial", {
            mutate: customDomain,
            mutateKey: "cloudfront-custom-domain",
        }),
};

/** The one web distribution: the only Distribution whose config names a default root object. */
function webDistribution(s: SynthResult): Resource {
    const found = s.where(
        DISTRIBUTION,
        (r) => r.properties.DistributionConfig?.DefaultRootObject === "index.html"
    );
    expect(found).toHaveLength(1);
    return found[0];
}

describe("CloudFront viewer TLS (#434)", () => {
    test("a custom domain gets the TLSv1.2_2021 security policy with its ACM certificate", () => {
        const config = webDistribution(S.commercialCustomDomain()).properties.DistributionConfig;
        expect(config.Aliases).toEqual(["vams-t1.example.com"]);
        expect(config.ViewerCertificate).toEqual({
            AcmCertificateArn: CERTIFICATE_ARN,
            MinimumProtocolVersion: "TLSv1.2_2021",
            SslSupportMethod: "sni-only",
        });
    });

    test("the default domain emits no ViewerCertificate, so CloudFront's own certificate applies", () => {
        const config = webDistribution(S.commercial()).properties.DistributionConfig;
        expect(config.Aliases).toBeUndefined();
        expect(config.ViewerCertificate).toBeUndefined();
    });

    test("synth raises no 'minimumProtocolVersion is ignored' warning on either domain", () => {
        // Before #434 the default domain raised this warning at StaticWeb/WebApp/WebAppDistribution.
        for (const s of [S.commercial(), S.commercialCustomDomain()]) {
            expect(s.warnings.filter((w) => IGNORED_WARNING.test(w.message))).toEqual([]);
        }
    });

    test.each(RESTRICTED_TEMPLATES)("%s emits no CloudFront distribution", (name) => {
        const s = synthTemplate(name);
        expectAbsent("a CloudFront distribution", s.ofType(DISTRIBUTION), {
            description: `${name} emits the ALB HTTPS listener that fronts the web app instead`,
            count: s.where(LISTENER, (r) => r.properties.Port === 443).length,
        });
    });
});
