"""Base authentication interface for VamsCLI."""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional


class BaseAuthenticator(ABC):
    """Base class for authentication providers."""
    
    @abstractmethod
    def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        """Authenticate user and return tokens."""
        pass
        
    @abstractmethod
    def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh access token using refresh token."""
        pass
        
    @abstractmethod
    def is_token_valid(self, token_data: Dict[str, Any]) -> bool:
        """Check if token is still valid."""
        pass
        
    @abstractmethod
    def handle_challenge(self, challenge_name: str, challenge_parameters: Dict[str, Any], 
                        session: str) -> Dict[str, Any]:
        """Handle authentication challenges."""
        pass
