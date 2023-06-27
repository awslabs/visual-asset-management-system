/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { CfnOutput } from "aws-cdk-lib";
import * as ec2 from 'aws-cdk-lib/aws-ec2';
import { LogGroup, RetentionDays } from "aws-cdk-lib/aws-logs";
import { NagSuppressions } from "cdk-nag";


/* eslint-disable @typescript-eslint/no-empty-interface */
export interface VpcSecurityGroupGatewayWebVisualizerPipelineConstructProps {

}

const defaultProps: Partial<VpcSecurityGroupGatewayWebVisualizerPipelineConstructProps> = {
  stackName: "",
  env: {},
};

/**
 * Custom configuration to Cognito.
 */
export class VpcSecurityGroupGatewayWebVisualizerPipelineConstruct extends Construct {

  readonly vpc: ec2.Vpc;
  readonly subnets: {
    pipeline: ec2.ISubnet[];
  };
  readonly securityGroups: {
    pipeline: ec2.SecurityGroup;
  };

  constructor(parent: Construct, name: string, props: VpcSecurityGroupGatewayWebVisualizerPipelineConstructProps) {
    super(parent, name);

    props = { ...defaultProps, ...props };

    /**
     * Subnets
     */
    const pipelineSubnetConfig: ec2.SubnetConfiguration = {
      name: "pipeline-private-subnet",
      subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
      cidrMask: 22, // 1024
    };

    /**
     * VPC
     */
    const vpcLogsGroups = new LogGroup(this, "CloudWatchVPCLogs", {
      retention: RetentionDays.ONE_WEEK,
      removalPolicy: cdk.RemovalPolicy.RETAIN,
    });

    const cidrRange: string = "10.0.0.0/16"; // 4096

    this.vpc = new ec2.Vpc(this, "Vpc", {
      ipAddresses: ec2.IpAddresses.cidr(cidrRange),
      subnetConfiguration: [
        pipelineSubnetConfig,
      ],
      maxAzs: 1, //One 1AZ as VPC is for a pipeline that can re-generate temporary files
      enableDnsHostnames: true,
      enableDnsSupport: true,
      flowLogs: {
        "vpc-logs": {
          destination: ec2.FlowLogDestination.toCloudWatchLogs(vpcLogsGroups),
          trafficType: ec2.FlowLogTrafficType.ALL,
        },
      },
    });

    this.subnets = {
      pipeline: this.vpc.selectSubnets({
        subnetGroupName: pipelineSubnetConfig.name,
      }).subnets,
    };

    /**
     * Security Groups
     */
    const pipelineSecurityGroup = new ec2.SecurityGroup(this, "PipelineSecurityGroup", {
      vpc: this.vpc,
      allowAllOutbound: true,
      description: "Pipeline Security Group",
    });

    // add ingress rules to allow Fargate to pull from ECR
    pipelineSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
      ec2.Port.tcp(443),
      "Allow HTTPS for ECR Access"
    );
    pipelineSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
      ec2.Port.tcp(53),
      "Allow TCP for ECR Access"
    );
    pipelineSecurityGroup.addIngressRule(
      ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
      ec2.Port.udp(53),
      "Allow UDP for ECR Access"
    );

    this.securityGroups = {
      pipeline: pipelineSecurityGroup,
    };

    /**
     * Gateway Endpoints
     */
    this.vpc.addGatewayEndpoint("S3Endpoint", {
      service: ec2.GatewayVpcEndpointAwsService.S3,
    });

    /**
     * VPC Endpoints
     */
    // Create VPC endpoint for ECR API
    new ec2.InterfaceVpcEndpoint(this, "ECRAPIEndpoint", {
      vpc: this.vpc,
      privateDnsEnabled: true, // Needed for Fargate<->ECR
      service: ec2.InterfaceVpcEndpointAwsService.ECR,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [pipelineSecurityGroup]
    });

    // Create VPC endpoint for ECR Docker API
    new ec2.InterfaceVpcEndpoint(this, "ECRDockerEndpoint", {
      vpc: this.vpc,
      privateDnsEnabled: true, // Needed for Fargate<->ECR
      service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [pipelineSecurityGroup]
    });

    // Create VPC endpoint for Batch
    new ec2.InterfaceVpcEndpoint(this, "BatchEndpoint", {
      vpc: this.vpc,
      privateDnsEnabled: true,
      service: ec2.InterfaceVpcEndpointAwsService.BATCH,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [pipelineSecurityGroup]
    });

    // Create VPC endpoint for CloudWatch Logs
    new ec2.InterfaceVpcEndpoint(this, "CloudWatchEndpoint", {
      vpc: this.vpc,
      privateDnsEnabled: true,
      service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [pipelineSecurityGroup]
    });

    // Create VPC endpoint for SNS
    new ec2.InterfaceVpcEndpoint(this, "SNSEndpoint", {
      vpc: this.vpc,
      privateDnsEnabled: true,
      service: ec2.InterfaceVpcEndpointAwsService.SNS,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [pipelineSecurityGroup]
    });

    // Create VPC endpoint for SNS
    new ec2.InterfaceVpcEndpoint(this, "SFNEndpoint", {
      vpc: this.vpc,
      privateDnsEnabled: true,
      service: ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS,
      subnets: { subnetType: ec2.SubnetType.PRIVATE_ISOLATED },
      securityGroups: [pipelineSecurityGroup]
    });

    /**
     * Outputs
     */
    new CfnOutput(this, "PipelineVpcId", {
      value: this.vpc.vpcId,
    });
  }
}
