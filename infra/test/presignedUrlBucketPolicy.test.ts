/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import { addPresignedUrlNetworkRestrictionsToBucketPolicy } from "../lib/helper/security";

const makeStackWithBucket = () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, "TestStack");
    const bucket = new s3.Bucket(stack, "TestBucket");
    return { stack, bucket };
};

// Extracts the presigned-URL Deny statements from every bucket policy in the template.
const findPresignedDenyStatements = (template: Template): any[] => {
    const policies = template.findResources("AWS::S3::BucketPolicy");
    return Object.values(policies)
        .flatMap((p: any) => p.Properties.PolicyDocument.Statement)
        .filter((s: any) => s.Sid === "DenyPresignedUrlOutsideAllowedNetworks");
};

describe("addPresignedUrlNetworkRestrictionsToBucketPolicy", () => {
    test("adds no statement when restrictions are undefined", () => {
        const { stack, bucket } = makeStackWithBucket();
        addPresignedUrlNetworkRestrictionsToBucketPolicy(bucket, undefined);
        const template = Template.fromStack(stack);
        expect(findPresignedDenyStatements(template)).toHaveLength(0);
    });

    test("adds no statement when both restriction lists are empty", () => {
        const { stack, bucket } = makeStackWithBucket();
        addPresignedUrlNetworkRestrictionsToBucketPolicy(bucket, {
            allowedIpRanges: [],
            allowedVpceIds: [],
        });
        const template = Template.fromStack(stack);
        expect(findPresignedDenyStatements(template)).toHaveLength(0);
    });

    test("adds a Deny with IP and VPCE conditions when both are configured", () => {
        const { stack, bucket } = makeStackWithBucket();
        addPresignedUrlNetworkRestrictionsToBucketPolicy(bucket, {
            allowedIpRanges: ["203.0.113.0/24", "2001:db8::/32"],
            allowedVpceIds: ["vpce-0123456789abcdef0"],
        });
        const template = Template.fromStack(stack);
        const statements = findPresignedDenyStatements(template);
        expect(statements).toHaveLength(1);
        const statement = statements[0];
        expect(statement.Effect).toEqual("Deny");
        expect(statement.Action).toEqual("s3:*");
        expect(statement.Condition).toEqual({
            StringEquals: { "s3:authType": "REST-QUERY-STRING" },
            BoolIfExists: { "aws:ViaAWSService": "false" },
            NotIpAddressIfExists: {
                "aws:SourceIp": ["203.0.113.0/24", "2001:db8::/32"],
            },
            StringNotEqualsIfExists: { "aws:SourceVpce": ["vpce-0123456789abcdef0"] },
        });
    });

    test("omits the VPCE condition when only IP ranges are configured", () => {
        const { stack, bucket } = makeStackWithBucket();
        addPresignedUrlNetworkRestrictionsToBucketPolicy(bucket, {
            allowedIpRanges: ["203.0.113.0/24"],
            allowedVpceIds: [],
        });
        const template = Template.fromStack(stack);
        const statement = findPresignedDenyStatements(template)[0];
        expect(statement.Condition.NotIpAddressIfExists).toEqual({
            "aws:SourceIp": ["203.0.113.0/24"],
        });
        expect(statement.Condition.StringNotEqualsIfExists).toBeUndefined();
    });

    test("omits the IP condition when only VPCE IDs are configured", () => {
        const { stack, bucket } = makeStackWithBucket();
        addPresignedUrlNetworkRestrictionsToBucketPolicy(bucket, {
            allowedIpRanges: [],
            allowedVpceIds: ["vpce-0123456789abcdef0"],
        });
        const template = Template.fromStack(stack);
        const statement = findPresignedDenyStatements(template)[0];
        expect(statement.Condition.StringNotEqualsIfExists).toEqual({
            "aws:SourceVpce": ["vpce-0123456789abcdef0"],
        });
        expect(statement.Condition.NotIpAddressIfExists).toBeUndefined();
    });

    test("scopes the Deny to object resources only", () => {
        const { stack, bucket } = makeStackWithBucket();
        addPresignedUrlNetworkRestrictionsToBucketPolicy(bucket, {
            allowedIpRanges: ["203.0.113.0/24"],
            allowedVpceIds: [],
        });
        const template = Template.fromStack(stack);
        const statement = findPresignedDenyStatements(template)[0];
        // Single object-level resource ({bucketArn}/*), not the bucket ARN itself
        expect(Array.isArray(statement.Resource)).toBe(false);
        expect(JSON.stringify(statement.Resource)).toContain("/*");
    });
});
