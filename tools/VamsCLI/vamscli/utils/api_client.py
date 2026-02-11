"""API client for VamsCLI."""

import json
import requests
from typing import Dict, Any, Optional
from urllib.parse import urljoin

from ..constants import (
    API_VERSION, DEFAULT_TIMEOUT, MAX_AUTH_RETRIES, MINIMUM_API_VERSION, 
    API_LOGIN_PROFILE, API_SECURE_CONFIG, API_ASSETS, API_DATABASE_ASSETS, API_DATABASE_ASSET,
    API_CREATE_FOLDER, API_LIST_FILES, API_FILE_INFO, API_MOVE_FILE, API_COPY_FILE,
    API_ARCHIVE_FILE, API_UNARCHIVE_FILE, API_DELETE_ASSET_PREVIEW, 
    API_DELETE_AUXILIARY_PREVIEW, API_DELETE_FILE, API_REVERT_FILE_VERSION, API_SET_PRIMARY_FILE,
    API_ARCHIVE_ASSET, API_DELETE_ASSET, API_DOWNLOAD_ASSET, API_ASSET_EXPORT, API_DATABASE, API_DATABASE_BY_ID, API_BUCKETS,
    API_TAGS, API_TAG_DELETE, API_TAG_TYPES, API_TAG_TYPE_DELETE,
    API_CREATE_ASSET_VERSION, API_REVERT_ASSET_VERSION, API_GET_ASSET_VERSIONS, API_GET_ASSET_VERSION,
    API_ASSET_LINKS, API_ASSET_LINKS_SINGLE, API_ASSET_LINKS_UPDATE, API_ASSET_LINKS_DELETE, API_ASSET_LINKS_FOR_ASSET,
    API_ASSET_LINKS_METADATA, API_ASSET_LINKS_METADATA_KEY, API_METADATA, API_METADATA_SCHEMA,
    API_METADATA_SCHEMA_LIST, API_METADATA_SCHEMA_BY_ID,
    API_SEARCH, API_SEARCH_SIMPLE, API_SEARCH_MAPPING,
    API_WORKFLOWS, API_DATABASE_WORKFLOWS, API_WORKFLOW_EXECUTIONS, API_EXECUTE_WORKFLOW
)
from ..version import get_version
from .exceptions import (
    APIError, VersionMismatchError, AuthenticationError, OverrideTokenError, 
    TokenExpiredError, PermissionDeniedError,
    APIUnavailableError, AssetNotFoundError, AssetAlreadyExistsError,
    DatabaseNotFoundError, DatabaseAlreadyExistsError, DatabaseDeletionError,
    BucketNotFoundError, InvalidDatabaseDataError, InvalidAssetDataError, FileUploadError,
    AssetAlreadyArchivedError, AssetDeletionError, TagNotFoundError, TagAlreadyExistsError,
    TagTypeNotFoundError, TagTypeAlreadyExistsError, TagTypeInUseError, 
    InvalidTagDataError, InvalidTagTypeDataError, AssetVersionError, AssetVersionNotFoundError,
    AssetVersionOperationError, InvalidAssetVersionDataError, AssetVersionRevertError,
    AssetLinkError, AssetLinkNotFoundError, AssetLinkValidationError, AssetLinkPermissionError,
    CycleDetectionError, AssetLinkAlreadyExistsError, InvalidRelationshipTypeError, AssetLinkOperationError,
    RateLimitExceededError, RetryExhaustedError
)
from .profile import ProfileManager
from .retry_config import get_retry_config


