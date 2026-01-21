/* eslint-disable @typescript-eslint/no-unused-vars */
/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { Construct } from "constructs";
import { NestedStack } from "aws-cdk-lib";
import * as cdk from "aws-cdk-lib";
import * as Config from "../../../config/config";
import { samlSettings } from "../../../config/saml-config";
import * as ssm from "aws-cdk-lib/aws-ssm";
import { storageResources } from "../storage/storageBuilder-nestedStack";
import { CloudFrontS3WebSiteConstruct } from "./constructs/cloudfront-s3-website-construct";
import { GatewayAlbDeployConstruct } from "./constructs/gateway-albDeploy-construct";
import { AlbS3WebsiteAlbDeployConstruct } from "./constructs/alb-s3-website-albDeploy-construct";
import { CustomCognitoConfigConstruct } from "./constructs/custom-cognito-config-construct";
import { addBehaviorToCloudFrontDistribution } from "./constructs/cloudfront-s3-website-construct";
import { generateContentSecurityPolicy } from "../../helper/security";
import { NagSuppressions } from "cdk-nag";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { requireTLSAndAdditionalPolicyAddToResourcePolicy } from "../../helper/security";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as iam from "aws-cdk-lib/aws-iam";
import { Service } from "../../helper/service-helper";
import { authResources } from "../auth/authBuilder-nestedStack";

export interface StaticWebBuilderNestedStackProps extends cdk.StackProps {
    config: Config.Config;
    webAppBuildPath: string;
    apiUrl: string;
    storageResources: storageResources;
    ssmWafArn: string;
    authResources: authResources;
    vpc: ec2.IVpc;
    subnetsIsolated: ec2.ISubnet[];
    subnetsPublic: ec2.ISubnet[];
}

/**
 * Default input properties
 */
const defaultProps: Partial<StaticWebBuilderNestedStackProps> = {
    //stackName: "",
    //env: {},
};

export class StaticWebBuilderNestedStack extends NestedStack {
    public endpointURL: string;
    public albEndpoint: string;
    public webAppBucket: s3.IBucket;

