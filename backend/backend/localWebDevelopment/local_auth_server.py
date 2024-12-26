# References:
# * https://auth0.com/docs/get-started/authentication-and-authorization-flow/authorization-code-flow-with-pkce
import random
import string
from flask import Flask, Blueprint, render_template, request, redirect, jsonify, make_response
import jwt as pyjwt

oauth2_routes = Blueprint('oauth', __name__)

@oauth2_routes.after_request
def after_request(response):
  response.headers['Access-Control-Allow-Origin'] = '*'
  response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
  response.headers['Access-Control-Allow-Headers'] = '*'
  # Other headers can be added here if needed
  return response

code_challenges={}
codes={}

@oauth2_routes.route('/as/authorization.oauth2', methods=['GET', 'POST'])
def authorize():
  print('/authorize', request.query_string.decode())

  code_challenges[request.args.get('code_challenge')] = 'n/a'
  print('code_challenges', code_challenges)

  return redirect(f'/login?{request.query_string.decode()}')

@oauth2_routes.route('/login', methods=['GET'])
def loginForm():
  print('/login', request.query_string.decode())

  return render_template('login.html')

char_set = string.ascii_uppercase + string.digits

@oauth2_routes.route('/signin', methods=['POST'])
def signin():
  print('/signin', request.query_string.decode())

  request_data = request.form
  print(request_data)

  # Assume good and authenticated.

  state = request_data['state']
  print('state', state)

  redirect_uri = request_data['redirect_uri']
  print('redirect_uri', redirect_uri)

  username = request_data['username']
  print('username', username)

  code = ''.join(random.sample(char_set*6, 6))

  codes[code] = username
  print('codes', codes)

  return redirect(f'{redirect_uri}?code={code}&state={state}')

@oauth2_routes.route('/as/token.oauth2', methods=['GET', 'POST'])
def token():
  print('/token', request.query_string.decode(), request.data)

  # print('/request.data', request.data)

  # if request.data and request.data['grant_type'] == 'refresh_token':
  #   return None
  # Test 403 error, ensure user get's signed out
  # return make_response("Auth error", 403)


  payload = {
    'iss':'default',
    'sub':'vams-test-user',
    'aud':'AUDIENCE',
    'role': 'user',
    'permission': 'read',
    'email': 'vams-test-user@amazon.com' #codes[request.args.get('code')] FIXME
  }

  id_token = pyjwt.encode(payload, key=None, algorithm=None)
  access_token = pyjwt.encode(payload, key=None, algorithm=None)
  refresh_token = pyjwt.encode(payload, key=None, algorithm=None)

  print('access_token', access_token)

  return jsonify({
    'id_token': id_token, # Proves user was authenticated.
    'access_token': access_token, # Proves app has been authorized.
    'token_type': 'Bearer',
    'expires_in': 60, # Seconds.
    'refresh_token': refresh_token
  })

#https://docs.pingidentity.com/pingdirectory/latest/managing_access_control/pd_ds_mock_access_token_validator.html
#curl -k -X GET https://localhost:1443/directory/v1/Me -H 'Authorization: Bearer {'active': true, 'sub':'user.1', 'scope':'email profile', 'client_id':'client1'}'

@oauth2_routes.route('/directory/v1/<path:person>', methods=['GET'])
def jwtValidator(person):
  print('/directory', request.query_string.decode(), request.data)

  return {
    'ok': 'yes'
  }

endpoint='https://localhost:9031'

@oauth2_routes.route('/default', methods=['GET'])
def defaultIssuer():
  return {}

@oauth2_routes.route('/idp/userinfo.openid', methods=['GET'])
def userInfo(person):
  print('/idp/userinfo.openid', request.query_string.decode(), request.data)

  return {
    'email': 'yes'
  }

@oauth2_routes.route('/.well-known/openid-configuration', methods=['GET'])
def wellKnown():
  return {
    'issuer':f'{endpoint}/default',
    'authorization_endpoint':f'{endpoint}/default/authorize',
    'end_session_endpoint' : f'{endpoint}/default/endsession',
    'revocation_endpoint' : f'{endpoint}/default/revoke',
    'token_endpoint':f'{endpoint}/default/token',
    'userinfo_endpoint':f'{endpoint}/default/userinfo',
    'jwks_uri':f'{endpoint}/default/jwks',
    'introspection_endpoint':f'{endpoint}/default/introspect',
    'response_types_supported':[
      'code',
      'none',
      'id_token',
      'token'
    ],
    'response_modes_supported':[
      'query',
      'fragment',
      'form_post'
    ],
    'subject_types_supported':[
      'public'
    ],
    'id_token_signing_alg_values_supported':[
      'ES256',
      'ES384',
      'RS256',
      'RS384',
      'RS512',
      'PS256',
      'PS384',
      'PS512'
    ],
    'code_challenge_methods_supported':[
      'plain',
      'S256'
    ]
  }

if __name__ == '__main__':
  app = Flask(__name__, template_folder='oauth2_local_templates')
  app.secret_key = 'development'
  app.register_blueprint(oauth2_routes)
  app.run(debug=True, port=9031, ssl_context='adhoc', host='0.0.0.0')