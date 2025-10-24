/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { CfnOutput } from "aws-cdk-lib";
import { Stack, NestedStack } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LogGroup, RetentionDays } from "aws-cdk-lib/aws-logs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../config/config";
import { generateUniqueNameHash } from "../../helper/security";

export interface VPCBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
}

/**
 * Default input properties
 */
const defaultProps: Partial<VPCBuilderNestedStackProps> = {};

export class VPCBuilderNestedStack extends NestedStack {
    public vpc: ec2.IVpc;
    public isolatedSubnets: ec2.ISubnet[] = []; // isolated
    public privateSubnets: ec2.ISubnet[] = []; // private + egress
    public publicSubnets: ec2.ISubnet[] = [];
    public vpceSecurityGroup: ec2.ISecurityGroup;

    private azCount: number;

    constructor(parent: Construct, name: string, props: VPCBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        //Set how many AZ's we need. Note: GovCloud only has max 3 AZs as of 11/09/2023
        //VisualizerPipelineReqs - 1Az - Private Subnet (Each)
        //ALBReqs or All Lambdas - 2AZ - Private or PublicSubnet (Each)
        //OpenSearchProvisioned - 3AZ - Private Subnet (Each)
        if (props.config.app.openSearch.useProvisioned.enabled) {
            this.azCount = 3;
        } else if (
            props.config.app.useAlb.enabled ||
            props.config.app.useGlobalVpc.useForAllLambdas
        ) {
            this.azCount = 2;
        }
        //Visualizer pipeline
        else this.azCount = 1;

        console.log("VPC AZ Count: ", this.azCount);

        if (
            props.config.app.useGlobalVpc.optionalExternalVpcId &&
            props.config.app.useGlobalVpc.optionalExternalVpcId != "" &&
            props.config.app.useGlobalVpc.optionalExternalVpcId != "UNDEFINED"
        ) {
            //Use Existing VPC
            this.vpc = ec2.Vpc.fromLookup(this, "ImportedVPC", {
                isDefault: false,
                vpcId: props.config.app.useGlobalVpc.optionalExternalVpcId.trim(),
            });

            //Get subnet IDs provided
            const subnetPrivateIds =
                props.config.app.useGlobalVpc.optionalExternalPrivateSubnetIds.split(",");
            const subnetIsolatedIds =
                props.config.app.useGlobalVpc.optionalExternalIsolatedSubnetIds.split(",");
            const subnetPublicIds =
                props.config.app.useGlobalVpc.optionalExternalPublicSubnetIds.split(",");

            //(Should run after CDK context is loaded) Resolve Subnets, Check if exists , and check for errors
            if (!props.config.env.loadContextIgnoreVPCStacks) {
                if (
                    props.config.app.useGlobalVpc.optionalExternalIsolatedSubnetIds &&
                    props.config.app.useGlobalVpc.optionalExternalIsolatedSubnetIds != "" &&
                    props.config.app.useGlobalVpc.optionalExternalIsolatedSubnetIds != "UNDEFINED"
                ) {
                    subnetIsolatedIds.forEach((element) => {
                        let foundVPCSubnet = false;

                        //Check all VPC subnets (even public) - user is defining public/private use based on configuration input and not what the VPC says
                        if (this.vpc.isolatedSubnets && this.vpc.isolatedSubnets.length > 0) {
                            this.vpc.isolatedSubnets.forEach((vpcSubnet) => {
                                //console.log(element.subnetId, vpcSubnet.subnetId, "I")
                                if (vpcSubnet.subnetId == element.trim() && !foundVPCSubnet) {
                                    foundVPCSubnet = true;
                                    this.isolatedSubnets.push(vpcSubnet);
                                }
                            });
                        }
                        if (this.vpc.privateSubnets && this.vpc.privateSubnets.length > 0) {
                            this.vpc.privateSubnets.forEach((vpcSubnet) => {
                                //console.log(element.subnetId, vpcSubnet.subnetId, "Pr")
                                if (vpcSubnet.subnetId == element.trim() && !foundVPCSubnet) {
                                    foundVPCSubnet = true;
                                    this.isolatedSubnets.push(vpcSubnet);
                                }
                            });
                        }
                        if (this.vpc.publicSubnets && this.vpc.publicSubnets.length > 0) {
                            this.vpc.publicSubnets.forEach((vpcSubnet) => {
                                //console.log(element.subnetId, vpcSubnet.subnetId, "Pu")
                                if (vpcSubnet.subnetId == element && !foundVPCSubnet) {
                                    foundVPCSubnet = true;
                                    this.isolatedSubnets.push(vpcSubnet);
                                }
                            });
                        }

                        if (!foundVPCSubnet) {
                            throw new Error(
                                `Existing Isolated Subnet ID ${element} provided does not exist in the provided VPC! Note: This may indiciate you need to synth/deploy the stack with the loadContextIgnoreVPCStacks configuration flag set to 'true' first. `
                            );
                        }
                    });
                }

                if (
                    props.config.app.useGlobalVpc.optionalExternalPrivateSubnetIds &&
                    props.config.app.useGlobalVpc.optionalExternalPrivateSubnetIds != "" &&
                    props.config.app.useGlobalVpc.optionalExternalPrivateSubnetIds != "UNDEFINED"
                ) {
                    subnetPrivateIds.forEach((element) => {
                        let foundVPCSubnet = false;

                        //Check all VPC subnets (even public) - user is defining public/private use based on configuration input and not what the VPC says
                        if (this.vpc.isolatedSubnets && this.vpc.isolatedSubnets.length > 0) {
                            this.vpc.isolatedSubnets.forEach((vpcSubnet) => {
                                //console.log(element.subnetId, vpcSubnet.subnetId, "I")
                                if (vpcSubnet.subnetId == element.trim() && !foundVPCSubnet) {
                                    foundVPCSubnet = true;
                                    this.privateSubnets.push(vpcSubnet);
                                }
                            });
                        }
                        if (this.vpc.privateSubnets && this.vpc.privateSubnets.length > 0) {
                            this.vpc.privateSubnets.forEach((vpcSubnet) => {
                                //console.log(element.subnetId, vpcSubnet.subnetId, "Pr")
                                if (vpcSubnet.subnetId == element.trim() && !foundVPCSubnet) {
                                    foundVPCSubnet = true;
                                    this.privateSubnets.push(vpcSubnet);
                                }
                            });
                        }
                        if (this.vpc.publicSubnets && this.vpc.publicSubnets.length > 0) {
                            this.vpc.publicSubnets.forEach((vpcSubnet) => {
                                //console.log(element.subnetId, vpcSubnet.subnetId, "Pu")
                                if (vpcSubnet.subnetId == element && !foundVPCSubnet) {
                                    foundVPCSubnet = true;
                                    this.privateSubnets.push(vpcSubnet);
                                }
                            });
                        }

                        if (!foundVPCSubnet) {
                            throw new Error(
                                `Existing Private Subnet ID ${element} provided does not exist in the provided VPC! Note: This may indiciate you need to synth/deploy the stack with the loadContextIgnoreVPCStacks configuration flag set to 'true' first. `
                            );
                        }
                    });
                }

                if (
                    props.config.app.useGlobalVpc.optionalExternalPublicSubnetIds &&
                    props.config.app.useGlobalVpc.optionalExternalPublicSubnetIds != "" &&
                    props.config.app.useGlobalVpc.optionalExternalPublicSubnetIds != "UNDEFINED"
                ) {
                    subnetPublicIds.forEach((element) => {
                        //Check all VPC subnets (even private/isolated) - user is defining public/private use based on configuration input and not what the VPC says
                        let foundVPCSubnet = false;

                        if (this.vpc.publicSubnets && this.vpc.publicSubnets.length > 0) {
                            this.vpc.publicSubnets.forEach((vpcSubnet) => {
                                //console.log(element.subnetId, vpcSubnet.subnetId, "Pu")
                                if (vpcSubnet.subnetId == element && !foundVPCSubnet) {
                                    foundVPCSubnet = true;
                                    this.publicSubnets.push(vpcSubnet);
                                }
                            });
                        }

                        if (this.vpc.isolatedSubnets && this.vpc.isolatedSubnets.length > 0) {
                            this.vpc.isolatedSubnets.forEach((vpcSubnet) => {
                                //console.log(element.subnetId, vpcSubnet.subnetId, "I")
                                if (vpcSubnet.subnetId == element.trim() && !foundVPCSubnet) {
                                    foundVPCSubnet = true;
                                    this.publicSubnets.push(vpcSubnet);
                                }
                            });
                        }

                        if (this.vpc.privateSubnets && this.vpc.privateSubnets.length > 0) {
                            this.vpc.privateSubnets.forEach((vpcSubnet) => {
                                //console.log(element.subnetId, vpcSubnet.subnetId, "Pr")
                                if (vpcSubnet.subnetId == element.trim() && !foundVPCSubnet) {
                                    foundVPCSubnet = true;
                                    this.publicSubnets.push(vpcSubnet);
                                }
                            });
                        }

                        if (!foundVPCSubnet) {
                            throw new Error(
                                `Existing Public Subnet ID ${element} provided does not exist in the provided VPC!  Note: This may indiciate you need to synth/deploy the stack with the loadContextIgnoreVPCStacks configuration flag set to 'true' first.`
                            );
                        }
                    });
                }

                //Error checks
                //check to make sure we have at least X subnets in different AZs, X being the azCount
                const azPrivateUsed: string[] = [];
                const azIsolatedUsed: string[] = [];
                const azPublicUsed: string[] = [];

                this.isolatedSubnets.forEach((element) => {
                    if (azIsolatedUsed.indexOf(element.availabilityZone) == -1) {
                        azIsolatedUsed.push(element.availabilityZone);
                    }
                });

                if (
                    props.config.app.useGlobalVpc.optionalExternalIsolatedSubnetIds &&
                    props.config.app.useGlobalVpc.optionalExternalIsolatedSubnetIds != "" &&
                    props.config.app.useGlobalVpc.optionalExternalIsolatedSubnetIds !=
                        "UNDEFINED" &&
                    azIsolatedUsed.length < this.azCount
                ) {
                    throw new Error(
                        `Existing Isolated VPC Subnets must be spread across a minimum of ${this.azCount} availabilty zones based on the confiuguration options chosen, currently only representing ${azPrivateUsed.length}!`
                    );
                }

                // this.privateSubnets.forEach((element) => {
                //     if (azPrivateUsed.indexOf(element.availabilityZone) == -1) {
                //         azPrivateUsed.push(element.availabilityZone);
                //     }
                // });

                // if (
                //     props.config.app.useGlobalVpc.optionalExternalPrivateSubnetIds &&
                //     props.config.app.useGlobalVpc.optionalExternalPrivateSubnetIds != "" &&
                //     props.config.app.useGlobalVpc.optionalExternalPrivateSubnetIds != "UNDEFINED" &&
                //     azPrivateUsed.length < this.azCount
                // ) {
                //     throw new Error(
                //         `Existing Private VPC Subnets must be spread across a minimum of ${this.azCount} availabilty zones based on the confiuguration options chosen, currently only representing ${azPrivateUsed.length}!`
                //     );
                // }

                this.publicSubnets.forEach((element) => {
                    if (azPublicUsed.indexOf(element.availabilityZone) == -1) {
                        azPublicUsed.push(element.availabilityZone);
                    }
                });

                if (
                    props.config.app.useGlobalVpc.optionalExternalPublicSubnetIds &&
                    props.config.app.useGlobalVpc.optionalExternalPublicSubnetIds != "" &&
                    props.config.app.useGlobalVpc.optionalExternalPublicSubnetIds != "UNDEFINED" &&
                    azPublicUsed.length < this.azCount
                ) {
                    throw new Error(
                        `Existing Public VPC Subnets must be spread across a minimum of ${this.azCount} availabilty zones based on the confiuguration options chosen, currently only representing ${azPublicUsed.length}!`
                    );
                }

                //TODO: Make sure we have at least 1 private subnet as these are required for almost everything except ALB Public Subnet piece
                // if (this.isolatedSubnets.length == 0) {
                //     throw new Error(
                //         "Existing VPC and provided subnets must have at least 1 private subnet provided!"
                //     );
                // }

                if (
                    props.config.app.openSearch.useProvisioned.enabled &&
                    this.isolatedSubnets.length < 3
                ) {
                    throw new Error(
                        "Existing VPC and provided subnets must have at least 3 private subnets in different AZs already setup when using OpenSearch provisioned!"
                    );
                }

                if (
                    props.config.app.useAlb.enabled &&
                    ((!props.config.app.useAlb.usePublicSubnet &&
                        this.isolatedSubnets.length < 2) ||
                        (props.config.app.useAlb.usePublicSubnet && this.publicSubnets.length < 2))
                ) {
                    throw new Error(
                        "Existing VPC and provided subnets must have at least 2 public or private subnets already setup when specifying the use of a ALB!"
                    );
                }
            }
        } else {
            /**
             * Subnets
             */
            const subnetIsolatedConfig: ec2.SubnetConfiguration = {
                name: "isolated-subnet",
                subnetType: ec2.SubnetType.PRIVATE_ISOLATED,
                cidrMask: 23, // 510 usable IPs
            };

            const subnetPrivateConfig: ec2.SubnetConfiguration = {
                name: "private-subnet",
                subnetType: ec2.SubnetType.PRIVATE_WITH_EGRESS,
                cidrMask: 26, // 62 usable IPs
            };

            const subnetPublicConfig: ec2.SubnetConfiguration = {
                name: "public-subnet",
                subnetType: ec2.SubnetType.PUBLIC,
                cidrMask: 26, // 62 usable IPs
            };

            /**
             * VPC
             */
            const vpcLogsGroups = new LogGroup(this, "CloudWatchVAMSVpc", {
                logGroupName:
                    "/aws/vendedlogs/VAMSCloudWatchVPCLogs" +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "VAMSCloudWatchVPCLogs",
                        10
                    ),
                retention: RetentionDays.TEN_YEARS,
                removalPolicy: cdk.RemovalPolicy.DESTROY,
            });

            const subnetConfigurations: ec2.SubnetConfiguration[] = [subnetIsolatedConfig];

            if (
                (props.config.app.useAlb.enabled && props.config.app.useAlb.usePublicSubnet) ||
                props.config.app.pipelines.useRapidPipeline.enabled ||
                props.config.app.pipelines.useModelOps.enabled ||
                props.config.app.pipelines.useSplatToolbox.enabled
            ) {
                subnetConfigurations.push(subnetPublicConfig);
                subnetConfigurations.push(subnetPrivateConfig);
            }

            // const cidrRange = "10.0.0.0/16"; // 4096

            this.vpc = new ec2.Vpc(this, "Vpc", {
                ipAddresses: ec2.IpAddresses.cidr(props.config.app.useGlobalVpc.vpcCidrRange),
                subnetConfiguration: subnetConfigurations,
                maxAzs: this.azCount,
                enableDnsHostnames: true,
                enableDnsSupport: true,
                flowLogs: {
                    "vpc-logs": {
                        destination: ec2.FlowLogDestination.toCloudWatchLogs(vpcLogsGroups),
                        trafficType: ec2.FlowLogTrafficType.ALL,
                    },
                },
            });

            //Get subnets from created VPC (one per AZ per subnet type)
            const azIsolatedUsed: string[] = [];
            const azPrivateUsed: string[] = [];
            const azPublicUsed: string[] = [];

            this.vpc.isolatedSubnets.forEach((element) => {
                if (azIsolatedUsed.indexOf(element.availabilityZone) == -1) {
                    azIsolatedUsed.push(element.availabilityZone);
                    this.isolatedSubnets.push(element);
                }
            });

            this.vpc.privateSubnets.forEach((element) => {
                if (azPrivateUsed.indexOf(element.availabilityZone) == -1) {
                    azPrivateUsed.push(element.availabilityZone);
                    this.privateSubnets.push(element);
                }
            });

            this.vpc.publicSubnets.forEach((element) => {
                if (azPublicUsed.indexOf(element.availabilityZone) == -1) {
                    azPublicUsed.push(element.availabilityZone);
                    this.publicSubnets.push(element);
                }
            });
        }

