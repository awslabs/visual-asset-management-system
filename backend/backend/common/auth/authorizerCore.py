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
from collections import OrderedDict
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
from common.validators import normalize_userid

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

# User Roles Configuration
USER_ROLES_CACHE_TTL = 60  # seconds before a cached entry expires
# An empty role list expires sooner than a populated one. The API-key branch DENIES a
# request whose user resolves to no roles, so a full-TTL empty entry keeps denying a machine
# identity for a minute after its role was granted, while a short window still bounds how
# often a roleless identity re-reads the table.
#
# This value is not the grant latency an operator sees. API Gateway caches the policy this
# authorizer returns -- a Deny included -- for authorizerResultTtlInSeconds, which the
# authenticated security scheme sets to 30 seconds keyed on the Authorization header (see
# infra buildOpenApiSpec.ts), and an API key travels in that same header. So the authorizer
# cache is the dominating term: this TTL removes the extra minute the role cache would add,
# leaving a floor of about 30 seconds rather than 5. A value below the authorizer cache
# shortens nothing an operator can observe; a value above it puts the role cache back in
# charge of the window.
USER_ROLES_EMPTY_CACHE_TTL = 5  # seconds before a cached EMPTY role list expires

# Upper bound on entries held in each of the two per-request caches below.
API_KEY_CACHE_MAX_ENTRIES = 1000
USER_ROLES_CACHE_MAX_ENTRIES = 1000

# DynamoDB client for API key lookups (only initialized if table configured)
_dynamodb_resource = None
_api_key_table = None
_user_roles_table = None

# Per-key cache: maps apiKeyHash -> { "record": DynamoDB item or None, "expiry": timestamp }
# - On cache hit (fresh): return cached record immediately (no DynamoDB call)
# - On cache miss (no entry): query GSI once, cache the result (record or None for not-found)
# - On cache miss (expired entry): query GSI once, update cache
# - None record means "we looked and it doesn't exist" — prevents repeated lookups for bad keys
# Bounded and insertion-ordered: the cache key is the hash of a caller-supplied header value,
# so entries are added for keys that can never be hit again (see _cache_store).
_api_key_cache = OrderedDict()

# Per-user cache: maps userId -> { "roles": [roleName, ...], "expiry": timestamp }
# An empty role list is cached too, so a user with no roles does not re-query the table on
# every request, but only for USER_ROLES_EMPTY_CACHE_TTL. Bounded on the same terms as the
# API key cache.
_user_roles_cache = OrderedDict()


def _cache_store(cache: OrderedDict, key: str, value: dict, max_entries: int) -> None:
    """Insert into a size-bounded, insertion-ordered cache.

    The cache key of both caches below derives from a caller-supplied value, so an unbounded
    dict keeps growing for the life of the container with entries that can never be hit
    again. At capacity, entries whose expiry has already passed are dropped first and then
    the oldest insertions, so a stream of distinct keys evicts itself instead of the cache
    growing without limit.

    Expired entries are deliberately kept while the cache is below capacity: both lookups
    fall back to an expired entry when the table read fails, which is what stops a transient
    DynamoDB error from dropping a live identity's roles.
    """
    cache.pop(key, None)
    if len(cache) >= max_entries:
        current_time = time.time()
        for expired_key in [k for k, v in cache.items() if v.get('expiry', 0) <= current_time]:
            cache.pop(expired_key, None)
    while len(cache) >= max_entries:
        cache.popitem(last=False)
    cache[key] = value

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
        _cache_store(
            _api_key_cache,
            key_hash,
            {'record': record, 'expiry': current_time + API_KEY_CACHE_TTL},
            API_KEY_CACHE_MAX_ENTRIES,
        )
        return record
    except Exception as e:
        logger.error(f"Failed to query API key by hash: {str(e)}")
        # On error, return cached record if available (even if expired), else None
        return cached['record'] if cached else None

