"""
SNS Queuing Lambda for VAMS indexing system.
Reads DynamoDB stream records and publishes them to SNS topics for downstream processing.

This Lambda acts as a bridge between DynamoDB streams and SNS topics, enabling
a decoupled architecture where multiple consumers can subscribe to data changes.

Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0
"""

import os
import boto3
import json
from typing import Dict, List, Any
from aws_lambda_powertools.utilities.typing import LambdaContext
from customLogging.logger import safeLogger

# Initialize AWS clients
sns_client = boto3.client('sns')
logger = safeLogger(service_name="SnsQueuing")

# Load environment variables
try:
    sns_topic_arn = os.environ["SNS_TOPIC_ARN"]
except Exception as e:
    logger.exception("Failed loading environment variables")
    raise e

def publish_to_sns(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Publish DynamoDB stream records to SNS topic.
    
    Args:
        records: List of DynamoDB stream records
        
    Returns:
        dict: Summary of publish operations
    """
    success_count = 0
    error_count = 0
    
    for record in records:
        try:
            # Publish the entire record to SNS
            # The record contains eventName, dynamodb (with Keys, NewImage, OldImage), etc.
            message = json.dumps(record, default=str)
            
            response = sns_client.publish(
                TopicArn=sns_topic_arn,
                Message=message,
                Subject='DynamoDB Stream Event'
            )
            
            success_count += 1
            logger.debug(f"Published record to SNS: {response['MessageId']}")
            
        except Exception as e:
            error_count += 1
            logger.exception(f"Error publishing record to SNS: {e}")
    
    return {
        'success_count': success_count,
        'error_count': error_count,
        'total_records': len(records)
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
        dict: Summary of processing results
    """
    try:
        logger.info(f"Processing {len(event.get('Records', []))} DynamoDB stream records")
        
        # Extract records from event
        records = event.get('Records', [])
        
        if not records:
            logger.warning("No records found in event")
            return {
                'statusCode': 200,
                'body': json.dumps({'message': 'No records to process'})
            }
        
        # Publish records to SNS
        result = publish_to_sns(records)
        
        logger.info(
            f"Published {result['success_count']}/{result['total_records']} records to SNS. "
            f"Errors: {result['error_count']}"
        )
        
        # Return success if at least some records were published
        if result['success_count'] > 0:
            return {
                'statusCode': 200,
                'body': json.dumps({
                    'message': f"Successfully published {result['success_count']} records",
                    'details': result
                })
            }
        else:
            return {
                'statusCode': 500,
                'body': json.dumps({
                    'message': 'Failed to publish any records',
                    'details': result
                })
            }
            
    except Exception as e:
        logger.exception(f"Unhandled error in SNS queuing lambda: {e}")
        return {
            'statusCode': 500,
            'body': json.dumps({'message': f'Internal error: {str(e)}'})
        }