        /**
         * Security Groups
         */
        const vpceSecurityGroup = new ec2.SecurityGroup(this, "VPCeSecurityGroup", {
            vpc: this.vpc,
            allowAllOutbound: true,
            description: "VPC Endpoints Security Group",
        });

        this.vpceSecurityGroup = vpceSecurityGroup;

        // add ingress rules for most service to service oriented communications
        vpceSecurityGroup.addIngressRule(
            ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
            ec2.Port.tcp(443),
            "Allow HTTPS Access"
        );
        vpceSecurityGroup.addIngressRule(
            ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
            ec2.Port.tcp(53),
            "Allow TCP for ECR Access"
        );
        vpceSecurityGroup.addIngressRule(
            ec2.Peer.ipv4(this.vpc.vpcCidrBlock),
            ec2.Port.udp(53),
            "Allow UDP for ECR Access"
        );

        /**
         * VPC Endpoints
         */

        //Add VPC endpoints based on configuration options
        //Note: This is mostly to not duplicate endpoints if bringing in an external VPC that already has the needed endpoints for the services
        //Note: More switching is done to avoid creating endpoints when not needed (mostly for cost)
        //Note: Don't add any end points if we are just loading context

        if (
            props.config.app.useGlobalVpc.addVpcEndpoints &&
            !props.config.env.loadContextIgnoreVPCStacks
        ) {
            ///Common endpoints needed for VAMS
            // Create VPC endpoint for API Gateway
            new ec2.InterfaceVpcEndpoint(this, "APIGatewayEndpoint", {
                vpc: this.vpc,
                privateDnsEnabled: true,
                service: ec2.InterfaceVpcEndpointAwsService.APIGATEWAY,
                subnets: { subnets: this.isolatedSubnets },
                securityGroups: [vpceSecurityGroup],
            });

            // Create VPC endpoint for SSM
            new ec2.InterfaceVpcEndpoint(this, "SSMEndpoint", {
                vpc: this.vpc,
                privateDnsEnabled: true,
                service: ec2.InterfaceVpcEndpointAwsService.SSM,
                subnets: { subnets: this.isolatedSubnets },
                securityGroups: [vpceSecurityGroup],
            });

            // Create VPC endpoint for Lambda
            new ec2.InterfaceVpcEndpoint(this, "LambdaEndpoint", {
                vpc: this.vpc,
                privateDnsEnabled: true,
                service: ec2.InterfaceVpcEndpointAwsService.LAMBDA,
                subnets: { subnets: this.isolatedSubnets },
                securityGroups: [vpceSecurityGroup],
            });

            // Create VPC endpoint for STS
            new ec2.InterfaceVpcEndpoint(this, "STSEndpoint", {
                vpc: this.vpc,
                privateDnsEnabled: true,
                service: ec2.InterfaceVpcEndpointAwsService.STS,
                subnets: { subnets: this.isolatedSubnets },
                securityGroups: [vpceSecurityGroup],
            });

            // Create VPC endpoint for CloudWatch Logs
            new ec2.InterfaceVpcEndpoint(this, "CloudWatchEndpoint", {
                vpc: this.vpc,
                privateDnsEnabled: true,
                service: ec2.InterfaceVpcEndpointAwsService.CLOUDWATCH_LOGS,
                subnets: { subnets: this.isolatedSubnets },
                securityGroups: [vpceSecurityGroup],
            });

            // Create VPC endpoint for SFN
            new ec2.InterfaceVpcEndpoint(this, "SFNEndpoint", {
                vpc: this.vpc,
                privateDnsEnabled: true,
                service: ec2.InterfaceVpcEndpointAwsService.STEP_FUNCTIONS,
                subnets: { subnets: this.isolatedSubnets },
                securityGroups: [vpceSecurityGroup],
            });

            // Create VPC endpoint for SNS
            new ec2.InterfaceVpcEndpoint(this, "SNSEndpoint", {
                vpc: this.vpc,
                privateDnsEnabled: true,
                service: ec2.InterfaceVpcEndpointAwsService.SNS,
                subnets: { subnets: this.isolatedSubnets },
                securityGroups: [vpceSecurityGroup],
            });

            // Create VPC endpoint for SQS
            new ec2.InterfaceVpcEndpoint(this, "SQSEndpoint", {
                vpc: this.vpc,
                privateDnsEnabled: true,
                service: ec2.InterfaceVpcEndpointAwsService.SQS,
                subnets: { subnets: this.isolatedSubnets },
                securityGroups: [vpceSecurityGroup],
            });

            //Add endpoint for cognito IDP
            if (props.config.app.authProvider.useCognito.enabled) {
                //Currently not suppored as a VPC endpoint
            }

            //Add for all endpoints if using KMS
            if (props.config.app.useKmsCmkEncryption.enabled) {
                // Create VPC endpoint for KMS
                new ec2.InterfaceVpcEndpoint(this, "KMSEndpoint", {
                    vpc: this.vpc,
                    privateDnsEnabled: true,
                    service: ec2.InterfaceVpcEndpointAwsService.KMS,
                    subnets: { subnets: this.isolatedSubnets },
                    securityGroups: [vpceSecurityGroup],
                });

                //Add KMS FIPS endpoints if we are using FIPS
                if (props.config.app.useFips) {
                    // Create VPC endpoint for KMS FIPS
                    new ec2.InterfaceVpcEndpoint(this, "KMSEndpoint_FIPS", {
                        vpc: this.vpc,
                        privateDnsEnabled: true,
                        service: ec2.InterfaceVpcEndpointAwsService.KMS_FIPS,
                        subnets: { subnets: this.isolatedSubnets },
                        securityGroups: [vpceSecurityGroup],
                    });
                }
            }

            //Pipeline-Only Required Endpoints
            if (
                props.config.app.pipelines.usePreviewPcPotreeViewer.enabled ||
                props.config.app.pipelines.useGenAiMetadata3dLabeling.enabled ||
                props.config.app.pipelines.useRapidPipeline.enabled ||
                props.config.app.pipelines.useModelOps.enabled ||
                props.config.app.pipelines.useSplatToolbox.enabled
            ) {
                // Create VPC endpoint for Batch
                new ec2.InterfaceVpcEndpoint(this, "BatchEndpoint", {
                    vpc: this.vpc,
                    privateDnsEnabled: true,
                    service: ec2.InterfaceVpcEndpointAwsService.BATCH,
                    subnets: { subnets: this.isolatedSubnets },
                    securityGroups: [vpceSecurityGroup],
                });

                // Create VPC endpoint for ECR API
                new ec2.InterfaceVpcEndpoint(this, "ECRAPIEndpoint", {
                    vpc: this.vpc,
                    privateDnsEnabled: true, // Needed for Fargate<->ECR
                    service: ec2.InterfaceVpcEndpointAwsService.ECR,
                    subnets: { subnets: this.isolatedSubnets },
                    securityGroups: [vpceSecurityGroup],
                });

                // Create VPC endpoint for ECR Docker API
                new ec2.InterfaceVpcEndpoint(this, "ECRDockerEndpoint", {
                    vpc: this.vpc,
                    privateDnsEnabled: true, // Needed for Fargate<->ECR
                    service: ec2.InterfaceVpcEndpointAwsService.ECR_DOCKER,
                    subnets: { subnets: this.isolatedSubnets },
                    securityGroups: [vpceSecurityGroup],
                });
            }

            //All Lambda and Metadata Generation Pipeline Required Endpoints
            if (
                props.config.app.useGlobalVpc.useForAllLambdas &&
                props.config.app.pipelines.useGenAiMetadata3dLabeling.enabled
            ) {
                // Create VPC endpoint for Bedrock Runtime
                new ec2.InterfaceVpcEndpoint(this, "BedrockEndpoint", {
                    vpc: this.vpc,
                    privateDnsEnabled: true,
                    service: ec2.InterfaceVpcEndpointAwsService.BEDROCK_RUNTIME,
                    subnets: { subnets: this.isolatedSubnets },
                    securityGroups: [vpceSecurityGroup],
                });

                // Create VPC endpoint for Rekognition
                new ec2.InterfaceVpcEndpoint(this, "RekognitionEndpoint", {
                    vpc: this.vpc,
                    privateDnsEnabled: true,
                    service: ec2.InterfaceVpcEndpointAwsService.REKOGNITION,
                    subnets: { subnets: this.isolatedSubnets },
                    securityGroups: [vpceSecurityGroup],
                });
            }

            // AWS Marketplace Pipeline Required Endpoint on Private Subnet (not isolated)
            if (
                props.config.app.pipelines.useModelOps.enabled ||
                props.config.app.pipelines.useRapidPipeline.enabled ||
                props.config.app.pipelines.useSplatToolbox.enabled
            ) {
                // Create VPC endpoint for ECS
                new ec2.InterfaceVpcEndpoint(this, "ECSEndpoint", {
                    vpc: this.vpc,
                    privateDnsEnabled: true,
                    service: ec2.InterfaceVpcEndpointAwsService.ECS,
                    subnets: { subnets: this.privateSubnets },
                    securityGroups: [vpceSecurityGroup],
                });
            }

            //Add Global Gateway Endpoints (no cost so we add for everything)
            //Note due to outstanding bugs, won't be able to create Gateway Endpoints when importing a VPC (https://github.com/aws/aws-cdk/issues/22025, https://github.com/aws/aws-cdk/issues/3472)
            this.vpc.addGatewayEndpoint("S3Endpoint", {
                service: ec2.GatewayVpcEndpointAwsService.S3,
                subnets: [{ subnets: this.isolatedSubnets }],
            });

            this.vpc.addGatewayEndpoint("DynamoEndpoint", {
                service: ec2.GatewayVpcEndpointAwsService.DYNAMODB,
                subnets: [{ subnets: this.isolatedSubnets }],
            });
        }

        //Nag Supressions
        NagSuppressions.addResourceSuppressions(vpceSecurityGroup, [
            {
                id: "AwsSolutions-EC23",
                reason: "VPCe Security Group is restricted to VPC cidr range on ports 443 and 53",
            },
            {
                id: "CdkNagValidationFailure",
                reason: "Validation failure due to inherent nature of CDK Nag Validations of CIDR ranges", //https://github.com/cdklabs/cdk-nag/issues/817
            },
        ]);

        /**
         * Outputs
         */
        new CfnOutput(this, "VPCId", {
            value: this.vpc.vpcId,
        });
    }
}
