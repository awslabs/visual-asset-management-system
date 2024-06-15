/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { storageResources } from "../storage/storageBuilder-nestedStack";
import { buildIndexingFunction } from "../../lambdaBuilder/searchFunctions";
import * as eventsources from "aws-cdk-lib/aws-lambda-event-sources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { LambdaSubscription } from "aws-cdk-lib/aws-sns-subscriptions";
import { NagSuppressions } from "cdk-nag";
import { OpensearchServerlessConstruct } from "./constructs/opensearch-serverless";
import { OpensearchProvisionedConstruct } from "./constructs/opensearch-provisioned";
import { buildSearchFunction } from "../../lambdaBuilder/searchFunctions";
import { attachFunctionToApi } from "../apiLambda/apiBuilder-nestedStack";
import { Stack, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as apigwv2 from "@aws-cdk/aws-apigatewayv2-alpha";
import * as cdk from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as Config from "../../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { PropagatedTagSource } from "aws-cdk-lib/aws-ecs";

export class SearchBuilderNestedStack extends NestedStack {
    constructor(
        parent: Construct,
        name: string,
        config: Config.Config,
        api: apigwv2.HttpApi,
        storageResources: storageResources,
        lambdaCommonBaseLayer: LayerVersion,
        vpc: ec2.IVpc,
        subnets: ec2.ISubnet[]
    ) {
        super(parent, name);

        searchBuilder(this, config, api, storageResources, lambdaCommonBaseLayer, vpc, subnets);
    }
}

export function searchBuilder(
    scope: Construct,
    config: Config.Config,
    api: apigwv2.HttpApi,
    storageResources: storageResources,
    lambdaCommonBaseLayer: LayerVersion,
    vpc: ec2.IVpc,
    subnets: ec2.ISubnet[]
) {
    const searchFun = buildSearchFunction(
        scope,
        lambdaCommonBaseLayer,
        storageResources,
        config,
        vpc,
        subnets
    );

    attachFunctionToApi(scope, searchFun, {
        routePath: "/search",
        method: apigwv2.HttpMethod.POST,
        api: api,
    });
    attachFunctionToApi(scope, searchFun, {
        routePath: "/search",
        method: apigwv2.HttpMethod.GET,
        api: api,
    });

    if (config.app.openSearch.useServerless.enabled) {
        //Serverless Deployment
        const aoss = new OpensearchServerlessConstruct(scope, "AOSS", {
            config: config,
            principalArn: [],
            storageResources: storageResources,
            vpc: vpc,
            subnets: subnets,
        });

        const indexingFunction = buildIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            "m",
            config,
            vpc,
            subnets
        );

        const assetIndexingFunction = buildIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            "a",
            config,
            vpc,
            subnets
        );

        //Add subscriptions to kick-off lambda function for indexing
        storageResources.sns.assetBucketObjectCreatedTopic.addSubscription(
            new LambdaSubscription(indexingFunction)
        );

        storageResources.sns.assetBucketObjectRemovedTopic.addSubscription(
            new LambdaSubscription(indexingFunction)
        );

        indexingFunction.addEventSource(
            new eventsources.DynamoEventSource(storageResources.dynamo.metadataStorageTable, {
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
            })
        );
        assetIndexingFunction.addEventSource(
            new eventsources.DynamoEventSource(storageResources.dynamo.assetStorageTable, {
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
            })
        );

        aoss.grantCollectionAccess(indexingFunction);
        aoss.grantCollectionAccess(assetIndexingFunction);
        aoss.grantVPCeAccess(indexingFunction);
        aoss.grantVPCeAccess(assetIndexingFunction);

        //grant search function access to collection and VPCe
        aoss.grantCollectionAccess(searchFun);
        aoss.grantVPCeAccess(searchFun);
    } else if (config.app.openSearch.useProvisioned.enabled) {
        //Provisioned Deployment
        const aos = new OpensearchProvisionedConstruct(scope, "AOS", {
            storageResources: storageResources,
            config: config,
            vpc: vpc,
            subnets: subnets,
            dataNodeInstanceType:
                config.app.openSearch.useProvisioned.dataNodeInstanceType &&
                config.app.openSearch.useProvisioned.dataNodeInstanceType != ""
                    ? config.app.openSearch.useProvisioned.dataNodeInstanceType
                    : undefined,
            masterNodeInstanceType:
                config.app.openSearch.useProvisioned.masterNodeInstanceType &&
                config.app.openSearch.useProvisioned.masterNodeInstanceType != ""
                    ? config.app.openSearch.useProvisioned.masterNodeInstanceType
                    : undefined,
            ebsVolumeSize: config.app.openSearch.useProvisioned.ebsInstanceNodeSizeGb
                ? config.app.openSearch.useProvisioned.ebsInstanceNodeSizeGb
                : undefined,
        });

        const indexingFunction = buildIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            "m",
            config,
            vpc,
            subnets
        );

        const assetIndexingFunction = buildIndexingFunction(
            scope,
            lambdaCommonBaseLayer,
            storageResources,
            "a",
            config,
            vpc,
            subnets
        );

        aos.grantOSDomainAccess(assetIndexingFunction);
        aos.grantOSDomainAccess(indexingFunction);

        //Add subscriptions to kick-off lambda function for indexing
        storageResources.sns.assetBucketObjectCreatedTopic.addSubscription(
            new LambdaSubscription(indexingFunction)
        );

        storageResources.sns.assetBucketObjectRemovedTopic.addSubscription(
            new LambdaSubscription(indexingFunction)
        );

        indexingFunction.addEventSource(
            new eventsources.DynamoEventSource(storageResources.dynamo.metadataStorageTable, {
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
            })
        );
        assetIndexingFunction.addEventSource(
            new eventsources.DynamoEventSource(storageResources.dynamo.assetStorageTable, {
                startingPosition: lambda.StartingPosition.TRIM_HORIZON,
            })
        );

        //grant search function access to AOS
        aos.grantOSDomainAccess(searchFun);
    }

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
