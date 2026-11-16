/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The web ALB name is cut to Elastic Load Balancing's 32-character limit, and a name must not end
 * with a hyphen. `getConfig()` makes `baseStackName` `<base>-<region>`, so a 7-character base in
 * us-gov-west-1 puts the hyphen before `WebAppALB` exactly at position 32 and the deploy is rejected.
 */

import { albLoadBalancerName } from "../../lib/nestedStacks/staticWebApp/constructs/alb-s3-website-albDeploy-construct";
import { synthTemplate } from "../support/templateSynth";

const ELB_NAME = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,30}[A-Za-z0-9])?$/;

describe("albLoadBalancerName", () => {
    test("drops the hyphen the 32-character cut lands on", () => {
        const name = albLoadBalancerName("vams", "sandbox-us-gov-west-1");
        expect(name).toBe("vams-core-sandbox-us-gov-west-1");
        expect(name).toMatch(ELB_NAME);
    });

    test("drops every trailing hyphen the cut leaves", () => {
        const name = albLoadBalancerName("vams", "abcdefghijklmnopqrs--");
        expect(name).toBe("vams-core-abcdefghijklmnopqrs");
        expect(name).toMatch(ELB_NAME);
    });

    test("a name that does not end on a hyphen is unchanged", () => {
        expect(albLoadBalancerName("vams", "prod-us-gov-west-1")).toBe(
            "vams-core-prod-us-gov-west-1-Web"
        );
        expect(albLoadBalancerName("vams", "a")).toBe("vams-core-a-WebAppALB");
    });

    test.each([
        "x",
        "prod-us-gov-west-1",
        "sandbox-us-gov-west-1",
        "abcdefghijklmnopqrstuvwxyz-eu-west-1",
        "a-b-c-d-e-f-g-h-i-j-k-l-m-n-o-p",
    ])("%s gives a valid load balancer name", (base) => {
        const name = albLoadBalancerName("vams", base);
        expect(name.length).toBeLessThanOrEqual(32);
        expect(name).toMatch(ELB_NAME);
    });
});

describe("govcloud synth with a base stack name the cut lands on a hyphen", () => {
    test("LoadBalancerName has no trailing hyphen", () => {
        const synth = synthTemplate("govcloud", {
            mutate: (c) => {
                c.app.baseStackName = "sandbox-us-gov-west-1";
            },
            mutateKey: "alb-name-trailing-hyphen",
        });
        const names = synth
            .ofType("AWS::ElasticLoadBalancingV2::LoadBalancer")
            .map((r) => (r.properties as any).Name);
        expect(names).toEqual(["vams-core-sandbox-us-gov-west-1"]);
    });
});
