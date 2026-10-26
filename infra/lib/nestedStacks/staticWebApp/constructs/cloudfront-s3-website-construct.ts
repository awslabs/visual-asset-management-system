/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cloudfront from "aws-cdk-lib/aws-cloudfront";
import * as cloudfrontOrigins from "aws-cdk-lib/aws-cloudfront-origins";
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
import { deployWebAppBundle } from "../../../helper/webAppDeployment";

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
                // Gzip and Brotli add the normalized Accept-Encoding to the cache key, which `compress`
                // needs before CloudFront compresses anything. The web bundle is static public content
                // and reflects no request input, so compressing it exposes nothing.
                cachePolicy: new cloudfront.CachePolicy(this, "CachePolicy", {
                    defaultTtl: cdk.Duration.hours(1),
                    enableAcceptEncodingGzip: true,
                    enableAcceptEncodingBrotli: true,
                }),
                allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD_OPTIONS,
                viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
            },

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

        // Uploads the bundle and, once every object is in place, invalidates `/*` on this distribution.
        deployWebAppBundle(this, {
            webSiteBuildPath: props.webSiteBuildPath,
            webAppBucket: props.webAppBucket,
            distribution: cloudFrontDistribution,
        });

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
 * Adds the proxy routes from CloudFront `/api/*` to the API Gateway REST API.
 *
 * The anonymous bootstrap routes are registered under the stage's own `/api/` prefix, so each of
 * those exact paths is forwarded with the stage as the origin path (`<cf>/api/version` reaches stage
 * route `/api/version`). Every other `/api/*` path is forwarded without an origin path, so
 * `<cf>/api/{route}` reaches stage `api`, route `/{route}` — the same URL shape as the execute-api
 * invoke URL. The exact behaviors come first because CloudFront matches behaviors in order.
 *
 * The catch-all uses the managed CachingDisabled and AllViewerExceptHostHeader policies: every
 * header, cookie and query string except Host reaches the API, and nothing is cached. Managed policies
 * do not count against the account's custom cache policy quota.
 *
 * Make sure APIs return the header `"Cache-Control" = "no-cache, no-store"` or CloudFront will cache
 * the bootstrap responses.
 *
 * `originCustomHeaders` (CloudFront deployments with an origin verification secret) is added to both
 * origins. CloudFront sets an origin custom header on every request it forwards, replacing a viewer
 * header of the same name; origin custom headers are not a CloudFront policy and use no policy quota.
 */
export function addBehaviorToCloudFrontDistribution(
    scope: Construct,
    cloudFrontDistribution: cloudfront.Distribution,
    apiUrl: string,
    apiStageName: string,
    apiGatewayTimeoutSeconds: number,
    originCustomHeaders?: Record<string, string>
) {
    const stagePrefixedOrigin = new cloudfrontOrigins.HttpOrigin(apiUrl, {
        originSslProtocols: [cloudfront.OriginSslPolicy.TLS_V1_2],
        protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
        originPath: `/${apiStageName}`,
        customHeaders: originCustomHeaders,
    });
    const stagePrefixedCachePolicy = new cloudfront.CachePolicy(scope, "ApiCachePolicy", {
        // required or CloudFront will strip the Authorization token from the request.
        // must be in the cache policy
        headerBehavior: cloudfront.CacheHeaderBehavior.allowList("Authorization"),
        enableAcceptEncodingGzip: true,
    });
    const stagePrefixedOriginRequestPolicy = new cloudfront.OriginRequestPolicy(
        scope,
        "ApiOriginRequestPolicy",
        {
            // required or CloudFront will strip all query strings off the request
            queryStringBehavior: cloudfront.OriginRequestQueryStringBehavior.all(),
        }
    );
    for (const routePath of Config.API_STAGE_PREFIXED_ROUTE_PATHS) {
        cloudFrontDistribution.addBehavior(routePath, stagePrefixedOrigin, {
            cachePolicy: stagePrefixedCachePolicy,
            originRequestPolicy: stagePrefixedOriginRequestPolicy,
            allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
            viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        });
    }

    // The origin response timeout is one second above the integration timeout, so the REST API answers a
    // timed-out integration before CloudFront does, capped at 60 seconds (CloudFront's default quota).
    cloudFrontDistribution.addBehavior(
        "/api/*",
        new cloudfrontOrigins.HttpOrigin(apiUrl, {
            originSslProtocols: [cloudfront.OriginSslPolicy.TLS_V1_2],
            protocolPolicy: cloudfront.OriginProtocolPolicy.HTTPS_ONLY,
            readTimeout: Duration.seconds(Math.min(apiGatewayTimeoutSeconds + 1, 60)),
            customHeaders: originCustomHeaders,
        }),
        {
            cachePolicy: cloudfront.CachePolicy.CACHING_DISABLED,
            originRequestPolicy: cloudfront.OriginRequestPolicy.ALL_VIEWER_EXCEPT_HOST_HEADER,
            allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
            viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        }
    );
}
