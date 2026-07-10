/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { IAMArn, Service, Partition } from "../../../helper/service-helper";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput, CustomResource } from "aws-cdk-lib";
import * as cr from "aws-cdk-lib/custom-resources";
import * as path from "path";
import { LAMBDA_NODE_RUNTIME } from "../../../../config/config";
import { Port, SecurityGroup, Vpc } from "aws-cdk-lib/aws-ec2";
import * as Config from "../../../../config/config";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { kmsKeyLambdaPermissionAddToResourcePolicy } from "../../../helper/security";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import * as njslambda from "aws-cdk-lib/aws-lambda-nodejs";

/* eslint-disable @typescript-eslint/no-empty-interface */
export interface OpensearchProvisionedConstructProps {
    storageResources: storageResources;
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    dataNodeInstanceType?: string;
    dataNodesCount?: number;
    masterNodeInstanceType?: string;
    masterNodesCount?: number;
    ebsVolumeSize?: number;
    ebsVolumeType?: cdk.aws_ec2.EbsDeviceVolumeType;
    zoneAwareness?: cdk.aws_opensearchservice.ZoneAwarenessConfig;
    availabilityZoneCount?: number;
    multiAzWithStandbyEnabled?: boolean;
    numberOfShards?: number;
}

const defaultProps: Partial<OpensearchProvisionedConstructProps> = {
    masterNodeInstanceType: "r7g.large.search",
    dataNodeInstanceType: "r7g.large.search",
    masterNodesCount: 3, //Minimum of 3
    //dataNodesCount intentionally not defaulted here: it is derived from availabilityZoneCount in
    //the constructor so it stays valid for the zone-awareness mode (multiple of 3 for Multi-AZ with
    //Standby at 3 AZs, an even count for 2 AZs).
    ebsVolumeSize: 120,
    ebsVolumeType: cdk.aws_ec2.EbsDeviceVolumeType.GENERAL_PURPOSE_SSD_GP3,
    availabilityZoneCount: 2,
};

/*
Deploys an Amazon Opensearch Domain
*/
export class OpensearchProvisionedConstruct extends Construct {
    public aosName: string;
    public domain: cdk.aws_opensearchservice.Domain;
    public domainEndpoint: string;
    config: Config.Config;

