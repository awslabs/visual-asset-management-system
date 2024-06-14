/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { CfnOutput, Names } from "aws-cdk-lib";
import { Stack } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LogGroup, RetentionDays } from "aws-cdk-lib/aws-logs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../config/config";
import { generateUniqueNameHash } from "../../../helper/security";

/* eslint-disable @typescript-eslint/no-empty-interface */
export interface SecurityGroupGatewayVisualizerPipelineConstructProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    vpceSecurityGroup: ec2.ISecurityGroup;
    subnets: ec2.ISubnet[];
}

const defaultProps: Partial<SecurityGroupGatewayVisualizerPipelineConstructProps> = {
    //stackName: "",
    //env: {},
};

/**
 * Custom configuration to Cognito.
 */
export class SecurityGroupGatewayVisualizerPipelineConstruct extends Construct {
    readonly vpc: ec2.IVpc;
    readonly subnets: {
        pipeline: ec2.ISubnet[];
    };
    readonly securityGroups: {
        pipeline: ec2.ISecurityGroup;
    };

    constructor(
        parent: Construct,
        name: string,
        props: SecurityGroupGatewayVisualizerPipelineConstructProps
    ) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        this.vpc = props.vpc;

        //For pipelines we are only deploying in 1 subnet/AZ, so just grab the top one from the isolated/private subnet list
        //At this point we already know there is at least 1  subnet with other checks previously done
        this.subnets = {
            pipeline: [props.subnets[0]],
        };

        this.securityGroups = {
            pipeline: props.vpceSecurityGroup,
        };

        ///////Commented out as now handled by global VPC setup (keeping in case this gets split out in the future)
        // /**
        //  * Security Groups
        //  */
        // const pipelineSecurityGroup = new ec2.SecurityGroup(
        //     this,
        //     "VisualizerPipelineSecurityGroup",
        //     {
        //         vpc: this.vpc,
        //         allowAllOutbound: true,
        //         description: "Visualizer Pipeline Security Group",
        //     }
        // );

        // // add ingress rules to allow Fargate to pull from ECR
        // pipelineSecurityGroup.addIngressRule(
        //     ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
        //     ec2.Port.tcp(443),
        //     "Allow HTTPS Access"
        // );
        // pipelineSecurityGroup.addIngressRule(
        //     ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
        //     ec2.Port.tcp(53),
        //     "Allow TCP for ECR Access"
        // );
        // pipelineSecurityGroup.addIngressRule(
        //     ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
        //     ec2.Port.udp(53),
        //     "Allow UDP for ECR Access"
        // );

        // this.securityGroups = {
        //     pipeline: pipelineSecurityGroup,
        // };

        // //Add VPC endpoints based on configuration options
        // //Note: This is mostly to not duplicate endpoints if bringing in an external VPC that already has the needed endpoints for the services
        // if(props.config.app.useGlobalVpc.addVpcEndpoints)
        // {
        //     //Note: S3 Gateway Added at the VPC global level, but keeping here in case the pipeline code ever get's split out
        //     // /**
        //     //  * Gateway Endpoints
        //     //  */
        //     // this.vpc.addGatewayEndpoint("S3Endpoint", {
        //     //     service: ec2.GatewayVpcEndpointAwsService.S3,
        //     //     subnets: [{ subnets: this.subnets.pipeline}],
        //     // });

        //     /**
        //      * VPC Endpoints
        //      */
        //     // Create VPC endpoint for ECR API
        //     new ec2.InterfaceVpcEndpoint(this, "ECRAPIEndpoint", {
        //         vpc: this.vpc,
        //         privateDnsEnabled: true, // Needed for Fargate<->ECR
        //         service: ec2.InterfaceVpcEndpointAwsService.ECR,
        //         subnets: { subnets: this.subnets.pipeline},
        //         securityGroups: [pipelineSecurityGroup],
        //     });

        //     // Create VPC endpoint for ECR Docker API
        //     new ec2.InterfaceVpcEndpoint(this, "ECRDockerEndpoint", {
        //         vpc: this.vpc,
        //         privateDnsEnabled: true, // Needed for Fargate<->ECR
        //         service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
        //         subnets: { subnets: this.subnets.pipeline},
        //         securityGroups: [pipelineSecurityGroup],
        //     });

        //     // Create VPC endpoint for Batch
        //     new ec2.InterfaceVpcEndpoint(this, "BatchEndpoint", {
        //         vpc: this.vpc,
        //         privateDnsEnabled: true,
        //         service: ec2.InterfaceVpcEndpointAwsService.BATCH,
        //         subnets: { subnets: this.subnets.pipeline},
        //         securityGroups: [pipelineSecurityGroup],
        //     });

        //     // Create VPC endpoint for CloudWatch Logs
        //     new ec2.InterfaceVpcEndpoint(this, "CloudWatchEndpoint", {
        //         vpc: this.vpc,
        //         privateDnsEnabled: true,
        //         service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
        //         subnets: { subnets: this.subnets.pipeline},
        //         securityGroups: [pipelineSecurityGroup],
        //     });

        //     // Create VPC endpoint for SNS
        //     new ec2.InterfaceVpcEndpoint(this, "SNSEndpoint", {
        //         vpc: this.vpc,
        //         privateDnsEnabled: true,
        //         service: ec2.InterfaceVpcEndpointAwsService.SNS,
        //         subnets: { subnets: this.subnets.pipeline},
        //         securityGroups: [pipelineSecurityGroup],
        //     });

        //     // Create VPC endpoint for SNS
        //     new ec2.InterfaceVpcEndpoint(this, "SFNEndpoint", {
        //         vpc: this.vpc,
        //         privateDnsEnabled: true,
        //         service: ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS,
        //         subnets: { subnets: this.subnets.pipeline},
        //         securityGroups: [pipelineSecurityGroup],
        //     });
        // }

        // //Nag Supressions
        // NagSuppressions.addResourceSuppressionsByPath(
        //     Stack.of(this),
        //     `/${this.toString()}/VisualizerPipelineSecurityGroup/Resource`,
        //     [
        //         {
        //             id: "AwsSolutions-EC23",
        //             reason: "Pipeline Security Group is restricted to VPC cidr range on ports 443 and 53",
        //         },
        //         {
        //             id: "CdkNagValidationFailure",
        //             reason: "Validation failure due to inherent nature of CDK Nag Validations of CIDR ranges", //https://github.com/cdklabs/cdk-nag/issues/817
        //         },
        //     ]
        // );
    }
}
