# Copyright Amazon.com and its affiliates; all rights reserved.
# SPDX-License-Identifier: LicenseRef-.amazon.com.-AmznSL-1.0
# Licensed under the Amazon Software License  https://aws.amazon.com/asl/

"""Shared authorizer core logic for API Gateway HTTP and REST authorizers.

Provides unified authentication logic for:
- IP range validation (using clientIp module for XFF/CloudFront-aware resolution)
- Ignored path bypass
- API key verification with DynamoDB cache
- Cognito JWT verification
- External IDP JWT verification
"""

import json
import os
import time
import re
import hashlib
import requests
import urllib.request
from typing import Dict, Any, Optional, List
from aws_lambda_powertools import Logger
import boto3
from boto3.dynamodb.conditions import Key as DDBKey
from botocore.config import Config as BotoConfig

# Import libraries for different JWT verification methods
from joserfc import jwt as joserfc_jwt
from joserfc import jwk as joserfc_jwk
from joserfc import jws as joserfc_jws
import jwt as pyjwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.backends import default_backend
import base64

from common.auth.clientIp import resolve_client_ip, is_ip_authorized
from common.resourceNames import ResourceKeys, get_table_name

# Configure AWS Lambda Powertools logger
logger = Logger()

# Environment Variables - Retrieved at module load time
AWS_REGION = os.environ.get('AWS_REGION')
AUTH_MODE = os.environ.get('AUTH_MODE', '').lower()

# Fronting configuration
API_FRONTED = os.environ.get("API_FRONTED", "none")  # "cloudfront" | "alb" | "none"

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

# API Key Configuration
API_KEY_HASH_INDEX_NAME = 'apiKeyHashIndex'
API_KEY_CACHE_TTL = 15  # seconds before a cached entry expires

# DynamoDB client for API key lookups (only initialized if table configured)
_dynamodb_resource = None
_api_key_table = None
_user_roles_table = None

# Per-key cache: maps apiKeyHash -> { "record": DynamoDB item or None, "expiry": timestamp }
# - On cache hit (fresh): return cached record immediately (no DynamoDB call)
# - On cache miss (no entry): query GSI once, cache the result (record or None for not-found)
# - On cache miss (expired entry): query GSI once, update cache
# - None record means "we looked and it doesn't exist" — prevents repeated lookups for bad keys
_api_key_cache = {}

def _get_api_key_table():
    global _dynamodb_resource, _api_key_table
    if _api_key_table is None:
        try:
            table_name = get_table_name(ResourceKeys.API_KEY_STORAGE_TABLE)
            _dynamodb_resource = boto3.resource('dynamodb', config=BotoConfig(retries={'max_attempts': 3, 'mode': 'adaptive'}))
            _api_key_table = _dynamodb_resource.Table(table_name)
        except Exception as e:
            logger.error(f"Failed to resolve API_KEY_STORAGE_TABLE name: {e}")
            return None
    return _api_key_table

def _get_user_roles_table():
    global _dynamodb_resource, _user_roles_table
    if _user_roles_table is None:
        try:
            table_name = get_table_name(ResourceKeys.USER_ROLES_STORAGE_TABLE)
            if _dynamodb_resource is None:
                _dynamodb_resource = boto3.resource('dynamodb', config=BotoConfig(retries={'max_attempts': 3, 'mode': 'adaptive'}))
            _user_roles_table = _dynamodb_resource.Table(table_name)
        except Exception as e:
            logger.error(f"Failed to resolve USER_ROLES_STORAGE_TABLE name: {e}")
            return None
    return _user_roles_table

def _lookup_api_key_by_hash(key_hash: str):
    """
    Look up an API key record by hash using a per-key cache.

    Cache behavior:
    - Fresh cache hit: return immediately (no DynamoDB call)
    - Expired or missing: query DynamoDB GSI once, cache result for API_KEY_CACHE_TTL seconds
    - Not-found keys are cached as None to prevent DDOS of DynamoDB with invalid keys
    """
    current_time = time.time()
    cached = _api_key_cache.get(key_hash)

    if cached and current_time < cached['expiry']:
        # Cache hit — return record (may be None for known-missing keys)
        return cached['record']

    # Cache miss or expired — query DynamoDB GSI
    api_key_table = _get_api_key_table()
    if not api_key_table:
        return None

    try:
        response = api_key_table.query(
            IndexName=API_KEY_HASH_INDEX_NAME,
            KeyConditionExpression=DDBKey('apiKeyHash').eq(key_hash)
        )
        items = response.get('Items', [])
        record = items[0] if items else None

        # Cache the result (including None for not-found)
        _api_key_cache[key_hash] = {
            'record': record,
            'expiry': current_time + API_KEY_CACHE_TTL
        }
        return record
    except Exception as e:
        logger.error(f"Failed to query API key by hash: {str(e)}")
        # On error, return cached record if available (even if expired), else None
        return cached['record'] if cached else None

