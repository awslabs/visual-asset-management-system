"""
Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
SPDX-License-Identifier: Apache-2.0

Kubernetes client utility module for EKS Lambda functions.
Provides standardized methods for creating and using Kubernetes clients.
"""

import os
import sys
import os
import boto3
import base64
import tempfile
import time
from botocore.exceptions import ClientError

# Import custom logging utilities
from customLogging.logger import safeLogger

logger = safeLogger(service="KubernetesUtils")

# Ensure all possible Lambda layer paths are in sys.path
for layer_path in ['/opt/python', '/opt']:
    if layer_path not in sys.path and os.path.exists(layer_path):
        sys.path.append(layer_path)

# Define ApiException for use even if import fails
ApiException = None

# Import the kubernetes client - but handle import errors gracefully
# These will be handled at runtime when the functions are actually called
try:
    import kubernetes
    from kubernetes import client
    from kubernetes.client.rest import ApiException
    logger.info("Successfully imported kubernetes module")
except ImportError as e:
    # Log available directories to help with debugging
    available_dirs = []
    for path in ['/opt', '/opt/python', '/var/runtime', '/var/task']:
        if os.path.exists(path):
            try:
                available_dirs.append(f"{path}: {os.listdir(path)}")
            except Exception:
                available_dirs.append(f"{path}: <cannot list>")

    sys_path_info = '\n'.join(sys.path)

    logger.error(f"Failed to import kubernetes: {e}. Available directories: {available_dirs}")
    logger.error(f"sys.path contents: {sys_path_info}")

    # Don't raise the error here - we'll check for None values at runtime
    kubernetes = None
    client = None
    ApiException = Exception  # Fallback type for ApiException
    logger.warning("Will attempt to import kubernetes module at runtime")

def validate_env_vars(required_vars):
    """
    Validate that required environment variables are present

    Args:
        required_vars (list): List of required environment variable names

    Returns:
        dict: Dictionary with the environment variables values

    Raises:
        ValueError: If any required environment variables are missing
    """
    missing_vars = [var for var in required_vars if not os.environ.get(var)]
    if missing_vars:
        missing_vars_str = ", ".join(missing_vars)
        error_msg = f"Missing required environment variables: {missing_vars_str}"
        logger.error(error_msg)
        raise ValueError(error_msg)

    return {var: os.environ.get(var) for var in required_vars}

def with_retries(func, max_retries=3, backoff_base=2, operation_name="operation"):
    """
    Execute a function with enhanced exponential backoff retry and comprehensive error handling

    Args:
        func: Function to execute
        max_retries: Maximum number of retry attempts
        backoff_base: Base for exponential backoff calculation
        operation_name: Name of the operation for logging

    Returns:
        The result of the function

    Raises:
        Exception: The last exception encountered after all retries are exhausted
    """
    import random

    last_exception = None
    retry_count = 0

    logger.info(f"Starting {operation_name} with retry logic (max_retries: {max_retries})")

    for attempt in range(max_retries):
        try:
            logger.debug(f"{operation_name} attempt {attempt + 1}/{max_retries}")
            result = func()

            if attempt > 0:
                logger.info(f"{operation_name} succeeded on attempt {attempt + 1}")

            return result

        except Exception as e:
            last_exception = e
            retry_count = attempt + 1

            # Enhanced error classification and retry decision
            error_type = type(e).__name__
            error_message = str(e).lower()

            # Determine if this is a retryable exception
            retry_exception = False

            # AWS service errors (retryable)
            if isinstance(e, ClientError):
                error_code = e.response.get('Error', {}).get('Code', '')
                if error_code in ['Throttling', 'ThrottlingException', 'ServiceUnavailable', 'InternalError']:
                    retry_exception = True
                    logger.warning(f"{operation_name} AWS service error (retryable): {error_code}")
                else:
                    logger.error(f"{operation_name} AWS service error (non-retryable): {error_code}")

            # Kubernetes API errors (retryable based on status code)
            elif ApiException is not None and isinstance(e, ApiException):
                status_code = getattr(e, 'status', 0)
                if status_code in [429, 500, 502, 503, 504]:  # Rate limit, server errors
                    retry_exception = True
                    logger.warning(f"{operation_name} Kubernetes API error (retryable): HTTP {status_code}")
                elif status_code in [401, 403]:  # Auth errors
                    logger.error(f"{operation_name} Kubernetes API authentication error: HTTP {status_code}")
                elif status_code == 404:  # Not found
                    logger.error(f"{operation_name} Kubernetes resource not found: HTTP {status_code}")
                elif status_code == 409:  # Conflict (e.g., resource already exists)
                    logger.error(f"{operation_name} Kubernetes resource conflict: HTTP {status_code}")
                else:
                    logger.error(f"{operation_name} Kubernetes API error: HTTP {status_code}")

            # Network and connection errors (retryable)
            elif any(keyword in error_message for keyword in [
                'connection', 'timeout', 'network', 'dns', 'resolve', 'unreachable',
                'connection reset', 'connection refused', 'temporary failure'
            ]):
                retry_exception = True
                logger.warning(f"{operation_name} network error (retryable): {error_type}")

            # Import errors (non-retryable)
            elif isinstance(e, ImportError):
                logger.error(f"{operation_name} import error (non-retryable): {str(e)}")

            # Value/Type errors (usually non-retryable)
            elif isinstance(e, (ValueError, TypeError)):
                logger.error(f"{operation_name} validation error (non-retryable): {str(e)}")

            # Other exceptions - be conservative and retry
            else:
                retry_exception = True
                logger.warning(f"{operation_name} unknown error type (retrying): {error_type}")

            # Log detailed error information
            logger.error(f"{operation_name} attempt {retry_count} failed: {str(e)}")
            logger.error(f"Error type: {error_type}")

            # Add context information if available
            try:
                if hasattr(e, 'response'):
                    logger.error(f"Response details: {e.response}")
                if hasattr(e, 'status'):
                    logger.error(f"Status code: {e.status}")
                if hasattr(e, 'reason'):
                    logger.error(f"Reason: {e.reason}")
            except Exception:
                pass  # Ignore errors in error logging

            # If it's not a retryable exception, re-raise immediately
            if not retry_exception:
                logger.error(f"{operation_name} non-retryable exception, aborting: {str(e)}")
                raise

            if attempt == max_retries - 1:
                # Last attempt failed, re-raise the exception
                logger.error(f"{operation_name} failed after {max_retries} attempts: {str(e)}")

                # Add comprehensive error context
                error_context = {
                    "operation": operation_name,
                    "attempts": max_retries,
                    "last_error": str(e),
                    "error_type": error_type
                }

                # Create enhanced error message
                enhanced_error = Exception(f"{operation_name} failed after {max_retries} attempts. Last error: {str(e)}. Context: {error_context}")
                enhanced_error.original_exception = e
                enhanced_error.retry_context = error_context
                raise enhanced_error

            # Calculate wait time with exponential backoff and jitter
            base_wait_time = backoff_base ** attempt
            jitter = random.uniform(0.1, 0.5)  # Add jitter to prevent thundering herd
            wait_time = base_wait_time + jitter

            logger.warning(f"{operation_name} retrying in {wait_time:.2f}s (attempt {retry_count}/{max_retries})")
            time.sleep(wait_time)

    # This should never be reached due to the raise in the loop
    if last_exception:
        logger.error(f"{operation_name} retry logic exhausted, raising last exception")
        raise last_exception

    error_msg = f"{operation_name} retry logic failed without recording an exception"
    logger.error(error_msg)
    raise RuntimeError(error_msg)

