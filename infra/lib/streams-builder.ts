/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { ApiGatewayV2CloudFrontConstruct } from "./constructs/apigatewayv2-cloudfront-construct";
import { storageResources } from "./storage-builder";
import { buildMetadataIndexingFunction } from "./lambdaBuilder/metadataFunctions";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as s3n from "aws-cdk-lib/aws-s3-notifications";
import { NagSuppressions } from "cdk-nag";
import { OpensearchServerlessConstruct } from "./constructs/opensearch-serverless";
import { Stack } from "aws-cdk-lib";
import { CognitoWebNativeConstruct } from "./constructs/cognito-web-native-construct";
import { buildSearchFunction } from "./lambdaBuilder/searchFunctions";
import { attachFunctionToApi } from "./api-builder";
import * as apigwv2 from "@aws-cdk/aws-apigatewayv2-alpha";

export function streamsBuilder(
    scope: Stack,
    cognitoResources: CognitoWebNativeConstruct,
    api: ApiGatewayV2CloudFrontConstruct,
    storage: storageResources
) {
    const aoss = new OpensearchServerlessConstruct(scope, "AOSS", {
        principalArn: [],
    });

    const indexingFunction = buildMetadataIndexingFunction(
        scope,
        storage,
        aoss.endpointSSMParameterName(),
        "m"
    );

    const assetIndexingFunction = buildMetadataIndexingFunction(
        scope,
        storage,
        aoss.endpointSSMParameterName(),
        "a"
    );

    storage.s3.assetBucket.addEventNotification(
        s3.EventType.OBJECT_CREATED,
        new s3n.LambdaDestination(indexingFunction)
    );

    storage.s3.assetBucket.addEventNotification(
        s3.EventType.OBJECT_REMOVED,
        new s3n.LambdaDestination(indexingFunction)
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

    const searchFun = buildSearchFunction(scope, aoss.endpointSSMParameterName(), aoss, storage);
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
