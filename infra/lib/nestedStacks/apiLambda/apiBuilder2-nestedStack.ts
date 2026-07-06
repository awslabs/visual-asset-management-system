/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { NestedStack } from "aws-cdk-lib";
import * as apigateway from "aws-cdk-lib/aws-apigatewayv2";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import { storageResources } from "../storage/storageBuilder-nestedStack";
import { buildTagService, buildCreateTagFunction } from "../../lambdaBuilder/tagFunctions";
import {
    buildTagTypeService,
    buildCreateTagTypeFunction,
} from "../../lambdaBuilder/tagTypeFunctions";
import {
    buildAuthConstraintsFunction,
    buildAuthConstraintsTemplateFunction,
} from "../../lambdaBuilder/authFunctions";
import { buildAssetHistoryFunction } from "../../lambdaBuilder/assetFunctions";
import { RouteRegistry, attachFunctionToApi } from "./apiRouteRegistry";
import * as Config from "../../../config/config";

/**
 * Properties for the secondary API builder nested stack.
 */
export interface ApiBuilder2NestedStackProps {
    config: Config.Config;
    registry: RouteRegistry;
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
}

/**
 * ApiBuilder2NestedStack
 *
 * Secondary backend API nested stack. The primary ApiBuilderNestedStack is approaching the
 * CloudFormation per-stack resource limit (500 resources), so some API domains are
 * relocated here to free up headroom. New API endpoints should be added to this stack going
 * forward until it too approaches the limit.
 *
 */
export class ApiBuilder2NestedStack extends NestedStack {
    constructor(parent: Construct, name: string, props: ApiBuilder2NestedStackProps) {
        super(parent, name);

        const { config, registry, storageResources, lambdaCommonBaseLayer, vpc, subnets } = props;

        //Tags Resources
        const tagService = buildTagService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, tagService, {
            routePath: "/tags",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, tagService, {
            routePath: "/tags/{tagId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        const createTagFunction = buildCreateTagFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, createTagFunction, {
            routePath: "/tags",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, createTagFunction, {
            routePath: "/tags",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        //Tag Types Resources
        const tagTypeService = buildTagTypeService(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, tagTypeService, {
            routePath: "/tag-types",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, tagTypeService, {
            routePath: "/tag-types/{tagTypeId}",
            method: apigateway.HttpMethod.DELETE,
            registry: registry,
        });

        const createTagTypeFunction = buildCreateTagTypeFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, createTagTypeFunction, {
            routePath: "/tag-types",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });
        attachFunctionToApi(this, createTagTypeFunction, {
            routePath: "/tag-types",
            method: apigateway.HttpMethod.PUT,
            registry: registry,
        });

        // Auth constraints service and its routes (relocated here from ApiBuilder to keep
        // the primary stack under the CFN per-stack resource limit).
        const authConstraintsService = buildAuthConstraintsFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        // permissionObjects must be registered before the {constraintId} route so the
        // literal path is not captured by the {constraintId} template.
        attachFunctionToApi(this, authConstraintsService, {
            routePath: "/auth/constraints/permissionObjects",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        attachFunctionToApi(this, authConstraintsService, {
            routePath: "/auth/constraints",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });
        const constraintMethods = [
            apigateway.HttpMethod.GET,
            apigateway.HttpMethod.POST,
            apigateway.HttpMethod.PUT,
            apigateway.HttpMethod.DELETE,
        ];
        for (let i = 0; i < constraintMethods.length; i++) {
            attachFunctionToApi(this, authConstraintsService, {
                routePath: "/auth/constraints/{constraintId}",
                method: constraintMethods[i],
                registry: registry,
            });
        }

        const authConstraintsTemplateService = buildAuthConstraintsTemplateFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, authConstraintsTemplateService, {
            routePath: "/auth/constraintsTemplateImport",
            method: apigateway.HttpMethod.POST,
            registry: registry,
        });

        //Asset History Resources
        const assetHistoryFunction = buildAssetHistoryFunction(
            this,
            lambdaCommonBaseLayer,
            storageResources,
            config,
            vpc,
            subnets
        );
        attachFunctionToApi(this, assetHistoryFunction, {
            routePath: "/database/{databaseId}/assets/{assetId}/assetHistory",
            method: apigateway.HttpMethod.GET,
            registry: registry,
        });

        //Nag Supressions
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "Not providing IAM wildcard permissions to constraint tables.",
                },
            ],
            true
        );
    }
}
