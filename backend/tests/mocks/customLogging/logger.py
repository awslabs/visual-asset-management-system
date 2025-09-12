# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

class safeLogger:
    """
    Mock implementation of the safeLogger class for testing purposes.
    
    This class provides a simplified version of the safeLogger that logs
    messages to the console during testing.
    """
    
    def __init__(self, service="Test", service_name=None):
        """
        Initialize the safeLogger with a service name.
        
        Args:
            service: The name of the service using the logger
            service_name: Alternative parameter name for service
        """
        self.service = service_name if service_name is not None else service
        
    def info(self, message):
        """
        Log an informational message.
        
        Args:
            message: The message to log
        """
        # In the mock implementation, we don't actually log anything
        pass
        
    def warning(self, message):
        """
        Log a warning message.
        
        Args:
            message: The message to log
        """
        # In the mock implementation, we don't actually log anything
        pass
        
    def error(self, message):
        """
        Log an error message.
        
        Args:
            message: The message to log
        """
        # In the mock implementation, we don't actually log anything
        pass
        
    def exception(self, message):
        """
        Log an exception message.
        
        Args:
            message: The message to log
        """
        # In the mock implementation, we don't actually log anything
        pass
