/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The interface endpoints the EDH Workstations add-on adds to the VAMS VPC.
 *
 * `addonInterfaceEndpoints()` lists them and the VPC builder creates each one under its id, so an id
 * is the endpoint's construct id in the VPC stack, and a changed id replaces the live endpoint. What
 * is pinned here:
 *   * the containers feature adds the Batch endpoint, and the AMI builds feature the Image Builder,
 *     EC2, Systems Manager messages and EC2 messages endpoints, each under the id it deploys with;
 *   * the VPC stack emits each one once, with a logical id built from that id and the service it
 *     names;
 *   * with a pipeline on that already creates the Batch endpoint, the VPC still has one;
 *   * with the add-on off, the VPC has none of them.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import { Template } from "aws-cdk-lib/assertions";
import * as Config from "../../../config/config";
import * as Service from "../../../lib/helper/service-helper";
import { addonInterfaceEndpoints } from "../../../lib/nestedStacks/addon/addonVpcEndpoints";
import { VPCBuilderNestedStack } from "../../../lib/nestedStacks/vpc/vpcBuilder-nestedStack";
import {
    ACCOUNT,
    SynthResult,
    buildTemplateConfig,
    expectAbsent,
} from "../../support/templateSynth";
import { newTestApp } from "../../support/testApp";
import { enableEdhAmiBuilds, enableEdhContainers } from "./support/edhSynth";

/** Endpoint id -> the service name suffix of the endpoint it creates. */
const CONTAINER_ENDPOINTS: Record<string, string> = { BatchEndpoint: "batch" };
const AMI_BUILD_ENDPOINTS: Record<string, string> = {
    ImageBuilderEndpoint: "imagebuilder",
    EC2Endpoint: "ec2",
    SSMMessagesEndpoint: "ssmmessages",
    EC2MessagesEndpoint: "ec2messages",
};
const EDH_ENDPOINTS: Record<string, string> = { ...CONTAINER_ENDPOINTS, ...AMI_BUILD_ENDPOINTS };

/** The three switches `addonInterfaceEndpoints` reads; nothing else in the config is consulted. */
const edhConfig = (enabled: boolean, containers: boolean, amiBuilds: boolean): Config.Config =>
    ({
        app: {
            addons: {
                useEdhWorkstations: {
                    enabled,
                    containers: { enabled: containers },
                    amiBuilds: { enabled: amiBuilds },
                },
            },
        },
    } as unknown as Config.Config);

const idsOf = (config: Config.Config): string[] => addonInterfaceEndpoints(config).map((e) => e.id);

/** Both optional features on; `enableEdhAmiBuilds` resets containers, so it runs first. */
const withBothFeatures = (c: any): void => {
    enableEdhAmiBuilds(c);
    enableEdhContainers(c);
};

/** Every interface endpoint of the VPC stack built from the commercial template. */
const vpcEndpoints = (mutate: (c: any) => void): Array<{ logicalId: string; service: string }> => {
    const config = buildTemplateConfig("commercial", (c: any) => {
        mutate(c);
        c.app.useGlobalVpc.enabled = true;
        c.app.useGlobalVpc.addVpcEndpoints = true;
    });
    Service.SetConfig(config);
    const app = newTestApp();
    const root = new cdk.Stack(app, "EdhVpcEndpointsTest", {
        env: { account: ACCOUNT, region: config.env.region },
    });
    const vpcStack = new VPCBuilderNestedStack(root, "VPCBuilder", { config });
    return Object.entries<any>(Template.fromStack(vpcStack).findResources("AWS::EC2::VPCEndpoint"))
        .filter(([, r]) => r.Properties.VpcEndpointType === "Interface")
        .map(([logicalId, r]) => ({
            logicalId,
            service: SynthResult.flatten(r.Properties.ServiceName),
        }));
};

const endsWithService = (service: string, suffix: string): boolean =>
    service.endsWith(`.${suffix}`);

describe("addonInterfaceEndpoints", () => {
    test("adds the Batch endpoint for the containers feature", () => {
        const endpoints = addonInterfaceEndpoints(edhConfig(true, true, false));
        expect(endpoints.map((e) => e.id)).toEqual(Object.keys(CONTAINER_ENDPOINTS));
        expect(endpoints[0].service).toBe(ec2.InterfaceVpcEndpointAwsService.BATCH);
    });

    test("adds the four build endpoints for the AMI builds feature", () => {
        const endpoints = addonInterfaceEndpoints(edhConfig(true, false, true));
        expect(endpoints.map((e) => e.id)).toEqual(Object.keys(AMI_BUILD_ENDPOINTS));
        expect(endpoints.map((e) => e.service)).toEqual([
            ec2.InterfaceVpcEndpointAwsService.IMAGE_BUILDER,
            ec2.InterfaceVpcEndpointAwsService.EC2,
            ec2.InterfaceVpcEndpointAwsService.SSM_MESSAGES,
            ec2.InterfaceVpcEndpointAwsService.EC2_MESSAGES,
        ]);
    });

    test("adds all five with both features on", () => {
        expect(idsOf(edhConfig(true, true, true))).toEqual(Object.keys(EDH_ENDPOINTS));
    });

    test.each([
        ["the add-on and both features are off", false, false, false],
        ["the add-on is on with both features off", true, false, false],
        ["AMI builds are on without the add-on", false, false, true],
    ])("adds none when %s", (_case, enabled, containers, amiBuilds) => {
        expect(idsOf(edhConfig(enabled, containers, amiBuilds))).toEqual([]);
    });
});

describe("the VPC stack with the EDH endpoints", () => {
    const both = vpcEndpoints(withBothFeatures);

    test.each(Object.entries(EDH_ENDPOINTS))(
        "emits %s once, with a logical id built from that id, for the %s service",
        (id, suffix) => {
            const matching = both.filter((e) => new RegExp(`^${id}[0-9A-F]{8}$`).test(e.logicalId));
            expect(matching).toHaveLength(1);
            expect(endsWithService(matching[0].service, suffix)).toBe(true);
            expect(both.filter((e) => endsWithService(e.service, suffix))).toHaveLength(1);
        }
    );

    test("with a pipeline that creates the Batch endpoint, the VPC still has one", () => {
        const enablePipeline = (c: any) => {
            c.app.pipelines.usePreview3dThumbnail.enabled = true;
        };
        // Control: the pipeline block creates the Batch endpoint by itself.
        const pipelineOnly = vpcEndpoints(enablePipeline);
        expect(pipelineOnly.filter((e) => endsWithService(e.service, "batch"))).toHaveLength(1);
        const bothWithPipeline = (c: any) => {
            withBothFeatures(c);
            enablePipeline(c);
        };
        // Control: the add-on asks for the Batch endpoint in this configuration as well.
        expect(idsOf(buildTemplateConfig("commercial", bothWithPipeline))).toEqual(
            Object.keys(EDH_ENDPOINTS)
        );
        const withPipeline = vpcEndpoints(bothWithPipeline);
        const batch = withPipeline.filter((e) => endsWithService(e.service, "batch"));
        expect(batch).toHaveLength(1);
        expect(batch[0].logicalId).toMatch(/^BatchEndpoint[0-9A-F]{8}$/);
    });

    test("with the add-on off, the VPC has none of the EDH endpoints", () => {
        const stock = vpcEndpoints(() => undefined);
        const found = stock.filter((e) =>
            Object.values(EDH_ENDPOINTS).some((suffix) => endsWithService(e.service, suffix))
        );
        expectAbsent("EDH interface endpoints with the add-on off", found, {
            description: "the stock VPC stack emits interface endpoints at all",
            count: stock.length,
        });
    });
});
