/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { ApiGatewayV2CloudFrontConstruct } from "./constructs/apigatewayv2-cloudfront-construct";
import { storageResources } from "./storage-builder";
import { buildMetadataIndexingFunction } from "./lambdaBuilder/metadataFunctions";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import { OpensearchServerlessConstruct } from "./constructs/opensearch-serverless";
import { Stack } from "aws-cdk-lib";
import { CognitoWebNativeConstruct } from "./constructs/cognito-web-native-construct";
import { SSMPARAM_NO_INVALIDATE } from "aws-cdk-lib/cx-api";
import * as ssm from "aws-cdk-lib/aws-ssm";
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
        // TODO Change to an admin only role
        principalArn: [
            cognitoResources.authenticatedRole.roleArn,
            "arn:aws:iam::098204178297:role/Admin",
        ],
    });

    // the ssm parameter store value for the endpoint
    const aossParam = ssm.StringParameter.fromStringParameterName(
        scope,
        "aossEndpoint",
        aoss.endpointSSMParameterName()
    );

    aossParam.node.addDependency(aoss);

    const indexingFunction = buildMetadataIndexingFunction(scope, storage, aossParam.stringValue);

    indexingFunction.addEventSource(
        new eventsources.DynamoEventSource(storage.dynamo.metadataStorageTable, {
            startingPosition: lambda.StartingPosition.TRIM_HORIZON,
        })
    );

    aoss.grantCollectionAccess(indexingFunction);

    const searchFun = buildSearchFunction(scope, aossParam.stringValue, aoss);
    attachFunctionToApi(scope, searchFun, {
        routePath: "/search",
        method: apigwv2.HttpMethod.POST,
        api: api.apiGatewayV2,
    });
    attachFunctionToApi(scope, searchFun, {
        routePath: "/search",
        method: apigwv2.HttpMethod.GET,
        api: api.apiGatewayV2,
    })

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
