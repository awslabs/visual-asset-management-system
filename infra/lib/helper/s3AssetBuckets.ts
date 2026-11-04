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
    // Account that owns the bucket. Undefined for VAMS-owned buckets (same account).
    accountId: string | undefined;
    // Region the bucket lives in. Undefined for buckets in the deployment Region; set for an
    // external bucket in another Region, whose notification topics live in that Region.
    region: string | undefined;
    // KMS key ARN the bucket is encrypted with, if a customer managed key is used.
    // Used to grant the VAMS Lambda/pipeline roles access to a cross-account key.
    kmsKeyArn: string | undefined;
    // Marks this bucket record as the VAMS default asset bucket (houses all pipeline template
    // data + execution-time run I/O under the pipelines/ prefix). Exactly one record is default.
    isDefault: boolean;
}

// Global array to store bucket records
export const s3AssetBucketRecords: S3AssetBucketRecord[] = [];

// Function to add a bucket to the global array
export function addS3AssetBucket(
    bucket: s3.IBucket,
    prefix: string,
    defaultSyncDatabaseId: string,
    accountId?: string,
    kmsKeyArn?: string,
    isDefault?: boolean,
    region?: string
): void {
    s3AssetBucketRecords.push({
        bucket,
        prefix,
        defaultSyncDatabaseId,
        snsS3ObjectCreatedTopic: undefined,
        snsS3ObjectDeletedTopic: undefined,
        accountId,
        region,
        kmsKeyArn,
        isDefault: !!isDefault,
    });
}

// Function to get all bucket records
export function getS3AssetBucketRecords(): S3AssetBucketRecord[] {
    return s3AssetBucketRecords;
}

/**
 * Normalizes a baseAssetsPrefix for keying: "", "/" and undefined are the bucket root, any
 * other value carries a single trailing slash. Same form as validateExternalAssetBuckets.
 */
export function normalizeAssetBucketPrefix(prefix: string | undefined): string {
    if (!prefix || prefix == "" || prefix == "/") {
        return "/";
    }
    return prefix.endsWith("/") ? prefix : prefix + "/";
}

/**
 * Key under which a cross-Region notification stack publishes the topic ARNs for one bucket
 * record, and under which the storage builder looks them up: one entry per registered
 * (bucket ARN, prefix) pair.
 */
export function crossRegionTopicKey(bucketArn: string, prefix: string | undefined): string {
    return `${bucketArn}|${normalizeAssetBucketPrefix(prefix)}`;
}

/** Topic ARNs created in a bucket's own Region for one registered (bucket, prefix) pair. */
export interface CrossRegionBucketTopicArns {
    createdTopicArn: string;
    removedTopicArn: string;
}

/** Map of crossRegionTopicKey() -> topic ARNs, across every cross-Region bucket Region. */
export type CrossRegionBucketTopics = Record<string, CrossRegionBucketTopicArns>;