# MFA sign-in check hook (customConfigCommon is a customer-customizable module: Cognito
# MFA-preference check by default, external OAuth IDP logic slot for external mode)
try:
    from customConfigCommon.customAuthClaimsCheck import customMFATokenScopeCheckOverride
except Exception:
    customMFATokenScopeCheckOverride = None
    logger.error("customAuthClaimsCheck module not available; MFA check disabled")


def resolve_mfa_enabled(username: str, claims: Dict[str, Any], event: dict) -> bool:
    """
    Resolve whether the user signed in with MFA via the customizable
    customMFATokenScopeCheckOverride hook. Runs at authorization time so the result
    can be passed to handler lambdas through the authorizer context. Returns False
    when the hook is unavailable or raises.
    """
    if not customMFATokenScopeCheckOverride or not username:
        return False
    try:
        return bool(customMFATokenScopeCheckOverride(username, claims, event))
    except Exception as e:
        logger.error(f"MFA check hook failed, defaulting to false: {str(e)}")
        return False


# Cache for public keys to avoid fetching them on every request
# Download them only on cold start as per AWS best practices
# https://aws.amazon.com/blogs/compute/container-reuse-in-lambda/
keys_cache = {}
keys_cache_expiry = 0
CACHE_TTL = 60 * 60  # 1 hour in seconds

# Resolved JWKS URI per external issuer, cached so OpenID Connect discovery is not
# performed on the hot path of every authenticated request. Maps issuer_url ->
# {"jwks_uri": str, "expiry": timestamp}.
jwks_uri_cache = {}

# URL Templates
COGNITO_JWKS_URL_TEMPLATE = "{cognito_base_url}/{user_pool_id}/.well-known/jwks.json"
EXTERNAL_JWKS_URL_TEMPLATE = "{issuer_url}/.well-known/jwks.json"
OPENID_DISCOVERY_TEMPLATE = "{issuer_url}/.well-known/openid-configuration"


def _path_from_method_arn(method_arn: str) -> str:
    """Extract the resource path from a REST authorizer methodArn.

    methodArn form: arn:partition:execute-api:region:acct:apiId/STAGE/VERB/res/path...
    Returns "/res/path" (leading slash), or "" if it cannot be parsed.
    """
    if not method_arn:
        return ""
    _, _, tail = method_arn.partition(":execute-api:")
    parts = tail.split("/") if tail else []
    # parts = [ "region:acct:apiId", STAGE, VERB, <path segments...> ]
    if len(parts) <= 3:
        return ""
    return "/" + "/".join(parts[3:])


def is_path_ignored(path: str) -> bool:
    """
    Check if the request path should bypass authentication (the IP check still applies).

    Matches an ignored path exactly, or as a path-segment-anchored suffix of the request
    path. Anchoring on a leading "/" tolerates a stage prefix that a REST API
    REQUEST-authorizer event may include (for example "/api/api/version" when the stage is
    "api") while ensuring an unrelated route cannot become anonymous merely because its
    tail happens to spell an ignored path (e.g. "/foo/notapi/version").
    """
    if not path:
        return False
    for ignored in IGNORED_PATHS:
        # Anchor the suffix match on a path-segment boundary ("/" + the ignored path,
        # normalized to a single leading slash) so only a whole trailing segment sequence
        # matches, never a partial segment.
        anchored = "/" + ignored.lstrip("/")
        if path == ignored or path.endswith(anchored):
            return True
    return False


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


