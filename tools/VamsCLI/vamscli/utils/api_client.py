"""API client for VamsCLI."""

import json
import threading
import requests
from typing import Dict, Any, List, Optional
from urllib.parse import quote, urljoin

# Single-flight token refresh: concurrent uploads share one APIClient (one requests.Session)
# and run API calls on parallel executor threads. Without serialization, a 403/401 on several
# threads at once triggers a stampede of simultaneous Cognito refresh_token calls, each rotating
# the refresh token and overwriting the saved auth profile — so all but one fail and the whole
# batch cascades to Forbidden. This module-level lock serializes refreshes; a thread that finds
# the access token already changed since its failed request reuses it instead of refreshing again.
_TOKEN_REFRESH_LOCK = threading.Lock()

from ..constants import (
    API_VERSION, API_AMPLIFY_CONFIG, DEFAULT_TIMEOUT, MAX_AUTH_RETRIES, MINIMUM_API_VERSION,
    API_LOGIN_PROFILE, API_SECURE_CONFIG, API_ASSETS, API_DATABASE_ASSETS, API_DATABASE_ASSET,
    API_CREATE_FOLDER, API_LIST_FILES, API_FILE_INFO, API_MOVE_FILE, API_COPY_FILE,
    API_ARCHIVE_FILE, API_UNARCHIVE_FILE, API_DELETE_ASSET_PREVIEW, 
    API_DELETE_AUXILIARY_PREVIEW, API_DELETE_FILE, API_REVERT_FILE_VERSION, API_SET_PRIMARY_FILE,
    API_ARCHIVE_ASSET, API_UNARCHIVE_ASSET, API_DELETE_ASSET, API_DOWNLOAD_ASSET, API_ASSET_EXPORT, API_GET_ASSET_HISTORY, API_DATABASE, API_DATABASE_BY_ID, API_BUCKETS,
    API_TAGS, API_TAG_DELETE, API_TAG_TYPES, API_TAG_TYPE_DELETE,
    API_CREATE_ASSET_VERSION, API_REVERT_ASSET_VERSION, API_GET_ASSET_VERSIONS, API_GET_ASSET_VERSION,
    API_ASSET_VERSION_BY_ID, API_ASSET_VERSION_ARCHIVE, API_ASSET_VERSION_UNARCHIVE,
    API_ASSET_LINKS, API_ASSET_LINKS_SINGLE, API_ASSET_LINKS_UPDATE, API_ASSET_LINKS_DELETE, API_ASSET_LINKS_FOR_ASSET,
    API_ASSET_LINKS_METADATA, API_ASSET_LINKS_METADATA_KEY, API_METADATA, API_METADATA_SCHEMA,
    API_METADATA_SCHEMA_LIST, API_METADATA_SCHEMA_BY_ID,
    API_SEARCH, API_SEARCH_SIMPLE, API_SEARCH_MAPPING,
    API_PIPELINES, API_DATABASE_PIPELINES, API_DATABASE_PIPELINE,
    API_PIPELINE_TEMPLATES, API_PIPELINE_TEMPLATE, API_PIPELINE_TEMPLATE_TAG_SCHEMA,
    API_WORKFLOWS, API_DATABASE_WORKFLOWS, API_DATABASE_WORKFLOW,
    API_WORKFLOW_TRIGGERS, API_WORKFLOW_TRIGGER,
    API_WORKFLOW_EXECUTIONS, API_EXECUTE_WORKFLOW, API_WORKFLOW_EXECUTIONS_GLOBAL,
    API_WORKFLOW_EXECUTION, API_WORKFLOW_EXECUTION_DETAILS,
    API_WORKFLOW_EXECUTION_DETAILS_METADATA, API_WORKFLOW_EXECUTION_LOGS,
    API_WORKFLOW_EXECUTION_RERUN, API_WORKFLOW_EXECUTION_PERMANENT,
    API_AUTH_API_KEYS, API_AUTH_API_KEY
)
from ..version import get_version
from .exceptions import (
    APIError, VersionMismatchError, AuthenticationError, OverrideTokenError, 
    TokenExpiredError, PermissionDeniedError,
    APIUnavailableError, AssetNotFoundError, AssetAlreadyExistsError,
    DatabaseNotFoundError, DatabaseAlreadyExistsError, DatabaseDeletionError,
    BucketNotFoundError, InvalidDatabaseDataError, InvalidAssetDataError, FileUploadError,
    AssetAlreadyArchivedError, AssetNotArchivedError, AssetDeletionError, TagNotFoundError, TagAlreadyExistsError,
    TagTypeNotFoundError, TagTypeAlreadyExistsError, TagTypeInUseError, 
    InvalidTagDataError, InvalidTagTypeDataError, AssetVersionError, AssetVersionNotFoundError,
    AssetVersionOperationError, InvalidAssetVersionDataError, AssetVersionRevertError, AssetVersionArchiveError,
    AssetLinkError, AssetLinkNotFoundError, AssetLinkValidationError, AssetLinkPermissionError,
    CycleDetectionError, AssetLinkAlreadyExistsError, InvalidRelationshipTypeError, AssetLinkOperationError,
    RateLimitExceededError, RetryExhaustedError,
    PipelineNotFoundError, PipelineAlreadyExistsError, InvalidPipelineDataError,
    PipelineTemplateNotFoundError, PipelineTemplateAlreadyExistsError, InvalidPipelineTemplateDataError,
    WorkflowNotFoundError, WorkflowExecutionError, WorkflowAlreadyRunningError,
    InvalidWorkflowDataError,
    WorkflowTriggerNotFoundError, InvalidWorkflowTriggerDataError,
    ExecutionNotFoundError, ExecutionInProgressError, InvalidExecutionDataError
)
from .profile import ProfileManager, read_active_profile_name
from .retry_config import get_retry_config