    constructor(scope: Construct, name: string, props: OpensearchProvisionedConstructProps) {
        super(scope, name);
        props = { ...defaultProps, ...props };

        this.aosName = name;

        this.config = props.config;

        // The domain runs zone-aware across availabilityZoneCount zones. A 3-AZ domain runs as Multi-AZ
        // with Standby (production posture); a 2-AZ domain runs without Standby. Multi-AZ with Standby
        // requires every index to have copies in a multiple of 3, so it is only enabled at 3 AZs (where
        // 3 data nodes + 2 replicas = 3 copies satisfy that requirement). The default availabilityZoneCount
        // of 2 keeps Standby off, so existing 2-AZ deployments are unchanged.
        const availabilityZoneCount = props.availabilityZoneCount!;
        const multiAzWithStandbyEnabled =
            props.multiAzWithStandbyEnabled ?? availabilityZoneCount === 3;
        // One data node per AZ (2 for a 2-AZ domain, 3 for a 3-AZ domain — a multiple of 3 for Standby).
        const dataNodesCount = props.dataNodesCount ?? availabilityZoneCount;
        // Index copies (primary + replicas) must be a multiple of 3 for Multi-AZ with Standby, so use
        // 2 replicas (3 copies) when Standby is on; otherwise keep 0 replicas. Threaded to the schema-deploy custom resource.
        const numberOfReplicas = multiAzWithStandbyEnabled ? 2 : 0;
        // Primary shard count per index. Defaults to 1. Larger indexes
        // (roughly >60 GB / ~3M records) should increase this; changing it requires re-creating the index.
        const numberOfShards = props.numberOfShards ?? 1;

        //https://github.com/aws-samples/opensearch-vpc-cdk/blob/main/lib/opensearch-vpc-cdk-stack.ts

        // Service-linked role for Amazon OpenSearch Service. The domain cannot be created in a VPC until the
        // "AWSServiceRoleForAmazonOpenSearchService" service-linked role exists in the account.
        //
        // Create it idempotently with a deploy-time custom resource: CreateServiceLinkedRole creates the role
        // when missing and returns InvalidInput ("has been taken in this account") when it already exists, so
        // we ignore InvalidInput to make the call a safe check-or-create. The partition-aware OpenSearch
        // service principal is resolved via the service helper.
        const openSearchServicePrincipal = Service("ES").PrincipalString;
        const serviceLinkedRole = new cr.AwsCustomResource(this, "OpensearchServiceLinkedRole", {
            onCreate: {
                service: "IAM",
                action: "createServiceLinkedRole",
                parameters: {
                    AWSServiceName: openSearchServicePrincipal,
                    Description:
                        "Service-linked role for Amazon OpenSearch Service (created by VAMS)",
                },
                //Stable physical id so the role is not recreated/deleted on update or delete. The
                //service-linked role is account-wide and shared, so VAMS does not manage its lifecycle
                //beyond ensuring it exists.
                physicalResourceId: cr.PhysicalResourceId.of(
                    `vams-opensearch-slr-${openSearchServicePrincipal}`
                ),
                //If the role already exists, CreateServiceLinkedRole returns InvalidInput — treat as success.
                ignoreErrorCodesMatching: "InvalidInput",
            },
            //No onUpdate/onDelete: the shared, account-wide service-linked role must not be deleted when
            //this stack is torn down (other resources may depend on it).
            policy: cr.AwsCustomResourcePolicy.fromStatements([
                new cdk.aws_iam.PolicyStatement({
                    effect: cdk.aws_iam.Effect.ALLOW,
                    actions: ["iam:CreateServiceLinkedRole"],
                    resources: ["*"],
                }),
            ]),
            installLatestAwsSdk: false,
        });

        //Select exactly one subnet per AZ, up to the configured Availability Zone count.
        //Note: OpenSearch domains require the number of subnets to match the zone-aware AZ count,
        //so we bound the selection by availabilityZoneCount (not dataNodesCount) and pick one subnet per AZ.
        const subnets: ec2.ISubnet[] = [];
        const azUsed: string[] = [];

        props.subnets.forEach((element) => {
            if (
                azUsed.indexOf(element.availabilityZone) == -1 &&
                subnets.length < availabilityZoneCount
            ) {
                azUsed.push(element.availabilityZone);
                subnets.push(element);
            }
        });

        //OpenSearch engine version is partition-dependent: the AWS European Sovereign Cloud (aws-eusc) does
        //not yet support OpenSearch 3.x, so it uses OPENSEARCH_VERSION_EUSOVEREIGN (2.x). All other partitions
        //use the standard OPENSEARCH_VERSION (3.x).
        const openSearchVersion =
            Partition() === "aws-eusc"
                ? Config.OPENSEARCH_VERSION_EUSOVEREIGN
                : Config.OPENSEARCH_VERSION;

        const osDomain = new cdk.aws_opensearchservice.Domain(this, "OpenSearchDomain", {
            version: openSearchVersion,

            ebs: {
                enabled: true,
                volumeSize: props.ebsVolumeSize,
                volumeType: props.ebsVolumeType,
            },
            nodeToNodeEncryption: true,
            encryptionAtRest: {
                enabled: true,
                kmsKey: props.config.app.useKmsCmkEncryption.enabled
                    ? props.storageResources.encryption.kmsKey
                    : undefined,
            },
            vpc: props.vpc,
            vpcSubnets: [{ subnets: subnets, onePerAz: true }],
            capacity: {
                dataNodeInstanceType: props.dataNodeInstanceType,
                dataNodes: dataNodesCount,
                masterNodeInstanceType: props.masterNodeInstanceType,
                masterNodes: props.masterNodesCount,
                //Multi-AZ with Standby is enabled only for a 3-AZ domain (it requires 3 AZs and data
                //nodes in multiples of 3); a 2-AZ domain runs zone-aware without standby.
                multiAzWithStandbyEnabled: multiAzWithStandbyEnabled,
            },
            enforceHttps: true,
            zoneAwareness: {
                enabled: true,
                availabilityZoneCount: availabilityZoneCount,
            },
            //Disabled fine grained access control to allow the VPC and domain access policy to restrict to IAM roles
            //fineGrainedAccessControl: {
            //    masterUserArn: props.cognitoAuthenticatedRole,
            //},
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            enableVersionUpgrade: true,
            enableAutoSoftwareUpdate: true,
            logging: {
                //auditLogEnabled: true, //Used only for fine-grained access control
                slowSearchLogEnabled: true,
                appLogEnabled: true,
                slowIndexLogEnabled: true,
            },
        });

        //The domain can only be created in the VPC after the OpenSearch Service service-linked role exists,
        //so order the domain after the check-or-create custom resource.
        osDomain.node.addDependency(serviceLinkedRole);

        this.domain = osDomain;
        this.domainEndpoint = "https://" + osDomain.domainEndpoint;

        const schemaDeploy = new njslambda.NodejsFunction(
            this,
            "OpensearchProvisionedDeploySchema",
            {
                entry: path.join(__dirname, "./schemaDeploy/deployschema.ts"),
                handler: "handler",
                bundling: {
                    externalModules: ["aws-sdk"],
                },
                runtime: LAMBDA_NODE_RUNTIME,
                //A freshly created domain can take several minutes to become reachable. The handler polls with
                //backoff, so allow ample time rather than failing on the first index call.
                timeout: cdk.Duration.minutes(14),
                vpc: props.vpc,
                vpcSubnets: { subnets: props.subnets },
                //Note: This schema deploy resource must run in the VPC in order to communicate with the AOS provisioned running in the VPC.
            }
        );

        kmsKeyLambdaPermissionAddToResourcePolicy(
            schemaDeploy,
            props.storageResources.encryption.kmsKey
        );

        schemaDeploy.addToRolePolicy(
            new cdk.aws_iam.PolicyStatement({
                actions: ["es:*"],
                resources: [this.domain.domainArn, this.domain.domainArn + "/*"],
                effect: cdk.aws_iam.Effect.ALLOW,
            })
        );
        schemaDeploy.addToRolePolicy(
            new cdk.aws_iam.PolicyStatement({
                actions: ["ssm:*"],
                resources: [IAMArn("*" + props.config.name + "*").ssm],
                effect: cdk.aws_iam.Effect.ALLOW,
            })
        );

        this.grantOSDomainAccess(schemaDeploy);

        const schemaDeployProvider = new cr.Provider(
            this,
            "OpensearchProvisionedDeploySchemaProvider",
            {
                onEventHandler: schemaDeploy,
            }
        );

        schemaDeployProvider.node.addDependency(schemaDeploy);
        schemaDeployProvider.node.addDependency(osDomain);

        new CustomResource(this, "DeploySSMIndexSchema", {
            serviceToken: schemaDeployProvider.serviceToken,
            properties: {
                endpointSSMParam: props.config.openSearchDomainEndpointSSMParam,
                assetIndexNameSSMParam: props.config.openSearchAssetIndexNameSSMParam,
                fileIndexNameSSMParam: props.config.openSearchFileIndexNameSSMParam,
                domainEndpoint: "https://" + osDomain.domainEndpoint,
                assetIndexName: props.config.openSearchAssetIndexName,
                fileIndexName: props.config.openSearchFileIndexName,
                //Index copies must be a multiple of 3 for a Multi-AZ-with-Standby (3-AZ) domain.
                numberOfReplicas: numberOfReplicas,
                //Primary shard count per index. Default 1; increase for large indexes.
                numberOfShards: numberOfShards,
                //A provisioned domain is always created in the VPC and reachable by the schema-deploy
                //function, so index creation is never deferred (only private next-gen Serverless can defer).
                deferIndexCreation: "false",
                version: "3",
                Timestamp: Date.now().toString(), //Used to check index deployment every CDK deployment
            },
        });

        /**
         * Outputs
         */
        new CfnOutput(this, "OpenSearchProvisionedDomainEndpoint", {
            value: this.domainEndpoint,
        });

        //NAG Surpressions
        NagSuppressions.addResourceSuppressions(schemaDeployProvider, [
            {
                id: "AwsSolutions-L1",
                reason: "Runtime is managed by the CDK custom-resources framework (cr.Provider) that backs the OpenSearch schema-deploy custom resource; VAMS does not control this provider function's runtime version.",
            },
        ]);

        NagSuppressions.addResourceSuppressions(
            osDomain,
            [
                {
                    id: "AwsSolutions-OS1",
                    reason: "Configured as intended. Provisioned configuration meant primarily for GovCloud deployment that won't be public and restricted to individual lambda roles for access to the domain.",
                },
                {
                    id: "AwsSolutions-OS3",
                    reason: "Configured as intended. Provisioned configuration meant primarily for GovCloud deployment that won't be public and restricted to individual lambda roles for access to the domain.",
                },
                {
                    id: "AwsSolutions-IAM5",
                    reason: "The Domain construct's CDK-generated ESLogGroupPolicy custom resource calls logs:PutResourcePolicy so the domain can write the enabled slow/application CloudWatch logs. PutResourcePolicy has no resource-level scoping in IAM, so the AwsCustomResource policy is emitted with Resource: *; this policy is generated by the aws-cdk Domain construct, not authored by VAMS.",
                    appliesTo: ["Resource::*"],
                },
            ],
            true
        );

        NagSuppressions.addResourceSuppressions(
            serviceLinkedRole,
            [
                {
                    id: "AwsSolutions-IAM5",
                    reason: "iam:CreateServiceLinkedRole requires a wildcard resource; it can only create the AWS-managed OpenSearch Service service-linked role and creates no other IAM resources.",
                },
                {
                    id: "AwsSolutions-IAM4",
                    reason: "The AwsCustomResource provider Lambda uses the AWS managed AWSLambdaBasicExecutionRole; acceptable for this short-lived deploy-time helper.",
                },
                {
                    id: "AwsSolutions-L1",
                    reason: "The AwsCustomResource provider Lambda runtime is managed by the CDK custom-resources framework.",
                },
            ],
            true
        );
    }

    public grantOSDomainAccess(lambdaFunction: lambda.Function & { role?: cdk.aws_iam.IRole }) {
        //Restrict to role ARNS of the lambda functions accessing opensearch (main access policy for opensearch provisioned + VPC security group)
        const opensearchDomainPolicy = new cdk.aws_iam.PolicyStatement({
            effect: cdk.aws_iam.Effect.ALLOW,
            principals: [lambdaFunction.role!],
            resources: [this.domain.domainArn + "/*"],
            actions: ["es:ESHttp*"],
        });

        this.domain.addAccessPolicies(opensearchDomainPolicy);
        this.domain.connections.allowFrom(lambdaFunction, Port.tcp(443));

        return opensearchDomainPolicy;
    }
}
