/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as s3deployment from "aws-cdk-lib/aws-s3-deployment";
import * as cdk from "aws-cdk-lib";
import { Duration, NestedStack } from "aws-cdk-lib";
import { Construct } from "constructs";
import * as iam from "aws-cdk-lib/aws-iam";
import {
    requireTLSAndAdditionalPolicyAddToResourcePolicy,
    suppressCdkNagLambda,
} from "../../../helper/security";
import { aws_wafv2 as wafv2 } from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as elbv2 from "aws-cdk-lib/aws-elasticloadbalancingv2";
import * as elbv2_targets from "aws-cdk-lib/aws-elasticloadbalancingv2-targets";
import customResources = require("aws-cdk-lib/custom-resources");
import * as route53 from "aws-cdk-lib/aws-route53";
import * as acm from "aws-cdk-lib/aws-certificatemanager";
import * as route53targets from "aws-cdk-lib/aws-route53-targets";
import * as Config from "../../../../config/config";
import { Partition } from "../../../helper/service-helper";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import { NagSuppressions } from "cdk-nag";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as lambda from "aws-cdk-lib/aws-lambda";
import * as path from "path";
import { LAMBDA_PYTHON_RUNTIME } from "../../../../config/config";

// The /tmp budget of the CDK BucketDeployment Lambda that uploads the built web bundle. That
// function downloads the bundle archive into /tmp and expands it there, holding the archive and the
// extracted tree at once — roughly twice the size of web/dist, which is ~255 MB (about half of it
// viewer plugins) and so already near the 512 MiB Lambda default. 4 GiB carries several times the
// current bundle and needs raising only once web/dist grows past ~2 GB (10 GiB is the Lambda
// maximum); ephemeral storage is billed per GB-second of the function's own runtime, so one upload
// per deployment costs a fraction of a cent. The CloudFront path deploys the same bundle — keep
// this equal to the constant in cloudfront-s3-website-construct.ts.
const WEB_DEPLOYMENT_EPHEMERAL_STORAGE = cdk.Size.gibibytes(4);

// Content-Type for the bundle's `.mjs` files, which the main deployment cannot set.
//
// `BucketDeployment` applies `contentType` to EVERY object in that deployment, so the extension can
// only be handled by a deployment of its own. Left to the default, the uploader derives the type from
// the extension using Python's `mimetypes`, which has no `.mjs` entry and falls back to `text/plain`.
// Browsers enforce strict MIME checking on module scripts and the bucket also sends
// `X-Content-Type-Options: nosniff`, so such a file is refused with "Failed to load module script"
// however valid its contents.
//
// This path is affected exactly as the CloudFront one is: the ALB forwards to an S3 VPC endpoint
// rather than rewriting anything, so the object's stored Content-Type is what reaches the browser.
// Keep this equal to the constant in cloudfront-s3-website-construct.ts, where the full reasoning is
// recorded.
const ES_MODULE_CONTENT_TYPE = "text/javascript";

// Elastic Load Balancing caps a listener attribute value at 1 KB ("The value for the attribute can
// not exceed 1K bytes in size"), and the whole Content-Security-Policy is delivered as one such
// attribute. CloudFront's response-headers policy allows 1783 bytes, so this ceiling is reached only
// on the ALB path — which is the only distribution available in a restricted partition and the one
// with no test environment. Checked at synth so an over-long policy names itself here rather than
// failing the deploy or arriving truncated.
const ALB_LISTENER_ATTRIBUTE_MAX_BYTES = 1024;

// Warn from 90% so the margin is visible before it runs out. One additional SHA-256 hash source costs
// roughly 52 bytes with quoting.
const ALB_CSP_WARN_BYTES = Math.floor(ALB_LISTENER_ATTRIBUTE_MAX_BYTES * 0.9);

/**
 * The byte length the load balancer will receive, which is not the length of the string held here.
 *
 * The policy carries unresolved CDK tokens at synthesis — the REST API id, referenced across a nested
 * stack boundary — and a token stringifies to text far longer than the value it becomes: the reference
 * name alone runs past 100 characters where the deployed id is about ten. Measuring the raw string
 * would therefore report roughly 130 bytes more than ALB ever sees, and could fail a deployment whose
 * real policy fits. Each token is replaced by a representative resolved width, matching how
 * `infra/test/t1Distribution.test.ts` models the same figure.
 */
const RESOLVED_TOKEN_WIDTH = 10;

function deployedCspBytes(csp: string): number {
    return Buffer.byteLength(csp.replace(/\$\{[^}]*\}/g, "x".repeat(RESOLVED_TOKEN_WIDTH)), "utf8");
}

/**
 * Security response headers the ALB path sets, matched to the CloudFront path's
 * `securityHeadersBehavior` so neither distribution is the weaker one.
 *
 * These are the three the ALB supports as listener attributes. `X-XSS-Protection` has no ALB
 * attribute and is a no-op in current browsers, and the `Cross-Origin-Embedder-Policy`,
 * `Cross-Origin-Opener-Policy`, `Referrer-Policy` and `Permissions-Policy` headers the CloudFront
 * path adds have no ALB equivalent either — the
 * ALB emits only its documented `routing.http.response.*` set, not arbitrary headers. That gap is a
 * platform limitation rather than an omission here; it costs cross-origin isolation
 * (`SharedArrayBuffer`) for viewers that rely on it.
 */
