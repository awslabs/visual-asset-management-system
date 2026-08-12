"""Cognito authentication for VamsCLI."""

import base64
import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

import boto3
import click
from botocore.exceptions import ClientError

try:
    from pycognito import AWSSRP
    HAS_PYCOGNITO = True
except ImportError:
    HAS_PYCOGNITO = False

from .base import BaseAuthenticator
from ..utils.exceptions import AuthenticationError


class CognitoAuthenticator(BaseAuthenticator):
    """AWS Cognito authentication provider."""
    
    def __init__(self, region: str, user_pool_id: str, client_id: str, client_secret: Optional[str] = None):
        self.region = region
        self.user_pool_id = user_pool_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = boto3.client('cognito-idp', region_name=region)
        
    def _calculate_secret_hash(self, username: str) -> str:
        """Calculate secret hash for Cognito SRP."""
        if not self.client_secret:
            return ""
            
        message = username + self.client_id
        dig = hmac.new(
            self.client_secret.encode('utf-8'),
            message.encode('utf-8'),
            hashlib.sha256
        ).digest()
        return base64.b64encode(dig).decode()
        
    def authenticate(self, username: str, password: str, new_password: Optional[str] = None,
                     interactive: bool = True) -> Dict[str, Any]:
        """Authenticate user using Cognito.

        Args:
            username: Cognito username.
            password: Current password.
            new_password: New password to satisfy a NEW_PASSWORD_REQUIRED challenge
                without prompting (used for forced password changes).
            interactive: When True, missing challenge responses are collected via
                prompts. When False (e.g. JSON output mode), an unmet challenge
                raises AuthenticationError instead of prompting.
        """
        # Import logging here to avoid circular imports
        from ..utils.logging import log_auth_diagnostic
        
        # Log authentication attempt
        log_auth_diagnostic(
            auth_type="cognito",
            status="attempting",
            details={
                'user_id': username,
                'region': self.region,
                'user_pool_id': self.user_pool_id,
                'client_id': self.client_id,
                'has_client_secret': bool(self.client_secret)
            }
        )
        
        try:
            # First try USER_PASSWORD_AUTH if available
            auth_params = {
                'USERNAME': username,
                'PASSWORD': password
            }
            
            if self.client_secret:
                auth_params['SECRET_HASH'] = self._calculate_secret_hash(username)
            
            try:
                log_auth_diagnostic(
                    auth_type="cognito",
                    status="trying_user_password_auth",
                    details={'flow': 'USER_PASSWORD_AUTH'}
                )
                
                response = self.client.initiate_auth(
                    ClientId=self.client_id,
                    AuthFlow='USER_PASSWORD_AUTH',
                    AuthParameters=auth_params
                )
                
                # Handle challenges if present
                if 'ChallengeName' in response:
                    log_auth_diagnostic(
                        auth_type="cognito",
                        status="challenge_required",
                        details={'challenge': response['ChallengeName']}
                    )
                    return self._handle_auth_challenge(
                        response, username, password,
                        new_password=new_password, interactive=interactive
                    )
                    
                # Extract tokens
                auth_result = response['AuthenticationResult']
                result = {
                    'access_token': auth_result['AccessToken'],
                    'refresh_token': auth_result['RefreshToken'],
                    'id_token': auth_result['IdToken'],
                    'token_type': auth_result['TokenType'],
                    'expires_in': auth_result['ExpiresIn'],
                    'expires_at': int(time.time()) + auth_result['ExpiresIn']
                }
                
                log_auth_diagnostic(
                    auth_type="cognito",
                    status="success",
                    details={
                        'user_id': username,
                        'token_type': result['token_type'],
                        'expires_at': result['expires_at'],
                        'flow': 'USER_PASSWORD_AUTH'
                    }
                )
                
                return result
                
            except ClientError as e:
                if 'USER_PASSWORD_AUTH flow not enabled' in str(e):
                    log_auth_diagnostic(
                        auth_type="cognito",
                        status="fallback_to_srp",
                        details={'reason': 'USER_PASSWORD_AUTH not enabled'}
                    )
                    # Fall back to SRP authentication
                    return self._authenticate_with_srp(
                        username, password,
                        new_password=new_password, interactive=interactive
                    )
                else:
                    raise e
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            
            log_auth_diagnostic(
                auth_type="cognito",
                status="failure",
                details={
                    'error_code': error_code,
                    'user_id': username
                },
                error=e
            )
            
            if error_code == 'NotAuthorizedException':
                raise AuthenticationError("Invalid username or password")
            elif error_code == 'UserNotConfirmedException':
                raise AuthenticationError("User account is not confirmed")
            elif error_code == 'PasswordResetRequiredException':
                raise AuthenticationError("Password reset is required")
            elif error_code == 'UserNotFoundException':
                raise AuthenticationError("User not found")
            else:
                raise AuthenticationError(f"Authentication failed: {error_message}")
    
    def _authenticate_with_srp(self, username: str, password: str, new_password: Optional[str] = None,
                              interactive: bool = True) -> Dict[str, Any]:
        """Authenticate using SRP protocol."""
        from ..utils.logging import log_auth_diagnostic
        
        if not HAS_PYCOGNITO:
            log_auth_diagnostic(
                auth_type="cognito_srp",
                status="failure",
                details={'error': 'pycognito library not available'},
                error=AuthenticationError("Missing pycognito library")
            )
            raise AuthenticationError(
                "SRP authentication requires the 'pycognito' library. "
                "Please install it with: pip install pycognito\n\n"
                "Alternatively, enable 'ALLOW_USER_PASSWORD_AUTH' in your Cognito User Pool App Client settings."
            )
        
        log_auth_diagnostic(
            auth_type="cognito_srp",
            status="attempting",
            details={
                'user_id': username,
                'flow': 'USER_SRP_AUTH',
                'has_pycognito': True
            }
        )
        
        try:
            # Create AWSSRP instance
            aws_srp = AWSSRP(
                username=username,
                password=password,
                pool_id=self.user_pool_id,
                client_id=self.client_id,
                client=self.client
            )
            
            # Initiate Auth with SRP
            auth_params = aws_srp.get_auth_params()
            response = self.client.initiate_auth(
                AuthFlow='USER_SRP_AUTH',
                AuthParameters=auth_params,
                ClientId=self.client_id
            )
            
            # Handle PASSWORD_VERIFIER challenge
            if response.get("ChallengeName") == "PASSWORD_VERIFIER":
                log_auth_diagnostic(
                    auth_type="cognito_srp",
                    status="password_verifier_challenge",
                    details={'challenge': 'PASSWORD_VERIFIER'}
                )
                
                challenge_response = aws_srp.process_challenge(
                    response["ChallengeParameters"], 
                    auth_params
                )
                response = self.client.respond_to_auth_challenge(
                    ClientId=self.client_id,
                    ChallengeName="PASSWORD_VERIFIER",
                    ChallengeResponses=challenge_response
                )
            
            # Handle any additional challenges
            if 'ChallengeName' in response:
                log_auth_diagnostic(
                    auth_type="cognito_srp",
                    status="additional_challenge",
                    details={'challenge': response['ChallengeName']}
                )
                return self._handle_auth_challenge(
                    response, username, password,
                    new_password=new_password, interactive=interactive
                )
                
            # Extract tokens
            auth_result = response['AuthenticationResult']
            result = {
                'access_token': auth_result['AccessToken'],
                'refresh_token': auth_result['RefreshToken'],
                'id_token': auth_result['IdToken'],
                'token_type': auth_result['TokenType'],
                'expires_in': auth_result['ExpiresIn'],
                'expires_at': int(time.time()) + auth_result['ExpiresIn']
            }
            
            log_auth_diagnostic(
                auth_type="cognito_srp",
                status="success",
                details={
                    'user_id': username,
                    'token_type': result['token_type'],
                    'expires_at': result['expires_at'],
                    'flow': 'USER_SRP_AUTH'
                }
            )
            
            return result
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            
            log_auth_diagnostic(
                auth_type="cognito_srp",
                status="failure",
                details={
                    'error_code': error_code,
                    'user_id': username
                },
                error=e
            )
            
            if error_code == 'NotAuthorizedException':
                raise AuthenticationError("Invalid username or password")
            elif error_code == 'UserNotConfirmedException':
                raise AuthenticationError("User account is not confirmed")
            elif error_code == 'PasswordResetRequiredException':
                raise AuthenticationError("Password reset is required")
            elif error_code == 'UserNotFoundException':
                raise AuthenticationError("User not found")
            else:
                raise AuthenticationError(f"SRP Authentication failed: {error_message}")
                
    def _handle_auth_challenge(self, response: Dict[str, Any], username: str, password: str,
                              new_password: Optional[str] = None, interactive: bool = True) -> Dict[str, Any]:
        """Handle Cognito authentication challenges.

        When new_password is supplied, a NEW_PASSWORD_REQUIRED challenge is
        answered without prompting. When interactive is False, any challenge
        that would otherwise require a prompt raises AuthenticationError so the
        caller (e.g. JSON output mode) can surface a clean error.
        """
        challenge_name = response['ChallengeName']
        session = response['Session']
        challenge_parameters = response.get('ChallengeParameters', {})

        if challenge_name == 'NEW_PASSWORD_REQUIRED':
            if not new_password:
                if not interactive:
                    raise AuthenticationError(
                        "A password change is required before you can sign in. "
                        "Re-run the command with --new-password to set a new password."
                    )
                new_password = click.prompt("New password required", hide_input=True, confirmation_prompt=True)
            result = self._respond_to_challenge(challenge_name, {'NEW_PASSWORD': new_password}, session, username)
            # Signal that the password was set while satisfying the challenge so
            # callers (e.g. change-password) can avoid a redundant change.
            result['password_changed_via_challenge'] = True
            return result
        elif challenge_name == 'SMS_MFA':
            if not interactive:
                raise AuthenticationError(
                    "An SMS MFA code is required to sign in, which is not supported in non-interactive mode."
                )
            mfa_code = click.prompt("Enter MFA code from SMS")
            return self._respond_to_challenge(challenge_name, {'SMS_MFA_CODE': mfa_code}, session, username)
        elif challenge_name == 'SOFTWARE_TOKEN_MFA':
            if not interactive:
                raise AuthenticationError(
                    "An MFA code is required to sign in, which is not supported in non-interactive mode."
                )
            mfa_code = click.prompt("Enter MFA code from authenticator app")
            return self._respond_to_challenge(challenge_name, {'SOFTWARE_TOKEN_MFA_CODE': mfa_code}, session, username)
        else:
            raise AuthenticationError(f"Unsupported challenge: {challenge_name}")
            
    def _respond_to_challenge(self, challenge_name: str, challenge_responses: Dict[str, str], 
                            session: str, username: str) -> Dict[str, Any]:
        """Respond to authentication challenge."""
        try:
            # Cognito requires USERNAME in the challenge response payload.
            challenge_responses.setdefault('USERNAME', username)

            if self.client_secret:
                challenge_responses['SECRET_HASH'] = self._calculate_secret_hash(username)

            response = self.client.respond_to_auth_challenge(
                ClientId=self.client_id,
                ChallengeName=challenge_name,
                ChallengeResponses=challenge_responses,
                Session=session
            )
            
            # Check if there are more challenges
            if 'ChallengeName' in response:
                return self._handle_auth_challenge(response, username, "")
                
            # Extract tokens
            auth_result = response['AuthenticationResult']
            return {
                'access_token': auth_result['AccessToken'],
                'refresh_token': auth_result['RefreshToken'],
                'id_token': auth_result['IdToken'],
                'token_type': auth_result['TokenType'],
                'expires_in': auth_result['ExpiresIn'],
                'expires_at': int(time.time()) + auth_result['ExpiresIn']
            }
            
        except ClientError as e:
            error_message = e.response['Error']['Message']
            raise AuthenticationError(f"Challenge response failed: {error_message}")

    def change_password(self, access_token: str, previous_password: str,
                       proposed_password: str) -> Dict[str, Any]:
        """Change the password of the signed-in user via Cognito ChangePassword.

        Args:
            access_token: A valid Cognito access token for the user.
            previous_password: The user's current password.
            proposed_password: The new password to set.
        """
        from ..utils.logging import log_auth_diagnostic

        log_auth_diagnostic(
            auth_type="cognito_change_password",
            status="attempting",
            details={'flow': 'CHANGE_PASSWORD'}
        )

        try:
            self.client.change_password(
                PreviousPassword=previous_password,
                ProposedPassword=proposed_password,
                AccessToken=access_token
            )

            log_auth_diagnostic(
                auth_type="cognito_change_password",
                status="success",
                details={}
            )

            return {'success': True, 'message': 'Password changed successfully'}

        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']

            log_auth_diagnostic(
                auth_type="cognito_change_password",
                status="failure",
                details={'error_code': error_code},
                error=e
            )

            if error_code == 'InvalidPasswordException':
                raise AuthenticationError(
                    f"New password does not meet the password policy: {error_message}"
                )
            elif error_code == 'NotAuthorizedException':
                raise AuthenticationError(
                    "Password change failed: the current password is incorrect or the session is no longer valid."
                )
            elif error_code == 'LimitExceededException':
                raise AuthenticationError(
                    "Attempt limit exceeded for password changes. Please wait and try again later."
                )
            elif error_code == 'UserNotFoundException':
                raise AuthenticationError("User not found")
            else:
                raise AuthenticationError(f"Password change failed: {error_message}")

    def forgot_password(self, username: str) -> Dict[str, Any]:
        """Start a self-service password reset by sending a verification code.

        Calls the Cognito ForgotPassword API, which sends a confirmation code to
        the user's verified email or phone. Returns the code delivery details.
        """
        from ..utils.logging import log_auth_diagnostic

        log_auth_diagnostic(
            auth_type="cognito_forgot_password",
            status="attempting",
            details={'user_id': username, 'flow': 'FORGOT_PASSWORD'}
        )

        try:
            params = {
                'ClientId': self.client_id,
                'Username': username
            }
            if self.client_secret:
                params['SecretHash'] = self._calculate_secret_hash(username)

            response = self.client.forgot_password(**params)

            log_auth_diagnostic(
                auth_type="cognito_forgot_password",
                status="success",
                details={'user_id': username}
            )

            return {'code_delivery': response.get('CodeDeliveryDetails', {})}

        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']

            log_auth_diagnostic(
                auth_type="cognito_forgot_password",
                status="failure",
                details={'error_code': error_code, 'user_id': username},
                error=e
            )

            if error_code == 'UserNotFoundException':
                raise AuthenticationError("User not found")
            elif error_code in ('LimitExceededException', 'TooManyRequestsException'):
                raise AuthenticationError(
                    "Attempt limit exceeded for password reset requests. Please wait and try again later."
                )
            elif error_code == 'InvalidParameterException':
                raise AuthenticationError(
                    "Cannot reset the password for this user. The account may not have a verified "
                    "email or phone number for code delivery."
                )
            else:
                raise AuthenticationError(f"Password reset request failed: {error_message}")

    def confirm_forgot_password(self, username: str, confirmation_code: str,
                               new_password: str) -> Dict[str, Any]:
        """Complete a self-service password reset using the emailed code.

        Calls the Cognito ConfirmForgotPassword API with the verification code
        and the new password.
        """
        from ..utils.logging import log_auth_diagnostic

        log_auth_diagnostic(
            auth_type="cognito_confirm_forgot_password",
            status="attempting",
            details={'user_id': username, 'flow': 'CONFIRM_FORGOT_PASSWORD'}
        )

        try:
            params = {
                'ClientId': self.client_id,
                'Username': username,
                'ConfirmationCode': confirmation_code,
                'Password': new_password
            }
            if self.client_secret:
                params['SecretHash'] = self._calculate_secret_hash(username)

            self.client.confirm_forgot_password(**params)

            log_auth_diagnostic(
                auth_type="cognito_confirm_forgot_password",
                status="success",
                details={'user_id': username}
            )

            return {'success': True, 'message': 'Password reset successfully'}

        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']

            log_auth_diagnostic(
                auth_type="cognito_confirm_forgot_password",
                status="failure",
                details={'error_code': error_code, 'user_id': username},
                error=e
            )

            if error_code == 'CodeMismatchException':
                raise AuthenticationError(
                    "The verification code is incorrect. Request a new code and try again."
                )
            elif error_code == 'ExpiredCodeException':
                raise AuthenticationError(
                    "The verification code has expired. Request a new code and try again."
                )
            elif error_code == 'InvalidPasswordException':
                raise AuthenticationError(
                    f"New password does not meet the password policy: {error_message}"
                )
            elif error_code in ('LimitExceededException', 'TooManyRequestsException'):
                raise AuthenticationError(
                    "Attempt limit exceeded for password reset. Please wait and try again later."
                )
            elif error_code == 'UserNotFoundException':
                raise AuthenticationError("User not found")
            else:
                raise AuthenticationError(f"Password reset failed: {error_message}")

    def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh access token using refresh token."""
        from ..utils.logging import log_auth_diagnostic
        
        log_auth_diagnostic(
            auth_type="cognito_refresh",
            status="attempting",
            details={'flow': 'REFRESH_TOKEN_AUTH'}
        )
        
        try:
            auth_params = {
                'REFRESH_TOKEN': refresh_token
            }
            
            if self.client_secret:
                # For refresh token, we need to use a dummy username for secret hash
                auth_params['SECRET_HASH'] = self._calculate_secret_hash("dummy")
                
            response = self.client.initiate_auth(
                ClientId=self.client_id,
                AuthFlow='REFRESH_TOKEN_AUTH',
                AuthParameters=auth_params
            )
            
            auth_result = response['AuthenticationResult']
            result = {
                'access_token': auth_result['AccessToken'],
                'id_token': auth_result['IdToken'],
                'token_type': auth_result['TokenType'],
                'expires_in': auth_result['ExpiresIn'],
                'expires_at': int(time.time()) + auth_result['ExpiresIn']
            }
            
            log_auth_diagnostic(
                auth_type="cognito_refresh",
                status="success",
                details={
                    'token_type': result['token_type'],
                    'expires_at': result['expires_at']
                }
            )
            
            return result
            
        except ClientError as e:
            error_code = e.response['Error']['Code']
            error_message = e.response['Error']['Message']
            
            log_auth_diagnostic(
                auth_type="cognito_refresh",
                status="failure",
                details={'error_code': error_code},
                error=e
            )
            
            raise AuthenticationError(f"Token refresh failed: {error_message}")
            
    def is_token_valid(self, token_data: Dict[str, Any]) -> bool:
        """Check if token is still valid."""
        if 'expires_at' not in token_data:
            return False
            
        # Check if token expires within the next 5 minutes
        current_time = int(time.time())
        expires_at = token_data['expires_at']
        
        return expires_at > (current_time + 300)  # 5 minutes buffer
        
    def handle_challenge(self, challenge_name: str, challenge_parameters: Dict[str, Any], 
                        session: str) -> Dict[str, Any]:
        """Handle authentication challenges."""
        # This is called from the base class interface
        return self._respond_to_challenge(challenge_name, challenge_parameters, session, "")