def verify_api_key(raw_key: str) -> Optional[Dict[str, Any]]:
    """
    Verify an API key by hashing it and looking up the hash via per-key cache.
    Each key is cached individually for API_KEY_CACHE_TTL seconds after first lookup.
    Invalid keys are cached as None to prevent DynamoDB DDOS.
    Returns a synthetic claims dict if valid, None otherwise.
    """
    try:
        user_roles_table = _get_user_roles_table()
        if not user_roles_table:
            logger.warning("API key user roles table not available, skipping API key auth")
            return None

        # Hash the incoming key
        key_hash = hashlib.sha256(raw_key.encode('utf-8')).hexdigest()

        # Look up from cache (cache misses do NOT trigger refresh)
        api_key_record = _lookup_api_key_by_hash(key_hash)
        if not api_key_record:
            return None  # No match — fall through to JWT

        # Check isActive
        if api_key_record.get('isActive') != 'true':
            logger.info(f"API key is disabled: {api_key_record.get('apiKeyId')}")
            return {'denied': True, 'reason': 'API key is disabled'}

        # Check expiration
        expires_at = api_key_record.get('expiresAt', '')
        if expires_at:
            from datetime import datetime, timezone
            try:
                expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if datetime.now(timezone.utc) > expiry:
                    logger.info(f"API key has expired: {api_key_record.get('apiKeyId')}")
                    return {'denied': True, 'reason': 'API key has expired'}
            except (ValueError, TypeError) as e:
                logger.warning(f"Could not parse expiresAt '{expires_at}': {e}")

        # Look up userId roles
        user_id = api_key_record.get('userId', '')
        if not user_id:
            logger.error(f"API key has no userId: {api_key_record.get('apiKeyId')}")
            return {'denied': True, 'reason': 'API key has no userId configured'}

        roles_response = user_roles_table.query(
            KeyConditionExpression=DDBKey('userId').eq(user_id)
        )
        user_roles = roles_response.get('Items', [])
        if not user_roles:
            logger.info(f"No roles found for API key userId: {user_id}")
            return {'denied': True, 'reason': f'No roles for API key user {user_id}'}

        # Build synthetic claims context
        role_names = [r.get('roleName', '') for r in user_roles if r.get('roleName')]
        claims = {
            'sub': user_id,
            'cognito:username': user_id,
            'vams:tokens': json.dumps([user_id]),
            'vams:roles': json.dumps(role_names),
            'vams:apiKeyId': api_key_record.get('apiKeyId', ''),
            'vams:authMethod': 'apiKey',
        }
        logger.info(f"API key authenticated successfully for user: {user_id}")
        return claims

    except Exception as e:
        logger.error(f"API key verification error: {str(e)}")
        return None  # Fall through to JWT on error


