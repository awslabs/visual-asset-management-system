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
# Where a row came from: 'static' for a row this function writes from the configuration, 'runtime'
# for a registration added at run time. A runtime row is never matched, rewritten or retired here.
SOURCE_FIELD = 'source'
SOURCE_STATIC = 'static'
SOURCE_RUNTIME = 'runtime'
# The attributes a deployment owns on a row it writes from the configuration. Each is set from the
# configuration, or removed when the configuration does not give it; any other attribute of the row
# is left as it is.
OWNED_FIELDS = (
    'bucketName', 'baseAssetsPrefix', 'isVersioningEnabled', 'isDefault', 'bucketRegion',
    'bucketAccountId', 'isAccelerated', 'tagSyncedObjects', 'syncCreatedQueueUrl', DUPLICATE_OF_FIELD,
    SOURCE_FIELD, 'defaultSyncDatabaseId', 'bucketKmsKeyArn', RETIRED_FIELD, RETIRED_AT_FIELD,
)

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

def is_runtime(item):
    """Whether a row is a registration added at run time rather than from the configuration."""
    return item.get(SOURCE_FIELD) == SOURCE_RUNTIME

def get_existing_entries(table_name):
    """The stored rows: the configuration's rows grouped by registration pair (bucket name, canonical
    prefix), and the runtime rows, which are kept apart so they are never matched or retired. The
    scan is paged to the end, so a table larger than one page does not re-mint the ids of later rows."""
    table = dynamodb.Table(table_name)
    groups = {}
    runtime_rows = []
    scan_kwargs = {}
    while True:
        response = table.scan(**scan_kwargs)
        for item in response.get('Items') or []:
            if not item.get('bucketId'):
                continue
            if is_runtime(item):
                runtime_rows.append(item)
                continue
            groups.setdefault(registration_pair(item), []).append(item)
        if 'LastEvaluatedKey' not in response:
            break
        scan_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']
    return groups, runtime_rows

def prefixes_overlap(first, second):
    """Whether two canonical prefixes of one bucket can hold the same object: equal, one inside the
    other, or either is the bucket root."""
    if first == '/' or second == '/':
        return True
    return first == second or first.startswith(second) or second.startswith(first)

def activated_pairs(configured_pairs, existing_groups):
    """The configured pairs this deployment makes active: a pair with no stored row, or one whose
    stored rows are all retired (a registration configured again)."""
    return [pair for pair in configured_pairs
            if pair not in existing_groups or all(row.get(RETIRED_FIELD) is True for row in existing_groups[pair])]

def is_rollback(event):
    """Whether an Update is CloudFormation rolling a failed update back. A rollback sends the previous
    properties as ResourceProperties and the failed update's as OldResourceProperties, so its synth
    timestamp is the older one; a forward update's is newer."""
    new_stamp = str((event.get('ResourceProperties') or {}).get('timestamp') or '')
    old_stamp = str((event.get('OldResourceProperties') or {}).get('timestamp') or '')
    return event.get('RequestType') == 'Update' and bool(new_stamp) and bool(old_stamp) and new_stamp < old_stamp

def stored_overlap_error(configured_pairs, existing_groups, runtime_rows):
    """The failure message for the first registration this deployment makes active (activated_pairs: a
    configured pair with no stored row, or with only retired rows) whose prefix equals or nests with the
    prefix of a stored row of the same bucket that the configuration does not include (a retired row, a
    row this deployment retires, or a runtime row), or None. Assets of that row keep its bucketId and
    their locations under its prefix, so a second registration over the same objects would leave a file
    with two owners.

    A configured pair that already has an active row is not refused (stored_overlap_warnings reports it):
    its overlap is already stored, for example by a release that kept a removed registration active, and
    refusing it would fail that deployment's upgrade and every later deployment. It would also fail the
    rollback: CloudFormation rolls a failed update back by sending the previous properties to this
    (already updated) function, and those name the same stored pairs. A pair with only retired rows is
    not exempt, because re-activating it creates the overlap now. The caller skips this check on a
    rollback (is_rollback): a rollback can re-activate a pair the failed update retired, and refusing it
    would leave the stack unable to roll back."""
    unmatched = unconfigured_rows(configured_pairs, existing_groups, runtime_rows)
    for bucket_name, prefix in sorted(activated_pairs(configured_pairs, existing_groups)):
        for row in overlapping_rows(bucket_name, prefix, unmatched):
            other_prefix = registration_pair(row)[1]
            if is_runtime(row):
                state, way_out = 'added at run time', ''
            elif row.get(RETIRED_FIELD) is True:
                state, way_out = 'retired', ', or configure ' + other_prefix + ' again in place of this entry'
            else:
                state, way_out = 'no longer configured', ', or keep ' + other_prefix + ' in the configuration in place of this entry'
            return (
                "Configuration Error: bucket " + str(bucket_name) + " cannot be registered under the prefix " +
                prefix + ": it overlaps the prefix " + other_prefix + " of the stored registration " +
                str(row.get('bucketId')) + " (" + state + "), and two registrations of one bucket cannot hold "
                "the same objects. Choose a prefix that does not overlap " + other_prefix + way_out +
                ". See 'Overlapping bucket registrations' in the external bucket setup guide."
            )
    return None

