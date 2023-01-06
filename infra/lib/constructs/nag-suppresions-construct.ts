/**
 * Copyright 2022 Amazon.com, Inc. and its affiliates. All Rights Reserved.
 *
 * Licensed under the Amazon Software License (the "License").
 * You may not use this file except in compliance with the License.
 * A copy of the License is located at
 *
 *   https://aws.amazon.com/asl/
 *
 * or in the "license" file accompanying this file. This file is distributed
 * on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
 * express or implied. See the License for the specific language governing
 * permissions and limitations under the License.
 */

import { NagSuppressions } from 'cdk-nag';
import { Stack } from 'aws-cdk-lib';

export function StackNagSuppression(scope:Stack) {
    NagSuppressions.addResourceSuppressions(scope, [
        {
            id: "AwsSolutions-IAM4",
            reason: "Intend to use AWSLambdaBasicExecutionRole as is at this stage of this project.",
            appliesTo: [
                {
                    regex: "/.*AWSLambdaBasicExecutionRole$/g",
                }
            ]
        }
    ], true);
    NagSuppressions.addResourceSuppressionsByPath(scope, "/dev/WebApp/WebAppDistribution/Resource", [
        {
            id: "AwsSolutions-CFR4",
            reason: "This requires use of a custom viewer certificate which should be provided by customers."
        }
    ], true);
    const refactorPaths = [
        "/dev/NestedAPIBuilder/VAMSWorkflowIAMRole/Resource", 
        "/dev/NestedAPIBuilder/lambdaPipelineRole", 
        "/dev/NestedAPIBuilder/pipelineService", 
        "/dev/NestedAPIBuilder/workflowService", 
        "/dev/NestedAPIBuilder/listExecutions"
    ]
    for(let path of refactorPaths) {
        const reason = 
            `Intention is to refactor this model away moving forward 
             so that this type of access is not required within the stack.
             Customers are advised to isolate VAMS to its own account in test and prod
             as a substitute to tighter resource access.`;
        NagSuppressions.addResourceSuppressionsByPath(scope, path, [
            {
                id: "AwsSolutions-IAM5",
                reason: reason,
            },
            {
                id: "AwsSolutions-IAM4",
                reason: reason,
            }
        ], true);
    }
}