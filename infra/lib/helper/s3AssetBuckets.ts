import * as iam from "aws-cdk-lib/aws-iam";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as sns from "aws-cdk-lib/aws-sns";
import * as crypto from "crypto";
import * as kms from "aws-cdk-lib/aws-kms";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as Config from "../../config/config";
import { Construct } from "constructs";
import { Service } from "../helper/service-helper";
import { NagSuppressions } from "cdk-nag";
import { storageResources } from "../nestedStacks/storage/storageBuilder-nestedStack";

// Define interface for bucket records
export interface S3AssetBucketRecord {
    bucket: s3.IBucket;
    prefix: string;
    defaultSyncDatabaseId: string;
    snsS3ObjectCreatedTopic: sns.ITopic | undefined;
    snsS3ObjectDeletedTopic: sns.ITopic | undefined;
}

// Global array to store bucket records
export const s3AssetBucketRecords: S3AssetBucketRecord[] = [];

// Function to add a bucket to the global array
export function addS3AssetBucket(
    bucket: s3.IBucket,
    prefix: string,
    defaultSyncDatabaseId: string
): void {
    s3AssetBucketRecords.push({
        bucket,
        prefix,
        defaultSyncDatabaseId,
        snsS3ObjectCreatedTopic: undefined,
        snsS3ObjectDeletedTopic: undefined,
    });
}

// Function to get all bucket records
export function getS3AssetBucketRecords(): S3AssetBucketRecord[] {
    return s3AssetBucketRecords;
}