def unconfigured_rows(configured_pairs, existing_groups, runtime_rows):
    """The stored rows the configuration does not include: rows of pairs it does not name (retired, or
    retired by this deployment) and every runtime row."""
    unmatched = [row for pair, rows in existing_groups.items() if pair not in configured_pairs
                 for row in rows]
    return unmatched + list(runtime_rows)

def overlapping_rows(bucket_name, prefix, rows):
    """The rows of bucket_name whose canonical prefix equals or nests with prefix, by bucketId."""
    return [row for row in sorted(rows, key=lambda r: str(r.get('bucketId')))
            if registration_pair(row)[0] == bucket_name and prefixes_overlap(prefix, registration_pair(row)[1])]

def stored_overlap_warnings(configured_pairs, existing_groups, runtime_rows, rollback=False):
    """One line per configured pair that is not refused for an overlap (one with an active stored row,
    or every pair on a rollback) and overlaps a stored row of the same bucket that the configuration
    does not include. Such an overlap is reported, not refused (stored_overlap_error)."""
    unmatched = unconfigured_rows(configured_pairs, existing_groups, runtime_rows)
    checked = set() if rollback else set(activated_pairs(configured_pairs, existing_groups))
    lines = []
    for bucket_name, prefix in sorted(pair for pair in configured_pairs if pair not in checked):
        for row in overlapping_rows(bucket_name, prefix, unmatched):
            lines.append(str(bucket_name) + " prefix " + prefix + " overlaps stored registration " +
                         str(row.get('bucketId')) + " (prefix " + registration_pair(row)[1] + ")")
    return lines

def write_owned_fields(table, key, item):
    """Write a configured row: SET every owned field the item carries and REMOVE every owned field it
    does not, leaving any other attribute of a stored row in place (merge, not replace). Creates the
    row when it does not exist."""
    names, values, sets, removes = {}, {}, [], []
    for index, field in enumerate(OWNED_FIELDS):
        name = '#f' + str(index)
        names[name] = field
        if field in item:
            values[':v' + str(index)] = item[field]
            sets.append(name + ' = :v' + str(index))
        else:
            removes.append(name)
    expression = 'SET ' + ', '.join(sets)
    if removes:
        expression += ' REMOVE ' + ', '.join(removes)
    table.update_item(Key=key, UpdateExpression=expression, ExpressionAttributeNames=names,
                      ExpressionAttributeValues=values)

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
        existing_groups, runtime_rows = get_existing_entries(table_name)
        logger.info(f"Existing registrations: {sum(len(rows) for rows in existing_groups.values())} rows "
                    f"for {len(existing_groups)} bucket name and prefix pairs, and {len(runtime_rows)} "
                    "rows added at run time")

        # A registration the configuration makes active may not overlap a stored registration of the
        # same bucket that the configuration does not include. Checked before any row is written, on
        # Create and Update only, so a Delete (and the cleanup after a rollback) is never blocked, and
        # not on a rollback, which restores the configuration of the last successful deployment.
        rolling_back = is_rollback(event)
        if rolling_back:
            logger.info("Rolling back to the previous configuration; overlaps are reported, not refused")
        if request_type in ('Create', 'Update') and not rolling_back:
            overlap = stored_overlap_error(
                {(b.get('bucketName'), canonical_prefix(b.get('prefix'))) for b in buckets},
                existing_groups, runtime_rows)
            if overlap:
                logger.error(overlap)
                raise Exception(overlap)
        stored_overlaps = stored_overlap_warnings(
            {(b.get('bucketName'), canonical_prefix(b.get('prefix'))) for b in buckets},
            existing_groups, runtime_rows, rollback=rolling_back) if request_type in ('Create', 'Update') else []
        for line in stored_overlaps:
            logger.warning("Stored overlapping registrations: " + line + ". Review the assets of the stored "
                           "registration; see 'Overlapping bucket registrations' in the external bucket setup guide.")

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

            # Create or update the record in DynamoDB. Only the owned fields are written, so an
            # attribute something else stored on the row stays; a row that was retired and is
            # configured again loses the retired flag here, because the item does not carry it.
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
                item[SOURCE_FIELD] = SOURCE_STATIC
                # The registration's fallback database for bucket sync, and the bucket's customer
                # managed key when one is configured, so code that serves a registration reads them
                # from the row.
                if bucket.get('defaultSyncDatabaseId'):
                    item['defaultSyncDatabaseId'] = bucket['defaultSyncDatabaseId']
                if bucket.get('bucketKmsKeyArn'):
                    item['bucketKmsKeyArn'] = bucket['bucketKmsKeyArn']
                write_owned_fields(table, {'bucketId': row_key['bucketId'], SORT_KEY: row_key[SORT_KEY]},
                                   {k: v for k, v in item.items() if k not in ('bucketId', SORT_KEY)})
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
                'StoredOverlaps': '; '.join(stored_overlaps)[:REPORT_FIELD_MAX_CHARS],
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
            defaultSyncDatabaseId: record.defaultSyncDatabaseId,
            ...(record.kmsKeyArn ? { bucketKmsKeyArn: record.kmsKeyArn } : {}),
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