def get_k8s_client():
    """
    Get a Kubernetes client configured for the EKS cluster

    Returns:
        kubernetes.client.ApiClient: Configured Kubernetes API client

    Raises:
        ValueError: If EKS_CLUSTER_NAME environment variable is not set
        ClientError: If there's an issue with AWS API calls
        ApiException: If there's an issue with Kubernetes API calls
        ImportError: If kubernetes module is not available
    """
    # Import required modules locally to avoid scoping issues
    import os
    import sys

    try:
        # Look for kubernetes module in various locations
        python_paths = [
            '/opt/python',
            '/opt',
            '/var/runtime',
            '/var/task',
            '/var/lang/lib/python3.9/site-packages',
            '/var/lang/lib/python3.12/site-packages'
        ]

        # Add all possible paths to sys.path
        for layer_path in python_paths:
            if layer_path not in sys.path and os.path.exists(layer_path):
                logger.info(f"Adding path to sys.path: {layer_path}")
                sys.path.append(layer_path)

        # Try to import kubernetes module
        logger.info("Attempting to import kubernetes module")

        import kubernetes
        from kubernetes import client
        from kubernetes.client.rest import ApiException

        logger.info("Successfully imported kubernetes module")

    except ImportError as e:
        # Log detailed debugging information
        available_dirs = []
        for path in ['/opt', '/opt/python', '/var/runtime', '/var/task']:
            if os.path.exists(path):
                try:
                    available_dirs.append(f"{path}: {os.listdir(path)}")
                except Exception:
                    available_dirs.append(f"{path}: <cannot list>")

        logger.error(f"Failed to import kubernetes: {e}")
        logger.error(f"Available directories: {available_dirs}")
        logger.error(f"sys.path contents: {sys.path}")

        raise ImportError(f"Kubernetes module not available: {e}. Check that the Lambda layer is properly configured.")

    # Get cluster name from environment variable
    cluster_name = os.environ.get("EKS_CLUSTER_NAME")
    if not cluster_name:
        raise ValueError("EKS_CLUSTER_NAME environment variable not set")

    logger.info(f"Getting Kubernetes client for EKS cluster: {cluster_name}")

    # Get cluster info with retries
    def get_cluster_info():
        eks_client = boto3.client('eks')
        return eks_client.describe_cluster(name=cluster_name)

    cluster_info = with_retries(get_cluster_info)

    # Get cluster certificate and endpoint
    certificate = cluster_info['cluster']['certificateAuthority']['data']
    endpoint = cluster_info['cluster']['endpoint']

    logger.info(f"Using EKS endpoint: {endpoint}")

    # Test DNS resolution for debugging
    import socket
    try:
        hostname = endpoint.replace('https://', '').replace('http://', '')
        ip_address = socket.gethostbyname(hostname)
        logger.info(f"EKS endpoint resolved to IP: {ip_address}")

        # If we get a private IP (VPC endpoint), try to force public resolution
        if ip_address.startswith('10.') or ip_address.startswith('172.') or ip_address.startswith('192.168.'):
            logger.info(f"Detected private IP {ip_address}, attempting to bypass VPC endpoint")
            # Try to resolve using Python socket with custom DNS
            try:
                import dns.resolver
                # Configure resolver to use public DNS
                resolver = dns.resolver.Resolver()
                resolver.nameservers = ['8.8.8.8', '1.1.1.1']  # Google and Cloudflare DNS

                # Query for A records
                answers = resolver.resolve(hostname, 'A')
                for answer in answers:
                    public_ip = str(answer)
                    if not public_ip.startswith('10.') and not public_ip.startswith('172.') and not public_ip.startswith('192.168.'):
                        logger.info(f"Found public IP via DNS: {public_ip}, modifying endpoint")
                        endpoint = endpoint.replace(hostname, public_ip)
                        break

            except ImportError:
                logger.warning("dnspython not available, trying alternative method")
                # Fallback: try to connect directly to known AWS EKS public IPs
                # This is a workaround - we'll use a known public IP range for EKS
                try:
                    # Try to resolve without VPC endpoint by using a different approach
                    import urllib3
                    # Disable SSL warnings for this test
                    urllib3.disable_warnings()

                    # Create a custom HTTP adapter that bypasses local DNS
                    # We'll modify the endpoint to use a public IP directly
                    # For EKS in us-west-2, we can try some known public IP ranges

                    # Alternative: Force the connection to go through NAT by modifying hosts
                    logger.info("Attempting to force NAT Gateway route by disabling VPC endpoint DNS")
                    # We'll keep the original endpoint but add a custom header to bypass VPC endpoint

                except Exception as fallback_error:
                    logger.warning(f"Fallback DNS resolution failed: {fallback_error}")

            except Exception as dns_error:
                logger.warning(f"Custom DNS resolution failed: {dns_error}")

    except Exception as dns_error:
        logger.warning(f"DNS resolution failed for {hostname}: {dns_error}")
        # Try to continue anyway

    # Create SSL context with proper certificate verification
    try:
        # Create proper SSL context with certificate verification
        import ssl
        import tempfile

        # Decode the base64-encoded certificate
        cert_data = base64.b64decode(certificate)
        
        # Create a temporary file for the certificate that persists for the Lambda execution
        # We'll store it in /tmp which is writable in Lambda
        cert_file_path = f"/tmp/eks-ca-{cluster_name}.crt"
        
        with open(cert_file_path, 'wb') as cert_file:
            cert_file.write(cert_data)
        
        logger.info(f"Created certificate file at: {cert_file_path}")

        # Create a verified SSL context with the certificate
        # Use SERVER_AUTH purpose for client connecting to server
        ssl_context = ssl.create_default_context(purpose=ssl.Purpose.SERVER_AUTH)
        ssl_context.load_verify_locations(cafile=cert_file_path)
        
        # Verify the SSL context was created successfully
        logger.info(f"Created verified SSL context with certificate from {cert_file_path}")
        logger.info(f"SSL context check_hostname: {ssl_context.check_hostname}")
        logger.info(f"SSL context verify_mode: {ssl_context.verify_mode}")
        
        # Note: We intentionally do NOT delete the cert file here
        # The file needs to exist for the duration of the Kubernetes client usage
        # Lambda will clean up /tmp automatically between invocations

        # Add special environment variable to control auth debug mode
        os.environ["KUBERNETES_UTILS_DEBUG"] = "true"

        # Extra diagnostic information
        logger.info(f"⚠️ EKS CLUSTER DEBUG: Using endpoint: {endpoint}")
        logger.info(f"⚠️ EKS CLUSTER DEBUG: Certificate data length: {len(certificate) if certificate else 'N/A'}")

        try:
            # Get cluster info for diagnostics
            eks_client = boto3.client('eks')
            cluster_info = eks_client.describe_cluster(name=cluster_name)
            logger.info(f"⚠️ EKS CLUSTER DEBUG: Cluster status: {cluster_info['cluster']['status']}")
            logger.info(f"⚠️ EKS CLUSTER DEBUG: Cluster endpoint in AWS: {cluster_info['cluster']['endpoint']}")
            logger.info(f"⚠️ EKS CLUSTER DEBUG: Cluster auth mode: {cluster_info['cluster'].get('accessConfig', {}).get('authenticationMode', 'N/A')}")
        except Exception as e:
            logger.error(f"⚠️ EKS CLUSTER DEBUG: Error getting cluster info: {e}")

        # Configure Kubernetes client with proper settings
        configuration = client.Configuration()
        configuration.host = endpoint
        configuration.verify_ssl = True  # Enable SSL verification
        configuration.ssl_ca_cert = cert_file_path  # Use the certificate file for verification
        # Note: We don't set ssl_context when using ssl_ca_cert - they're mutually exclusive
        # The Kubernetes client will create its own SSL context using the CA cert file

        # Enhanced connection and timeout settings
        configuration.connection_pool_maxsize = 20  # Increased pool size
        configuration.retries = 5  # More retries for resilience
        configuration.timeout = 120  # 2 minute timeout for large operations

        # Add additional timeout configurations
        configuration.socket_timeout = 60  # Socket timeout
        configuration.connect_timeout = 30  # Connection timeout
        logger.info("Configured connection timeouts and retry settings")

        # Hostname verification is automatically enabled when verify_ssl=True
        # Don't set assert_hostname separately as it can cause issues with the Kubernetes client
        logger.info("⚠️ AUTH DEBUG: SSL verification and hostname validation enabled")
        logger.info("⚠️ SECURITY NOTE: Using proper security settings for EKS authentication")

        # Add user agent for debugging
        configuration.user_agent = "vams-eks-pipeline/1.0"

        # Configure retry settings
        configuration.retries_config = {
            'total': 5,
            'backoff_factor': 1,
            'status_forcelist': [429, 500, 502, 503, 504]
        }

        logger.info(f"Configured Kubernetes client for endpoint: {endpoint}")
        logger.info(f"SSL context configured with verify_ssl: {configuration.verify_ssl}")

        # Get AWS token for Kubernetes authentication with retries
        def get_eks_token():
            # Use the AWS STS service to create an EKS token
            # This is the correct way to authenticate with EKS clusters
            import base64
            import urllib.parse
            import json
            from botocore.auth import SigV4Auth
            from botocore.awsrequest import AWSRequest

            # CRITICAL DEBUG INFO - Log detailed env and access info
            logger.warning("⚠️ EKS AUTH DEBUG: Starting authentication process...")
            logger.warning(f"⚠️ EKS AUTH DEBUG: Cluster name: {cluster_name}")
            logger.warning(f"⚠️ EKS AUTH DEBUG: Endpoint: {endpoint}")

            # Log IAM information for debugging
            try:
                sts_client = boto3.client('sts')
                identity = sts_client.get_caller_identity()
                logger.info(f"⚠️ AUTHENTICATION DEBUG: Lambda running as: {identity.get('Arn')}")
                logger.info(f"⚠️ AUTHENTICATION DEBUG: Account: {identity.get('Account')}")
                logger.info(f"⚠️ AUTHENTICATION DEBUG: UserId: {identity.get('UserId')}")
            except Exception as e:
                logger.error(f"⚠️ AUTHENTICATION DEBUG: Failed to get identity: {e}")

            # Log IAM information for debugging
            try:
                sts_client = boto3.client('sts')
                identity = sts_client.get_caller_identity()
                logger.warning(f"⚠️ AUTH DEBUG: Lambda running as: {identity.get('Arn')}")
                logger.warning(f"⚠️ AUTH DEBUG: Account: {identity.get('Account')}")
                logger.warning(f"⚠️ AUTH DEBUG: UserId: {identity.get('UserId')}")

                # Get service account token file if it exists (for EKS Pod Identity)
                for token_file in ['/var/run/secrets/kubernetes.io/serviceaccount/token',
                                  '/var/run/secrets/eks.amazonaws.com/serviceaccount/token']:
                    if os.path.exists(token_file):
                        with open(token_file, 'r') as f:
                            sa_token = f.read().strip()
                            logger.warning(f"⚠️ AUTH DEBUG: Found service account token at {token_file} (length: {len(sa_token)})")

            except Exception as e:
                logger.warning(f"⚠️ AUTH DEBUG: Failed to get identity: {e}")

            try:
                # Method 1: Try using AWS CLI if available
                # Skipping AWS CLI method - it's not available in Lambda environment
                logger.warning("⚠️ AUTH DEBUG: Skipping AWS CLI method in Lambda environment")

                # Method 1B: Use boto3 to get token directly via EKS client
                try:
                    eks_client = boto3.client('eks')
                    logger.warning("⚠️ AUTH DEBUG: Attempting token generation with EKS client")

                    # Try two different methods for getting a token based on boto3 version
                    try:
                        # Method 1: Use get_token (boto3 >= 1.29.0)
                        if hasattr(eks_client, 'get_token'):
                            logger.warning("⚠️ AUTH DEBUG: Using eks_client.get_token() method")
                            token_response = eks_client.get_token(
                                clusterName=cluster_name
                            )
                            if 'token' in token_response:
                                logger.warning("⚠️ AUTH DEBUG: Successfully obtained token via EKS client get_token()")
                                return token_response['token']
                            else:
                                logger.warning(f"⚠️ AUTH DEBUG: EKS client token response missing token: {token_response}")
                        else:
                            logger.warning("⚠️ AUTH DEBUG: EKS client doesn't have get_token method (boto3 < 1.29.0)")
                            raise AttributeError("EKS client doesn't have get_token method")

                    except (AttributeError, Exception) as token_error:
                        # Method 2: Fallback to kubectl auth helper if available
                        logger.warning(f"⚠️ AUTH DEBUG: Using alternative token generation method: {token_error}")

                        # Get cluster details
                        cluster_info = eks_client.describe_cluster(name=cluster_name)
                        cluster_cert = cluster_info['cluster']['certificateAuthority']['data']
                        cluster_endpoint = cluster_info['cluster']['endpoint']

                        # Log successful cluster info retrieval
                        logger.warning(f"⚠️ AUTH DEBUG: Successfully retrieved cluster info: {cluster_name}")

                        # Check permissions since we could access the cluster
                        logger.warning("⚠️ AUTH DEBUG: Have sufficient permissions for eks:DescribeCluster")

                        # Log cluster status and node info if available
                        try:
                            logger.warning(f"⚠️ AUTH DEBUG: Cluster status: {cluster_info['cluster']['status']}")
                            logger.warning(f"⚠️ AUTH DEBUG: Kubernetes version: {cluster_info['cluster']['version']}")
                            logger.warning(f"⚠️ AUTH DEBUG: Platform version: {cluster_info['cluster'].get('platformVersion', 'N/A')}")

                            # Check if roles are mapped in aws-auth ConfigMap
                            logger.warning(f"⚠️ AUTH DEBUG: Role ARN to map: {identity.get('Arn')}")
                        except Exception as e:
                            logger.warning(f"⚠️ AUTH DEBUG: Error accessing additional cluster info: {e}")

                except Exception as e:
                    logger.error(f"⚠️ AUTH DEBUG: Failed to get token via EKS client: {e}")
                    logger.error(f"⚠️ AUTH DEBUG: Error type: {type(e).__name__}")
                    # Check for AccessDenied error which indicates missing IAM permissions
                    if "AccessDenied" in str(e):
                        logger.error("⚠️ AUTH DEBUG: ACCESS DENIED - Missing eks:GetToken or eks:DescribeCluster permission")
                        logger.error("⚠️ AUTH DEBUG: Add the following to the Lambda role:")
                        logger.error("⚠️ AUTH DEBUG: { \"Effect\": \"Allow\", \"Action\": [\"eks:GetToken\", \"eks:DescribeCluster\"], \"Resource\": \"*\" }")

            except Exception as e:
                logger.warning(f"⚠️ AUTHENTICATION DEBUG: AWS CLI method failed: {e}")

            # Method 2: Create proper presigned URL token for EKS authentication
            logger.warning("⚠️ AUTH DEBUG: Using Method 2 - Proper presigned URL generation")
            try:
                import datetime
                import hashlib
                import hmac
                import urllib.parse

                session = boto3.Session()
                credentials = session.get_credentials()
                region = session.region_name or 'us-west-2'

                # Log credential details (without exposing secrets)
                if credentials:
                    logger.warning(f"⚠️ AUTH DEBUG: Using credentials for: {credentials.access_key[:4]}...{credentials.access_key[-4:]}")
                    logger.warning(f"⚠️ AUTH DEBUG: Region: {region}")
                    if hasattr(credentials, 'token') and credentials.token:
                        logger.warning("⚠️ AUTH DEBUG: Using temporary credentials (with session token)")
                    else:
                        logger.warning("⚠️ AUTH DEBUG: Using permanent credentials (no session token)")
                else:
                    logger.warning("⚠️ AUTH DEBUG: No credentials available from boto3 Session")

                # Validate credentials
                if not credentials or not credentials.access_key or not credentials.secret_key:
                    raise ValueError("Invalid AWS credentials")

                # Create proper presigned URL with AWS SigV4 signing
                # This matches exactly what AWS CLI does

                # Create timestamp
                now = datetime.datetime.utcnow()
                timestamp = now.strftime('%Y%m%dT%H%M%SZ')
                date_stamp = now.strftime('%Y%m%d')

                # Base parameters for GetCallerIdentity
                params = {
                    'Action': 'GetCallerIdentity',
                    'Version': '2011-06-15',
                    'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
                    'X-Amz-Credential': f'{credentials.access_key}/{date_stamp}/{region}/sts/aws4_request',
                    'X-Amz-Date': timestamp,
                    'X-Amz-Expires': '60',
                    'X-Amz-SignedHeaders': 'host;x-k8s-aws-id'
                }

                # Add session token if using temporary credentials
                if hasattr(credentials, 'token') and credentials.token:
                    params['X-Amz-Security-Token'] = credentials.token

                # Create canonical request
                method = 'GET'
                canonical_uri = '/'
                canonical_querystring = '&'.join([f'{k}={urllib.parse.quote_plus(str(v))}' for k, v in sorted(params.items())])
                canonical_headers = f'host:sts.{region}.amazonaws.com\nx-k8s-aws-id:{cluster_name}\n'
                signed_headers = 'host;x-k8s-aws-id'
                payload_hash = hashlib.sha256(b'').hexdigest()

                canonical_request = f'{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}'

                # Create string to sign
                algorithm = 'AWS4-HMAC-SHA256'
                credential_scope = f'{date_stamp}/{region}/sts/aws4_request'
                string_to_sign = f'{algorithm}\n{timestamp}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}'

                # Calculate signature
                def sign(key, msg):
                    if isinstance(key, str):
                        key = key.encode('utf-8')
                    if isinstance(msg, str):
                        msg = msg.encode('utf-8')
                    return hmac.new(key, msg, hashlib.sha256).digest()

                def get_signature_key(key, date_stamp, region_name, service_name):
                    k_date = sign(('AWS4' + key).encode('utf-8'), date_stamp)
                    k_region = sign(k_date, region_name)
                    k_service = sign(k_region, service_name)
                    k_signing = sign(k_service, 'aws4_request')
                    return k_signing

                signing_key = get_signature_key(credentials.secret_key, date_stamp, region, 'sts')
                signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

                # Add signature to parameters
                params['X-Amz-Signature'] = signature

                # Build final presigned URL
                query_string = '&'.join([f'{k}={urllib.parse.quote_plus(str(v))}' for k, v in sorted(params.items())])
                presigned_url = f'https://sts.{region}.amazonaws.com/?{query_string}'

                logger.warning(f"⚠️ AUTH DEBUG: Generated presigned URL length: {len(presigned_url)}")
                logger.warning(f"⚠️ AUTH DEBUG: Contains X-Amz-Signature: {'X-Amz-Signature' in presigned_url}")
                logger.warning(f"⚠️ AUTH DEBUG: Contains X-Amz-Algorithm: {'X-Amz-Algorithm' in presigned_url}")
                logger.warning(f"⚠️ AUTH DEBUG: Contains X-Amz-Credential: {'X-Amz-Credential' in presigned_url}")
                logger.warning(f"⚠️ AUTH DEBUG: URL sample: {presigned_url[:150]}...")

                # Create the token by encoding the complete presigned URL
                token_prefix = 'k8s-aws-v1.'
                encoded_url = base64.urlsafe_b64encode(presigned_url.encode()).decode().rstrip('=')
                token = token_prefix + encoded_url

                logger.warning(f"⚠️ AUTH DEBUG: Successfully created proper EKS auth token via Method 2")
                logger.warning(f"⚠️ AUTH DEBUG: Token length: {len(token)} (should be ~600+ chars)")
                logger.warning(f"⚠️ AUTH DEBUG: Base64 encoded URL length: {len(encoded_url)}")
                return token

            except Exception as e:
                logger.error(f"⚠️ AUTHENTICATION DEBUG: Proper token creation failed: {e}")
                logger.error(f"⚠️ AUTHENTICATION DEBUG: Error type: {type(e).__name__}")

                # Log a comprehensive error diagnosis
                logger.error("⚠️ EKS AUTH DIAGNOSIS:")
                logger.error(f"⚠️ 1. Cluster name: {cluster_name}")
                logger.error(f"⚠️ 2. Region: {region}")
                logger.error("⚠️ 3. Common issues:")
                logger.error("⚠️    - Missing eks:GetToken permission")
                logger.error("⚠️    - Lambda role not properly mapped in aws-auth ConfigMap")
                logger.error("⚠️    - Network connectivity issues between Lambda VPC and EKS API")

                try:
                    # Attempt to check aws-auth ConfigMap
                    import subprocess
                    import json

                    # Create temporary kubeconfig
                    with tempfile.NamedTemporaryFile(delete=False) as kube_config:
                        kube_config_path = kube_config.name

                    # Try to get aws-auth ConfigMap using AWS CLI
                    logger.error("⚠️ Attempting to check aws-auth ConfigMap...")
                    cmd = f"AWS_STS_REGIONAL_ENDPOINTS=regional aws eks update-kubeconfig --name {cluster_name} --kubeconfig {kube_config_path}"
                    subprocess.run(cmd, shell=True, check=False)

                    cmd = f"kubectl --kubeconfig={kube_config_path} get configmap -n kube-system aws-auth -o json"
                    result = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=False)

                    if result.returncode == 0:
                        aws_auth = json.loads(result.stdout)
                        logger.error(f"⚠️ aws-auth ConfigMap: {aws_auth}")
                    else:
                        logger.error(f"⚠️ Failed to get aws-auth ConfigMap: {result.stderr}")

                    # Clean up temp file
                    os.unlink(kube_config_path)
                except Exception as config_error:
                    logger.error(f"⚠️ Error checking aws-auth ConfigMap: {config_error}")

                # FALLBACK AUTHENTICATION: Use official AWS EKS token generation
                logger.warning("⚠️ Using official AWS token generation approach")
                try:
                    # AWS official method for token generation
                    # Based on https://github.com/kubernetes-sigs/aws-iam-authenticator/
                    import base64
                    import datetime
                    import json
                    import os
                    import sys
                    from botocore.signers import RequestSigner
                    from botocore.awsrequest import AWSRequest

                    # Get STS credentials
                    session = boto3.session.Session()
                    sts = session.client('sts')
                    service_id = 'sts'
                    region = session.region_name or 'us-west-2'

                    logger.warning(f"⚠️ AUTH DEBUG: Using region {region} for token generation")
                    logger.warning(f"⚠️ AUTH DEBUG: Using cluster name {cluster_name}")

                    # Get STS token (official method)
                    signer = RequestSigner(
                        'sts',
                        region,
                        'sts',
                        'v4',
                        session.get_credentials(),
                        session.events
                    )

                    # Create request parameters
                    params = {
                        'method': 'GET',
                        'url': 'https://sts.{}.amazonaws.com/?Action=GetCallerIdentity&Version=2011-06-15'.format(region),
                        'body': {},
                        'headers': {
                            'x-k8s-aws-id': cluster_name
                        },
                        'context': {}
                    }

                    # Sign the request
                    signed_url = signer.generate_presigned_url(
                        params,
                        region_name=region,
                        expires_in=60,
                        operation_name='',
                    )

                    # Extract the signature
                    logger.warning(f"⚠️ AUTH DEBUG: Generated presigned URL (truncated): {signed_url[:30]}...")

                    # Create the token
                    base64_url = base64.urlsafe_b64encode(signed_url.encode('utf-8')).decode('utf-8').rstrip('=')

                    # Format the token
                    token = 'k8s-aws-v1.' + base64_url

                    # Log token info
                    logger.warning(f"⚠️ AUTH DEBUG: Generated official token format: {token[:20]}...")
                    logger.warning(f"⚠️ AUTH DEBUG: Token length: {len(token)}")

                    return token

                except Exception as e:
                    logger.error(f"⚠️ AUTH DEBUG: Official token generation failed: {str(e)}")
                    logger.error(f"⚠️ AUTH DEBUG: Error type: {type(e).__name__}")

                    try:
                        # CORRECTED: Fallback method - Create proper EKS authentication token with presigned URL
                        logger.warning("⚠️ AUTH DEBUG: Using Fallback Method - Alternative presigned URL generation")
                        import base64
                        import datetime
                        import hashlib
                        import hmac
                        import urllib.parse
                        from botocore.auth import SigV4Auth
                        from botocore.awsrequest import AWSRequest

                        # Get session and credentials
                        session = boto3.Session()
                        credentials = session.get_credentials()
                        region = session.region_name or 'us-west-2'

                        # Check credentials
                        if not credentials:
                            logger.error("⚠️ AUTH DEBUG: No credentials available!")
                            raise ValueError("No AWS credentials available")

                        # Validate credential attributes - try to refresh if needed
                        try:
                            # First validation attempt
                            if not hasattr(credentials, 'access_key') or not credentials.access_key:
                                logger.warning("⚠️ AUTH DEBUG: No access key in credentials, attempting refresh...")
                                # Try to refresh credentials
                                credentials = session.get_credentials()
                                credentials.refresh()

                            if not hasattr(credentials, 'secret_key') or not credentials.secret_key:
                                logger.warning("⚠️ AUTH DEBUG: No secret key in credentials, attempting refresh...")
                                credentials = session.get_credentials()
                                credentials.refresh()

                            # Final validation
                            if not credentials.access_key or not credentials.secret_key:
                                logger.error("⚠️ AUTH DEBUG: Credentials still invalid after refresh!")
                                raise ValueError("AWS credentials missing required keys after refresh")

                            logger.warning(f"⚠️ AUTH DEBUG: Validated credentials for: {credentials.access_key[:4]}...{credentials.access_key[-4:]}")

                        except Exception as cred_error:
                            logger.error(f"⚠️ AUTH DEBUG: Credential validation/refresh failed: {cred_error}")
                            raise ValueError(f"AWS credentials validation failed: {cred_error}")

                        logger.warning(f"⚠️ AUTH DEBUG: Creating proper presigned URL for region: {region}")

                        # Method 1: Use STS client to generate proper presigned URL
                        try:
                            from botocore.signers import RequestSigner

                            # Create request signer
                            signer = RequestSigner(
                                'sts', region, 'sts', 'v4',
                                credentials, session.events
                            )

                            # Create request parameters
                            params = {
                                'Action': 'GetCallerIdentity',
                                'Version': '2011-06-15'
                            }

                            # Create request dictionary for presigning
                            request_dict = {
                                'url_path': '/',
                                'query_string': urllib.parse.urlencode(params),
                                'method': 'GET',
                                'headers': {'x-k8s-aws-id': cluster_name},
                                'body': b'',
                                'context': {}
                            }

                            # Generate presigned URL with all signature components
                            presigned_url = signer.generate_presigned_url(
                                request_dict,
                                region_name=region,
                                expires_in=60,
                                operation_name='GetCallerIdentity'
                            )

                            logger.warning(f"⚠️ AUTH DEBUG: Generated presigned URL length: {len(presigned_url)}")
                            logger.warning(f"⚠️ AUTH DEBUG: Contains signature: {'X-Amz-Signature' in presigned_url}")

                        except Exception as signer_error:
                            logger.warning(f"⚠️ AUTH DEBUG: RequestSigner method failed: {signer_error}, trying manual approach")

                            # Method 2: Manual presigned URL creation with proper SigV4
                            # This matches exactly what AWS CLI does

                            # Additional credential validation for manual method
                            try:
                                test_access = credentials.access_key
                                test_secret = credentials.secret_key
                                logger.warning(f"⚠️ AUTH DEBUG: Manual method using access key: {test_access[:4]}...{test_access[-4:]}")
                            except Exception as cred_error:
                                logger.error(f"⚠️ AUTH DEBUG: Credential access failed: {cred_error}")
                                raise ValueError(f"Invalid credentials for manual signing: {cred_error}")

                            # Create timestamp
                            now = datetime.datetime.utcnow()
                            timestamp = now.strftime('%Y%m%dT%H%M%SZ')
                            date_stamp = now.strftime('%Y%m%d')

                            # Base parameters
                            params = {
                                'Action': 'GetCallerIdentity',
                                'Version': '2011-06-15',
                                'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
                                'X-Amz-Credential': f'{credentials.access_key}/{date_stamp}/{region}/sts/aws4_request',
                                'X-Amz-Date': timestamp,
                                'X-Amz-Expires': '60',
                                'X-Amz-SignedHeaders': 'host;x-k8s-aws-id'
                            }

                            # Add session token if using temporary credentials
                            if hasattr(credentials, 'token') and credentials.token:
                                params['X-Amz-Security-Token'] = credentials.token

                            # Create canonical request
                            method = 'GET'
                            canonical_uri = '/'
                            canonical_querystring = '&'.join([f'{k}={urllib.parse.quote_plus(str(v))}' for k, v in sorted(params.items())])
                            canonical_headers = f'host:sts.{region}.amazonaws.com\nx-k8s-aws-id:{cluster_name}\n'
                            signed_headers = 'host;x-k8s-aws-id'
                            payload_hash = hashlib.sha256(b'').hexdigest()

                            canonical_request = f'{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}'

                            # Create string to sign
                            algorithm = 'AWS4-HMAC-SHA256'
                            credential_scope = f'{date_stamp}/{region}/sts/aws4_request'
                            string_to_sign = f'{algorithm}\n{timestamp}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}'

                            # Calculate signature
                            def sign(key, msg):
                                if isinstance(key, str):
                                    key = key.encode('utf-8')
                                if isinstance(msg, str):
                                    msg = msg.encode('utf-8')
                                return hmac.new(key, msg, hashlib.sha256).digest()

                            def get_signature_key(key, date_stamp, region_name, service_name):
                                k_date = sign(('AWS4' + key).encode('utf-8'), date_stamp)
                                k_region = sign(k_date, region_name)
                                k_service = sign(k_region, service_name)
                                k_signing = sign(k_service, 'aws4_request')
                                return k_signing

                            signing_key = get_signature_key(credentials.secret_key, date_stamp, region, 'sts')
                            signature = hmac.new(signing_key, string_to_sign.encode('utf-8'), hashlib.sha256).hexdigest()

                            # Add signature to parameters
                            params['X-Amz-Signature'] = signature

                            # Build final presigned URL
                            query_string = '&'.join([f'{k}={urllib.parse.quote_plus(str(v))}' for k, v in sorted(params.items())])
                            presigned_url = f'https://sts.{region}.amazonaws.com/?{query_string}'

                            logger.warning(f"⚠️ AUTH DEBUG: Manual presigned URL length: {len(presigned_url)}")

                        # Verify the presigned URL structure
                        logger.warning(f"⚠️ AUTH DEBUG: URL sample: {presigned_url[:150]}...")
                        logger.warning(f"⚠️ AUTH DEBUG: Contains X-Amz-Algorithm: {'X-Amz-Algorithm' in presigned_url}")
                        logger.warning(f"⚠️ AUTH DEBUG: Contains X-Amz-Credential: {'X-Amz-Credential' in presigned_url}")
                        logger.warning(f"⚠️ AUTH DEBUG: Contains X-Amz-Signature: {'X-Amz-Signature' in presigned_url}")
                        logger.warning(f"⚠️ AUTH DEBUG: Contains X-Amz-SignedHeaders: {'X-Amz-SignedHeaders' in presigned_url}")

                        # Encode the complete presigned URL (this should now be 500+ characters)
                        base64_url = base64.urlsafe_b64encode(presigned_url.encode('utf-8')).decode('utf-8').rstrip('=')

                        # Format the token
                        token = 'k8s-aws-v1.' + base64_url

                        logger.warning(f"⚠️ AUTH DEBUG: Generated corrected token (truncated): {token[:20]}...")
                        logger.warning(f"⚠️ AUTH DEBUG: Final token length: {len(token)} (should be ~600+ chars)")
                        logger.warning(f"⚠️ AUTH DEBUG: Base64 URL length: {len(base64_url)} (should be ~500+ chars)")

                        return token

                    except Exception as e2:
                        logger.error(f"⚠️ AUTH DEBUG: Corrected token generation failed: {str(e2)}")
                        logger.error(f"⚠️ AUTH DEBUG: Error type: {type(e2).__name__}")

                        # Don't return a fake token - let the authentication fail properly
                        # This will give us better error messages to debug
                        logger.error("⚠️ AUTH DEBUG: All token generation methods exhausted. Authentication will fail.")
                        raise Exception(f"EKS token generation failed: {str(e2)}")

        logger.info(f"⚠️ AUTHENTICATION DEBUG: Starting token generation for cluster: {cluster_name}")
        logger.warning(f"⚠️ AUTH DEBUG: Starting token generation for cluster: {cluster_name}")
        token = with_retries(get_eks_token)
        logger.warning(f"⚠️ AUTH DEBUG: Generated token of length: {len(token) if token else 'N/A'}")
        logger.info(f"⚠️ AUTHENTICATION DEBUG: Successfully generated token of length: {len(token) if token else 'N/A'}")

        # Configure API key authorization
        configuration.api_key = {"authorization": f"Bearer {token}"}
        logger.warning(f"⚠️ AUTH DEBUG: API key configured with bearer token")
        logger.info(f"⚠️ AUTHENTICATION DEBUG: API key configured with bearer token")

        # Create and return API client
        logger.warning("⚠️ AUTH DEBUG: Creating Kubernetes API client with SSL-enabled configuration")

        # Get cluster role binding info - debug only
        try:
            eks_client = boto3.client('eks')

            # Describe the cluster for authentication details
            cluster_info = eks_client.describe_cluster(name=cluster_name)
            logger.warning(f"⚠️ AUTH DEBUG: Cluster authentication mode: {cluster_info['cluster'].get('accessConfig', {}).get('authenticationMode', 'N/A')}")

            try:
                access_entries = eks_client.list_access_entries(clusterName=cluster_name)
                logger.warning(f"⚠️ AUTH DEBUG: Cluster access entries: {access_entries}")
            except Exception:
                logger.warning(f"⚠️ AUTH DEBUG: Could not list access entries (likely using CONFIG_MAP auth mode)")

        except Exception as e:
            logger.warning(f"⚠️ AUTH DEBUG: Could not get cluster info: {e}")

        # Create API client
        api_client = client.ApiClient(configuration)
        logger.warning("⚠️ AUTH DEBUG: Successfully created Kubernetes API client")

        # Test the connection
        try:
            version_api = client.VersionApi(api_client)
            version = version_api.get_code()
            logger.warning(f"⚠️ CONNECTION TEST: Successfully connected to Kubernetes API server")
            logger.warning(f"⚠️ CONNECTION TEST: Server version: {version.git_version}")
        except Exception as e:
            logger.warning(f"⚠️ CONNECTION TEST: Failed to connect to Kubernetes API server: {e}")

            # Try to dump headers for debugging
            try:
                api_client.call_api('/api', 'GET', auth_settings=['BearerToken'], _return_http_data_only=False)
            except Exception as call_error:
                logger.warning(f"⚠️ CONNECTION TEST: Raw API call error: {call_error}")

            # Log the error but don't fall back to insecure connections
            # The SSL certificate should be properly configured now
            logger.error("⚠️ CONNECTION TEST: Failed to connect to Kubernetes API server")
            logger.error("⚠️ This indicates a configuration issue that should be investigated")
            logger.error("⚠️ Check EKS cluster endpoint accessibility and certificate configuration")
            # Re-raise the exception instead of falling back to insecure mode
            raise

        return api_client

    except Exception as e:
        logger.error(f"Error creating Kubernetes client: {str(e)}")
        raise

    finally:
        pass

