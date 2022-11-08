#!/usr/bin/env node
import 'source-map-support/register';
import * as cdk from '@aws-cdk/core';
import { VAMS } from '../lib/infra-stack';

const app = new cdk.App();

/** development variables **/
const region = process.env.AWS_REGION || app.node.tryGetContext("region");
const stackName = process.env.STACK_NAME || app.node.tryGetContext("stack-name");

console.log('STACK_NAME 👉', stackName);
console.log('REGION 👉', region);

/** demo factory variables **/
console.log('DEMO_LABEL 👉', process.env.DEMO_LABEL);

const vamsStack = new VAMS(app, `${stackName || process.env.DEMO_LABEL || 'dev'}`, {prod: false, stackName: `vams--${stackName || process.env.DEPLOYMENT_ENV || 'dev'}`});
//new VAMS(app, 'prod', {prod: true, stackName: 'vams--prod'});
app.synth();
