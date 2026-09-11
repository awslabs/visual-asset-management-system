/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Amazon Location Service API key must not survive its own stack and block the next deployment.
 *
 * The reported failure, twice, from a customer's first deployment: any unrelated failure rolls the stack
 * back, the retained API key stays behind, and because its name is deterministic the retry fails
 * immediately at changeset validation with an already-exists error naming a resource the operator was
 * never working on.
 *
 * The fix keeps the name and changes the lifecycle, which is the opposite of the obvious approach and is
 * deliberate. Three facts drove it, each checked rather than assumed:
 *
 *   1. `keyName` is REQUIRED on `AWS::Location::APIKey` (`readonly keyName: string` in the CDK type), so
 *      the name cannot simply be dropped and left to CloudFormation.
 *   2. `generateUniqueNameHash` — the pattern used for other unique names in this repository — is
 *      `sha1(stackName + accountId + resourceIdentifier)` and therefore DETERMINISTIC. A retry of the
 *      same deployment would produce the same name and collide with its own orphan exactly as before, so
 *      that route does not fix this defect.
 *   3. The retain policy cited a 90-day wait before an API key can be deleted. That applies to a key
 *      DEPRECATED by being given a past expiry, not to one created with `noExpiry`. Measured live in
 *      us-west-2: a `noExpiry` key deleted immediately with no force flag, both unused and after
 *      serving a real `geo-maps` request.
 *
 * So the name stays — which is what makes this change safe for existing deployments, since `keyName` is
 * the property that would force a replacement — and the key is deleted with the stack.
 *
 * Nothing downstream depends on the literal name: the construct publishes the key's ARN to SSM, and
 * `backend/handlers/config/configService.py` recovers the name from that ARN with
 * `api_key_arn.split('/')[-1]` before calling `describe_key`. That indirection is asserted here too,
 * because it is what makes the name an implementation detail rather than a contract.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { SynthResult, synthTemplate } from "../support/templateSynth";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

// Restored to DELEGATING after each test, never cleared: mockReset() would leave readFileSync returning
// undefined for every path, which breaks the synthTemplate() harness by making it read nothing rather
// than by failing outright. See configPartitionValidation.test.ts for why jest.resetModules() is barred.
const serveConfig = (configJson: unknown) => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(
        (path: string, ...rest: unknown[]) => {
            if (typeof path === "string" && path.endsWith("config.json")) {
                return JSON.stringify(configJson);
            }
            return realReadFileSync(path, ...rest);
        }
    );
};

