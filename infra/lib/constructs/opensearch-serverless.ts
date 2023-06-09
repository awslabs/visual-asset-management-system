/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as aoss from "aws-cdk-lib/aws-opensearchserverless";

interface OpensearchServerlessConstructProps extends cdk.StackProps {
    principalArn: string[];
}

export class OpensearchServerlessConstruct extends Construct {
    constructor(parent: Construct, name: string, props: OpensearchServerlessConstructProps) {
        super(parent, name);

        const policy = [
            {
                Description: "Access for test-user",
                Rules: [
                    { ResourceType: "index", Resource: ["index/*/*"], Permission: ["aoss:*"] },
                    {
                        ResourceType: "collection",
                        Resource: ["collection/my-collection"],
                        Permission: ["aoss:*"],
                    },
                ],
                Principal: props.principalArn,
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
    }
}
