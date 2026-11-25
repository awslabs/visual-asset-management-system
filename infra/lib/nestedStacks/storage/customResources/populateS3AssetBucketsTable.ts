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
 * @param assetTable Optional asset table and its bucketId index. When two rows exist for one
 *   registration, the Lambda queries the index (one item per row) to keep the row assets reference
 *   as the one that answers lookups. Without it the Lambda keeps every row and logs the choice.
 * @returns The custom resource
 */
export function createPopulateS3AssetBucketsTableCustomResource(
    scope: Construct,
    id: string,
    table: dynamodb.Table,
    newBucket?: s3.Bucket,
    assetTable?: { table: dynamodb.Table; bucketIdIndexName: string }
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
from datetime import datetime, timezone
from botocore.exceptions import ClientError

# Configure logging
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialize clients
dynamodb = boto3.resource('dynamodb')
s3_clients_by_region = {}

SORT_KEY = 'bucketName:baseAssetsPrefix'
# A registration that left the configuration. Its row is kept, never deleted, so the assets and
# databases that name its bucketId still resolve; lookups by bucket name and prefix and the default
# bucket choice leave it out (backend common/bucketRegistrations.py).
RETIRED_FIELD = 'retired'
RETIRED_AT_FIELD = 'retiredAt'
# A second row for one bucket name and prefix, written by an earlier release when only the prefix's
# spelling changed. It names the row that answers lookups by name and prefix (the primary).
DUPLICATE_OF_FIELD = 'duplicateOfBucketId'
REPORT_FIELD_MAX_CHARS = 1000

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

def canonical_prefix(prefix):
    """A registration prefix with exactly one trailing '/', and '/' for the bucket root. 'assets',
    'assets/' and 'assets//' are one registration; a leading '/' is kept as written."""
    trimmed = (prefix or '').rstrip('/')
    return trimmed + '/' if trimmed else '/'

def registration_pair(item):
    """(bucket name, canonical prefix) of a stored row, read from its attributes, or from its sort key
    for a row that lacks them. A bucket name has no ':', so the sort key splits at the first one."""
    bucket_name = item.get('bucketName')
    prefix = item.get('baseAssetsPrefix')
    if bucket_name is None or prefix is None:
        stored_name, _, stored_prefix = str(item.get(SORT_KEY) or '').partition(':')
        bucket_name = bucket_name if bucket_name is not None else stored_name
        prefix = prefix if prefix is not None else stored_prefix
    return bucket_name, canonical_prefix(prefix)

def get_existing_entries(table_name):
    """Every stored row, grouped by registration pair (bucket name, canonical prefix). The scan is
    paged to the end, so a table larger than one page does not re-mint the ids of later rows."""
    table = dynamodb.Table(table_name)
    groups = {}
    scan_kwargs = {}
    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get('Items') or []:
            if not item.get('bucketId'):
                continue
            groups.setdefault(registration_pair(item), []).append(item)
        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    return groups

def referenced_bucket_ids(bucket_ids, asset_table_name, asset_bucket_index):
    """The ids among bucket_ids that at least one asset row names: one Query of one item per id on
    the asset table's bucketId index, never a scan. None when the answer cannot be read."""
    if not asset_table_name or not asset_bucket_index:
        return None
    from boto3.dynamodb.conditions import Key
    asset_table = dynamodb.Table(asset_table_name)
    referenced = set()
    try:
        for bucket_id in bucket_ids:
            response = asset_table.query(
                IndexName=asset_bucket_index,
                KeyConditionExpression=Key('bucketId').eq(bucket_id),
                Limit=1,
            )
            if response.get('Items'):
                referenced.add(bucket_id)
    except ClientError as e:
        logger.warning("Could not read which duplicate registrations assets reference: " + str(e))
        return None
    return referenced

def choose_primary(rows, sort_key, asset_table_name, asset_bucket_index):
    """The row of a duplicate group that answers lookups by bucket name and prefix, and whether the
    choice is unresolved (several rows are referenced by assets and none was chosen before).

    1. A row already chosen by an earlier deployment: the one row the others name in
       duplicateOfBucketId, when it is in the group.
    2. The one row assets reference (one Query per row on the asset table's bucketId index).
    3. Otherwise the row whose sort key the current configuration spells, then the lowest bucketId.
    """
    ids = {row['bucketId'] for row in rows}
    named = {row.get(DUPLICATE_OF_FIELD) for row in rows if row.get(DUPLICATE_OF_FIELD)}
    previous = [row for row in rows if row['bucketId'] in named and not row.get(DUPLICATE_OF_FIELD)]
    if len(previous) == 1 and named <= ids:
        return previous[0], False
    ordered = sorted(rows, key=lambda row: (0 if row.get(SORT_KEY) == sort_key else 1, row['bucketId']))
    referenced = referenced_bucket_ids([row['bucketId'] for row in ordered], asset_table_name,
                                       asset_bucket_index)
    if referenced is not None:
        candidates = [row for row in ordered if row['bucketId'] in referenced]
        if len(candidates) == 1:
            return candidates[0], False
        if len(candidates) > 1:
            return candidates[0], True
        return ordered[0], False
    return ordered[0], True

def retire_row(table, row, retired_at):
    """Mark a row whose registration left the configuration. The row is kept: an asset or a database
    that names its bucketId still resolves. The update is conditional on the row still existing, so a
    row removed by hand is not written back."""
    try:
        table.update_item(
            Key={'bucketId': row['bucketId'], SORT_KEY: row[SORT_KEY]},
            UpdateExpression='SET #retired = :true, #isDefault = :false, #retiredAt = :at',
            ConditionExpression='attribute_exists(bucketId)',
            ExpressionAttributeNames={'#retired': RETIRED_FIELD, '#isDefault': 'isDefault',
                                      '#retiredAt': RETIRED_AT_FIELD},
            ExpressionAttributeValues={':true': True, ':false': False, ':at': retired_at},
        )
    except ClientError as e:
        if e.response.get('Error', {}).get('Code') != 'ConditionalCheckFailedException':
            raise

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
        asset_table_name = resource_properties.get('assetTableName')
        asset_bucket_index = resource_properties.get('assetTableBucketIdIndex')
        request_type = event.get('RequestType')

        # A registration is matched on its bucket name and canonical prefix, read from the stored
        # rows, so a prefix whose spelling changed ('assets' to 'assets/') keeps its bucketId.
        logger.info(f"Scanning table {table_name} for existing entries...")
        existing_groups = get_existing_entries(table_name)
        logger.info(f"Existing registrations: {sum(len(rows) for rows in existing_groups.values())} rows "
                    f"for {len(existing_groups)} bucket name and prefix pairs")

        # Process each bucket
        table = dynamodb.Table(table_name)
        configured_pairs = set()
        duplicate_reports = []
        for bucket in buckets:
            bucket_name = bucket.get('bucketName')
            prefix = canonical_prefix(bucket.get('prefix'))
            pair = (bucket_name, prefix)
            sort_key = f"{bucket_name}:{prefix}"
            if pair in configured_pairs:
                raise Exception(
                    "Two entries of the configuration register bucket " + str(bucket_name) +
                    " under the prefix " + prefix + ". Remove one of them from "
                    "app.assetBuckets.externalAssetBuckets and deploy again."
                )
            configured_pairs.add(pair)

            # The rows already stored for this registration. Each keeps its own key (the sort key is
            # part of the primary key, so a rewritten sort key would be a second item).
            group = existing_groups.get(pair, [])
            duplicate_of = None
            if not group:
                bucket_id = generate_guid()
                rows_to_write = [{'bucketId': bucket_id, SORT_KEY: sort_key}]
                logger.info(f"Generating new bucketId for {sort_key}: {bucket_id}")
            elif len(group) == 1:
                bucket_id = group[0]['bucketId']
                rows_to_write = [{'bucketId': bucket_id, SORT_KEY: group[0][SORT_KEY]}]
                logger.info(f"Found existing entry for {sort_key} with bucketId: {bucket_id}")
            else:
                # Rows an earlier release wrote twice for one registration. None is merged or
                # deleted: the primary answers lookups by name and prefix and carries isDefault, the
                # others name it, and every row keeps resolving for the assets that name it.
                primary, unresolved = choose_primary(group, sort_key, asset_table_name, asset_bucket_index)
                bucket_id = primary['bucketId']
                duplicate_of = bucket_id
                rows_to_write = [{'bucketId': row['bucketId'], SORT_KEY: row[SORT_KEY]}
                                 for row in [primary] + [r for r in group if r is not primary]]
                others = sorted(row['bucketId'] for row in group if row is not primary)
                report = (f"{bucket_name} prefix {prefix}: {len(group)} registrations; {bucket_id} "
                          f"answers lookups, {', '.join(others)} kept as duplicates")
                duplicate_reports.append(report)
                if unresolved:
                    logger.error(
                        "Bucket " + str(bucket_name) + " prefix " + prefix + " has " + str(len(group)) +
                        " registrations, and assets reference more than one of them (or the references "
                        "could not be read). All are kept; " + bucket_id + " answers lookups by name and "
                        "prefix. Review the assets of " + ", ".join(others) + "."
                    )
                else:
                    logger.warning("Duplicate registrations: " + report)

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

            # Create or update the record in DynamoDB. put_item replaces the whole item, so a row
            # that was retired and is configured again loses the retired flag here.
            for row_key in rows_to_write:
                item = {
                    'bucketId': row_key['bucketId'],
                    SORT_KEY: row_key[SORT_KEY],
                    'bucketName': bucket_name,
                    'baseAssetsPrefix': prefix,
                    'isVersioningEnabled': is_versioning_enabled,
                    'isDefault': is_default and row_key['bucketId'] == bucket_id,
                    'bucketRegion': bucket_region,
                }
                if duplicate_of and row_key['bucketId'] != duplicate_of:
                    item[DUPLICATE_OF_FIELD] = duplicate_of
                if bucket_account_id:
                    item['bucketAccountId'] = bucket_account_id
                # Written only when true, so a row of a bucket without acceleration keeps today's shape
                # (a missing attribute means false) and toggling the flag off removes it on the next deploy.
                if is_accelerated:
                    item['isAccelerated'] = True
                # Whether bucket sync tags the file versions it syncs from this registration (VAMS:databaseId,
                # VAMS:assetId). Always written; a row from an earlier release without it means true.
                item['tagSyncedObjects'] = bucket.get('tagSyncedObjects') is not False
                # The registration's bucket-sync created-events queue, to which createAsset sends an import
                # request for the files already at a bucketExistingKey.
                if bucket.get('syncCreatedQueueUrl'):
                    item['syncCreatedQueueUrl'] = bucket['syncCreatedQueueUrl']
                table.put_item(Item=item)
            logger.info(f"Successfully added/updated record for bucket: {bucket_name} (region: {bucket_region}, versioning: {is_versioning_enabled}, default: {is_default})")

        # Rows whose registration left the configuration are retired, never deleted. Only on Create
        # and Update: a Delete leaves the table as it is. An empty bucket list is never a reason to
        # retire every row (the configuration always registers the default bucket).
        retired = []
        if request_type in ('Create', 'Update') and configured_pairs:
            retired_at = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
            for pair, rows in existing_groups.items():
                if pair in configured_pairs:
                    continue
                for row in rows:
                    if row.get(RETIRED_FIELD) is True:
                        continue
                    retire_row(table, row, retired_at)
                    retired.append(row['bucketId'])
                    logger.info(f"Retired bucketId {row['bucketId']} ({pair[0]} prefix {pair[1]}): the "
                                "registration is no longer configured; its row is kept")

        # A custom resource response is limited to 4 KB, so the report fields are capped; the log
        # lines above carry every id.
        return {
            'PhysicalResourceId': 'S3AssetBucketsTablePopulator',
            'Data': {
                'Message': f"Successfully processed {len(buckets)} buckets",
                'RetiredRegistrations': ', '.join(sorted(retired))[:REPORT_FIELD_MAX_CHARS],
                'DuplicateRegistrations': '; '.join(duplicate_reports)[:REPORT_FIELD_MAX_CHARS],
            }
        }
    except Exception as e:
        logger.error(f"Error: {str(e)}")
        raise
        `),
    });

    // Grant the Lambda function permissions to read/write to the DynamoDB table
    table.grantReadWriteData(populateS3AssetBucketsTableLambda);

    // Which of two rows for one registration assets reference: Query on the asset table's bucketId
    // index only (no table read, no scan), plus decrypt when the table uses the customer managed key.
    if (assetTable) {
        populateS3AssetBucketsTableLambda.addToRolePolicy(
            new iam.PolicyStatement({
                actions: ["dynamodb:Query"],
                resources: [`${assetTable.table.tableArn}/index/${assetTable.bucketIdIndexName}`],
                effect: iam.Effect.ALLOW,
            })
        );
        assetTable.table.encryptionKey?.grantDecrypt(populateS3AssetBucketsTableLambda);
    }

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
    // Built when the template is synthesized, after the storage stack has created each record's
    // bucket-sync queues (their URLs are set on the records then).
    const bucketData = () =>
        bucketRecords.map((record) => ({
            bucketName: record.bucket.bucketName,
            prefix: record.prefix || "/",
            isDefault: !!record.isDefault,
            bucketRegion: record.region || deploymentRegion,
            bucketAccountId: record.accountId,
            // Present only when true, so the custom resource properties of a deployment without
            // acceleration are unchanged.
            ...(record.isAccelerated ? { isAccelerated: true } : {}),
            tagSyncedObjects: record.tagSyncedObjects,
            ...(record.syncCreatedQueueUrl
                ? { syncCreatedQueueUrl: record.syncCreatedQueueUrl }
                : {}),
        }));

    // Create the custom resource provider
    const populateS3AssetBucketsTableProvider = new cr.Provider(scope, `${id}Provider`, {
        onEventHandler: populateS3AssetBucketsTableLambda,
    });

    // Create the custom resource
    const customResource = new cdk.CustomResource(scope, id, {
        serviceToken: populateS3AssetBucketsTableProvider.serviceToken,
        properties: {
            buckets: cdk.Lazy.string({ produce: () => JSON.stringify(bucketData()) }),
            tableName: table.tableName,
            ...(assetTable
                ? {
                      assetTableName: assetTable.table.tableName,
                      assetTableBucketIdIndex: assetTable.bucketIdIndexName,
                  }
                : {}),
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