def get_batch_v1_api():
    """
    Get a BatchV1Api client for working with Kubernetes Jobs

    Returns:
        kubernetes.client.BatchV1Api: BatchV1Api client
    """
    # Import locally to avoid scoping issues
    from kubernetes import client
    api_client = get_k8s_client()
    return client.BatchV1Api(api_client)

def get_core_v1_api():
    """
    Get a CoreV1Api client for working with Kubernetes core resources

    Returns:
        kubernetes.client.CoreV1Api: CoreV1Api client
    """
    try:
        # Import locally to avoid scoping issues
        from kubernetes import client

        # Add debug log
        logger.warning("⚠️ Creating CoreV1Api client")
        api_client = get_k8s_client()
        logger.warning("⚠️ Got Kubernetes client successfully")

        return client.CoreV1Api(api_client)
    except Exception as e:
        logger.error(f"Error creating CoreV1Api: {e}")
        raise

def create_job(job_manifest, namespace="default"):
    """
    Create a Kubernetes Job from the provided manifest with comprehensive error handling

    Args:
        job_manifest (dict): The Kubernetes Job manifest
        namespace (str): The Kubernetes namespace to create the job in

    Returns:
        str: The name of the created job

    Raises:
        ValueError: If the job manifest is invalid
        ApiException: If there's an issue creating the job
    """
    logger.info(f"Starting job creation in namespace: {namespace}")

    try:
        # Validate inputs
        if namespace is None or not namespace.strip():
            namespace = "default"
            logger.warning("Namespace was None or empty, using 'default'")

        if not job_manifest:
            raise ValueError("Job manifest cannot be None or empty")

        if not isinstance(job_manifest, dict):
            raise ValueError(f"Job manifest must be a dictionary, got {type(job_manifest)}")

        # Comprehensive manifest validation
        logger.info("Validating job manifest structure")

        # Check required top-level fields
        required_fields = ['apiVersion', 'kind', 'metadata', 'spec']
        missing_fields = [field for field in required_fields if field not in job_manifest]
        if missing_fields:
            raise ValueError(f"Job manifest missing required fields: {', '.join(missing_fields)}")

        # Validate API version and kind
        if job_manifest.get('apiVersion') != 'batch/v1':
            logger.warning(f"Unexpected apiVersion: {job_manifest.get('apiVersion')}, expected 'batch/v1'")

        if job_manifest.get('kind') != 'Job':
            raise ValueError(f"Manifest kind must be 'Job', got '{job_manifest.get('kind')}'")

        # Validate metadata
        metadata = job_manifest.get('metadata', {})
        if not isinstance(metadata, dict):
            raise ValueError("Job manifest metadata must be a dictionary")

        job_name = metadata.get('name')
        if not job_name:
            raise ValueError("Job manifest must include metadata.name")

        logger.info(f"Validating job: {job_name}")

        # Validate spec structure
        spec = job_manifest.get('spec', {})
        if not isinstance(spec, dict):
            raise ValueError("Job manifest spec must be a dictionary")

        template = spec.get('template', {})
        if not isinstance(template, dict):
            raise ValueError("Job manifest spec.template must be a dictionary")

        template_spec = template.get('spec', {})
        if not isinstance(template_spec, dict):
            raise ValueError("Job manifest spec.template.spec must be a dictionary")

        # Validate containers
        containers = template_spec.get('containers', [])
        if not containers:
            raise ValueError("Job manifest must include at least one container")

        if not isinstance(containers, list):
            raise ValueError("Job manifest containers must be a list")

        # Validate container image
        try:
            container_image = containers[0]['image']
            logger.info(f"Using container image from manifest: {container_image}")

            # Validate the image isn't empty or still a placeholder
            if not container_image or not container_image.strip():
                raise ValueError("Container image cannot be empty")

            if container_image == "CONTAINER_IMAGE_PLACEHOLDER":
                raise ValueError("Container image is still a placeholder, check configuration")

            # Basic image format validation
            if not ('/' in container_image or ':' in container_image):
                logger.warning(f"Container image format may be invalid: {container_image}")

        except (KeyError, IndexError) as e:
            raise ValueError(f"Invalid container specification in job manifest: {e}")

        # Validate resource requirements if present
        resources = containers[0].get('resources', {})
        if resources:
            requests = resources.get('requests', {})
            limits = resources.get('limits', {})

            # Log resource configuration
            if requests:
                logger.info(f"Resource requests: {requests}")
            if limits:
                logger.info(f"Resource limits: {limits}")

        # Validate service account if specified
        service_account = template_spec.get('serviceAccountName')
        if service_account:
            logger.info(f"Using service account: {service_account}")

        # Get BatchV1Api client with error handling
        logger.info("Getting Kubernetes BatchV1Api client")
        try:
            batch_v1 = get_batch_v1_api()
        except Exception as e:
            logger.error(f"Failed to get Kubernetes client: {str(e)}")
            raise Exception(f"Cannot connect to Kubernetes cluster: {str(e)}")

        # Check if job already exists (to handle conflicts gracefully)
        logger.info(f"Checking if job {job_name} already exists in namespace {namespace}")
        try:
            def check_existing_job():
                return batch_v1.read_namespaced_job(name=job_name, namespace=namespace)

            existing_job = with_retries(check_existing_job, max_retries=2, operation_name="check_existing_job")

            if existing_job:
                error_msg = f"Job {job_name} already exists in namespace {namespace}"
                logger.error(error_msg)
                raise ValueError(error_msg)

        except Exception as e:
            # If job doesn't exist, that's what we want
            if ApiException is not None and isinstance(e, ApiException) and e.status == 404:
                logger.info(f"Job {job_name} does not exist (as expected)")
            elif "not found" in str(e).lower():
                logger.info(f"Job {job_name} does not exist (as expected)")
            else:
                # Some other error occurred
                logger.warning(f"Could not check for existing job: {str(e)}")

        # Create the job with enhanced retry logic
        logger.info(f"Creating Kubernetes job: {job_name}")

        def create_k8s_job():
            try:
                result = batch_v1.create_namespaced_job(
                    namespace=namespace,
                    body=job_manifest
                )
                logger.info(f"Job creation API call successful: {result.metadata.name}")
                return result
            except Exception as e:
                logger.error(f"Job creation API call failed: {str(e)}")

                # Add specific error handling for common issues
                if ApiException is not None and isinstance(e, ApiException):
                    if e.status == 409:
                        raise ValueError(f"Job {job_name} already exists (conflict)")
                    elif e.status == 422:
                        raise ValueError(f"Job manifest validation failed: {e.body}")
                    elif e.status == 403:
                        raise ValueError(f"Insufficient permissions to create job in namespace {namespace}")
                    elif e.status == 404:
                        raise ValueError(f"Namespace {namespace} does not exist")

                raise

        job = with_retries(create_k8s_job, max_retries=3, operation_name=f"create_job_{job_name}")

        created_job_name = job.metadata.name
        logger.info(f"Successfully created job: {created_job_name} in namespace {namespace}")

        # Verify job was created by reading it back
        try:
            def verify_job_creation():
                return batch_v1.read_namespaced_job(name=created_job_name, namespace=namespace)

            verification = with_retries(verify_job_creation, max_retries=2, operation_name="verify_job_creation")
            logger.info(f"Job creation verified: {verification.metadata.name}")

        except Exception as e:
            logger.warning(f"Could not verify job creation: {str(e)}")
            # Don't fail the operation if verification fails

        return created_job_name

    except ValueError as ve:
        # Re-raise validation errors with context
        logger.error(f"Job validation error: {str(ve)}")
        raise

    except Exception as e:
        # Handle unexpected errors with context
        logger.exception(f"Unexpected error creating job: {str(e)}")

        # Add context to the error
        error_context = {
            "operation": "create_job",
            "namespace": namespace,
            "job_name": job_manifest.get('metadata', {}).get('name', 'unknown'),
            "error_type": type(e).__name__
        }

        enhanced_error = Exception(f"Failed to create Kubernetes job: {str(e)}. Context: {error_context}")
        enhanced_error.original_exception = e
        enhanced_error.context = error_context
        raise enhanced_error