class APIClient:
    """HTTP client for VAMS API Gateway."""
    
    def __init__(self, base_url: str, profile_manager: Optional[ProfileManager] = None):
        self.base_url = base_url.rstrip('/')
        # A bare ProfileManager() targets the DEFAULT profile, not the active one, so a client
        # constructed without one would read another deployment's credentials.
        self.profile_manager = profile_manager or ProfileManager(read_active_profile_name())
        self.session = requests.Session()
        self.session.timeout = DEFAULT_TIMEOUT
        
    def _get_headers(self, include_auth: bool = True) -> Dict[str, str]:
        """Get request headers."""
        headers = {
            'Content-Type': 'application/json',
            'User-Agent': f'vamscli/{get_version()}'
        }
        
        if include_auth:
            auth_profile = self.profile_manager.load_auth_profile()
            if auth_profile and 'access_token' in auth_profile:
                headers['Authorization'] = f"Bearer {auth_profile['access_token']}"
                
        return headers
        
    def _validate_token_before_request(self, include_auth: bool = True):
        """Validate token before making request (pre-flight check for override tokens)."""
        if not include_auth:
            return
            
        # Check if we have an override token and validate it
        if self.profile_manager.is_override_token():
            if self.profile_manager.is_token_expired():
                expiration_info = self.profile_manager.get_token_expiration_info()
                raise OverrideTokenError(
                    "Override token has expired. Please provide a new token with "
                    "'vamscli auth set-override --token <new_token>' or use "
                    "'vamscli --token-override <new_token> <command>'"
                )
    
    def _make_request(self, method: str, endpoint: str, include_auth: bool = True,
                     retry_count: int = 0, throttle_retry_count: int = 0,
                     raise_http_errors: bool = False, **kwargs) -> requests.Response:
        """Make HTTP request with error handling and retries for both auth and throttling.

        `raise_http_errors` leaves a non-2xx as the original `requests.exceptions.HTTPError` instead
        of converting it to an `APIError` here, for callers that map the status code and the handler's
        response body onto their own domain exception.
        """
        # Import logging utilities
        from .logging import log_api_request, log_api_response
        import time
        
        # Pre-flight validation for override tokens
        self._validate_token_before_request(include_auth)
        
        url = urljoin(self.base_url + '/', endpoint.lstrip('/'))
        headers = self._get_headers(include_auth)
        retry_config = get_retry_config()
        # The bearer token this request carries — passed to _try_refresh_token so concurrent 401/403
        # handlers can detect an already-completed refresh (single-flight) instead of stampeding.
        request_token = headers.get('Authorization', '').replace('Bearer ', '') or None
        
        # Log API request (always to file, console if verbose)
        try:
            log_api_request(method, url, headers, kwargs.get('json') or kwargs.get('data'))
        except Exception:
            # Don't fail if logging fails
            pass
        
        try:
            start_time = time.time()
            response = self.session.request(method, url, headers=headers, **kwargs)
            duration = time.time() - start_time
            
            # Log API response (always to file, console if verbose)
            try:
                response_data = None
                try:
                    if response.content:
                        # Try to parse as JSON for better formatting
                        try:
                            response_data = response.json()
                        except Exception:
                            # If not JSON, use text
                            response_data = response.text
                except Exception:
                    pass
                log_api_response(response.status_code, response_data, duration)
            except Exception:
                # Don't fail if logging fails
                pass
            
            # Handle 429 rate limiting with exponential backoff
            if response.status_code == 429:
                if retry_config.should_retry(throttle_retry_count):
                    # Parse Retry-After header if present
                    retry_after = None
                    retry_after_header = response.headers.get('Retry-After')
                    if retry_after_header:
                        try:
                            retry_after = int(retry_after_header)
                        except (ValueError, TypeError):
                            pass
                    
                    # Calculate delay with exponential backoff
                    delay = retry_config.calculate_delay(throttle_retry_count, retry_after)
                    
                    # Show progress and sleep
                    retry_config.sleep_with_progress(
                        delay, 
                        throttle_retry_count + 1, 
                        retry_config.max_retry_attempts + 1,
                        show_progress=True
                    )
                    
                    # Retry the request
                    return self._make_request(
                        method, endpoint, include_auth, retry_count,
                        throttle_retry_count + 1, raise_http_errors, **kwargs
                    )
                else:
                    # All retry attempts exhausted
                    error_msg = (
                        f"Rate limit exceeded. All {retry_config.max_retry_attempts} retry attempts exhausted. "
                        f"The API is currently throttling requests. Please try again later."
                    )
                    raise RetryExhaustedError(error_msg)
            
            # Handle 401 errors with retry logic
            if response.status_code == 401 and include_auth and retry_count < MAX_AUTH_RETRIES:
                # For override tokens, don't retry - fail immediately with clear message
                if self.profile_manager.is_override_token():
                    raise OverrideTokenError(
                        "Override token authentication failed. The token may be invalid or expired. "
                        "Please provide a new token with 'vamscli auth set-override --token <new_token>' "
                        "or use 'vamscli --token-override <new_token> <command>'"
                    )
                
                # Try to refresh token or re-authenticate (for Cognito tokens only)
                if self._try_refresh_token(token_before=request_token):
                    return self._make_request(
                        method, endpoint, include_auth, retry_count + 1,
                        throttle_retry_count, raise_http_errors, **kwargs
                    )
                else:
                    raise AuthenticationError("Authentication failed. Please run 'vamscli auth login' to re-authenticate.")
            
            # Handle 403 errors - distinguish between expired tokens and permission issues
            if response.status_code == 403 and include_auth and retry_count < MAX_AUTH_RETRIES:
                # For override tokens, check if expired
                if self.profile_manager.is_override_token():
                    if self.profile_manager.is_token_expired():
                        raise TokenExpiredError(
                            "Override token has expired. Please provide a new token with "
                            "'vamscli auth set-override --token <new_token>' or use "
                            "'vamscli --token-override <new_token> <command>'"
                        )
                    else:
                        # Token not expired, this is a permission issue
                        raise PermissionDeniedError(
                            "Access forbidden. You do not have permission to perform this action. "
                            "Contact your administrator if you believe this is an error."
                        )
                
                # For Cognito tokens, try to refresh and retry
                if self._try_refresh_token(token_before=request_token):
                    return self._make_request(
                        method, endpoint, include_auth, retry_count + 1,
                        throttle_retry_count, raise_http_errors, **kwargs
                    )
                else:
                    # Refresh failed - could be expired token or permission issue
                    # Check if token is expired
                    if self.profile_manager.is_token_expired():
                        raise TokenExpiredError(
                            "Authentication token has expired. Please run 'vamscli auth login' to re-authenticate."
                        )
                    else:
                        # Not expired, this is a permission issue
                        raise PermissionDeniedError(
                            "Access forbidden. You do not have permission to perform this action. "
                            "Contact your administrator if you believe this is an error."
                        )
            
            response.raise_for_status()
            return response
            
        except (RateLimitExceededError, RetryExhaustedError, TokenExpiredError, PermissionDeniedError):
            # Re-raise specific errors without wrapping
            raise
        except requests.exceptions.HTTPError as e:
            if raise_http_errors:
                # The caller maps the status code and the handler's body itself.
                raise

            # Handle other HTTP errors with appropriate messages
            status_code = e.response.status_code

            if status_code == 429:
                # This shouldn't happen due to the check above, but just in case
                raise RateLimitExceededError(f"Rate limit exceeded: {e}")
            elif status_code >= 500:
                # Server errors (500, 502, 503, 504, etc.)
                error_data = {}
                try:
                    error_data = e.response.json() if e.response.content else {}
                except Exception:
                    pass
                error_message = error_data.get('message', str(e))
                raise APIError(
                    f"Server error ({status_code}): {error_message}. "
                    "The VAMS API is experiencing issues. Please try again later."
                )
            elif status_code == 404:
                # Not found errors
                error_data = {}
                try:
                    error_data = e.response.json() if e.response.content else {}
                except Exception:
                    pass
                error_message = error_data.get('message', str(e))
                raise APIError(f"Resource not found (404): {error_message}")
            elif status_code == 400:
                # Bad request errors
                error_data = {}
                try:
                    error_data = e.response.json() if e.response.content else {}
                except Exception:
                    pass
                error_message = error_data.get('message', str(e))
                raise APIError(f"Invalid request (400): {error_message}")
            else:
                # Other HTTP errors
                error_data = {}
                try:
                    error_data = e.response.json() if e.response.content else {}
                except Exception:
                    pass
                error_message = error_data.get('message', str(e))
                raise APIError(f"API request failed ({status_code}): {error_message}")
        except requests.exceptions.ConnectionError as e:
            raise APIError(
                f"Connection error: Unable to connect to the VAMS API. "
                "Please check your network connection and verify the API Gateway URL is correct. "
                f"Details: {e}"
            )
        except requests.exceptions.Timeout as e:
            raise APIError(
                f"Request timeout: The VAMS API did not respond in time. "
                "The service may be experiencing high load. Please try again later. "
                f"Details: {e}"
            )
        except requests.exceptions.RequestException as e:
            raise APIError(f"Request failed: {e}")
            
    def _try_refresh_token(self, token_before: Optional[str] = None) -> bool:
        """Try to refresh the authentication token or re-authenticate using saved credentials.

        Serialized across threads via _TOKEN_REFRESH_LOCK (single-flight): when concurrent uploads
        each hit a 403/401, only the first thread through the lock actually refreshes; the others
        see that the saved access token already changed from the one their request used
        (token_before) and reuse it instead of triggering another refresh + token rotation."""
        from .logging import log_auth_diagnostic, log_config_diagnostic

        with _TOKEN_REFRESH_LOCK:
            # Another thread may have already refreshed while we waited for the lock. If the saved
            # access token differs from the one our failed request carried, treat it as refreshed.
            if token_before is not None:
                current = (self.profile_manager.load_auth_profile() or {}).get('access_token')
                if current and current != token_before:
                    log_auth_diagnostic(
                        auth_type="token_refresh", status="reused_concurrent_refresh",
                        details={'profile_name': self.profile_manager.profile_name})
                    return True
            return self._do_refresh_token()

    def _do_refresh_token(self) -> bool:
        """Perform the actual token refresh / re-auth. Callers hold _TOKEN_REFRESH_LOCK."""
        from .logging import log_auth_diagnostic, log_config_diagnostic

        try:
            auth_profile = self.profile_manager.load_auth_profile()
            if not auth_profile or 'refresh_token' not in auth_profile:
                log_auth_diagnostic(
                    auth_type="token_refresh",
                    status="no_refresh_token",
                    details={'has_auth_profile': bool(auth_profile)}
                )
                # No refresh token available, try re-authentication with saved credentials
                return self._try_reauth_with_saved_credentials()
            
            # Load configuration to get Cognito settings
            config = self.profile_manager.load_config()
            amplify_config = config.get('amplify_config', {})
            
            # Log configuration diagnostic
            log_config_diagnostic(config, self.profile_manager.profile_name)
            
            region = amplify_config.get('region')
            user_pool_id = amplify_config.get('cognitoUserPoolId')
            client_id = amplify_config.get('cognitoAppClientId')
            
            if not all([region, user_pool_id, client_id]):
                log_auth_diagnostic(
                    auth_type="token_refresh",
                    status="incomplete_config",
                    details={
                        'has_region': bool(region),
                        'has_user_pool_id': bool(user_pool_id),
                        'has_client_id': bool(client_id)
                    }
                )
                return self._try_reauth_with_saved_credentials()
            
            # Import here to avoid circular imports
            from ..auth.cognito import CognitoAuthenticator
            
            authenticator = CognitoAuthenticator(region, user_pool_id, client_id)
            
            # Try to refresh tokens
            new_tokens = authenticator.refresh_token(auth_profile['refresh_token'])
            
            # Update auth profile with new tokens
            auth_profile.update(new_tokens)
            self.profile_manager.save_auth_profile(auth_profile)
            
            log_auth_diagnostic(
                auth_type="token_refresh",
                status="success",
                details={'profile_name': self.profile_manager.profile_name}
            )
            
            return True
            
        except Exception as e:
            log_auth_diagnostic(
                auth_type="token_refresh",
                status="failure",
                details={'profile_name': self.profile_manager.profile_name},
                error=e
            )
            # If refresh fails, try re-authentication with saved credentials
            return self._try_reauth_with_saved_credentials()
    
    def _try_reauth_with_saved_credentials(self) -> bool:
        """Try to re-authenticate using saved credentials."""
        from .logging import log_auth_diagnostic
        
        try:
            # Check if we have saved credentials
            saved_credentials = self.profile_manager.load_credentials()
            if not saved_credentials or 'username' not in saved_credentials or 'password' not in saved_credentials:
                log_auth_diagnostic(
                    auth_type="reauth_saved_creds",
                    status="no_saved_credentials",
                    details={'has_credentials': bool(saved_credentials)}
                )
                return False
            
            log_auth_diagnostic(
                auth_type="reauth_saved_creds",
                status="attempting",
                details={
                    'user_id': saved_credentials['username'],
                    'profile_name': self.profile_manager.profile_name
                }
            )
            
            # Load configuration to get Cognito settings
            config = self.profile_manager.load_config()
            amplify_config = config.get('amplify_config', {})
            
            region = amplify_config.get('region')
            user_pool_id = amplify_config.get('cognitoUserPoolId')
            client_id = amplify_config.get('cognitoAppClientId')
            
            if not all([region, user_pool_id, client_id]):
                log_auth_diagnostic(
                    auth_type="reauth_saved_creds",
                    status="incomplete_config",
                    details={
                        'has_region': bool(region),
                        'has_user_pool_id': bool(user_pool_id),
                        'has_client_id': bool(client_id)
                    }
                )
                return False
            
            # Import here to avoid circular imports
            from ..auth.cognito import CognitoAuthenticator
            
            authenticator = CognitoAuthenticator(region, user_pool_id, client_id)
            
            # Re-authenticate using saved credentials
            auth_result = authenticator.authenticate(
                saved_credentials['username'], 
                saved_credentials['password']
            )
            
            # Add user_id to the auth result
            auth_result['user_id'] = saved_credentials['username']
            
            # Save new authentication profile
            self.profile_manager.save_auth_profile(auth_result)
            
            # Try to call login profile API to validate and refresh user profile.
            # A failure here is non-blocking: we still have valid tokens and must
            # continue on to fetch feature switches below.
            login_profile_error = None
            try:
                login_profile_result = self.call_login_profile(saved_credentials['username'])
            except Exception as lp_error:
                login_profile_error = lp_error

            # Try to fetch feature switches independently of the login-profile result
            secure_config_error = None
            try:
                secure_config_result = self.get_secure_config()
                self.profile_manager.save_feature_switches(secure_config_result)
            except Exception as sc_error:
                # Feature switches fetch failure is non-blocking
                secure_config_error = sc_error

            if login_profile_error is None and secure_config_error is None:
                log_auth_diagnostic(
                    auth_type="reauth_saved_creds",
                    status="success",
                    details={
                        'user_id': saved_credentials['username'],
                        'profile_name': self.profile_manager.profile_name,
                        'secure_config': secure_config_result
                    }
                )
            else:
                log_auth_diagnostic(
                    auth_type="reauth_saved_creds",
                    status="success_partial",
                    details={
                        'user_id': saved_credentials['username'],
                        'profile_name': self.profile_manager.profile_name,
                        'login_profile_error': str(login_profile_error) if login_profile_error else None,
                        'secure_config_error': str(secure_config_error) if secure_config_error else None
                    }
                )

            return True
            
        except Exception as e:
            log_auth_diagnostic(
                auth_type="reauth_saved_creds",
                status="failure",
                details={'profile_name': self.profile_manager.profile_name},
                error=e
            )
            # If re-authentication fails, return False
            return False
        
    def get(self, endpoint: str, include_auth: bool = True, **kwargs) -> requests.Response:
        """Make GET request."""
        return self._make_request('GET', endpoint, include_auth, **kwargs)
        
    def post(self, endpoint: str, data: Optional[Dict[str, Any]] = None, 
             include_auth: bool = True, **kwargs) -> requests.Response:
        """Make POST request."""
        if data:
            kwargs['json'] = data
        return self._make_request('POST', endpoint, include_auth, **kwargs)
        
    def put(self, endpoint: str, data: Optional[Dict[str, Any]] = None, 
            include_auth: bool = True, **kwargs) -> requests.Response:
        """Make PUT request."""
        if data:
            kwargs['json'] = data
        return self._make_request('PUT', endpoint, include_auth, **kwargs)
        
    def delete(self, endpoint: str, include_auth: bool = True, **kwargs) -> requests.Response:
        """Make DELETE request."""
        return self._make_request('DELETE', endpoint, include_auth, **kwargs)
        
    def check_version(self) -> Dict[str, str]:
        """Check API version and compare with CLI version."""
        try:
            response = self.get(API_VERSION, include_auth=False)
            api_version_data = response.json()
            
            api_version = api_version_data.get('version', 'unknown')
            cli_version = get_version()
            
            return {
                'api_version': api_version,
                'cli_version': cli_version,
                'match': api_version == cli_version
            }
            
        except Exception as e:
            raise APIError(f"Failed to check API version: {e}")
            
    def get_amplify_config(self) -> Dict[str, Any]:
        """Get Amplify configuration from API."""
        try:
            response = self.get(API_AMPLIFY_CONFIG, include_auth=False)
            return response.json()
        except Exception as e:
            raise APIError(f"Failed to get Amplify configuration: {e}")
    
    def _is_version_compatible(self, api_version: str) -> bool:
        """Check if API version is compatible with CLI requirements."""
        try:
            # Parse version numbers for comparison
            api_parts = [int(x) for x in api_version.split('.')]
            min_parts = [int(x) for x in MINIMUM_API_VERSION.split('.')]
            
            # Pad shorter version with zeros
            max_len = max(len(api_parts), len(min_parts))
            api_parts.extend([0] * (max_len - len(api_parts)))
            min_parts.extend([0] * (max_len - len(min_parts)))
            
            # Compare versions
            return api_parts >= min_parts
            
        except (ValueError, AttributeError):
            # If we can't parse the version, assume incompatible
            return False
    
    def check_api_availability(self) -> Dict[str, Any]:
        """Check if API is available and compatible."""
        try:
            # Use a shorter timeout for availability check
            response = self.session.get(
                urljoin(self.base_url + '/', API_VERSION.lstrip('/')),
                headers={'User-Agent': f'vamscli/{get_version()}'},
                timeout=10
            )
            
            if response.status_code == 404:
                raise APIUnavailableError(
                    "VAMS API version endpoint not found. You may be using the CLI against "
                    f"a VAMS version older than {MINIMUM_API_VERSION}."
                )
            
            response.raise_for_status()
            version_data = response.json()
            api_version = version_data.get('version', 'unknown')
            
            if not self._is_version_compatible(api_version):
                raise APIUnavailableError(
                    f"VAMS API version {api_version} detected. VamsCLI requires "
                    f"VAMS version {MINIMUM_API_VERSION} or higher."
                )
            
            return {
                'available': True,
                'version': api_version,
                'compatible': True
            }
            
        except APIUnavailableError:
            # Re-raise our specific errors
            raise
        except requests.exceptions.ConnectionError:
            raise APIUnavailableError(
                "VAMS API is not currently available. Please check your network connection "
                "and verify the API Gateway URL is correct."
            )
        except requests.exceptions.Timeout:
            raise APIUnavailableError(
                "VAMS API is not responding. The service may be temporarily unavailable."
            )
        except requests.exceptions.RequestException as e:
            raise APIUnavailableError(
                f"VAMS API is not currently available: {e}"
            )
        except Exception as e:
            raise APIUnavailableError(
                "VAMS API is not responding correctly. You may be using the CLI against "
                f"a VAMS version older than {MINIMUM_API_VERSION}."
            )
    
    def call_login_profile(self, user_id: str) -> Dict[str, Any]:
        """Call login profile API to refresh user profile and validate authentication."""
        try:
            endpoint = f"{API_LOGIN_PROFILE}/{user_id}"
            response = self.get(endpoint, include_auth=True)
            
            # If we get here, the authentication was successful
            return {
                'success': True,
                'user_id': user_id,
                'profile_refreshed': True
            }
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                # Authentication failed - clear credentials
                self.profile_manager.delete_auth_profile()
                
                if e.response.status_code == 401:
                    raise AuthenticationError(
                        f"Authentication failed: Invalid or expired token for user '{user_id}'. "
                        "Credentials have been cleared. Please re-authenticate."
                    )
                else:  # 403
                    raise AuthenticationError(
                        f"Authentication failed: User '{user_id}' is not authorized. "
                        "Credentials have been cleared. Please contact your administrator."
                    )
            else:
                # Other HTTP errors
                raise APIError(f"Login profile API call failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to call login profile API: {e}")
    
    def get_secure_config(self) -> Dict[str, Any]:
        """
        Fetch fsecure config from secure-config API.
        
        Returns:
            API response data with featuresEnabled string
        
        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            response = self.get(API_SECURE_CONFIG, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Feature switches API call failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to fetch feature switches: {e}")
    
    def create_asset(self, asset_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new asset using the /assets POST endpoint.
        
        Args:
            asset_data: Asset creation data matching CreateAssetRequestModel
        
        Returns:
            API response data with assetId and message
        
        Raises:
            AssetAlreadyExistsError: When asset already exists
            DatabaseNotFoundError: When database doesn't exist
            InvalidAssetDataError: When asset data is invalid
            APIError: When API call fails
        """
        try:
            response = self.post(API_ASSETS, data=asset_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already exists' in error_message.lower():
                    raise AssetAlreadyExistsError(f"Asset already exists: {error_message}")
                else:
                    raise InvalidAssetDataError(f"Invalid asset data: {error_message}")
                    
            elif e.response.status_code == 404:
                raise DatabaseNotFoundError("Database not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset creation failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to create asset: {e}")
    
    def update_asset(self, database_id: str, asset_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing asset using the /database/{databaseId}/assets/{assetId} PUT endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            update_data: Asset update data matching UpdateAssetRequestModel
        
        Returns:
            API response data with operation result
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            InvalidAssetDataError: When update data is invalid
            APIError: When API call fails
        """
        try:
            endpoint = API_DATABASE_ASSET.format(databaseId=database_id, assetId=asset_id)
            response = self.put(endpoint, data=update_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid update data: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update asset: {e}")
    
    def get_asset(self, database_id: str, asset_id: str, show_archived: bool = False) -> Dict[str, Any]:
        """
        Get an asset using the /database/{databaseId}/assets/{assetId} GET endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            show_archived: Whether to include archived assets
        
        Returns:
            API response data with asset details
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            endpoint = API_DATABASE_ASSET.format(databaseId=database_id, assetId=asset_id)
            params = {}
            if show_archived:
                params['showArchived'] = 'true'
                
            response = self.get(endpoint, include_auth=True, params=params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get asset: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get asset: {e}")

    def initialize_upload(self, database_id: str, asset_id: str, upload_type: str, files: list) -> dict:
        """Initialize a multipart upload for asset files or preview."""
        from ..constants import API_UPLOADS
        
        endpoint = API_UPLOADS
        data = {
            "databaseId": database_id,
            "assetId": asset_id,
            "uploadType": upload_type,
            "files": files
        }
        
        try:
            response = self.post(endpoint, data=data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid upload data: {error_message}")
                
            elif e.response.status_code == 404:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Upload initialization failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to initialize upload: {e}")

    def complete_upload(self, upload_id: str, database_id: str, asset_id: str, upload_type: str, files: list) -> dict:
        """Complete a multipart upload.
        
        Note: 503 Service Unavailable responses are treated as successful asynchronous operations.
        The backend will process the upload completion asynchronously when it returns 503.
        """
        from ..constants import API_UPLOADS_COMPLETE
        
        endpoint = API_UPLOADS_COMPLETE.format(uploadId=upload_id)
        data = {
            "databaseId": database_id,
            "assetId": asset_id,
            "uploadType": upload_type,
            "files": files
        }
        
        try:
            response = self.post(endpoint, data=data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 503:
                # 503 Service Unavailable means backend will process asynchronously
                # This is considered a success - return a synthetic success response
                return {
                    "message": "Upload completion accepted for asynchronous processing",
                    "overallSuccess": True,
                    "asynchronousProcessing": True,
                    "uploadId": upload_id,
                    "note": "Processing times are undergoing expected throttling. Your upload will be processed asynchronously."
                }
                
            elif e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid completion data: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'upload' in error_message.lower():
                    raise FileUploadError(f"Upload '{upload_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code == 409:
                # Some files failed but others may have succeeded
                error_data = e.response.json() if e.response.content else {}
                return error_data  # Return the partial success response
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Upload completion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to complete upload: {e}")

    # File Management API Methods

    def create_folder(self, database_id: str, asset_id: str, folder_data: Dict[str, Any]) -> Dict[str, Any]:
        """Create a folder in an asset."""
        try:
            endpoint = API_CREATE_FOLDER.format(databaseId=database_id, assetId=asset_id)
            response = self.post(endpoint, data=folder_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid folder data: {error_message}")
                
            elif e.response.status_code == 404:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Folder creation failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to create folder: {e}")

    def list_asset_files(self, database_id: str, asset_id: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """List files in an asset."""
        try:
            endpoint = API_LIST_FILES.format(databaseId=database_id, assetId=asset_id)
            response = self.get(endpoint, include_auth=True, params=params or {})
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list files: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list asset files: {e}")

    def get_file_info(self, database_id: str, asset_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """Get detailed information about a specific file."""
        try:
            endpoint = API_FILE_INFO.format(databaseId=database_id, assetId=asset_id)
            response = self.get(endpoint, include_auth=True, params=params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get file info: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get file information: {e}")

    def move_file(self, database_id: str, asset_id: str, move_data: Dict[str, Any]) -> Dict[str, Any]:
        """Move a file within an asset."""
        try:
            endpoint = API_MOVE_FILE.format(databaseId=database_id, assetId=asset_id)
            response = self.post(endpoint, data=move_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid move operation: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"File move failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to move file: {e}")

    def copy_file(self, database_id: str, asset_id: str, copy_data: Dict[str, Any]) -> Dict[str, Any]:
        """Copy a file within an asset or to another asset."""
        try:
            endpoint = API_COPY_FILE.format(databaseId=database_id, assetId=asset_id)
            response = self.post(endpoint, data=copy_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid copy operation: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"File copy failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to copy file: {e}")

    def archive_file(self, database_id: str, asset_id: str, archive_data: Dict[str, Any]) -> Dict[str, Any]:
        """Archive a file (soft delete)."""
        try:
            endpoint = API_ARCHIVE_FILE.format(databaseId=database_id, assetId=asset_id)
            response = self.delete(endpoint, include_auth=True, json=archive_data)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid archive operation: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"File archive failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to archive file: {e}")

    def unarchive_file(self, database_id: str, asset_id: str, unarchive_data: Dict[str, Any]) -> Dict[str, Any]:
        """Unarchive a previously archived file."""
        try:
            endpoint = API_UNARCHIVE_FILE.format(databaseId=database_id, assetId=asset_id)
            response = self.post(endpoint, data=unarchive_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid unarchive operation: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"File unarchive failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to unarchive file: {e}")

    def delete_asset_preview(self, database_id: str, asset_id: str) -> Dict[str, Any]:
        """Delete the asset preview file."""
        try:
            endpoint = API_DELETE_ASSET_PREVIEW.format(databaseId=database_id, assetId=asset_id)
            response = self.delete(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'preview' in error_message.lower():
                    raise APIError(f"Asset preview not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset preview deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete asset preview: {e}")

    def delete_auxiliary_preview_files(self, database_id: str, asset_id: str, delete_data: Dict[str, Any]) -> Dict[str, Any]:
        """Delete auxiliary preview asset files."""
        try:
            endpoint = API_DELETE_AUXILIARY_PREVIEW.format(databaseId=database_id, assetId=asset_id)
            response = self.delete(endpoint, include_auth=True, json=delete_data)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid delete operation: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'auxiliary' in error_message.lower() or 'preview' in error_message.lower():
                    raise APIError(f"Auxiliary files not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Auxiliary files deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete auxiliary preview files: {e}")

    def delete_file(self, database_id: str, asset_id: str, delete_data: Dict[str, Any]) -> Dict[str, Any]:
        """Permanently delete a file or files under a prefix."""
        try:
            endpoint = API_DELETE_FILE.format(databaseId=database_id, assetId=asset_id)
            response = self.delete(endpoint, include_auth=True, json=delete_data)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid delete operation: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"File deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete file: {e}")

    def revert_file_version(self, database_id: str, asset_id: str, version_id: str, revert_data: Dict[str, Any]) -> Dict[str, Any]:
        """Revert a file to a previous version."""
        try:
            endpoint = API_REVERT_FILE_VERSION.format(databaseId=database_id, assetId=asset_id, versionId=version_id)
            response = self.post(endpoint, data=revert_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid revert operation: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'version' in error_message.lower() or 'file' in error_message.lower():
                    raise APIError(f"File or version not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"File revert failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to revert file version: {e}")

    def set_primary_file(self, database_id: str, asset_id: str, primary_data: Dict[str, Any]) -> Dict[str, Any]:
        """Set or remove primary type metadata for a file."""
        try:
            endpoint = API_SET_PRIMARY_FILE.format(databaseId=database_id, assetId=asset_id)
            response = self.put(endpoint, data=primary_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid primary file operation: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Set primary file failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to set primary file: {e}")

    # Asset Management API Methods

    def archive_asset(self, database_id: str, asset_id: str, reason: Optional[str] = None) -> Dict[str, Any]:
        """
        Archive an asset (soft delete) using the /database/{databaseId}/assets/{assetId}/archiveAsset DELETE endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            reason: Optional reason for archiving the asset
        
        Returns:
            API response data with operation result
        
        Raises:
            AssetNotFoundError: When asset is not found
            AssetAlreadyArchivedError: When asset is already archived
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            endpoint = API_ARCHIVE_ASSET.format(databaseId=database_id, assetId=asset_id)
            # Always send a body — the archive endpoint requires a non-empty request
            # body. confirmArchive signals intent; reason is optional.
            data = {'confirmArchive': True}
            if reason:
                data['reason'] = reason

            response = self.delete(endpoint, include_auth=True, json=data)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already archived' in error_message.lower():
                    raise AssetAlreadyArchivedError(f"Asset is already archived: {error_message}")
                else:
                    raise InvalidAssetDataError(f"Invalid archive operation: {error_message}")
                    
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset archive failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to archive asset: {e}")

    def unarchive_asset(self, database_id: str, asset_id: str, reason: Optional[str] = None,
                        unarchive_files: bool = False) -> Dict[str, Any]:
        """
        Unarchive an asset (restore from soft delete) using the
        /database/{databaseId}/assets/{assetId}/unarchiveAsset PUT endpoint.

        Restores the asset record to active state. Files remain archived unless
        unarchive_files is True, which also restores the files archived by the
        asset archive operation (files archived individually beforehand always
        stay archived).

        Args:
            database_id: Database ID (with or without the #deleted suffix)
            asset_id: Asset ID
            reason: Optional reason for unarchiving the asset
            unarchive_files: Also restore files archived by the asset archive

        Returns:
            API response data with operation result

        Raises:
            AssetNotFoundError: When asset is not found
            AssetNotArchivedError: When the asset is not archived
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            endpoint = API_UNARCHIVE_ASSET.format(databaseId=database_id, assetId=asset_id)
            data = {'confirmUnarchive': True}
            if reason:
                data['reason'] = reason
            if unarchive_files:
                data['unarchiveFiles'] = True

            response = self.put(endpoint, data=data)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))

                if 'not archived' in error_message.lower() or 'not in a valid archived state' in error_message.lower():
                    raise AssetNotArchivedError(f"Asset is not archived: {error_message}")
                else:
                    raise InvalidAssetDataError(f"Invalid unarchive operation: {error_message}")

            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))

                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset unarchive failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to unarchive asset: {e}")

    def delete_asset_permanent(self, database_id: str, asset_id: str, reason: Optional[str] = None, confirm: bool = False) -> Dict[str, Any]:
        """
        Permanently delete an asset using the /database/{databaseId}/assets/{assetId}/deleteAsset DELETE endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            reason: Optional reason for deleting the asset
            confirm: Confirmation flag for permanent deletion
        
        Returns:
            API response data with operation result
        
        Raises:
            AssetNotFoundError: When asset is not found
            AssetDeletionError: When deletion operation fails
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            endpoint = API_DELETE_ASSET.format(databaseId=database_id, assetId=asset_id)
            data = {
                'confirmPermanentDelete': confirm
            }
            if reason:
                data['reason'] = reason
                
            response = self.delete(endpoint, include_auth=True, json=data)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'confirmation' in error_message.lower() or 'confirm' in error_message.lower():
                    raise AssetDeletionError(f"Deletion confirmation required: {error_message}")
                else:
                    raise InvalidAssetDataError(f"Invalid delete operation: {error_message}")
                    
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete asset: {e}")

    # Database Management API Methods

    def create_database(self, database_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new database using the /database POST endpoint.
        
        Args:
            database_data: Database creation data with databaseId, description, defaultBucketId
        
        Returns:
            API response data with database creation result
        
        Raises:
            DatabaseAlreadyExistsError: When database already exists
            BucketNotFoundError: When bucket doesn't exist
            InvalidDatabaseDataError: When database data is invalid
            APIError: When API call fails
        """
        try:
            response = self.post(API_DATABASE, data=database_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already exists' in error_message.lower():
                    raise DatabaseAlreadyExistsError(f"Database already exists: {error_message}")
                elif 'bucket' in error_message.lower() and 'not found' in error_message.lower():
                    raise BucketNotFoundError(f"Bucket not found: {error_message}")
                else:
                    raise InvalidDatabaseDataError(f"Invalid database data: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Database creation failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to create database: {e}")

    def update_database(self, database_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing database using the /database/{databaseId} PUT endpoint.
        
        Args:
            database_data: Database update data with databaseId and optional fields:
                - description: New database description
                - defaultBucketId: New default bucket ID
                - restrictMetadataOutsideSchemas: Enable/disable metadata restriction
                - restrictFileUploadsToExtensions: Set allowed file extensions
        
        Returns:
            API response data with database update result
        
        Raises:
            DatabaseNotFoundError: When database doesn't exist
            BucketNotFoundError: When bucket doesn't exist
            InvalidDatabaseDataError: When database data is invalid
            APIError: When API call fails
        """
        try:
            # Extract database_id and build endpoint
            database_id = database_data.get('databaseId')
            if not database_id:
                raise InvalidDatabaseDataError("databaseId is required for update")
            
            endpoint = API_DATABASE_BY_ID.format(databaseId=database_id)
            
            # Remove databaseId from data as it's in the path
            update_data = {k: v for k, v in database_data.items() if k != 'databaseId'}
            
            response = self.put(endpoint, data=update_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'bucket' in error_message.lower() and 'not found' in error_message.lower():
                    raise BucketNotFoundError(f"Bucket not found: {error_message}")
                else:
                    raise InvalidDatabaseDataError(f"Invalid database data: {error_message}")
                    
            elif e.response.status_code == 404:
                raise DatabaseNotFoundError("Database not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Database update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update database: {e}")

    def get_database(self, database_id: str, show_deleted: bool = False) -> Dict[str, Any]:
        """
        Get a database using the /database/{databaseId} GET endpoint.
        
        Args:
            database_id: Database ID
            show_deleted: Whether to include deleted databases
        
        Returns:
            API response data with database details
        
        Raises:
            DatabaseNotFoundError: When database is not found
            APIError: When API call fails
        """
        try:
            endpoint = API_DATABASE_BY_ID.format(databaseId=database_id)
            params = {}
            if show_deleted:
                params['showDeleted'] = 'true'
                
            response = self.get(endpoint, include_auth=True, params=params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise DatabaseNotFoundError(f"Database '{database_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get database: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get database: {e}")

    def list_databases(self, show_deleted: bool = False, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        List databases using the /database GET endpoint.
        
        Args:
            show_deleted: Whether to include deleted databases
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
        
        Returns:
            API response data with databases list
        
        Raises:
            APIError: When API call fails
        """
        try:
            query_params = params or {}
            if show_deleted:
                query_params['showDeleted'] = 'true'
                
            response = self.get(API_DATABASE, include_auth=True, params=query_params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list databases: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list databases: {e}")

    def delete_database(self, database_id: str) -> Dict[str, Any]:
        """
        Delete a database using the /database/{databaseId} DELETE endpoint.
        
        Args:
            database_id: Database ID
        
        Returns:
            API response data with deletion result
        
        Raises:
            DatabaseNotFoundError: When database is not found
            DatabaseDeletionError: When database contains active resources
            APIError: When API call fails
        """
        try:
            endpoint = API_DATABASE_BY_ID.format(databaseId=database_id)
            response = self.delete(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise DatabaseDeletionError(f"Database deletion failed: {error_message}")
                
            elif e.response.status_code == 404:
                raise DatabaseNotFoundError(f"Database '{database_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Database deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete database: {e}")

    def list_buckets(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        List S3 bucket configurations using the /buckets GET endpoint.
        
        Args:
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
        
        Returns:
            API response data with buckets list
        
        Raises:
            APIError: When API call fails
        """
        try:
            query_params = params or {}
            response = self.get(API_BUCKETS, include_auth=True, params=query_params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list buckets: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list buckets: {e}")

    # Tag Management API Methods

    def get_tags(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        List all tags using the /tags GET endpoint.
        
        Args:
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
        
        Returns:
            API response data with tags list
        
        Raises:
            APIError: When API call fails
        """
        try:
            query_params = params or {}
            response = self.get(API_TAGS, include_auth=True, params=query_params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list tags: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list tags: {e}")

    def create_tags(self, tags_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create new tags using the /tags POST endpoint.
        
        Args:
            tags_data: Tags creation data with tags array
        
        Returns:
            API response data with creation result
        
        Raises:
            TagAlreadyExistsError: When tag already exists
            TagTypeNotFoundError: When tag type doesn't exist
            InvalidTagDataError: When tag data is invalid
            APIError: When API call fails
        """
        try:
            response = self.post(API_TAGS, data=tags_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already exists' in error_message.lower():
                    raise TagAlreadyExistsError(f"Tag already exists: {error_message}")
                elif 'tagtype' in error_message.lower() and "doesn't exist" in error_message.lower():
                    raise TagTypeNotFoundError(f"Tag type not found: {error_message}")
                else:
                    raise InvalidTagDataError(f"Invalid tag data: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Tag creation failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to create tags: {e}")

    def update_tags(self, tags_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update existing tags using the /tags PUT endpoint.
        
        Args:
            tags_data: Tags update data with tags array
        
        Returns:
            API response data with update result
        
        Raises:
            TagNotFoundError: When tag is not found
            TagTypeNotFoundError: When tag type doesn't exist
            InvalidTagDataError: When tag data is invalid
            APIError: When API call fails
        """
        try:
            response = self.put(API_TAGS, data=tags_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'tagname or tagtype' in error_message.lower() and "don't exist" in error_message.lower():
                    if 'tagtype' in error_message.lower():
                        raise TagTypeNotFoundError(f"Tag type not found: {error_message}")
                    else:
                        raise TagNotFoundError(f"Tag not found: {error_message}")
                else:
                    raise InvalidTagDataError(f"Invalid tag data: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Tag update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update tags: {e}")

    def delete_tag(self, tag_id: str) -> Dict[str, Any]:
        """
        Delete a tag using the /tags/{tagId} DELETE endpoint.
        
        Args:
            tag_id: Tag ID (tag name)
        
        Returns:
            API response data with deletion result
        
        Raises:
            TagNotFoundError: When tag is not found
            APIError: When API call fails
        """
        try:
            endpoint = API_TAG_DELETE.format(tagId=tag_id)
            response = self.delete(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidTagDataError(f"Invalid tag deletion: {error_message}")
                
            elif e.response.status_code == 404:
                raise TagNotFoundError(f"Tag '{tag_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Tag deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete tag: {e}")

    def get_tag_types(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        List all tag types using the /tag-types GET endpoint.
        
        Args:
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
        
        Returns:
            API response data with tag types list
        
        Raises:
            APIError: When API call fails
        """
        try:
            query_params = params or {}
            response = self.get(API_TAG_TYPES, include_auth=True, params=query_params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list tag types: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list tag types: {e}")

    def create_tag_types(self, tag_types_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create new tag types using the /tag-types POST endpoint.
        
        Args:
            tag_types_data: Tag types creation data with tagTypes array
        
        Returns:
            API response data with creation result
        
        Raises:
            TagTypeAlreadyExistsError: When tag type already exists
            InvalidTagTypeDataError: When tag type data is invalid
            APIError: When API call fails
        """
        try:
            response = self.post(API_TAG_TYPES, data=tag_types_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already exists' in error_message.lower():
                    raise TagTypeAlreadyExistsError(f"Tag type already exists: {error_message}")
                else:
                    raise InvalidTagTypeDataError(f"Invalid tag type data: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Tag type creation failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to create tag types: {e}")

    def update_tag_types(self, tag_types_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update existing tag types using the /tag-types PUT endpoint.
        
        Args:
            tag_types_data: Tag types update data with tagTypes array
        
        Returns:
            API response data with update result
        
        Raises:
            TagTypeNotFoundError: When tag type is not found
            InvalidTagTypeDataError: When tag type data is invalid
            APIError: When API call fails
        """
        try:
            response = self.put(API_TAG_TYPES, data=tag_types_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidTagTypeDataError(f"Invalid tag type data: {error_message}")
                
            elif e.response.status_code == 404:
                raise TagTypeNotFoundError("Tag type not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Tag type update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update tag types: {e}")

    def delete_tag_type(self, tag_type_id: str) -> Dict[str, Any]:
        """
        Delete a tag type using the /tag-types/{tagTypeId} DELETE endpoint.
        
        Args:
            tag_type_id: Tag type ID (tag type name)
        
        Returns:
            API response data with deletion result
        
        Raises:
            TagTypeNotFoundError: When tag type is not found
            TagTypeInUseError: When tag type is currently in use by tags
            APIError: When API call fails
        """
        try:
            endpoint = API_TAG_TYPE_DELETE.format(tagTypeId=tag_type_id)
            response = self.delete(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'currently in use' in error_message.lower() or 'cannot delete' in error_message.lower():
                    raise TagTypeInUseError(f"Tag type is in use: {error_message}")
                else:
                    raise InvalidTagTypeDataError(f"Invalid tag type deletion: {error_message}")
                
            elif e.response.status_code == 404:
                raise TagTypeNotFoundError(f"Tag type '{tag_type_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Tag type deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete tag type: {e}")

    # Asset Version API Methods

    def create_asset_version(self, database_id: str, asset_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new asset version using the /database/{databaseId}/assets/{assetId}/createVersion POST endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            request_data: Version creation data matching CreateAssetVersionRequestModel
        
        Returns:
            API response data with version creation result
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            InvalidAssetVersionDataError: When version data is invalid
            AssetVersionOperationError: When version creation fails
            APIError: When API call fails
        """
        try:
            endpoint = API_CREATE_ASSET_VERSION.format(databaseId=database_id, assetId=asset_id)
            response = self.post(endpoint, data=request_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetVersionDataError(f"Invalid version data: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise AssetVersionOperationError(f"Asset version creation failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to create asset version: {e}")

    def revert_asset_version(self, database_id: str, asset_id: str, asset_version_id: str, request_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Revert to a previous asset version using the /database/{databaseId}/assets/{assetId}/revertAssetVersion/{assetVersionId} POST endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            asset_version_id: Asset version ID to revert to
            request_data: Optional revert data with comment
        
        Returns:
            API response data with revert operation result
        
        Raises:
            AssetNotFoundError: When asset is not found
            AssetVersionNotFoundError: When version is not found
            DatabaseNotFoundError: When database doesn't exist
            InvalidAssetVersionDataError: When revert data is invalid
            AssetVersionRevertError: When revert operation fails
            APIError: When API call fails
        """
        try:
            endpoint = API_REVERT_ASSET_VERSION.format(
                databaseId=database_id, 
                assetId=asset_id, 
                assetVersionId=asset_version_id
            )
            response = self.post(endpoint, data=request_data or {}, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetVersionDataError(f"Invalid revert data: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'version' in error_message.lower():
                    raise AssetVersionNotFoundError(f"Asset version '{asset_version_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise AssetVersionRevertError(f"Asset version revert failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to revert asset version: {e}")

    def get_asset_versions(self, database_id: str, asset_id: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Get all versions for an asset using the /database/{databaseId}/assets/{assetId}/getVersions GET endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
        
        Returns:
            API response data with versions list
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            endpoint = API_GET_ASSET_VERSIONS.format(databaseId=database_id, assetId=asset_id)
            query_params = params or {}
            response = self.get(endpoint, include_auth=True, params=query_params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get asset versions: {e}")

        except Exception as e:
            raise APIError(f"Failed to get asset versions: {e}")

    def get_asset_history(self, database_id: str, asset_id: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        Get lifecycle history records for an asset using the /database/{databaseId}/assets/{assetId}/assetHistory GET endpoint.

        Args:
            database_id: Database ID
            asset_id: Asset ID
            params: Optional pagination parameters (pageSize, startingToken)

        Returns:
            API response data with history records list

        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            endpoint = API_GET_ASSET_HISTORY.format(databaseId=database_id, assetId=asset_id)
            query_params = params or {}
            response = self.get(endpoint, include_auth=True, params=query_params)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))

                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get asset history: {e}")

        except Exception as e:
            raise APIError(f"Failed to get asset history: {e}")

    def get_asset_version(self, database_id: str, asset_id: str, asset_version_id: str) -> Dict[str, Any]:
        """
        Get details for a specific asset version using the /database/{databaseId}/assets/{assetId}/getVersion/{assetVersionId} GET endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            asset_version_id: Asset version ID
        
        Returns:
            API response data with version details
        
        Raises:
            AssetNotFoundError: When asset is not found
            AssetVersionNotFoundError: When version is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            endpoint = API_GET_ASSET_VERSION.format(
                databaseId=database_id, 
                assetId=asset_id, 
                assetVersionId=asset_version_id
            )
            response = self.get(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'version' in error_message.lower():
                    raise AssetVersionNotFoundError(f"Asset version '{asset_version_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get asset version: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get asset version: {e}")

    def update_asset_version(self, database_id: str, asset_id: str, asset_version_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an asset version using the PUT /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId} endpoint.

        Args:
            database_id: Database ID
            asset_id: Asset ID
            asset_version_id: Asset version ID to update
            data: Update data (comment, versionAlias)

        Returns:
            API response data with update result

        Raises:
            AssetNotFoundError: When asset is not found
            AssetVersionNotFoundError: When version is not found
            DatabaseNotFoundError: When database doesn't exist
            InvalidAssetVersionDataError: When update data is invalid
            AssetVersionOperationError: When update operation fails
            APIError: When API call fails
        """
        try:
            endpoint = API_ASSET_VERSION_BY_ID.format(
                databaseId=database_id,
                assetId=asset_id,
                assetVersionId=asset_version_id
            )
            response = self.put(endpoint, data=data, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetVersionDataError(f"Invalid update data: {error_message}")

            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))

                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'version' in error_message.lower():
                    raise AssetVersionNotFoundError(f"Asset version '{asset_version_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise AssetVersionOperationError(f"Asset version update failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to update asset version: {e}")

    def archive_asset_version(self, database_id: str, asset_id: str, asset_version_id: str) -> Dict[str, Any]:
        """
        Archive an asset version using the POST /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/archive endpoint.

        Args:
            database_id: Database ID
            asset_id: Asset ID
            asset_version_id: Asset version ID to archive

        Returns:
            API response data with archive result

        Raises:
            AssetNotFoundError: When asset is not found
            AssetVersionNotFoundError: When version is not found
            DatabaseNotFoundError: When database doesn't exist
            AssetVersionArchiveError: When archive operation fails
            APIError: When API call fails
        """
        try:
            endpoint = API_ASSET_VERSION_ARCHIVE.format(
                databaseId=database_id,
                assetId=asset_id,
                assetVersionId=asset_version_id
            )
            response = self.post(endpoint, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetVersionArchiveError(f"Archive failed: {error_message}")

            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))

                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'version' in error_message.lower():
                    raise AssetVersionNotFoundError(f"Asset version '{asset_version_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise AssetVersionArchiveError(f"Asset version archive failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to archive asset version: {e}")

    def unarchive_asset_version(self, database_id: str, asset_id: str, asset_version_id: str) -> Dict[str, Any]:
        """
        Unarchive an asset version using the POST /database/{databaseId}/assets/{assetId}/assetversions/{assetVersionId}/unarchive endpoint.

        Args:
            database_id: Database ID
            asset_id: Asset ID
            asset_version_id: Asset version ID to unarchive

        Returns:
            API response data with unarchive result

        Raises:
            AssetNotFoundError: When asset is not found
            AssetVersionNotFoundError: When version is not found
            DatabaseNotFoundError: When database doesn't exist
            AssetVersionArchiveError: When unarchive operation fails
            APIError: When API call fails
        """
        try:
            endpoint = API_ASSET_VERSION_UNARCHIVE.format(
                databaseId=database_id,
                assetId=asset_id,
                assetVersionId=asset_version_id
            )
            response = self.post(endpoint, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetVersionArchiveError(f"Unarchive failed: {error_message}")

            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))

                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'version' in error_message.lower():
                    raise AssetVersionNotFoundError(f"Asset version '{asset_version_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise AssetVersionArchiveError(f"Asset version unarchive failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to unarchive asset version: {e}")

    # Asset Links API Methods

    def create_asset_link(self, link_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new asset link using the /asset-links POST endpoint.
        
        Args:
            link_data: Asset link creation data matching CreateAssetLinkRequestModel
        
        Returns:
            API response data with asset link creation result
        
        Raises:
            AssetLinkValidationError: When link data is invalid
            AssetLinkAlreadyExistsError: When link already exists
            CycleDetectionError: When creating link would create a cycle
            AssetLinkPermissionError: When user lacks permissions
            AssetNotFoundError: When one or both assets don't exist
            APIError: When API call fails
        """
        try:
            response = self.post(API_ASSET_LINKS, data=link_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already exists' in error_message.lower():
                    raise AssetLinkAlreadyExistsError(f"Asset link already exists: {error_message}")
                elif 'cycle' in error_message.lower():
                    raise CycleDetectionError(f"Creating link would create cycle: {error_message}")
                elif 'not exist' in error_message.lower() or 'not found' in error_message.lower():
                    raise AssetNotFoundError(f"Asset not found: {error_message}")
                else:
                    raise AssetLinkValidationError(f"Invalid asset link data: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkPermissionError(f"Not authorized to create asset link: {error_message}")
            else:
                raise APIError(f"Asset link creation failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to create asset link: {e}")

    def get_single_asset_link(self, asset_link_id: str) -> Dict[str, Any]:
        """
        Get a single asset link using the /asset-links/single/{assetLinkId} GET endpoint.
        
        Args:
            asset_link_id: Asset link ID
        
        Returns:
            API response data with asset link details
        
        Raises:
            AssetLinkNotFoundError: When asset link is not found
            AssetLinkPermissionError: When user lacks permissions
            APIError: When API call fails
        """
        try:
            endpoint = API_ASSET_LINKS_SINGLE.format(assetLinkId=asset_link_id)
            response = self.get(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise AssetLinkNotFoundError(f"Asset link '{asset_link_id}' not found")
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkPermissionError(f"Not authorized to view asset link: {error_message}")
            else:
                raise APIError(f"Failed to get asset link: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get asset link: {e}")

    def update_asset_link(self, asset_link_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an asset link using the /asset-links/{assetLinkId} PUT endpoint.
        
        Args:
            asset_link_id: Asset link ID
            update_data: Asset link update data matching UpdateAssetLinkRequestModel
        
        Returns:
            API response data with update result
        
        Raises:
            AssetLinkNotFoundError: When asset link is not found
            AssetLinkValidationError: When update data is invalid
            AssetLinkPermissionError: When user lacks permissions
            APIError: When API call fails
        """
        try:
            endpoint = API_ASSET_LINKS_UPDATE.format(assetLinkId=asset_link_id)
            response = self.put(endpoint, data=update_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkValidationError(f"Invalid update data: {error_message}")
                
            elif e.response.status_code == 404:
                raise AssetLinkNotFoundError(f"Asset link '{asset_link_id}' not found")
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkPermissionError(f"Not authorized to update asset link: {error_message}")
            else:
                raise APIError(f"Asset link update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update asset link: {e}")

    def delete_asset_link(self, asset_link_id: str) -> Dict[str, Any]:
        """
        Delete an asset link using the /asset-links/{relationId} DELETE endpoint.
        
        Args:
            asset_link_id: Asset link ID (called relationId in the API for backwards compatibility)
        
        Returns:
            API response data with deletion result
        
        Raises:
            AssetLinkNotFoundError: When asset link is not found
            AssetLinkPermissionError: When user lacks permissions
            APIError: When API call fails
        """
        try:
            endpoint = API_ASSET_LINKS_DELETE.format(assetLinkId=asset_link_id)
            response = self.delete(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise AssetLinkNotFoundError(f"Asset link '{asset_link_id}' not found")
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkPermissionError(f"Not authorized to delete asset link: {error_message}")
            else:
                raise APIError(f"Asset link deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete asset link: {e}")

    def get_asset_links_for_asset(self, database_id: str, asset_id: str, child_tree_view: bool = False) -> Dict[str, Any]:
        """
        Get asset links for a specific asset using the /database/{databaseId}/assets/{assetId}/asset-links GET endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            child_tree_view: Whether to return children as a tree structure
        
        Returns:
            API response data with asset links (related, parents, children)
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            AssetLinkPermissionError: When user lacks permissions
            APIError: When API call fails
        """
        try:
            endpoint = API_ASSET_LINKS_FOR_ASSET.format(databaseId=database_id, assetId=asset_id)
            params = {}
            if child_tree_view:
                params['childTreeView'] = 'true'
                
            response = self.get(endpoint, include_auth=True, params=params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkValidationError(f"Invalid parameters: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkPermissionError(f"Not authorized to view asset links: {error_message}")
            else:
                raise APIError(f"Failed to get asset links: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get asset links for asset: {e}")

    # Unified Metadata API Methods (v2.2+)

    def get_asset_metadata_v2(self, database_id: str, asset_id: str, page_size: int = 3000, starting_token: str = None, asset_version_id: str = None) -> Dict[str, Any]:
        """
        Get metadata for an asset using the new unified API.

        Args:
            database_id: Database ID
            asset_id: Asset ID
            page_size: Page size for pagination (default: 3000)
            starting_token: Token for pagination
            asset_version_id: Optional asset version ID to retrieve metadata snapshot

        Returns:
            API response with metadata list and optional NextToken

        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            from ..constants import API_ASSET_METADATA
            endpoint = API_ASSET_METADATA.format(databaseId=database_id, assetId=asset_id)
            params = {'pageSize': page_size}
            if starting_token:
                params['startingToken'] = starting_token
            if asset_version_id:
                params['assetVersionId'] = asset_version_id
                
            response = self.get(endpoint, include_auth=True, params=params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get asset metadata: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get asset metadata: {e}")

    def update_asset_metadata_v2(self, database_id: str, asset_id: str, metadata_items: list, update_type: str = 'update') -> Dict[str, Any]:
        """
        Create or update metadata for an asset using the new unified API (bulk operation).
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            metadata_items: List of metadata items [{"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}]
            update_type: 'update' (default, upsert) or 'replace_all' (replace all metadata)
        
        Returns:
            BulkOperationResponseModel with operation results
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            InvalidAssetDataError: When metadata data is invalid
            APIError: When API call fails
        """
        try:
            from ..constants import API_ASSET_METADATA
            endpoint = API_ASSET_METADATA.format(databaseId=database_id, assetId=asset_id)
            data = {
                'metadata': metadata_items,
                'updateType': update_type
            }
            
            response = self.put(endpoint, data=data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid metadata data: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset metadata update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update asset metadata: {e}")

    def delete_asset_metadata_v2(self, database_id: str, asset_id: str, metadata_keys: list) -> Dict[str, Any]:
        """
        Delete metadata for an asset using the new unified API (bulk operation).
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            metadata_keys: List of metadata keys to delete
        
        Returns:
            BulkOperationResponseModel with operation results
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            from ..constants import API_ASSET_METADATA
            endpoint = API_ASSET_METADATA.format(databaseId=database_id, assetId=asset_id)
            data = {'metadataKeys': metadata_keys}
            
            response = self.delete(endpoint, include_auth=True, json=data)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset metadata deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete asset metadata: {e}")

    def get_file_metadata_v2(self, database_id: str, asset_id: str, file_path: str, metadata_type: str = 'metadata',
                            page_size: int = 3000, starting_token: str = None, asset_version_id: str = None) -> Dict[str, Any]:
        """
        Get metadata or attributes for a file using the new unified API.

        Args:
            database_id: Database ID
            asset_id: Asset ID
            file_path: Relative file path
            metadata_type: 'metadata' or 'attribute'
            page_size: Page size for pagination (default: 3000)
            starting_token: Token for pagination
            asset_version_id: Optional asset version ID to retrieve metadata snapshot

        Returns:
            API response with metadata list and optional NextToken

        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails or file not found
        """
        try:
            from ..constants import API_FILE_METADATA
            endpoint = API_FILE_METADATA.format(databaseId=database_id, assetId=asset_id)
            params = {
                'filePath': file_path,
                'type': metadata_type,
                'pageSize': page_size
            }
            if starting_token:
                params['startingToken'] = starting_token
            if asset_version_id:
                params['assetVersionId'] = asset_version_id
                
            response = self.get(endpoint, include_auth=True, params=params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get file metadata: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get file metadata: {e}")

    def update_file_metadata_v2(self, database_id: str, asset_id: str, file_path: str, metadata_type: str, 
                               metadata_items: list, update_type: str = 'update') -> Dict[str, Any]:
        """
        Create or update metadata/attributes for a file using the new unified API (bulk operation).
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            file_path: Relative file path
            metadata_type: 'metadata' or 'attribute'
            metadata_items: List of metadata items [{"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}]
            update_type: 'update' (default, upsert) or 'replace_all' (replace all metadata)
        
        Returns:
            BulkOperationResponseModel with operation results
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            InvalidAssetDataError: When metadata data is invalid
            APIError: When API call fails or file not found
        """
        try:
            from ..constants import API_FILE_METADATA
            endpoint = API_FILE_METADATA.format(databaseId=database_id, assetId=asset_id)
            data = {
                'filePath': file_path,
                'type': metadata_type,
                'metadata': metadata_items,
                'updateType': update_type
            }
            
            response = self.put(endpoint, data=data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid metadata data: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"File metadata update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update file metadata: {e}")

    def delete_file_metadata_v2(self, database_id: str, asset_id: str, file_path: str, metadata_type: str, metadata_keys: list) -> Dict[str, Any]:
        """
        Delete metadata/attributes for a file using the new unified API (bulk operation).
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            file_path: Relative file path
            metadata_type: 'metadata' or 'attribute'
            metadata_keys: List of metadata keys to delete
        
        Returns:
            BulkOperationResponseModel with operation results
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails or file not found
        """
        try:
            from ..constants import API_FILE_METADATA
            endpoint = API_FILE_METADATA.format(databaseId=database_id, assetId=asset_id)
            data = {
                'filePath': file_path,
                'type': metadata_type,
                'metadataKeys': metadata_keys
            }
            
            response = self.delete(endpoint, include_auth=True, json=data)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"File metadata deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete file metadata: {e}")

    def get_asset_link_metadata_v2(self, asset_link_id: str, page_size: int = 3000, starting_token: str = None) -> Dict[str, Any]:
        """
        Get metadata for an asset link using the new unified API.
        
        Args:
            asset_link_id: Asset link ID
            page_size: Page size for pagination (default: 3000)
            starting_token: Token for pagination
        
        Returns:
            API response with metadata list and optional NextToken
        
        Raises:
            AssetLinkNotFoundError: When asset link is not found
            AssetLinkPermissionError: When user lacks permissions
            APIError: When API call fails
        """
        try:
            from ..constants import API_ASSET_LINK_METADATA
            endpoint = API_ASSET_LINK_METADATA.format(assetLinkId=asset_link_id)
            params = {'pageSize': page_size}
            if starting_token:
                params['startingToken'] = starting_token
                
            response = self.get(endpoint, include_auth=True, params=params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise AssetLinkNotFoundError(f"Asset link '{asset_link_id}' not found")
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkPermissionError(f"Not authorized to view metadata for this asset link: {error_message}")
            else:
                raise APIError(f"Failed to get asset link metadata: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get asset link metadata: {e}")

    def update_asset_link_metadata_v2(self, asset_link_id: str, metadata_items: list, update_type: str = 'update') -> Dict[str, Any]:
        """
        Create or update metadata for an asset link using the new unified API (bulk operation).
        
        Args:
            asset_link_id: Asset link ID
            metadata_items: List of metadata items [{"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}]
            update_type: 'update' (default, upsert) or 'replace_all' (replace all metadata)
        
        Returns:
            BulkOperationResponseModel with operation results
        
        Raises:
            AssetLinkNotFoundError: When asset link is not found
            AssetLinkValidationError: When metadata data is invalid
            AssetLinkPermissionError: When user lacks permissions
            APIError: When API call fails
        """
        try:
            from ..constants import API_ASSET_LINK_METADATA
            endpoint = API_ASSET_LINK_METADATA.format(assetLinkId=asset_link_id)
            data = {
                'metadata': metadata_items,
                'updateType': update_type
            }
            
            response = self.put(endpoint, data=data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkValidationError(f"Invalid metadata data: {error_message}")
                
            elif e.response.status_code == 404:
                raise AssetLinkNotFoundError(f"Asset link '{asset_link_id}' not found")
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkPermissionError(f"Not authorized to update metadata for this asset link: {error_message}")
            else:
                raise APIError(f"Asset link metadata update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update asset link metadata: {e}")

    def delete_asset_link_metadata_v2(self, asset_link_id: str, metadata_keys: list) -> Dict[str, Any]:
        """
        Delete metadata for an asset link using the new unified API (bulk operation).
        
        Args:
            asset_link_id: Asset link ID
            metadata_keys: List of metadata keys to delete
        
        Returns:
            BulkOperationResponseModel with operation results
        
        Raises:
            AssetLinkNotFoundError: When asset link is not found
            AssetLinkPermissionError: When user lacks permissions
            APIError: When API call fails
        """
        try:
            from ..constants import API_ASSET_LINK_METADATA
            endpoint = API_ASSET_LINK_METADATA.format(assetLinkId=asset_link_id)
            data = {'metadataKeys': metadata_keys}
            
            response = self.delete(endpoint, include_auth=True, json=data)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise AssetLinkNotFoundError(f"Asset link '{asset_link_id}' not found")
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AssetLinkPermissionError(f"Not authorized to delete metadata for this asset link: {error_message}")
            else:
                raise APIError(f"Asset link metadata deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete asset link metadata: {e}")

    def get_database_metadata_v2(self, database_id: str, page_size: int = 3000, starting_token: str = None) -> Dict[str, Any]:
        """
        Get metadata for a database using the new unified API.
        
        Args:
            database_id: Database ID
            page_size: Page size for pagination (default: 3000)
            starting_token: Token for pagination
        
        Returns:
            API response with metadata list and optional NextToken
        
        Raises:
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            from ..constants import API_DATABASE_METADATA
            endpoint = API_DATABASE_METADATA.format(databaseId=database_id)
            params = {'pageSize': page_size}
            if starting_token:
                params['startingToken'] = starting_token
                
            response = self.get(endpoint, include_auth=True, params=params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise DatabaseNotFoundError(f"Database '{database_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get database metadata: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get database metadata: {e}")

    def update_database_metadata_v2(self, database_id: str, metadata_items: list, update_type: str = 'update') -> Dict[str, Any]:
        """
        Create or update metadata for a database using the new unified API (bulk operation).
        
        Args:
            database_id: Database ID
            metadata_items: List of metadata items [{"metadataKey": "k", "metadataValue": "v", "metadataValueType": "string"}]
            update_type: 'update' (default, upsert) or 'replace_all' (replace all metadata)
        
        Returns:
            BulkOperationResponseModel with operation results
        
        Raises:
            DatabaseNotFoundError: When database doesn't exist
            InvalidDatabaseDataError: When metadata data is invalid
            APIError: When API call fails
        """
        try:
            from ..constants import API_DATABASE_METADATA
            endpoint = API_DATABASE_METADATA.format(databaseId=database_id)
            data = {
                'metadata': metadata_items,
                'updateType': update_type
            }
            
            response = self.put(endpoint, data=data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidDatabaseDataError(f"Invalid metadata data: {error_message}")
                
            elif e.response.status_code == 404:
                raise DatabaseNotFoundError(f"Database '{database_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Database metadata update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update database metadata: {e}")

    def delete_database_metadata_v2(self, database_id: str, metadata_keys: list) -> Dict[str, Any]:
        """
        Delete metadata for a database using the new unified API (bulk operation).
        
        Args:
            database_id: Database ID
            metadata_keys: List of metadata keys to delete
        
        Returns:
            BulkOperationResponseModel with operation results
        
        Raises:
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails
        """
        try:
            from ..constants import API_DATABASE_METADATA
            endpoint = API_DATABASE_METADATA.format(databaseId=database_id)
            data = {'metadataKeys': metadata_keys}
            
            response = self.delete(endpoint, include_auth=True, json=data)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise DatabaseNotFoundError(f"Database '{database_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Database metadata deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete database metadata: {e}")

    # Metadata Schema API Methods

    def get_metadata_schema(self, database_id: str, max_items: int = 1000, page_size: int = 100, starting_token: str = None) -> Dict[str, Any]:
        """
        Get metadata schema for a database using the /metadataschema/{databaseId} GET endpoint.
        
        DEPRECATED: Use list_metadata_schemas() instead for the new V2 API.
        This method is kept for backward compatibility.
        
        Args:
            database_id: Database ID
            max_items: Maximum number of items to return (default: 1000)
            page_size: Number of items per page (default: 100)
            starting_token: Token for pagination (optional)
        
        Returns:
            API response data with metadata schema list
        
        Raises:
            DatabaseNotFoundError: When database doesn't exist
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            endpoint = API_METADATA_SCHEMA.format(databaseId=database_id)
            params = {
                'maxItems': max_items,
                'pageSize': page_size
            }
            if starting_token:
                params['startingToken'] = starting_token
                
            response = self.get(endpoint, include_auth=True, params=params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise DatabaseNotFoundError(f"Database '{database_id}' not found: {error_message}")
                
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AuthenticationError(f"Authentication failed: {error_message}")
            else:
                raise APIError(f"Failed to get metadata schema: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get metadata schema: {e}")

    def list_metadata_schemas(
        self,
        database_id: Optional[str] = None,
        metadata_entity_type: Optional[str] = None,
        max_items: int = 1000,
        page_size: int = 100,
        starting_token: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        List metadata schemas with optional filters using the /metadataschema GET endpoint.
        
        Args:
            database_id: Optional database ID to filter schemas
            metadata_entity_type: Optional entity type filter (databaseMetadata, assetMetadata, fileMetadata, fileAttribute, assetLinkMetadata)
            max_items: Maximum number of items to return (default: 1000)
            page_size: Number of items per page (default: 100)
            starting_token: Token for pagination (optional)
        
        Returns:
            API response data with metadata schemas list
        
        Raises:
            DatabaseNotFoundError: When database doesn't exist (if database_id provided)
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            endpoint = API_METADATA_SCHEMA_LIST
            params = {
                'maxItems': max_items,
                'pageSize': page_size
            }
            
            if database_id:
                params['databaseId'] = database_id
            if metadata_entity_type:
                params['metadataEntityType'] = metadata_entity_type
            if starting_token:
                params['startingToken'] = starting_token
                
            response = self.get(endpoint, include_auth=True, params=params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise APIError(f"Invalid parameters: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if database_id and 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found: {error_message}")
                else:
                    raise APIError(f"Metadata schemas not found: {error_message}")
                
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AuthenticationError(f"Authentication failed: {error_message}")
            else:
                raise APIError(f"Failed to list metadata schemas: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list metadata schemas: {e}")

    def get_metadata_schema_by_id(self, database_id: str, metadata_schema_id: str) -> Dict[str, Any]:
        """
        Get a specific metadata schema by ID using the /database/{databaseId}/metadataSchema/{metadataSchemaId} GET endpoint.
        
        Args:
            database_id: Database ID
            metadata_schema_id: Metadata schema ID
        
        Returns:
            API response data with metadata schema details
        
        Raises:
            DatabaseNotFoundError: When database doesn't exist
            APIError: When metadata schema is not found or API call fails
            AuthenticationError: When authentication fails
        """
        try:
            endpoint = API_METADATA_SCHEMA_BY_ID.format(
                databaseId=database_id,
                metadataSchemaId=metadata_schema_id
            )
            response = self.get(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found: {error_message}")
                else:
                    raise APIError(f"Metadata schema '{metadata_schema_id}' not found: {error_message}")
                
            elif e.response.status_code in [401, 403]:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise AuthenticationError(f"Authentication failed: {error_message}")
            else:
                raise APIError(f"Failed to get metadata schema: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get metadata schema by ID: {e}")

    # Asset Download API Methods

    def download_asset_file(self, database_id: str, asset_id: str, file_key: Optional[str] = None, version_id: Optional[str] = None,
                            asset_version_id: Optional[str] = None, asset_version_alias: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate presigned URL for downloading asset files using the /database/{databaseId}/assets/{assetId}/download POST endpoint.

        Args:
            database_id: Database ID
            asset_id: Asset ID
            file_key: Optional specific file key to download
            version_id: Optional version ID for specific version
            asset_version_id: Optional asset version ID to download files from a specific asset version
            asset_version_alias: Optional asset version alias to download files from a specific asset version by alias

        Returns:
            API response data with download URL and metadata

        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails or asset not distributable
        """
        try:
            endpoint = API_DOWNLOAD_ASSET.format(databaseId=database_id, assetId=asset_id)
            data = {
                "downloadType": "assetFile"
            }
            if file_key:
                data["key"] = file_key
            if version_id:
                data["versionId"] = version_id
            if asset_version_id:
                data["assetVersionId"] = asset_version_id
            if asset_version_alias:
                data["assetVersionIdAlias"] = asset_version_alias
                
            response = self.post(endpoint, data=data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise APIError(f"Invalid download request: {error_message}")
                
            elif e.response.status_code == 401:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'not distributable' in error_message.lower():
                    raise APIError(f"Asset not distributable: {error_message}")
                else:
                    raise AuthenticationError(f"Authentication failed: {e}")
                    
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'file' in error_message.lower():
                    raise APIError(f"File not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code == 410:
                # File version archived/deleted
                raise APIError("File version has been archived and cannot be downloaded")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset download failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to download asset file: {e}")

    def download_asset_files_bulk(self, database_id: str, asset_id: str, file_keys: List[Any],
                                  asset_version_id: Optional[str] = None,
                                  asset_version_alias: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate presigned URLs for multiple asset files in one request using the
        /database/{databaseId}/assets/{assetId}/download POST endpoint.

        Args:
            database_id: Database ID
            asset_id: Asset ID
            file_keys: File keys to generate URLs for (max MAX_DOWNLOAD_KEYS_PER_REQUEST).
                Each entry is either a relative-path string (latest version) or a
                {'key': str, 'versionId': str} dict to pin that file to a specific
                S3 version. Per-file versionIds are mutually exclusive with
                asset_version_id/asset_version_alias.
            asset_version_id: Optional asset version ID to pin all files to
            asset_version_alias: Optional asset version alias to pin all files to

        Returns:
            API response data with per-file entries under 'files'
            ({key, downloadUrl, versionId, success, error}).

        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails or asset not distributable
        """
        try:
            endpoint = API_DOWNLOAD_ASSET.format(databaseId=database_id, assetId=asset_id)
            data = {
                "downloadType": "assetFile",
                "keys": file_keys
            }
            if asset_version_id:
                data["assetVersionId"] = asset_version_id
            if asset_version_alias:
                data["assetVersionIdAlias"] = asset_version_alias

            response = self.post(endpoint, data=data, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise APIError(f"Invalid download request: {error_message}")
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset bulk download failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to generate bulk download URLs: {e}")

    def download_asset_preview(self, database_id: str, asset_id: str) -> Dict[str, Any]:
        """
        Generate presigned URL for downloading asset preview using the /database/{databaseId}/assets/{assetId}/download POST endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
        
        Returns:
            API response data with download URL and metadata
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            APIError: When API call fails, asset not distributable, or preview not found
        """
        try:
            endpoint = API_DOWNLOAD_ASSET.format(databaseId=database_id, assetId=asset_id)
            data = {
                "downloadType": "assetPreview"
            }
                
            response = self.post(endpoint, data=data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise APIError(f"Invalid download request: {error_message}")
                
            elif e.response.status_code == 401:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'not distributable' in error_message.lower():
                    raise APIError(f"Asset not distributable: {error_message}")
                else:
                    raise AuthenticationError(f"Authentication failed: {e}")
                    
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'preview' in error_message.lower():
                    raise APIError(f"Asset preview not found: {error_message}")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset preview download failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to download asset preview: {e}")

    # Asset Export API Methods

    def export_asset(self, database_id: str, asset_id: str, export_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Export comprehensive asset data with filtering options.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID (root asset for tree export)
            export_params: Export parameters matching AssetExportRequestModel:
                - generatePresignedUrls: Generate presigned URLs for files
                - includeFolderFiles: Include folder files
                - includeOnlyPrimaryTypeFiles: Only files with primaryType
                - includeFileMetadata: Include file metadata
                - includeAssetLinkMetadata: Include asset link metadata
                - includeAssetMetadata: Include asset metadata
                - fetchAssetRelationships: Fetch asset relationships
                - fetchEntireChildrenSubtrees: Fetch entire children subtrees
                - includeArchivedFiles: Include archived files
                - fileExtensions: Filter by file extensions
                - maxAssets: Max assets per page (1-1000)
                - startingToken: Pagination token
        
        Returns:
            API response with assets, relationships, and pagination info
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            InvalidAssetDataError: When export parameters are invalid
            APIError: When API call fails
        """
        try:
            endpoint = API_ASSET_EXPORT.format(databaseId=database_id, assetId=asset_id)
            # Backend expects POST with JSON body
            response = self.post(endpoint, data=export_params, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidAssetDataError(f"Invalid export parameters: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Asset export failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to export asset: {e}")

    # ------------------------------------------------------------------
    # Pipeline / Workflow / Execution V2 API Methods
    # ------------------------------------------------------------------
    # Shared helpers keep the per-endpoint methods small: _pwe_request issues the call and
    # returns the body with the {"message": ...} envelope every V2 handler emits left intact,
    # so callers decide whether to unwrap; _pwe_error_message extracts the handler's message
    # for the per-method HTTPError mapping.

    @staticmethod
    def _pwe_body(response: "requests.Response") -> Dict[str, Any]:
        """Return the JSON body of a pipeline/workflow/execution response (raw, envelope intact)."""
        return response.json()

    def _pwe_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Call a pipeline/workflow/execution route and return its raw body.

        The non-2xx stays a requests HTTPError so each method can map the status code and the
        handler's own message onto its domain exception.
        """
        verb = {'GET': self.get, 'POST': self.post, 'PUT': self.put, 'DELETE': self.delete}[method]
        response = verb(endpoint, include_auth=True, raise_http_errors=True, **kwargs)
        return self._pwe_body(response)

    @staticmethod
    def _pwe_error_message(e: "requests.exceptions.HTTPError") -> str:
        """Best-effort extraction of the handler's error message string."""
        try:
            data = e.response.json() if e.response is not None and e.response.content else {}
        except ValueError:
            return str(e)
        msg = data.get('message', str(e))
        # Some handlers nest structured errors under message (e.g. triggerTemplateErrors,
        # saveErrors). Flatten string lists into readable lines instead of raw JSON.
        if isinstance(msg, dict):
            lines = []
            for key, val in msg.items():
                if isinstance(val, list):
                    lines.extend(str(item) for item in val)
                else:
                    lines.append(f"{key}: {val}")
            if lines:
                return "\n".join(lines)
            try:
                return json.dumps(msg)
            except (TypeError, ValueError):
                return str(msg)
        if isinstance(msg, list):
            return "\n".join(str(item) for item in msg)
        return msg

    # ---- Pipeline CRUD ------------------------------------------------

    def list_pipelines(self, database_id: Optional[str] = None, include_archived: bool = False,
                       params: Dict[str, Any] = None) -> Dict[str, Any]:
        """List pipelines. GET /pipelines (all) or GET /database/{databaseId}/pipelines."""
        try:
            endpoint = (API_DATABASE_PIPELINES.format(databaseId=database_id)
                        if database_id else API_PIPELINES)
            query_params = dict(params or {})
            if include_archived:
                query_params['includeArchived'] = 'true'
            return self._pwe_request('GET', endpoint, params=query_params)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                if database_id:
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                raise APIError("Failed to list pipelines: the /pipelines route returned 404")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to list pipelines: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to list pipelines: {e}")

    def get_pipeline(self, database_id: str, pipeline_id: str,
                     include_archived: bool = False) -> Dict[str, Any]:
        """Get a pipeline (+ its templates). GET /database/{databaseId}/pipelines/{pipelineId}."""
        try:
            endpoint = API_DATABASE_PIPELINE.format(databaseId=database_id, pipelineId=pipeline_id)
            query_params = {'includeArchived': 'true'} if include_archived else {}
            return self._pwe_request('GET', endpoint, params=query_params)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise PipelineNotFoundError(f"Pipeline '{pipeline_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to get pipeline: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to get pipeline: {e}")

    def create_pipeline(self, database_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Create a pipeline. POST /database/{databaseId}/pipelines."""
        try:
            endpoint = API_DATABASE_PIPELINES.format(databaseId=database_id)
            return self._pwe_request('POST', endpoint, data=body)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                msg = self._pwe_error_message(e)
                if 'already exists' in msg.lower():
                    raise PipelineAlreadyExistsError(msg)
                raise InvalidPipelineDataError(msg)
            if e.response.status_code == 404:
                raise DatabaseNotFoundError(f"Database '{database_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to create pipeline: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to create pipeline: {e}")

    def update_pipeline(self, database_id: str, pipeline_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Update a pipeline. PUT /database/{databaseId}/pipelines/{pipelineId}."""
        try:
            endpoint = API_DATABASE_PIPELINE.format(databaseId=database_id, pipelineId=pipeline_id)
            return self._pwe_request('PUT', endpoint, data=body)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise PipelineNotFoundError(f"Pipeline '{pipeline_id}' not found")
            if e.response.status_code == 400:
                raise InvalidPipelineDataError(self._pwe_error_message(e))
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to update pipeline: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to update pipeline: {e}")

    def delete_pipeline(self, database_id: str, pipeline_id: str) -> Dict[str, Any]:
        """Archive (soft-delete) a pipeline. DELETE /database/{databaseId}/pipelines/{pipelineId}."""
        try:
            endpoint = API_DATABASE_PIPELINE.format(databaseId=database_id, pipelineId=pipeline_id)
            return self._pwe_request('DELETE', endpoint)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise PipelineNotFoundError(f"Pipeline '{pipeline_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to archive pipeline: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to archive pipeline: {e}")

    # ---- Pipeline templates -------------------------------------------

    def list_pipeline_templates(self, database_id: str, pipeline_id: str) -> Dict[str, Any]:
        """List a pipeline's templates. GET .../pipelines/{pipelineId}/templates.

        The handler returns one page plus a NextToken, so the pages are drained here and returned as
        a single Items list."""
        try:
            endpoint = API_PIPELINE_TEMPLATES.format(databaseId=database_id, pipelineId=pipeline_id)
            body = self._pwe_request('GET', endpoint)
            message = body.get('message') if isinstance(body, dict) else None
            if not isinstance(message, dict):
                return body
            items = list(message.get('Items') or [])
            next_token = message.get('NextToken')
            while next_token:
                page = self._pwe_request('GET', endpoint,
                                         params={'startingToken': next_token})
                page_message = page.get('message') if isinstance(page, dict) else None
                if not isinstance(page_message, dict):
                    break
                items.extend(page_message.get('Items') or [])
                next_token = page_message.get('NextToken')
            return {'message': {'Items': items}}
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise PipelineNotFoundError(f"Pipeline '{pipeline_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to list templates: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to list templates: {e}")

    def get_pipeline_template(self, database_id: str, pipeline_id: str, template_id: str) -> Dict[str, Any]:
        """Get a template (config body rehydrated). GET .../templates/{templateId}."""
        try:
            endpoint = API_PIPELINE_TEMPLATE.format(
                databaseId=database_id, pipelineId=pipeline_id, templateId=template_id)
            return self._pwe_request('GET', endpoint)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise PipelineTemplateNotFoundError(f"Template '{template_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to get template: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to get template: {e}")

    def create_pipeline_template(self, database_id: str, pipeline_id: str,
                                 body: Dict[str, Any]) -> Dict[str, Any]:
        """Create a template. POST .../pipelines/{pipelineId}/templates."""
        try:
            endpoint = API_PIPELINE_TEMPLATES.format(databaseId=database_id, pipelineId=pipeline_id)
            return self._pwe_request('POST', endpoint, data=body)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                msg = self._pwe_error_message(e)
                if 'already exists' in msg.lower():
                    raise PipelineTemplateAlreadyExistsError(msg)
                raise InvalidPipelineTemplateDataError(msg)
            if e.response.status_code == 404:
                raise PipelineNotFoundError(f"Pipeline '{pipeline_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to create template: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to create template: {e}")

    def update_pipeline_template(self, database_id: str, pipeline_id: str, template_id: str,
                                 body: Dict[str, Any]) -> Dict[str, Any]:
        """Update a template. PUT .../templates/{templateId}."""
        try:
            endpoint = API_PIPELINE_TEMPLATE.format(
                databaseId=database_id, pipelineId=pipeline_id, templateId=template_id)
            return self._pwe_request('PUT', endpoint, data=body)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise PipelineTemplateNotFoundError(f"Template '{template_id}' not found")
            if e.response.status_code == 400:
                raise InvalidPipelineTemplateDataError(self._pwe_error_message(e))
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to update template: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to update template: {e}")

    def delete_pipeline_template(self, database_id: str, pipeline_id: str, template_id: str) -> Dict[str, Any]:
        """Delete a template. DELETE .../templates/{templateId}."""
        try:
            endpoint = API_PIPELINE_TEMPLATE.format(
                databaseId=database_id, pipelineId=pipeline_id, templateId=template_id)
            return self._pwe_request('DELETE', endpoint)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise PipelineTemplateNotFoundError(f"Template '{template_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to delete template: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to delete template: {e}")

    # ---- Pipeline template tag schema ---------------------------------

    def get_pipeline_template_tag_schema(self, database_id: str, pipeline_id: str,
                                         template_id: str) -> Dict[str, Any]:
        """Get a template's tag schema. GET .../templates/{templateId}/tagSchema."""
        try:
            endpoint = API_PIPELINE_TEMPLATE_TAG_SCHEMA.format(
                databaseId=database_id, pipelineId=pipeline_id, templateId=template_id)
            return self._pwe_request('GET', endpoint)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise PipelineTemplateNotFoundError(f"Template '{template_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to get tag schema: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to get tag schema: {e}")

    def set_pipeline_template_tag_schema(self, database_id: str, pipeline_id: str, template_id: str,
                                         fields: list) -> Dict[str, Any]:
        """Set (replace) a template's tag schema. PUT .../templates/{templateId}/tagSchema."""
        try:
            endpoint = API_PIPELINE_TEMPLATE_TAG_SCHEMA.format(
                databaseId=database_id, pipelineId=pipeline_id, templateId=template_id)
            return self._pwe_request('PUT', endpoint, data={'fields': fields})
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise PipelineTemplateNotFoundError(f"Template '{template_id}' not found")
            if e.response.status_code == 400:
                raise InvalidPipelineTemplateDataError(self._pwe_error_message(e))
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to set tag schema: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to set tag schema: {e}")

    # ---- Workflow CRUD ------------------------------------------------

    def list_workflows(self, database_id: Optional[str] = None, include_archived: bool = False,
                       params: Dict[str, Any] = None) -> Dict[str, Any]:
        """List workflows. GET /workflows (all) or GET /database/{databaseId}/workflows."""
        try:
            endpoint = (API_DATABASE_WORKFLOWS.format(databaseId=database_id)
                        if database_id else API_WORKFLOWS)
            query_params = dict(params or {})
            if include_archived:
                query_params['includeArchived'] = 'true'
            return self._pwe_request('GET', endpoint, params=query_params)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise DatabaseNotFoundError(f"Database '{database_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to list workflows: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to list workflows: {e}")

    def get_workflow(self, database_id: str, workflow_id: str,
                     include_archived: bool = False) -> Dict[str, Any]:
        """Get a workflow (+ its triggers). GET /database/{databaseId}/workflows/{workflowId}."""
        try:
            endpoint = API_DATABASE_WORKFLOW.format(databaseId=database_id, workflowId=workflow_id)
            query_params = {'includeArchived': 'true'} if include_archived else {}
            return self._pwe_request('GET', endpoint, params=query_params)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to get workflow: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to get workflow: {e}")

    def create_workflow(self, database_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Create a workflow. POST /database/{databaseId}/workflows."""
        try:
            endpoint = API_DATABASE_WORKFLOWS.format(databaseId=database_id)
            return self._pwe_request('POST', endpoint, data=body)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                raise InvalidWorkflowDataError(self._pwe_error_message(e))
            if e.response.status_code == 404:
                raise DatabaseNotFoundError(f"Database '{database_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to create workflow: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to create workflow: {e}")

    def update_workflow(self, database_id: str, workflow_id: str, body: Dict[str, Any]) -> Dict[str, Any]:
        """Update a workflow. PUT /database/{databaseId}/workflows/{workflowId}."""
        try:
            endpoint = API_DATABASE_WORKFLOW.format(databaseId=database_id, workflowId=workflow_id)
            return self._pwe_request('PUT', endpoint, data=body)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
            if e.response.status_code == 400:
                raise InvalidWorkflowDataError(self._pwe_error_message(e))
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to update workflow: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to update workflow: {e}")

    def delete_workflow(self, database_id: str, workflow_id: str) -> Dict[str, Any]:
        """Archive (soft-delete) a workflow. DELETE /database/{databaseId}/workflows/{workflowId}."""
        try:
            endpoint = API_DATABASE_WORKFLOW.format(databaseId=database_id, workflowId=workflow_id)
            return self._pwe_request('DELETE', endpoint)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to archive workflow: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to archive workflow: {e}")

    # ---- Workflow triggers --------------------------------------------

    def list_workflow_triggers(self, database_id: str, workflow_id: str) -> Dict[str, Any]:
        """List a workflow's triggers. GET .../workflows/{workflowId}/triggers."""
        try:
            endpoint = API_WORKFLOW_TRIGGERS.format(databaseId=database_id, workflowId=workflow_id)
            return self._pwe_request('GET', endpoint)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to list triggers: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to list triggers: {e}")

    def get_workflow_trigger(self, database_id: str, workflow_id: str, trigger_type: str) -> Dict[str, Any]:
        """Get a workflow trigger. GET .../triggers/{triggerType}.

        `trigger_type` is the trigger's KEY: the bare type for a workflow's first trigger of that type,
        or 'type#triggerId' for an additional one. It is percent-encoded into the path because a raw '#'
        is a URL fragment delimiter — the server would receive only the bare type and act on the wrong
        trigger.
        """
        try:
            endpoint = API_WORKFLOW_TRIGGER.format(
                databaseId=database_id, workflowId=workflow_id,
                triggerType=quote(trigger_type, safe=''))
            return self._pwe_request('GET', endpoint)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise WorkflowTriggerNotFoundError(f"Trigger '{trigger_type}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to get trigger: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to get trigger: {e}")

    def set_workflow_trigger(self, database_id: str, workflow_id: str, trigger_type: str,
                             body: Dict[str, Any]) -> Dict[str, Any]:
        """Set (create/replace) a workflow trigger. PUT .../triggers/{triggerType}."""
        try:
            endpoint = API_WORKFLOW_TRIGGER.format(
                databaseId=database_id, workflowId=workflow_id,
                triggerType=quote(trigger_type, safe=''))
            return self._pwe_request('PUT', endpoint, data=body)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
            if e.response.status_code == 400:
                raise InvalidWorkflowTriggerDataError(self._pwe_error_message(e))
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to set trigger: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to set trigger: {e}")

    def delete_workflow_trigger(self, database_id: str, workflow_id: str, trigger_type: str) -> Dict[str, Any]:
        """Delete a workflow trigger. DELETE .../triggers/{triggerType}."""
        try:
            endpoint = API_WORKFLOW_TRIGGER.format(
                databaseId=database_id, workflowId=workflow_id,
                triggerType=quote(trigger_type, safe=''))
            return self._pwe_request('DELETE', endpoint)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise WorkflowTriggerNotFoundError(f"Trigger '{trigger_type}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to delete trigger: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to delete trigger: {e}")

    # ---- Execute + execution operations -------------------------------

    def execute_workflow(self, workflow_database_id: str, workflow_id: str,
                         body: Dict[str, Any]) -> Dict[str, Any]:
        """Execute a workflow (asset-less, multi-file). POST /workflows/{workflowDatabaseId}/{workflowId}/execute."""
        try:
            endpoint = API_EXECUTE_WORKFLOW.format(
                workflowDatabaseId=workflow_database_id, workflowId=workflow_id)
            return self._pwe_request('POST', endpoint, data=body)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                msg = self._pwe_error_message(e)
                if 'input file' in msg.lower():
                    raise WorkflowExecutionError(msg)
                raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
            if e.response.status_code == 400:
                msg = self._pwe_error_message(e)
                if 'already running' in msg.lower() or 'conflicting execution' in msg.lower():
                    raise WorkflowAlreadyRunningError(msg)
                raise WorkflowExecutionError(msg)
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise WorkflowExecutionError(f"Workflow execution failed: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to execute workflow: {e}")

    def list_workflow_executions(self, database_id: str, asset_id: str,
                                 workflow_database_id: Optional[str] = None,
                                 workflow_id: Optional[str] = None,
                                 params: Dict[str, Any] = None) -> Dict[str, Any]:
        """List an asset's workflow executions. GET .../assets/{assetId}/workflows/executions.

        The workflow filter is sent as QUERY parameters, which the route matches per field. The
        alternative `.../executions/{workflowId}` path form compares against the joined
        `workflowDatabaseId:workflowId` key and reads its companion database from a GET request body,
        so a caller supplying only a workflow id there filters against ':<workflowId>' and receives an
        empty list — indistinguishable from an asset with no matching history.
        """
        try:
            endpoint = API_WORKFLOW_EXECUTIONS.format(databaseId=database_id, assetId=asset_id)
            query_params = dict(params or {})
            if workflow_id:
                query_params['workflowId'] = workflow_id
            if workflow_database_id:
                query_params['workflowDatabaseId'] = workflow_database_id
            return self._pwe_request('GET', endpoint, params=query_params)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                msg = self._pwe_error_message(e)
                if 'asset' in msg.lower():
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                raise DatabaseNotFoundError(f"Database '{database_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to list workflow executions: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to list workflow executions: {e}")

    def list_executions(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """List executions globally (permission-filtered). GET /workflows/executions.

        params may include the filter query strings: workflowId, workflowDatabaseId, status,
        triggerType, groupId, triggeredByUserId, plus pageSize/startingToken.
        """
        try:
            return self._pwe_request('GET', API_WORKFLOW_EXECUTIONS_GLOBAL, params=params or {})
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to list executions: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to list executions: {e}")

    def get_execution_details(self, execution_id: str) -> Dict[str, Any]:
        """Get an execution's full detail/traceability. GET /workflows/executions/{executionId}/details.

        Collections are bounded server-side; truncatedCollections names any that came back partial.
        A pipeline entry carries renderedConfigLocation ({bucket, key}) whenever that object exists
        — not only on truncation — because it is the FULLY substituted body the step ran with,
        while the inline renderedConfig is pre-system-tag. renderedConfigTruncated reports only
        whether the inline copy was shortened."""
        try:
            endpoint = API_WORKFLOW_EXECUTION_DETAILS.format(executionId=execution_id)
            return self._pwe_request('GET', endpoint)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ExecutionNotFoundError(f"Execution '{execution_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to get execution details: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to get execution details: {e}")

    def get_execution_details_metadata(self, execution_id: str,
                                       params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Get one page of an execution's detail metadata.
        GET /workflows/executions/{executionId}/details/metadata.

        params may include: collection (input | inputDatabase | output), pageSize, startingToken,
        pipelineId. Rows carry the same scrubbed shape the details view returns plus the producing
        pipelineId. NextToken is absent on the last page, so its presence is the only signal that
        more rows exist. A token is only valid alongside the same collection and pipelineId it was
        issued with."""
        try:
            endpoint = API_WORKFLOW_EXECUTION_DETAILS_METADATA.format(executionId=execution_id)
            return self._pwe_request('GET', endpoint, params=params or {})
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ExecutionNotFoundError(f"Execution '{execution_id}' not found")
            if e.response.status_code == 400:
                raise InvalidExecutionDataError(self._pwe_error_message(e))
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to get execution detail metadata: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to get execution detail metadata: {e}")

    def get_execution_logs(self, execution_id: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """Get an execution's logs. GET /workflows/executions/{executionId}/logs.

        params may include: mode (truncated|full), pipelineExecutionId, and (full mode)
        filterPattern/limit/startTime/endTime/nextToken.
        """
        try:
            endpoint = API_WORKFLOW_EXECUTION_LOGS.format(executionId=execution_id)
            return self._pwe_request('GET', endpoint, params=params or {})
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ExecutionNotFoundError(f"Execution '{execution_id}' not found")
            if e.response.status_code == 400:
                raise InvalidExecutionDataError(self._pwe_error_message(e))
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to get execution logs: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to get execution logs: {e}")

    def abort_execution(self, execution_id: str, group_id: Optional[str] = None) -> Dict[str, Any]:
        """Abort a running execution, or (with group_id) abort every active execution in the group.
        DELETE /workflows/executions/{executionId}[?groupId=...]."""
        try:
            endpoint = API_WORKFLOW_EXECUTION.format(executionId=execution_id)
            params = {'groupId': group_id} if group_id else {}
            return self._pwe_request('DELETE', endpoint, params=params)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ExecutionNotFoundError(
                    f"No executions found for group '{group_id}'" if group_id
                    else f"Execution '{execution_id}' not found")
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to abort execution: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to abort execution: {e}")

    def rerun_execution(self, execution_id: str, execution_group_id: Optional[str] = None) -> Dict[str, Any]:
        """Re-run an execution (reconstructed from stored records). POST /workflows/executions/{executionId}/rerun."""
        try:
            endpoint = API_WORKFLOW_EXECUTION_RERUN.format(executionId=execution_id)
            body = {}
            if execution_group_id:
                body['executionGroupId'] = execution_group_id
            return self._pwe_request('POST', endpoint, data=body)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ExecutionNotFoundError(f"Execution '{execution_id}' not found")
            if e.response.status_code == 400:
                raise InvalidExecutionDataError(self._pwe_error_message(e))
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to re-run execution: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to re-run execution: {e}")

    def permanent_delete_execution(self, execution_id: str) -> Dict[str, Any]:
        """Permanently delete an execution's DynamoDB records (admin). DELETE .../{executionId}/permanent."""
        try:
            endpoint = API_WORKFLOW_EXECUTION_PERMANENT.format(executionId=execution_id)
            return self._pwe_request('DELETE', endpoint, json={'confirmDelete': True})
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ExecutionNotFoundError(f"Execution '{execution_id}' not found")
            if e.response.status_code == 400:
                msg = self._pwe_error_message(e)
                if 'in progress' in msg.lower():
                    raise ExecutionInProgressError(msg)
                raise InvalidExecutionDataError(msg)
            if e.response.status_code in (401, 403):
                raise AuthenticationError(f"Authentication failed: {e}")
            raise APIError(f"Failed to permanently delete execution: {self._pwe_error_message(e)}")
        except Exception as e:
            raise APIError(f"Failed to permanently delete execution: {e}")

    # Search API Methods

    def search_query(self, search_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute complex search query using the /search POST endpoint.
        
        Args:
            search_params: Search parameters matching SearchRequestModel format:
                - query: General text search
                - entityTypes: List of entity types to search (["asset"], ["file"], or both)
                - metadataQuery: Separate metadata search query
                - metadataSearchMode: "key", "value", or "both"
                - includeMetadataInSearch: Include metadata in general search
                - explainResults: Include match explanations
                - filters: Additional query filters
                - sort: Sort configuration
                - from_: Pagination offset
                - size: Results per page
                - includeArchived: Include archived items
                - aggregations: Include aggregations
        
        Returns:
            API response data with search results
        
        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails or search is disabled
        """
        try:
            response = self.post(API_SEARCH, data=search_params, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise APIError(f"Invalid search parameters: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'not available' in error_message.lower() or 'opensearch' in error_message.lower():
                    raise APIError(f"Search is not available: {error_message}")
                else:
                    raise APIError(f"Search endpoint not found: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Search query failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to execute search query: {e}")

    def search_simple(self, search_params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute simple search query using the /search/simple POST endpoint.
        
        Args:
            search_params: Simple search parameters matching SimpleSearchRequestModel format:
                - query: General keyword search
                - assetName: Search by asset name
                - assetId: Search by asset ID
                - assetType: Filter by asset type
                - fileKey: Search by file key
                - fileExtension: Filter by file extension
                - databaseId: Filter by database ID
                - tags: Filter by tags
                - metadataKey: Search metadata field names
                - metadataValue: Search metadata field values
                - entityTypes: Filter by entity type (["asset"], ["file"], or both)
                - includeArchived: Include archived items
                - from_: Pagination offset
                - size: Results per page
        
        Returns:
            API response data with search results
        
        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails or search is disabled
        """
        try:
            response = self.post(API_SEARCH_SIMPLE, data=search_params, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise APIError(f"Invalid search parameters: {error_message}")
                
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'not available' in error_message.lower() or 'opensearch' in error_message.lower():
                    raise APIError(f"Search is not available: {error_message}")
                else:
                    raise APIError(f"Search endpoint not found: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Simple search query failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to execute simple search query: {e}")

    def get_search_mapping(self) -> Dict[str, Any]:
        """
        Get search index mapping using the /search GET endpoint.
        
        Returns:
            API response data with dual-index search field mapping
        
        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails or search is disabled
        """
        try:
            response = self.get(API_SEARCH_MAPPING, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                if 'not available' in error_message.lower() or 'opensearch' in error_message.lower():
                    raise APIError(f"Search is not available: {error_message}")
                else:
                    raise APIError(f"Search mapping endpoint not found: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get search mapping: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get search mapping: {e}")

    # Role Management API Methods

    def list_roles(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        List all roles using the /roles GET endpoint.
        
        Args:
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
        
        Returns:
            API response data with roles list wrapped in message field
        
        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_ROLES
        from .exceptions import RoleError
        
        try:
            query_params = params or {}
            response = self.get(API_ROLES, include_auth=True, params=query_params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list roles: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list roles: {e}")

    def create_role(self, role_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new role using the /roles POST endpoint.
        
        Args:
            role_data: Role creation data matching CreateRoleRequestModel:
                - roleName: Role name (required)
                - description: Role description (required)
                - source: Optional source
                - sourceIdentifier: Optional source identifier
                - mfaRequired: Optional MFA requirement (default: false)
        
        Returns:
            API response data with operation result
        
        Raises:
            RoleAlreadyExistsError: When role already exists
            InvalidRoleDataError: When role data is invalid
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_ROLES
        from .exceptions import RoleAlreadyExistsError, InvalidRoleDataError
        
        try:
            response = self.post(API_ROLES, data=role_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already exists' in error_message.lower():
                    raise RoleAlreadyExistsError(f"Role already exists: {error_message}")
                else:
                    raise InvalidRoleDataError(f"Invalid role data: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Role creation failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to create role: {e}")

    def update_role(self, role_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing role using the /roles PUT endpoint.
        
        Args:
            role_data: Role update data matching UpdateRoleRequestModel:
                - roleName: Role name (required)
                - description: Role description (required)
                - source: Optional source
                - sourceIdentifier: Optional source identifier
                - mfaRequired: Optional MFA requirement
        
        Returns:
            API response data with operation result
        
        Raises:
            RoleNotFoundError: When role is not found
            InvalidRoleDataError: When role data is invalid
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_ROLES
        from .exceptions import RoleNotFoundError, InvalidRoleDataError
        
        try:
            response = self.put(API_ROLES, data=role_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'does not exist' in error_message.lower() or 'not found' in error_message.lower():
                    raise RoleNotFoundError(f"Role not found: {error_message}")
                else:
                    raise InvalidRoleDataError(f"Invalid role data: {error_message}")
                    
            elif e.response.status_code == 404:
                raise RoleNotFoundError("Role not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Role update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update role: {e}")

    def delete_role(self, role_name: str) -> Dict[str, Any]:
        """
        Delete a role using the /roles/{roleId} DELETE endpoint.
        
        Args:
            role_name: Role name to delete
        
        Returns:
            API response data with deletion result
        
        Raises:
            RoleNotFoundError: When role is not found
            RoleDeletionError: When role deletion fails due to dependencies
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_ROLE_BY_ID
        from .exceptions import RoleNotFoundError, RoleDeletionError
        
        try:
            endpoint = API_ROLE_BY_ID.format(roleId=role_name)
            response = self.delete(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise RoleDeletionError(f"Role deletion failed: {error_message}")
                
            elif e.response.status_code == 404:
                raise RoleNotFoundError(f"Role '{role_name}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Role deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete role: {e}")

    # Cognito User Management API Methods

    def list_cognito_users(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        List Cognito users using the /user/cognito GET endpoint.
        
        Args:
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
        
        Returns:
            API response data with users list and NextToken
        
        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails or Cognito is not enabled
        """
        from ..constants import API_COGNITO_USERS
        from .exceptions import CognitoUserOperationError
        
        try:
            query_params = params or {}
            response = self.get(API_COGNITO_USERS, include_auth=True, params=query_params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise CognitoUserOperationError(f"Invalid list parameters: {error_message}")
                
            elif e.response.status_code == 503:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise CognitoUserOperationError(f"Cognito not enabled: {error_message}")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list Cognito users: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list Cognito users: {e}")

    def create_cognito_user(self, user_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new Cognito user using the /user/cognito POST endpoint.
        
        Args:
            user_data: User creation data matching CreateCognitoUserRequestModel:
                - userId: User ID (email format)
                - email: Email address
                - phone: Optional phone number in E.164 format
        
        Returns:
            API response data with operation result and temporary password
        
        Raises:
            CognitoUserAlreadyExistsError: When user already exists
            InvalidCognitoUserDataError: When user data is invalid
            CognitoUserOperationError: When Cognito is not enabled
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_COGNITO_USERS
        from .exceptions import (
            CognitoUserAlreadyExistsError, InvalidCognitoUserDataError, CognitoUserOperationError
        )
        
        try:
            response = self.post(API_COGNITO_USERS, data=user_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already exists' in error_message.lower() or 'user exists' in error_message.lower():
                    raise CognitoUserAlreadyExistsError(f"User already exists: {error_message}")
                else:
                    raise InvalidCognitoUserDataError(f"Invalid user data: {error_message}")
                    
            elif e.response.status_code == 503:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise CognitoUserOperationError(f"Cognito not enabled: {error_message}")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"User creation failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to create Cognito user: {e}")

    def update_cognito_user(self, user_id: str, update_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update a Cognito user using the /user/cognito/{userId} PUT endpoint.
        
        Args:
            user_id: User ID to update
            update_data: User update data matching UpdateCognitoUserRequestModel:
                - email: Optional new email address
                - phone: Optional new phone number in E.164 format
        
        Returns:
            API response data with operation result
        
        Raises:
            CognitoUserNotFoundError: When user is not found
            InvalidCognitoUserDataError: When update data is invalid
            CognitoUserOperationError: When Cognito is not enabled
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_COGNITO_USER_BY_ID
        from .exceptions import (
            CognitoUserNotFoundError, InvalidCognitoUserDataError, CognitoUserOperationError
        )
        
        try:
            endpoint = API_COGNITO_USER_BY_ID.format(userId=user_id)
            response = self.put(endpoint, data=update_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidCognitoUserDataError(f"Invalid update data: {error_message}")
                
            elif e.response.status_code == 404:
                raise CognitoUserNotFoundError(f"User '{user_id}' not found")
                
            elif e.response.status_code == 503:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise CognitoUserOperationError(f"Cognito not enabled: {error_message}")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"User update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update Cognito user: {e}")

    def delete_cognito_user(self, user_id: str) -> Dict[str, Any]:
        """
        Delete a Cognito user using the /user/cognito/{userId} DELETE endpoint.
        
        Args:
            user_id: User ID to delete
        
        Returns:
            API response data with operation result
        
        Raises:
            CognitoUserNotFoundError: When user is not found
            CognitoUserOperationError: When Cognito is not enabled
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_COGNITO_USER_BY_ID
        from .exceptions import CognitoUserNotFoundError, CognitoUserOperationError
        
        try:
            endpoint = API_COGNITO_USER_BY_ID.format(userId=user_id)
            response = self.delete(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise CognitoUserNotFoundError(f"User '{user_id}' not found")
                
            elif e.response.status_code == 503:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise CognitoUserOperationError(f"Cognito not enabled: {error_message}")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"User deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete Cognito user: {e}")

    def reset_cognito_user_password(self, user_id: str, confirm_reset: bool = False) -> Dict[str, Any]:
        """
        Reset a Cognito user's password using the /user/cognito/{userId}/resetPassword POST endpoint.
        
        Args:
            user_id: User ID to reset password for
            confirm_reset: Confirmation flag for password reset
        
        Returns:
            API response data with operation result and temporary password
        
        Raises:
            CognitoUserNotFoundError: When user is not found
            InvalidCognitoUserDataError: When confirmation is not provided
            CognitoUserOperationError: When Cognito is not enabled
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_COGNITO_USER_RESET_PASSWORD
        from .exceptions import (
            CognitoUserNotFoundError, InvalidCognitoUserDataError, CognitoUserOperationError
        )
        
        try:
            endpoint = API_COGNITO_USER_RESET_PASSWORD.format(userId=user_id)
            data = {'confirmReset': confirm_reset}
            response = self.post(endpoint, data=data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidCognitoUserDataError(f"Invalid reset request: {error_message}")
                
            elif e.response.status_code == 404:
                raise CognitoUserNotFoundError(f"User '{user_id}' not found")
                
            elif e.response.status_code == 503:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise CognitoUserOperationError(f"Cognito not enabled: {error_message}")
                
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Password reset failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to reset Cognito user password: {e}")

    # Constraint Management API Methods

    def list_constraints(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        List all constraints using the /auth/constraints GET endpoint.
        
        Args:
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
        
        Returns:
            API response data with constraints list wrapped in message field
        
        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_CONSTRAINTS
        
        try:
            query_params = params or {}
            response = self.get(API_CONSTRAINTS, include_auth=True, params=query_params)
            result = response.json()
            
            # Backend wraps response in "message" field for backward compatibility
            # Unwrap it for consistent API client behavior
            if 'message' in result and isinstance(result['message'], dict):
                return result['message']
            return result
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list constraints: {e}")

        except Exception as e:
            raise APIError(f"Failed to list constraints: {e}")

    def list_api_routes(self) -> Dict[str, Any]:
        """
        List all available VAMS API routes using the /auth/routes/api GET endpoint.

        Returns:
            API response data with the full API route list (routes: [{path, methods, category}])

        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_AUTH_ROUTES_API

        try:
            response = self.get(API_AUTH_ROUTES_API, include_auth=True)
            result = response.json()

            # Backend wraps response in "message" field for backward compatibility
            if 'message' in result and isinstance(result['message'], dict):
                return result['message']
            return result

        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list API routes: {e}")

        except Exception as e:
            raise APIError(f"Failed to list API routes: {e}")

    def list_allowed_api_routes(self) -> Dict[str, Any]:
        """
        List the VAMS API routes and methods the current user is authorized to
        call, using the /auth/routes/api/allowed GET endpoint.

        Returns:
            API response data with the allowed API routes (routes: [{path, methods, category}], userId)

        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_AUTH_ROUTES_API_ALLOWED

        try:
            response = self.get(API_AUTH_ROUTES_API_ALLOWED, include_auth=True)
            result = response.json()

            # Backend wraps response in "message" field for backward compatibility
            if 'message' in result and isinstance(result['message'], dict):
                return result['message']
            return result

        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list allowed API routes: {e}")

        except Exception as e:
            raise APIError(f"Failed to list allowed API routes: {e}")

    def list_constraint_permission_objects(self) -> Dict[str, Any]:
        """
        List the constraint permission objects (object types with their valid
        fields, operators, permissions, and permission types) using the
        /auth/constraints/permissionObjects GET endpoint.

        Returns:
            API response data: {objectTypes: [{label, value, fields: [{label, value}]}],
            operators: [{label, value}], permissions: [{label, value}], permissionTypes: [{label, value}]}

        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_AUTH_CONSTRAINT_PERMISSION_OBJECTS

        try:
            response = self.get(API_AUTH_CONSTRAINT_PERMISSION_OBJECTS, include_auth=True)
            result = response.json()

            # Backend wraps response in "message" field for backward compatibility
            if 'message' in result and isinstance(result['message'], dict):
                return result['message']
            return result

        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list constraint permission objects: {e}")

        except Exception as e:
            raise APIError(f"Failed to list constraint permission objects: {e}")

    def get_constraint(self, constraint_id: str) -> Dict[str, Any]:
        """
        Get a specific constraint using the /auth/constraints/{constraintId} GET endpoint.
        
        Args:
            constraint_id: Constraint ID
        
        Returns:
            API response data with constraint details
        
        Raises:
            ConstraintNotFoundError: When constraint is not found
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_CONSTRAINT_BY_ID
        from .exceptions import ConstraintNotFoundError
        
        try:
            endpoint = API_CONSTRAINT_BY_ID.format(constraintId=constraint_id)
            response = self.get(endpoint, include_auth=True)
            result = response.json()
            
            # Extract constraint from response
            if 'constraint' in result:
                return result['constraint']
            return result
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ConstraintNotFoundError(f"Constraint '{constraint_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to get constraint: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to get constraint: {e}")

    def create_constraint(self, constraint_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new constraint using the /auth/constraints/{constraintId} POST endpoint.
        
        Args:
            constraint_data: Constraint creation data matching CreateConstraintRequestModel:
                - identifier: Constraint ID (required)
                - name: Constraint name (required)
                - description: Constraint description (required)
                - objectType: Object type (required)
                - criteriaAnd: AND criteria array (optional)
                - criteriaOr: OR criteria array (optional)
                - groupPermissions: Group permissions array (optional)
                - userPermissions: User permissions array (optional)
        
        Returns:
            API response data with operation result
        
        Raises:
            ConstraintAlreadyExistsError: When constraint already exists
            InvalidConstraintDataError: When constraint data is invalid
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_CONSTRAINT_BY_ID
        from .exceptions import ConstraintAlreadyExistsError, InvalidConstraintDataError
        
        try:
            constraint_id = constraint_data.get('identifier')
            if not constraint_id:
                raise InvalidConstraintDataError("Constraint identifier is required")
            
            endpoint = API_CONSTRAINT_BY_ID.format(constraintId=constraint_id)
            response = self.post(endpoint, data=constraint_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already exists' in error_message.lower():
                    raise ConstraintAlreadyExistsError(f"Constraint already exists: {error_message}")
                else:
                    raise InvalidConstraintDataError(f"Invalid constraint data: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Constraint creation failed: {e}")
                
        except InvalidConstraintDataError:
            raise
        except Exception as e:
            raise APIError(f"Failed to create constraint: {e}")

    def update_constraint(self, constraint_id: str, constraint_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an existing constraint using the /auth/constraints/{constraintId} POST endpoint.
        
        Args:
            constraint_id: Constraint ID
            constraint_data: Constraint update data matching CreateConstraintRequestModel
        
        Returns:
            API response data with operation result
        
        Raises:
            ConstraintNotFoundError: When constraint is not found
            InvalidConstraintDataError: When constraint data is invalid
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_CONSTRAINT_BY_ID
        from .exceptions import ConstraintNotFoundError, InvalidConstraintDataError
        
        try:
            # Ensure identifier matches the path parameter
            constraint_data['identifier'] = constraint_id
            
            endpoint = API_CONSTRAINT_BY_ID.format(constraintId=constraint_id)
            response = self.post(endpoint, data=constraint_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'does not exist' in error_message.lower() or 'not found' in error_message.lower():
                    raise ConstraintNotFoundError(f"Constraint not found: {error_message}")
                else:
                    raise InvalidConstraintDataError(f"Invalid constraint data: {error_message}")
                    
            elif e.response.status_code == 404:
                raise ConstraintNotFoundError(f"Constraint '{constraint_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Constraint update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update constraint: {e}")

    def delete_constraint(self, constraint_id: str) -> Dict[str, Any]:
        """
        Delete a constraint using the /auth/constraints/{constraintId} DELETE endpoint.
        
        Args:
            constraint_id: Constraint ID to delete
        
        Returns:
            API response data with deletion result
        
        Raises:
            ConstraintNotFoundError: When constraint is not found
            ConstraintDeletionError: When constraint deletion fails due to dependencies
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_CONSTRAINT_BY_ID
        from .exceptions import ConstraintNotFoundError, ConstraintDeletionError
        
        try:
            endpoint = API_CONSTRAINT_BY_ID.format(constraintId=constraint_id)
            response = self.delete(endpoint, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise ConstraintDeletionError(f"Constraint deletion failed: {error_message}")
                
            elif e.response.status_code == 404:
                raise ConstraintNotFoundError(f"Constraint '{constraint_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Constraint deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete constraint: {e}")

    def import_constraints_template(self, template_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Import constraints from a permission template using the /auth/constraintsTemplateImport POST endpoint.

        Args:
            template_data: Template import data matching ImportConstraintsTemplateRequestModel:
                - template: Optional template metadata (name, description, version)
                - variables: Optional list of variable definitions
                - variableValues: Dictionary of variable name -> value mappings (ROLE_NAME required)
                - constraints: List of constraint definitions

        Returns:
            API response data with import results (constraintsCreated, constraintIds, etc.)

        Raises:
            InvalidConstraintDataError: When template data is invalid
            TemplateImportError: When template import fails
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_CONSTRAINTS_TEMPLATE_IMPORT
        from .exceptions import InvalidConstraintDataError, TemplateImportError

        try:
            response = self.post(API_CONSTRAINTS_TEMPLATE_IMPORT, data=template_data, include_auth=True)
            result = response.json()

            # Backend wraps response in "message" field for backward compatibility
            if 'message' in result and isinstance(result['message'], dict):
                return result['message']
            return result

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise InvalidConstraintDataError(f"Invalid template data: {error_message}")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise TemplateImportError(f"Template import failed: {e}")

        except (InvalidConstraintDataError, AuthenticationError, TemplateImportError):
            raise
        except Exception as e:
            raise APIError(f"Failed to import constraints template: {e}")

    # User Role Management API Methods

    def list_user_roles(self, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        List all user roles using the /user-roles GET endpoint.
        
        Args:
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
        
        Returns:
            API response data with user roles list wrapped in message field
        
        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_USER_ROLES
        
        try:
            query_params = params or {}
            response = self.get(API_USER_ROLES, include_auth=True, params=query_params)
            result = response.json()
            
            # Backend wraps response in "message" field for backward compatibility
            # Unwrap it for consistent API client behavior
            if 'message' in result and isinstance(result['message'], dict):
                return result['message']
            return result
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list user roles: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list user roles: {e}")

    def create_user_roles(self, user_role_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create new user roles using the /user-roles POST endpoint.
        
        Args:
            user_role_data: User role creation data matching CreateUserRolesRequestModel:
                - userId: User ID (required)
                - roleName: Array of role names (required)
        
        Returns:
            API response data with operation result
        
        Raises:
            UserRoleAlreadyExistsError: When user role already exists
            InvalidUserRoleDataError: When user role data is invalid
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_USER_ROLES
        from .exceptions import UserRoleAlreadyExistsError, InvalidUserRoleDataError
        
        try:
            response = self.post(API_USER_ROLES, data=user_role_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already exist' in error_message.lower():
                    raise UserRoleAlreadyExistsError(f"User role already exists: {error_message}")
                else:
                    raise InvalidUserRoleDataError(f"Invalid user role data: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"User role creation failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to create user roles: {e}")

    def update_user_roles(self, user_role_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update user roles using the /user-roles PUT endpoint.
        
        Args:
            user_role_data: User role update data matching UpdateUserRolesRequestModel:
                - userId: User ID (required)
                - roleName: Array of role names (required)
        
        Returns:
            API response data with operation result
        
        Raises:
            UserRoleNotFoundError: When user role is not found
            InvalidUserRoleDataError: When user role data is invalid
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_USER_ROLES
        from .exceptions import UserRoleNotFoundError, InvalidUserRoleDataError
        
        try:
            response = self.put(API_USER_ROLES, data=user_role_data, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'does not exist' in error_message.lower() or 'not found' in error_message.lower():
                    raise UserRoleNotFoundError(f"User role not found: {error_message}")
                else:
                    raise InvalidUserRoleDataError(f"Invalid user role data: {error_message}")
                    
            elif e.response.status_code == 404:
                raise UserRoleNotFoundError("User role not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"User role update failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to update user roles: {e}")

    def delete_user_roles(self, user_id: str) -> Dict[str, Any]:
        """
        Delete all roles for a user using the /user-roles DELETE endpoint.
        
        Args:
            user_id: User ID whose roles should be deleted
        
        Returns:
            API response data with deletion result
        
        Raises:
            UserRoleNotFoundError: When user role is not found
            UserRoleDeletionError: When user role deletion fails
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_USER_ROLES
        from .exceptions import UserRoleNotFoundError, UserRoleDeletionError
        
        try:
            data = {'userId': user_id}
            response = self.delete(API_USER_ROLES, include_auth=True, json=data)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise UserRoleDeletionError(f"User role deletion failed: {error_message}")
                
            elif e.response.status_code == 404:
                raise UserRoleNotFoundError(f"User roles for '{user_id}' not found")
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"User role deletion failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to delete user roles: {e}")

    # API Key Management API Methods

    def list_api_keys(self) -> Dict[str, Any]:
        """
        List all API keys using the /auth/api-keys GET endpoint.

        Returns:
            API response data with API keys list

        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from .exceptions import ApiKeyError

        try:
            response = self.get(API_AUTH_API_KEYS, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list API keys: {e}")

        except Exception as e:
            raise APIError(f"Failed to list API keys: {e}")

    def create_api_key(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a new API key using the /auth/api-keys POST endpoint.

        Args:
            data: API key creation data:
                - apiKeyName: Name for the key (required)
                - userId: VAMS user ID this key acts as (required)
                - description: Optional description
                - expiresAt: Optional expiration date in ISO 8601 format

        Returns:
            API response data including the generated API key (shown only once)

        Raises:
            ApiKeyCreationError: When API key creation fails
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from .exceptions import ApiKeyCreationError

        try:
            response = self.post(API_AUTH_API_KEYS, data=data, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise ApiKeyCreationError(f"API key creation failed: {error_message}")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"API key creation failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to create API key: {e}")

    def update_api_key(self, api_key_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update an API key using the /auth/api-keys/{apiKeyId} PUT endpoint.

        Args:
            api_key_id: ID of the API key to update
            data: Update data:
                - description: Optional new description
                - expiresAt: Optional new expiration date

        Returns:
            API response data with updated API key details

        Raises:
            ApiKeyNotFoundError: When API key is not found
            ApiKeyUpdateError: When API key update fails
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from .exceptions import ApiKeyNotFoundError, ApiKeyUpdateError

        endpoint = API_AUTH_API_KEY.format(apiKeyId=api_key_id)

        try:
            response = self.put(endpoint, data=data, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ApiKeyNotFoundError(f"API key '{api_key_id}' not found")

            elif e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise ApiKeyUpdateError(f"API key update failed: {error_message}")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"API key update failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to update API key: {e}")

    def delete_api_key(self, api_key_id: str) -> Dict[str, Any]:
        """
        Delete an API key using the /auth/api-keys/{apiKeyId} DELETE endpoint.

        Args:
            api_key_id: ID of the API key to delete

        Returns:
            API response data with deletion result

        Raises:
            ApiKeyNotFoundError: When API key is not found
            ApiKeyDeletionError: When API key deletion fails
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from .exceptions import ApiKeyNotFoundError, ApiKeyDeletionError

        endpoint = API_AUTH_API_KEY.format(apiKeyId=api_key_id)

        try:
            response = self.delete(endpoint, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ApiKeyNotFoundError(f"API key '{api_key_id}' not found")

            elif e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise ApiKeyDeletionError(f"API key deletion failed: {error_message}")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"API key deletion failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to delete API key: {e}")

    def list_user_api_keys(self) -> Dict[str, Any]:
        """
        List the current user's own API keys using the /auth/user/api-keys GET endpoint.

        Returns:
            API response data with the user's API keys list

        Raises:
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_AUTH_USER_API_KEYS

        try:
            response = self.get(API_AUTH_USER_API_KEYS, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list user API keys: {e}")

        except Exception as e:
            raise APIError(f"Failed to list user API keys: {e}")

    def create_user_api_key(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Create a self-service API key for the current user using the
        /auth/user/api-keys POST endpoint. The key is always tied to the
        authenticated user and requires an expiration date no more than 365
        days from creation.

        Args:
            data: API key creation data:
                - apiKeyName: Name for the key (required)
                - description: Description (required)
                - expiresAt: Expiration date in ISO 8601 format (required, max 365 days out)

        Returns:
            API response data including the generated API key (shown only once)

        Raises:
            ApiKeyCreationError: When API key creation fails
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_AUTH_USER_API_KEYS
        from .exceptions import ApiKeyCreationError

        try:
            response = self.post(API_AUTH_USER_API_KEYS, data=data, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise ApiKeyCreationError(f"API key creation failed: {error_message}")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"API key creation failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to create user API key: {e}")

    def update_user_api_key(self, api_key_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Update one of the current user's own API keys using the
        /auth/user/api-keys/{apiKeyId} PUT endpoint. The expiration cannot be
        cleared and cannot exceed 365 days from the key's original creation.

        Args:
            api_key_id: ID of the API key to update (must be owned by the current user)
            data: Update data:
                - description: Optional new description
                - expiresAt: Optional new expiration date (within the 365-day window)
                - isActive: Optional 'true'/'false'

        Returns:
            API response data with updated API key details

        Raises:
            ApiKeyNotFoundError: When the API key is not found or not owned by the user
            ApiKeyUpdateError: When API key update fails
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_AUTH_USER_API_KEY
        from .exceptions import ApiKeyNotFoundError, ApiKeyUpdateError

        endpoint = API_AUTH_USER_API_KEY.format(apiKeyId=api_key_id)

        try:
            response = self.put(endpoint, data=data, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ApiKeyNotFoundError(f"API key '{api_key_id}' not found")

            elif e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise ApiKeyUpdateError(f"API key update failed: {error_message}")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"API key update failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to update user API key: {e}")

    def delete_user_api_key(self, api_key_id: str) -> Dict[str, Any]:
        """
        Delete one of the current user's own API keys using the
        /auth/user/api-keys/{apiKeyId} DELETE endpoint.

        Args:
            api_key_id: ID of the API key to delete (must be owned by the current user)

        Returns:
            API response data with deletion result

        Raises:
            ApiKeyNotFoundError: When the API key is not found or not owned by the user
            ApiKeyDeletionError: When API key deletion fails
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from ..constants import API_AUTH_USER_API_KEY
        from .exceptions import ApiKeyNotFoundError, ApiKeyDeletionError

        endpoint = API_AUTH_USER_API_KEY.format(apiKeyId=api_key_id)

        try:
            response = self.delete(endpoint, include_auth=True)
            return response.json()

        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                raise ApiKeyNotFoundError(f"API key '{api_key_id}' not found")

            elif e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                raise ApiKeyDeletionError(f"API key deletion failed: {error_message}")

            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"API key deletion failed: {e}")

        except Exception as e:
            raise APIError(f"Failed to delete user API key: {e}")
