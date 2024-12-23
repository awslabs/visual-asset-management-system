import json
import boto3
import requests
import os
from handlers.auth import request_to_claims
from handlers.authz import CasbinEnforcer
from common.constants import STANDARD_JSON_RESPONSE
from customLogging.logger import safeLogger

logger = safeLogger(service_name="CustomConfigAuthLoginProfile")

#Possible environment variables used and passed in for various purposes
try:
    external_oath_idp_url = os.environ["EXTERNAL_OATH_IDP_URL"]
except:
    logger.exception("Failed loading environment variables")

def customAuthProfileLoginWriteOverride(userProfile, lambdaRequestEvent):

    ###################ADD CUSTOM LOGIC TO GET USER PROFILE DATA AT LOGIN FOR USER PROFILE###################

    ###Input User Profile
    # userProfile = {
    #     'userId': userId, #Do not change - fixed for lookup
    #     'email': email
    # }
    ###

    #Default to override incoming email with what's in the claims
    if 'email' in lambdaRequestEvent['requestContext']['authorizer']['jwt']['claims']:
        claimsEmail = lambdaRequestEvent['requestContext']['authorizer']['jwt']['claims']['email']
        if claimsEmail != None and claimsEmail != "":
            userProfile["email"] = claimsEmail

    #Example to reach out to a custom IDP (PingFederate) to grab user data such as email for the profile
    # access_token = lambdaRequestEvent["headers"]["authorization"].split()[1]
    # response = requests.get(f'{external_oath_idp_url}/idp/userinfo.openid', headers={
    #     'Content-Type': 'application/json',
    #     'Authorization': f'Bearer {access_token}'
    # })

    # try:
    #     data = response.json()
    # except requests.JSONDecodeError:
    #     data = None

    # if data and "email" in data:
    #     userProfile["email"] = data["email"]


    #########################################################################################################
    return userProfile