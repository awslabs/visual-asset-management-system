# Copyright Amazon.com and its affiliates; all rights reserved.
# SPDX-License-Identifier: LicenseRef-.amazon.com.-AmznSL-1.0
# Licensed under the Amazon Software License  https://aws.amazon.com/asl/

import json
import os
import time
import re
import requests
import urllib.request
from typing import Dict, Any, Optional, List, Tuple
from aws_lambda_powertools import Logger

# Import libraries for different JWT verification methods
from jose import jwk, jwt as jose_jwt
from jose.utils import base64url_decode
import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import base64

# Configure AWS Lambda Powertools logger
logger = Logger()

# Environment Variables - Retrieved at module load time
AWS_REGION = os.environ.get('AWS_REGION')
AUTH_MODE = os.environ.get('AUTH_MODE', '').lower()

# Cognito Configuration
USER_POOL_ID = os.environ.get('USER_POOL_ID')
APP_CLIENT_ID = os.environ.get('APP_CLIENT_ID')
COGNITO_BASE_URL = os.environ.get('COGNITO_BASE_URL')

# External IDP Configuration
JWT_ISSUER_URL = os.environ.get('JWT_ISSUER_URL')
JWT_AUDIENCE = os.environ.get('JWT_AUDIENCE')

# Authorizer Configuration
ALLOWED_IP_RANGES_ENV = os.environ.get('ALLOWED_IP_RANGES')
IGNORED_PATHS_ENV = os.environ.get('IGNORED_PATHS')

# Parse JSON environment variables
try:
    ALLOWED_IP_RANGES = json.loads(ALLOWED_IP_RANGES_ENV) if ALLOWED_IP_RANGES_ENV else []
except json.JSONDecodeError:
    logger.error("Failed to parse ALLOWED_IP_RANGES environment variable")
    ALLOWED_IP_RANGES = []

try:
    IGNORED_PATHS = json.loads(IGNORED_PATHS_ENV) if IGNORED_PATHS_ENV else []
except json.JSONDecodeError:
    logger.error("Failed to parse IGNORED_PATHS environment variable")
    IGNORED_PATHS = []

# Cache for public keys to avoid fetching them on every request
# Download them only on cold start as per AWS best practices
# https://aws.amazon.com/blogs/compute/container-reuse-in-lambda/
keys_cache = {}
keys_cache_expiry = 0
CACHE_TTL = 60 * 60  # 1 hour in seconds

# URL Templates
COGNITO_JWKS_URL_TEMPLATE = "{cognito_base_url}/{user_pool_id}/.well-known/jwks.json"
EXTERNAL_JWKS_URL_TEMPLATE = "{issuer_url}/.well-known/jwks.json"
OPENID_DISCOVERY_TEMPLATE = "{issuer_url}/.well-known/openid-configuration"


def lambda_handler(event, context):
    """
    Lambda authorizer for HTTP API Gateway with custom JWT verification and IP validation
    Supports both Cognito and External IDP authentication based on environment variables
    Uses payload format version 2.0 (simple boolean response)
    """
    logger.info(f"Event: {json.dumps(event)}")
    
    try:
        # Get source IP for validation
        source_ip = event.get('requestContext', {}).get('http', {}).get('sourceIp')
        
        # Validate IP ranges first (before authentication for performance)
        if not is_ip_authorized(source_ip):
            logger.info(f"IP {source_ip} not in allowed ranges")
            return {"isAuthorized": False}
        
        # Check if path should be ignored
        request_path = event.get('requestContext', {}).get('http', {}).get('path', '')
        if is_path_ignored(request_path):
            logger.info(f"Path {request_path} is in ignored paths, allowing access")
            return {"isAuthorized": True}
        
        # Extract the JWT token from Authorization header
        token = extract_token_from_header(event)
        if not token:
            logger.info("Token not found in Authorization header")
            return {"isAuthorized": False}
        
        if AUTH_MODE == 'cognito':
            claims = verify_cognito_jwt(token)
        elif AUTH_MODE == 'external':
            claims = verify_external_jwt(token)
        else:
            logger.error(f"Invalid AUTH_MODE: {AUTH_MODE}")
            return {"isAuthorized": False}
        
        if not claims:
            logger.error("Token verification failed")
            return {"isAuthorized": False}
        
        logger.info(f"Token verified successfully for user: {claims.get('sub', 'unknown')}")
        
        # Return simple authorization response with comprehensive JWT claims context
        # Format expected by VAMS auth system: ['requestContext']['authorizer']['jwt']['claims']
        context = {}
        
        # Add all JWT claims to context for downstream processing
        for key, value in claims.items():
            # API Gateway context values must be strings
            if value is not None:
                context[key] = str(value)
        
        return {
            "isAuthorized": True,
            "context": context
        }
        
    except Exception as e:
        logger.error(f"Authorizer error: {str(e)}")
        return {"isAuthorized": False}

