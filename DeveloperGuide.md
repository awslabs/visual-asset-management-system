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

# Installation Steps
### Build & Deploy Steps

1) `cd ./web nvm use` - make sure you're node version matches the project. 

2) `npm run build` - build the web app

3) `cd ../infra npm install` - installs dependencies in package.json

4) If you haven't already bootstrapped your aws account with CDK. `cdk bootstrap aws://101010101010/us-east-1` - replace with your account and region

5) `cdk deploy dev --parameters adminEmailAddress=myuser@amazon.com` - replace with your email address to deploy dev stack

### Deployment Success

1) Navigate to URL provided in `{stackName].WebAppCloudFrontDistributionDomainName{uuid}` from `cdk deploy` output.

2) Check email for temporary account password.

### Multiple Deployments Same Account/Region

Providing a unique stack name in the deployment command `cdk deploy STACK_NAME --parameters adminEmailAddress=myuser@amazon.com` will allow for this to work without conflicts.


# Uninstalling

1. Run `cdk destroy` from infra folder
2. Some resources may not be deleted by CDK (e.g S3 buckets and DynamoDB table) and you will have to delete them via aws cli or using aws console

Note:

After running CDK destroy there might still some resources be running in AWS that will have to be cleaned up manually as CDK does not delete some resources.


# Deployment Overview

The CDK deployment deploys the VAMS stack into your account. The components that are created by this app are:

1. Web app hosted on [cloudfront](https://aws.amazon.com/cloudfront/) distribution
1. [API Gateway](https://aws.amazon.com/api-gateway/) to route front end calls to api handlers.
3. [Lambda](https://aws.amazon.com/lambda/) Lambda handlers are created per API path.
3. [DynamoDB](https://aws.amazon.com/dynamodb/) tables to store Workflows, Assets, Pipelines
4. [S3 Buckets](https://aws.amazon.com/s3/) for assets, cdk deployments and log storage
5. [Jupyter Notebook](https://docs.aws.amazon.com/dlami/latest/devguide/setup-jupyter.html) is created one per pipeline
6. [Sagemaker](https://docs.aws.amazon.com/sagemaker/latest/dg/processing-job.html) Processing jobs are created per pipeline execution
6. [Cognito User Pool](https://docs.aws.amazon.com/cognito/) for authentication


![ARCHITECTURE](./VAMS_Architecture.jpg)

# API Schema:

Please see [Swagger Spec](https://github.com/awslabs/visual-asset-management-system/blob/main/VAMS_API.yaml) for details

# Database Schema

| Table                         | Partition Key | Sort Key   | Attributes                                                                                                                                                    |
| ----------------------------- | ------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| AssetStorageTable             | databaseId    | assetId    | assetLocation, assetName, assetType, currentVersion, description, generated_artifacts, isDistributable, previewLocation, versions |
| JobStorageTable               | jobId         | databaseId |                                                                                                                                                               |
| PipelineStorageTable           | databaseId    | pipelineId | assetType, dateCreated, description, enabled, outputType, pipelineType                                                                                        |
| DatabaseStorageTable          | databaseId    | n/a        | assetCount, dateCreated, description                                                                                                                          |
| WorkflowStorageTable          | databaseId    | workflowId | dateCreated, description, specifiedPipelines, workflow_arn                                                    |
| WorkflowExecutionStorageTable | pk            | sk         | asset_id, database_id, execution_arn, execution_id, workflow_arn, workflow_id, assets                                                                         |
| MetadataStorageTable          | databaseId    | assetId    | Varies with user provided attributes                                                                                                                          |
## AssetStorageTable

| Field               | Data Type | Description                                                                                                    |
| ------------------- | --------- | -------------------------------------------------------------------------------------------------------------- |
| assetLocation       | Map       | S3 Bucket and Key for this asset                                                                               |
| assetName           | String    | The user provided asset name                                                                                   |
| assetType           | String    | The file extension of the asset                                                                                |
| currentVersion      | Map       | The current version of the S3 object                                                                           |
| description         | String    | The user provided description                                                                                  |
| generated_artifacts | Map       | S3 bucket and key references to artifacts generated automatically through pipelines when an asset is uploaded. |
| isDistributable     | Boolean   | Whether the asset is distributable                                                                             |

## PipelineStorageTable

| Field        | Data Type | Description                                   |
| ------------ | --------- | --------------------------------------------- |
| assetType    | String    | File extension of the asset                   |
| dateCreated  | String    | Creation date of this record                  |
| description  | String    | User provided description                     |
| enabled      | Boolean   | Whether this pipeline is enabled              |
| outputType   | String    | File extension of the output asset            |
| pipelineType | String    | Defines the pipeline type — Lambda, SageMaker |

## DatabaseStorageTable

| Field       | Data Type | Description                       |
| ----------- | --------- | --------------------------------- |
| assetCount  | String    | Number of assets in this database |
| dateCreated | String    | Creation date of this record      |
| description | String    | User provided description         |


## WorkflowStorageTable

| Field              | Data Type              | Description                                                         |
| ------------------ | ---------------------- | ------------------------------------------------------------------- |
| dateCreated        | String                 | Creation date of this record                                        |
| description        | String                 | User provided description                                           |
| specifiedPipelines | Map, List, Map, String | List of pipelines given by their name, outputType, and pipelineType |
| workflow_arn       | String                 | The ARN identifying the step function state machine |


## WorkflowExecutionStorageTable


| Field         | Data Type | Description                                  |
| ------------- | --------- | -------------------------------------------- |
| asset_id      | String    | Asset identifier for this workflow execution |
| database_id   | String    | Database to which the asset belongs          |
| execution_arn | String    | The state machine execution arn              |
| execution_id  | String    | Execution identifier                         |
| workflow_arn  | String    | State machine ARN                            |
| workflow_id   | String    | Workflow identifier                          |
| assets        | List, Map | List of Maps of asset objects (see AssetStorageTable for attribute definitions) |
k

## MetadataStorageTable

Attributes are driven by user input. No predetermined fields aside from the partition and sort keys.

# Updating Backend

The dependencies for the backend lambda functions are handled using poetry. If you changed the lambda functions make sure to do a `cdk deploy` to reflect the change.

The lambda handlers are categorized based on the project domain. E.g you will find all assets related functions in `/backend/backend/assets` folder.
# Testing API

Please see the corresponding [Postman Collection](https://github.com/awslabs/visual-asset-management-system/blob/main/VAMS_API_Tests.postman_collection.json) provided. 

Once the solution is deployed, you will have to put in the below details as Global Variables in the Postman Collection

![Postman Variables](https://github.com/awslabs/visual-asset-management-system/blob/main/Postman_Test_Variables.png)
# Updating and Testing Frontend

Within the web folder You can do `npm run start` to start a local frontend application. 
