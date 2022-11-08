import * as cdk from "@aws-cdk/core";
import * as apigateway from "@aws-cdk/aws-apigatewayv2"
import * as logs from '@aws-cdk/aws-logs';
import { ApiGatewayV2LambdaConstruct } from "./constructs/apigatewayv2-lambda-construct";
import { ApiGatewayV2CloudFrontConstruct } from "./constructs/apigatewayv2-cloudfront-construct";
import * as apigw from "@aws-cdk/aws-apigatewayv2";
import * as lambda from "@aws-cdk/aws-lambda";
import * as path from "path";
import { storageResources } from "./storage-builder";
import { buildConfigService } from "./lambdaBuilder/configFunctions";
import {
  buildCreateDatabaseLambdaFunction,
  buildDatabaseService,
} from "./lambdaBuilder/databaseFunctions";
import {
  buildListlWorkflowExecutionsFunction,
  buildWorkflowService,
  buildCreateWorkflowFunction,
  buildRunWorkflowFunction,
} from "./lambdaBuilder/workflowFunctions";
import {
  buildAssetColumnsFunction,
  buildAssetMetadataFunction,
  buildAssetService,
  buildUploadAllAssetsFunction,
  buildUploadAssetFunction,
  buildRevertAssetFunction,
} from "./lambdaBuilder/assetFunctions";
import {
  buildCreatePipelineFunction,
  buildEnablePipelineFunction,
  buildPipelineService,
} from "./lambdaBuilder/pipelineFunctions";
import { buildListLambdasFunction } from "./lambdaBuilder/transformerFunctions";
import { buildErrorApiFunction } from "./lambdaBuilder/handlerFunctions";

interface apiGatewayLambdaConfiguration {
  routePath: string;
  method: apigw.HttpMethod;
  api: apigw.HttpApi;
}

function attachFunctionToApi(
  scope: cdk.Construct,
  lambdaFunction: lambda.Function,
  apiGatewayConfiguration: apiGatewayLambdaConfiguration
) {
  const apig = new ApiGatewayV2LambdaConstruct(
    scope,
    apiGatewayConfiguration.method + apiGatewayConfiguration.routePath,
    {
      ...{},
      lambdaFn: lambdaFunction,
      routePath: apiGatewayConfiguration.routePath,
      methods: [apiGatewayConfiguration.method],
      api: apiGatewayConfiguration.api,
    }
  );
}

