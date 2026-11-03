/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Cross-Region Amazon S3 interface endpoints for external asset buckets in other Regions.
 *
 * The S3 gateway endpoint the VPC builder always adds serves the deployment Region only. When
 * `app.useGlobalVpc.addCrossRegionS3Endpoints` is on and an external asset bucket is in another
 * Region, the builder adds one interface endpoint per distinct bucket Region — service in the bucket
 * Region (`ServiceRegion`), endpoint in this VPC's isolated subnets, private DNS on, the shared VPC
 * endpoint security group — so in-VPC Lambdas and pipelines reach that Region's S3 hostnames without
 * internet egress. Nothing is emitted when the flag is off, when the VPC is off, or when every
 * bucket is in the deployment Region; a restricted-partition deployment with the flag off emits
 * none either (the positive control is the commercial endpoint).
 */

import { crossRegionS3ServiceName } from "../../lib/nestedStacks/vpc/vpcBuilder-nestedStack";
import { synthTemplate, expectAbsent, SynthResult, Resource } from "../support/templateSynth";

const crossRegionEndpoints = (s: SynthResult): Resource[] =>
    s.where("AWS::EC2::VPCEndpoint", (r) => "ServiceRegion" in r.properties);

const withVpcAndBuckets = (regions: string[], flag: boolean | undefined) => (c: any) => {
    c.app.useGlobalVpc.enabled = true;
    if (flag !== undefined) c.app.useGlobalVpc.addCrossRegionS3Endpoints = flag;
    c.app.assetBuckets.externalAssetBuckets = regions.map((region, i) => ({
        bucketArn: `arn:${c.env.partition}:s3:::remote-assets-${i}`,
        baseAssetsPrefix: "/",
        defaultSyncDatabaseId: `remote${i}`,
        bucketRegion: region,
    }));
    // In-VPC synth of the commercial template: a public Serverless collection is rejected with
    // the VPC on, so the collection is private here (an unrelated rule).
    c.app.openSearch.useServerless.allowPublic = false;
};

describe("crossRegionS3ServiceName", () => {
    test.each([
        ["us-east-1", "com.amazonaws.us-east-1.s3"],
        ["eu-west-1", "com.amazonaws.eu-west-1.s3"],
        ["us-gov-east-1", "com.amazonaws.us-gov-east-1.s3"],
        ["cn-north-1", "cn.com.amazonaws.cn-north-1.s3"],
        ["eusc-de-east-1", "eu.amazonaws.eusc-de-east-1.s3"],
    ])("%s -> %s", (region, expected) => {
        expect(crossRegionS3ServiceName(region)).toBe(expected);
    });
});