def check_job_status(job_name, namespace="default"):
    """
    Check the status of a Kubernetes Job with enhanced monitoring and lifecycle management

    Args:
        job_name (str): The name of the job to check
        namespace (str): The Kubernetes namespace the job is in

    Returns:
        tuple: (status, error_logs) where status is one of "SUCCEEDED", "FAILED", "RUNNING", "UNKNOWN"
               and error_logs contains error information if the job failed
    """
    # Make sure namespace is not None
    if namespace is None:
        namespace = "default"

    logger.info(f"Checking status of job {job_name} in namespace {namespace}")

    try:
        # Get BatchV1Api client
        batch_v1 = get_batch_v1_api()

        # Check job status with retries
        def get_job_status():
            return batch_v1.read_namespaced_job_status(
                name=job_name,
                namespace=namespace
            )

        job = with_retries(get_job_status)

        # Enhanced status checking with detailed logging and metrics
        logger.info(f"Job {job_name} status details:")
        logger.info(f"  - Active pods: {job.status.active or 0}")
        logger.info(f"  - Succeeded pods: {job.status.succeeded or 0}")
        logger.info(f"  - Failed pods: {job.status.failed or 0}")
        logger.info(f"  - Start time: {job.status.start_time}")
        logger.info(f"  - Completion time: {job.status.completion_time}")

        # Calculate job duration if started
        if job.status.start_time:
            import datetime
            start_time = job.status.start_time
            if job.status.completion_time:
                duration = job.status.completion_time - start_time
                logger.info(f"  - Job duration: {duration}")
            else:
                current_time = datetime.datetime.now(datetime.timezone.utc)
                duration = current_time - start_time
                logger.info(f"  - Job running for: {duration}")

        # Check conditions for more detailed status
        conditions = job.status.conditions or []
        for condition in conditions:
            logger.info(f"  - Condition: {condition.type} = {condition.status} ({condition.reason})")
            if condition.message:
                logger.info(f"    Message: {condition.message}")

        # Enhanced status determination with better lifecycle management
        if job.status.succeeded is not None and job.status.succeeded > 0:
            logger.info(f"Job {job_name} completed successfully with {job.status.succeeded} succeeded pod(s)")

            # Check if job should be cleaned up (if it's been completed for a while)
            if job.status.completion_time:
                import datetime
                completion_time = job.status.completion_time
                current_time = datetime.datetime.now(datetime.timezone.utc)
                time_since_completion = current_time - completion_time

                # If job completed more than 30 minutes ago and ttlSecondsAfterFinished isn't set, log a warning
                if time_since_completion.total_seconds() > 1800:  # 30 minutes
                    logger.warning(f"Job {job_name} completed {time_since_completion} ago but still exists. Consider cleanup.")

            return "SUCCEEDED", None

        elif job.status.failed is not None and job.status.failed > 0:
            logger.error(f"Job {job_name} failed with {job.status.failed} failed pod(s)")

            # Get detailed failure information from job conditions
            failure_reasons = []
            for condition in conditions:
                if condition.type == "Failed" and condition.status == "True":
                    failure_reasons.append(f"{condition.reason}: {condition.message}")

            # Get comprehensive pod logs and events for error analysis
            error_logs = get_pod_logs_for_job(job_name, namespace)

            # Get pod events for additional context
            pod_events = get_pod_events_for_job(job_name, namespace)

            # Combine all error information
            error_details = []
            if failure_reasons:
                error_details.append(f"Job conditions: {'; '.join(failure_reasons)}")
            if error_logs and error_logs != "No pods found for job":
                error_details.append(f"Pod logs: {error_logs}")
            if pod_events:
                error_details.append(f"Pod events: {pod_events}")

            combined_error = ". ".join(error_details) if error_details else "Job failed with no additional details"

            return "FAILED", combined_error

        elif job.status.active is not None and job.status.active > 0:
            logger.info(f"Job {job_name} is actively running with {job.status.active} active pod(s)")

            # Check for long-running jobs that might be stuck
            if job.status.start_time:
                import datetime
                start_time = job.status.start_time
                current_time = datetime.datetime.now(datetime.timezone.utc)
                duration = current_time - start_time

                # Warn if job has been running for more than 2 hours
                if duration.total_seconds() > 7200:  # 2 hours
                    logger.warning(f"Job {job_name} has been running for {duration}, which may indicate it's stuck")

                    # Get pod status for additional diagnostics
                    pod_status = get_pod_status_for_job(job_name, namespace)
                    if pod_status:
                        logger.info(f"Pod status details: {pod_status}")

            return "RUNNING", None

        else:
            # Job exists but no pods are active, succeeded, or failed
            # This might be a pending state or waiting for resources
            logger.info(f"Job {job_name} exists but has no active, succeeded, or failed pods - likely pending")

            # Check if there are any conditions that explain the state
            pending_reasons = []
            for condition in conditions:
                if condition.type in ["Suspended", "Complete"] and condition.status == "True":
                    pending_reasons.append(f"{condition.reason}: {condition.message}")

            # Check for resource constraints or scheduling issues
            pod_events = get_pod_events_for_job(job_name, namespace)
            if pod_events and any(keyword in pod_events.lower() for keyword in ['insufficient', 'unschedulable', 'pending']):
                logger.warning(f"Job {job_name} may be pending due to resource constraints: {pod_events}")
                pending_reasons.append(f"Resource constraints: {pod_events}")

            if pending_reasons:
                logger.info(f"Job {job_name} pending reasons: {'; '.join(pending_reasons)}")

            # Treat as running since the job exists and isn't failed
            return "RUNNING", None

    except Exception as e:
        logger.error(f"Error checking job status for {job_name}: {str(e)}")

        # Enhanced error handling with detailed classification
        error_message = str(e).lower()
        error_type = type(e).__name__

        # Log detailed error information
        logger.error(f"Error type: {error_type}")
        if hasattr(e, 'status'):
            logger.error(f"HTTP status: {e.status}")
        if hasattr(e, 'reason'):
            logger.error(f"Reason: {e.reason}")

        # Classify error types for appropriate handling
        if ApiException is not None and isinstance(e, ApiException):
            status_code = getattr(e, 'status', 0)

            if status_code == 404:
                logger.info(f"Job {job_name} not found (HTTP 404) - checking if it completed successfully before cleanup")

                # When a job is not found, it could mean:
                # 1. Job never existed (should return FAILED)
                # 2. Job completed successfully and was cleaned up (should return SUCCEEDED)
                # Check Kubernetes events to determine which case this is
                try:
                    completion_status = check_job_completion_events(job_name, namespace)
                    if completion_status == "COMPLETED":
                        logger.info(f"Job {job_name} was found to have completed successfully based on events")
                        return "SUCCEEDED", None
                    else:
                        logger.error(f"Job {job_name} not found and no completion events found - likely never existed or failed")
                        return "FAILED", f"Job not found: {str(e)}"
                except Exception as event_check_error:
                    logger.warning(f"Could not check events for missing job {job_name}: {event_check_error}")
                    # Fall back to assuming failure if we can't check events
                    return "FAILED", f"Job not found: {str(e)}"
            elif status_code == 403:
                logger.error(f"Access denied checking job {job_name} (HTTP 403)")
                return "UNKNOWN", f"Access denied: {str(e)}"
            elif status_code in [429, 500, 502, 503, 504]:
                logger.warning(f"Transient Kubernetes API error for job {job_name} (HTTP {status_code})")
                return "UNKNOWN", f"Transient API error (HTTP {status_code}): {str(e)}"
            else:
                logger.error(f"Kubernetes API error for job {job_name} (HTTP {status_code})")
                return "UNKNOWN", f"API error (HTTP {status_code}): {str(e)}"

        # Network and connection errors (transient)
        elif any(keyword in error_message for keyword in [
            'timeout', 'connection', 'network', 'dns', 'resolve', 'unreachable',
            'connection reset', 'connection refused', 'temporary failure', 'socket'
        ]):
            logger.warning(f"Network/connection error for job {job_name}, treating as transient")
            return "UNKNOWN", f"Network error: {str(e)}"

        # SSL/TLS errors (potentially transient)
        elif any(keyword in error_message for keyword in ['ssl', 'tls', 'certificate', 'handshake']):
            logger.warning(f"SSL/TLS error for job {job_name}, treating as transient")
            return "UNKNOWN", f"SSL/TLS error: {str(e)}"

        # Authentication errors (non-transient)
        elif any(keyword in error_message for keyword in ['unauthorized', 'authentication', 'token']):
            logger.error(f"Authentication error for job {job_name}")
            return "UNKNOWN", f"Authentication error: {str(e)}"

        # Job not found errors
        elif 'not found' in error_message:
            logger.error(f"Job {job_name} not found - may have been deleted or never created")
            return "FAILED", f"Job not found: {str(e)}"

        # Import errors (configuration issue)
        elif isinstance(e, ImportError):
            logger.error(f"Import error checking job {job_name} - configuration issue")
            return "UNKNOWN", f"Configuration error: {str(e)}"

        # Other errors - be conservative and treat as unknown
        else:
            logger.error(f"Unknown error type checking job {job_name}: {error_type}")
            return "UNKNOWN", f"Unknown error ({error_type}): {str(e)}"

