#!/usr/bin/env node

/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import 'source-map-support/register';
import * as cdk from 'aws-cdk-lib';
import { VAMS } from '../lib/infra-stack';
import { CfWafStack } from '../lib/cf-waf-stack';
import { AwsSolutionsChecks } from 'cdk-nag'
import { Aspects } from 'aws-cdk-lib';

const app = new cdk.App();

/** development variables **/
const region = process.env.AWS_REGION || app.node.tryGetContext("region");
const stackName = (process.env.STACK_NAME || app.node.tryGetContext("stack-name")) + "-" + region;
const dockerDefaultPlatform = process.env.DOCKER_DEFAULT_PLATFORM ;
const enableCdkNag = process.env.CDK_NAG_ENABLED || false; 

console.log('CDK_NAG_ENABLED 👉', enableCdkNag);
console.log('STACK_NAME 👉', stackName);
console.log('REGION 👉', region);
console.log('DOCKER_DEFAULT_PLATFORM 👉', dockerDefaultPlatform);

/** demo factory variables **/
console.log('DEMO_LABEL 👉', process.env.DEMO_LABEL);

if(enableCdkNag) {
    Aspects.of(app).add(new AwsSolutionsChecks({ verbose: true }))
}

//The web access firewall currently needs to be in us-east-1
const cfWafStack = new CfWafStack(app, `vams-waf-${stackName || process.env.DEMO_LABEL || 'dev'}`, {
    stackName: `vams-waf-${stackName || process.env.DEPLOYMENT_ENV || 'dev'}`,
    env: {
        account: process.env.CDK_DEFAULT_ACCOUNT,
        region: "us-east-1",
    }
})

const vamsStack = new VAMS(app, `vams-${stackName || process.env.DEMO_LABEL || 'dev'}`, {
    prod: false, 
    stackName: `vams-${stackName || process.env.DEPLOYMENT_ENV || 'dev'}`,
    env: {
        account: process.env.CDK_DEFAULT_ACCOUNT,
        region: region,
        
    },
    ssmWafArnParameterName: cfWafStack.ssmWafArnParameterName,
    ssmWafArnParameterRegion: cfWafStack.region,
});

vamsStack.addDependency(cfWafStack);
//new VAMS(app, 'prod', {prod: true, stackName: 'vams--prod'});


app.synth();
