<h1> VAMS Developer Guide </h1>

## Install

### Requirements

-   Python 3.10
-   Poetry (for managing python dependencies in the VAMS backend)
-   Docker
-   Node >=18.7
-   Yarn >=1.22.19
-   Node Version Manager (nvm)
-   AWS cli
-   AWS CDK cli
-   Programatic access to AWS account at minimum access levels outlined above.

### Deploy VAMS for the First Time

#### Build & Deploy Steps (Linux/Mac)

VAMS Codebase is changing frequently and we recommend you checkout the stable released version from github.

You can identify stable releases by their tag. Fetch the tags `git fetch --all --tags` and then `git checkout tags/TAG` or `git checkout -b TAG tags/TAG` where TAG is the actual desired tag. A list of tags is found by running `git tag --list` or on the [releases page](https://github.com/awslabs/visual-asset-management-system/releases).

1. `cd ./web && nvm use` - make sure you're node version matches the project. Make sure Docker daemon is running.

2. `yarn install` - make sure you install the packages required by the web app

3. `npm run build` - build the web app.

4. `cd ../infra && npm install` - installs dependencies defined in package.json.

5. If you haven't already bootstrapped your aws account with CDK. `cdk bootstrap aws://101010101010/us-east-1` - replace with your account and region. If you are boostrapping a GovCloud account, run `export AWS_REGION=[gov-cloud-region]` as the AWS SDK needs to be informed to use GovCloud endpoints.

6. Modify the `config.json` in `/infra/config` to set the VAMS deployment parameters and features you would like to deploy. Recommended minimum fields to update are `region`, `adminEmailAddress`, and `baseStackName` when using the default provided template. More information about the configuration options can be found in the Configuration Options section below.

7. (Optional) Override the the CDK stack name and region for deployment with environment variables `export AWS_REGION=us-east-1 && export STACK_NAME=dev` - replace with the region you would like to deploy to and the name you want to associate with the cloudformation stack that the CDK will deploy.

8. (FIPS Use Only) If deploying with FIPS, enable FIPS environment variables for AWS CLI `export AWS_USE_FIPS_ENDPOINT=true` and enable `app.useFips` in the `config.json` configuration file in `/infra/config`

9. (External VPC Import Only) If importing an external VPC with subnets in the `config.json` configuration, run `cdk deploy --all --require-approval never --context loadContextIgnoreVPCStacks=true` to import the VPC ID/Subnets context and deploy all non-VPC dependant stacks first. Failing to run this with the context setting or configuration setting of `loadContextIgnoreVPCStacks` will cause the final deployment of all stacks step to fail.

10. `npm run deploy.dev` - An account is created in an AWS Cognito User Pool using the email address specified in the infrastructure config file. Expect an email from <no-reply@verificationemail.com> with a temporary password.

    10a. Ensure that docker is running before deploying as a container will need to be built

#### Deployment Success

1. Navigate to URL provided in `{stackName].WebAppCloudFrontDistributionDomainNameOutput` (Cloudfront) or `{stackName].WebsiteEndpointURLOutput` (ALB) from `cdk deploy` output.

2. Check email for temporary account password to log in with the email address you provided.

### Multiple Deployments With Different or Same Region in Single Account

You can change the region and deploy a new instance of VAMS my setting the environment variables to new values (`export AWS_REGION=us-east-1 && export STACK_NAME=dev`) and then running `npm run deploy.dev` again.

### Deploy VAMS Updates

To deploy customzations or updates to VAMS, you can update the stack by running `cdk deploy --all`. A changeset is created and deployed to your stack.

Please note, depending on what changes are in flight, VAMS may not be available to users in part or in whole during the deployment. Please read the change log carefully and test changes before exposing your users to new versions.

Deployment data migration documentation and scripts between major VAMS version deployments are located in `/infra/deploymentDataMigration`

### SAML Authentication

SAML authentication enables you to provision access to your VAMS instance using your organization's federated identity provider such as Auth0, Active Directory, or Google Workspace.

If the configuration file `/infra/config/config.json` set `AuthProvider.UseCognito.UseSaml` to `true` to enable, `false` for disabled

You need your SAML metadata url, and then you can fill out the required information in `infra/config/saml-config.ts`.

The required information is as follows:

-   `name` identifies the name of your identity provider.
-   `cognitoDomainPrefix` is a DNS compatible, globally unique string used as a subdomain of cognito's signon url.
-   `metadataContent` is a url of your SAML metadata. This can also point to a local file if `metadataType` is changed to `cognito.UserPoolIdentityProviderSamlMetadataType.FILE`.

Then you can deploy the infra stack by running `cdk deploy --all` if you have already deployed or using the same build and deploy steps as above.

The following stack outputs are required by your identity provider to establish trust with your instance of VAMS:

-   SAML IdP Response URL
-   SP urn / Audience URI / SP entity ID
-   CloudFrontDistributionUrl for the list of callback urls. Include this url with and without a single trailing slack (e.g., <https://example.com> and <https://example.com/>)

### JWT Token Authentication

VAMS API requires a valid authorization token that will be validated on each call against the configured authentication system (eg. Cognito).

All API calls require that the below claims be included as part of that JWT token. This is done via the `pretokengen` lambda that is triggered on token generation in Cognito. If implementing a different authentication OATH system, developers must ensure these claim token are included in their JWT token.

The critical component right now is that the authenticated VAMS username be included in the `tokens` array. Roles and externalAttributes are optional right now as they are looked up at runtime.

```
{
    'claims': {
        "tokens": [<username>],
        "roles": [<roles>],
        "externalAttributes": []
    }
}
```

### Local Docker Builds - Custom Build Settings

If you are needing to add custom settings to your local docker builds, such as adding custom SSL CA certificates to get through HTTPS proxies, modify the following docker build files:

1. `/infra/config/dockerDockerfile-customDependencyBuildConfig` - Docker file for all local packaging environments such as Lambda Layers and/or Custom Resources. Add extra lines to end of file.
2. `/backendVisualizerPipelines/pc/Dockerfile_PDAL` - Docker file for Visualizer Pipeline container for PointClouds - PDAL Stage. Add extra lines above any package install or downloads.
3. `/backendVisualizerPipelines/pc/Dockerfile_Potree` - Docker file for Visualizer Pipeline container for PointClouds - PotreeConverter Stage. Add extra lines above any package install or downloads.

### CDK Deploy with Custom SSL Cert Proxy

If you need to deploy VAMS CDK using custom SSL certificates due to internal organization HTTPS proxy requirements, follow the below instructions.

1. Download to your host machine the .pem certificate that is valid for your HTTPS proxy to a specific path
2. Set the following environments variables to the file path in step 1: `$AWS_CA_BUNDLE` and `$NODE_EXTRA_CA_CERTS`
3. Modify the Dockerbuild files specified and instructed in ![Local Docker BUilds](#Local-Docker-Builds---Custom-Build-Settings) and add the following lines (for Python PIP installs) below. Update `/local/OShost/path/Combined.pem` to the local host path relative to the Dockerfile location.

```
COPY /local/OShost/path/Combined.pem /var/task/Combined.crt
RUN pip config set global.cert /var/task/Combined.crt
```

4. You may need to add additional environment variables to allow using the ceritificate to be used for for `apk install` or `apt-get` system actions.

#### Web Development

The web front-end runs on NodeJS React with a supporting library of amplify-js SDK. The React web page is setup as a single page app using React routes with a hash (#) router.

Infrastructure Note (Hash Router): The hash router was chosen in order so support both cloudfront and application load balancer (ALB) deployment options. As of today, ALBs do not support URL re-writing (without a EC2 reverse proxy), something needed to support normal (non-hash) web routing in React. It was chosen to go this route to ensure that the static web page serving is a AWS serverless process at the expense of SEO degredation, something generally not critical in internal enterprise deployments.

(Important!) Development Note (Hash Router): When using `<Link>`, ensure that the route paths have a `#` in front of them as Link uses the cloudscape library which doesn't tie into the React router. When using `<navigate>`, part of the native React library and thus looking at the route manager, exclude the `#` from the beginning of the route path. Not following this will cause links to return either additional appended hash routes in the path or not use hashes at all.

The front end when loading the page receives a configuration from the AWS backend to include amplify storage bucket, API Gateway/Cloudfront endpoints, authentication endpoints, and features enabled. Some of these are retrieved on load pre-authentication while others are received post-authentication. Features enabled is a comma-deliminated list of infrastructure features that were enabled/disabled on CDK deployment through the `config.json` file and toggle different front-end features to view.

#### Implementing pipelines outside of Lambda

To process an asset through VAMS using an external system or when a job can take longer than the Lambda timeout of 15 minutes, it is recommended that you use the _Wait for a Callback with the Task Token_ feature so that the Pipeline Lambda can initiate your job and then exit instead of waiting for the work to complete before it also finishes. This reduces your Lambda costs and helps you avoid failed jobs that fail simply because they take longer than the timeout to complete.

To use _Wait for a call back with the Task Token_, enable the option to use Task Tokens on the create pipeline screen. When using this option, you must explicitly make a callback to the Step Functions API with the Task Token in the event passed to your Lambda function. The Task Token is provided in the event with the key `TaskToken`. You can see this using the Step Functions execution viewer under the Input tab for an execution with the call back enabled. Pass the `TaskToken` to the system that can notify the Step Functions API that the work is complete with the `SendTaskSuccess` message.

`SendTaskSuccess` is sent with the [aws cli](https://docs.aws.amazon.com/cli/latest/reference/stepfunctions/send-task-success.html#send-task-success) like this:

```
aws stepfunctions send-task-success --task-token 'YOUR_TASK_TOKEN' --task-output '{"status": "success"}'
```

Or, in python [using boto3, like this](https://boto3.amazonaws.com/v1/documentation/api/latest/reference/services/stepfunctions/client/send_task_success.html):

```
response = client.send_task_success(
    taskToken='string',
    output='string'
)
```

For other platforms, see the SDK documentation.

For task failures, see the adjacent api calls for `SendTaskFailure`.

Two additional settings enable your job to end with a timeout error by defining a task timeout. This can reduce your time to detect a problem with your task. By default, the timeout is over a year when working with task tokens. To set a timeout, specify a Task Timeout on the create pipeline screen.

If you would like your job check in to show that it is still running and fail the step if it does not check in within some amount of time less than the task timeout, define the Task Heartbeat Timeout on the create pipeline screen also. If more time than the specified seconds elapses between heartbeats from the task, this state fails with a States.Timeout error name.

#### Uninstalling

1. Run `cdk destroy` from infra folder
2. Some resources may not be deleted by CDK (e.g S3 buckets and DynamoDB table) and you will have to delete them via aws cli or using aws console

Note:

After running CDK destroy there might still some resources be running in AWS that will have to be cleaned up manually as CDK does not delete some resources.

#### Deployment Overview

The CDK deployment deploys the VAMS stack into your account. The components that are created by this app are:

1. Web app hosted on [cloudfront](https://aws.amazon.com/cloudfront/) distribution
1. [API Gateway](https://aws.amazon.com/api-gateway/) to route front end calls to api handlers.
1. [Lambda](https://aws.amazon.com/lambda/) Lambda handlers are created per API path.
1. [DynamoDB](https://aws.amazon.com/dynamodb/) tables to store Workflows, Assets, Pipelines
1. [S3 Buckets](https://aws.amazon.com/s3/) for assets, cdk deployments and log storage
1. [Cognito User Pool](https://docs.aws.amazon.com/cognito/) for authentication
1. [Open Search Collection](https://aws.amazon.com/opensearch-service/features/serverless/) for searching the assets using metadata
   ![ARCHITECTURE](./VAMS_Architecture.jpg)

# API Schema

Please see [Swagger Spec](https://github.com/awslabs/visual-asset-management-system/blob/main/VAMS_API.yaml) for details

# Database Schema

| Table                         | Partition Key | Sort Key   | Attributes                                                                                                                        |
| ----------------------------- | ------------- | ---------- | --------------------------------------------------------------------------------------------------------------------------------- |
| AppFeatureEnabledStorageTable | featureName   | n/a        |                                                                                                                                   |
| AssetStorageTable             | databaseId    | assetId    | assetLocation, assetName, assetType, currentVersion, description, generated_artifacts, isDistributable, previewLocation, versions |
| JobStorageTable               | jobId         | databaseId |                                                                                                                                   |
| PipelineStorageTable          | databaseId    | pipelineId | assetType, dateCreated, description, enabled, outputType, pipelineType                                                            |
| DatabaseStorageTable          | databaseId    | n/a        | assetCount, dateCreated, description                                                                                              |
| WorkflowStorageTable          | databaseId    | workflowId | dateCreated, description, specifiedPipelines, workflow_arn                                                                        |
| WorkflowExecutionStorageTable | pk            | sk         | asset_id, database_id, execution_arn, execution_id, workflow_arn, workflow_id, assets                                             |
| MetadataStorageTable          | databaseId    | assetId    | Varies with user provided attributes                                                                                              |

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

| Field        | Data Type | Description                        |
| ------------ | --------- | ---------------------------------- |
| assetType    | String    | File extension of the asset        |
| dateCreated  | String    | Creation date of this record       |
| description  | String    | User provided description          |
| enabled      | Boolean   | Whether this pipeline is enabled   |
| outputType   | String    | File extension of the output asset |
| pipelineType | String    | Defines the pipeline type — Lambda |

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
| workflow_arn       | String                 | The ARN identifying the step function state machine                 |

## WorkflowExecutionStorageTable

| Field         | Data Type | Description                                                                     |
| ------------- | --------- | ------------------------------------------------------------------------------- |
| asset_id      | String    | Asset identifier for this workflow execution                                    |
| database_id   | String    | Database to which the asset belongs                                             |
| execution_arn | String    | The state machine execution arn                                                 |
| execution_id  | String    | Execution identifier                                                            |
| workflow_arn  | String    | State machine ARN                                                               |
| workflow_id   | String    | Workflow identifier                                                             |
| assets        | List, Map | List of Maps of asset objects (see AssetStorageTable for attribute definitions) |

## MetadataStorageTable

| Field       | Data Type | Description                                  |
| ----------- | --------- | -------------------------------------------- |
| asset_id    | String    | Asset identifier for this workflow execution |
| database_id | String    | Database to which the asset belongs          |

Attributes are driven by user input. No predetermined fields aside from the partition and sort key.
From rel 1.4 onwards, when you add metadata on a file / folder, the s3 key prefix of the file/folder is used as the asset key in the metadata table

# Updating Backend

The dependencies for the backend lambda functions are handled using poetry. If you changed the lambda functions make sure to do a `cdk deploy` to reflect the change.

The lambda handlers are categorized based on the project domain. E.g you will find all assets related functions in `/backend/backend/assets` folder.

# Adding your own pipelines

When you create pipelines in VAMS, you have two options

1. Create a lambda pipeline

## Lambda pipeline

AWS Lambda is the compute platform for VAMS Lambda Pipelines.

When you create a VAMS Lambda pipeline you can either allow VAMS to create a new AWS Lambda function or provide a name of an existing AWS Lambda function to be used as a pipeline.

### Creating your lambda function through VAMS pipeline

When you create a VAMS Lambda pipeline and dont provide name of an existing AWS Lambda function, VAMS will create an AWS Lambda function in your AWS account where VAMS is deployed. This lambda function will have the same name as the pipelineId you provided while creating the pipeline and append `vams-`. This lambda function contains an example pipeline code. This example code can be modified with your own pipeline business logic.

### Using existing lambda function as a pipeline

Sometimes you may want to write your pipelines separately from VAMS stack. Some reasons for this are

1. Separating pipeline code from VAMS deployment code
2. Different personas with no access to VAMS are working on pipeline code
3. Pipelines are managed in a separate CDK/CloudFormation stack altogether.

If you want to use an existing AWS Lambda function as a pipeline in VAMS you can provide the function name of your AWS Lambda function in the create pipeline UI. See the section below for the event payload passed by VAMS workflows when your pipelines are executed.

The VAMS workflow functionality by default will have access to any lambda function within the deployed AWS account with the word `vams` in it. If your existing function does not have this, you will have to grant manual invoke permissions to the workflow stepfunctions role.

## Lambda pipeline interface

When a VAMS workflow invokes a VAMS Lambda pipeline, it invokes the corresponding AWS Lambda function with an event payload like below:

```
"body": {
    "inputPath": "<S3 URI of the asset to be used as input>",
    "outputPath": "<Predetermined output path for assets generated by pipeline's execution>",
}
```

A simple lambda handler is provided below for reference. You may chose to override your own function in place of `write_input_output` function in below code.

```
def lambda_handler(event, context):
    """
    Example of a NoOp pipeline
    Uploads input file to output
    """
    print(event)
    if isinstance(event['body'], str):
        data = json.loads(event['body'])
    else:
        data = event['body']

    write_input_output(data['inputPath'], data['outputPath'])
    return {
        'statusCode': 200,
        'body': 'Success'
    }
```

## Visualizer Pipeline - Visualizer Pipeline Execution Through VAMS Pipeline

Visualizer pipelines to generate preview files for certain types of files like points clouds, are implemented outside of the regular VAMS pipeline at this time. Until these get fully integrated as part of the regular VAMS pipeline design, these pipelines are triggered primarily through a S3 Event Notification on uploading new asset files to VAMS.

If you wish to trigger these pipelines additionally/manually through VAMS pipeline, you can setup a new VAMS pipeline using the table below. You will need to lookup the lambda function name in the AWS console based on the base deployment name listed.

| Visualizer Pipeline | Input/Output File Types Supported | Base Lambda Function Name   |
| :------------------ | :-------------------------------- | :-------------------------- |
| Point Clouds        | LAS, LAZ, E57                     | executeVisualizerPCPipeline |

# Testing API

Please see the corresponding [Postman Collection](https://github.com/awslabs/visual-asset-management-system/blob/main/VAMS_API_Tests.postman_collection.json) provided.

Once the solution is deployed, you will have to put in the below details as Global Variables in the Postman Collection

![Postman Variables](https://github.com/awslabs/visual-asset-management-system/blob/main/Postman_Test_Variables.png)

# Updating and Testing Frontend

Within the web folder You can do `npm run start` to start a local frontend application.