const ALB_SECURITY_HEADERS: Record<string, string> = {
    // Two years with subdomains, the same value the CloudFront response-headers policy declares.
    "routing.http.response.strict_transport_security.header_value": `max-age=${
        60 * 60 * 24 * 365 * 2
    }; includeSubDomains`,
    "routing.http.response.x_content_type_options.header_value": "nosniff",
    // SAMEORIGIN rather than DENY for the reason recorded in cloudfront-s3-website-construct.ts: the
    // iframe-embedded viewers frame VAMS's own same-origin pages. Keep in sync with the CSP
    // "frame-ancestors 'self'" directive generated in security.ts.
    "routing.http.response.x_frame_options.header_value": "SAMEORIGIN",
};

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
    apiStageName: string;
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
            // Minimum TLS version + cipher suite for the web front. The ELB default policy still
            // negotiates TLS 1.0 and TLS 1.1; ELBSecurityPolicy-TLS13-1-2-2021-06 (the CDK
            // `RECOMMENDED_TLS` value) raises the floor to TLS 1.2 while still accepting TLS 1.3.
            //
            // A named policy is asserted only in the commercial partition, because ELB does not
            // publish the same set of policy names everywhere and an unknown name is rejected at
            // deploy time. The gate is the literal partition rather than the `app.govCloud`
            // restricted-partition flag, which is operator-set and unvalidated against
            // `env.partition` — it can be false while deploying into a restricted partition. Left
            // unset elsewhere, which keeps the listener on whatever default its partition applies.
            sslPolicy: Partition() === "aws" ? elbv2.SslPolicy.RECOMMENDED_TLS : undefined,
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
                // The group that carries the ingress rules added above, not the load balancer's own.
                // The two ingress rules exist to scope this endpoint to the ALB, and attaching a
                // different group left them governing nothing while the endpoint admitted whatever the
                // ALB group allows — and `vpceSecurityGroup` was attached to no resource at all, so it
                // read as an active restriction while being inert.
                securityGroups: [props.vpceSecurityGroup],
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

            suppressCdkNagLambda(getVpcEndpointIpsFunction);

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
            const cspBytes = deployedCspBytes(props.csp);
            if (cspBytes > ALB_LISTENER_ATTRIBUTE_MAX_BYTES) {
                throw new Error(
                    `Configuration Error: the generated Content-Security-Policy is ${cspBytes} bytes, ` +
                        `which exceeds the ${ALB_LISTENER_ATTRIBUTE_MAX_BYTES}-byte limit on an Application ` +
                        `Load Balancer listener attribute. The ALB web distribution cannot carry it. ` +
                        `Reduce the policy by trimming cspAdditionalConfig.json entries or the inline ` +
                        `script hashes, or serve the web front through CloudFront, whose response-headers ` +
                        `policy allows a longer value.`
                );
            }
            if (cspBytes > ALB_CSP_WARN_BYTES) {
                cdk.Annotations.of(this).addWarning(
                    `The generated Content-Security-Policy is ${cspBytes} bytes of the ` +
                        `${ALB_LISTENER_ATTRIBUTE_MAX_BYTES} an ALB listener attribute allows ` +
                        `(${ALB_LISTENER_ATTRIBUTE_MAX_BYTES - cspBytes} bytes remaining). Each ` +
                        `additional inline-script hash costs about 52 bytes.`
                );
            }
            listener.setAttribute(
                "routing.http.response.content_security_policy.header_value",
                props.csp
            );
        }

        // Transport and content-type hardening, matched to the CloudFront path.
        for (const [attribute, value] of Object.entries(ALB_SECURITY_HEADERS)) {
            listener.setAttribute(attribute, value);
        }

        // The ALB's own `Server: awselb/2.0` response header, which names the infrastructure to any
        // caller and is of no use to the application.
        listener.setAttribute("routing.http.response.server.enabled", "false");

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
                    path: `/${props.apiStageName}/#{path}`,
                    // Must be a temporary (302) redirect: the target is the API Gateway hostname, which
                    // is regenerated whenever the API is replaced. Browsers cache 301s indefinitely (no
                    // Cache-Control is set here), so a permanent redirect leaves returning users pointing
                    // at the previous deployment's hostname, which no longer resolves — the app then fails
                    // at startup with "Failed to fetch" and only a manual cache clear recovers it.
                    permanent: false,
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
                    path: `/${props.apiStageName}/#{path}`,
                    // Temporary (302) for the same reason as the /api* rule above.
                    permanent: false,
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

        //Deploy website to Bucket
        const mainDeployment = new s3deployment.BucketDeployment(this, "DeployWithInvalidation", {
            sources: [s3deployment.Source.asset(props.webSiteBuildPath)],
            destinationBucket: props.webAppBucket,
            memoryLimit: Config.LAMBDA_MEMORY_SIZE,
            ephemeralStorageSize: WEB_DEPLOYMENT_EPHEMERAL_STORAGE,
            exclude: ["*.mjs"],
        });

        // The two deployments write disjoint key sets and the only one that prunes excludes `*.mjs`,
        // which per BucketDeployment's `exclude` contract also protects those objects from its
        // `--delete` pass — so neither can remove the other's files whichever order they run in.
        // Ordering them anyway keeps that outcome from depending on that subtlety, and stops two
        // Lambdas expanding the same multi-hundred-MB bundle concurrently. Mirrors the CloudFront
        // construct, where the order additionally has to precede the `/*` invalidation.
        mainDeployment.node.addDependency(esModuleDeployment);

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
