/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { storageResources } from "../storage/storageBuilder-nestedStack";
import * as cognito from "aws-cdk-lib/aws-cognito";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { NagSuppressions } from "cdk-nag";
import {
    CognitoWebNativeConstructStack,
    CognitoWebNativeConstructStackProps,
} from "./constructs/cognito-web-native-construct";
import { DynamoDbAuthDefaultsAdminConstructStack } from "./constructs/dynamodb-authdefaults-admin-construct";
import { DynamoDbAuthDefaultsROConstructStack } from "./constructs/dynamodb-authdefaults-ro-construct";
import { kmsKeyPolicyStatementGenerator } from "../../helper/security";
import { Stack, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as Config from "../../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import { samlSettings } from "../../../config/saml-config";
import { Service } from "../../../lib/helper/service-helper";
import {
    AwsCustomResource,
    AwsSdkCall,
    AwsCustomResourcePolicy,
    PhysicalResourceId,
    PhysicalResourceIdReference,
} from "aws-cdk-lib/custom-resources";

export interface authResources {
    roles: {
        unAuthenticatedRole: iam.Role;
    };
    cognito: {
        userPool: cognito.UserPool;
        webClientUserPool: cognito.UserPoolClient;
        userPoolId: string;
        identityPoolId: string;
        webClientId: string;
    };
}

/* eslint-disable @typescript-eslint/no-empty-interface */
export interface AuthBuilderNestedStackProps {
    config: Config.Config;
    storageResources: storageResources;
    lambdaCommonBaseLayer: LayerVersion;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
}

const defaultProps: Partial<AuthBuilderNestedStackProps> = {};

export class AuthBuilderNestedStack extends NestedStack {
    public authResources: authResources;

    constructor(parent: Construct, name: string, props: AuthBuilderNestedStackProps) {
        super(parent, name);

        if (props.config.app.authProvider.useCognito.enabled) {
            //Use Cognito
            const cognitoProps: CognitoWebNativeConstructStackProps = {
                ...props,
                lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                storageResources: props.storageResources,
                config: props.config,
            };

            //TODO: Migrate rest of settings to main config file
            if (props.config.app.authProvider.useCognito.useSaml) {
                cognitoProps.samlSettings = samlSettings;
            }

            const cognitoWebNativeConstruct = new CognitoWebNativeConstructStack(
                this,
                "Cognito",
                cognitoProps
            );

            this.authResources = {
                roles: {
                    unAuthenticatedRole: cognitoWebNativeConstruct.unauthenticatedRole,
                },
                cognito: {
                    userPool: cognitoWebNativeConstruct.userPool,
                    webClientUserPool: cognitoWebNativeConstruct.webClientUserPool,
                    userPoolId: cognitoWebNativeConstruct.userPoolId,
                    identityPoolId: cognitoWebNativeConstruct.identityPoolId,
                    webClientId: cognitoWebNativeConstruct.webClientId,
                },
            };
        } else {
            //TODO - Other Authentications Setup
        }

        //Setup Custom Resource Role Policy
        const customResourcePolicy = new iam.PolicyDocument({
            statements: [
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["dynamodb:PutItem"],
                    resources: [props.storageResources.dynamo.rolesStorageTable.tableArn],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["dynamodb:PutItem"],
                    resources: [props.storageResources.dynamo.userRolesStorageTable.tableArn],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["dynamodb:PutItem"],
                    resources: [props.storageResources.dynamo.userStorageTable.tableArn],
                }),
                new iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["dynamodb:PutItem"],
                    resources: [props.storageResources.dynamo.constraintsStorageTable.tableArn],
                }),
            ],
        });

        const authDefaultCustomResourceRole = new iam.Role(this, "AuthDefaultCustomResourceRole", {
            assumedBy: Service("LAMBDA").Principal,
            inlinePolicies: {
                TablePolicy: customResourcePolicy,
            },
            managedPolicies: [
                iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "service-role/AWSLambdaVPCAccessExecutionRole"
                ),
            ],
        });

        // Add KMS permissions when KMS encryption is enabled, regardless of timing issues
        if (props.config.app.useKmsCmkEncryption.enabled) {
            if (props.storageResources.encryption.kmsKey) {
                // KMS key is available, add specific permissions
                authDefaultCustomResourceRole.attachInlinePolicy(
                    new iam.Policy(this, "CRAuthKmsPolicy", {
                        statements: [
                            kmsKeyPolicyStatementGenerator(
                                props.storageResources.encryption.kmsKey
                            ),
                        ],
                    })
                );
            } else {
                // KMS key not yet available, add general KMS permissions for custom resources
                authDefaultCustomResourceRole.attachInlinePolicy(
                    new iam.Policy(this, "CRAuthKmsPolicy", {
                        statements: [
                            new iam.PolicyStatement({
                                actions: [
                                    "kms:Decrypt",
                                    "kms:DescribeKey",
                                    "kms:Encrypt",
                                    "kms:GenerateDataKey*",
                                    "kms:ReEncrypt*",
                                    "kms:ListKeys",
                                    "kms:CreateGrant",
                                    "kms:ListAliases",
                                ],
                                effect: iam.Effect.ALLOW,
                                resources: ["*"], // Will be constrained by KMS key policy
                                conditions: {
                                    StringEquals: {
                                        "kms:ViaService": [
                                            Service("DYNAMODB").Endpoint,
                                            Service("S3").Endpoint,
                                        ],
                                    },
                                },
                            }),
                        ],
                    })
                );
            }
        }

        //Deploy DynamoDB system user login profile
        const awsSdkCallUserSystemUser: AwsSdkCall = {
            service: "DynamoDB",
            action: "putItem",
            parameters: {
                TableName: props.storageResources.dynamo.userStorageTable.tableName,
                Item: {
                    userId: {
                        S: "SYSTEM_USER",
                    },
                    email: {
                        S: "VAMS_SYSTEM@DO.NOT.REPLY.com",
                    },
                    createdOn: {
                        S: new Date().toISOString(),
                    },
                },
                //ConditionExpression: "attribute_not_exists(userId)",
            },
            physicalResourceId: PhysicalResourceId.of(
                props.storageResources.dynamo.userStorageTable.tableName + `_system_initialization`
            ),
        };

        new AwsCustomResource(this, `userStorageTable_system_CustomResource`, {
            onCreate: awsSdkCallUserSystemUser,
            onUpdate: awsSdkCallUserSystemUser,
            role: authDefaultCustomResourceRole,
        });

        //Deploy DynamoDB admin user login profile
        const awsSdkCallUserAdmin: AwsSdkCall = {
            service: "DynamoDB",
            action: "putItem",
            parameters: {
                TableName: props.storageResources.dynamo.userStorageTable.tableName,
                Item: {
                    userId: {
                        S: props.config.app.adminUserId,
                    },
                    email: {
                        S: props.config.app.adminEmailAddress,
                    },
                    createdOn: {
                        S: new Date().toISOString(),
                    },
                },
                //ConditionExpression: "attribute_not_exists(userId)",
            },
            physicalResourceId: PhysicalResourceId.of(
                props.storageResources.dynamo.userStorageTable.tableName + `_admin_initialization`
            ),
        };

        new AwsCustomResource(this, `userStorageTable_admin_CustomResource`, {
            onCreate: awsSdkCallUserAdmin,
            onUpdate: awsSdkCallUserAdmin,
            role: authDefaultCustomResourceRole,
        });

        //Deploy DynamoDB for roles/constraints defaults
        const dynamoDbAuthDefaultsAdmin = new DynamoDbAuthDefaultsAdminConstructStack(
            this,
            "DynamoDBAuthDefaultsAdmin",
            {
                config: props.config,
                customResourceRole: authDefaultCustomResourceRole,
                lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                storageResources: props.storageResources,
            }
        );

        // const dynamoDbAuthDefaultsBasic = new DynamoDbAuthDefaultsBasicConstructStack(
        //     this,
        //     "DynamoDBAuthDefaultsBasic",
        //     {
        //         config: props.config,
        //         lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
        //         storageResources: props.storageResources,
        //     }
        // );

        const dynamoDbAuthDefaultsRO = new DynamoDbAuthDefaultsROConstructStack(
            this,
            "DynamoDBAuthDefaultsRO",
            {
                config: props.config,
                customResourceRole: authDefaultCustomResourceRole,
                lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
                storageResources: props.storageResources,
            }
        );

        // const dynamoDbAuthDefaultsPipeline = new DynamoDbAuthDefaultsPipelineConstructStack(
        //     this,
        //     "DynamoDBAuthDefaultsPipeline",
        //     {
        //         config: props.config,
        //         lambdaCommonBaseLayer: props.lambdaCommonBaseLayer,
        //         storageResources: props.storageResources,
        //     }
        // );

        //Nag Supressions
    }
}
