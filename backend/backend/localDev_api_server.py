from flask import Flask, Blueprint, request, redirect, url_for
import jwt as pyjwt

from handlers.auth import routes as routes_api

from localMockData.mockRoles import mockRoles

vams = Blueprint('vams', __name__)

@vams.after_request
def after_request(response):
  response.headers['Access-Control-Allow-Origin'] = '*'
  response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
  response.headers['Access-Control-Allow-Headers'] = '*'
  # Other headers can be added here if needed
  return response

@vams.route('/api/amplify-config', methods=['GET'])
def amplifyConfig():
  return {
    'api': 'http://localhost:8002/',
    'region': '',
    #'cognitoUserPoolId': 'XX-XXXX-X_abcd1234',
    #'cognitoAppClientId': '1',
    'cognitoIdentityPoolId': 'XXXXX',
    'externalOAuthIdpURL': 'https://localhost:9031',
    'externalOAuthIdpClientId': 'clientId',
    'externalOAuthIdpScope': 'openid',
    'externalOAuthIdpScopeMfa': 'test_mfa_scope',
    'externalOAuthIdpTokenEndpoint': '/as/token.oauth2',
    'externalOAuthIdpAuthorizationEndpoint': '/as/authorization.oauth2',
    'externalOAuthIdpDiscoveryEndpoint': '/.well-known/openid-configuration',
    'bannerHtmlMessage': 'This is a test banner.'
  }

@vams.route('/secure-config', methods=['GET'])
def secureConfig():
  return {
    'featuresEnabled': False,
    'bucket': 'TODO'
  }

@vams.route('/auth/loginProfile/<userId>', methods=['POST'])
def authLoginProfile(userId):
  return {
    "message": {
        "Items": [
            {
                "userId": "vams-test-user",
                "email": "vams-test-user@amazon.com",
            }
        ]
    }
}

@vams.route('/auth/routes', methods=['POST'])
def routes():

  # FIXME Issue with accessing header outside of route.
  access_token = request.headers['Authorization'].split()[1]
  header = pyjwt.get_unverified_header(access_token)
  claims = pyjwt.decode(access_token, options={ 'verify_signature': False })
  print('header', header)
  print('claims', claims)

  #decode_access_token(access_token)

  routes = request.get_json()
  print(routes)

  response = routes_api.lambda_handler({
    'body': request.get_json(),
    'requestContext': {
      'authorizer': { # This is what the JWT authorizer sets, I believe.
        'jwt': {
          'claims': {
            'email': [claims['email']]
          }
        }
      }
    }
  }, {})

  return response['body']

# We're not doing signature verification.
# It is generally ill-advised to use this functionality unless you clearly understand what you are doing.
# Without digital signature information, the integrity or authenticity of the claimset cannot be trusted. FYI.
#header, claims = jwt.verify_jwt(access_token, jwk.JWK.from_json(public_key), ['RS256'])
def decode_access_token(access_token):
  print('access_token', access_token)

  header = pyjwt.get_unverified_header(access_token)
  claims = pyjwt.decode(access_token, options={ 'verify_signature': False })
  print('header', header)
  print('claims', claims)

@vams.route('/databases', methods=['GET'])
def databases():
  access_token = request.headers['Authorization'].split('Bearer ')[1]
  decode_access_token(access_token)

  return {
    'message': {
      'Items': [{
        'databaseId': 'test'
      }]
    }
  }

@vams.route('/roles', methods=['GET', 'POST', 'PUT', 'OPTIONS'], provide_automatic_options=True)
def roles():
  # POST used for create role.
  # PUT used for update role, if mock updating role just refresh the page after you press the update button.
  # Restart API server to clear changes to the mock data for the roles. Session specific.

  global mockRoles

  if request.method == 'GET':
    return mockRoles
  elif request.method == 'POST':
    new_role = request.get_json()
    mockRoles['message']['Items'].append(new_role)
    return redirect(url_for('vams.roles'))
  elif request.method == 'PUT':
    updated_role = request.get_json()
    for index, role in enumerate(mockRoles['message']['Items']):
      if role["roleName"] == updated_role["roleName"]:
        mockRoles['message']['Items'][index] = updated_role
    return redirect(url_for('vams.roles'))

if __name__ == '__main__':
  app = Flask(__name__)
  app.secret_key = 'development'
  app.register_blueprint(vams)
  app.run(debug=True, port=8002, host='0.0.0.0')