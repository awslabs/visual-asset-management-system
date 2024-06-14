/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { CfnOutput } from "aws-cdk-lib";
import { Stack } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LogGroup, RetentionDays } from "aws-cdk-lib/aws-logs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../config/config";
import { generateUniqueNameHash } from "../../../helper/security";

/* eslint-disable @typescript-eslint/no-empty-interface */
export interface GatewayAlbDeployConstructProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    subnetsPrivate: ec2.ISubnet[];
    subnetsPublic: ec2.ISubnet[];
}

const defaultProps: Partial<GatewayAlbDeployConstructProps> = {
    stackName: "",
    env: {},
};

/**
 * Custom configuration to Cognito.
 */
export class GatewayAlbDeployConstruct extends Construct {
    readonly vpc: ec2.IVpc;
    readonly subnets: {
        webApp: ec2.ISubnet[];
    };

    readonly securityGroups: {
        webAppALB: ec2.SecurityGroup;
        webAppVPCE: ec2.SecurityGroup;
    };

    readonly s3VpcEndpoint: ec2.InterfaceVpcEndpoint;

    constructor(parent: Construct, name: string, props: GatewayAlbDeployConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        this.vpc = props.vpc;

        //At this point we already know there is at least 2 public or private/isolate subnets with other checks previously done
        this.subnets = {
            webApp: props.config.app.useAlb.usePublicSubnet
                ? props.subnetsPublic
                : props.subnetsPrivate,
        };

        //Create ALB security group and open to any IP on port 443/80
        const webAppALBSecurityGroup = new ec2.SecurityGroup(this, "WepAppDistroALBSecurityGroup", {
            vpc: props.vpc,
            allowAllOutbound: true,
            description: "Web Application Distribution for ALB Security Group",
        });

        webAppALBSecurityGroup.connections.allowFromAnyIpv4(ec2.Port.tcp(80));
        webAppALBSecurityGroup.connections.allowFromAnyIpv4(ec2.Port.tcp(443));

        //Create VPC Endpoint for ALB-<->S3 Comms
        //Rules will be setup in the ALB construct / stack
        const webAppVPCESecurityGroup = new ec2.SecurityGroup(
            this,
            "WepAppDistroVPCS3EndpointSecurityGroup",
            {
                vpc: props.vpc,
                allowAllOutbound: true,
                description: "Web Application Distribution for VPC S3 Endpoint Security Group",
            }
        );

        this.securityGroups = {
            webAppALB: webAppALBSecurityGroup,
            webAppVPCE: webAppVPCESecurityGroup,
        };

        // Create VPC interface endpoint for S3 (Needed for ALB<->S3)
        //Note: This endpoint should be created despite the GlobalVPC flag of create endpoint or not in order to setup ALB listeners properly
        const s3VPCEndpoint = new ec2.InterfaceVpcEndpoint(this, "S3InterfaceVPCEndpoint", {
            vpc: props.vpc,
            privateDnsEnabled: false,
            service: ec2.InterfaceVpcEndpointAwsService.S3,
            subnets: { subnets: this.subnets.webApp },
            securityGroups: [webAppVPCESecurityGroup],
        });

        this.s3VpcEndpoint = s3VPCEndpoint;

        //Nag Supressions
        NagSuppressions.addResourceSuppressions(webAppVPCESecurityGroup, [
            {
                id: "AwsSolutions-EC23",
                reason: "Web App VPC Endpoint Security Group is restricted to ALB on ports 443 and 80.",
            },
            {
                id: "CdkNagValidationFailure",
                reason: "Validation failure due to inherent nature of CDK Nag Validations of CIDR ranges", //https://github.com/cdklabs/cdk-nag/issues/817
            },
        ]);

        NagSuppressions.addResourceSuppressions(webAppALBSecurityGroup, [
            {
                id: "AwsSolutions-EC23",
                reason: "Web App ALB Security Group is purposely left open to any IP (0.0.0.0) on port 443 and 80 as this is the public website entry point",
            },
        ]);
    }
}
