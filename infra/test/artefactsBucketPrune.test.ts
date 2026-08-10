/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The artefacts bucket holds two independently-owned trees: ./lib/artefacts, uploaded by the
 * root-scoped DeployArtefacts deployment, and vamsSchema/<hash>/, uploaded per pipeline by
 * VamsSchemaRegistration. The root deployment prunes by default, and its underlying `aws s3 sync
 * --delete` deletes any destination key absent from its source — which includes every schema
 * bundle. Refreshing an unrelated artefact would then erase the bundles while the registration
 * custom resources still expect to read them, and because the SchemaDeploy resources see no
 * property change they do not re-upload.
 *
 * The exclude filter is what prevents that, so assert it reaches the synthesized resource.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { LayerVersion } from "aws-cdk-lib/aws-lambda";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../config/config";
import * as Service from "../lib/helper/service-helper";
import commercialTemplate from "../config/config.template.commercial.json";
import { storageResourcesBuilder } from "../lib/nestedStacks/storage/storageBuilder-nestedStack";
import { ResourceNameRegistry } from "../lib/nestedStacks/resourceNames/resourceNameRegistry";

const mockConfig = (): Config.Config => {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = "123456789012";
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    config.enableCdkNag = false;
    config.resourceNamesSSMParamPrefix = "/vams-test-us-east-1/resourceNames";
    return config;
};

describe("artefacts bucket deployment", () => {
    let template: Template;

    beforeAll(() => {
        const config = mockConfig();
        Service.SetConfig(config);

        const app = new cdk.App();
        const stack = new cdk.Stack(app, "S", {
            env: { account: config.env.account, region: config.env.region },
        });
        const layer = lambda.LayerVersion.fromLayerVersionArn(
            stack,
            "CommonLayer",
            "arn:aws:lambda:us-east-1:123456789012:layer:vams-common:1"
        ) as LayerVersion;

        storageResourcesBuilder(
            stack,
            config,
            layer,
            undefined as unknown as ec2.IVpc,
            [],
            new ResourceNameRegistry()
        );
        template = Template.fromStack(stack);
    });

    test("the root artefacts deployment excludes the vamsSchema prefix from its prune", () => {
        // Locate the deployment by its source, not by logical id, so a construct rename does not
        // silently turn this into a no-op assertion.
        const deployments = template.findResources("Custom::CDKBucketDeployment");
        const roots = Object.values(deployments).filter(
            (r) => r.Properties?.DestinationBucketKeyPrefix === undefined
        );

        expect(roots).toHaveLength(1);
        const props = roots[0].Properties!;

        // Prune defaults to true and is what makes the exclude necessary; if a future change sets
        // it false the exclude is redundant but harmless, so accept either as long as they agree.
        if (props.Prune !== false) {
            expect(props.Exclude).toContain("vamsSchema/*");
        }
    });
});
