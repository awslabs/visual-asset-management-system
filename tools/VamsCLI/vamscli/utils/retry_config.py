"""Retry configuration utilities for handling 429 throttling errors."""

import os
import random
import time
from typing import Optional

from ..constants import (
    DEFAULT_MAX_RETRY_ATTEMPTS,
    DEFAULT_INITIAL_RETRY_DELAY,
    DEFAULT_MAX_RETRY_DELAY,
    DEFAULT_RETRY_BACKOFF_MULTIPLIER,
    DEFAULT_RETRY_JITTER
)


class RetryConfig:
    """Configuration for retry behavior with exponential backoff."""
    
    def __init__(self):
        """Initialize retry configuration from environment variables."""
        self.max_retry_attempts = self._get_env_int(
            'VAMS_CLI_MAX_RETRY_ATTEMPTS', 
            DEFAULT_MAX_RETRY_ATTEMPTS
        )
        self.initial_retry_delay = self._get_env_float(
            'VAMS_CLI_INITIAL_RETRY_DELAY', 
            DEFAULT_INITIAL_RETRY_DELAY
        )
        self.max_retry_delay = self._get_env_float(
            'VAMS_CLI_MAX_RETRY_DELAY', 
            DEFAULT_MAX_RETRY_DELAY
        )
        self.backoff_multiplier = self._get_env_float(
            'VAMS_CLI_RETRY_BACKOFF_MULTIPLIER', 
            DEFAULT_RETRY_BACKOFF_MULTIPLIER
        )
        self.jitter = self._get_env_float(
            'VAMS_CLI_RETRY_JITTER', 
            DEFAULT_RETRY_JITTER
        )
        
        # Validate configuration
        self._validate_config()
    
    def _get_env_int(self, env_var: str, default: int) -> int:
        """Get integer value from environment variable with fallback to default."""
        try:
            value = os.environ.get(env_var)
            if value is not None:
                return int(value)
        except (ValueError, TypeError):
            pass
        return default
    
    def _get_env_float(self, env_var: str, default: float) -> float:
        """Get float value from environment variable with fallback to default."""
        try:
            value = os.environ.get(env_var)
            if value is not None:
                return float(value)
        except (ValueError, TypeError):
            pass
        return default
    
    def _validate_config(self):
        """Validate retry configuration values."""
        if self.max_retry_attempts < 0:
            self.max_retry_attempts = 0
        elif self.max_retry_attempts > 20:  # Reasonable upper limit
            self.max_retry_attempts = 20
            
        if self.initial_retry_delay < 0.1:
            self.initial_retry_delay = 0.1
        elif self.initial_retry_delay > 30:
            self.initial_retry_delay = 30
            
        if self.max_retry_delay < self.initial_retry_delay:
            self.max_retry_delay = self.initial_retry_delay * 10
        elif self.max_retry_delay > 300:  # 5 minutes max
            self.max_retry_delay = 300
            
        if self.backoff_multiplier < 1.0:
            self.backoff_multiplier = 1.0
        elif self.backoff_multiplier > 5.0:
            self.backoff_multiplier = 5.0
            
        if self.jitter < 0.0:
            self.jitter = 0.0
        elif self.jitter > 0.5:  # Max 50% jitter
            self.jitter = 0.5
    
    def calculate_delay(self, attempt: int, retry_after: Optional[int] = None) -> float:
        """
        Calculate delay for retry attempt with exponential backoff and jitter.
        
        Args:
            attempt: Current retry attempt number (0-based)
            retry_after: Optional Retry-After header value in seconds
            
        Returns:
            Delay in seconds before next retry attempt
        """
        if retry_after is not None and retry_after > 0:
            # Respect server's Retry-After header, but apply jitter and max delay
            base_delay = min(retry_after, self.max_retry_delay)
        else:
            # Calculate exponential backoff delay
            base_delay = min(
                self.initial_retry_delay * (self.backoff_multiplier ** attempt),
                self.max_retry_delay
            )
        
        # Apply jitter to prevent thundering herd
        if self.jitter > 0:
            jitter_range = base_delay * self.jitter
            jitter_offset = random.uniform(-jitter_range, jitter_range)
            delay = max(0.1, base_delay + jitter_offset)  # Minimum 0.1 second delay
            # Ensure delay doesn't exceed max_retry_delay even with jitter
            delay = min(delay, self.max_retry_delay)
        else:
            delay = base_delay
            
        return delay
    
    def should_retry(self, attempt: int) -> bool:
        """
        Determine if we should retry based on current attempt number.
        
        Args:
            attempt: Current retry attempt number (0-based)
            
        Returns:
            True if we should retry, False otherwise
        """
        return attempt < self.max_retry_attempts
    
    def sleep_with_progress(self, delay: float, attempt: int, total_attempts: int, 
                           show_progress: bool = True) -> None:
        """
        Sleep for the specified delay with optional progress indication.
        
        Args:
            delay: Delay in seconds
            attempt: Current attempt number (1-based for display)
            total_attempts: Total number of attempts
            show_progress: Whether to show progress indication
        """
        if not show_progress or delay < 1.0:
            time.sleep(delay)
            return
            
        # Show progress for longer delays
        import sys
        
        print(f"Rate limited. Retrying in {delay:.1f}s (attempt {attempt}/{total_attempts})...", 
              end='', flush=True)
        
        # Sleep in small increments to allow for interruption
        remaining = delay
        while remaining > 0:
            sleep_time = min(0.1, remaining)
            time.sleep(sleep_time)
            remaining -= sleep_time
            
        print(" retrying now.", flush=True)


# Global retry configuration instance
_retry_config = None


def get_retry_config() -> RetryConfig:
    """Get the global retry configuration instance."""
    global _retry_config
    if _retry_config is None:
        _retry_config = RetryConfig()
    return _retry_config


def reset_retry_config():
    """Reset the global retry configuration (useful for testing)."""
    global _retry_config
    _retry_config = None
