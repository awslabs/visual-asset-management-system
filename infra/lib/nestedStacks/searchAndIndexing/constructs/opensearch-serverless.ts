/*
 * Copyright 2023 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Construct } from "constructs";
import * as aoss from "aws-cdk-lib/aws-opensearchserverless";
import * as cr from "aws-cdk-lib/custom-resources";
import * as path from "path";
import { CustomResource, Names, NestedStack } from "aws-cdk-lib";
import * as iam from "aws-cdk-lib/aws-iam";
import { LAMBDA_NODE_RUNTIME } from "../../../../config/config";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../config/config";
import { generateUniqueNameHash } from "../../../helper/security";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { aws_opensearchserverless as opensearchserverless } from "aws-cdk-lib";
import * as lambda from "aws-cdk-lib/aws-lambda";
import {
    kmsKeyLambdaPermissionAddToResourcePolicy,
    suppressCdkNagLambda,
} from "../../../helper/security";
import { storageResources } from "../../storage/storageBuilder-nestedStack";
import * as njslambda from "aws-cdk-lib/aws-lambda-nodejs";
import { IAMArn } from "../../../helper/service-helper";

interface OpensearchServerlessConstructProps extends cdk.StackProps {
    config: Config.Config;
    principalArn: string[];
    storageResources: storageResources;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
}

export class OpensearchServerlessConstruct extends Construct {
    public aossEndpointUrl: string;
    collectionUid: string;
    collectionArn: string;
    config: Config.Config;
    useVPCEndpoint: boolean;
    //Whether VAMS itself creates the data-plane VPC endpoint, its security group, and the VPC network
    //access policy. True for a private CLASSIC collection (the managed endpoint is an OpenSearch Serverless
    //resource, not an EC2 interface endpoint) and for a private NEXTGEN collection only when
    //useGlobalVpc.addVpcEndpoints is true. False (deferred) for a private NEXTGEN collection when
    //addVpcEndpoints is false — the operator creates the standard EC2 aoss-data endpoint and the network
    //policy manually after deployment.
    createEndpointResources: boolean;
    //The data-plane VPC endpoint differs by generation: NEXTGEN uses a standard EC2 interface endpoint
    //(service com.amazonaws.{region}.aoss-data) for the on.aws collection hostname; CLASSIC uses the
    //OpenSearch Serverless-managed endpoint for the aoss.amazonaws.com hostname.
    vpcEndpointAOSS?: cdk.aws_opensearchserverless.CfnVpcEndpoint;
    vpcEndpointAOSSStandard?: ec2.InterfaceVpcEndpoint;
    vpcEndpointAOSSId?: string;
    vpcEndpointAOSSDependable?: Construct;
    vpcEndpointAOSSSecurityGroup?: ec2.SecurityGroup;

    constructor(parent: Construct, name: string, props: OpensearchServerlessConstructProps) {
        super(parent, name);

        this.collectionUid = (
            "collection" +
            generateUniqueNameHash(
                props.config.env.coreStackName,
                props.config.env.account,
                "AOSSCollection",
                10
            )
        ).toLowerCase();
        this.config = props.config;

        const useServerless = props.config.app.openSearch.useServerless;
        const standbyReplicas = useServerless.enableStandbyReplicas ? "ENABLED" : "DISABLED";

        //A private (non-public) collection is reachable only through a VPC endpoint. Config validation
        //guarantees that allowPublic=false implies useGlobalVpc.enabled is true, so the VPC and its subnets
        //are available here (only the OpenSearch-facing Lambdas are placed in the VPC; useForAllLambdas is
        //not required).
        this.useVPCEndpoint = !useServerless.allowPublic;

        //The NEXTGEN data-plane endpoint is a standard EC2 interface endpoint, so it follows the global
        //useGlobalVpc.addVpcEndpoints setting like every other EC2 interface endpoint VAMS creates. When that
        //is false for a private NEXTGEN collection, VAMS does NOT create the endpoint or the VPC network
        //access policy — the operator creates the standard com.amazonaws.{region}.aoss-data interface endpoint
        //and the matching network policy manually after deployment (see the OpenSearch developer guide).
        //CLASSIC uses the OpenSearch Serverless-managed endpoint (not an EC2 interface endpoint), so it is not
        //governed by addVpcEndpoints and is always created for a private collection.
        this.createEndpointResources =
            this.useVPCEndpoint &&
            (!useServerless.nextGen || props.config.app.useGlobalVpc.addVpcEndpoints);

        //"Deferred VPC setup" = a private NEXTGEN collection where VAMS does not create the endpoint/policy
        //(addVpcEndpoints=false). VAMS NEVER auto-creates the endpoint or network policy in this case — that is
        //always manual. The deployDeferredIndexSchema flag does NOT change that; it only controls whether the
        //schema-deploy custom resource attempts index creation against the operator-created endpoint. When
        //addVpcEndpoints=true the deployment is not deferred, so the flag is ignored and the schema is always
        //deployed normally.
        const deferVpcSetup = this.useVPCEndpoint && !this.createEndpointResources;
        const deferIndexCreation = deferVpcSetup && !useServerless.deployDeferredIndexSchema;
        //Run schema-deploy in the VPC whenever it will actually talk to a private collection endpoint: either
        //VAMS created the endpoint, or the operator created it manually and we are now deploying the deferred
        //schema. For a public collection the function stays outside the VPC.
        const schemaDeployInVpc =
            this.createEndpointResources || (deferVpcSetup && !deferIndexCreation);

        //Create the data-plane VPC endpoint when the collection is private and VAMS owns endpoint creation.
        //The endpoint is required to attach to the collection network security policy. The endpoint type
        //depends on the collection generation:
        // - NEXTGEN collections expose an on.aws hostname (collection-id.aoss.{region}.on.aws) reached through a
        //   standard EC2 PrivateLink interface endpoint (service com.amazonaws.{region}.aoss-data, private DNS).
        // - CLASSIC collections expose an aoss.amazonaws.com hostname reached through the OpenSearch
        //   Serverless-managed endpoint, which provisions its own Route 53 private hosted zone.
        //(https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vpc.html,
        // https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-collection-endpoints.html)
        if (this.createEndpointResources) {
            const aossVPCESecurityGroup = new ec2.SecurityGroup(this, "AossVPCESecurityGroup", {
                vpc: props.vpc,
                allowAllOutbound: true, //allows all output on endpoint to the service
                description: "AOSS VPC Endpoint Security Group",
            });

            //Allow connections from any VPC IP
            aossVPCESecurityGroup.addIngressRule(
                ec2.Peer.ipv4(props.vpc.vpcCidrBlock),
                ec2.Port.tcp(443)
            );
            this.vpcEndpointAOSSSecurityGroup = aossVPCESecurityGroup;

            if (useServerless.nextGen) {
                //Standard interface endpoint for NextGen on.aws collection hostnames. The service name is
                //com.amazonaws.{region}.aoss-data; build it partition-aware via InterfaceVpcEndpointAwsService
                //(it substitutes the region and the partition-correct DNS prefix). Private DNS supplies
                //resolution for the *.aoss.{region}.on.aws hostnames.
                const standardVpcEndpoint = new ec2.InterfaceVpcEndpoint(
                    this,
                    "AOSSDataVpcEndpoint",
                    {
                        vpc: props.vpc,
                        service: new ec2.InterfaceVpcEndpointAwsService(
                            "aoss-data",
                            "com.amazonaws",
                            443
                        ),
                        subnets: { subnets: props.subnets },
                        securityGroups: [aossVPCESecurityGroup],
                        privateDnsEnabled: true,
                    }
                );
                standardVpcEndpoint.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);
                this.vpcEndpointAOSSStandard = standardVpcEndpoint;
                this.vpcEndpointAOSSId = standardVpcEndpoint.vpcEndpointId;
                this.vpcEndpointAOSSDependable = standardVpcEndpoint;
            } else {
                //OpenSearch Serverless-managed endpoint for CLASSIC aoss.amazonaws.com collection hostnames.
                const cfnVpcEndpoint = new opensearchserverless.CfnVpcEndpoint(
                    this,
                    "AOSSCfnVpcEndpoint",
                    {
                        name:
                            "aossendpoint" +
                            generateUniqueNameHash(
                                props.config.env.coreStackName,
                                props.config.env.account,
                                "AOSSCfnVpcEndpoint",
                                10
                            ).toLowerCase(),
                        subnetIds: props.vpc.selectSubnets({ subnets: props.subnets }).subnetIds,
                        vpcId: props.vpc.vpcId,
                        securityGroupIds: [aossVPCESecurityGroup.securityGroupId],
                    }
                );

                cfnVpcEndpoint.applyRemovalPolicy(cdk.RemovalPolicy.DESTROY);
                this.vpcEndpointAOSS = cfnVpcEndpoint;
                this.vpcEndpointAOSSId = cfnVpcEndpoint.ref;
                this.vpcEndpointAOSSDependable = cfnVpcEndpoint;
            }
        }

        const schemaDeploy = new njslambda.NodejsFunction(
            this,
            "OpensearchServerlessDeploySchema",
            {
                entry: path.join(__dirname, "./schemaDeploy/deployschema.ts"),
                handler: "handler",
                bundling: {
                    externalModules: ["aws-sdk"],
                },
                runtime: LAMBDA_NODE_RUNTIME,
                //A freshly created collection (and, for a private collection, its VPC endpoint) can take
                //several minutes to become reachable, and a NEXTGEN scale-to-zero collection adds a 10-30s
                //cold start on the first request. The handler polls with backoff, so allow ample time.
                timeout: cdk.Duration.minutes(14),
                //Run in the VPC when the function will talk to a private collection endpoint — either VAMS
                //created the endpoint, or the operator created it manually and deployDeferredIndexSchema=true
                //is now deploying the deferred schema. In the deferred-and-not-yet-deploying case the function
                //runs outside the VPC and only writes SSM parameters.
                vpc: schemaDeployInVpc ? props.vpc : undefined,
                vpcSubnets: schemaDeployInVpc ? { subnets: props.subnets } : undefined,
                //Note: This schema deploy resource must run in the VPC in order to communicate with the AOSS and associated VPC Endpoint.
            }
        );

        kmsKeyLambdaPermissionAddToResourcePolicy(
            schemaDeploy,
            props.storageResources.encryption.kmsKey
        );

        //Apply the standard per-Lambda IAM4/IAM5 suppressions. Required because a private collection runs this
        //function in the VPC, which attaches the AWSLambdaVPCAccessExecutionRole managed policy.
        suppressCdkNagLambda(schemaDeploy);

        const principalsForAOSS = [...props.principalArn, schemaDeploy.role?.roleArn];

        const accessPolicy = this._grantCollectionAccess(principalsForAOSS);
        this.grantVPCeAccess(schemaDeploy);

        //Create a collection group and associate the collection with it. The collection group manages OCU
        //capacity limits and standby replicas for the collection. The group's generation (CLASSIC or NEXTGEN)
        //determines the OpenSearch Serverless behavior — NEXTGEN supports scale-to-zero capacity.
        const collectionGroupName = (
            "cg" +
            generateUniqueNameHash(
                props.config.env.coreStackName,
                props.config.env.account,
                "AOSSCollectionGroup",
                24
            )
        ).toLowerCase();
        const collectionGroup = new aoss.CfnCollectionGroup(this, "OSCollectionGroup", {
            name: collectionGroupName,
            generation: useServerless.nextGen ? "NEXTGEN" : "CLASSIC",
            standbyReplicas: standbyReplicas,
            capacityLimits: {
                minIndexingCapacityInOcu: useServerless.minIndexingOcu,
                maxIndexingCapacityInOcu: useServerless.maxIndexingOcu,
                minSearchCapacityInOcu: useServerless.minSearchOcu,
                maxSearchCapacityInOcu: useServerless.maxSearchOcu,
            },
        });

        const collection = new aoss.CfnCollection(this, "OSCollection", {
            name: this.collectionUid,
            type: "SEARCH",
            //The collection inherits OCU capacity limits and standby replicas from its collection group.
            collectionGroupName: collectionGroupName,
        });

        collection.addDependency(collectionGroup);

        this.collectionArn = collection.attrArn;

        const encryptionPolicy = {
            Rules: [{ ResourceType: "collection", Resource: [`collection/${collection.name}`] }],
            AWSOwnedKey: !props.config.app.useKmsCmkEncryption.enabled,
            KmsARN: props.config.app.useKmsCmkEncryption.enabled
                ? props.storageResources.encryption.kmsKey!.keyArn
                : undefined,
        };
        const encryptionPolicyCfn = new aoss.CfnSecurityPolicy(this, "OSEncryptionPolicy", {
            name: (
                `ep` +
                generateUniqueNameHash(
                    props.config.env.coreStackName,
                    props.config.env.account,
                    "OSEncryptionPolicy",
                    20
                )
            ).toLowerCase(),
            policy: JSON.stringify(encryptionPolicy),
            type: "encryption",
        });

        //When the NEXTGEN endpoint is deferred (private + addVpcEndpoints=false), VAMS does not create the
        //network access policy either — the operator creates both the standard aoss-data endpoint and a
        //matching network policy (with that endpoint's id in SourceVPCEs) manually after deployment. This is
        //independent of deployDeferredIndexSchema, which only governs index creation, not endpoint/policy
        //creation.
        let networkPolicyCfn: aoss.CfnSecurityPolicy | undefined;
        if (!deferVpcSetup) {
            const networkPolicy = [
                {
                    Rules: [
                        {
                            ResourceType: "collection",
                            Resource: [`collection/${collection.name}`],
                        },
                        {
                            ResourceType: "dashboard",
                            Resource: [`collection/${collection.name}`],
                        },
                    ],
                    AllowFromPublic: useServerless.allowPublic,
                    SourceVPCEs: this.useVPCEndpoint ? [this.vpcEndpointAOSSId] : undefined,
                },
            ];

            networkPolicyCfn = new aoss.CfnSecurityPolicy(this, "OSNetworkPolicy", {
                name: (
                    `np` +
                    generateUniqueNameHash(
                        props.config.env.coreStackName,
                        props.config.env.account,
                        "OSNetworkPolicy",
                        20
                    )
                ).toLowerCase(),
                policy: JSON.stringify(networkPolicy),
                type: "network",
            });

            if (this.useVPCEndpoint && this.vpcEndpointAOSSDependable)
                networkPolicyCfn.node.addDependency(this.vpcEndpointAOSSDependable);
        }

        collection.addDependency(encryptionPolicyCfn);
        if (networkPolicyCfn) collection.addDependency(networkPolicyCfn);

        schemaDeploy.addToRolePolicy(
            new cdk.aws_iam.PolicyStatement({
                actions: ["aoss:*"],
                resources: [collection.attrArn],
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

        const schemaDeployProvider = new cr.Provider(this, "OSSDeploySchemaProvider", {
            onEventHandler: schemaDeploy,
        });

        schemaDeployProvider.node.addDependency(schemaDeploy);
        schemaDeployProvider.node.addDependency(collection);
        schemaDeployProvider.node.addDependency(accessPolicy);

        if (this.createEndpointResources && this.vpcEndpointAOSSSecurityGroup) {
            schemaDeployProvider.node.addDependency(this.vpcEndpointAOSSSecurityGroup);
        }
        if (this.createEndpointResources && this.vpcEndpointAOSSDependable) {
            schemaDeployProvider.node.addDependency(this.vpcEndpointAOSSDependable);
        }

        new CustomResource(this, "DeploySSMIndexSchema", {
            serviceToken: schemaDeployProvider.serviceToken,
            properties: {
                endpointSSMParam: props.config.openSearchDomainEndpointSSMParam,
                assetIndexNameSSMParam: props.config.openSearchAssetIndexNameSSMParam,
                fileIndexNameSSMParam: props.config.openSearchFileIndexNameSSMParam,
                collectionEndpoint: collection.attrCollectionEndpoint,
                assetIndexName: props.config.openSearchAssetIndexName,
                fileIndexName: props.config.openSearchFileIndexName,
                //In deferred mode the VPC endpoint and network policy do not exist yet, so the collection is
                //not reachable. The handler still writes the SSM parameters but skips index creation so the
                //deployment completes. After the operator manually creates the endpoint + network policy, a
                //deployment with deployDeferredIndexSchema=true clears this flag so the schema is created.
                deferIndexCreation: deferIndexCreation ? "true" : "false",
                version: "2",
                Timestamp: Date.now().toString(), //Used to check index deployment every CDK deployment
            },
        });

        this.aossEndpointUrl = collection.attrCollectionEndpoint;

        //Nag Supressions
        NagSuppressions.addResourceSuppressions(schemaDeployProvider, [
            {
                id: "AwsSolutions-L1",
                reason: "Runtime is managed by the CDK custom-resources framework (cr.Provider) that backs the OpenSearch schema-deploy custom resource; VAMS does not control this provider function's runtime version.",
            },
        ]);

        if (this.createEndpointResources && this.vpcEndpointAOSSSecurityGroup) {
            //Nag Supressions
            NagSuppressions.addResourceSuppressions(this.vpcEndpointAOSSSecurityGroup, [
                {
                    id: "AwsSolutions-EC23",
                    reason: "VPC Endpoint Security Group is restricted to VPC cidr range on ports 443",
                },
                {
                    id: "CdkNagValidationFailure",
                    reason: "Validation failure due to inherent nature of CDK Nag Validations of CIDR ranges", //https://github.com/cdklabs/cdk-nag/issues/817
                },
            ]);
        }
    }

    public grantCollectionAccess(construct: Construct & { role?: cdk.aws_iam.IRole }) {
        const policy = [
            {
                Description: "Access",
                Rules: [
                    {
                        ResourceType: "index",
                        // Resource: ["index/*/*"],
                        Resource: [`index/${this.collectionUid}/*`],
                        Permission: [
                            // "aoss:*",
                            "aoss:ReadDocument",
                            "aoss:WriteDocument",
                            "aoss:CreateIndex",
                            "aoss:DeleteIndex",
                            "aoss:UpdateIndex",
                            "aoss:DescribeIndex",
                        ],
                    },
                    {
                        ResourceType: "collection",
                        Resource: [`collection/${this.collectionUid}`],
                        Permission: [
                            // "aoss:*",
                            "aoss:CreateCollectionItems",
                            "aoss:DeleteCollectionItems",
                            "aoss:UpdateCollectionItems",
                            "aoss:DescribeCollectionItems",
                        ],
                    },
                ],
                Principal: [construct.role?.roleArn],
            },
        ];

        const accessPolicy = new aoss.CfnAccessPolicy(construct, "Policy", {
            name:
                "ac" +
                generateUniqueNameHash(
                    this.config.env.coreStackName,
                    this.config.env.account,
                    "ac" + construct.role?.roleArn,
                    20
                ),
            type: "data",
            policy: JSON.stringify(policy),
        });

        construct.role?.addToPrincipalPolicy(
            new iam.PolicyStatement({
                effect: iam.Effect.ALLOW,
                resources: [this.collectionArn],
                actions: ["aoss:*"],
            })
        );
        return accessPolicy;
    }

    public grantVPCeAccess(lambdaFunction: lambda.Function) {
        //Add ingress to the AOSS VPC endpoint security group for this Lambda, but only when VAMS created the
        //endpoint and its security group. In deferred mode (private NEXTGEN with addVpcEndpoints=false) the
        //operator-created endpoint's security group must grant this access manually.
        if (this.createEndpointResources && this.vpcEndpointAOSSSecurityGroup) {
            this.vpcEndpointAOSSSecurityGroup.connections.allowFrom(
                lambdaFunction,
                ec2.Port.tcp(443)
            );
        }
    }

    private _grantCollectionAccess(principalsForAOSS: (string | undefined)[]) {
        // type that extends Construct and has a role property
        const policy = [
            {
                Description: "Access",
                Rules: [
                    {
                        ResourceType: "index",
                        Resource: [`index/${this.collectionUid}/*`],
                        Permission: ["aoss:*"],
                    },
                    {
                        ResourceType: "collection",
                        Resource: [`collection/${this.collectionUid}`],
                        Permission: ["aoss:*"],
                    },
                ],
                Principal: principalsForAOSS,
            },
        ];

        const accessPolicy = new aoss.CfnAccessPolicy(this, "Policy", {
            name:
                "acp" +
                generateUniqueNameHash(
                    this.config.env.coreStackName,
                    this.config.env.account,
                    "acp" + principalsForAOSS.toString(),
                    20
                ),
            type: "data",
            policy: JSON.stringify(policy),
        });
        return accessPolicy;
    }
}
