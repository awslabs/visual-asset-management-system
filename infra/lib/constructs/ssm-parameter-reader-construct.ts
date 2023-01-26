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

import * as cdk from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as iam from "aws-cdk-lib/aws-iam";
import * as custom_resources from 'aws-cdk-lib/custom-resources';
import { Construct } from "constructs";
import { CfnCustomResource, CustomResource } from "aws-cdk-lib";
import { CfnFunction } from "aws-cdk-lib/aws-lambda";

export interface SsmParameterReaderConstructProps extends cdk.StackProps {
    readonly ssmParameterName: string;
    readonly ssmParameterRegion: string;
    /**
     * Sets the physical resource id to current date to force a pull of the parameter on subsequent
     * deploys
     *
     * @default false
     */
    readonly pullEveryTime?: boolean;
}

const defaultProps: Partial<SsmParameterReaderConstructProps> = {
    pullEveryTime: false,
};

/**
 * Deploys the SsmParameterReaderConstruct construct
 *
 * Reads a inter / intra region parameter by name.
 *
 */
export class SsmParameterReaderConstruct extends Construct {
    public ssmParameter: cdk.CustomResource;

    constructor(parent: Construct, name: string, props: SsmParameterReaderConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const stack = cdk.Stack.of(this);

        const physicalResourceId = props.pullEveryTime
            ? Date.UTC.toString()
            : `${props.ssmParameterName}-${props.ssmParameterRegion}`;
        
        const ssmGetParameterFunction = new lambda.Function(this, "getSSMParameterFunction", {
            runtime: lambda.Runtime.PYTHON_3_9,
            code: lambda.Code.fromInline(this.ssmGetParameterLambda()),
            handler: "index.lambda_handler",
            timeout: cdk.Duration.seconds(15),
            environment: {
                REGION: props.ssmParameterRegion,
                PHYSICAL_RESOURCE_ID: physicalResourceId,
            },
        });

        const getParameterPolicy = new iam.PolicyStatement();
        getParameterPolicy.addResources(`arn:aws:ssm:${props.ssmParameterRegion}:${stack.account}:parameter/${props.ssmParameterName}`);
        getParameterPolicy.addActions('ssm:GetParameters')

        ssmGetParameterFunction.addToRolePolicy(getParameterPolicy);

        const ssmGetParameterProvider = new custom_resources.Provider(this, "ssmGetParameterProvider", {
            onEventHandler: ssmGetParameterFunction,
        })
        //const cfnProvider = ssmGetParameterProvider.node.defaultChild as CfnCustomResource
        const cfnFunction = ssmGetParameterProvider.node.children[0].node.defaultChild as CfnFunction
        cfnFunction.runtime = "nodejs18.x"
        console.log(cfnFunction)

        this.ssmParameter = new CustomResource(this, 'Param', {
            serviceToken: ssmGetParameterProvider.serviceToken,
            properties: {
                ParameterName: `${props.ssmParameterName}`
            },
        })

        // this.ssmParameter = new cdk.custom_resources.AwsCustomResource(this, "Param", {
        //     onUpdate: {
        //         service: "SSM",
        //         action: "getParameter",
        //         parameters: { Name: `${props.ssmParameterName}` },
        //         region: props.ssmParameterRegion,
        //         physicalResourceId: cdk.custom_resources.PhysicalResourceId.of(physicalResourceId),
        //     },
        //     policy: cdk.custom_resources.AwsCustomResourcePolicy.fromSdkCalls({
        //         resources: [
        //             `arn:aws:ssm:${props.ssmParameterRegion}:${stack.account}:parameter/${props.ssmParameterName}`,
        //         ],
        //     }),
        // });
        
    }

    private ssmGetParameterLambda(): string {
        // string requires left justification so that the python code is correctly indented
        return `
        import json
        import boto3
        import os
        
        ssm = boto3.client('ssm',region_name= os.getenv("REGION", None) )
        
        def lambda_handler(event, context):
            
            print(event)
            props = event['ResourceProperties']
            param_name = props['ParameterName']
            
            param_response = ssm.get_parameter(
                Name=param_name,
            )['Parameter']
            print(param_response)
            
            output = {
                    'PhysicalResourceId': os.getenv("PHYSICAL_RESOURCE_ID", None),
                    'Data': {
                        'ParameterValue': param_response["Value"]
                    }
                }
            
            print("Output: " + json.dumps(output))
            return output
      `;
    }

    /**
     * @returns string value of the parameter
     */
    public getValue() {
        return this.ssmParameter.getAtt("ParameterValue").toString();
    }
}
