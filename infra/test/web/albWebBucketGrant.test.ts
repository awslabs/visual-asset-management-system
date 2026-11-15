/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The anonymous grant that lets the ALB serve the web app is limited to reading objects.
 *
 * On the ALB path the load balancer forwards static file requests, unsigned, through a dedicated
 * Amazon S3 interface endpoint to the web app bucket, so the read access cannot be granted to an IAM
 * principal: it is granted to any principal, and narrowed by WHERE the request comes from. Two policies
 * carry that grant and both have to be narrow, because a request is allowed only when the endpoint
 * policy and the bucket policy both allow it:
 *
 *  * the web app bucket policy statement, conditioned on `aws:SourceVpce` naming the ALB's endpoint;
 *  * the endpoint policy on that interface endpoint.
 *
 * Serving the app needs `GET` and `HEAD` of an object, which are both `s3:GetObject`. Nothing the app
 * loads is a bucket-level request, so the grant names the bucket's objects and not the bucket, and no
 * `s3:List*` action or other `s3:Get*` action (ACLs, tags, versions, bucket configuration). Without
 * `s3:ListBucket`, Amazon S3 answers a request for a key that does not exist with 403 rather than 404.
 *
 * Least privilege for an anonymous grant: each statement names one action and the object ARN only. The
 * assertions compare the whole action and resource lists, not a substring, so a wildcard or an extra
 * action added later fails here rather than widening the grant unnoticed.
 *
 * Three cells: the GovCloud template (ships with the ALB, isolated subnets), the commercial template
 * switched to an internet-facing ALB, and an ALB whose endpoint is created outside the stack
 * (`addAlbS3SpecialVpcEndpoint: false`), where only the bucket policy statement is emitted.
 */

import { Resource, SynthResult, synthTemplate } from "../support/templateSynth";

/** The bucket name the harness gives the ALB web app bucket on the shipped ALB templates. */
const GOVCLOUD_DOMAIN_HOST = "vams-t1.example.com";
/** The bucket name used for the commercial ALB cells, which set their own domain host. */
const COMMERCIAL_DOMAIN_HOST = "vams-t1-alb.example.com";

/** Commercial template served through an internet-facing ALB instead of CloudFront. */
function commercialAlb(addSpecialEndpoint: boolean) {
    return (c: any) => {
        c.app.useGlobalVpc.enabled = true;
        c.app.useCloudFront.enabled = false;
        c.app.useAlb.enabled = true;
        c.app.useAlb.usePublicSubnet = true;
        c.app.useAlb.addAlbS3SpecialVpcEndpoint = addSpecialEndpoint;
        c.app.useAlb.domainHost = COMMERCIAL_DOMAIN_HOST;
        c.app.useAlb.certificateArn =
            "arn:aws:acm:us-east-1:123456789012:certificate/11111111-2222-3333-4444-555555555555";
        c.app.useAlb.optionalHostedZoneId = "";
    };
}

interface Cell {
    name: string;
    domainHost: string;
    expectEndpoint: boolean;
    build: () => SynthResult;
}

const CELLS: Cell[] = [
    {
        name: "govcloud template (ALB, isolated subnets)",
        domainHost: GOVCLOUD_DOMAIN_HOST,
        expectEndpoint: true,
        build: () => synthTemplate("govcloud"),
    },
    {
        name: "commercial template with an internet-facing ALB",
        domainHost: COMMERCIAL_DOMAIN_HOST,
        expectEndpoint: true,
        build: () =>
            synthTemplate("commercial", {
                mutate: commercialAlb(true),
                mutateKey: "alb-web-bucket-grant-public",
            }),
    },
    {
        name: "commercial ALB with the S3 interface endpoint created outside the stack",
        domainHost: COMMERCIAL_DOMAIN_HOST,
        expectEndpoint: false,
        build: () =>
            synthTemplate("commercial", {
                mutate: commercialAlb(false),
                mutateKey: "alb-web-bucket-grant-external-endpoint",
            }),
    },
];

const asList = (v: unknown): unknown[] => (v === undefined ? [] : Array.isArray(v) ? v : [v]);

/** True for `"*"`, `{AWS: "*"}` and `{AWS: ["*"]}` — the forms CDK emits for `AnyPrincipal`. */
function isAnonymous(principal: any): boolean {
    if (principal === "*") return true;
    if (principal && typeof principal === "object") {
        return asList(principal.AWS).includes("*");
    }
    return false;
}

