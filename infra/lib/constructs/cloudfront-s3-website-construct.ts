/**
 * Copyright 2021 Amazon.com, Inc. and its affiliates. All Rights Reserved.
 *
 * Licensed under the Amazon Software License (the "License").
 * You may not use this file except in compliance with the License.
 * A copy of the License is located at
 *
 *   http://aws.amazon.com/asl/
 *
 * or in the "license" file accompanying this file. This file is distributed
 * on an "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either
 * express or implied. See the License for the specific language governing
 * permissions and limitations under the License.
 */

import * as cloudfront from "@aws-cdk/aws-cloudfront";
import * as cloudfrontOrigins from "@aws-cdk/aws-cloudfront-origins";
import * as s3 from "@aws-cdk/aws-s3";
import { BlockPublicAccess } from "@aws-cdk/aws-s3";
import * as s3deployment from "@aws-cdk/aws-s3-deployment";
import * as cdk from "@aws-cdk/core";
import { Duration } from "@aws-cdk/core";

export interface CloudFrontS3WebSiteConstructProps extends cdk.StackProps {
  /**
   * The path to the build directory of the web site, relative to the project root
   * ex: "./app/build"
   */
  webSiteBuildPath: string;
  webAcl: string;
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
 * - S3 bucket
 * - CloudFrontDistribution
 * - OriginAccessIdentity
 *
 * On redeployment, will automatically invalidate the CloudFront distribution cache
 */
export class CloudFrontS3WebSiteConstruct extends cdk.Construct {
  /**
   * The origin access identity used to access the S3 website
   */
  public originAccessIdentity: cloudfront.OriginAccessIdentity;

  /**
   * The cloud front distribution to attach additional behaviors like `/api`
   */
  public cloudFrontDistribution: cloudfront.Distribution;

  constructor(
    parent: cdk.Construct,
    name: string,
    props: CloudFrontS3WebSiteConstructProps
  ) {
    super(parent, name);

    props = { ...defaultProps, ...props };

    const accessLogsBucket = new s3.Bucket(this, "AccessLogsBucket", {
      encryption: s3.BucketEncryption.KMS_MANAGED,
      serverAccessLogsPrefix: "web-app-access-log-bucket-logs/",
      versioned: true,
    });

    accessLogsBucket.addLifecycleRule({
      enabled: true,
      expiration: Duration.days(3650),
    });

    // When using Distribution, do not set the s3 bucket website documents
    // if these are set then the distribution origin is configured for HTTP communication with the
    // s3 bucket and won't configure the cloudformation correctly.
    const siteBucket = new s3.Bucket(this, "WebApp", {
      // websiteIndexDocument: "index.html",
      // websiteErrorDocument: "index.html",
      encryption: s3.BucketEncryption.S3_MANAGED,
      autoDeleteObjects: true,
      removalPolicy: cdk.RemovalPolicy.DESTROY,
      serverAccessLogsBucket: accessLogsBucket,
      serverAccessLogsPrefix: "web-app-access-log-bucket-logs/",
      blockPublicAccess: BlockPublicAccess.BLOCK_ALL
    });

    const originAccessIdentity = new cloudfront.OriginAccessIdentity(
      this,
      "OriginAccessIdentity"
    );
    siteBucket.grantRead(originAccessIdentity);

    const s3origin = new cloudfrontOrigins.S3Origin(siteBucket, {
      originAccessIdentity: originAccessIdentity,
    });

    let cloudFrontDistribution = new cloudfront.Distribution(
      this,
      "WebAppDistribution",
      {
        defaultBehavior: {
          origin: s3origin,
          cachePolicy: new cloudfront.CachePolicy(this, "CachePolicy", {
            defaultTtl: cdk.Duration.hours(1),
          }),
          allowedMethods: cloudfront.AllowedMethods.ALLOW_ALL,
          viewerProtocolPolicy:
            cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
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
        webAclId: props.webAcl,
        minimumProtocolVersion: cloudfront.SecurityPolicyProtocol.TLS_V1_2_2018, // Required by security
      }
    );

    new s3deployment.BucketDeployment(this, "DeployWithInvalidation", {
      sources: [s3deployment.Source.asset(props.webSiteBuildPath)],
      destinationBucket: siteBucket,
      distribution: cloudFrontDistribution, // this assignment, on redeploy, will automatically invalidate the cloudfront cache
      distributionPaths: ["/*"],
      memoryLimit: 1024,
    });

    // export any cf outputs
    new cdk.CfnOutput(this, "SiteBucket", { value: siteBucket.bucketName });
    new cdk.CfnOutput(this, "CloudFrontDistributionId", {
      value: cloudFrontDistribution.distributionId,
    });
    new cdk.CfnOutput(this, "CloudFrontDistributionDomainName", {
      value: cloudFrontDistribution.distributionDomainName,
    });

    // assign public properties
    this.originAccessIdentity = originAccessIdentity;
    this.cloudFrontDistribution = cloudFrontDistribution;
  }
}