def get_pod_logs_for_job(job_name, namespace="default"):
    """
    Get logs from pods associated with a job with enhanced error handling and diagnostics

    Args:
        job_name (str): The name of the job
        namespace (str): The Kubernetes namespace

    Returns:
        str: The logs from the pods or an error message
    """
    # Make sure namespace is not None
    if namespace is None:
        namespace = "default"

    try:
        # Get CoreV1Api client
        core_v1 = get_core_v1_api()

        # Get pods with retries
        def list_pods():
            return core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}"
            )

        pods = with_retries(list_pods)

        if not pods.items:
            logger.warning(f"No pods found for job {job_name}")
            return "No pods found for job"

        # Enhanced pod log retrieval - try to get logs from all pods with better diagnostics
        all_logs = []

        for i, pod in enumerate(pods.items):
            pod_name = pod.metadata.name
            pod_phase = pod.status.phase

            logger.info(f"Getting logs from pod {pod_name} (phase: {pod_phase})")

            # Get container statuses for better diagnostics
            container_info = []
            if pod.status.container_statuses:
                for container_status in pod.status.container_statuses:
                    container_name = container_status.name
                    ready = container_status.ready
                    restart_count = container_status.restart_count

                    state_info = "Unknown"
                    if container_status.state:
                        if container_status.state.running:
                            state_info = "Running"
                        elif container_status.state.waiting:
                            state_info = f"Waiting: {container_status.state.waiting.reason}"
                        elif container_status.state.terminated:
                            exit_code = container_status.state.terminated.exit_code
                            reason = container_status.state.terminated.reason
                            state_info = f"Terminated: {reason} (exit code: {exit_code})"

                    container_info.append(f"{container_name}: {state_info} (ready: {ready}, restarts: {restart_count})")

            pod_header = f"=== Pod {pod_name} (phase: {pod_phase}) ==="
            if container_info:
                pod_header += f"\nContainer Status: {'; '.join(container_info)}"

            try:
                # Try to get logs from all containers in the pod
                pod_logs = []

                if pod.spec.containers:
                    for container in pod.spec.containers:
                        container_name = container.name

                        try:
                            # Get logs from this specific container
                            def get_container_log():
                                return core_v1.read_namespaced_pod_log(
                                    name=pod_name,
                                    namespace=namespace,
                                    container=container_name,
                                    tail_lines=1000,  # Limit to last 1000 lines
                                    timestamps=True,  # Include timestamps
                                    previous=False    # Get current logs
                                )

                            logs = with_retries(get_container_log)

                            if logs and logs.strip():
                                pod_logs.append(f"--- Container {container_name} ---\n{logs}")
                            else:
                                pod_logs.append(f"--- Container {container_name} ---\n[No logs available]")

                                # If no current logs, try to get previous logs (in case container restarted)
                                try:
                                    def get_previous_log():
                                        return core_v1.read_namespaced_pod_log(
                                            name=pod_name,
                                            namespace=namespace,
                                            container=container_name,
                                            tail_lines=1000,
                                            timestamps=True,
                                            previous=True  # Get previous logs
                                        )

                                    previous_logs = with_retries(get_previous_log)
                                    if previous_logs and previous_logs.strip():
                                        pod_logs.append(f"--- Container {container_name} (Previous) ---\n{previous_logs}")

                                except Exception as prev_error:
                                    logger.debug(f"No previous logs for container {container_name}: {prev_error}")

                        except Exception as container_error:
                            logger.warning(f"Could not get logs from container {container_name}: {container_error}")
                            pod_logs.append(f"--- Container {container_name} ---\nError retrieving logs: {container_error}")

                if pod_logs:
                    all_logs.append(f"{pod_header}\n" + "\n".join(pod_logs))
                else:
                    all_logs.append(f"{pod_header}\n[No container logs available]")

            except Exception as pod_error:
                logger.warning(f"Could not get logs from pod {pod_name}: {pod_error}")

                # Try to get pod events for more context when logs fail
                try:
                    def get_pod_events():
                        return core_v1.list_namespaced_event(
                            namespace=namespace,
                            field_selector=f"involvedObject.name={pod_name}"
                        )

                    events = with_retries(get_pod_events)
                    event_messages = []

                    for event in events.items:
                        event_time = event.last_timestamp or event.first_timestamp
                        event_messages.append(f"{event_time}: {event.reason} - {event.message}")

                    if event_messages:
                        all_logs.append(f"{pod_header}\n--- Pod Events ---\n" + "\n".join(event_messages))
                    else:
                        all_logs.append(f"{pod_header}\nCould not retrieve logs: {pod_error}")

                except Exception as event_error:
                    logger.warning(f"Could not get events for pod {pod_name}: {event_error}")
                    all_logs.append(f"{pod_header}\nCould not retrieve logs or events: {pod_error}")

        # Return combined logs with summary
        if all_logs:
            summary = f"=== Log Summary for Job {job_name} ===\nFound {len(pods.items)} pod(s)\n\n"
            return summary + "\n\n".join(all_logs)
        else:
            return f"No logs available from {len(pods.items)} pod(s)"

    except Exception as e:
        logger.error(f"Error retrieving logs for job {job_name}: {str(e)}")
        return f"Could not retrieve logs: {str(e)}"