def _lookup_user_roles(user_id: str) -> List[str]:
    """
    Look up the role names assigned to a user, using a per-user cache.

    Cache behavior mirrors the API key cache:
    - Fresh cache hit: return immediately (no DynamoDB call)
    - Expired or missing: query the user roles table once, cache for USER_ROLES_CACHE_TTL
    - An empty list is cached for the shorter USER_ROLES_EMPTY_CACHE_TTL: it is the result a
      role grant changes, and the API-key branch denies on it, so a machine identity must
      not keep being denied for the full window after its role is assigned. What an
      operator observes is floored by the 30-second API Gateway authorizer result cache,
      which replays the Deny for the same Authorization header without calling this
      function at all

    Returns an empty list when the table is unavailable or the query fails. Roles are
    informational in the authorizer context (Casbin re-reads a user's roles from DynamoDB
    when building policy), so a lookup failure degrades the context rather than denying a
    request that authorization would otherwise allow.

    The user id is normalized before it is used, because a user-role row is written with the
    normalized id and the id reaching here is whatever the IDP issued. Normalizing at this
    one point covers both callers and keys the cache on one spelling per identity.
    """
    if not user_id:
        return []

    user_id = normalize_userid(user_id)
    current_time = time.time()
    cached = _user_roles_cache.get(user_id)

    if cached and current_time < cached['expiry']:
        return cached['roles']

    user_roles_table = _get_user_roles_table()
    if not user_roles_table:
        logger.warning("User roles table not available; authorizer context roles will be empty")
        return cached['roles'] if cached else []

    try:
        # Paged to exhaustion: one query returns at most 1 MB, and a user with more role
        # assignments than that would otherwise have vams:roles silently truncated. Reading
        # the PRESENCE of LastEvaluatedKey is how DynamoDB reports the end of a listing, and
        # it also keeps the loop finite against a stubbed reader whose .get() answers every
        # key with a truthy mock.
        roles = []
        query_kwargs = {'KeyConditionExpression': DDBKey('userId').eq(user_id)}
        while True:
            response = user_roles_table.query(**query_kwargs)
            roles.extend(
                r.get('roleName', '') for r in response.get('Items', []) if r.get('roleName'))
            if 'LastEvaluatedKey' not in response:
                break
            query_kwargs['ExclusiveStartKey'] = response['LastEvaluatedKey']

        ttl = USER_ROLES_CACHE_TTL if roles else USER_ROLES_EMPTY_CACHE_TTL
        _cache_store(
            _user_roles_cache,
            user_id,
            {'roles': roles, 'expiry': current_time + ttl},
            USER_ROLES_CACHE_MAX_ENTRIES,
        )
        return roles
    except Exception as e:
        logger.error(f"Failed to query roles for user: {str(e)}")
        # On error, fall back to the cached roles if present (even if expired), else empty
        return cached['roles'] if cached else []


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
# Maps cache_key -> {"keys": [...], "expiry": timestamp, "fetched": timestamp}. The expiry is
# held per entry, matching _api_key_cache and jwks_uri_cache: a single module-wide expiry
# scalar is reset by a fetch under any cache_key, which extends the freshness window of every
# other entry and can serve a stale key set past its own TTL.
keys_cache = {}
CACHE_TTL = 60 * 60  # 1 hour in seconds

# Minimum age a cached key set must reach before a `kid` miss may force a refetch. An issuer
# signing-key rotation puts a kid in circulation that a warm container's cached set does not
# contain, and that entry stays fresh for CACHE_TTL, so without a refetch every validly
# signed new-kid token is denied for the rest of that window. The floor bounds the JWKS
# request rate an unknown kid can drive to one per key set per container per interval.
JWKS_MIN_REFETCH_INTERVAL_SECONDS = 60

# Timeout for JWKS and OpenID discovery HTTP fetches. urlopen with no timeout inherits the
# process-wide default socket timeout, which is None, so a black-holed endpoint blocks the
# authorizer until the Lambda timeout instead of failing fast.
JWKS_FETCH_TIMEOUT_SECONDS = 10

# Resolved JWKS URI per external issuer, cached so OpenID Connect discovery is not
# performed on the hot path of every authenticated request. Maps issuer_url ->
# {"jwks_uri": str, "expiry": timestamp}.
jwks_uri_cache = {}


def _cached_jwks_keys(cache_key: str, current_time: float) -> Optional[List[Dict[str, Any]]]:
    """The cached key set for cache_key while its own expiry is still in the future."""
    entry = keys_cache.get(cache_key)
    if entry and current_time < entry['expiry']:
        return entry['keys']
    return None


def _jwks_refetch_is_rate_limited(cache_key: str, current_time: float) -> bool:
    """True when cache_key was fetched too recently to be force-refetched again."""
    entry = keys_cache.get(cache_key)
    if not entry:
        return False
    return (current_time - entry.get('fetched', 0)) < JWKS_MIN_REFETCH_INTERVAL_SECONDS


def _store_jwks_keys(cache_key: str, keys: List[Dict[str, Any]], current_time: float) -> None:
    keys_cache[cache_key] = {
        'keys': keys,
        'expiry': current_time + CACHE_TTL,
        'fetched': current_time,
    }


