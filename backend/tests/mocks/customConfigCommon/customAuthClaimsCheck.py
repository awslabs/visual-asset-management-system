# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

def customAuthClaimsCheckOverride(claims_and_roles, request):
    """
    Mock implementation of the customAuthClaimsCheckOverride function for testing purposes.
    
    Args:
        claims_and_roles: Dictionary containing user claims and roles
        request: The API Gateway event
        
    Returns:
        The same claims_and_roles dictionary (no modifications in the mock implementation)
    """
    # In the mock implementation, we just return the claims_and_roles without modification
    return claims_and_roles
