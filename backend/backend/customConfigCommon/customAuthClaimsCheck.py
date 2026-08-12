import os
import boto3
from customLogging.logger import safeLogger

logger = safeLogger()

#Possible environment variables used and passed in for various purposes
#USER_POOL_ID is only expected when Cognito is the auth provider; its absence is
#normal for external OAuth IDP deployments and is not a failure state
try:
    cognito_auth_enabled = os.environ.get("COGNITO_AUTH_ENABLED", "FALSE")
    user_pool_id = os.environ.get("USER_POOL_ID")

    if cognito_auth_enabled == "TRUE":
        cognitoClient = boto3.client('cognito-idp')
        if not user_pool_id:
            logger.warning("USER_POOL_ID not set; Cognito MFA check will default to false")
except:
    logger.exception("Failed loading environment variables")

#Caches
#Cache this for a user based on their auth time to save on external calls
usersMFACache = {}

def customMFATokenScopeCheckOverride(user, authorizerJwtClaims, lambdaRequest):
    """Called by the API Gateway authorizer (common/auth/authorizerCore.py) after JWT
    verification. The result is passed to handler lambdas as the vams:mfaEnabled
    authorizer context value, so handlers never call an IDP themselves.

    user: resolved username from the verified claims
    authorizerJwtClaims: the verified JWT claims dict
    lambdaRequest: the raw authorizer event (headers include the presented bearer token,
                   usable for external IDP userinfo calls)
    """

    mfaLoginEnabled = False
    try:
        if cognito_auth_enabled == "TRUE":
            #Cognito MFA check
            #Without a user pool id there is no pool to query; default to false
            if not user_pool_id:
                return False
            #Check if user in a cache list based on their token auth time (sign-in session)
            auth_time = (authorizerJwtClaims or {}).get('auth_time')
            if user in usersMFACache and usersMFACache[user]['auth_time'] == auth_time:
                mfaLoginEnabled = usersMFACache[user]['MFAEnabled']
            else:
                #Make call to cognito for the user to see if MFA preference is enabled. If it is, the user has authenticated with MFA
                response = cognitoClient.admin_get_user(
                    UserPoolId=user_pool_id,
                    Username=user
                )
                if response and 'UserMFASettingList' in response and len(response['UserMFASettingList']) > 0:
                    mfaLoginEnabled = True
                    logger.info("User logged in with MFA")
                else:
                    mfaLoginEnabled = False
                    logger.info("User logged in without MFA")
                usersMFACache[user] = {'MFAEnabled': mfaLoginEnabled, 'auth_time': auth_time}
        else:

    ############################################################################################################################
    ###################ADD CUSTOM EXTERNAL OAUTH IDP LOGIC TO CHECK IF LOGGED IN USER HAS MFA ENABLED###########################
    ############################################################################################################################

            #External OAUTH IDP MFA check
            mfaLoginEnabled = False


    ############################################################################################################################
    ############################################################################################################################

    except Exception as e:
        logger.exception(e)
        logger.exception("Failed to check if user logged in with MFA... defaulting to false")
        mfaLoginEnabled = False
    #Return true/false
    return mfaLoginEnabled

def customAuthClaimsCheckOverride(claims_and_roles, lambdaRequest):
    """Called by handler lambdas (handlers/auth/request_to_claims). MFA status is already
    resolved at authorization time and read from the vams:mfaEnabled claim before this
    hook runs; use this hook for additional handler-time claims checks."""

    ###########################################################################################################################
    ###################ADD CUSTOM LOGIC TO CHECK CLAIMS###########################
    ############################################################################################################################


    ############################################################################################################################
    ############################################################################################################################

    return claims_and_roles