def _key_for_kid(keys: Optional[List[Dict[str, Any]]], kid: str) -> Optional[Dict[str, Any]]:
    """The JWK carrying this kid, or None when the set does not contain it."""
    for key in keys or []:
        if key.get('kid') == kid:
            return key
    return None

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

    Matches an ignored path exactly. A REST API REQUEST-authorizer event exposes the
    resource path with the stage already stripped ("/api/version", not "/api/api/version"),
    which is also what _path_from_method_arn returns, so the resolved request path is
    directly comparable to a configured entry. The comparison stays an equality test: a
    prefix or suffix test would let a greedy "{proxy+}" route whose tail spells an ignored
    path (e.g. ".../download/stream/api/version") bypass authentication.
    """
    if not path:
        return False
    for ignored in IGNORED_PATHS:
        if not ignored:
            continue
        # Compare against a single-leading-slash form of the configured entry so an entry
        # written without one still matches the resolved path.
        if path == "/" + ignored.lstrip("/"):
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


def _api_key_expiry_denial(expires_at: str, api_key_id: str) -> Optional[str]:
    """
    Evaluate a stored API key expiresAt value, returning a denial reason or None.

    Accepts both formats the API key models accept — an ISO 8601 datetime
    ("2026-12-31T23:59:59Z") and a date-only value ("2026-12-31") — and reads a value
    carrying no UTC offset as UTC, so a date-only expiry compares instead of raising.
    A value that cannot be evaluated denies: expiry is the only lifetime bound on an
    API key (isActive is a separate manual flag), so an unreadable value must not read
    as "no expiry" and extend the credential indefinitely. The raw value is not logged.
    """
    from datetime import datetime, timezone
    try:
        try:
            expiry = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            expiry = datetime.strptime(expires_at, '%Y-%m-%d')
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError, AttributeError) as e:
        logger.warning(f"API key expiresAt could not be evaluated, denying key {api_key_id}: {e}")
        return 'API key expiry could not be evaluated'

    if datetime.now(timezone.utc) > expiry:
        logger.info(f"API key has expired: {api_key_id}")
        return 'API key has expired'
    return None


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
            expiry_denial = _api_key_expiry_denial(expires_at, api_key_record.get('apiKeyId'))
            if expiry_denial:
                return {'denied': True, 'reason': expiry_denial}

        # Look up userId roles
        user_id = api_key_record.get('userId', '')
        if not user_id:
            logger.error(f"API key has no userId: {api_key_record.get('apiKeyId')}")
            return {'denied': True, 'reason': 'API key has no userId configured'}

        # Roles come from the same per-user helper the JWT path uses, so the two cannot
        # drift. It pages the query to exhaustion -- one query returns at most 1 MB, and a
        # machine identity holding more role assignments than that would otherwise carry a
        # silently short vams:roles -- and caches the result per user.
        role_names = _lookup_user_roles(user_id)
        if not role_names:
            logger.info(f"No roles found for API key userId: {user_id}")
            return {'denied': True, 'reason': f'No roles for API key user {user_id}'}

        # Build synthetic claims context
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

        # Search for the kid in the downloaded public keys. A kid the cached set does not
        # contain is what an issuer signing-key rotation looks like, so refetch once
        # (rate-limited) before denying rather than waiting out the entry's own TTL.
        key_record = _key_for_kid(keys, kid)
        if key_record is None:
            logger.info(f"Public key for kid {kid} not in the cached key set; refetching JWKS")
            keys = get_cognito_keys(AWS_REGION, USER_POOL_ID, force_refresh=True)
            key_record = _key_for_kid(keys, kid)

        if key_record is None:
            logger.error(f"Public key not found in jwks.json for kid: {kid}")
            return None

        # Import the public key using joserfc
        public_key = joserfc_jwk.import_key(key_record)

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


def get_cognito_keys(region: str, user_pool_id: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Download and cache Cognito public keys from JWKS endpoint

    force_refresh bypasses a still-fresh cache entry, which is how a kid the cached set does
    not contain (an issuer key rotation) is picked up before that entry's TTL lapses. The
    refetch is skipped, and the cached set returned unchanged, while the entry is younger
    than JWKS_MIN_REFETCH_INTERVAL_SECONDS.
    """
    global keys_cache

    current_time = time.time()
    cache_key = f"cognito:{region}:{user_pool_id}"

    if force_refresh:
        if _jwks_refetch_is_rate_limited(cache_key, current_time):
            logger.info("Cognito public keys were fetched recently; serving the cached set")
            return keys_cache[cache_key]['keys']
    else:
        # Check if we have valid cached keys
        cached_keys = _cached_jwks_keys(cache_key, current_time)
        if cached_keys is not None:
            logger.info("Using cached Cognito public keys")
            return cached_keys

    # Download fresh keys using configurable base URL
    if not COGNITO_BASE_URL:
        logger.error("Missing COGNITO_BASE_URL environment variable")
        raise Exception("COGNITO_BASE_URL environment variable is required")

    keys_url = COGNITO_JWKS_URL_TEMPLATE.format(cognito_base_url=COGNITO_BASE_URL, user_pool_id=user_pool_id)
    logger.info(f"Downloading Cognito public keys from: {keys_url}")

    try:
        with urllib.request.urlopen(keys_url, timeout=JWKS_FETCH_TIMEOUT_SECONDS) as response:
            if response.getcode() != 200:
                raise Exception(f"Failed to fetch JWKS. Status code: {response.getcode()}")

            jwks_data = json.loads(response.read().decode('utf-8'))
            keys = jwks_data['keys']

            # Cache the keys
            _store_jwks_keys(cache_key, keys, current_time)

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

        # Find the key with matching kid, refetching once (rate-limited) when the cached set
        # does not carry it — the shape an issuer signing-key rotation takes.
        key = _key_for_kid(keys, kid)
        if key is None:
            logger.info(f"Public key for kid {kid} not in the cached key set; refetching JWKS")
            keys = get_external_keys(jwt_issuer_url, force_refresh=True)
            key = _key_for_kid(keys, kid)

        if key is None:
            logger.error(f"Public key not found for kid: {kid}")
            return None

        return construct_public_key_from_jwk(key)

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
        response = requests.get(discovery_url, timeout=JWKS_FETCH_TIMEOUT_SECONDS)
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


