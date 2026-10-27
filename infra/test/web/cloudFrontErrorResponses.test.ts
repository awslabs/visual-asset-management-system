/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The web distribution's custom error responses must agree with what its Amazon S3 origins can
 * return. Through origin access control with read access only, S3 answers a missing key with 403,
 * so a 404 mapping can fire only when the distribution is also granted s3:ListBucket. Routing is
 * hash-based (root CLAUDE.md Rule 10), so no in-app URL needs a rewrite to index.html, and a
 * distribution-wide 403 rewrite would also turn a denied /api/* response into index.html with 200.
 */

import { synthTemplate } from "../support/templateSynth";

const synth = synthTemplate("commercial");

const statements = (): any[] =>
    synth
        .ofType("AWS::S3::BucketPolicy")
        .flatMap((p) => p.properties.PolicyDocument?.Statement ?? []);

const cloudFrontGrantsList = (): boolean =>
    statements().some((s) => {
        const principal = JSON.stringify(s.Principal ?? {});
        const actions = ([] as string[]).concat(s.Action ?? []);
        return (
            s.Effect === "Allow" &&
            principal.includes("cloudfront.amazonaws.com") &&
            actions.some((a) => a === "s3:ListBucket" || a === "s3:*" || a === "s3:List*")
        );
    });

describe("web distribution error responses", () => {
    const distributions = synth.ofType("AWS::CloudFront::Distribution");

    test("the commercial template emits the web distribution (control)", () => {
        expect(distributions.length).toBeGreaterThan(0);
    });

    test("a 404 mapping exists only when the distribution can list the bucket", () => {
        distributions.forEach((d) => {
            const responses: any[] = d.properties.DistributionConfig.CustomErrorResponses ?? [];
            const maps404 = responses.some((r) => r.ErrorCode === 404);
            if (maps404) {
                expect(cloudFrontGrantsList()).toBe(true);
            }
        });
    });

    test("no 403 response is rewritten to the app shell", () => {
        distributions.forEach((d) => {
            const responses: any[] = d.properties.DistributionConfig.CustomErrorResponses ?? [];
            expect(responses.filter((r) => r.ErrorCode === 403)).toEqual([]);
        });
    });
});