afterEach(() => {
    (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
});

/** The one template shape that has Amazon Location Service enabled; it is commercial-only. */
const LOCATION_TEMPLATE = "commercial" as const;

describe("Location Service API key lifecycle", () => {
    test("the key is deleted with the stack rather than retained", () => {
        // The whole point: a retained key is what blocked the retry.
        const synth = synthTemplate(LOCATION_TEMPLATE);
        const keys = synth.ofType("AWS::Location::APIKey");
        expect(keys).toHaveLength(1);
        expect(keys[0].raw.DeletionPolicy).toBe("Delete");
        expect(keys[0].raw.UpdateReplacePolicy).toBe("Delete");
    });

    test("ForceDelete is set, so the delete cannot be refused on an expiry condition", () => {
        // The documented bypass. Belt and braces over the measured behaviour, because the measurement
        // was through the Location API directly and the deletion here goes through CloudFormation's
        // resource handler.
        const synth = synthTemplate(LOCATION_TEMPLATE);
        const key = synth.ofType("AWS::Location::APIKey")[0];
        expect(key.properties.ForceDelete).toBe(true);
    });

    test("ForceUpdate is set, so an update to a live key is not rejected", () => {
        // Not optional and not defensive. AWS Location Service refuses ANY update to a key whose maps
        // have been loaded in the last seven days — which is every live deployment — with "you must set
        // 'ForceUpdate' to true to confirm this change". A deployment carrying forceDelete without this
        // rolled the whole core stack back. The equivalent `aws location update-key` CLI call succeeds
        // without the flag, so a CLI measurement is not evidence about the CloudFormation handler.
        const synth = synthTemplate(LOCATION_TEMPLATE);
        const key = synth.ofType("AWS::Location::APIKey")[0];
        expect(key.properties.ForceUpdate).toBe(true);
    });

    test("the key still has no expiry, so map access does not lapse", () => {
        // Guards the other direction: making the key deletable must not have made it expiring.
        const synth = synthTemplate(LOCATION_TEMPLATE);
        const key = synth.ofType("AWS::Location::APIKey")[0];
        expect(key.properties.NoExpiry).toBe(true);
        expect(key.properties.ExpireTime).toBeUndefined();
    });

    test("the name is unchanged, which is what keeps this safe for existing deployments", () => {
        // `keyName` is the property that requires replacement. If a change ever alters it, every
        // existing deployment replaces its key on upgrade — so the shape is pinned here rather than
        // left to review.
        const synth = synthTemplate(LOCATION_TEMPLATE);
        const key = synth.ofType("AWS::Location::APIKey")[0];
        expect(SynthResult.flatten(key.properties.KeyName)).toMatch(/^vams-location-api-key-/);
    });

    test("the name is built from the configuration name and the stack name", () => {
        // Multi-deployment safety, which is the reason a fixed name is acceptable at all. Asserted
        // against what the harness configures rather than against a Region string: the T1 harness sets
        // `baseStackName` directly (`t1-<template>`), so it bypasses the getConfig() step that appends
        // the Region. The Region reaches the name THROUGH baseStackName, and that step is pinned by its
        // own case below rather than inferred from this one.
        const synth = synthTemplate(LOCATION_TEMPLATE);
        const key = synth.ofType("AWS::Location::APIKey")[0];
        const name = SynthResult.flatten(key.properties.KeyName);
        expect(name).toBe(`vams-location-api-key-vams-t1-${LOCATION_TEMPLATE}`);
    });

    test("getConfig() appends the Region to baseStackName, so the name is Region-scoped", () => {
        // The other half of multi-deployment safety, and the half the synth harness cannot show. Without
        // this, two deployments sharing a configuration name and stack name in different Regions would
        // produce the same key name — and Amazon Location Service names are per-Region, so the clash
        // would only appear on the second Region's deploy. The live key on the development deployment is
        // `vams-location-api-key-vams-prod5-us-west-2`, which is this rule at work.
        const config = JSON.parse(JSON.stringify(commercialTemplate));
        config.env.region = "eu-central-1";
        config.env.account = "123456789012";
        config.app.baseStackName = "prod";
        serveConfig(config);
        const resolved = Config.getConfig(newTestApp());
        expect(resolved.app.baseStackName).toBe("prod-eu-central-1");
    });

    test("the key's ARN — not its name — is what gets published for consumers", () => {
        // The indirection that makes the name an implementation detail. The SSM parameter carries the
        // ARN, and the backend recovers the name from it at run time, so a future rename would need no
        // code change anywhere. If this ever became a name instead of an ARN, the name would turn into
        // a contract and the reasoning above would stop holding.
        const synth = synthTemplate(LOCATION_TEMPLATE);
        const params = synth
            .ofType("AWS::SSM::Parameter")
            .filter((p) => /location/i.test(SynthResult.flatten(p.properties.Name ?? "")));
        expect(params.length).toBeGreaterThan(0);
        const locationParam = params.find((p) =>
            /apiKeyArn/i.test(SynthResult.flatten(p.properties.Name ?? ""))
        );
        expect(locationParam).toBeDefined();
        // The value must resolve from the key's ARN attribute, not from its name.
        expect(JSON.stringify(locationParam!.properties.Value)).toContain("KeyArn");
    });

    test("Location Service is absent from the restricted-partition templates", () => {
        // The positive control for the assertions above being about a resource that is conditionally
        // created: Amazon Location Service is not offered in these partitions, and getConfig() requires
        // useLocationService to be off there. If a key appeared here, the commercial-only assumption
        // this suite rests on would be wrong.
        for (const template of ["govcloud", "eusovereign"] as const) {
            expect(synthTemplate(template).ofType("AWS::Location::APIKey")).toHaveLength(0);
        }
    });
});