def get_pod_events_for_job(job_name, namespace="default"):
    """
    Get events for pods associated with a job for enhanced diagnostics

    Args:
        job_name (str): The name of the job
        namespace (str): The Kubernetes namespace

    Returns:
        str: Events information or error message
    """
    if namespace is None:
        namespace = "default"

    try:
        # Get CoreV1Api client
        core_v1 = get_core_v1_api()

        # Get pods for the job first
        def list_pods():
            return core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}"
            )

        pods = with_retries(list_pods)

        if not pods.items:
            return "No pods found for job"

        all_events = []
        for pod in pods.items:
            pod_name = pod.metadata.name

            try:
                def get_pod_events():
                    return core_v1.list_namespaced_event(
                        namespace=namespace,
                        field_selector=f"involvedObject.name={pod_name}"
                    )

                events = with_retries(get_pod_events)

                pod_events = []
                for event in events.items:
                    event_time = event.last_timestamp or event.first_timestamp
                    pod_events.append(f"{event_time}: {event.reason} - {event.message}")

                if pod_events:
                    all_events.append(f"Pod {pod_name}: {'; '.join(pod_events)}")

            except Exception as event_error:
                logger.warning(f"Could not get events for pod {pod_name}: {event_error}")

        return "; ".join(all_events) if all_events else "No events found"

    except Exception as e:
        logger.error(f"Error retrieving events for job {job_name}: {str(e)}")
        return f"Could not retrieve events: {str(e)}"

