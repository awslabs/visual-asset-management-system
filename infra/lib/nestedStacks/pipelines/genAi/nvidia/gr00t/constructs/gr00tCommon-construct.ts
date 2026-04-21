/*
 * Copyright 2024 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */
import { storageResources } from "../../../../../storage/storageBuilder-nestedStack";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as efs from "aws-cdk-lib/aws-efs";
import * as s3 from "aws-cdk-lib/aws-s3";
import * as cdk from "aws-cdk-lib";
import { Stack, RemovalPolicy } from "aws-cdk-lib";
import { Construct } from "constructs";
import { NagSuppressions } from "cdk-nag";
import * as Config from "../../../../../../../config/config";
import { requireTLSAndAdditionalPolicyAddToResourcePolicy } from "../../../../../../helper/security";

export interface Gr00tCommonConstructProps extends cdk.StackProps {
    config: Config.Config;
    vpc: ec2.IVpc;
    subnets: ec2.ISubnet[];
    securityGroups: ec2.ISecurityGroup[];
    storageResources: storageResources;
}

/**
 * Default input properties
 */
const defaultProps: Partial<Gr00tCommonConstructProps> = {};

/**
 * Gr00tCommonConstruct
 *
 * Shared resources across all Gr00t model types.
 * - S3 Model Cache Bucket: Caches downloaded model weights
 * - EFS FileSystem: Persists downloaded models across Batch job invocations
 * - EFS Security Group: Controls NFS access to the EFS filesystem
 */
export class Gr00tCommonConstruct extends Construct {
    public readonly modelCacheBucket: s3.Bucket;
    public readonly efsFileSystem: efs.FileSystem;
    public readonly efsSecurityGroup: ec2.SecurityGroup;

    constructor(parent: Construct, name: string, props: Gr00tCommonConstructProps) {
        super(parent, name);

        props = { ...defaultProps, ...props };

        const region = Stack.of(this).region;
        const account = Stack.of(this).account;

        /**
         * S3 Model Cache Bucket
         * Shared across all Gr00t models for caching downloaded model weights
         */
        this.modelCacheBucket = new s3.Bucket(this, "Gr00tModelCacheBucket", {
            encryption: props.storageResources.encryption.kmsKey
                ? s3.BucketEncryption.KMS
                : s3.BucketEncryption.S3_MANAGED,
            encryptionKey: props.storageResources.encryption.kmsKey,
            blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
            removalPolicy: RemovalPolicy.RETAIN,
            versioned: false,
        });
        requireTLSAndAdditionalPolicyAddToResourcePolicy(this.modelCacheBucket, props.config);

        /**
         * EFS FileSystem for Gr00t model weights
         * Persists downloaded models across Batch job invocations to avoid re-downloading
         */
        this.efsSecurityGroup = new ec2.SecurityGroup(this, "Gr00tEfsSecurityGroup", {
            vpc: props.vpc,
            description: "Security group for Gr00t EFS file system",
            allowAllOutbound: false,
        });

        this.efsFileSystem = new efs.FileSystem(this, "Gr00tModelEfs", {
            vpc: props.vpc,
            vpcSubnets: props.subnets.length > 0 ? { subnets: props.subnets } : undefined,
            securityGroup: this.efsSecurityGroup,
            encrypted: true,
            kmsKey: props.storageResources.encryption.kmsKey,
            performanceMode: efs.PerformanceMode.GENERAL_PURPOSE,
            throughputMode: efs.ThroughputMode.ELASTIC,
            removalPolicy: RemovalPolicy.DESTROY,
        });

        /**
         * CDK Nag Suppressions
         */
        NagSuppressions.addResourceSuppressions(
            this.modelCacheBucket,
            [
                {
                    id: "AwsSolutions-S1",
                    reason: "Model cache bucket does not require access logging as it contains only cached model weights downloaded for Gr00t",
                },
            ],
            true
        );
    }
}
