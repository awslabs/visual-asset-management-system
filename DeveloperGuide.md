<h1> VAMS Developer Guide </h1>

# Installing VAMS

VAMS is installed using AWS CDK.

## Prerequisites
* Python 3.8
* Node 16.x
* Node Version Manager (nvm)
* AWS cli
* CDK cli
* Amplify cli
* Programatic access to AWS account at minimum access levels outlined above.

## Installation Steps
### A. Build the Web App. 

1. `cd ./web nvm use` - make sure you're node version matches the project. 

   Success: Console outputs `Now using node v16.11.0 (npm v8.0.0)`

   Failure Scenario 1: nvm not installed, visit [nvm.sh](nvm.sh)

   Failure Scenario 2: Console output `You need to run "nvm install v16.11.0" to install it before using it.`, follow instructions.

2) `npm run build` - builds the web app

   Success: Console output includes `The build folder is ready to be deployed.`

   Failure Scenario 1: npm not installed, visit [nodejs.org](nodejs.org)

   Failure Scenario 2: npm install fails with console error, visit [https://docs.npmjs.com/common-errors](https://docs.npmjs.com/common-errors)

### B. Build the Infrastructure

1) `cd ../infra npm install` - installs dependencies in package.json

   Success: Console output includes `added # packages, and audited # packages in #s` without error message outlined here: [https://docs.npmjs.com/common-errors](https://docs.npmjs.com/common-errors)

   Failure Scenario 1: npm not installed, visit [nodejs.org](nodejs.org)

   Failure Scenario 2: npm install fails with error message, visit [https://docs.npmjs.com/common-errors](https://docs.npmjs.com/common-errors)

### C. Bootstrap AWS CDK (Skip if done already)

1. `cdk bootstrap aws://101010101010/us-east-1` - replace with your account and region

### D. Deploy The application

1. `npm run deploy.dev -- --parameters adminEmailAddress=<youreamil>@<mailprovider>.com`



# Uninstalling

1. Run `cdk destroy` from infra folder

Note:

After running CDK destroy there might still some resources be running in AWS that will have to be cleaned up manually as CDK does not delete some resources.


# Deployment Overview

The CDK deployment deploys the VAMS stack into your account. The components that are created by this app are:

![ARCHITECTURE](./seed/architecture.png)

1. Web app hosted on [cloudfront](https://aws.amazon.com/cloudfront/) distribution
1. [API Gateway](https://aws.amazon.com/api-gateway/) to route front end calls to api handlers.
3. [Lambda](https://aws.amazon.com/lambda/) Lambda handlers are created per API path.
3. [DynamoDB](https://aws.amazon.com/dynamodb/) tables to store Workflows, Assets, Pipelines
4. [S3 Buckets](https://aws.amazon.com/s3/) for assets, cdk deployments and log storage
5. [Jupyter Notebook](https://docs.aws.amazon.com/dlami/latest/devguide/setup-jupyter.html) is created one per pipeline
6. [Sagemaker](https://docs.aws.amazon.com/sagemaker/latest/dg/processing-job.html) Processing jobs are created per pipeline execution
6. [Cognito User Pool](https://docs.aws.amazon.com/cognito/) for authentication


# API Schema:

Please see [Swagger Spec](../vams/VAMS_API.yaml) for details

# Testing API

Please see the corresponding [Postman Collection](../vams/VAMS_API_Tests.postman_collection.json) provided. 

Once the solution is deployed, you will have to put in the below details as Global Variables in the Postman Collection

![Postman Variables](../vams/Postman_Test_Variables.png)