    constructor(parent: Construct, name: string, props: StaticWebBuilderNestedStackProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        //Create S3 WebApp bucket.
        //Note: This is done outside of storageBuilder due to circular dependencies to update bucket policy
        ////
        //Note: (Cloudfront)
        // When using Distribution, do not set the s3 bucket website documents
        // if these are set then the distribution origin is configured for HTTP communication with the
        // s3 bucket and won't configure the cloudformation correctly.
        //Note: (ALB)
        //Setup S3 WebApp Distro bucket (public website contents) with the name that matches the deployed domain hostname (in order to work with the ALB/Endpoint)
        //Bucket name must match final domain name for the ALB/VPCEndpoint architecture to work as ALB does not support host/path rewriting
        //Bucket cannot have CMK encryption on it using ALB -> VPCEndpoint without using a NGINX reverse proxy
        const s3DefaultProps: Partial<s3.BucketProps> = {
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            versioned: true,
            objectOwnership: s3.ObjectOwnership.OBJECT_WRITER,
        };

        const webAppAccessLogsBucket = new s3.Bucket(this, "WebAppAccessLogsBucket", {
            ...s3DefaultProps,
            bucketName: props.config.app.useAlb.enabled
                ? props.config.app.useAlb.domainHost + "-webappaccesslogs"
                : undefined,
            encryption:
                props.config.app.useKmsCmkEncryption.enabled && !props.config.app.useAlb.enabled //ALB doesn't support encryption logs bucket encrypted with KMS
                    ? s3.BucketEncryption.KMS
                    : s3.BucketEncryption.S3_MANAGED,
            encryptionKey:
                props.config.app.useKmsCmkEncryption.enabled && !props.config.app.useAlb.enabled //ALB doesn't support encryption logs bucket encrypted with KMS
                    ? props.storageResources.encryption.kmsKey
                    : undefined,
            bucketKeyEnabled:
                props.config.app.useKmsCmkEncryption.enabled && !props.config.app.useAlb.enabled //ALB doesn't support encryption logs bucket encrypted with KMS
                    ? true
                    : false,
            lifecycleRules: [
                {
                    enabled: true,
                    expiration: cdk.Duration.days(30),
                    noncurrentVersionExpiration: cdk.Duration.days(30),
                },
            ],
        });
        requireTLSAndAdditionalPolicyAddToResourcePolicy(webAppAccessLogsBucket, props.config);

        const webAppBucket = new s3.Bucket(this, "WebAppBucket", {
            ...s3DefaultProps,
            bucketName: props.config.app.useAlb.enabled
                ? props.config.app.useAlb.domainHost
                : undefined,
            cors: [
                {
                    allowedOrigins: ["*"],
                    allowedHeaders: ["*"],
                    allowedMethods: [
                        s3.HttpMethods.GET,
                        s3.HttpMethods.PUT,
                        s3.HttpMethods.POST,
                        s3.HttpMethods.HEAD,
                    ],
                    exposedHeaders: ["ETag"],
                },
            ],
            // websiteIndexDocument: "index.html",
            // websiteErrorDocument: "index.html",
            encryption: s3.BucketEncryption.S3_MANAGED,
            //TODO: Figure out encryption for both ALB and Cloudfront OAC with KMS
            // encryption:
            //     props.config.app.useKmsCmkEncryption.enabled && !props.config.app.useAlb.enabled
            //         ? s3.BucketEncryption.KMS
            //         : s3.BucketEncryption.S3_MANAGED,
            // encryptionKey:
            //     props.config.app.useKmsCmkEncryption.enabled && !props.config.app.useAlb.enabled
            //         ? props.storageResources.encryption.kmsKey
            //         : undefined,
            // bucketKeyEnabled:
            //     props.config.app.useKmsCmkEncryption.enabled && !props.config.app.useAlb.enabled
            //         ? true
            //         : false,
            objectOwnership: s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
            autoDeleteObjects: true,
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            serverAccessLogsBucket: webAppAccessLogsBucket,
            serverAccessLogsPrefix: "web-app-access-log-S3bucket-logs/",
        });
        requireTLSAndAdditionalPolicyAddToResourcePolicy(webAppBucket, props.config);

        //Set public variable to bucket
        this.webAppBucket = webAppBucket;

        //Generate CSP
        //Generate Auth Domain and Global CSP policy
        let authDomain = "";

        if (props.config.app.authProvider.useCognito.useSaml) {
            authDomain = `https://${samlSettings.cognitoDomainPrefix}.auth.${props.config.env.region}.amazoncognito.com`;
        } else if (props.config.app.authProvider.useExternalOAuthIdp.enabled) {
            authDomain = props.config.app.authProvider.useExternalOAuthIdp.idpAuthProviderUrl;
        }

        const cspPolicy = generateContentSecurityPolicy(
            props.storageResources,
            authDomain,
            props.apiUrl,
            props.config
        );

        //Deploy website distribution infrastructure and authentication tie-ins
        //Cloudfront deployment
        if (props.config.app.useCloudFront.enabled) {
            //Deploy through CloudFront (default)
            const website = new CloudFrontS3WebSiteConstruct(this, "WebApp", {
                ...props,
                config: props.config,
                storageResources: props.storageResources,
                webAppBucket,
                webAppAccessLogsBucket,
                webSiteBuildPath: props.webAppBuildPath,
                webAcl: props.ssmWafArn,
                apiUrl: props.apiUrl,
                csp: cspPolicy,
                cognitoDomain: props.config.app.authProvider.useCognito.useSaml
                    ? `https://${samlSettings.cognitoDomainPrefix}.auth.${props.config.env.region}.amazoncognito.com`
                    : "",
            });

            // Bind API Gateway to /api route of cloudfront
            addBehaviorToCloudFrontDistribution(this, website.cloudFrontDistribution, props.apiUrl);

            //Cloudfront Bucket Access
            webAppBucket.addToResourcePolicy(
                new cdk.aws_iam.PolicyStatement({
                    effect: iam.Effect.ALLOW,
                    actions: ["s3:GetObject"],
                    principals: [Service("CLOUDFRONT").Principal],
                    resources: [webAppBucket.arnForObjects("*")],
                    // conditions: {
                    //     StringEquals: {
                    //         "AWS:SourceArn": this.formatArn({
                    //             service: "cloudfront",
                    //             account: props.config.env.account,
                    //             region: props.config.env.region,
                    //             partition: props.config.env.partition,
                    //             resource: "distribution",
                    //             resourceName: website.cloudFrontDistribution.distributionId,
                    //             arnFormat: cdk.ArnFormat.SLASH_RESOURCE_NAME,
                    //         }),
                    //     },
                    // },
                })
            );

            /**
             * When using federated identities, this list of callback urls must include
             * the set of names that VAMSAuth.tsx will resolve when it calls
             * window.location.origin for the redirectSignIn and redirectSignout callback urls.
             */
            const callbackUrls = [
                "http://localhost:3000",
                "http://localhost:3000/",
                `https://${website.cloudFrontDistribution.domainName}/`,
                `https://${website.cloudFrontDistribution.domainName}`,
            ];

            /**
             * Propagate Base CloudFront URL to Cognito User Pool Callback and Logout URLs
             * if SAML is enabled.
             */
            if (props.config.app.authProvider.useCognito.useSaml) {
                const customCognitoWebClientConfig = new CustomCognitoConfigConstruct(
                    this,
                    "CustomCognitoWebClientConfig",
                    {
                        name: "Web",
                        clientId: props.authResources.cognito.webClientId,
                        userPoolId: props.authResources.cognito.userPoolId,
                        callbackUrls: callbackUrls,
                        logoutUrls: callbackUrls,
                        identityProviders: ["COGNITO", samlSettings.name],
                    }
                );
                customCognitoWebClientConfig.node.addDependency(website);
            }

            this.endpointURL = website.endPointURL;

            // Store Web URL in SSM Parameter Store
            const webUrlSSMParameter = new ssm.StringParameter(this, "WebUrlDeploymentParamSSM", {
                parameterName: props.config.webUrlDeploymentSSMParam,
                stringValue: website.endPointURL,
                description: "Web URL for Cloudfront Deployment of VAMS",
                tier: ssm.ParameterTier.STANDARD,
            });

            // Add dependency to ensure website is created
            webUrlSSMParameter.node.addDependency(website);
        }

        //ALB deploy
        if (props.config.app.useAlb.enabled) {
            //TBD: Implement third ALB option to use a middleware NGINX reverse proxy with a EC2. https://www.nginx.com/blog/using-nginx-as-object-storage-gateway/
            //Pro: Reduces need to name S3 bucket the same as the domain name for the ALB/VPCEndpoint architecture to work as ALB does not support host/path rewriting. Would also allow for VAMS A/B deployments with ALB again.
            //Pro: Supports S3 KMS CMK encryption as NGINX can do SigV4 signing
            //Con: Would not be entirely serverless and would require management of an EC2 instance. Avoid ECS/Fargate deployment due to all customers not being able to use those services in all partitions.

            //Deploy with ALB (aka, use ALB->VPCEndpoint->S3 as path for web deployment)
            const webAppDistroNetwork = new GatewayAlbDeployConstruct(this, "WebAppDistroNetwork", {
                ...props,
                vpc: props.vpc,
                subnetsIsolated: props.subnetsIsolated,
                subnetsPublic: props.subnetsPublic,
            });

            const website = new AlbS3WebsiteAlbDeployConstruct(this, "WebApp", {
                ...props,
                config: props.config,
                storageResources: props.storageResources,
                webAppBucket,
                webAppAccessLogsBucket,
                webSiteBuildPath: props.webAppBuildPath,
                webAcl: props.ssmWafArn,
                apiUrl: props.apiUrl,
                csp: cspPolicy,
                vpc: webAppDistroNetwork.vpc,
                albSubnets: webAppDistroNetwork.subnets.webApp,
                albSecurityGroup: webAppDistroNetwork.securityGroups.webAppALB,
                vpceSecurityGroup: webAppDistroNetwork.securityGroups.webAppVPCE,
            });

            //ALB Bucket Access
            const webAppBucketPolicy = new iam.PolicyStatement({
                actions: ["s3:Get*", "s3:List*"],
                principals: [new iam.AnyPrincipal()],
                resources: [webAppBucket.arnForObjects("*"), webAppBucket.bucketArn],
            });

            //Restrict to just the VPCe (if enabled)
            //Note: If not adding VPCe at deployment, add condition restriction to blank VPCe (that get's filled in afterwards as part of manual steps)
            let vpcEndpointId = "";
            if (props.config.app.useAlb.addAlbS3SpecialVpcEndpoint) {
                vpcEndpointId = website.s3VpcEndpoint.vpcEndpointId;
            }
            webAppBucketPolicy.addCondition("StringEquals", {
                "aws:SourceVpce": vpcEndpointId,
            });

            webAppBucket.addToResourcePolicy(webAppBucketPolicy);

            /**
             * When using federated identities, this list of callback urls must include
             * the set of names that VAMSAuth.tsx will resolve when it calls
             * window.location.origin for the redirectSignIn and redirectSignout callback urls.
             */
            const callbackUrls = [
                "http://localhost:3000",
                "http://localhost:3000/",
                `${website.endPointURL}`,
                `${website.endPointURL}/`,
            ];

            /**
             * Propagate Base CloudFront URL to Cognito User Pool Callback and Logout URLs
             * if SAML is enabled.
             */
            if (props.config.app.authProvider.useCognito.useSaml) {
                const customCognitoWebClientConfig = new CustomCognitoConfigConstruct(
                    this,
                    "CustomCognitoWebClientConfig",
                    {
                        name: "Web",
                        clientId: props.authResources.cognito.webClientId,
                        userPoolId: props.authResources.cognito.userPoolId,
                        callbackUrls: callbackUrls,
                        logoutUrls: callbackUrls,
                        identityProviders: ["COGNITO", samlSettings.name],
                    }
                );
                customCognitoWebClientConfig.node.addDependency(website);
            }

            // Store Web URL in SSM Parameter Store
            const webUrlSSMParameter = new ssm.StringParameter(this, "WebUrlDeploymentParamSSM", {
                parameterName: props.config.webUrlDeploymentSSMParam,
                stringValue: website.endPointURL,
                description: "Web URL for Cloudfront Deployment of VAMS",
                tier: ssm.ParameterTier.STANDARD,
            });

            // Add dependency to ensure website is created
            webUrlSSMParameter.node.addDependency(website);

            this.endpointURL = website.endPointURL;
            this.albEndpoint = website.albEndpoint;
        }

        //Nag supressions
        const reason =
            "The custom resource CDK bucket deployment needs full access to the bucket to deploy web static files";
        NagSuppressions.addResourceSuppressions(
            this,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            regex: "/Action::s3:.*/g",
                        },
                    ],
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: reason,
                    appliesTo: [
                        {
                            // https://github.com/cdklabs/cdk-nag#suppressing-a-rule
                            regex: "/^Resource::.*/g",
                        },
                    ],
                },
            ],
            true
        );
    }
}
