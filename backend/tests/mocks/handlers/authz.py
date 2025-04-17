# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

class CasbinEnforcer:
    """
    Mock implementation of the CasbinEnforcer class for testing purposes.
    
    This class provides a simplified version of the CasbinEnforcer that always
    returns True for enforce and enforceAPI methods.
    """
    
    def __init__(self, claims_and_roles):
        """
        Initialize the CasbinEnforcer with claims and roles.
        
        Args:
            claims_and_roles: Dictionary containing user claims and roles
        """
        self.claims_and_roles = claims_and_roles
        
    def enforce(self, asset_object, action):
        """
        Check if the user has permission to perform the action on the asset.
        
        Args:
            asset_object: The asset object to check permissions for
            action: The action to check permissions for
            
        Returns:
            True (always allows access in this mock implementation)
        """
        return True
        
    def enforceAPI(self, event):
        """
        Check if the user has permission to access the API endpoint.
        
        Args:
            event: The API Gateway event
            
        Returns:
            True (always allows access in this mock implementation)
        """
        return True
