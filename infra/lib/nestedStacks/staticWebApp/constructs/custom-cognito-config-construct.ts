/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";

/* eslint-disable @typescript-eslint/no-empty-interface */
export interface CustomCognitoConfigConstructProps {
    name: string;
    clientId: string;
    userPoolId: string;
    callbackUrls: string[];
    logoutUrls: string[];
    identityProviders: string[];
}

const defaultProps: Partial<CustomCognitoConfigConstructProps> = {};

/**
 * Custom configuration to Cognito.
 */
export class CustomCognitoConfigConstruct extends Construct {
    public resource: cdk.custom_resources.AwsCustomResource;

    constructor(parent: Construct, name: string, props: CustomCognitoConfigConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        /**
         * Use the AWS SDK to configure User Pool "App client settings", e.g.
         * Sign in and sign out URLs
         *
         * @type {AwsCustomResource}
         *
         * @see https://docs.aws.amazon.com/cdk/api/latest/docs/@aws-cdk_custom-resources.AwsCustomResource.html
         * @see https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_UpdateUserPoolClient.html
         * @see https://docs.aws.amazon.com/AWSJavaScriptSDK/latest/AWS/CognitoIdentityServiceProvider.html#updateUserPoolClient-property
         */
        const resource = new cdk.custom_resources.AwsCustomResource(
            this,
            "UpdateUserPool" + props.name,
            {
                onUpdate: {
                    service: "CognitoIdentityServiceProvider",
                    action: "updateUserPoolClient",
                    parameters: {
                        ClientId: props.clientId,
                        UserPoolId: props.userPoolId,
                        SupportedIdentityProviders: props.identityProviders,
                        AllowedOAuthFlows: ["code"],
                        AllowedOAuthScopes: ["email", "openid", "profile"],
                        AllowedOAuthFlowsUserPoolClient: true,
                        CallbackURLs: props.callbackUrls,
                        LogoutURLs: props.logoutUrls,
                    },
                    physicalResourceId: cdk.custom_resources.PhysicalResourceId.of(
                        `${props.userPoolId}-${props.clientId}`
                    ),
                },
                policy: cdk.custom_resources.AwsCustomResourcePolicy.fromSdkCalls({
                    resources: cdk.custom_resources.AwsCustomResourcePolicy.ANY_RESOURCE,
                }),
                installLatestAwsSdk: false,
            }
        );

        this.resource = resource;
    }
}
