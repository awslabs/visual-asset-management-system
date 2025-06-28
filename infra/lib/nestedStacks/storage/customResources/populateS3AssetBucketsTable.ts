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

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize clients
dynamodb = boto3.resource('dynamodb')
s3_client = boto3.client('s3')

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

def check_bucket_versioning(bucket_name):
    """Check if bucket has versioning enabled"""
    try:
        response = s3_client.get_bucket_versioning(Bucket=bucket_name)
        # Status will be 'Enabled' if versioning is enabled
        is_versioning_enabled = response.get('Status') == 'Enabled'
        logger.info(f"Bucket {bucket_name} versioning status: {is_versioning_enabled}")
        return is_versioning_enabled
    except Exception as e:
        logger.error(f"Error checking versioning for bucket {bucket_name}: {str(e)}")
        # Default to False if there's an error
        return False

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
            
            # Check if bucket has versioning enabled
            is_versioning_enabled = check_bucket_versioning(bucket_name)
            
            # Create or update the record in DynamoDB
            table.put_item(
                Item={
                    'bucketId': bucket_id,
                    'bucketName:baseAssetsPrefix': sort_key,
                    'bucketName': bucket_name,
                    'baseAssetsPrefix': prefix,
                    'isVersioningEnabled': is_versioning_enabled
                }
            )
            logger.info(f"Successfully added/updated record for bucket: {bucket_name} with versioning status: {is_versioning_enabled}")
        
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

    // Grant the Lambda function permissions to check S3 bucket versioning status
    populateS3AssetBucketsTableLambda.addToRolePolicy(
        new iam.PolicyStatement({
            actions: ["s3:GetBucketVersioning"],
            resources: ["arn:aws:s3:::*"],
            effect: iam.Effect.ALLOW,
        })
    );

    // Prepare bucket data for the custom resource
    const bucketRecords = s3AssetBuckets.getS3AssetBucketRecords();
    const bucketData = bucketRecords.map((record) => ({
        bucketName: record.bucket.bucketName,
        prefix: record.prefix || "/",
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
