/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import { Template } from "aws-cdk-lib/assertions";
import { createPopulateS3AssetBucketsTableCustomResource } from "../../lib/nestedStacks/storage/customResources/populateS3AssetBucketsTable";
import { s3AssetBucketRecords, addS3AssetBucket } from "../../lib/helper/s3AssetBuckets";
import { newTestApp } from "../support/testApp";

// Collects the resource list of every s3:GetBucketVersioning statement in the template.
const findGetBucketVersioningResources = (template: Template): any[] => {
    const policies = template.findResources("AWS::IAM::Policy");
    return Object.values(policies)
        .flatMap((p: any) => p.Properties.PolicyDocument.Statement)
        .filter((s: any) => JSON.stringify(s.Action ?? "").includes("s3:GetBucketVersioning"))
        .map((s: any) => (Array.isArray(s.Resource) ? s.Resource : [s.Resource]));
};

const buildStack = () => {
    const app = newTestApp();
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

// Every statement that names cloudformation:DescribeStacks, with its resource list.
const findDescribeStacksStatements = (template: Template): any[] =>
    Object.values(template.findResources("AWS::IAM::Policy"))
        .flatMap((p: any) => p.Properties.PolicyDocument.Statement)
        .filter((s: any) => JSON.stringify(s.Action ?? "").includes("cloudformation:"));

describe("populateS3AssetBucketsTable rollback status read", () => {
    beforeEach(() => {
        s3AssetBucketRecords.length = 0;
    });

    test("is DescribeStacks on the stack that holds the resource only, when it is not nested", () => {
        const { stack, table } = buildStack();
        createPopulateS3AssetBucketsTableCustomResource(
            stack,
            "PopulateS3AssetBucketsTable",
            table
        );
        const statements = findDescribeStacksStatements(Template.fromStack(stack));
        expect(statements).toHaveLength(1);
        expect(statements[0]).toMatchObject({
            Effect: "Allow",
            Action: "cloudformation:DescribeStacks",
            Resource: { Ref: "AWS::StackId" },
        });
    });

    test("is DescribeStacks on the nested stack and its parent, by their stack ids, with no wildcard", () => {
        const app = newTestApp();
        const parent = new cdk.Stack(app, "ParentStack", {
            env: { account: "111111111111", region: "us-east-1" },
        });
        const nested = new cdk.NestedStack(parent, "StorageNested");
        const table = new dynamodb.Table(nested, "S3AssetBucketsStorageTable", {
            partitionKey: { name: "bucketId", type: dynamodb.AttributeType.STRING },
            sortKey: { name: "bucketName:baseAssetsPrefix", type: dynamodb.AttributeType.STRING },
        });
        createPopulateS3AssetBucketsTableCustomResource(
            nested,
            "PopulateS3AssetBucketsTable",
            table
        );

        const nestedTemplate = Template.fromStack(nested);
        const statements = findDescribeStacksStatements(nestedTemplate);
        expect(statements).toHaveLength(1);
        expect(statements[0].Action).toBe("cloudformation:DescribeStacks");
        const [own, parentRef] = statements[0].Resource;
        expect(statements[0].Resource).toHaveLength(2);
        expect(own).toEqual({ Ref: "AWS::StackId" });
        // The parent's stack id reaches the nested template as a parameter the parent sets to its own id.
        const parameter = parentRef.Ref as string;
        expect(Object.keys(nestedTemplate.toJSON().Parameters ?? {})).toContain(parameter);
        const nestedResource = Object.values(
            Template.fromStack(parent).findResources("AWS::CloudFormation::Stack")
        )[0] as any;
        expect(nestedResource.Properties.Parameters[parameter]).toEqual({ Ref: "AWS::StackId" });
        expect(JSON.stringify(statements[0])).not.toContain("*");
    });
});