class APIClient:
    """HTTP client for VAMS API Gateway."""
    
    def __init__(self, base_url: str, profile_manager: Optional[ProfileManager] = None):
        self.base_url = base_url.rstrip('/')
        self.profile_manager = profile_manager or ProfileManager()
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
                     retry_count: int = 0, throttle_retry_count: int = 0, **kwargs) -> requests.Response:
        """Make HTTP request with error handling and retries for both auth and throttling."""
        # Import logging utilities
        from .logging import log_api_request, log_api_response
        import time
        
        # Pre-flight validation for override tokens
        self._validate_token_before_request(include_auth)
        
        url = urljoin(self.base_url + '/', endpoint.lstrip('/'))
        headers = self._get_headers(include_auth)
        retry_config = get_retry_config()
        
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
                        throttle_retry_count + 1, **kwargs
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
                if self._try_refresh_token():
                    return self._make_request(
                        method, endpoint, include_auth, retry_count + 1, 
                        throttle_retry_count, **kwargs
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
                if self._try_refresh_token():
                    return self._make_request(
                        method, endpoint, include_auth, retry_count + 1, 
                        throttle_retry_count, **kwargs
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
            
    def _try_refresh_token(self) -> bool:
        """Try to refresh the authentication token or re-authenticate using saved credentials."""
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
            
            # Try to call login profile API to validate and refresh user profile
            try:
                login_profile_result = self.call_login_profile(saved_credentials['username'])
                
                # Try to fetch feature switches after successful re-authentication
                try:
                    secure_config_result = self.get_secure_config()
                    self.profile_manager.save_feature_switches(secure_config_result)
                    
                    log_auth_diagnostic(
                        auth_type="reauth_saved_creds",
                        status="success",
                        details={
                            'user_id': saved_credentials['username'],
                            'profile_name': self.profile_manager.profile_name,
                            'secure_config': secure_config_result
                        }
                    )
                except Exception as sc_error:
                    # Feature switches fetch failure is non-blocking
                    log_auth_diagnostic(
                        auth_type="reauth_saved_creds",
                        status="success_partial",
                        details={
                            'user_id': saved_credentials['username'],
                            'profile_name': self.profile_manager.profile_name,
                            'secure_config_error': str(sc_error)
                        }
                    )
                    
            except Exception as lp_error:
                # If login profile API fails, we still have valid tokens
                log_auth_diagnostic(
                    auth_type="reauth_saved_creds",
                    status="success_partial",
                    details={
                        'user_id': saved_credentials['username'],
                        'profile_name': self.profile_manager.profile_name,
                        'login_profile_error': str(lp_error)
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
            response = self.get('/api/amplify-config', include_auth=False)
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
            data = {}
            if reason:
                data['reason'] = reason
                
            response = self.delete(endpoint, include_auth=True, json=data if data else None)
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
            endpoint = API_ASSET_LINKS_DELETE.format(relationId=asset_link_id)
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

    def get_asset_metadata_v2(self, database_id: str, asset_id: str, page_size: int = 3000, starting_token: str = None) -> Dict[str, Any]:
        """
        Get metadata for an asset using the new unified API.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            page_size: Page size for pagination (default: 3000)
            starting_token: Token for pagination
        
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
                            page_size: int = 3000, starting_token: str = None) -> Dict[str, Any]:
        """
        Get metadata or attributes for a file using the new unified API.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            file_path: Relative file path
            metadata_type: 'metadata' or 'attribute'
            page_size: Page size for pagination (default: 3000)
            starting_token: Token for pagination
        
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

    def download_asset_file(self, database_id: str, asset_id: str, file_key: Optional[str] = None, version_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Generate presigned URL for downloading asset files using the /database/{databaseId}/assets/{assetId}/download POST endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            file_key: Optional specific file key to download
            version_id: Optional version ID for specific version
        
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

    # Workflow API Methods

    def list_workflows(self, database_id: Optional[str] = None, show_deleted: bool = False, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        List workflows using GET /workflows or GET /database/{databaseId}/workflows endpoint.
        
        Args:
            database_id: Optional database ID to filter workflows
            show_deleted: Whether to include deleted workflows
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
        
        Returns:
            API response data with workflows list
        
        Raises:
            DatabaseNotFoundError: When database doesn't exist
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            # Determine endpoint based on database_id
            if database_id:
                endpoint = API_DATABASE_WORKFLOWS.format(databaseId=database_id)
            else:
                endpoint = API_WORKFLOWS
            
            query_params = params or {}
            if show_deleted:
                query_params['showDeleted'] = 'true'
                
            response = self.get(endpoint, include_auth=True, params=query_params)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if database_id and 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                else:
                    raise APIError(f"Workflows not found: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list workflows: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list workflows: {e}")

    def list_workflow_executions(self, database_id: str, asset_id: str, workflow_database_id: Optional[str] = None, 
                                 workflow_id: Optional[str] = None, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """
        List workflow executions for an asset using GET /database/{databaseId}/assets/{assetId}/workflows/executions endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            workflow_database_id: Optional workflow's database ID (for filtering)
            workflow_id: Optional workflow ID (for filtering)
            params: Optional pagination parameters (maxItems, pageSize, startingToken)
                    Note: pageSize is limited to 50 due to Step Functions API throttling
        
        Returns:
            API response data with workflow executions list
        
        Raises:
            AssetNotFoundError: When asset is not found
            DatabaseNotFoundError: When database doesn't exist
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        try:
            # Build endpoint - workflow_id is optional in path
            if workflow_id:
                endpoint = API_WORKFLOW_EXECUTIONS + f"/{workflow_id}"
            else:
                endpoint = API_WORKFLOW_EXECUTIONS
            
            endpoint = endpoint.format(databaseId=database_id, assetId=asset_id)
            
            # Prepare query parameters
            query_params = params or {}
            
            # Prepare request body for workflowDatabaseId if provided
            request_body = {}
            if workflow_database_id:
                request_body['workflowDatabaseId'] = workflow_database_id
            
            # Make request with body if needed
            if request_body:
                response = self.post(endpoint, data=request_body, include_auth=True, params=query_params)
            else:
                response = self.get(endpoint, include_auth=True, params=query_params)
            
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'asset' in error_message.lower():
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                else:
                    raise APIError(f"Workflow executions not found: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise APIError(f"Failed to list workflow executions: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to list workflow executions: {e}")

    def execute_workflow(self, database_id: str, asset_id: str, workflow_id: str, 
                        workflow_database_id: str, file_key: Optional[str] = None) -> Dict[str, Any]:
        """
        Execute a workflow on an asset using POST /database/{databaseId}/assets/{assetId}/workflows/{workflowId} endpoint.
        
        Args:
            database_id: Database ID
            asset_id: Asset ID
            workflow_id: Workflow ID to execute
            workflow_database_id: Workflow's database ID (required)
            file_key: Optional specific file key to run workflow on
        
        Returns:
            API response data with execution ID
        
        Raises:
            AssetNotFoundError: When asset is not found
            WorkflowNotFoundError: When workflow is not found
            WorkflowAlreadyRunningError: When workflow is already running on the file
            WorkflowExecutionError: When workflow execution fails
            DatabaseNotFoundError: When database doesn't exist
            InvalidWorkflowDataError: When workflow data is invalid
            AuthenticationError: When authentication fails
            APIError: When API call fails
        """
        from .exceptions import WorkflowNotFoundError, WorkflowAlreadyRunningError, WorkflowExecutionError, InvalidWorkflowDataError
        
        try:
            endpoint = API_EXECUTE_WORKFLOW.format(
                databaseId=database_id,
                assetId=asset_id,
                workflowId=workflow_id
            )
            
            # Prepare request body
            request_body = {
                'workflowDatabaseId': workflow_database_id
            }
            
            if file_key:
                request_body['fileKey'] = file_key
            
            response = self.post(endpoint, data=request_body, include_auth=True)
            return response.json()
            
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 400:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'already running' in error_message.lower() or 'currently running' in error_message.lower():
                    raise WorkflowAlreadyRunningError(f"Workflow already running: {error_message}")
                elif 'pipeline' in error_message.lower() and ('disabled' in error_message.lower() or 'not enabled' in error_message.lower()):
                    raise WorkflowExecutionError(f"Pipeline not enabled: {error_message}")
                else:
                    raise InvalidWorkflowDataError(f"Invalid workflow execution request: {error_message}")
                    
            elif e.response.status_code == 404:
                error_data = e.response.json() if e.response.content else {}
                error_message = error_data.get('message', str(e))
                
                if 'database' in error_message.lower():
                    raise DatabaseNotFoundError(f"Database '{database_id}' not found")
                elif 'workflow' in error_message.lower():
                    raise WorkflowNotFoundError(f"Workflow '{workflow_id}' not found")
                elif 'asset' in error_message.lower():
                    raise AssetNotFoundError(f"Asset '{asset_id}' not found in database '{database_id}'")
                else:
                    raise APIError(f"Resource not found: {error_message}")
                    
            elif e.response.status_code in [401, 403]:
                raise AuthenticationError(f"Authentication failed: {e}")
            else:
                raise WorkflowExecutionError(f"Workflow execution failed: {e}")
                
        except Exception as e:
            raise APIError(f"Failed to execute workflow: {e}")

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