def is_ip_authorized(source_ip: Optional[str]) -> bool:
    """
    Check if source IP is within allowed IP ranges
    """
    if not ALLOWED_IP_RANGES:
        return True  # Allow if no IP restrictions configured
    
    if not source_ip:
        logger.error("Source IP not found in event and IP restrictions defined")
        return False
    
    try:
        source_num = ip_to_num(source_ip)
        return any(
            ip_to_num(min_ip) <= source_num <= ip_to_num(max_ip)
            for min_ip, max_ip in ALLOWED_IP_RANGES
        )
    except (ValueError, IndexError) as e:
        logger.error(f"IP range validation error: {e}")
        return False  # Deny on validation errors

def ip_to_num(ip: str) -> int:
    """Convert IP address to numeric representation for comparison"""
    return int(''.join(f"{int(part):03d}" for part in ip.split('.')))

def is_path_ignored(path: str) -> bool:
    """
    Check if the request path should bypass authorization
    """
    return path in IGNORED_PATHS

def extract_token_from_header(event: Dict[str, Any]) -> Optional[str]:
    """
    Extract JWT token from Authorization header
    """
    headers = event.get('headers', {})
    authorization_header = headers.get('Authorization') or headers.get('authorization')
    
    if not authorization_header:
        return None
    
    # Check if the header follows the "Bearer <token>" format
    match = re.match(r'^Bearer\s+(.*)$', authorization_header, re.IGNORECASE)
    if not match:
        return None
    
    return match.group(1)

