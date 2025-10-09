/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import { IAMArn, Service } from "../../../helper/service-helper";
import { NagSuppressions } from "cdk-nag";
import { CfnOutput, CustomResource } from "aws-cdk-lib";
import * as cr from "aws-cdk-lib/custom-resources";
import * as path from "path";
import { LAMBDA_NODE_RUNTIME } from "../../../../config/config";
import { Port, SecurityGroup, Vpc } from "aws-cdk-lib/aws-ec2";
import { CfnServiceLinkedRole } from "aws-cdk-lib/aws-iam";
import { IAMClient, ListRolesCommand } from "@aws-sdk/client-iam";
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
}

const defaultProps: Partial<OpensearchProvisionedConstructProps> = {
    //  masterNodeInstanceType: 'r6g.2xlarge.search',
    //  dataNodeInstanceType: 'r6g.2xlarge.search',
    // masterNodeInstanceType: 'r6g.large.search',
    masterNodeInstanceType: "r6g.large.search",
    // masterNodeInstanceType: 'r5.large.search',
    // dataNodeInstanceType:   'r6g.large.search',
    // dataNodeInstanceType: 'r6g.2xlarge.search',
    // dataNodeInstanceType: 'i3.2xlarge.search',
    dataNodeInstanceType: "r6gd.large.search",
    masterNodesCount: 3, //Minimum of 3
    dataNodesCount: 2, //Minimum of 2, must be even number.
    ebsVolumeSize: 120,
    ebsVolumeType: cdk.aws_ec2.EbsDeviceVolumeType.GENERAL_PURPOSE_SSD_GP3,
    zoneAwareness: { enabled: true },
};

const iam = new IAMClient({});

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

        //https://github.com/aws-samples/opensearch-vpc-cdk/blob/main/lib/opensearch-vpc-cdk-stack.ts

        // Service-linked role(s) that Amazon OpenSearch Service will use
        let serviceLinkedRoleEs: CfnServiceLinkedRole | undefined;
        (async () => {
            const response = await iam.send(
                new ListRolesCommand({
                    PathPrefix: `/aws-service-role/es.amazonaws.com/`, //Currently fixed name and not related to principal name
                })
            );

            // Only if the role for OpenSearch Service doesn't exist, it will be created.
            if (response.Roles && response.Roles?.length == 0) {
                serviceLinkedRoleEs = new CfnServiceLinkedRole(
                    this,
                    "OpensearchServiceLinkedRoleEs",
                    {
                        awsServiceName: "es.amazonaws.com", //Currently fixed name and not related to principal name
                    }
                );
            }
        })();

        //Test service linked role to make sure we cover other partitions. No harm in creating additional service linked role right now.
        let serviceLinkedRoleAos: CfnServiceLinkedRole | undefined;
        (async () => {
            const response = await iam.send(
                new ListRolesCommand({
                    PathPrefix: `/aws-service-role/${Service("ES").PrincipalString}/`,
                })
            );

            // Only if the role for OpenSearch Service doesn't exist, it will be created.
            if (response.Roles && response.Roles?.length == 0) {
                serviceLinkedRoleAos = new CfnServiceLinkedRole(
                    this,
                    "OpensearchServiceLinkedRoleAos",
                    {
                        awsServiceName: `${Service("ES").PrincipalString}`,
                    }
                );
            }
        })();

        //Loop through all  subnets and store subnets in an array up to the total number of data nodes specified
        //Note: Make sure each subnet chosen is in a different availability zone. OS Domains are very sensitive about choosing the right subnets, thus this additional filter.
        const subnets: ec2.ISubnet[] = [];
        const azUsed: string[] = [];

        props.subnets.forEach((element) => {
            if (
                azUsed.indexOf(element.availabilityZone) == -1 &&
                subnets.length < props.dataNodesCount!
            ) {
                azUsed.push(element.availabilityZone);
                subnets.push(element);
            }
        });

        const osDomain = new cdk.aws_opensearchservice.Domain(this, "OpenSearchDomain", {
            version: Config.OPENSEARCH_VERSION,

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
                dataNodes: props.dataNodesCount,
                masterNodeInstanceType: props.masterNodeInstanceType,
                masterNodes: props.masterNodesCount,
            },
            enforceHttps: true,
            zoneAwareness: props.zoneAwareness,
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

        //Add dependency to the service-linked role if it exists. This is required for the domain to be created in the VPC.
        if (serviceLinkedRoleEs) {
            osDomain.node.addDependency(serviceLinkedRoleEs);
        }

        if (serviceLinkedRoleAos) {
            osDomain.node.addDependency(serviceLinkedRoleAos);
        }

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
                timeout: cdk.Duration.seconds(30),
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
                version: "2",
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
                reason: "Configured as intended.",
            },
        ]);

        NagSuppressions.addResourceSuppressions(osDomain, [
            {
                id: "AwsSolutions-OS1",
                reason: "Configured as intended. Provisioned configuration meant primarily for GovCloud deployment that won't be public and restricted to individual lambda roles for access to the domain.",
            },
            {
                id: "AwsSolutions-OS3",
                reason: "Configured as intended. Provisioned configuration meant primarily for GovCloud deployment that won't be public and restricted to individual lambda roles for access to the domain.",
            },
        ]);
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
