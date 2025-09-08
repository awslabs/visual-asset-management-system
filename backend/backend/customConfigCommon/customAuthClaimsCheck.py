import json
import boto3
import os
from customLogging.logger import safeLogger

logger = safeLogger()

#Possible environment variables used and passed in for various purposes
try:
    cognito_auth_enabled = os.environ["COGNITO_AUTH_ENABLED"]

    if cognito_auth_enabled == "TRUE":
        cognitoClient = boto3.client('cognito-idp')
except:
    logger.exception("Failed loading environment variables")

#Caches
#Cache this for a user based on their auth time to save on external calls
usersMFACache = {}

def customMFATokenScopeCheckOverride(user, lambdaRequest):

    mfaLoginEnabled = False
    try:
        if cognito_auth_enabled == "TRUE":
            #Handle both claims from APIGateway standard authorizer format, lambda authorizers, or lambda cross-calls
            if 'jwt' in lambdaRequest['requestContext']['authorizer']:
                authorizerJwt = lambdaRequest['requestContext']['authorizer']['jwt']['claims']
            elif 'lambda' in lambdaRequest['requestContext']['authorizer']:
                authorizerJwt = lambdaRequest['requestContext']['authorizer']['lambda']
            elif 'lambdaCrossCall' in lambdaRequest:
                authorizerJwt = lambdaRequest['lambdaCrossCall']
            else:
                authorizerJwt = None

            #Cognito MFA check
            #Check if user in a cache lists
            if user in usersMFACache and usersMFACache[user]['auth_time'] == authorizerJwt['auth_time']:
                mfaLoginEnabled = usersMFACache[user]['MFAEnabled']
            else:
                #Make call to cognito for user in JWT token to see if MFA preference is enabled. If it is, the user has authenticated with MFA
                authorizer_jwt_token=lambdaRequest["headers"]["authorization"].split(" ")[1]
                response = cognitoClient.get_user(
                    AccessToken=authorizer_jwt_token
                )
                if 'UserMFASettingList' in response and len(response['UserMFASettingList']) > 0:
                    mfaLoginEnabled = True
                    logger.info("User logged in with MFA")
                    usersMFACache[user] = {'MFAEnabled': True, 'auth_time': authorizerJwt['auth_time']}
                else:
                    mfaLoginEnabled = False
                    logger.info("User logged in without MFA")
                    usersMFACache[user] = {'MFAEnabled': False, 'auth_time': authorizerJwt['auth_time']}
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

    #Conduct MFA sign-in check using custom scope check
    try:
        mfaEnabled = customMFATokenScopeCheckOverride(claims_and_roles["tokens"][0], lambdaRequest)
        claims_and_roles["mfaEnabled"] = mfaEnabled
    except:
        pass

    ###########################################################################################################################
    ###################ADD CUSTOM LOGIC TO CHECK CLAIMS###########################
    ############################################################################################################################


    ############################################################################################################################
    ############################################################################################################################

    return claims_and_roles
