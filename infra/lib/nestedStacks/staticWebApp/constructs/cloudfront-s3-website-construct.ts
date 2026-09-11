/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as cloudfrontOrigins from "aws-cdk-lib/aws-cloudfront-origins";
import * as s3deployment from "aws-cdk-lib/aws-s3-deployment";
import * as cdk from "aws-cdk-lib";
import { Duration } from "aws-cdk-lib";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../config/config";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { Service } from "../../../helper/service-helper";
import * as s3 from "aws-cdk-lib/aws-s3";
import { generateUniqueNameHash } from "../../../helper/security";
import * as route53 from "aws-cdk-lib/aws-route53";
import * as route53targets from "aws-cdk-lib/aws-route53-targets";
import * as acm from "aws-cdk-lib/aws-certificatemanager";

// The /tmp budget of the CDK BucketDeployment Lambda that uploads the built web bundle. That
// function downloads the bundle archive into /tmp and expands it there, holding the archive and the
// extracted tree at once — roughly twice the size of web/dist, which is ~255 MB (about half of it
// viewer plugins) and so already near the 512 MiB Lambda default. 4 GiB carries several times the
// current bundle and needs raising only once web/dist grows past ~2 GB (10 GiB is the Lambda
// maximum); ephemeral storage is billed per GB-second of the function's own runtime, so one upload
// per deployment costs a fraction of a cent. The ALB path deploys the same bundle — keep this equal
// to the constant in alb-s3-website-albDeploy-construct.ts.
const WEB_DEPLOYMENT_EPHEMERAL_STORAGE = cdk.Size.gibibytes(4);

// Content-Type for the bundle's `.mjs` files, which the main deployment cannot set.
//
// `BucketDeployment` applies `contentType` to EVERY object in that deployment, so the extension can
// only be handled by a deployment of its own. Left to the default, the uploader derives the type
// from the extension using Python's `mimetypes`, which has no `.mjs` entry and falls back to
// `text/plain`. Browsers enforce strict MIME checking on module scripts and the bucket also sends
// `X-Content-Type-Options: nosniff`, so such a file is refused with "Failed to load module script"
// however valid its contents. That took out the PDF viewer, whose pdf.js worker is emitted as
// `pdf.worker.min-<hash>.mjs`: it fell back to a fake worker and then failed the document load.
//
// The mis-typed object is only visible when the key is NEW. `aws s3 sync` compares size and time, so
// the viewer plugins' stable `.mjs` paths kept the correct type from an earlier upload while the
// content-hashed worker — a fresh key on every build — got the wrong one. A bucket can therefore hold
// both, which makes this look viewer-specific when it is not.
//
// `text/javascript` matches the objects already stored correctly and is the type the HTML spec now
// prescribes for JavaScript. This applies to the ALB path too: there the ALB forwards to an S3 VPC
// endpoint, so the object's stored Content-Type is what reaches the browser either way — keep this
// equal to the constant in alb-s3-website-albDeploy-construct.ts.
const ES_MODULE_CONTENT_TYPE = "text/javascript";

export interface CloudFrontS3WebSiteConstructProps extends cdk.StackProps {
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
    cognitoDomain: string;
}

/**
 * Default input properties
 */
const defaultProps: Partial<CloudFrontS3WebSiteConstructProps> = {
    stackName: "",
    env: {},
};

/**
 * Deploys a static website to s3 with a cloud front distribution.
 * Creates:
 * - CloudFrontDistribution
 *
 * On redeployment, will automatically invalidate the CloudFront distribution cache
 */
export class CloudFrontS3WebSiteConstruct extends Construct {
    /**
     * The cloud front distribution to attach additional behaviors like `/api`
     */
    public cloudFrontDistribution: cloudfront.Distribution;

    public endPointURL: string;

