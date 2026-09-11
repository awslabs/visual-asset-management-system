# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

class safeLogger:
    """
    Mock implementation of the safeLogger class for testing purposes.

    Stands in for the AWS Lambda Powertools ``Logger`` that the real ``safeLogger()``
    returns, so every method mirrors that interface and accepts ``*args, **kwargs``.
    Powertools is routinely called as ``logger.info(msg, extra={...})``, and a mock
    that took one positional argument raised ``TypeError`` from inside production code
    the moment a handler used the structured form -- a failure that surfaces far from
    its cause and only in whichever test happens to import that handler first.
    """

    def __init__(self, service="Test", service_name=None, **kwargs):
        """
        Initialize the safeLogger with a service name.

        Args:
            service: The name of the service using the logger
            service_name: Alternative parameter name for service
        """
        self.service = service_name if service_name is not None else service

    def info(self, message, *args, **kwargs):
        """Log an informational message."""
        # In the mock implementation, we don't actually log anything
        pass

    def warning(self, message, *args, **kwargs):
        """Log a warning message."""
        # In the mock implementation, we don't actually log anything
        pass

    # Powertools exposes warn as an alias of warning.
    def warn(self, message, *args, **kwargs):
        """Log a warning message."""
        pass

    def error(self, message, *args, **kwargs):
        """Log an error message."""
        # In the mock implementation, we don't actually log anything
        pass

    def critical(self, message, *args, **kwargs):
        """Log a critical message."""
        pass

    def exception(self, message, *args, **kwargs):
        """Log an exception message."""
        # In the mock implementation, we don't actually log anything
        pass

    def debug(self, message, *args, **kwargs):
        """Log a debug message."""
        # In the mock implementation, we don't actually log anything
        pass

    def append_keys(self, **kwargs):
        """Attach structured keys to subsequent records."""
        pass

    def remove_keys(self, keys=None):
        """Detach structured keys."""
        pass

    def set_correlation_id(self, value=None):
        """Set the correlation id."""
        pass


def mask_sensitive_data(data):
    """Mock of mask_sensitive_data: returns the data unchanged.

    The real implementation redacts sensitive keys; tests do not depend on
    redaction, so the mock is a pass-through.
    """
    return data
