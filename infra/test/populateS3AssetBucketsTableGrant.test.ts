/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import { createPopulateS3AssetBucketsTableCustomResource } from "../lib/nestedStacks/storage/customResources/populateS3AssetBucketsTable";
import { s3AssetBucketRecords, addS3AssetBucket } from "../lib/helper/s3AssetBuckets";

// Collects the resource list of every s3:GetBucketVersioning statement in the template.
const findGetBucketVersioningResources = (template: Template): any[] => {
    const policies = template.findResources("AWS::IAM::Policy");
    return Object.values(policies)
        .flatMap((p: any) => p.Properties.PolicyDocument.Statement)
        .filter((s: any) => JSON.stringify(s.Action ?? "").includes("s3:GetBucketVersioning"))
        .map((s: any) => (Array.isArray(s.Resource) ? s.Resource : [s.Resource]));
};

const buildStack = () => {
    const app = new cdk.App();
    const stack = new cdk.Stack(app, "TestStack", {
        env: { account: "111111111111", region: "us-east-1" },
    });
    const table = new dynamodb.Table(stack, "S3AssetBucketsStorageTable", {
        partitionKey: { name: "bucketId", type: dynamodb.AttributeType.STRING },
        sortKey: { name: "bucketName:baseAssetsPrefix", type: dynamodb.AttributeType.STRING },
    });
    return { stack, table };
};

describe("populateS3AssetBucketsTable s3:GetBucketVersioning grant", () => {
    beforeEach(() => {
        // The bucket registry is a module-level global shared by the storage builder.
        s3AssetBucketRecords.length = 0;
    });

    test("is scoped to the registered asset buckets, not every bucket in the partition", () => {
        const { stack, table } = buildStack();
        addS3AssetBucket(
            s3.Bucket.fromBucketArn(stack, "OwnedBucket", "arn:aws:s3:::vams-owned-bucket"),
            "/",
            "default"
        );
        addS3AssetBucket(
            s3.Bucket.fromBucketArn(stack, "ExternalBucket", "arn:aws:s3:::external-bucket"),
            "teamA/",
            "teamA"
        );

        createPopulateS3AssetBucketsTableCustomResource(
            stack,
            "PopulateS3AssetBucketsTable",
            table
        );

        const resourceLists = findGetBucketVersioningResources(Template.fromStack(stack));
        expect(resourceLists).toHaveLength(1);
        expect(resourceLists[0].sort()).toEqual([
            "arn:aws:s3:::external-bucket",
            "arn:aws:s3:::vams-owned-bucket",
        ]);
        // No partition-wide wildcard.
        expect(resourceLists[0]).not.toContain("arn:aws:s3:::*");
    });

    test("lists a bucket registered under several prefixes once", () => {
        const { stack, table } = buildStack();
        const bucket = s3.Bucket.fromBucketArn(stack, "SharedBucket", "arn:aws:s3:::shared-bucket");
        addS3AssetBucket(bucket, "teamA/", "teamA");
        addS3AssetBucket(bucket, "teamB/", "teamB");

        createPopulateS3AssetBucketsTableCustomResource(
            stack,
            "PopulateS3AssetBucketsTable",
            table
        );

        const resourceLists = findGetBucketVersioningResources(Template.fromStack(stack));
        expect(resourceLists).toHaveLength(1);
        expect(resourceLists[0]).toEqual(["arn:aws:s3:::shared-bucket"]);
    });

    test("adds no statement when no asset bucket is registered", () => {
        const { stack, table } = buildStack();

        createPopulateS3AssetBucketsTableCustomResource(
            stack,
            "PopulateS3AssetBucketsTable",
            table
        );

        expect(findGetBucketVersioningResources(Template.fromStack(stack))).toHaveLength(0);
    });
});