    constructor(parent: Construct, name: string, props: CloudFrontS3WebSiteConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        //Note: Implementation: https://github.com/awslabs/aws-solutions-constructs/issues/831
        const originAccessControl = new cdk.aws_cloudfront.CfnOriginAccessControl(
            this,
            "WebAppCloudFrontOac",
            {
                originAccessControlConfig: {
                    name:
                        "WebAppS3CfOAC" +
                        generateUniqueNameHash(
                            props.config.env.coreStackName,
                            props.config.env.account,
                            "WebAppCloudFrontOac",
                            10
                        ),
                    originAccessControlOriginType: "s3",
                    signingBehavior: "always",
                    signingProtocol: "sigv4",
                },
            }
        );

        const responseHeadersPolicy = new cloudfront.ResponseHeadersPolicy(
            this,
            "ResponseHeadersPolicy",
            {
                securityHeadersBehavior: {
                    strictTransportSecurity: {
                        accessControlMaxAge: Duration.days(365 * 2),
                        includeSubdomains: true,
                        override: true,
                    },
                    xssProtection: {
                        override: true,
                        protection: true,
                        modeBlock: true,
                    },
                    // SAMEORIGIN (not DENY) so VAMS can frame its own same-origin
                    // pages, which iframe-embedded viewers require (e.g. the SuperSplat
                    // editor served under /viewers/supersplat/). External sites still
                    // cannot frame VAMS. Keep in sync with the CSP "frame-ancestors 'self'"
                    // directive generated in security.ts.
                    frameOptions: {
                        frameOption: cloudfront.HeadersFrameOption.SAMEORIGIN,
                        override: true,
                    },
                    contentTypeOptions: {
                        override: true,
                    },
                    contentSecurityPolicy: {
                        contentSecurityPolicy: props.csp,
                        override: true,
                    },
                    // Send the origin but not the path on a cross-origin request, and nothing at
                    // all when downgrading to HTTP.
                    referrerPolicy: {
                        referrerPolicy:
                            cloudfront.HeadersReferrerPolicy.STRICT_ORIGIN_WHEN_CROSS_ORIGIN,
                        override: true,
                    },
                },
                customHeadersBehavior: {
                    customHeaders: [
                        {
                            header: "Cross-Origin-Embedder-Policy",
                            value: "credentialless",
                            override: true,
                        },
                        {
                            header: "Cross-Origin-Opener-Policy",
                            value: "same-origin",
                            override: true,
                        },
                        // Denies the hardware and payment APIs no VAMS page or bundled viewer uses.
                        //
                        // Deliberately NOT restricted: fullscreen and xr-spatial-tracking (the
                        // three.js VRButton/ARButton, Babylon and PlayCanvas viewers enter WebXR),
                        // geolocation (the Potree map builds on OpenLayers, which reads it), the
                        // motion sensors (DeviceOrientation drives viewer camera control), and camera
                        // (immersive-ar sessions need it). Denying any of those breaks a viewer in a
                        // way that surfaces only when that viewer is opened.
                        {
                            header: "Permissions-Policy",
                            value:
                                "microphone=(), payment=(), usb=(), serial=(), bluetooth=(), " +
                                "hid=(), midi=(), idle-detection=()",
                            override: true,
                        },
                    ],
                },
            }
        );

        // Configure custom domain if enabled
        let certificate: acm.ICertificate | undefined;
        let domainNames: string[] | undefined;

        if (props.config.app.useCloudFront.customDomain.enabled) {
            // Import ACM certificate (must be in us-east-1 for CloudFront)
            certificate = acm.Certificate.fromCertificateArn(
                this,
                "CloudFrontCertificate",
                props.config.app.useCloudFront.customDomain.certificateArn
            );

            // Set custom domain name
            domainNames = [props.config.app.useCloudFront.customDomain.domainHost];
        }

        const cloudFrontDistribution = new cloudfront.Distribution(this, "WebAppDistribution", {
            defaultBehavior: {
                compress: true,
                responseHeadersPolicy: responseHeadersPolicy,
                origin: new cdk.aws_cloudfront_origins.OriginGroup({
                    primaryOrigin:
                        cdk.aws_cloudfront_origins.S3BucketOrigin.withOriginAccessControl(
                            props.webAppBucket
                        ),
                    fallbackOrigin:
                        cdk.aws_cloudfront_origins.S3BucketOrigin.withOriginAccessControl(
                            props.webAppBucket
                        ),
                }),
                cachePolicy: new cloudfront.CachePolicy(this, "CachePolicy", {
                    defaultTtl: cdk.Duration.hours(1),
                }),
                allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            },

            errorResponses: [
                {
                    httpStatus: 404,
                    ttl: cdk.Duration.hours(0),
                    responseHttpStatus: 200,
                    responsePagePath: "/index.html",
                },
            ],
            defaultRootObject: "index.html",
            webAclId: props.webAcl != "" ? props.webAcl : undefined,
            minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2021, // Required by security
            enableLogging: true,
            logBucket: props.webAppAccessLogsBucket,
            logFilePrefix: "cloudfront-access-logs/",
            // Custom domain configuration (optional)
            certificate: certificate,
            domainNames: domainNames,
        });

        // Attach the OriginAccessControl to the CloudFront Distribution and remove any OriginAccessIdentity
        const l1CloudFrontDistribution = cloudFrontDistribution.node
            .defaultChild as cdk.aws_cloudfront.CfnDistribution;
        l1CloudFrontDistribution.addPropertyOverride(
            "DistributionConfig.Origins.0.OriginAccessControlId",
            originAccessControl.getAtt("Id")
        );
        l1CloudFrontDistribution.addPropertyOverride(
            "DistributionConfig.Origins.0.S3OriginConfig.OriginAccessIdentity",
            ""
        );

        // ES module workers, uploaded on their own so their Content-Type can be declared. See
        // ES_MODULE_CONTENT_TYPE for why this cannot be folded into the main deployment.
        const esModuleDeployment = new s3deployment.BucketDeployment(this, "DeployEsModules", {
            sources: [s3deployment.Source.asset(props.webSiteBuildPath)],
            destinationBucket: props.webAppBucket,
            contentType: ES_MODULE_CONTENT_TYPE,
            exclude: ["*"],
            include: ["*.mjs"],
            prune: false,
            memoryLimit: Config.LAMBDA_MEMORY_SIZE,
            ephemeralStorageSize: WEB_DEPLOYMENT_EPHEMERAL_STORAGE,
        });

        const mainDeployment = new s3deployment.BucketDeployment(this, "DeployWithInvalidation", {
            sources: [s3deployment.Source.asset(props.webSiteBuildPath)],
            destinationBucket: props.webAppBucket,
            distribution: cloudFrontDistribution, // this assignment, on redeploy, will automatically invalidate the cloudfront cache
            distributionPaths: ["/*"],
            memoryLimit: Config.LAMBDA_MEMORY_SIZE,
            ephemeralStorageSize: WEB_DEPLOYMENT_EPHEMERAL_STORAGE,
            exclude: ["*.mjs"],
        });

        // Ordered so the ES modules are in place before this deployment invalidates `/*`; otherwise a
        // stable `.mjs` path (the viewer plugins ship several) could be uploaded after the
        // invalidation and stay cached at its previous version.
        mainDeployment.node.addDependency(esModuleDeployment);

        // Optional: Add Route53 alias if custom domain is enabled and hosted zone ID is provided
        if (
            props.config.app.useCloudFront.customDomain.enabled &&
            props.config.app.useCloudFront.customDomain.optionalHostedZoneId &&
            props.config.app.useCloudFront.customDomain.optionalHostedZoneId != "" &&
            props.config.app.useCloudFront.customDomain.optionalHostedZoneId != "UNDEFINED"
        ) {
            // Extract zone name from domain host (e.g., "vams.example.com" -> "example.com")
            const domainHost = props.config.app.useCloudFront.customDomain.domainHost;
            const zoneName = domainHost.substring(domainHost.indexOf(".") + 1, domainHost.length);

            const zone = route53.HostedZone.fromHostedZoneAttributes(this, "CloudFrontHostedZone", {
                zoneName: zoneName,
                hostedZoneId: props.config.app.useCloudFront.customDomain.optionalHostedZoneId,
            });

            // Create Route53 A record alias pointing to CloudFront distribution
            new route53.ARecord(this, "CloudFrontAliasRecord", {
                zone: zone,
                recordName: `${domainHost}.`,
                target: route53.RecordTarget.fromAlias(
                    new route53targets.CloudFrontTarget(cloudFrontDistribution)
                ),
            });
        }

        //Nag supressions
        NagSuppressions.addResourceSuppressions(
            cloudFrontDistribution,
            [
                {
                    id: "AwsSolutions-CFR4",
                    reason: "Custom domain support is now available through configuration. When custom domain is disabled, CloudFront uses the default certificate. When enabled, customers must provide their own ACM certificate in us-east-1.",
                },
            ],
            true
        );

        // export any cf outputs
        new cdk.CfnOutput(this, "WebAppBucket", {
            value: props.webAppBucket.bucketName,
        });
        new cdk.CfnOutput(this, "CloudFrontDistributionId", {
            value: cloudFrontDistribution.distributionId,
        });
        new cdk.CfnOutput(this, "CloudFrontDistributionDomainName", {
            value: cloudFrontDistribution.distributionDomainName,
        });

        new cdk.CfnOutput(this, "CloudFrontDistributionUrl", {
            value: `https://${cloudFrontDistribution.distributionDomainName}`,
        });

        // Add custom domain URL output if enabled
        if (props.config.app.useCloudFront.customDomain.enabled) {
            new cdk.CfnOutput(this, "CloudFrontCustomDomainUrl", {
                value: `https://${props.config.app.useCloudFront.customDomain.domainHost}`,
                description: "Custom domain URL for CloudFront distribution",
            });
        }

        // assign public properties
        this.cloudFrontDistribution = cloudFrontDistribution;
        // Use custom domain if enabled, otherwise use CloudFront domain
        this.endPointURL = props.config.app.useCloudFront.customDomain.enabled
            ? `https://${props.config.app.useCloudFront.customDomain.domainHost}`
            : `https://${cloudFrontDistribution.distributionDomainName}`;
    }
}