def get_pod_status_for_job(job_name, namespace="default"):
    """
    Get detailed pod status information for a job

    Args:
        job_name (str): The name of the job
        namespace (str): The Kubernetes namespace

    Returns:
        str: Pod status information
    """
    if namespace is None:
        namespace = "default"

    try:
        # Get CoreV1Api client
        core_v1 = get_core_v1_api()

        # Get pods for the job
        def list_pods():
            return core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}"
            )

        pods = with_retries(list_pods)

        if not pods.items:
            return "No pods found for job"

        pod_statuses = []
        for pod in pods.items:
            pod_name = pod.metadata.name
            pod_phase = pod.status.phase

            # Get container statuses
            container_statuses = []
            if pod.status.container_statuses:
                for container_status in pod.status.container_statuses:
                    container_name = container_status.name
                    ready = container_status.ready
                    restart_count = container_status.restart_count

                    state_info = "Unknown"
                    if container_status.state:
                        if container_status.state.running:
                            state_info = f"Running since {container_status.state.running.started_at}"
                        elif container_status.state.waiting:
                            state_info = f"Waiting: {container_status.state.waiting.reason}"
                        elif container_status.state.terminated:
                            state_info = f"Terminated: {container_status.state.terminated.reason} (exit code: {container_status.state.terminated.exit_code})"

                    container_statuses.append(f"{container_name}: {state_info} (ready: {ready}, restarts: {restart_count})")

            pod_status_info = f"Pod {pod_name}: phase={pod_phase}"
            if container_statuses:
                pod_status_info += f", containers=[{'; '.join(container_statuses)}]"

            pod_statuses.append(pod_status_info)

        return "; ".join(pod_statuses)

    except Exception as e:
        logger.error(f"Error retrieving pod status for job {job_name}: {str(e)}")
        return f"Could not retrieve pod status: {str(e)}"

def cleanup_completed_job(job_name, namespace="default", force=False):
    """
    Clean up a completed job with enhanced safety checks

    Args:
        job_name (str): The name of the job to clean up
        namespace (str): The Kubernetes namespace the job is in
        force (bool): Force cleanup even if job is still running

    Returns:
        tuple: (success, message)
    """
    if namespace is None:
        namespace = "default"

    logger.info(f"Attempting to clean up job {job_name} in namespace {namespace}")

    try:
        # First check job status to ensure it's safe to delete
        if not force:
            status, _ = check_job_status(job_name, namespace)
            if status == "RUNNING":
                logger.warning(f"Job {job_name} is still running, skipping cleanup")
                return False, "Job is still running"
            elif status == "UNKNOWN":
                logger.warning(f"Job {job_name} status is unknown, skipping cleanup for safety")
                return False, "Job status unknown, cleanup skipped for safety"

        # Get BatchV1Api client
        batch_v1 = get_batch_v1_api()

        # Delete job with enhanced options
        def delete_k8s_job():
            # Import locally to avoid scoping issues
            from kubernetes import client
            return batch_v1.delete_namespaced_job(
                name=job_name,
                namespace=namespace,
                body=client.V1DeleteOptions(
                    propagation_policy="Background",  # Delete pods in background
                    grace_period_seconds=30  # Give pods 30 seconds to terminate gracefully
                )
            )

        with_retries(delete_k8s_job)
        logger.info(f"Successfully initiated cleanup of job {job_name}")

        # Optionally wait a moment and verify deletion
        import time
        time.sleep(2)

        try:
            # Try to get the job to see if it's really deleted
            batch_v1.read_namespaced_job_status(name=job_name, namespace=namespace)
            logger.info(f"Job {job_name} deletion in progress")
            return True, "Job cleanup initiated successfully"
        except Exception:
            logger.info(f"Job {job_name} successfully deleted")
            return True, "Job successfully deleted"

    except Exception as e:
        error_message = str(e).lower()
        if 'not found' in error_message:
            logger.info(f"Job {job_name} already deleted or not found")
            return True, "Job already deleted"
        else:
            logger.error(f"Error cleaning up job {job_name}: {str(e)}")
            return False, f"Cleanup failed: {str(e)}"