def verify_cognito_jwt(token: str) -> Optional[Dict[str, Any]]:
    """
    Verify Cognito JWT token using joserfc library
    """
    try:
        if not USER_POOL_ID or not APP_CLIENT_ID:
            logger.error("Missing Cognito configuration")
            return None

        # Get the kid from the headers prior to verification
        token_obj = joserfc_jws.extract_compact(token.encode())
        headers = token_obj.protected
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

        # Import the public key using joserfc
        public_key = joserfc_jwk.import_key(keys[key_index])

        # Decode and verify the token using joserfc, pinning the accepted algorithm to
        # RS256 (Cognito's issuance algorithm). An explicit allow-list prevents algorithm
        # confusion / alg=none from ever being accepted, matching verify_external_jwt.
        token_result = joserfc_jwt.decode(token, public_key, algorithms=['RS256'])

        logger.info('JWT signature successfully verified')

        # Extract claims from the verified token
        claims = token_result.claims

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
    Get JWKS URI for external IDP with discovery fallback.

    The resolved URI is cached per issuer for CACHE_TTL so OpenID Connect discovery
    is not performed on the hot path of every authenticated request.

    Args:
        issuer_url: The issuer URL for the external IDP

    Returns:
        The JWKS URI to use for fetching keys
    """
    global jwks_uri_cache

    current_time = time.time()
    cached = jwks_uri_cache.get(issuer_url)
    if cached and current_time < cached["expiry"]:
        return cached["jwks_uri"]

    # First try OpenID Connect discovery
    discovered_uri = discover_jwks_uri(issuer_url)
    if discovered_uri:
        logger.info(f"Using discovered JWKS URI: {discovered_uri}")
        jwks_uri_cache[issuer_url] = {
            "jwks_uri": discovered_uri,
            "expiry": current_time + CACHE_TTL,
        }
        return discovered_uri

    # Fall back to standard .well-known/jwks.json
    fallback_uri = EXTERNAL_JWKS_URL_TEMPLATE.format(issuer_url=issuer_url)
    logger.info(f"OpenID Connect discovery failed, falling back to: {fallback_uri}")
    jwks_uri_cache[issuer_url] = {
        "jwks_uri": fallback_uri,
        "expiry": current_time + CACHE_TTL,
    }
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


def authenticate_request(event: dict, *, fronted: str = None) -> dict:
    """
    Authenticate API Gateway request using IP validation, ignored paths, API key, or JWT.

    Returns a provider-neutral result dict:
        {"authorized": bool, "context": dict|None, "reason": str|None}

    The IP check uses the clientIp module's resolve_client_ip to handle XFF/CloudFront-aware
    resolution based on the fronted parameter.
    """
    fronted = fronted if fronted is not None else API_FRONTED

    # Step 1: IP validation (using shared clientIp module)
    client_ip = resolve_client_ip(event, fronted=fronted)
    if not is_ip_authorized(client_ip, ALLOWED_IP_RANGES):
        logger.info(f"IP {client_ip} not in allowed ranges")
        return {"authorized": False, "context": None, "reason": "IP address not authorized"}

    # Step 2: Check for ignored paths.
    # Resolve the request path across event shapes: proxy events expose it at
    # requestContext.http.path or the top-level "path"; a REST REQUEST-authorizer event
    # exposes "path" and "methodArn" (".../STAGE/VERB/resource/path"). The methodArn
    # fallback ensures the ignored-path check works in the authorizer context even when
    # "path" is absent.
    request_path = (
        (event.get("requestContext", {}).get("http", {}) or {}).get("path")
        or event.get("path")
        or _path_from_method_arn(event.get("methodArn", ""))
        or ""
    )
    if is_path_ignored(request_path):
        logger.info(f"Path {request_path} is in ignored paths, allowing access")
        return {"authorized": True, "context": None, "reason": None}

    # Step 3: Extract authorization header
    headers = event.get("headers", {}) or {}
    authorization_header = headers.get("Authorization") or headers.get("authorization")

    if not authorization_header:
        logger.info("Authorization header not found")
        return {"authorized": False, "context": None, "reason": "Token missing or invalid format"}

    # Step 4: API key path
    api_key_value = None
    if authorization_header.startswith('vams_'):
        api_key_value = authorization_header
    elif re.match(r'^Bearer\s+vams_', authorization_header, re.IGNORECASE):
        api_key_value = re.sub(r'^Bearer\s+', '', authorization_header, flags=re.IGNORECASE)

    if api_key_value:
        api_key_result = verify_api_key(api_key_value)
        if api_key_result is not None:
            if api_key_result.get('denied'):
                logger.info(f"API key denied: {api_key_result.get('reason')}")
                return {"authorized": False, "context": None, "reason": api_key_result.get('reason', 'API key denied')}
            # Valid API key — build context
            context = {}
            for key, value in api_key_result.items():
                if value is not None:
                    context[key] = str(value)
            logger.info("API key authorization successful")
            return {"authorized": True, "context": context, "reason": None}
        # api_key_result is None means no match found — fall through to JWT

    # Step 5: JWT path
    token = extract_token_from_header(event)
    if not token:
        logger.info("Token not found in Authorization header")
        return {"authorized": False, "context": None, "reason": "Token missing or invalid format"}

    if AUTH_MODE == 'cognito':
        claims = verify_cognito_jwt(token)
    elif AUTH_MODE == 'external':
        claims = verify_external_jwt(token)
    else:
        logger.error(f"Invalid AUTH_MODE: {AUTH_MODE}")
        return {"authorized": False, "context": None, "reason": "Token verification failed"}

    if not claims:
        logger.error("Token verification failed")
        return {"authorized": False, "context": None, "reason": "Token verification failed"}

    logger.info(f"Token verified successfully for user: {claims.get('sub', 'unknown')}")

    # Build context with string coercion
    context = {}
    for key, value in claims.items():
        if value is not None:
            context[key] = str(value)

    # MFA sign-in check: resolved once at authorization time via the customizable hook
    # (Cognito MFA preference by default; external IDP logic slot for external mode) and
    # passed to handlers through the authorizer context, so handler Lambdas make no IDP
    # calls of their own.
    username = (
        claims.get('cognito:username')
        or claims.get('username')
        or claims.get('sub')
    )
    mfa_enabled = resolve_mfa_enabled(username, claims, event)
    context['vams:mfaEnabled'] = 'true' if mfa_enabled else 'false'

    return {"authorized": True, "context": context, "reason": None}