/**
 * Adds a proxy route from CloudFront /api to the api gateway url
 *
 * Deploys Api gateway (proxied through a CloudFront distribution at route `/api` if deploying through cloudfront)
 *
 * Any Api's attached to the gateway should be located at `/api/*` so that requests are correctly proxied.
 * Make sure Api's return the header `"Cache-Control" = "no-cache, no-store"` or CloudFront will cache responses
 *
 */
export function addBehaviorToCloudFrontDistribution(
    scope: Construct,
    cloudFrontDistribution: cloudfront.Distribution,
    apiUrl: string,
    apiStageName: string
) {
    // Add general behavior for all other /api/* routes (excluding /api/amplify-config)
    cloudFrontDistribution.addBehavior(
        "/api/*",
        new cloudfrontOrigins.HttpOrigin(apiUrl, {
            originSslProtocols: [cloudfront.OriginSslPolicy.TLS_V1_2],
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            originPath: `/${apiStageName}`,
        }),
        {
            cachePolicy: new cloudfront.CachePolicy(scope, "ApiCachePolicy", {
                // required or CloudFront will strip the Authorization token from the request.
                // must be in the cache policy
                headerBehavior: cloudfront.CacheHeaderBehavior.allowList("Authorization"),
                enableAcceptEncodingGzip: true,
            }),
            originRequestPolicy: new cloudfront.OriginRequestPolicy(
                scope,
                "ApiOriginRequestPolicy",
                {
                    // required or CloudFront will strip all query strings off the request
                    queryStringBehavior: cloudfront.OriginRequestQueryStringBehavior.all(),
                }
            ),
            allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
            viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        }
    );
}
