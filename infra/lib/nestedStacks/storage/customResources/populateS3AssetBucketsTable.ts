/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as cr from "aws-cdk-lib/custom-resources";
import * as dynamodb from "aws-cdk-lib/aws-dynamodb";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as iam from "aws-cdk-lib/aws-iam";
import { Duration } from "aws-cdk-lib";
import { Construct } from "constructs";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../config/config";
import * as s3AssetBuckets from "../../../helper/s3AssetBuckets";
import { suppressCdkNagLambda } from "../../../helper/security";

/**
 * Creates a custom resource that populates the S3AssetBucketsStorageTable with bucket information
 * @param scope The construct scope
 * @param id The construct ID
 * @param table The DynamoDB table to populate
 * @param newBucket Optional reference to a newly created bucket to add as a dependency
 * @returns The custom resource
 */
export function createPopulateS3AssetBucketsTableCustomResource(
    scope: Construct,
    id: string,
    table: dynamodb.Table,
    newBucket?: s3.Bucket
): cdk.CustomResource {
    // Create the Lambda function for the custom resource
    const populateS3AssetBucketsTableLambda = new lambda.Function(scope, `${id}Lambda`, {
        runtime: LAMBDA_PYTHON_RUNTIME,
        handler: "index.lambda_handler",
        timeout: Duration.minutes(5),
        code: lambda.Code.fromInline(`
import json
import boto3
import uuid
import logging
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize clients
dynamodb = boto3.resource('dynamodb')
s3_clients_by_region = {}

def s3_client_for_region(region):
    """An S3 client for the bucket's Region. A bucket in another Region answers GetBucketVersioning
    only on that Region's endpoint, so the client is built per Region and cached."""
    client = s3_clients_by_region.get(region)
    if client is None:
        client = boto3.client('s3', region_name=region)
        s3_clients_by_region[region] = client
    return client

def generate_guid():
    """Generate a UUID (GUID)"""
    return str(uuid.uuid4())

def get_existing_entries(table_name):
    """Scan the table for existing entries"""
    table = dynamodb.Table(table_name)
    response = table.scan()
    
    existing_entries = {}
    if 'Items' in response and response['Items']:
        for item in response['Items']:
            sort_key = item.get('bucketName:baseAssetsPrefix')
            if sort_key:
                existing_entries[sort_key] = item.get('bucketId')
    
    return existing_entries

def check_bucket_versioning(bucket_name, bucket_region):
    """Check whether a bucket has versioning enabled.

    A failure to READ the setting is raised rather than recorded as "not enabled". The two answers are
    not interchangeable: VAMS stores this flag and uses it to decide whether an asset's file version
    history can be kept, so a bucket that IS versioned but whose status could not be read is registered
    as unversioned and stays that way - every later deployment re-reads it and gets the same wrong
    answer for as long as the grant is missing. The external-bucket documentation also states that a
    bucket VAMS cannot inspect fails the deployment, so raising is the behaviour the operator was told
    to expect.

    A genuinely unversioned bucket answers with no Status field and is still reported as False.
    """
    try:
        response = s3_client_for_region(bucket_region).get_bucket_versioning(Bucket=bucket_name)
        # Status is 'Enabled' when versioning is on, 'Suspended' when it was turned off, and absent
        # when it was never enabled.
        is_versioning_enabled = response.get('Status') == 'Enabled'
        logger.info(f"Bucket {bucket_name} versioning status: {is_versioning_enabled}")
        return is_versioning_enabled
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', '')
        http_status = e.response.get('ResponseMetadata', {}).get('HTTPStatusCode')
        if code in ('AccessDenied', 'AccessDeniedException', 'AllAccessDisabled') or http_status == 403:
            logger.error(
                "Access denied reading the versioning configuration of bucket " + str(bucket_name) +
                ". VAMS cannot determine whether this bucket is versioned, and registering it as "
                "unversioned would silently disable file version history for its assets. Grant "
                "s3:GetBucketVersioning on the bucket to this deployment, or remove the bucket from "
                "app.assetBuckets.externalAssetBuckets."
            )
            raise
        # Anything else - a missing bucket, a region redirect, a throttle - is also a fault the
        # operator needs to see rather than a versioning answer.
        logger.error("Error checking versioning for bucket " + str(bucket_name) + ": " + str(e))
        raise

def verify_transfer_acceleration(bucket_name, bucket_region):
    """Fail the deployment when a bucket flagged for S3 Transfer Acceleration does not have it
    enabled. VAMS never enables acceleration on a bucket it does not own, and presigned URLs for the
    accelerate host fail against a bucket without it, so the mismatch is raised here, naming the
    bucket, its Region and the fix, rather than surfacing later as failed uploads and downloads.
    Every failure is raised as an Exception carrying that text, because the CloudFormation failure
    reason is the exception message and the operator reads it there, not in the log."""
    try:
        response = s3_client_for_region(bucket_region).get_bucket_accelerate_configuration(Bucket=bucket_name)
    except ClientError as e:
        code = e.response.get('Error', {}).get('Code', 'ClientError')
        if code == 'NoSuchBucket':
            fix = (
                "The bucket does not exist in that Region. Create it (with Transfer Acceleration enabled), "
                "correct the bucket ARN or bucketRegion of the entry, or set useS3TransferAcceleration to "
                "false for it."
            )
        elif code in ('AccessDenied', 'AccessDeniedException', 'AllAccessDisabled'):
            fix = (
                "Allow s3:GetAccelerateConfiguration on the bucket in its bucket policy for this deployment's "
                "account (the s3:* grant of the external bucket setup includes it), or set "
                "useS3TransferAcceleration to false for the entry."
            )
        else:
            fix = (
                "Check that the bucket is reachable from this deployment in its Region, or set "
                "useS3TransferAcceleration to false for the entry."
            )
        message = (
            "Could not read the S3 Transfer Acceleration configuration of bucket " + str(bucket_name) +
            " in Region " + str(bucket_region) + " (" + str(code) + "). " + fix
        )
        logger.error(message + " Original error: " + str(e))
        raise Exception(message) from e
    status = response.get('Status')
    if status != 'Enabled':
        message = (
            "S3 Transfer Acceleration is requested for bucket " + str(bucket_name) + " (Region " +
            str(bucket_region) + ") but its status is " + str(status or 'not configured') +
            ". Enable Transfer Acceleration on the bucket (S3 console > Properties > Transfer acceleration) "
            "and deploy again, or set useS3TransferAcceleration to false for it. VAMS does not change the "
            "configuration of a bucket it does not own."
        )
        logger.error(message)
        raise Exception(message)
    logger.info(f"Bucket {bucket_name} Transfer Acceleration status: Enabled")

def lambda_handler(event, context):
    """Main handler function for the Lambda"""
    logger.info(f"Event: {json.dumps(event)}")
    
    try:
        # Extract parameters from the event
        resource_properties = event.get('ResourceProperties', {})
        buckets = json.loads(resource_properties.get('buckets', '[]'))
        table_name = resource_properties.get('tableName')
        
        # Get existing entries from the table
        logger.info(f"Scanning table {table_name} for existing entries...")
        existing_entries = get_existing_entries(table_name)
        logger.info(f"Existing entries: {json.dumps(existing_entries)}")
        
        # Process each bucket
        table = dynamodb.Table(table_name)
        for bucket in buckets:
            bucket_name = bucket.get('bucketName')
            prefix = bucket.get('prefix')
            sort_key = f"{bucket_name}:{prefix}"

            #if prefix doesn't end in a slash, add a slash at the end
            if prefix and not prefix.endswith("/"):
                prefix = prefix + "/"
            
            # Check if an entry with this sort key already exists
            if sort_key in existing_entries:
                # Use the existing bucketId
                bucket_id = existing_entries[sort_key]
                logger.info(f"Found existing entry for {sort_key} with bucketId: {bucket_id}")
            else:
                # Generate a new bucketId
                bucket_id = generate_guid()
                logger.info(f"Generating new bucketId for {sort_key}: {bucket_id}")
            
            logger.info(f"Processing bucket: {bucket_name} with prefix: {prefix}")

            # The bucket's Region and owning account, resolved at synth time per bucket record.
            # Handlers build their S3 clients and sign presigned URLs for bucketRegion.
            bucket_region = bucket.get('bucketRegion')
            bucket_account_id = bucket.get('bucketAccountId')

            # Check if bucket has versioning enabled
            is_versioning_enabled = check_bucket_versioning(bucket_name, bucket_region)

            # Whether this bucket record is the VAMS default (houses all pipeline template + run
            # I/O data). Resolved at synth time per bucket record; exactly one record is default.
            is_default = bool(bucket.get('isDefault'))

            # Whether S3 Transfer Acceleration is requested for this bucket. Verified before the row
            # is written, so a bucket without it never gets a row that makes URLs use the accelerate host.
            is_accelerated = bool(bucket.get('isAccelerated'))
            # The check runs on Create and Update only. On Delete (and the cleanup after a rollback) a
            # suspended acceleration or a revoked grant must never block removing the stack.
            if is_accelerated and event.get('RequestType') in ('Create', 'Update'):
                verify_transfer_acceleration(bucket_name, bucket_region)

            # Create or update the record in DynamoDB
            item = {
                'bucketId': bucket_id,
                'bucketName:baseAssetsPrefix': sort_key,
                'bucketName': bucket_name,
                'baseAssetsPrefix': prefix,
                'isVersioningEnabled': is_versioning_enabled,
                'isDefault': is_default,
                'bucketRegion': bucket_region,
            }
            if bucket_account_id:
                item['bucketAccountId'] = bucket_account_id
            # Written only when true, so a row of a bucket without acceleration keeps today's shape
            # (a missing attribute means false) and toggling the flag off removes it on the next deploy.
            if is_accelerated:
                item['isAccelerated'] = True
            table.put_item(Item=item)
            logger.info(f"Successfully added/updated record for bucket: {bucket_name} (region: {bucket_region}, versioning: {is_versioning_enabled}, default: {is_default})")
        
        return {
            'PhysicalResourceId': 'S3AssetBucketsTablePopulator',
            'Data': {
                'Message': f"Successfully processed {len(buckets)} buckets"
            }
        }
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise
        `),
    });

    // Grant the Lambda function permissions to read/write to the DynamoDB table
    table.grantReadWriteData(populateS3AssetBucketsTableLambda);

    const bucketRecords = s3AssetBuckets.getS3AssetBucketRecords();

    // Grant the Lambda function permissions to check S3 bucket versioning status. Scoped to the
    // registered asset buckets; a bucket registered under several prefixes yields one ARN.
    const registeredBucketArns = Array.from(
        new Set(bucketRecords.map((record) => record.bucket.bucketArn))
    );
    if (registeredBucketArns.length > 0) {
        populateS3AssetBucketsTableLambda.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["s3:GetBucketVersioning"],
                resources: registeredBucketArns,
                effect: iam.Effect.ALLOW,
            })
        );
    }

    suppressCdkNagLambda(populateS3AssetBucketsTableLambda);

    // S3 Transfer Acceleration (#1610): the Lambda verifies acceleration is enabled on every flagged
    // bucket, so the read grant is scoped to the flagged buckets only. With no flagged bucket the
    // statement is not emitted and the role's policy is unchanged.
    const acceleratedBucketArns = Array.from(
        new Set(
            bucketRecords
                .filter((record) => record.isAccelerated)
                .map((record) => record.bucket.bucketArn)
        )
    );
    if (acceleratedBucketArns.length > 0) {
        populateS3AssetBucketsTableLambda.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["s3:GetAccelerateConfiguration"],
                resources: acceleratedBucketArns,
                effect: iam.Effect.ALLOW,
            })
        );
    }

    // Prepare bucket data for the custom resource. isDefault is resolved per bucket record at synth
    // time (exactly one record is the VAMS default), so the Lambda writes the flag directly with no
    // name/ARN matching. bucketRegion is the record's Region, or the deployment Region for a bucket
    // registered without one, so every row carries a Region the handlers can sign for.
    const deploymentRegion = cdk.Stack.of(scope).region;
    const bucketData = bucketRecords.map((record) => ({
        bucketName: record.bucket.bucketName,
        prefix: record.prefix || "/",
        isDefault: !!record.isDefault,
        bucketRegion: record.region || deploymentRegion,
        bucketAccountId: record.accountId,
        // Present only when true, so the custom resource properties of a deployment without
        // acceleration are unchanged.
        ...(record.isAccelerated ? { isAccelerated: true } : {}),
    }));

    // Create the custom resource provider
    const populateS3AssetBucketsTableProvider = new cr.Provider(scope, `${id}Provider`, {
        onEventHandler: populateS3AssetBucketsTableLambda,
    });

    // Create the custom resource
    const customResource = new cdk.CustomResource(scope, id, {
        serviceToken: populateS3AssetBucketsTableProvider.serviceToken,
        properties: {
            buckets: JSON.stringify(bucketData),
            tableName: table.tableName,
            // Add a timestamp to force the custom resource to run on every deployment
            timestamp: new Date().toISOString(),
        },
    });

    // Add dependency to ensure the table exists before the custom resource runs
    customResource.node.addDependency(table);

    // Add dependency to ensure the new bucket exists before the custom resource runs (if provided)
    if (newBucket) {
        customResource.node.addDependency(newBucket);
    }

    return customResource;
}
