/**
 * Copyright 2022 Amazon.com, Inc. and its affiliates. All Rights Reserved.
 *
 * Licensed under the Amazon Software License (the "License").
 * You may not use this file except in compliance with the License.
 * A copy of the License is located at
 *
 *   http://aws.amazon.com/asl/
 *
 * or in the "license" file accompanying this file. This file is distributed
 * on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
 * express or implied. See the License for the specific language governing
 * permissions and limitations under the License.
 */

import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";
import { OpensearchServerlessConstruct } from "./constructs/opensearch-serverless";
import { NagSuppressions } from "cdk-nag";

interface EnvProps {
    env?: cdk.Environment;
    stackName: string;
}

export class AossStack extends cdk.Stack {
    constructor(scope: Construct, id: string, props: EnvProps) {
        super(scope, id, { ...props, crossRegionReferences: true });

        new OpensearchServerlessConstruct(this, "OpensearchServerlessConstruct", {
            principalArn: [
                "arn:aws:iam::098204178297:role/vams-dev-us-east-1-CognitoDefaultAuthenticatedRole-1XK4JA5DESZWR",
                "arn:aws:iam::098204178297:role/Admin",
            ],
        });

        NagSuppressions.addResourceSuppressions(
            this,
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

        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-L1",
                    reason: "Configured as intended.",
                },
            ]
        );

        NagSuppressions.addResourceSuppressionsByPath(this, `/${this.stackName}/OpensearchServerlessConstruct/OpensearchServerlessDeploySchemaProvider/framework-onEvent/Resource`, [
            {
                id: "AwsSolutions-L1",
                reason: "Configured as intended."
            }
        ])

        NagSuppressions.addResourceSuppressions(
            this,
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
}
