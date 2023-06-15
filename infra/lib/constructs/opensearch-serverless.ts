/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as aoss from "aws-cdk-lib/aws-opensearchserverless";
import * as cr from "aws-cdk-lib/custom-resources";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as njslambda from "aws-cdk-lib/aws-lambda-nodejs";
import * as path from "path";
import { CustomResource } from "aws-cdk-lib";

interface OpensearchServerlessConstructProps extends cdk.StackProps {
    principalArn: string[];
}

export class OpensearchServerlessConstruct extends Construct {
    constructor(parent: Construct, name: string, props: OpensearchServerlessConstructProps) {
        super(parent, name);

        const schemaDeploy = new njslambda.NodejsFunction(
            this,
            "OpensearchServerlessDeploySchema",
            {
                entry: path.join(__dirname, "./opensearchserverless/deployschema.ts"),
                handler: "handler",
                bundling: {
                    externalModules: ["aws-sdk"],
                },
                runtime: lambda.Runtime.NODEJS_18_X,
            }
        );

        schemaDeploy.addToRolePolicy(
            new cdk.aws_iam.PolicyStatement({
                actions: ["aoss:*"],
                resources: ["*"],
                effect: cdk.aws_iam.Effect.ALLOW,
            })
        );

        const principalsForAOSS = [...props.principalArn, schemaDeploy.role?.roleArn];

        const policy = [
            {
                Description: "Access for test-user",
                Rules: [
                    {
                        ResourceType: "index",
                        Resource: ["index/*/*"],
                        Permission: ["aoss:*"],
                    },
                    {
                        ResourceType: "collection",
                        Resource: ["collection/my-collection"],
                        Permission: ["aoss:*"],
                    },
                ],
                Principal: principalsForAOSS,
            },
        ];

        const accessPolicy = new aoss.CfnAccessPolicy(this, "OpensearchAccessPolicy", {
            name: "accplicyname",
            type: "data",
            policy: JSON.stringify(policy),
        });

        const collection = new aoss.CfnCollection(this, "OpensearchCollection", {
            name: "my-collection",
            description: "my most excellent collection",
            type: "SEARCH",
        });

        const encryptionPolicy = {
            Rules: [{ ResourceType: "collection", Resource: [`collection/${collection.name}`] }],
            AWSOwnedKey: true,
        };
        const encryptionPolicyCfn = new aoss.CfnSecurityPolicy(this, "OpensearchEncryptionPolicy", {
            name: "my-encryption-policy",
            policy: JSON.stringify(encryptionPolicy),
            type: "encryption",
        });

        const networkPolicy = [
            {
                Rules: [
                    { ResourceType: "collection", Resource: [`collection/${collection.name}`] },
                    { ResourceType: "dashboard", Resource: [`collection/${collection.name}`] },
                ],
                AllowFromPublic: true,
            },
        ];

        const networkPolicyCfn = new aoss.CfnSecurityPolicy(this, "OpensearchNetworkPolicy", {
            name: "my-network-policy",
            policy: JSON.stringify(networkPolicy),
            type: "network",
        });

        collection.addDependency(encryptionPolicyCfn);
        collection.addDependency(networkPolicyCfn);
        collection.addDependency(accessPolicy);

        const schemaDeployProvider = new cr.Provider(
            this,
            "OpensearchServerlessDeploySchemaProvider",
            {
                onEventHandler: schemaDeploy,
            }
        );

        schemaDeployProvider.node.addDependency(schemaDeploy);
        schemaDeployProvider.node.addDependency(collection);

        new CustomResource(this, "DeployIndex", {
            serviceToken: schemaDeployProvider.serviceToken,
            properties: {
                collectionName: collection.name,
                indexName: "assets1236",
            },
        });
    }
}
