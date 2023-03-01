/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

 import * as cdk from 'aws-cdk-lib';
import * as wafv2 from "aws-cdk-lib/aws-wafv2";
import { Construct } from "constructs";
export enum WAFScope {
    CLOUDFRONT = "CLOUDFRONT",
    REGIONAL = "REGIONAL",
}

export interface Wafv2BasicConstructProps extends cdk.StackProps {
    readonly wafScope?: WAFScope;
    readonly rules?: Array<wafv2.CfnWebACL.RuleProperty | cdk.IResolvable> | cdk.IResolvable;
    readonly stackName?: string;
    readonly env?: cdk.Environment;
}

/**
 * Default input properties
 */
const defaultProps: Partial<Wafv2BasicConstructProps> = {
    wafScope: WAFScope.CLOUDFRONT,
    stackName: "",
    env: {},
    rules: [{                
        priority: 1,
        overrideAction: {count: {}},   //change this back to none might blocks some  existing requests, however, it will reduce security risk
        visibilityConfig: {
            sampledRequestsEnabled: true,
            cloudWatchMetricsEnabled: true,
            metricName: "AWS-AWSManagedRulesCommonRuleSet",
        },
        name: "AWS-AWSManagedRulesCommonRuleSet",
        statement: {
            managedRuleGroupStatement: {
                vendorName: "AWS",
                name: "AWSManagedRulesCommonRuleSet",
            },
        },
    }]
};

/**
 * Deploys the notification handlers
 */
export class Wafv2BasicConstruct extends Construct {
    public webacl: wafv2.CfnWebACL;

    constructor(parent: Construct, name: string, props: Wafv2BasicConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const wafScopeString = props.wafScope!.toString();
        
        /*
        if (props.wafScope === WAFScope.CLOUDFRONT && props.env?.region !== "us-east-1") {
            throw new Error(
                "Only supported region for WAFv2 scope when set to CLOUDFRONT is us-east-1. " +
                    "see - https://docs.aws.amazon.com/AWSCloudFormation/latest/UserGuide/aws-resource-wafv2-webacl.html"
            );
        } */

        const webacl = new wafv2.CfnWebACL(this, "webacl", {
            description: "Basic WAF",
            defaultAction: {
                allow: {},
            },
            rules: props.rules,
            scope: wafScopeString,
            visibilityConfig: {
                cloudWatchMetricsEnabled: true,
                metricName: "WAFACLGlobal",
                sampledRequestsEnabled: true,
            },
        });

        this.webacl = webacl;
    }
}
