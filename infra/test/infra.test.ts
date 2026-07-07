/*
 * Copyright 2022 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import * as cdk from "aws-cdk-lib";
import * as Infra from "../lib/core-stack";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import commercialTemplate from "../config/config.template.commercial.json";

/**
 * Build a minimal mock configuration for testing. The public (`ConfigPublic`)
 * portion is sourced from the commercial deploy template so it never drifts
 * from the config shape, then the handful of test-specific overrides (fixed
 * account/region, everything toggled off for the empty-stack assertion) and
 * the internal `Config`-only fields are layered on top.
 */
const createMockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;

    // Fixed synth environment.
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.env.loadContextIgnoreVPCStacks = true; // avoid VPC-dependent stacks in tests

    // Minimal, isolated stack: no WAF, no CloudTrail, no OpenSearch, no pipelines.
    config.app.baseStackName = "vams-test";
    config.app.adminUserId = "test-admin";
    config.app.adminEmailAddress = "test@example.com";
    config.app.useWaf = false;
    config.app.addStackCloudTrailLogs = false;
    config.app.openSearch.useServerless.enabled = false;
    config.app.openSearch.useProvisioned.enabled = false;
    config.app.useLocationService.enabled = false;
    config.app.pipelines.useConversion3dBasic.enabled = false;

    // Internal (non-public) Config fields normally set by getConfig().
    config.enableCdkNag = false;
    config.dockerDefaultPlatform = "";
    config.s3AdditionalBucketPolicyJSON = undefined;
    config.iamRoleCustomizationJSON = undefined;
    config.openSearchAssetIndexName = "assets1236";
    config.openSearchFileIndexName = "files1236";
    config.openSearchAssetIndexNameSSMParam = "/vams-test-us-east-1/aos/assetIndexName";
    config.openSearchFileIndexNameSSMParam = "/vams-test-us-east-1/aos/fileIndexName";
    config.openSearchDomainEndpointSSMParam = "/vams-test-us-east-1/aos/endPoint";
    config.locationServiceApiKeyArnSSMParam = "/vams-test-us-east-1/locationService/apiKeyArn";
    config.webUrlDeploymentSSMParam = "/vams-test-us-east-1/web/url";
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";

    return config;
};

test("Core stack synthesizes", () => {
    // CoreVAMSStack reads the "environments" context (tags + IAM role transform),
    // so seed it the way cdk.json does.
    const app = new cdk.App({
        context: {
            environments: {
                common: { SolutionName: "AWSVisualAssetManagementSystem" },
                aws: { PermissionBoundaryArn: "", IamRoleNamePrefix: "" },
            },
        },
    });
    const mockConfig = createMockConfig();
    // The service helper resolves partition-aware ARNs/endpoints from a
    // module-level config, initialized in bin/infra.ts. Mirror that here.
    Service.SetConfig(mockConfig);

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

    // THEN — the stack synthesizes to a non-empty CloudFormation template.
    const template = app.synth().getStackArtifact(stack.artifactId).template;
    expect(Object.keys(template.Resources ?? {}).length).toBeGreaterThan(0);
});
