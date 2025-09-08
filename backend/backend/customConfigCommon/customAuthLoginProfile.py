import json
import boto3
import requests
import os
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger

logger = safeLogger()

#Possible environment variables used and passed in for various purposes
try:
    external_oath_idp_url = os.environ["EXTERNAL_OATH_IDP_URL"]
except:
    logger.exception("Failed loading environment variables")

def customAuthProfileLoginWriteOverride(userProfile, lambdaRequestEvent):

    #Handle both claims from APIGateway standard authorizer format, lambda authorizers, or lambda cross-calls
    if 'jwt' in lambdaRequestEvent['requestContext']['authorizer'] and 'claims' in lambdaRequestEvent['requestContext']['authorizer']['jwt']:
        claims = lambdaRequestEvent['requestContext']['authorizer']['jwt']['claims']
    elif 'lambda' in lambdaRequestEvent['requestContext']['authorizer']:
        claims = lambdaRequestEvent['requestContext']['authorizer']['lambda']
    elif 'lambdaCrossCall' in lambdaRequestEvent: #currently this case wouldn't apply for now due to check above
        claims = lambdaRequestEvent['lambdaCrossCall']
    else:
        claims = {}

    ###################ADD CUSTOM LOGIC TO GET USER PROFILE DATA AT LOGIN FOR USER PROFILE###################

    ###Input User Profile
    # userProfile = {
    #     'userId': userId, #Do not change - fixed for lookup
    #     'email': email
    # }
    ###

    #Default to override incoming email with what's in the claims
    if 'email' in claims:
        claimsEmail = claims['email']
        if claimsEmail != None and claimsEmail != "":
            userProfile["email"] = claimsEmail

    #Example to reach out to a custom IDP (PingFederate) to grab user data such as email for the profile
    # access_token = lambdaRequestEvent["headers"]["authorization"].split()[1]
    # response = requests.get(f'{external_oath_idp_url}/idp/userinfo.openid', headers={
    #     'Content-Type': 'application/json',
    #     'Cache-Control': 'no-cache, no-store',
    #     'Authorization': f'Bearer {access_token}'
    # })

    # try:
    #     data = response.json()
    # except requests.JSONDecodeError:
    #     data = None

    # if data:
    #     if "email" in data:
    #         userProfile["email"] = data["email"]
    #     if "name" in data:
    #         userProfile["name"] = data["name"]


    #########################################################################################################
    return userProfile