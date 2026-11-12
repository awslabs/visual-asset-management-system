/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The VPC that `app.addons.useEdhWorkstations.lambdaSubnets` "private" builds.
 *
 * An EDH cluster installed with `--deployment-mode public` has an internet-facing load balancer, which
 * resolves to public addresses, so the add-on's Lambdas reach it only from subnets that route through a
 * NAT gateway. With "private", the add-on asks the VPC builder for the public and private subnet groups
 * through `addonNeedsPrivateSubnets()` (the VPC builder's add-on lane), and the Lambdas that call EDH run
 * in the private subnets (`edhWorkstations.test.ts`).
 *
 * What is pinned here, on the commercial template, which turns on no pipeline that needs private
 * subnets, so the add-on is what adds them:
 *   * with the default "isolated", the add-on adds no public subnet, private subnet or NAT gateway;
 *   * with "private", the VPC has a public and a private subnet and a NAT gateway in each Availability
 *     Zone, and each private subnet routes 0.0.0.0/0 through one;
 *   * the isolated subnets keep their CIDR blocks, so switching an existing VAMS-created VPC to
 *     "private" adds subnets and replaces none;
 *   * the root key sync, which calls no EDH route, stays in the isolated subnets under
 *     `useForAllLambdas`;
 *   * a "private" placement in a VPC without private subnets fails the synth by name instead of
 *     placing the Lambdas in no subnet.
 */

import * as addonVpcEndpoints from "../../../lib/nestedStacks/addon/addonVpcEndpoints";
import {
    Resource,
    SynthResult,
    handlerOf,
    resolveReference,
    synthTemplate,
} from "../../support/templateSynth";
import {
    EDH_ROOT_KEY_SYNC_HANDLER,
    edhClientFunctions,
    edhFunctions,
    enableEdh,
    enableEdhPrivateSubnets,
} from "./support/edhSynth";

// Three full-app synths (isolated, private, private with useForAllLambdas) and one that fails at the
// add-on stack.
jest.setTimeout(600_000);

/** The `aws-cdk:subnet-type` tag value CDK writes on each subnet it creates. */
const subnetType = (subnet: Resource): string | undefined =>
    ((subnet.properties.Tags ?? []) as Array<{ Key: string; Value: unknown }>)
        .filter((t) => t.Key === "aws-cdk:subnet-type")
        .map((t) => String(t.Value))[0];

const subnetsOfType = (s: SynthResult, type: string): Resource[] =>
    s.ofType("AWS::EC2::Subnet").filter((subnet) => subnetType(subnet) === type);

/** The subnet tier tags of the subnets a Lambda is placed in. */
const placementOf = (s: SynthResult, fn: Resource): string[] =>
    (fn.properties.VpcConfig?.SubnetIds ?? []).map((subnetId: unknown) => {
        const subnet = resolveReference(s, fn.stack, subnetId);
        if (subnet === undefined || subnet.type !== "AWS::EC2::Subnet") {
            throw new Error(
                `${fn.logicalId}: subnet id ${JSON.stringify(subnetId)} resolves to no subnet`
            );
        }
        return subnetType(subnet) ?? "untagged";
    });

const isolatedCidrs = (s: SynthResult): string[] =>
    subnetsOfType(s, "Isolated")
        .map((subnet) => String(subnet.properties.CidrBlock))
        .sort();

describe("lambdaSubnets and the VPC that VAMS creates", () => {
    let isolated: SynthResult;
    let privateTier: SynthResult;
    let privateAllLambdas: SynthResult;

    beforeAll(() => {
        isolated = synthTemplate("commercial", {
            mutate: enableEdh,
            mutateKey: "edhWorkstationsEnabled",
        });
        privateTier = synthTemplate("commercial", {
            mutate: enableEdhPrivateSubnets,
            mutateKey: "edhWorkstationsPrivateSubnets",
        });
        privateAllLambdas = synthTemplate("commercial", {
            mutate: (c: Parameters<typeof enableEdhPrivateSubnets>[0]) => {
                enableEdhPrivateSubnets(c);
                c.app.useGlobalVpc.useForAllLambdas = true;
            },
            mutateKey: "edhWorkstationsPrivateSubnetsForAllLambdas",
        });
    });

    test("with lambdaSubnets private the VPC has a public and a private subnet and a NAT gateway in each Availability Zone, and with isolated none", () => {
        // Isolated, the default: the add-on adds nothing to the VPC.
        expect(subnetsOfType(isolated, "Isolated").length).toBeGreaterThan(0);
        expect(subnetsOfType(isolated, "Private")).toEqual([]);
        expect(subnetsOfType(isolated, "Public")).toEqual([]);
        expect(isolated.ofType("AWS::EC2::NatGateway")).toEqual([]);

        const azCount = subnetsOfType(privateTier, "Isolated").length;
        // The commercial template's baseline: two Availability Zones.
        expect(azCount).toBe(2);
        expect(subnetsOfType(privateTier, "Private")).toHaveLength(azCount);
        expect(subnetsOfType(privateTier, "Public")).toHaveLength(azCount);
        expect(privateTier.ofType("AWS::EC2::NatGateway")).toHaveLength(azCount);
        const natDefaultRoutes = privateTier
            .ofType("AWS::EC2::Route")
            .filter(
                (route) =>
                    route.properties.DestinationCidrBlock === "0.0.0.0/0" &&
                    route.properties.NatGatewayId !== undefined
            );
        expect(natDefaultRoutes).toHaveLength(azCount);
    });

    test("the isolated subnets keep their CIDR blocks when the private and public subnets are added", () => {
        const before = isolatedCidrs(isolated);
        expect(before.length).toBeGreaterThan(0);
        expect(isolatedCidrs(privateTier)).toEqual(before);
        // The new subnets exist and take address space the isolated subnets do not use.
        const added = [
            ...subnetsOfType(privateTier, "Private"),
            ...subnetsOfType(privateTier, "Public"),
        ].map((subnet) => String(subnet.properties.CidrBlock));
        expect(added).toHaveLength(2 * before.length);
        expect(added.filter((cidr) => before.includes(cidr))).toEqual([]);
    });

    test("under useForAllLambdas the root key sync stays in the isolated subnets while the EDH callers run in the private ones", () => {
        const s = privateAllLambdas;
        const sync = edhFunctions(s).filter((fn) => handlerOf(fn) === EDH_ROOT_KEY_SYNC_HANDLER);
        expect(sync).toHaveLength(1);
        const syncTiers = placementOf(s, sync[0]);
        expect(syncTiers.length).toBeGreaterThan(0);
        expect([...new Set(syncTiers)]).toEqual(["Isolated"]);
        const callers = edhClientFunctions(s);
        expect(callers.length).toBeGreaterThan(0);
        for (const fn of callers) {
            expect([handlerOf(fn), [...new Set(placementOf(s, fn))]]).toEqual([
                handlerOf(fn),
                ["Private"],
            ]);
        }
    });
});

describe("a private placement without private subnets", () => {
    test("fails the synth by name", () => {
        // The VPC builder is told no add-on needs private subnets, so the VPC it creates has none, as an
        // imported VPC would without optionalExternalPrivateSubnetIds if getConfig() were bypassed.
        const spy = jest
            .spyOn(addonVpcEndpoints, "addonNeedsPrivateSubnets")
            .mockReturnValue(false);
        try {
            expect(() =>
                synthTemplate("commercial", {
                    mutate: enableEdhPrivateSubnets,
                    mutateKey: "edhWorkstationsPrivateSubnetsWithoutPrivateSubnets",
                    sharedCache: false,
                })
            ).toThrow(/lambdaSubnets is "private", but the VAMS VPC has no private subnets/);
            expect(spy).toHaveBeenCalled();
        } finally {
            spy.mockRestore();
        }
    });
});
