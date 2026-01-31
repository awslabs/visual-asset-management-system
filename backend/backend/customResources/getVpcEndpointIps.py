"""
Custom resource to get unique VPC Endpoint IPs for ALB Target Group.
This Lambda function retrieves network interface IPs from a VPC Endpoint
and returns only unique IPs to prevent duplicate target errors.
"""

import json
import boto3
import logging
from typing import Dict, Any, List

logger = logging.getLogger()
logger.setLevel(logging.INFO)

ec2_client = boto3.client('ec2')


def lambda_handler(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    """
    Custom resource handler to get unique VPC Endpoint IPs.
    
    Args:
        event: CloudFormation custom resource event
        context: Lambda context
        
    Returns:
        Response with unique IP addresses
    """
    try:
        logger.info(f"Received event: {json.dumps(event)}")
        
        request_type = event['RequestType']
        
        if request_type in ['Create', 'Update']:
            # Get network interface IDs from resource properties
            network_interface_ids = event['ResourceProperties']['NetworkInterfaceIds']
            
            logger.info(f"Querying network interfaces: {network_interface_ids}")
            
            # Describe network interfaces
            response = ec2_client.describe_network_interfaces(
                NetworkInterfaceIds=network_interface_ids
            )
            
            # Extract unique private IP addresses
            unique_ips = []
            seen_ips = set()
            
            for interface in response['NetworkInterfaces']:
                private_ip = interface['PrivateIpAddress']
                if private_ip not in seen_ips:
                    unique_ips.append(private_ip)
                    seen_ips.add(private_ip)
                    logger.info(f"Added unique IP: {private_ip}")
                else:
                    logger.info(f"Skipped duplicate IP: {private_ip}")
            
            logger.info(f"Found {len(unique_ips)} unique IPs out of {len(response['NetworkInterfaces'])} interfaces")
            
            # Return response
            return {
                'Status': 'SUCCESS',
                'PhysicalResourceId': event.get('PhysicalResourceId', f"vpc-endpoint-ips-{context.aws_request_id}"),
                'StackId': event['StackId'],
                'RequestId': event['RequestId'],
                'LogicalResourceId': event['LogicalResourceId'],
                'Data': {
                    'IpAddresses': ','.join(unique_ips),
                    'IpCount': str(len(unique_ips))
                }
            }
        
        elif request_type == 'Delete':
            # Nothing to clean up
            return {
                'Status': 'SUCCESS',
                'PhysicalResourceId': event['PhysicalResourceId'],
                'StackId': event['StackId'],
                'RequestId': event['RequestId'],
                'LogicalResourceId': event['LogicalResourceId']
            }
        
        else:
            raise ValueError(f"Unknown request type: {request_type}")
            
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}", exc_info=True)
        return {
            'Status': 'FAILED',
            'Reason': str(e),
            'PhysicalResourceId': event.get('PhysicalResourceId', 'error'),
            'StackId': event['StackId'],
            'RequestId': event['RequestId'],
            'LogicalResourceId': event['LogicalResourceId']
        }
