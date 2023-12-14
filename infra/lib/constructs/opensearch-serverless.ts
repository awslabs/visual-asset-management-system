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
import { CustomResource, Names } from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";

interface OpensearchServerlessConstructProps extends cdk.StackProps {
    principalArn: string[];
}

export class OpensearchServerlessConstruct extends Construct {
    collectionUid: string;

    constructor(parent: Construct, name: string, props: OpensearchServerlessConstructProps) {
        super(parent, name);

        this.collectionUid = Names.uniqueResourceName(this, { maxLength: 16 }).toLowerCase();

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
        schemaDeploy.addToRolePolicy(
            new cdk.aws_iam.PolicyStatement({
                actions: ["ssm:*"],
                resources: ["*"],
                // resources: [`arn:aws:ssm:::parameter/${cdk.Stack.of(this).stackName}/*`],
                effect: cdk.aws_iam.Effect.ALLOW,
            })
        );

        const principalsForAOSS = [...props.principalArn, schemaDeploy.role?.roleArn];

        const accessPolicy = this._grantCollectionAccess(principalsForAOSS);

        const collection = new aoss.CfnCollection(this, "OpensearchCollection", {
            name: this.collectionUid,
            type: "SEARCH",
        });

        const encryptionPolicy = {
            Rules: [{ ResourceType: "collection", Resource: [`collection/${collection.name}`] }],
            AWSOwnedKey: true,
        };
        const encryptionPolicyCfn = new aoss.CfnSecurityPolicy(this, "OpensearchEncryptionPolicy", {
            name: `EncryptPolicy${this.collectionUid}`.toLowerCase(),
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
            name: `NetworkPolicy${this.collectionUid}`.toLowerCase(),
            policy: JSON.stringify(networkPolicy),
            type: "network",
        });

        collection.addDependency(encryptionPolicyCfn);
        collection.addDependency(networkPolicyCfn);

        const schemaDeployProvider = new cr.Provider(
            this,
            "OpensearchServerlessDeploySchemaProvider",
            {
                onEventHandler: schemaDeploy,
            }
        );

        schemaDeployProvider.node.addDependency(schemaDeploy);
        schemaDeployProvider.node.addDependency(collection);
        schemaDeployProvider.node.addDependency(accessPolicy);

        new CustomResource(this, "DeployIndex", {
            serviceToken: schemaDeployProvider.serviceToken,
            properties: {
                collectionName: collection.name,
                indexName: "assets1236",
                stackName: cdk.Stack.of(this).stackName,
                version: "1",
            },
        });
    }

    // type ConstructWithRole = Construct & { role?: cdk.aws_iam.IRole };
    public endpointSSMParameterName(): string {
        // look up parameter store value
        return "/" + [cdk.Stack.of(this).stackName, this.collectionUid, "endpoint"].join("/");
        // return cdk.aws_ssm.StringParameter.valueForStringParameter(this,
    }

    // todo rename to grantXxxx
    public grantCollectionAccess(construct: Construct & { role?: cdk.aws_iam.IRole }) {
        const policy = [
            {
                Description: "Access",
                Rules: [
                    {
                        ResourceType: "index",
                        // Resource: ["index/*/*"],
                        Resource: [`index/${this.collectionUid}/assets1236`],
                        Permission: [
                            // "aoss:*",
                            "aoss:ReadDocument",
                            "aoss:WriteDocument",
                            "aoss:CreateIndex",
                            "aoss:DeleteIndex",
                            "aoss:UpdateIndex",
                            "aoss:DescribeIndex",
                        ],
                    },
                    {
                        ResourceType: "collection",
                        Resource: [`collection/${this.collectionUid}`],
                        Permission: [
                            // "aoss:*",
                            "aoss:CreateCollectionItems",
                            "aoss:DeleteCollectionItems",
                            "aoss:UpdateCollectionItems",
                            "aoss:DescribeCollectionItems",
                        ],
                    },
                ],
                Principal: [construct.role?.roleArn],
            },
        ];

        const accessPolicy = new aoss.CfnAccessPolicy(construct, "Policy", {
            name: "acc" + Names.uniqueResourceName(construct, { maxLength: 8 }).toLowerCase(),
            type: "data",
            policy: JSON.stringify(policy),
        });

        construct.role?.addToPrincipalPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                resources: ["*"],
                actions: ["aoss:*"],
            })
        );
        return accessPolicy;
    }

    private _grantCollectionAccess(principalsForAOSS: (string | undefined)[]) {
        // type that extends Construct and has a role property
        const policy = [
            {
                Description: "Access",
                Rules: [
                    {
                        ResourceType: "index",
                        Resource: [`index/${this.collectionUid}/assets1236`],
                        Permission: ["aoss:*"],
                    },
                    {
                        ResourceType: "collection",
                        Resource: [`collection/${this.collectionUid}`],
                        Permission: ["aoss:*"],
                    },
                ],
                Principal: principalsForAOSS,
            },
        ];

        const accessPolicy = new aoss.CfnAccessPolicy(this, "Policy", {
            name: "acc" + Names.uniqueResourceName(this, { maxLength: 8 }).toLowerCase(),
            type: "data",
            policy: JSON.stringify(policy),
        });
        return accessPolicy;
    }
}