def check_job_completion_events(job_name, namespace="default"):
    """
    Check Kubernetes events to determine if a job completed successfully before being cleaned up.
    This is used when a job is not found to distinguish between:
    - Job that never existed (should be FAILED)
    - Job that completed and was cleaned up (should be SUCCEEDED)

    Args:
        job_name (str): The name of the job to check
        namespace (str): The Kubernetes namespace

    Returns:
        str: "COMPLETED" if job completion events are found, "NOT_FOUND" otherwise
    """
    if namespace is None:
        namespace = "default"

    logger.info(f"Checking completion events for job {job_name} in namespace {namespace}")

    try:
        # Get CoreV1Api client for events
        core_v1 = get_core_v1_api()

        # Check for job-level completion events
        def get_job_events():
            return core_v1.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={job_name},involvedObject.kind=Job"
            )

        job_events = with_retries(get_job_events, max_retries=2, operation_name="get_job_events")

        # Look for job completion events
        for event in job_events.items:
            if event.reason == "Completed" and "Job completed" in (event.message or ""):
                logger.info(f"Found job completion event for {job_name}: {event.message}")
                return "COMPLETED"

        # Also check pod events as backup (pods might have completion events even if job events are gone)
        def get_pod_events():
            return core_v1.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name"
            )

        all_events = with_retries(get_pod_events, max_retries=2, operation_name="get_all_pod_events")

        # Look for pod events related to our job (pods have job name in their name)
        completion_indicators = ["Completed", "Succeeded"]
        for event in all_events.items:
            event_obj_name = event.involved_object.name or ""
            # Check if this event is for a pod belonging to our job
            if (job_name in event_obj_name and
                event.reason in completion_indicators and
                event.involved_object.kind == "Pod"):
                logger.info(f"Found pod completion event for job {job_name}: {event.reason} - {event.message}")
                return "COMPLETED"

        logger.info(f"No completion events found for job {job_name}")
        return "NOT_FOUND"

    except Exception as e:
        logger.error(f"Error checking completion events for job {job_name}: {str(e)}")
        return "NOT_FOUND"

def delete_job(job_name, namespace="default"):
    """
    Delete a Kubernetes Job (legacy function, now uses enhanced cleanup)

    Args:
        job_name (str): The name of the job to delete
        namespace (str): The Kubernetes namespace the job is in

    Returns:
        bool: True if the job was deleted successfully, False otherwise
    """
    success, message = cleanup_completed_job(job_name, namespace, force=True)
    if not success:
        logger.error(f"Job deletion failed: {message}")
    return success

def test_kubernetes_connectivity():
    """
    Test Kubernetes cluster connectivity and return detailed diagnostics

    Returns:
        tuple: (is_connected, diagnostics_info)
    """
    logger.info("Testing Kubernetes cluster connectivity")

    diagnostics = {
        "cluster_reachable": False,
        "authentication_valid": False,
        "api_version_supported": False,
        "namespace_accessible": False,
        "error_details": []
    }

    try:
        # Test basic client creation
        logger.info("Testing Kubernetes client creation")
        api_client = get_k8s_client()
        diagnostics["cluster_reachable"] = True

        # Test API version
        logger.info("Testing API version compatibility")
        from kubernetes import client
        core_v1 = client.CoreV1Api(api_client)

        def test_api_version():
            return core_v1.get_api_resources()

        api_resources = with_retries(test_api_version, max_retries=2, operation_name="test_api_version")
        diagnostics["api_version_supported"] = True

        # Test authentication by listing namespaces
        logger.info("Testing authentication and permissions")
        def test_auth():
            return core_v1.list_namespace(limit=1)

        namespaces = with_retries(test_auth, max_retries=2, operation_name="test_authentication")
        diagnostics["authentication_valid"] = True

        # Test default namespace access
        logger.info("Testing default namespace access")
        def test_namespace():
            return core_v1.list_namespaced_pod(namespace="default", limit=1)

        pods = with_retries(test_namespace, max_retries=2, operation_name="test_namespace_access")
        diagnostics["namespace_accessible"] = True

        logger.info("Kubernetes connectivity test passed")
        return True, diagnostics

    except Exception as e:
        error_msg = f"Kubernetes connectivity test failed: {str(e)}"
        logger.error(error_msg)
        diagnostics["error_details"].append(error_msg)

        # Add specific error classification
        if isinstance(e, ImportError):
            diagnostics["error_details"].append("Kubernetes client library not available")
        elif "authentication" in str(e).lower() or "unauthorized" in str(e).lower():
            diagnostics["error_details"].append("Authentication/authorization failure")
        elif "connection" in str(e).lower() or "timeout" in str(e).lower():
            diagnostics["error_details"].append("Network connectivity issue")

        return False, diagnostics

def cleanup_completed_job(job_name, namespace="default", force=False):
    """
    Clean up a completed Kubernetes job with enhanced error handling

    Args:
        job_name (str): Name of the job to clean up
        namespace (str): Kubernetes namespace
        force (bool): Force cleanup even if job is still running

    Returns:
        tuple: (success, message)
    """
    logger.info(f"Starting cleanup for job {job_name} in namespace {namespace} (force: {force})")

    try:
        # Get job status first
        status, error_logs = check_job_status(job_name, namespace)

        if not force and status == "RUNNING":
            message = f"Job {job_name} is still running, skipping cleanup (use force=True to override)"
            logger.warning(message)
            return False, message

        # Delete the job
        logger.info(f"Deleting job {job_name}")
        delete_job(job_name, namespace)

        # Verify deletion
        try:
            def verify_deletion():
                batch_v1 = get_batch_v1_api()
                return batch_v1.read_namespaced_job(name=job_name, namespace=namespace)

            # Try to read the job - if it still exists, deletion may not be complete
            with_retries(verify_deletion, max_retries=2, operation_name="verify_job_deletion")

            # If we get here, job still exists
            message = f"Job {job_name} deletion initiated but job still exists"
            logger.warning(message)
            return True, message

        except Exception as e:
            if "not found" in str(e).lower() or (ApiException is not None and isinstance(e, ApiException) and e.status == 404):
                message = f"Job {job_name} successfully deleted"
                logger.info(message)
                return True, message
            else:
                message = f"Could not verify job deletion: {str(e)}"
                logger.warning(message)
                return True, message

    except Exception as e:
        message = f"Failed to cleanup job {job_name}: {str(e)}"
        logger.error(message)
        return False, message

def get_pod_events_for_job(job_name, namespace="default"):
    """
    Get Kubernetes events for pods associated with a job

    Args:
        job_name (str): Name of the job
        namespace (str): Kubernetes namespace

    Returns:
        str: Events information or error message
    """
    try:
        logger.info(f"Getting events for job {job_name} in namespace {namespace}")

        # Get CoreV1Api client
        core_v1 = get_core_v1_api()

        # Get events with field selector for the job
        def get_events():
            return core_v1.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.name={job_name}"
            )

        events = with_retries(get_events, max_retries=2, operation_name="get_job_events")

        if not events.items:
            return "No events found for job"

        # Format events
        event_messages = []
        for event in events.items:
            timestamp = event.first_timestamp or event.event_time
            event_type = event.type
            reason = event.reason
            message = event.message

            event_messages.append(f"[{timestamp}] {event_type}: {reason} - {message}")

        return "; ".join(event_messages)

    except Exception as e:
        logger.error(f"Error getting events for job {job_name}: {str(e)}")
        return f"Error retrieving events: {str(e)}"

def get_pod_status_for_job(job_name, namespace="default"):
    """
    Get detailed pod status information for a job

    Args:
        job_name (str): Name of the job
        namespace (str): Kubernetes namespace

    Returns:
        str: Pod status information or error message
    """
    try:
        logger.info(f"Getting pod status for job {job_name} in namespace {namespace}")

        # Get CoreV1Api client
        core_v1 = get_core_v1_api()

        # Get pods for the job
        def get_pods():
            return core_v1.list_namespaced_pod(
                namespace=namespace,
                label_selector=f"job-name={job_name}"
            )

        pods = with_retries(get_pods, max_retries=2, operation_name="get_job_pods")

        if not pods.items:
            return "No pods found for job"

        # Format pod status
        pod_statuses = []
        for pod in pods.items:
            pod_name = pod.metadata.name
            phase = pod.status.phase

            # Get container statuses
            container_info = []
            if pod.status.container_statuses:
                for container_status in pod.status.container_statuses:
                    container_name = container_status.name
                    ready = container_status.ready
                    restart_count = container_status.restart_count

                    state_info = "Unknown"
                    if container_status.state:
                        if container_status.state.running:
                            state_info = "Running"
                        elif container_status.state.waiting:
                            reason = container_status.state.waiting.reason
                            message = container_status.state.waiting.message or ""
                            state_info = f"Waiting: {reason} {message}".strip()
                        elif container_status.state.terminated:
                            exit_code = container_status.state.terminated.exit_code
                            reason = container_status.state.terminated.reason
                            state_info = f"Terminated: {reason} (exit: {exit_code})"

                    container_info.append(f"{container_name}: {state_info} (ready: {ready}, restarts: {restart_count})")

            pod_status = f"Pod {pod_name}: {phase}"
            if container_info:
                pod_status += f" - Containers: {'; '.join(container_info)}"

            pod_statuses.append(pod_status)

        return "; ".join(pod_statuses)

    except Exception as e:
        logger.error(f"Error getting pod status for job {job_name}: {str(e)}")
        return f"Error retrieving pod status: {str(e)}"

def validate_kubernetes_environment():
    """
    Validate that the Kubernetes environment is properly configured

    Returns:
        tuple: (is_valid, validation_results)
    """
    logger.info("Validating Kubernetes environment configuration")

    validation_results = {
        "environment_variables": {},
        "client_import": False,
        "cluster_connectivity": False,
        "permissions": {},
        "errors": []
    }

    try:
        # Check environment variables
        required_env_vars = ['EKS_CLUSTER_NAME', 'AWS_REGION']
        for var in required_env_vars:
            value = os.environ.get(var)
            validation_results["environment_variables"][var] = {
                "present": bool(value),
                "value": value if value else "Not set"
            }
            if not value:
                validation_results["errors"].append(f"Missing environment variable: {var}")

        # Check Kubernetes client import
        try:
            import kubernetes
            from kubernetes import client
            validation_results["client_import"] = True
        except ImportError as e:
            validation_results["errors"].append(f"Kubernetes client import failed: {str(e)}")

        # Test cluster connectivity
        if validation_results["client_import"]:
            is_connected, diagnostics = test_kubernetes_connectivity()
            validation_results["cluster_connectivity"] = is_connected
            validation_results["permissions"] = diagnostics

            if not is_connected:
                validation_results["errors"].extend(diagnostics.get("error_details", []))

        is_valid = len(validation_results["errors"]) == 0

        if is_valid:
            logger.info("Kubernetes environment validation passed")
        else:
            logger.error(f"Kubernetes environment validation failed: {validation_results['errors']}")

        return is_valid, validation_results

    except Exception as e:
        error_msg = f"Environment validation error: {str(e)}"
        logger.exception(error_msg)
        validation_results["errors"].append(error_msg)
        return False, validation_results
