"""Authentication module for VamsCLI."""

from .base import BaseAuthenticator
from .cognito import CognitoAuthenticator

__all__ = ['BaseAuthenticator', 'CognitoAuthenticator']