function anonymousAllows(statements: any[]): any[] {
    return statements.filter((st) => st.Effect === "Allow" && isAnonymous(st.Principal));
}

/** The ALB web app bucket: on this path it is named for the domain host. */
function webAppBucket(synth: SynthResult, domainHost: string): Resource {
    const buckets = synth.where(
        "AWS::S3::Bucket",
        (r) => SynthResult.flatten(r.properties.BucketName) === domainHost
    );
    expect(buckets).toHaveLength(1);
    return buckets[0];
}

function bucketPolicyStatements(synth: SynthResult, bucket: Resource): any[] {
    const policies = synth.where(
        "AWS::S3::BucketPolicy",
        (p) => p.stack === bucket.stack && (p.properties.Bucket as any)?.Ref === bucket.logicalId
    );
    expect(policies).toHaveLength(1);
    return (policies[0].properties.PolicyDocument?.Statement ?? []) as any[];
}

/** Interface endpoints whose policy names the web app bucket. */
function webAppEndpointStatements(synth: SynthResult, bucket: Resource): any[][] {
    return synth
        .where(
            "AWS::EC2::VPCEndpoint",
            (e) =>
                e.properties.VpcEndpointType === "Interface" &&
                JSON.stringify(e.properties.PolicyDocument ?? {}).includes(bucket.logicalId)
        )
        .map((e) => (e.properties.PolicyDocument?.Statement ?? []) as any[]);
}

/** The single object-read statement this fix requires, compared as whole lists. */
function expectObjectReadOnly(statement: any, bucket: Resource) {
    expect(asList(statement.Action)).toEqual(["s3:GetObject"]);
    expect(statement.NotAction).toBeUndefined();
    expect(statement.NotResource).toBeUndefined();
    expect(asList(statement.Resource).map((r) => SynthResult.flatten(r))).toEqual([
        `\${${bucket.logicalId}.Arn}/*`,
    ]);
}

describe.each(CELLS)("$name: ALB web app bucket grant", ({ domainHost, expectEndpoint, build }) => {
    let synth: SynthResult;
    let bucket: Resource;

    beforeAll(() => {
        synth = build();
        bucket = webAppBucket(synth, domainHost);
    });

    test("the bucket policy has exactly one anonymous allow", () => {
        // Positive control as well as the count: no anonymous allow at all would leave the ALB unable
        // to serve the app, and would satisfy every "nothing broader" assertion below.
        expect(anonymousAllows(bucketPolicyStatements(synth, bucket))).toHaveLength(1);
    });

    test("that allow is s3:GetObject on the bucket's objects only", () => {
        const [statement] = anonymousAllows(bucketPolicyStatements(synth, bucket));
        expectObjectReadOnly(statement, bucket);
    });

    test("that allow stays conditioned on the source VPC endpoint", () => {
        const [statement] = anonymousAllows(bucketPolicyStatements(synth, bucket));
        expect(Object.keys(statement.Condition ?? {})).toEqual(["StringEquals"]);
        expect(Object.keys(statement.Condition.StringEquals)).toEqual(["aws:SourceVpce"]);
        const vpce = SynthResult.flatten(statement.Condition.StringEquals["aws:SourceVpce"]);
        if (expectEndpoint) {
            // A reference to the endpoint this stack creates, never a wildcard.
            expect(vpce).toMatch(/^\$\{.+\}$/);
        } else {
            // No endpoint in the stack: the condition matches no endpoint until the operator's own
            // endpoint id is filled in after deployment, so nothing is reachable meanwhile.
            expect(vpce).toBe("");
        }
        expect(vpce).not.toContain("*");
    });

    if (expectEndpoint) {
        test("exactly one interface endpoint carries a policy for the web app bucket", () => {
            expect(webAppEndpointStatements(synth, bucket)).toHaveLength(1);
        });

        test("the endpoint policy allows only s3:GetObject on the bucket's objects", () => {
            const [statements] = webAppEndpointStatements(synth, bucket);
            // The endpoint policy is the endpoint's whole grant, so every statement is checked, not
            // only the anonymous ones.
            expect(statements).toHaveLength(1);
            expect(statements[0].Effect).toBe("Allow");
            expect(isAnonymous(statements[0].Principal)).toBe(true);
            expectObjectReadOnly(statements[0], bucket);
        });
    } else {
        test("no interface endpoint for the web app bucket is created", () => {
            expect(webAppEndpointStatements(synth, bucket)).toEqual([]);
        });
    }
});
