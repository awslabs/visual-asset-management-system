/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { expect as expectCDK, matchTemplate, MatchStyle } from "@aws-cdk/assert";
import * as cdk from "aws-cdk-lib";
import * as Infra from "../lib/core-stack";
import * as Config from "../config/config";

// Create a minimal mock configuration for testing
const createMockConfig = (): Config.Config => {
    return {
        name: "vams",
        env: {
            account: "123456789012",
            region: "us-east-1",
            partition: "aws",
            coreStackName: "vams-test-us-east-1",
            loadContextIgnoreVPCStacks: true, // Set to true to avoid VPC-dependent stacks in tests
        },
        app: {
            baseStackName: "vams-test",
            assetBuckets: {
                createNewBucket: true,
                defaultNewBucketSyncDatabaseId: "default",
                externalAssetBuckets: [] as any,
            },
            adminUserId: "test-admin",
            adminEmailAddress: "test@example.com",
            useFips: false,
            useWaf: false,
            addStackCloudTrailLogs: false,
            useKmsCmkEncryption: {
                enabled: false,
                optionalExternalCmkArn: "",
            },
            govCloud: {
                enabled: false,
                il6Compliant: false,
            },
            useGlobalVpc: {
                enabled: false,
                useForAllLambdas: false,
                addVpcEndpoints: false,
                optionalExternalVpcId: "",
                optionalExternalIsolatedSubnetIds: "",
                optionalExternalPrivateSubnetIds: "",
                optionalExternalPublicSubnetIds: "",
                vpcCidrRange: "10.1.0.0/16",
            },
            openSearch: {
                useServerless: {
                    enabled: false,
                },
                useProvisioned: {
                    enabled: false,
                    dataNodeInstanceType: "r6g.large.search",
                    masterNodeInstanceType: "r6g.large.search",
                    ebsInstanceNodeSizeGb: 120,
                },
            },
            useLocationService: {
                enabled: false,
            },
            useAlb: {
                enabled: false,
                usePublicSubnet: false,
                addAlbS3SpecialVpcEndpoint: false,
                domainHost: "",
                certificateArn: "",
                optionalHostedZoneId: "",
            },
            useCloudfront: {
                enabled: true,
            },
            pipelines: {
                useConversion3dBasic: {
                    enabled: false,
                },
                usePreviewPcPotreeViewer: {
                    enabled: false,
                },
                useGenAiMetadata3dLabeling: {
                    enabled: false,
                },
                useRapidPipeline: {
                    enabled: false,
                    ecrContainerImageURI: "",
                },
                useModelOps: {
                    enabled: false,
                    ecrContainerImageURI: "",
                },
            },
            authProvider: {
                presignedUrlTimeoutSeconds: 86400,
                authorizerOptions: {
                    allowedIpRanges: [],
                },
                useCognito: {
                    enabled: true,
                    useSaml: false,
                    useUserPasswordAuthFlow: false,
                    credTokenTimeoutSeconds: 3600,
                },
                useExternalOAuthIdp: {
                    enabled: false,
                    idpAuthProviderUrl: "",
                    idpAuthClientId: "",
                    idpAuthProviderScope: "",
                    idpAuthProviderScopeMfa: "",
                    idpAuthPrincipalDomain: "",
                    idpAuthProviderTokenEndpoint: "",
                    idpAuthProviderAuthorizationEndpoint: "",
                    idpAuthProviderDiscoveryEndpoint: "",
                    lambdaAuthorizorJWTIssuerUrl: "",
                    lambdaAuthorizorJWTAudience: "",
                },
            },
            webUi: {
                optionalBannerHtmlMessage: "",
                allowUnsafeEvalFeatures: false,
            },
        },
        // Internal config properties
        enableCdkNag: false,
        dockerDefaultPlatform: "",
        s3AdditionalBucketPolicyJSON: undefined,
        openSearchIndexName: "assets1236",
        openSearchIndexNameSSMParam: "/vams-test-us-east-1/aos/indexName",
        openSearchDomainEndpointSSMParam: "/vams-test-us-east-1/aos/endPoint",
    };
};

test("Empty Stack", () => {
    const app = new cdk.App();
    const mockConfig = createMockConfig();

    // WHEN
    const stack = new Infra.CoreVAMSStack(app, "MyTestStack", {
        env: {
            account: mockConfig.env.account,
            region: mockConfig.env.region,
        },
        stackName: mockConfig.app.baseStackName,
        ssmWafArn: "",
        config: mockConfig,
        description: "Test stack for VAMS",
    });

    // THEN
    expectCDK(stack).to(
        matchTemplate(
            {
                Resources: {},
            },
            MatchStyle.EXACT
        )
    );
});
