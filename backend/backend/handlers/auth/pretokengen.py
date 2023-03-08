import json

# {
#     "version": "1",
#     "triggerSource": "TokenGeneration_Authentication",
#     "region": "us-east-1",
#     "userPoolId": "...",
#     "userName": "name@example.com",
#     "callerContext": {
#         "awsSdkVersion": "aws-sdk-unknown-unknown",
#         "clientId": "..."
#     },
#     "request": {
#         "userAttributes": {
#             "sub": "...",
#             "cognito:user_status": "CONFIRMED",
#             "email_verified": "true",
#             "email": "name@example.com"
#         },
#         "groupConfiguration": {
#             "groupsToOverride": [
#                 "group1"
#             ],
#             "iamRolesToOverride": [],
#             "preferredRole": null
#         }
#     },
#     "response": {
#         "claimsOverrideDetails": null
#     }
# }
# 

def lambda_handler(event, context):

    result = {}
    result.update(event)
    result.update({
        "response": {
            "claimsOverrideDetails": {
                "claimsToAddOrOverride": {
                    "vams:databases:database1": "crud",
                    "vams:databases:database2": "xrxx",
                    "vams:pipelines": "crud"
                }
            }
        }
    })

    # Return to Amazon Cognito
    return result
