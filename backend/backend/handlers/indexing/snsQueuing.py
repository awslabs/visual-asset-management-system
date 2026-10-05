"""
SNS Queuing Lambda for VAMS indexing system.
Reads DynamoDB stream records and publishes them to SNS topics for downstream processing.

This Lambda acts as a bridge between DynamoDB streams and SNS topics, enabling
a decoupled architecture where multiple consumers can subscribe to data changes.

A record that could not be published is reported in `batchItemFailures` by its
stream sequence number, so the stream event source mapping retries it instead of
checkpointing past it. A record Amazon SNS cannot accept, such as one over the
SNS message size limit, cannot be published by a retry and is logged and skipped.

Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import os
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError
import json
from typing import Dict, List, Any
from aws_lambda_powertools.utilities.typing import LambdaContext
from customLogging.logger import safeLogger
from common.batchItemFailures import all_batch_item_failures, with_batch_item_failures

# Initialize AWS clients
retry_config = Config(retries={'max_attempts': 5, 'mode': 'adaptive'})
sns_client = boto3.client('sns', config=retry_config)
logger = safeLogger(service_name="SnsQueuing")

# A message over the Amazon SNS 256 KiB size limit, or a publish rejected with one of these
# error codes, cannot succeed on a retry.
SNS_MAX_MESSAGE_BYTES = 262144
NON_RETRYABLE_PUBLISH_ERRORS = {'InvalidParameter', 'InvalidParameterValue'}

# Load environment variables
try:
    sns_topic_arn = os.environ["SNS_TOPIC_ARN"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

def publish_to_sns(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Publish DynamoDB stream records to SNS topic, in stream order.

    Publishing stops at the first record that fails. That record and every record after it
    are unpublished: the event source mapping resumes from the lowest reported sequence
    number, so they are redelivered in their original order and no change is published
    ahead of an earlier change to the same item. A record over the SNS message size limit,
    or one SNS rejects as an invalid parameter, is logged and skipped instead.

    Args:
        records: List of DynamoDB stream records

    Returns:
        dict: success_count, skipped_count, unpublished_count, total_records, and
        batch_item_failures (one {'itemIdentifier': SequenceNumber} entry per unpublished record)
    """
    success_count = 0
    skipped_count = 0
    unpublished: List[Dict[str, Any]] = []

    for index, record in enumerate(records):
        try:
            # Publish the entire record to SNS
            # The record contains eventName, dynamodb (with Keys, NewImage, OldImage), etc.
            message = json.dumps(record, default=str)

            message_bytes = len(message.encode('utf-8'))
            if message_bytes > SNS_MAX_MESSAGE_BYTES:
                skipped_count += 1
                sequence_number = (record.get('dynamodb') or {}).get('SequenceNumber')
                logger.error(
                    f"Skipping stream record {sequence_number}: {message_bytes} bytes exceeds the "
                    f"SNS message size limit of {SNS_MAX_MESSAGE_BYTES} bytes"
                )
                continue

            response = sns_client.publish(
                TopicArn=sns_topic_arn,
                Message=message,
                Subject='DynamoDB Stream Event'
            )
            
            success_count += 1
            logger.debug(f"Published record to SNS: {response['MessageId']}")
            
        except ClientError as e:
            if e.response.get('Error', {}).get('Code') in NON_RETRYABLE_PUBLISH_ERRORS:
                skipped_count += 1
                logger.exception(f"Skipping stream record SNS rejected as invalid: {e}")
                continue
            logger.exception(f"Error publishing record to SNS: {e}")
            unpublished = records[index:]
            break
        except Exception as e:
            logger.exception(f"Error publishing record to SNS: {e}")
            unpublished = records[index:]
            break
    
    return {
        'success_count': success_count,
        'skipped_count': skipped_count,
        'unpublished_count': len(unpublished),
        'total_records': len(records),
        'batch_item_failures': all_batch_item_failures({'Records': unpublished}),
    }

def lambda_handler(event: Dict[str, Any], context: LambdaContext) -> Dict[str, Any]:
    """
    Lambda handler for SNS queuing from DynamoDB streams.
    
    This function processes DynamoDB stream events in batches and publishes
    each record to the configured SNS topic. Downstream consumers can then
    subscribe to the SNS topic via SQS queues for decoupled processing.
    
    Args:
        event: DynamoDB stream event containing Records
        context: Lambda context
        
    Returns:
        dict: Summary of processing results. For a stream batch it carries
        `batchItemFailures` naming every record that was not published (empty when
        all were). A failure that names no record is raised, so the mapping retries
        the whole batch rather than reading an empty report as success.
    """
    try:
        logger.info(f"Processing {len(event.get('Records', []))} DynamoDB stream records")
        
        # Extract records from event
        records = event.get('Records', [])
        
        if not records:
            logger.warning("No records found in event")
            return with_batch_item_failures({
                'statusCode': 200,
                'body': json.dumps({'message': 'No records to process'})
            }, event, [])
        
        # Publish records to SNS
        result = publish_to_sns(records)
        
        logger.info(
            f"Published {result['success_count']}/{result['total_records']} records to SNS. "
            f"Skipped: {result['skipped_count']}. Unpublished: {result['unpublished_count']}"
        )
        
        details = {key: value for key, value in result.items() if key != 'batch_item_failures'}

        if result['unpublished_count'] == 0:
            return with_batch_item_failures({
                'statusCode': 200,
                'body': json.dumps({
                    'message': f"Successfully published {result['success_count']} records",
                    'details': details
                })
            }, event, [])

        if not result['batch_item_failures']:
            raise RuntimeError(
                f"{result['unpublished_count']} unpublished stream record(s) carry no sequence number"
            )

        return with_batch_item_failures({
            'statusCode': 500,
            'body': json.dumps({
                'message': f"Failed to publish {result['unpublished_count']} of {result['total_records']} records",
                'details': details
            })
        }, event, result['batch_item_failures'])
            
    except Exception as e:
        logger.exception(f"Unhandled error in SNS queuing lambda: {e}")
        failures = all_batch_item_failures(event)
        if not failures:
            # An empty report reads as a clean batch; raising fails it whole instead.
            raise
        return with_batch_item_failures({
            'statusCode': 500,
            'body': json.dumps({'message': f'Internal error: {str(e)}'})
        }, event, failures)