def get_external_keys(jwt_issuer_url: str, force_refresh: bool = False) -> List[Dict[str, Any]]:
    """
    Download and cache External IDP public keys from JWKS endpoint
    Uses OpenID Connect discovery with fallback to standard JWKS endpoint

    force_refresh behaves as it does in get_cognito_keys: it bypasses a still-fresh entry so
    a rotated signing key is picked up on a kid miss, subject to the same minimum interval.
    """
    global keys_cache

    current_time = time.time()

    # Get the JWKS URI (with discovery and fallback)
    jwks_uri = get_jwks_uri_for_external_idp(jwt_issuer_url)

    # Use the actual JWKS URI in the cache key to ensure proper cache isolation
    cache_key = f"external_jwks:{jwks_uri}"

    if force_refresh:
        if _jwks_refetch_is_rate_limited(cache_key, current_time):
            logger.info(f"External IDP public keys for {jwks_uri} were fetched recently; serving the cached set")
            return keys_cache[cache_key]['keys']
    else:
        # Check if we have valid cached keys for this specific JWKS URI
        cached_keys = _cached_jwks_keys(cache_key, current_time)
        if cached_keys is not None:
            logger.info(f"Using cached External IDP public keys for: {jwks_uri}")
            return cached_keys

    # Download fresh keys from the determined JWKS URI
    logger.info(f"Downloading External IDP public keys from: {jwks_uri}")

    try:
        response = requests.get(jwks_uri, timeout=JWKS_FETCH_TIMEOUT_SECONDS)
        response.raise_for_status()

        jwks_data = response.json()
        keys = jwks_data['keys']

        # Cache the keys with the specific JWKS URI
        _store_jwks_keys(cache_key, keys, current_time)

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

    An ignored-path bypass additionally carries "ignoredPath": True. It is the one
    authorized result that establishes no identity, so a caller building an IAM policy
    can scope it to the ignored paths rather than to the whole API.

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
        return {"authorized": True, "context": None, "reason": None, "ignoredPath": True}

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

    username = (
        claims.get('cognito:username')
        or claims.get('username')
        or claims.get('sub')
    )

    # Role resolution: read the user's roles from the user roles table at authorization time
    # so every JWT auth mode carries them, and assign unconditionally so the freshly-read
    # value replaces any vams:roles claim copied out of the token above. Resolving here
    # rather than at token issuance means a role assignment or revocation takes effect
    # within USER_ROLES_CACHE_TTL instead of lasting for the lifetime of an issued token.
    context['vams:roles'] = json.dumps(_lookup_user_roles(username))

    # MFA sign-in check: resolved once at authorization time via the customizable hook
    # (Cognito MFA preference by default; external IDP logic slot for external mode) and
    # passed to handlers through the authorizer context, so handler Lambdas make no IDP
    # calls of their own.
    mfa_enabled = resolve_mfa_enabled(username, claims, event)
    context['vams:mfaEnabled'] = 'true' if mfa_enabled else 'false'

    return {"authorized": True, "context": context, "reason": None}