describe("cross-Region S3 interface endpoints (commercial)", () => {
    const synth = () =>
        synthTemplate("commercial", {
            mutateKey: "xregion-vpc-two-regions",
            mutate: withVpcAndBuckets(["eu-west-1", "ap-southeast-2", "eu-west-1"], true),
        });

    test("emits one endpoint per distinct cross-Region bucket Region", () => {
        const endpoints = crossRegionEndpoints(synth());
        expect(endpoints.map((r) => r.properties.ServiceRegion).sort()).toEqual([
            "ap-southeast-2",
            "eu-west-1",
        ]);
    });

    test("each endpoint names the S3 service in the bucket Region with private DNS", () => {
        for (const ep of crossRegionEndpoints(synth())) {
            const region = ep.properties.ServiceRegion;
            expect(ep.properties.ServiceName).toBe(`com.amazonaws.${region}.s3`);
            expect(ep.properties.VpcEndpointType).toBe("Interface");
            expect(ep.properties.PrivateDnsEnabled).toBe(true);
        }
    });

    test("each endpoint uses the isolated subnets and the shared VPC endpoint security group", () => {
        const s = synth();
        const [ssmEndpoint] = s.where("AWS::EC2::VPCEndpoint", (r) =>
            SynthResult.flatten(r.properties.ServiceName).endsWith(".ssm")
        );
        expect(ssmEndpoint).toBeDefined();
        for (const ep of crossRegionEndpoints(s)) {
            expect(ep.properties.SubnetIds).toEqual(ssmEndpoint.properties.SubnetIds);
            expect(ep.properties.SecurityGroupIds).toEqual(ssmEndpoint.properties.SecurityGroupIds);
            expect((ep.properties.SubnetIds as unknown[]).length).toBeGreaterThanOrEqual(2);
        }
    });

    test("the same-Region S3 gateway endpoint is still emitted alongside them", () => {
        const gateways = synth().where(
            "AWS::EC2::VPCEndpoint",
            (r) =>
                r.properties.VpcEndpointType === "Gateway" &&
                SynthResult.flatten(r.properties.ServiceName).endsWith(".s3")
        );
        expect(gateways).toHaveLength(1);
    });

    test("emits none when the flag is false (control: the SSM endpoint is there)", () => {
        const s = synthTemplate("commercial", {
            mutateKey: "xregion-vpc-flag-off",
            mutate: withVpcAndBuckets(["eu-west-1"], false),
        });
        expectAbsent("cross-Region S3 endpoint with the flag off", crossRegionEndpoints(s), {
            description: "the VPC builder emitted interface endpoints at all",
            count: s.countOfType("AWS::EC2::VPCEndpoint"),
        });
    });

    test("emits none when no bucket is in another Region (control: the SSM endpoint is there)", () => {
        const s = synthTemplate("commercial", {
            mutateKey: "xregion-vpc-same-region-bucket",
            mutate: withVpcAndBuckets(["us-east-1"], true),
        });
        expectAbsent(
            "cross-Region S3 endpoint with same-Region buckets only",
            crossRegionEndpoints(s),
            {
                description: "the VPC builder emitted interface endpoints at all",
                count: s.countOfType("AWS::EC2::VPCEndpoint"),
            }
        );
    });

    test("emits none when the VPC is off (control: the storage stack is there)", () => {
        const s = synthTemplate("commercial", {
            mutateKey: "xregion-no-vpc",
            mutate: (c) => {
                c.app.assetBuckets.externalAssetBuckets = [
                    {
                        bucketArn: "arn:aws:s3:::remote-assets-0",
                        baseAssetsPrefix: "/",
                        defaultSyncDatabaseId: "remote0",
                        bucketRegion: "eu-west-1",
                    },
                ];
            },
        });
        expectAbsent(
            "cross-Region S3 endpoint with the VPC off",
            s.ofType("AWS::EC2::VPCEndpoint"),
            {
                description: "the synth produced the storage stack",
                count: s.resources.filter((r) => /StorageResourcesBuilder/.test(r.stack)).length,
            }
        );
    });
});

describe("cross-Region S3 interface endpoints (restricted partitions, flag false)", () => {
    test.each([
        ["govcloud", "us-gov-east-1"],
        ["eusovereign", "eusc-de-west-1"],
    ] as const)("%s with the flag false emits no cross-Region endpoint", (name, bucketRegion) => {
        // getConfig() rejects the flag true here, so false is the only deployable value; the
        // shipped templates already run the VPC with endpoints, which is the positive control.
        const s = synthTemplate(name, {
            mutateKey: `xregion-vpc-${name}-flag-off`,
            mutate: (c) => {
                c.app.useGlobalVpc.addCrossRegionS3Endpoints = false;
                c.app.assetBuckets.externalAssetBuckets = [
                    {
                        bucketArn: `arn:${c.env.partition}:s3:::remote-assets-0`,
                        baseAssetsPrefix: "/",
                        defaultSyncDatabaseId: "remote0",
                        bucketRegion,
                    },
                ];
            },
        });
        expectAbsent(`cross-Region S3 endpoint in ${name}`, crossRegionEndpoints(s), {
            description: `${name} emitted interface endpoints at all`,
            count: s.countOfType("AWS::EC2::VPCEndpoint"),
        });
        // And the per-Region notification stack is still emitted: the flag governs the network
        // path only, not the notification plumbing.
        expect(s.resources.some((r) => /-xregion-/.test(r.stack))).toBe(true);
    });

    test("[control] the commercial synth with the flag on emits one", () => {
        const s = synthTemplate("commercial", {
            mutateKey: "xregion-vpc-two-regions",
            mutate: withVpcAndBuckets(["eu-west-1", "ap-southeast-2", "eu-west-1"], true),
        });
        expect(crossRegionEndpoints(s).length).toBeGreaterThan(0);
    });
});
