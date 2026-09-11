# Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0

def customMFATokenScopeCheckOverride(user, authorizerJwtClaims, lambdaRequest):
    """
    Mock implementation of the customMFATokenScopeCheckOverride function for testing purposes.

    Args:
        user: Resolved username from the verified claims
        authorizerJwtClaims: The verified JWT claims dict
        lambdaRequest: The raw authorizer event

    Returns:
        False (no MFA in the mock implementation)
    """
    return False


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