export function apiBuilder(
  scope: cdk.Construct,
  api: ApiGatewayV2CloudFrontConstruct,
  storageResources: storageResources
  ) {
  const layer = new lambda.LayerVersion(scope, "stepfunctions", {
    code: lambda.Code.fromAsset(
      path.join(__dirname, "./lambda_layers/stepfunctions.zip")
    ),
    compatibleRuntimes: [lambda.Runtime.PYTHON_3_8],
    license: "Apache-2.0",
    description: "Step functions layer",
  });

  //config resources
  const createConfigFunction = buildConfigService(
    scope, 
    storageResources.s3.assetBucket
  )

  attachFunctionToApi(scope, createConfigFunction, {
    routePath: '/secure-config', 
    method: apigw.HttpMethod.GET, 
    api: api.apiGatewayV2
  })

  //Database Resources
  const createDatabaseFunction = buildCreateDatabaseLambdaFunction(
    scope,
    storageResources.dynamo.databaseStorageTable
  );
  attachFunctionToApi(scope, createDatabaseFunction, {
    routePath: "/databases",
    method: apigw.HttpMethod.PUT,
    api: api.apiGatewayV2,
  });

  const databaseService = buildDatabaseService(
    scope,
    storageResources.dynamo.databaseStorageTable,
    storageResources.dynamo.workflowStorageTable,
    storageResources.dynamo.pipelineStorageTable,
    storageResources.dynamo.assetStorageTable
  );
  attachFunctionToApi(scope, databaseService, {
    routePath: "/databases",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, databaseService, {
    routePath: "/databases/{databaseId}",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, databaseService, {
    routePath: "/databases/{databaseId}",
    method: apigw.HttpMethod.DELETE,
    api: api.apiGatewayV2,
  });

  //Asset Resources
  const assetService = buildAssetService(
    scope,
    storageResources.dynamo.assetStorageTable,
    storageResources.s3.assetBucket
  );
  attachFunctionToApi(scope, assetService, {
    routePath: "/database/{databaseId}/assets",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, assetService, {
    routePath: "/database/{databaseId}/assets/{assetId}",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, assetService, {
    routePath: "/database/{databaseId}/assets/{assetId}",
    method: apigw.HttpMethod.DELETE,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, assetService, {
    routePath: "/assets",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });

  const assetMetadataFunction = buildAssetMetadataFunction(
    scope,
    storageResources.s3.assetBucket,
    storageResources.dynamo.assetStorageTable
  );
  attachFunctionToApi(scope, assetMetadataFunction, {
    routePath: "/database/{databaseId}/assets/{assetId}/metadata",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });

  const assetColumnsFunction = buildAssetColumnsFunction(
    scope,
    storageResources.s3.assetBucket,
    storageResources.dynamo.assetStorageTable
  );
  attachFunctionToApi(scope, assetColumnsFunction, {
    routePath: "/database/{databaseId}/assets/{assetId}/columns",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });

  const uploadAssetFunction = buildUploadAssetFunction(
    scope,
    storageResources.s3.assetBucket,
    storageResources.dynamo.databaseStorageTable,
    storageResources.dynamo.assetStorageTable
  );
  attachFunctionToApi(scope, uploadAssetFunction, {
    routePath: "/assets",
    method: apigw.HttpMethod.PUT,
    api: api.apiGatewayV2,
  });

  const uploadAllAssetFunction = buildUploadAllAssetsFunction(
    scope,
    storageResources.s3.assetBucket,
    storageResources.dynamo.databaseStorageTable,
    storageResources.dynamo.assetStorageTable,
    storageResources.dynamo.workflowExecutionStorageTable,
    uploadAssetFunction
  );
  attachFunctionToApi(scope, uploadAllAssetFunction, {
    routePath: "/assets/all",
    method: apigw.HttpMethod.PUT,
    api: api.apiGatewayV2,
  });

  const assetRevertFunction = buildRevertAssetFunction(
    scope,
    storageResources.s3.assetBucket,
    storageResources.dynamo.databaseStorageTable,
    storageResources.dynamo.assetStorageTable
  );
  attachFunctionToApi(scope, assetRevertFunction, {
    routePath: "/database/{databaseId}/assets/{assetId}/revert",
    method: apigw.HttpMethod.POST,
    api: api.apiGatewayV2,
  });

  //Pipeline Resources
  const enablePipelineFunction = buildEnablePipelineFunction(
    scope,
    storageResources.dynamo.pipelineStorageTable
  );

  const createPipelineFunction = buildCreatePipelineFunction(
    scope,
    storageResources.dynamo.pipelineStorageTable,
    storageResources.s3.artefactsBucket,
    storageResources.s3.sagemakerBucket, 
    storageResources.s3.assetBucket,
    enablePipelineFunction
  );
  attachFunctionToApi(scope, createPipelineFunction, {
    routePath: "/pipelines",
    method: apigw.HttpMethod.PUT,
    api: api.apiGatewayV2,
  });

  const pipelineService = buildPipelineService(
    scope,
    storageResources.dynamo.pipelineStorageTable
  );
  attachFunctionToApi(scope, pipelineService, {
    routePath: "/database/{databaseId}/pipelines",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, pipelineService, {
    routePath: "/database/{databaseId}/pipelines/{pipelineId}",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, pipelineService, {
    routePath: "/database/{databaseId}/pipelines/{pipelineId}",
    method: apigw.HttpMethod.DELETE,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, pipelineService, {
    routePath: "/pipelines",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });


  //Workflows
  const workflowService = buildWorkflowService(
    scope,
    storageResources.dynamo.workflowStorageTable
  );
  attachFunctionToApi(scope, workflowService, {
    routePath: "/database/{databaseId}/workflows",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, workflowService, {
    routePath: "/database/{databaseId}/workflows/{workflowId}",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, workflowService, {
    routePath: "/database/{databaseId}/workflows/{workflowId}",
    method: apigw.HttpMethod.DELETE,
    api: api.apiGatewayV2,
  });
  attachFunctionToApi(scope, workflowService, {
    routePath: "/workflows",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });

  const listWorkflowExecutionsFunction = buildListlWorkflowExecutionsFunction(
    scope,
    storageResources.dynamo.workflowExecutionStorageTable
  );
  attachFunctionToApi(scope, listWorkflowExecutionsFunction, {
    routePath:
      "/database/{databaseId}/assets/{assetId}/workflows/{workflowId}/executions",
    method: apigw.HttpMethod.GET,
    api: api.apiGatewayV2,
  });

  const createWorkflowFunction = buildCreateWorkflowFunction(
    scope,
    storageResources.dynamo.workflowStorageTable,
    storageResources.s3.assetBucket,
    uploadAllAssetFunction,
    layer
  );
  attachFunctionToApi(scope, createWorkflowFunction, {
    routePath: "/workflows",
    method: apigw.HttpMethod.PUT,
    api: api.apiGatewayV2,
  });

  const runWorkflowFunction = buildRunWorkflowFunction(
    scope,
    storageResources.dynamo.workflowStorageTable,
    storageResources.dynamo.pipelineStorageTable,
    storageResources.dynamo.assetStorageTable,
    storageResources.dynamo.workflowExecutionStorageTable,
    storageResources.s3.assetBucket,
    layer
  );
  attachFunctionToApi(scope, runWorkflowFunction, {
    routePath: "/database/{databaseId}/assets/{assetId}/workflows/{workflowId}",
    method: apigw.HttpMethod.POST,
    api: api.apiGatewayV2,
  });

  //Enabling API Gateway Access Logging: Currently the only way to do this is via V1 constructs
  //https://github.com/aws/aws-cdk/issues/11100#issuecomment-904627081
  
  const accessLogs = new logs.LogGroup(scope, 'VAMS-API-AccessLogs')
  const stage = api.apiGatewayV2.defaultStage?.node.defaultChild as apigateway.CfnStage
  stage.accessLogSettings = {
      destinationArn: accessLogs.logGroupArn,
      format: JSON.stringify({
      requestId: '$context.requestId',
      userAgent: '$context.identity.userAgent',
      sourceIp: '$context.identity.sourceIp',
      requestTime: '$context.requestTime',
      requestTimeEpoch: '$context.requestTimeEpoch',
      httpMethod: '$context.httpMethod',
      path: '$context.path',
      status: '$context.status',
      protocol: '$context.protocol',
      responseLength: '$context.responseLength',
      domainName: '$context.domainName'
      })
  }
}
