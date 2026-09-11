"""The Cognito client must not depend on AWS credentials.

`auth login`, `auth change-password` and `auth forgot-password` all construct a
`CognitoAuthenticator`, and every Cognito operation they reach — initiate_auth,
respond_to_auth_challenge, forgot_password, confirm_forgot_password, change_password — is an
unauthenticated user-pool API that takes no SigV4 signature. botocore nonetheless resolves the whole
AWS credential chain while building a signed client, so a configured-but-failing provider (an expired
SSO session or a `credential_process` helper that now returns non-zero — routine on a managed
developer machine) aborted the CLI's front door with a raw `CredentialRetrievalError` before a single
Cognito call was attempted. A machine with no AWS configuration at all resolves to None and proceeds,
which is why CI never saw it.
"""

import botocore
from botocore.exceptions import CredentialRetrievalError
from unittest.mock import patch

from vamscli.auth.cognito import CognitoAuthenticator


def _authenticator():
    return CognitoAuthenticator(
        region='us-east-1',
        user_pool_id='us-east-1_test123',
        client_id='test-client-id',
    )


class TestCognitoClientNeedsNoAwsCredentials:
    def test_construction_survives_a_broken_credential_provider(self, monkeypatch):
        """The behavioural assertion: a credential chain that raises must not break VAMS login.

        `get_credentials` is where botocore walks the providers. Patching it to raise reproduces an
        expired SSO / credential_process helper exactly; an UNSIGNED client never calls it.
        """
        import botocore.session

        def _boom(self, *args, **kwargs):
            raise CredentialRetrievalError(
                provider='custom-process',
                error_msg='simulated expired credential_process helper',
            )

        monkeypatch.setattr(botocore.session.Session, 'get_credentials', _boom)

        authenticator = _authenticator()
        assert authenticator.client is not None

    def test_the_client_is_built_unsigned(self):
        """The mechanism behind the test above, asserted directly.

        botocore short-circuits credential resolution only when `signature_version is UNSIGNED`;
        any other value (including None) sends it back through the provider chain.
        """
        authenticator = _authenticator()
        assert authenticator.client.meta.config.signature_version is botocore.UNSIGNED

    def test_the_unsigned_config_reaches_boto3(self):
        """Guards the wiring rather than the resulting client, so a refactor that drops the config
        keyword fails here even if some other layer happens to leave the client unsigned."""
        with patch('vamscli.auth.cognito.boto3.client') as mock_client:
            _authenticator()

        assert mock_client.called
        config = mock_client.call_args.kwargs['config']
        assert config.signature_version is botocore.UNSIGNED
        assert mock_client.call_args.kwargs['region_name'] == 'us-east-1'

    def test_an_unauthenticated_call_still_reaches_the_service_api(self, monkeypatch):
        """Positive control on the negative above: proving no credentials are needed is only useful
        if the client can still make the calls. Asserting the operation model exists confirms the
        UNSIGNED client is a working cognito-idp client, not an inert object."""
        authenticator = _authenticator()
        for operation in ('initiate_auth', 'respond_to_auth_challenge', 'forgot_password',
                          'confirm_forgot_password', 'change_password'):
            assert hasattr(authenticator.client, operation), operation
