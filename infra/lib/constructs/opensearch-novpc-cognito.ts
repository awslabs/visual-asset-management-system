/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";

/* eslint-disable @typescript-eslint/no-empty-interface */
export interface OpensearchConstructProps {
    region: string;
    accountId: string;
    stackName: string;
    cognitoUserPool: cdk.aws_cognito.IUserPool;
    cognitoIdentityPoolId: string;
    cognitoAuthenticatedRole: string;
    dataNodeInstanceType?: string;
    dataNodesCount?: number;
    masterNodeInstanceType?: string;
    masterNodesCount?: number;
    ebsVolumeSize?: number;
    ebsVolumeType?: cdk.aws_ec2.EbsDeviceVolumeType;
    zoneAwareness?: cdk.aws_opensearchservice.ZoneAwarenessConfig;
    version?: cdk.aws_opensearchservice.EngineVersion;
}

const defaultProps: Partial<OpensearchConstructProps> = {
    //  masterNodeInstanceType: 'r6g.2xlarge.search',
    //  dataNodeInstanceType: 'r6g.2xlarge.search',
    // masterNodeInstanceType: 'r6g.large.search',
    masterNodeInstanceType: "r6g.large.search",
    // masterNodeInstanceType: 'r5.large.search',
    // dataNodeInstanceType:   'r6g.large.search',
    // dataNodeInstanceType: 'r6g.2xlarge.search',
    // dataNodeInstanceType: 'i3.2xlarge.search',
    dataNodeInstanceType: "r6gd.large.search",
    masterNodesCount: 3,
    dataNodesCount: 6,
    // ebsVolumeSize: 256,
    // ebsVolumeType: cdk.aws_ec2.EbsDeviceVolumeType.GENERAL_PURPOSE_SSD_GP3,
    zoneAwareness: { enabled: true },
    version: cdk.aws_opensearchservice.EngineVersion.OPENSEARCH_2_3,
};

/*


{
   "Version": "2012-10-17",
   "Statement": [
     {
       "Effect": "Allow",
       "Action": [
         "cognito-idp:DescribeUserPool",
         "cognito-idp:CreateUserPoolClient",
         "cognito-idp:DeleteUserPoolClient",
         "cognito-idp:DescribeUserPoolClient",
         "cognito-idp:AdminInitiateAuth",
         "cognito-idp:AdminUserGlobalSignOut",
         "cognito-idp:ListUserPoolClients",
         "cognito-identity:DescribeIdentityPool",
         "cognito-identity:UpdateIdentityPool",
         "cognito-identity:SetIdentityPoolRoles",
         "cognito-identity:GetIdentityPoolRoles"
       ],
       "Resource": [
         "arn:aws:cognito-identity:*:*:identitypool/*",
         "arn:aws:cognito-idp:*:*:userpool/*"
       ]
     },
     {
       "Effect": "Allow",
       "Action": "iam:PassRole",
       "Resource": "arn:aws:iam::*:role/*",
       "Condition": {
         "StringLike": {
           "iam:PassedToService": "cognito-identity.amazonaws.com"
         }
       }
     }
   ]
 }

*/

/*
Deploys an Amazon Opensearch Domain with Cognito Authentication enabled for Dashboard users
*/
export class OpensearchConstruct extends Construct {
    public config: OpensearchConstructProps;
    public osDomain: cdk.aws_opensearchservice.Domain;
    public openSearchRole: cdk.aws_iam.Role;
    public openSearchDomainPolicy: cdk.aws_iam.PolicyStatement;

    constructor(parent: Construct, name: string, props: OpensearchConstructProps) {
        super(parent, name);
        props = { ...defaultProps, ...props };

        const opensearchCognitoRole = new cdk.aws_iam.Role(this, "openSearchRole", {
            roleName: "opensearchCognitoRole",
            managedPolicies: [
                cdk.aws_iam.ManagedPolicy.fromAwsManagedPolicyName(
                    "AmazonOpenSearchServiceCognitoAccess"
                ),
            ],
            assumedBy: new cdk.aws_iam.ServicePrincipal("opensearchservice.amazonaws.com"),
        });

        const opensearchDomainPolicy = new cdk.aws_iam.PolicyStatement({
            effect: cdk.aws_iam.Effect.ALLOW,
            principals: [new cdk.aws_iam.AnyPrincipal()],
            resources: ["arn:aws:es:" + props.region + ":" + props.accountId + ":" + "domain/*"],
            actions: ["es:*"],
        });

        const osDomain = new cdk.aws_opensearchservice.Domain(this, "OpenSearchDomain", {
            version: cdk.aws_opensearchservice.EngineVersion.OPENSEARCH_2_3,
            ebs: {
                enabled: false,
            },
            // ebs: {
            //    volumeSize: props.ebsVolumeSize,
            //    volumeType: props.ebsVolumeType,
            // },
            nodeToNodeEncryption: true,
            encryptionAtRest: {
                enabled: true,
            },
            capacity: {
                dataNodeInstanceType: props.dataNodeInstanceType,
                dataNodes: props.dataNodesCount,
                masterNodeInstanceType: props.masterNodeInstanceType,
                masterNodes: props.masterNodesCount,
            },
            enforceHttps: true,
            zoneAwareness: props.zoneAwareness,
            fineGrainedAccessControl: {
                masterUserArn: props.cognitoAuthenticatedRole,
            },
            cognitoDashboardsAuth: {
                identityPoolId: props.cognitoIdentityPoolId,
                userPoolId: props.cognitoUserPool.userPoolId,
                role: opensearchCognitoRole,
            },
            removalPolicy: cdk.RemovalPolicy.DESTROY,
            logging: {
                auditLogEnabled: true,
                slowSearchLogEnabled: true,
                appLogEnabled: true,
                slowIndexLogEnabled: true,
            },
        });

        osDomain.addAccessPolicies(opensearchDomainPolicy);

        // Assign Cfn Outputs
        new cdk.CfnOutput(this, "OpenSearch Domain", {
            value: osDomain.domainEndpoint,
            exportName: "opensearchdomainendpoint",
        });

        // Store in Parameter Store
        new cdk.aws_ssm.StringParameter(this, "OpenSearch_Domain_Endpoint", {
            parameterName: `/${props.stackName}/${name}OpenSearchDomainEndpoint`,
            stringValue: osDomain.domainEndpoint,
            description: `The endpoint of the OpenSearch Domain ${name} in ${props.stackName}`,
            tier: cdk.aws_ssm.ParameterTier.STANDARD,
            allowedPattern: ".*",
        });

        // assign public properties
        this.config = props;
        this.osDomain = osDomain;
        this.openSearchRole = opensearchCognitoRole;
        this.openSearchDomainPolicy = opensearchDomainPolicy;
    }
}