def verify_cognito_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify Cognito JWT token using python-jose library (AWS best practices)
    Based on: https://github.com/awslabs/aws-support-tools/blob/master/Cognito/decode-verify-jwt/decode-verify-jwt.py
    """
    try:
        if not USER_POOL_ID or not APP_CLIENT_ID:
            logger.error("Missing Cognito configuration")
            return None
        
        # Get the kid from the headers prior to verification
        headers = jose_jwt.get_unverified_headers(token)
        kid = headers.get('kid')
        
        if not kid:
            logger.error("Token header missing 'kid' field")
            return None
        
        # Get the public keys
        keys = get_cognito_keys(AWS_REGION, USER_POOL_ID)
        
        # Search for the kid in the downloaded public keys
        key_index = -1
        for i in range(len(keys)):
            if kid == keys[i]['kid']:
                key_index = i
                break
        
        if key_index == -1:
            logger.error(f"Public key not found in jwks.json for kid: {kid}")
            return None
        
        # Construct the public key
        public_key = jwk.construct(keys[key_index])
        
        # Get the last two sections of the token (message and signature)
        message, encoded_signature = str(token).rsplit('.', 1)
        
        # Decode the signature
        decoded_signature = base64url_decode(encoded_signature.encode('utf-8'))
        
        # Verify the signature
        if not public_key.verify(message.encode("utf8"), decoded_signature):
            logger.error('JWT signature verification failed')
            return None
        
        logger.info('JWT signature successfully verified')
        
        # Since we passed the verification, we can now safely use the unverified claims
        claims = jose_jwt.get_unverified_claims(token)
        
        # Verify the token expiration
        current_time = time.time()
        if current_time > claims.get('exp', 0):
            logger.error('Token is expired')
            return None
        
        # Verify the Audience (use claims['client_id'] if verifying an access token)
        # For ID tokens, use 'aud' claim
        token_audience = claims.get('aud') or claims.get('client_id')
        if token_audience != APP_CLIENT_ID:
            logger.error(f'Token was not issued for this audience. Expected: {APP_CLIENT_ID}, Got: {token_audience}')
            return None
        
        # Additional validations
        # Verify issuer using configurable base URL
        if not COGNITO_BASE_URL:
            logger.error("Missing COGNITO_BASE_URL environment variable")
            return None
        
        expected_issuer = f"{COGNITO_BASE_URL}/{USER_POOL_ID}"
        if claims.get('iss') != expected_issuer:
            logger.error(f'Invalid token issuer. Expected: {expected_issuer}, Got: {claims.get("iss")}')
            return None
        
        # Verify token use (should be 'id' for ID tokens)
        token_use = claims.get('token_use')
        if token_use not in ['id', 'access']:
            logger.error(f'Invalid token_use: {token_use}')
            return None
        
        logger.info(f'Cognito token successfully verified for user: {claims.get("sub", "unknown")}')
        return claims
        
    except Exception as e:
        logger.error(f"Cognito JWT verification error: {str(e)}")
        return None

def verify_external_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify External IDP JWT token using PyJWT library
    """
    try:
        if not JWT_ISSUER_URL or not JWT_AUDIENCE:
            logger.error("Missing External IDP configuration")
            return None
        
        # Get the signing key for token verification
        signing_key = get_signing_key_for_external_token(token, JWT_ISSUER_URL)
        if not signing_key:
            return None
        
        # Verify and decode the token
        claims = pyjwt.decode(
            token,
            signing_key,
            algorithms=['RS256'],
            audience=JWT_AUDIENCE,
            issuer=JWT_ISSUER_URL,
            options={
                'verify_signature': True,
                'verify_exp': True,
                'verify_aud': True,
                'verify_iss': True
            }
        )
        
        logger.info(f'External IDP token successfully verified for user: {claims.get("sub", "unknown")}')
        return claims
        
    except pyjwt.ExpiredSignatureError:
        logger.error("Token has expired")
        return None
    except pyjwt.InvalidAudienceError:
        logger.error("Token audience validation failed")
        return None
    except pyjwt.InvalidIssuerError:
        logger.error("Token issuer validation failed")
        return None
    except pyjwt.InvalidSignatureError:
        logger.error("Token signature validation failed")
        return None
    except pyjwt.InvalidTokenError as e:
        logger.error(f"Invalid token: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"External JWT verification error: {str(e)}")
        return None

def get_cognito_keys(region: str, user_pool_id: str) -> List[Dict[str, Any]]:
    """
    Download and cache Cognito public keys from JWKS endpoint
    """
    global keys_cache, keys_cache_expiry
    
    current_time = time.time()
    cache_key = f"cognito:{region}:{user_pool_id}"
    
    # Check if we have valid cached keys
    if cache_key in keys_cache and current_time < keys_cache_expiry:
        logger.info("Using cached Cognito public keys")
        return keys_cache[cache_key]
    
    # Download fresh keys using configurable base URL
    if not COGNITO_BASE_URL:
        logger.error("Missing COGNITO_BASE_URL environment variable")
        raise Exception("COGNITO_BASE_URL environment variable is required")
    
    keys_url = COGNITO_JWKS_URL_TEMPLATE.format(cognito_base_url=COGNITO_BASE_URL, user_pool_id=user_pool_id)
    logger.info(f"Downloading Cognito public keys from: {keys_url}")
    
    try:
        with urllib.request.urlopen(keys_url) as response:
            if response.getcode() != 200:
                raise Exception(f"Failed to fetch JWKS. Status code: {response.getcode()}")
            
            jwks_data = json.loads(response.read().decode('utf-8'))
            keys = jwks_data['keys']
            
            # Cache the keys
            keys_cache[cache_key] = keys
            keys_cache_expiry = current_time + CACHE_TTL
            
            logger.info(f"Successfully downloaded and cached {len(keys)} public keys")
            return keys
            
    except Exception as e:
        logger.error(f"Error downloading Cognito public keys: {str(e)}")
        raise

def get_signing_key_for_external_token(token: str, jwt_issuer_url: str) -> Optional[str]:
    """
    Get the signing key for External IDP JWT token verification
    """
    try:
        # Get the kid from the token header
        unverified_header = pyjwt.get_unverified_header(token)
        kid = unverified_header.get('kid')
        
        if not kid:
            logger.error("Token header missing 'kid' field")
            return None
        
        # Get the public keys
        keys = get_external_keys(jwt_issuer_url)
        
        # Find the key with matching kid
        for key in keys:
            if key.get('kid') == kid:
                return construct_public_key_from_jwk(key)
        
        logger.error(f"Public key not found for kid: {kid}")
        return None
        
    except Exception as e:
        logger.error(f"Error getting signing key: {str(e)}")
        return None

def construct_public_key_from_jwk(jwk_key: Dict[str, Any]) -> Optional[str]:
    """
    Construct a public key from JWK format for PyJWT
    """
    try:
        # Extract the modulus and exponent from the JWK
        n = jwk_key.get('n')
        e = jwk_key.get('e')
        
        if not n or not e:
            logger.error("JWK missing required 'n' or 'e' parameters")
            return None
        
        # Decode base64url encoded values
        def base64url_decode(data):
            # Add padding if needed
            missing_padding = len(data) % 4
            if missing_padding:
                data += '=' * (4 - missing_padding)
            return base64.urlsafe_b64decode(data)
        
        n_bytes = base64url_decode(n)
        e_bytes = base64url_decode(e)
        
        # Convert to integers
        n_int = int.from_bytes(n_bytes, byteorder='big')
        e_int = int.from_bytes(e_bytes, byteorder='big')
        
        # Create RSA public key
        public_numbers = rsa.RSAPublicNumbers(e_int, n_int)
        public_key = public_numbers.public_key(backend=default_backend())
        
        # Convert to PEM format for PyJWT
        pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo
        )
        
        return pem.decode('utf-8')
        
    except Exception as e:
        logger.error(f"Error constructing public key from JWK: {str(e)}")
        return None

