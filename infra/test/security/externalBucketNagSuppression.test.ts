/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `cdk synth` passes CDK Nag when an external asset bucket is configured (#415).
 *
 * A grant on an imported external bucket names the bucket by a configured literal ARN, so the finding
 * reads `Resource::arn:aws:s3:::<bucket>/*`. The bucket-object suppression in
 * `suppressCdkNagErrorsByGrantReadWrite()` only matched a stack reference (`Resource::<...Bucket...Arn>/*`),
 * so every role granted on an external bucket stopped the synth on AwsSolutions-IAM5.
 *
 * `templateSynth` runs with CDK Nag off, so the first group applies the AwsSolutions pack to a small
 * stack that grants a role on an imported bucket and reads the stack's annotations. The second group
 * pins what the new pattern does and does not match.
 *
 * The pattern covers a grant on ONE named bucket (#1650). A bucket ARN with `*` or `?` in its name
 * would make the grant reach every matching bucket, so such a grant must stay an IAM5 finding rather
 * than be hidden by this suppression; `getConfig()` also rejects that ARN before synth.
 */

import * as cdk from "aws-cdk-lib";
import { Annotations, Match } from "aws-cdk-lib/assertions";
import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import { AwsSolutionsChecks } from "cdk-nag";
import * as fs from "fs";
import * as path from "path";
import { suppressCdkNagErrorsByGrantReadWrite } from "../../lib/helper/security";
import { newTestApp } from "../support/testApp";

function iamFindings(grantBucketArn: string, suppress: boolean) {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "ExternalBucketNag", {
        env: { account: "123456789012", region: "us-east-1" },
    });
    const bucket = s3.Bucket.fromBucketAttributes(stack, "ExternalBucket", {
        bucketArn: grantBucketArn,
        account: "222222222222",
        region: "us-east-1",
    });
    const role = new iam.Role(stack, "Grantee", {
        assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });
    bucket.grantReadWrite(role);
    if (suppress) suppressCdkNagErrorsByGrantReadWrite(role);
    cdk.Aspects.of(stack).add(new AwsSolutionsChecks());
    return Annotations.fromStack(stack).findError("*", Match.stringLikeRegexp("AwsSolutions-IAM5"));
}

/**
 * IAM5 findings for a role whose policy grants object access on `objectArn` directly. CDK's
 * `Bucket.fromBucketAttributes()` refuses a bucket name with `*` or `?`, so a wildcard bucket grant is
 * built here as the statement such a grant would produce, to check the suppression on its own.
 */
function iamFindingsForObjectStatement(objectArn: string) {
    const app = newTestApp();
    const stack = new cdk.Stack(app, "ExternalBucketNagStatement", {
        env: { account: "123456789012", region: "us-east-1" },
    });
    const role = new iam.Role(stack, "Grantee", {
        assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
    });
    role.addToPolicy(
        new iam.PolicyStatement({
            actions: ["s3:GetObject*", "s3:PutObject"],
            resources: [objectArn],
        })
    );
    suppressCdkNagErrorsByGrantReadWrite(role);
    cdk.Aspects.of(stack).add(new AwsSolutionsChecks());
    return Annotations.fromStack(stack).findError("*", Match.stringLikeRegexp("AwsSolutions-IAM5"));
}

describe("an external asset bucket grant passes the AwsSolutions IAM5 rule", () => {
    test.each([
        ["commercial", "arn:aws:s3:::vams-external-assets"],
        ["GovCloud", "arn:aws-us-gov:s3:::vams-external-assets"],
        ["EU Sovereign", "arn:aws-eusc:s3:::vams-external-assets"],
    ])("%s bucket ARN", (_partition, bucketArn) => {
        const findings = iamFindings(bucketArn, true);
        expect(findings.map((f) => f.entry.data)).toEqual([]);
    });

    test.each([
        "arn:aws:s3:::*/*",
        "arn:aws:s3:::vams-*/*",
        "arn:aws:s3:::vams-?xternal/*",
        "arn:aws-us-gov:s3:::*/*",
        "arn:aws-eusc:s3:::vams-*/*",
    ])(
        "an object grant on the wildcard bucket %s stays a finding with the helper applied",
        (arn) => {
            const findings = iamFindingsForObjectStatement(arn);
            expect(findings.map((f) => f.entry.data)).toEqual([
                expect.stringContaining(`Resource::${arn}`),
            ]);
        }
    );

    test("the same statement on one named bucket is suppressed", () => {
        // The control for the case above: the harness suppresses a named bucket's object grant.
        expect(iamFindingsForObjectStatement("arn:aws:s3:::vams-external-assets/*")).toEqual([]);
    });

    test("the control: the same grant without the helper is a finding", () => {
        // Proves the harness sees the rule at all; without it a clean result above proves nothing.
        const findings = iamFindings("arn:aws:s3:::vams-external-assets", false);
        expect(findings.length).toBeGreaterThan(0);
    });
});

describe("the external bucket object pattern", () => {
    const source = fs.readFileSync(
        path.resolve(__dirname, "../../lib/helper/security.ts"),
        "utf-8"
    );
    // The helper's own pattern for a literal bucket ARN, as the source spells it.
    const literal = source.match(/regex:\s*"(\/\^Resource::arn:\[a-z-\]\+:s3:::[^"]+)"/);

    test("is declared in security.ts", () => {
        expect(literal).not.toBeNull();
    });

    const patternOf = (): RegExp => {
        // A missing pattern must fail every case below, not match everything through an empty RegExp.
        if (!literal) {
            throw new Error(
                "the literal external bucket ARN pattern is not declared in security.ts"
            );
        }
        return new RegExp(
            literal[1]
                .replace(/^\//, "")
                .replace(/\/[a-z]*$/, "")
                .replace(/\\\\/g, "\\")
        );
    };

    test.each([
        "Resource::arn:aws:s3:::b/*",
        "Resource::arn:aws-us-gov:s3:::b/*",
        "Resource::arn:aws-eusc:s3:::b/*",
        "Resource::arn:aws-cn:s3:::my.bucket-name/*",
        "Resource::arn:aws:s3:::my.bucket_1/*",
        "Resource::arn:aws:s3:::Legacy_Bucket/*",
    ])("matches %s", (finding) => {
        expect(patternOf().test(finding)).toBe(true);
    });

    test.each([
        "Resource::arn:aws:s3:::b/prefix/*",
        "Resource::arn:aws:s3:::b",
        "Resource::arn:aws:s3:::*",
        "Resource::arn:aws:s3:::*/*",
        "Resource::arn:aws:s3:::my-*/*",
        "Resource::arn:aws:s3:::my-?ucket/*",
        "Resource::arn:aws-us-gov:s3:::*/*",
        "Resource::arn:aws-eusc:s3:::b*/*",
        "Resource::arn:aws:iam::123456789012:role/*",
        "Resource::arn:aws:kms:us-east-1:123456789012:key/*",
        "Resource::*",
    ])("does not match %s", (finding) => {
        expect(patternOf().test(finding)).toBe(false);
    });
});
