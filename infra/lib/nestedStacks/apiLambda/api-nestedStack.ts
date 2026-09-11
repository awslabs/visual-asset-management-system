/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as cdk from "aws-cdk-lib";
import { NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as Config from "../../../config/config";
import { storageResources } from "../storage/storageBuilder-nestedStack";
import { authResources } from "../auth/authBuilder-nestedStack";
import { RouteRegistry } from "./apiRouteRegistry";
import {
    IApiImplementation,
    RestApiGatewayConstruct,
} from "./constructs/rest-api-gateway-construct";

export interface ApiNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    authResources: authResources;
    storageResources: storageResources;
    lambdaAuthorizerLayer: LayerVersion;
    registry: RouteRegistry;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    vamsCreatedApiGatewayVpcEndpointId?: string;
    wafArn?: string;
}

/**
 * Backend API nested stack.
 *
 * This stack is implementation-agnostic: it selects an {@link IApiImplementation}
 * construct based on configuration and surfaces its outputs. Currently VAMS uses API
 * Gateway REST ({@link RestApiGatewayConstruct})
 */
export class ApiNestedStack extends NestedStack {
    public apiEndpoint: string;
    public stageName: string;
    public invokeUrlWithStage: string;
    /** The selected API implementation (today always the REST API Gateway construct). */
    public readonly apiImplementation: IApiImplementation;

    constructor(parent: Construct, name: string, props: ApiNestedStackProps) {
        super(parent, name);

        // Select the API implementation from config.app.api.apiType.
        let impl: IApiImplementation;
        if (props.config.app.api.apiType === Config.API_TYPE_APIGATEWAY_REST) {
            impl = new RestApiGatewayConstruct(this, "RestApiGateway", {
                config: props.config,
                authResources: props.authResources,
                storageResources: props.storageResources,
                lambdaAuthorizerLayer: props.lambdaAuthorizerLayer,
                registry: props.registry,
                vpc: props.vpc,
                subnets: props.subnets,
                vamsCreatedApiGatewayVpcEndpointId: props.vamsCreatedApiGatewayVpcEndpointId,
                wafArn: props.wafArn,
            });
        } else {
            throw new Error(
                `Unsupported app.api.apiType '${props.config.app.api.apiType}'. ` +
                    `Supported types: [${Config.SUPPORTED_API_TYPES.join(", ")}].`
            );
        }

        this.apiImplementation = impl;
        this.apiEndpoint = impl.apiEndpoint;
        this.invokeUrlWithStage = impl.invokeUrlWithStage;
        this.stageName = impl.stageName;
    }
}