def discover_jwks_uri(issuer_url: str) -> Optional[str]:
    """
    Discover JWKS URI using OpenID Connect Discovery
    
    Args:
        issuer_url: The issuer URL for the external IDP
        
    Returns:
        The jwks_uri from .well-known/openid-configuration or None if discovery fails
    """
    discovery_url = OPENID_DISCOVERY_TEMPLATE.format(issuer_url=issuer_url)
    logger.info(f"Attempting OpenID Connect discovery at: {discovery_url}")
    
    try:
        response = requests.get(discovery_url, timeout=10)
        response.raise_for_status()
        
        discovery_data = response.json()
        jwks_uri = discovery_data.get('jwks_uri')
        
        if jwks_uri:
            logger.info(f"OpenID Connect discovery successful. JWKS URI: {jwks_uri}")
            return jwks_uri
        else:
            logger.warning("OpenID Connect discovery response missing 'jwks_uri' field")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.warning(f"OpenID Connect discovery failed with request error: {str(e)}")
        return None
    except json.JSONDecodeError as e:
        logger.warning(f"OpenID Connect discovery failed with JSON decode error: {str(e)}")
        return None
    except Exception as e:
        logger.warning(f"OpenID Connect discovery failed with unexpected error: {str(e)}")
        return None

def get_jwks_uri_for_external_idp(issuer_url: str) -> str:
    """
    Get JWKS URI for external IDP with discovery fallback
    
    Args:
        issuer_url: The issuer URL for the external IDP
        
    Returns:
        The JWKS URI to use for fetching keys
    """
    # First try OpenID Connect discovery
    discovered_uri = discover_jwks_uri(issuer_url)
    if discovered_uri:
        logger.info(f"Using discovered JWKS URI: {discovered_uri}")
        return discovered_uri
    
    # Fall back to standard .well-known/jwks.json
    fallback_uri = EXTERNAL_JWKS_URL_TEMPLATE.format(issuer_url=issuer_url)
    logger.info(f"OpenID Connect discovery failed, falling back to: {fallback_uri}")
    return fallback_uri

def get_external_keys(jwt_issuer_url: str) -> List[Dict[str, Any]]:
    """
    Download and cache External IDP public keys from JWKS endpoint
    Uses OpenID Connect discovery with fallback to standard JWKS endpoint
    """
    global keys_cache, keys_cache_expiry
    
    current_time = time.time()
    
    # Get the JWKS URI (with discovery and fallback)
    jwks_uri = get_jwks_uri_for_external_idp(jwt_issuer_url)
    
    # Use the actual JWKS URI in the cache key to ensure proper cache isolation
    cache_key = f"external_jwks:{jwks_uri}"
    
    # Check if we have valid cached keys for this specific JWKS URI
    if cache_key in keys_cache and current_time < keys_cache_expiry:
        logger.info(f"Using cached External IDP public keys for: {jwks_uri}")
        return keys_cache[cache_key]
    
    # Download fresh keys from the determined JWKS URI
    logger.info(f"Downloading External IDP public keys from: {jwks_uri}")
    
    try:
        response = requests.get(jwks_uri, timeout=10)
        response.raise_for_status()
        
        jwks_data = response.json()
        keys = jwks_data['keys']
        
        # Cache the keys with the specific JWKS URI
        keys_cache[cache_key] = keys
        keys_cache_expiry = current_time + CACHE_TTL
        
        logger.info(f"Successfully downloaded and cached {len(keys)} public keys from: {jwks_uri}")
        return keys
        
    except Exception as e:
        logger.error(f"Error downloading External IDP public keys from {jwks_uri}: {str(e)}")
        raise
