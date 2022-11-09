import boto3
import os

API_URL              = os.environ['API_URL']
COGNITO_CLIENT_ID    = os.environ['COGNITO_CLIENT_ID']
COGNITO_USER_POOL_ID = os.environ['COGNITO_USER_POOL_ID']
def get_auth_token():
    f = open("./.creds", "r")
    txt = f.readlines()
    split = txt[0].split(',')
    username = split[0]
    password = split[1]

    client = boto3.client('cognito-idp')
    resp = client.initiate_auth(
        ClientId=COGNITO_CLIENT_ID,
        AuthFlow='USER_PASSWORD_AUTH',
        AuthParameters={
            "USERNAME": username,
            "PASSWORD": password
        }
    )

    #print("Access token:", resp['AuthenticationResult']['AccessToken'])
    #print("ID token:", resp['AuthenticationResult']['IdToken'])
    return resp['AuthenticationResult']['AccessToken']
AUTH_TOKEN = get_auth_token()
