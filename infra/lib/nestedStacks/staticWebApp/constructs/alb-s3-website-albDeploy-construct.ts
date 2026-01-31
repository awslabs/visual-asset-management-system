/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as s3deployment from "aws-cdk-lib/aws-s3-deployment";
import * as cdk from "aws-cdk-lib";
import { Duration, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as iam from "aws-cdk-lib/aws-iam";
import { requireTLSAndAdditionalPolicyAddToResourcePolicy } from "../../../helper/security";
import { aws_wafv2 as wafv2 } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as elbv2_targets from "aws-cdk-lib/aws-elasticloadbalancingv2-targets";
import customResources = require("aws-cdk-lib/custom-resources");
import * as route53 from "aws-cdk-lib/aws-route53";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as route53targets from "aws-cdk-lib/aws-route53-targets";
import * as Config from "../../../../config/config";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { NagSuppressions } from "cdk-nag";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../config/config";

export interface AlbS3WebsiteAlbDeployConstructProps extends cdk.StackProps {
    /**
     * The path to the build directory of the web site, relative to the project root
     * ex: "./app/build"
     */
    config: Config.Config;
    storageResources: storageResources;
    webAppBucket: s3.Bucket;
    webAppAccessLogsBucket: s3.Bucket;
    webSiteBuildPath: string;
    webAcl: string;
    apiUrl: string;
    csp: string;
    vpc: ec2.IVpc;
    albSubnets: ec2.ISubnet[];
    albSecurityGroup: ec2.SecurityGroup;
    vpceSecurityGroup: ec2.SecurityGroup;
}

/**
 * Default input properties
 */
const defaultProps: Partial<AlbS3WebsiteAlbDeployConstructProps> = {
    stackName: "",
    env: {},
};

/**
 * Deploys a static website to s3 with a ALB distribution for GovCloud deployments.
 * Creates:
 * - S3 bucket
 * - ALB
 *
 */
export class AlbS3WebsiteAlbDeployConstruct extends Construct {
    /**
     * Returns the ALB URL instance for the static webpage
     */
    public endPointURL: string;
    public albEndpoint: string;

    readonly s3VpcEndpoint: ec2.InterfaceVpcEndpoint;

    constructor(parent: Construct, name: string, props: AlbS3WebsiteAlbDeployConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        //Use provided ACM certificate
        const acmDomainCertificate = acm.Certificate.fromCertificateArn(
            this,
            "DomainCertificateImported",
            props.config.app.useAlb.certificateArn
        );

        // Create an ALB
        const alb = new elbv2.ApplicationLoadBalancer(this, "WebAppDistroALB", {
            loadBalancerName: `${
                props.config.name + "-core-" + props.config.app.baseStackName
            }-WebAppALB`.substring(0, 32),
            internetFacing: props.config.app.useAlb.usePublicSubnet,
            vpc: props.vpc,
            vpcSubnets: { subnets: props.albSubnets },
            securityGroup: props.albSecurityGroup,
            deletionProtection: false,
        });

        //Add access logging on ALB
        alb.logAccessLogs(props.webAppAccessLogsBucket, "web-app-access-log-alb-logs");

        // Add a listener to the ALB
        const listener = alb.addListener("WebAppDistroALBListener", {
            port: 443, // The port on which the ALB listens
            certificates: [acmDomainCertificate], // The certificate to use for the listener
        });

        //Setup target group to point to Special S3 VPC Endpoint Interface
        const targetGroup1 = new elbv2.ApplicationTargetGroup(this, "WebAppALBTargetGroup", {
            port: 443,
            vpc: props.vpc,
            targetType: elbv2.TargetType.IP,
            healthCheck: {
                enabled: true,
                healthyHttpCodes: "200,307,405", //These are the health codes we will see returned from VPCEndpointInterface<->S3
            },
        });

        //Add ingress rules (HTTP/HTTPS) to VPC Endpoint security group
        props.vpceSecurityGroup.connections.allowFrom(alb, ec2.Port.tcp(443));
        props.vpceSecurityGroup.connections.allowFrom(alb, ec2.Port.tcp(80));

        //Create the VPCe if enabled
        //NOTE: Only time we should disable this is for stack deployments where the VPCe needs to be created outside of the stack manually
        if (props.config.app.useAlb.addAlbS3SpecialVpcEndpoint) {
            // Create VPC interface endpoint for S3 (Needed for ALB<->S3)
            //Note: This endpoint should be created despite the GlobalVPC flag of create endpoint or not in order to setup ALB listeners properly
            const s3VPCEndpoint = new ec2.InterfaceVpcEndpoint(this, "S3InterfaceVPCEndpoint", {
                vpc: props.vpc,
                privateDnsEnabled: false,
                service: ec2.InterfaceVpcEndpointAwsService.S3,
                subnets: { subnets: props.albSubnets },
                securityGroups: [props.albSecurityGroup],
            });

            this.s3VpcEndpoint = s3VPCEndpoint;

            //TODO: Figure out why this policy is not working and still letting requests through for other bucket names (use ALB dns name to test)
            //TODO?: Specifically add a deny policy for anything outside of bucket
            //Add policy to VPC endpoint to only allow access to the specific S3 Bucket
            s3VPCEndpoint.addToPolicy(
                new iam.PolicyStatement({
                    resources: [
                        props.webAppBucket.arnForObjects("*"),
                        props.webAppBucket.bucketArn,
                    ],
                    actions: ["s3:Get*", "s3:List*"],
                    principals: [new iam.AnyPrincipal()],
                })
            );

            //Create Lambda-backed custom resource to get unique VPC Endpoint IPs
            //This prevents duplicate target errors and avoids CloudFormation response size limits
            //https://repost.aws/questions/QUjISNyk6aTA6jZgZQwKWf4Q/how-to-connect-a-load-balancer-and-an-interface-vpc-endpoint-together-using-cdk

            // Create Lambda function for custom resource
            const getVpcEndpointIpsFunction = new lambda.Function(
                this,
                "GetVpcEndpointIpsFunction",
                {
                    runtime: LAMBDA_PYTHON_RUNTIME,
                    handler: "getVpcEndpointIps.lambda_handler",
                    code: lambda.Code.fromAsset(
                        path.join(__dirname, "../../../../../backend/backend/customResources")
                    ),
                    timeout: Duration.minutes(2),
                    memorySize: 256,
                }
            );

            // Grant permissions to describe network interfaces
            getVpcEndpointIpsFunction.addToRolePolicy(
                new iam.PolicyStatement({
                    actions: ["ec2:DescribeNetworkInterfaces"],
                    resources: ["*"],
                })
            );

            // Create custom resource provider
            const getVpcEndpointIpsProvider = new customResources.Provider(
                this,
                "GetVpcEndpointIpsProvider",
                {
                    onEventHandler: getVpcEndpointIpsFunction,
                }
            );

            // Create custom resource
            const getVpcEndpointIps = new cdk.CustomResource(this, "GetVpcEndpointIps", {
                serviceToken: getVpcEndpointIpsProvider.serviceToken,
                properties: {
                    NetworkInterfaceIds: s3VPCEndpoint.vpcEndpointNetworkInterfaceIds,
                },
            });

            // Get the comma-separated list of unique IPs
            const ipAddressesList = getVpcEndpointIps.getAttString("IpAddresses");

            // Split and add each IP as a target
            // Note: We use Fn.split to handle the comma-separated list at deployment time
            const ipAddresses = cdk.Fn.split(",", ipAddressesList);

            // Add each IP as a target using a loop
            // CloudFormation will resolve the actual IPs at deployment time
            for (let i = 0; i < props.albSubnets.length; i++) {
                const ipAddress = cdk.Fn.select(i, ipAddresses);
                targetGroup1.addTarget(new elbv2_targets.IpTarget(ipAddress));
            }
        }

        // Add target group to listener after all targets are added
        listener.addTargetGroups("WebAppTargetGroup1", {
            targetGroups: [targetGroup1],
        });

        //If CSP not empty, add it to the header
        if (props.csp !== "") {
            listener.setAttribute(
                "routing.http.response.content_security_policy.header_value",
                props.csp
            );
        }

        //Setup listener rule to rewrite path to forward to API Gateway for backend API calls
        const applicationListenerRuleBackendAPI = new elbv2.ApplicationListenerRule(
            this,
            "WebAppnListenerRuleBackendAPI",
            {
                listener: listener,
                priority: 1,
                action: elbv2.ListenerAction.redirect({
                    host: `${props.apiUrl}`,
                    port: "443",
                    protocol: "HTTPS",
                    permanent: true,
                }),
                conditions: [elbv2.ListenerCondition.pathPatterns(["/api*"])],
            }
        );

        //Setup listener rule to rewrite path to forward to API Gateway for backend API calls
        const applicationListenerRuleBackendSecureConfig = new elbv2.ApplicationListenerRule(
            this,
            "WebAppnListenerRuleBackendSecureConfig",
            {
                listener: listener,
                priority: 2,
                action: elbv2.ListenerAction.redirect({
                    host: `${props.apiUrl}`,
                    port: "443",
                    protocol: "HTTPS",
                    permanent: true,
                }),
                conditions: [elbv2.ListenerCondition.pathPatterns(["/secure-config*"])],
            }
        );

        //Setup listener rule to forward index.html to S3
        const applicationListenerRuleBackendIndex = new elbv2.ApplicationListenerRule(
            this,
            "WebAppnListenerRuleBackendIndex",
            {
                listener: listener,
                priority: 3,
                targetGroups: [targetGroup1],
                conditions: [elbv2.ListenerCondition.pathPatterns(["/index.html*"])],
            }
        );

        //Setup listener rule to forward individual file requests to S3
        const applicationListenerRuleBackendIndividualFile = new elbv2.ApplicationListenerRule(
            this,
            "WebAppnListenerRuleBackendIndividualFile",
            {
                listener: listener,
                priority: 4,
                targetGroups: [targetGroup1],
                conditions: [elbv2.ListenerCondition.pathPatterns(["*/*.*"])],
            }
        );

        //Setup listener rule to rewrite path to forward to index.html for a no path route
        const applicationListenerRuleBaseRoute = new elbv2.ApplicationListenerRule(
            this,
            "WebAppnListenerRuleBaseRoute",
            {
                listener: listener,
                priority: 5,
                action: elbv2.ListenerAction.redirect({
                    path: "/#{path}index.html",
                    permanent: false,
                }),
                conditions: [elbv2.ListenerCondition.pathPatterns(["*/"])],
            }
        );

        //Setup listener rule to rewrite path to forward to index.html for any other (no file) path route
        const applicationListenerRuleOtherRoute = new elbv2.ApplicationListenerRule(
            this,
            "WebAppnListenerRuleOtherRoute",
            {
                listener: listener,
                priority: 6,
                action: elbv2.ListenerAction.redirect({
                    path: "/index.html",
                    permanent: false,
                }),
                conditions: [elbv2.ListenerCondition.pathPatterns(["/*"])],
            }
        );

        // Enable a ALB redirect from port 80 to 443
        alb.addRedirect();

        // Optional: Add alias to ALB if hosted zone ID provided (must match domain root of provided domain host)
        if (
            props.config.app.useAlb.optionalHostedZoneId &&
            props.config.app.useAlb.optionalHostedZoneId != "" &&
            props.config.app.useAlb.optionalHostedZoneId != "UNDEFINED"
        ) {
            const zone = route53.HostedZone.fromHostedZoneAttributes(
                this,
                "ExistingRoute53HostedZone",
                {
                    zoneName: props.config.app.useAlb.domainHost.substring(
                        props.config.app.useAlb.domainHost.indexOf(".") + 1,
                        props.config.app.useAlb.domainHost.length
                    ),
                    hostedZoneId: props.config.app.useAlb.optionalHostedZoneId,
                }
            );

            // Add a Route 53 alias with the Load Balancer as the target (using sub-domain in provided domain host)
            new route53.ARecord(this, "WebAppALBAliasRecord", {
                zone: zone,
                recordName: `${props.config.app.useAlb.domainHost}.`,
                target: route53.RecordTarget.fromAlias(new route53targets.LoadBalancerTarget(alb)),
            });
        }

        //Associate WAF to ALB
        if (props.webAcl != "") {
            const cfnWebACLAssociation = new wafv2.CfnWebACLAssociation(
                this,
                "WebAppWAFAssociation",
                {
                    resourceArn: alb.loadBalancerArn,
                    webAclArn: props.webAcl,
                }
            );
        }

        //Deploy website to Bucket
        new s3deployment.BucketDeployment(this, "DeployWithInvalidation", {
            sources: [s3deployment.Source.asset(props.webSiteBuildPath)],
            destinationBucket: props.webAppBucket,
            memoryLimit: 1024,
        });

        // assign public properties
        this.endPointURL = `https://${props.config.app.useAlb.domainHost}`;
        this.albEndpoint = alb.loadBalancerDnsName;

        new cdk.CfnOutput(this, "webAppAlbDns", {
            value: alb.loadBalancerDnsName,
        });

        new cdk.CfnOutput(this, "webDistributionUrl", {
            value: this.endPointURL,
        });

        // export any cf outputs
        new cdk.CfnOutput(this, "webAppBucket", {
            value: props.webAppBucket.bucketName,
        });
    }
}
