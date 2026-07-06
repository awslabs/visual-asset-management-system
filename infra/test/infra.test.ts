/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import * as Infra from "../lib/core-stack";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";

// Create a minimal mock configuration for testing.
// This is an intentionally partial mock: the snapshot test only needs the fields
// the stack reads to synthesize an empty template, not the full ConfigPublic shape.
// Cast through unknown so the test does not have to track every optional pipeline/
// addon field on the (large, frequently extended) Config interface.
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
                presignedUrlNetworkRestrictions: {
                    allowedIpRanges: [],
                    allowedVpceIds: [],
                },
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
                    availabilityZoneCount: 3,
                    numberOfShards: 1,
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
    } as unknown as Config.Config;
};

// The CoreVAMSStack is synthesized ONCE and shared across assertions. It must not be
// built more than once per test run: the stack populates module-level singletons (the
// s3AssetBuckets registry, the service-helper config) that are not reset between builds,
// so a second construction raises duplicate-construct errors. Synthesizing once in
// beforeAll also keeps the suite fast.
let template: Template;
let fromBuildSpy: jest.SpyInstance;

beforeAll(() => {
    const mockConfig = createMockConfig();

    // The Lambda layers call cdk.DockerImage.fromBuild(...), which eagerly invokes
    // `docker build` at construct time even when asset bundling is skipped. Stub it to
    // a no-op image (fromRegistry does not touch Docker) so the test is fully
    // Docker-free; the image is never used because bundling is disabled below.
    fromBuildSpy = jest
        .spyOn(cdk.DockerImage, "fromBuild")
        .mockReturnValue(cdk.DockerImage.fromRegistry("vams-test-stub-image"));

    // Initialize the partition-aware service helper with the mock config, mirroring
    // bin/infra.ts (which calls Service.SetConfig(config) before building stacks).
    // Without this the module-level config is undefined and ServiceFormatter throws.
    Service.SetConfig(mockConfig);

    const app = new cdk.App({
        context: {
            // Skip asset bundling for all stacks (equivalent to `cdk synth --no-bundle`)
            // so the test runs without Docker. The Lambda layers use
            // DockerImage.fromBuild bundling, which would otherwise require a Docker
            // daemon at synth time.
            "aws:cdk:bundling-stacks": [],
            // The stack reads "environments" for tags / IAM role transforms (mirrors
            // cdk.json). Without it CoreVAMSStack dereferences an undefined context value.
            environments: {
                common: {
                    SolutionName: "AWSVisualAssetManagementSystem",
                    Owner: "",
                    CostCenter: "",
                    BusinessUnit: "",
                },
                aws: {
                    PermissionBoundaryArn: "",
                    IamRoleNamePrefix: "",
                },
            },
        },
    });

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

    template = Template.fromStack(stack);
});

afterAll(() => {
    fromBuildSpy?.mockRestore();
});

test("CoreVAMSStack synthesizes from a minimal config", () => {
    // Smoke test: the root stack must construct and synthesize to a valid template
    // without throwing (verified by beforeAll completing). This guards against
    // config-interface drift and stack wiring regressions. (The previous assertion
    // compared against an empty template, which could never match a populated stack.)
    expect(Object.keys(template.toJSON().Resources ?? {}).length).toBeGreaterThan(0);
});

test("CoreVAMSStack provisions its core nested stacks", () => {
    // The core stack composes its functionality from nested stacks (storage, auth,
    // API, etc.). Assert nested-stack resources are present so a broken composition
    // surfaces here rather than only at deploy time.
    const nestedStacks = template.findResources("AWS::CloudFormation::Stack");
    expect(Object.keys(nestedStacks).length).toBeGreaterThan(0);
});
