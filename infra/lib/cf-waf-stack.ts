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

import { Wafv2BasicConstruct, WAFScope } from "./constructs/wafv2-basic-construct";
import { Construct } from "constructs";
import * as cdk from "aws-cdk-lib";

interface EnvProps {
    env?: cdk.Environment;
    stackName: string;
    wafScope?: WAFScope;
}

export class CfWafStack extends cdk.Stack {
    public ssmWafArnParameterName: string;
    public wafArn: string;

    constructor(scope: Construct, id: string, props: EnvProps) {
        super(scope, id, { ...props, crossRegionReferences: true });

        // ssm parameter name must be unique in a region
        this.ssmWafArnParameterName = "waf_acl_arn_" + this.stackName;

        const wafv2CF = new Wafv2BasicConstruct(this, "Wafv2CF", {
            ...props,
            wafScope: props.wafScope,
        });

        new cdk.aws_ssm.StringParameter(this, "waf_acl_arn", {
            parameterName: this.ssmWafArnParameterName,
            description: "WAF ACL ARN",
            stringValue: wafv2CF.webacl.attrArn,
        });

        this.wafArn = wafv2CF.webacl.attrArn;
    }
}
