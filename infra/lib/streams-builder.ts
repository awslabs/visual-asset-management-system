/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ApiGatewayV2CloudFrontConstruct } from "./constructs/apigatewayv2-cloudfront-construct";
import { storageResources } from "./storage-builder";
import { buildMetadataIndexingFunction } from "./lambdaBuilder/metadataFunctions";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { LambdaSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import { NagSuppressions } from "cdk-nag";
import { OpensearchServerlessConstruct } from "./constructs/opensearch-serverless";
import { Stack } from "aws-cdk-lib";
import { CognitoWebNativeConstruct } from "./constructs/cognito-web-native-construct";
import { buildSearchFunction } from "./lambdaBuilder/searchFunctions";
import { attachFunctionToApi } from "./api-builder";
import * as apigwv2 from "@aws-cdk/aws-apigatewayv2-alpha";
import * as cdk from "aws-cdk-lib";

export function streamsBuilder(
    scope: Stack,
    cognitoResources: CognitoWebNativeConstruct,
    api: ApiGatewayV2CloudFrontConstruct,
    storage: storageResources
) {
    const aoss = new OpensearchServerlessConstruct(scope, "AOSS", {
        principalArn: [],
    });

    const indexNameParam = "/" + [cdk.Stack.of(scope).stackName, "indexName"].join("/");

    const indexingFunction = buildMetadataIndexingFunction(
        scope,
        storage,
        aoss.endpointSSMParameterName(),
        indexNameParam,
        "m"
    );

    const assetIndexingFunction = buildMetadataIndexingFunction(
        scope,
        storage,
        aoss.endpointSSMParameterName(),
        indexNameParam,
        "a"
    );

    //Add subscriptions to kick-off lambda function for indexing
    storage.sns.assetBucketObjectCreatedTopic.addSubscription(
        new LambdaSubscription(indexingFunction)
    );

    storage.sns.assetBucketObjectRemovedTopic.addSubscription(
        new LambdaSubscription(indexingFunction)
    );

    indexingFunction.addEventSource(
        new eventsources.DynamoEventSource(storage.dynamo.metadataStorageTable, {
            startingPosition: lambda.StartingPosition.TRIM_HORIZON,
        })
    );
    assetIndexingFunction.addEventSource(
        new eventsources.DynamoEventSource(storage.dynamo.assetStorageTable, {
            startingPosition: lambda.StartingPosition.TRIM_HORIZON,
        })
    );

    aoss.grantCollectionAccess(indexingFunction);
    aoss.grantCollectionAccess(assetIndexingFunction);

    const searchFun = buildSearchFunction(
        scope,
        aoss.endpointSSMParameterName(),
        indexNameParam,
        aoss,
        storage
    );
    attachFunctionToApi(scope, searchFun, {
        routePath: "/search",
        method: apigwv2.HttpMethod.POST,
        api: api.apiGatewayV2,
    });
    attachFunctionToApi(scope, searchFun, {
        routePath: "/search",
        method: apigwv2.HttpMethod.GET,
        api: api.apiGatewayV2,
    });

    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: "AwsSolutions-IAM4",
                reason: "Intend to use AWSLambdaBasicExecutionRole as is at this stage of this project.",
                appliesTo: [
                    {
                        regex: "/.*AWSLambdaBasicExecutionRole$/g",
                    },
                ],
            },
        ],
        true
    );

    NagSuppressions.addResourceSuppressions(scope, [
        {
            id: "AwsSolutions-L1",
            reason: "Configured as intended.",
        },
    ]);

    NagSuppressions.addResourceSuppressionsByPath(
        scope,
        `/${scope.stackName}/AOSS/OpensearchServerlessDeploySchemaProvider/framework-onEvent/Resource`,
        [
            {
                id: "AwsSolutions-L1",
                reason: "Configured as intended.",
            },
        ]
    );

    NagSuppressions.addResourceSuppressions(
        scope,
        [
            {
                id: "AwsSolutions-IAM5",
                reason: "Configured as intended.",
                appliesTo: [
                    {
                        regex: "/.*$/g",
                    },
                ],
            },
        ],
        true
    );
}