/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Two findings about who may read the web bundle and where a sign-in may be redirected.
 *
 *  * **S1-INFRA-095** — the web bucket's policy carried TWO `s3:GetObject` allows for
 *    `cloudfront.amazonaws.com`: the one CDK adds for the origin, correctly conditioned on
 *    `AWS:SourceArn` for this deployment's distribution, and a hand-written one whose `conditions`
 *    block was commented out. IAM takes the union of allows, so the unconditioned statement did not
 *    merely duplicate the scoped one — it reinstated read access for any CloudFront distribution in any
 *    account and made the condition beside it inert. The fix removes the hand-written statement, since
 *    `S3BucketOrigin.withOriginAccessControl` already emits exactly the scoped one.
 *
 *  * **S1-INFRA-094** — `http://localhost:3001` was registered unconditionally as a Cognito callback
 *    and logout URL, on the same user pool that serves real users, at both call sites (CloudFront and
 *    ALB). It is now behind `app.webUi.allowLocalhostAuthCallbacks`, default false.
 *
 * The callback assertions need a FEDERATED synth: `CustomCognitoConfigConstruct`, which registers the
 * URLs, is created only when Cognito SAML or OIDC is enabled. Without federation there are no callback
 * URLs at all, so a test run against the shipped templates would pass while checking nothing — and both
 * call sites are exercised, because a fix applied to one is not a fix.
 */

import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

/** Commercial + OIDC federation, which is what creates the callback-URL custom resource. */
const oidcFederation = (c: any) => {
    c.app.authProvider.useCognito.enabled = true;
    c.app.authProvider.useCognito.useOidc = true;
    c.app.authProvider.useCognito.useSaml = false;
};

/** The ALB branch, the second of the two callback-URL call sites. */
const albBranch = (c: any) => {
    c.app.useGlobalVpc.enabled = true;
    c.app.useCloudFront.enabled = false;
    c.app.useAlb.enabled = true;
};

function updateUserPoolClientResources(s: SynthResult): Resource[] {
    return s.resources.filter(
        (r) => r.type === "Custom::AWS" && /updateUserPoolClient/.test(JSON.stringify(r.raw))
    );
}

function callbackUrlsOf(s: SynthResult): string[] {
    const resources = updateUserPoolClientResources(s);
    // Control for every callback assertion: without the custom resource there are no URLs to check.
    expect(resources.length).toBeGreaterThan(0);
    return resources.flatMap((r) => {
        const text = SynthResult.flatten(r.properties.Update ?? r.properties.Create);
        const params = JSON.parse(text).parameters ?? {};
        return [...(params.CallbackURLs ?? []), ...(params.LogoutURLs ?? [])].map((u: unknown) =>
            SynthResult.flatten(u)
        );
    });
}

describe("web bucket policy scopes CloudFront read access", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial");
    });

    /** Every statement on any bucket policy that allows the CloudFront service principal. */
    function cloudFrontStatements() {
        return synth
            .ofType("AWS::S3::BucketPolicy")
            .flatMap((p) => ((p.properties as any).PolicyDocument?.Statement ?? []) as any[])
            .filter((st) =>
                JSON.stringify(st.Principal ?? {}).includes("cloudfront.amazonaws.com")
            );
    }

    test("a CloudFront read statement exists at all", () => {
        // The positive control, and the one that matters most here. "No unconditioned statement" is
        // satisfied by a policy with no CloudFront statement whatsoever — which would break the
        // distribution rather than secure it.
        expect(cloudFrontStatements().length).toBeGreaterThan(0);
    });

    test("every CloudFront read statement is scoped to this deployment's distribution", () => {
        const unscoped = cloudFrontStatements().filter(
            (st) => !JSON.stringify(st.Condition ?? {}).includes("AWS:SourceArn")
        );
        expect(unscoped).toEqual([]);
    });

    test("the scoping condition names a distribution, not a wildcard", () => {
        // A condition present but comparing against "*" would satisfy the assertion above.
        for (const st of cloudFrontStatements()) {
            const condition = JSON.stringify(st.Condition);
            expect(condition).toContain("cloudfront");
            expect(condition).toContain("distribution");
            expect(condition).not.toContain('"*"');
        }
    });

    test("exactly one CloudFront read statement is emitted", () => {
        // The duplicate is what caused the finding: two allows for the same principal, only one
        // conditioned. Asserting the count keeps a future re-addition from being invisible just because
        // it happens to carry a condition too.
        expect(cloudFrontStatements().length).toBe(1);
    });
});

describe("localhost is not a registered auth callback by default", () => {
    const variants: Array<[string, () => SynthResult]> = [
        [
            "CloudFront",
            () =>
                synthTemplate("commercial", {
                    mutate: oidcFederation,
                    mutateKey: "callbacks-oidc-cloudfront",
                }),
        ],
        [
            "ALB",
            () =>
                synthTemplate("commercial", {
                    mutate: (c) => {
                        albBranch(c);
                        oidcFederation(c);
                    },
                    mutateKey: "callbacks-oidc-alb",
                }),
        ],
    ];

    describe.each(variants)("%s branch", (_name, build) => {
        test("no callback or logout URL points at localhost", () => {
            const urls = callbackUrlsOf(build());
            expect(urls.filter((u) => /localhost/i.test(u))).toEqual([]);
        });

        test("no callback or logout URL is plain http", () => {
            // The property that makes the localhost entry a problem is that it is an unencrypted
            // redirect target, so it is worth asserting directly rather than only by hostname.
            const urls = callbackUrlsOf(build());
            expect(urls.filter((u) => u.startsWith("http://"))).toEqual([]);
        });

        test("the deployment's own origin IS still registered", () => {
            // Removing localhost must not have emptied the list — an empty callback list breaks
            // federated sign-in entirely, and every assertion above is satisfied by one.
            const urls = callbackUrlsOf(build());
            expect(urls.length).toBeGreaterThan(0);
            expect(urls.every((u) => u.startsWith("https://"))).toBe(true);
        });
    });
});

describe("the localhost callbacks can be opted back in", () => {
    // The escape hatch has to work, or the flag is a way of writing "removed" that reads as
    // "configurable". Local federated development against a deployed user pool is a real workflow.
    test("enabling the flag registers both spellings", () => {
        const synth = synthTemplate("commercial", {
            mutate: (c) => {
                oidcFederation(c);
                c.app.webUi.allowLocalhostAuthCallbacks = true;
            },
            mutateKey: "callbacks-oidc-localhost-allowed",
        });
        const urls = callbackUrlsOf(synth);
        // Cognito matches a registered URL exactly, trailing slash included, and VAMSAuth.tsx derives
        // the value from window.location.origin — so both forms are required for the workflow to work.
        expect(urls).toContain("http://localhost:3001");
        expect(urls).toContain("http://localhost:3001/");
    });
});
