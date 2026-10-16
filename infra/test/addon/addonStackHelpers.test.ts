/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Publishing an add-on or custom resource name refuses a key another lane owns.
 *
 * Both lanes ship empty, so the cross-lane cases add a probe category to the other lane's registry
 * for the duration of one test and remove it afterwards.
 */

import * as cdk from "aws-cdk-lib";
import { Template } from "aws-cdk-lib/assertions";
import { ADDON_RESOURCE_PARAM_KEYS } from "../../common/addonResourceParamKeys";
import { CUSTOM_RESOURCE_PARAM_KEYS } from "../../common/customResourceParamKeys";
import { RESOURCE_PARAM_KEYS } from "../../common/resourceParamKeys";
import { publishAddonResourceName } from "../../lib/nestedStacks/addon/addonStackHelpers";
import { publishCustomResourceName } from "../../lib/nestedStacks/custom/customStackHelpers";
import { newTestApp } from "../support/testApp";

const config = { resourceNamesSSMParamPrefix: "/vams-test/resourceNames" } as any;
const PROBE_CATEGORY = "laneCollisionProbe";

const stackFor = () => new cdk.Stack(newTestApp(), "LaneNameStack");

/** Run `body` with `key` declared in `registry` under a probe category, then remove it. */
function withDeclared(
    registry: Record<string, Record<string, string>>,
    key: string,
    body: () => void
): void {
    registry[PROBE_CATEGORY] = { probe: key };
    try {
        body();
    } finally {
        delete registry[PROBE_CATEGORY];
    }
}

describe("publishAddonResourceName", () => {
    test("publishes the value under the deployment's resource-name prefix", () => {
        const stack = stackFor();
        publishAddonResourceName(stack, config, "addon/dynamoTables/probeStorage", "probe");
        Template.fromStack(stack).hasResourceProperties("AWS::SSM::Parameter", {
            Name: "/vams-test/resourceNames/addon/dynamoTables/probeStorage",
            Value: "probe",
        });
    });

    test("rejects a key equal to a core resource-name key", () => {
        expect(() =>
            publishAddonResourceName(
                stackFor(),
                config,
                RESOURCE_PARAM_KEYS.dynamoTables.assetStorage,
                "shadow"
            )
        ).toThrow(/publishAddonResourceName: .* is a core VAMS resource-name key/);
    });

    test("rejects a key the custom lane declares", () => {
        withDeclared(CUSTOM_RESOURCE_PARAM_KEYS, "custom/probe", () => {
            expect(() =>
                publishAddonResourceName(stackFor(), config, "custom/probe", "shadow")
            ).toThrow(/publishAddonResourceName: "custom\/probe" is declared as a custom/);
        });
    });

    test("rejects an empty key", () => {
        expect(() => publishAddonResourceName(stackFor(), config, "", "x")).toThrow(/non-empty/);
    });
});

describe("publishCustomResourceName and the add-on lane", () => {
    test("rejects a key the add-on lane declares", () => {
        withDeclared(ADDON_RESOURCE_PARAM_KEYS, "addon/probe", () => {
            expect(() =>
                publishCustomResourceName(stackFor(), config, "addon/probe", "shadow")
            ).toThrow(/publishCustomResourceName: "addon\/probe" is declared as an add-on/);
        });
    });

    test("[control] the same key is accepted once no lane declares it", () => {
        expect(() =>
            publishCustomResourceName(stackFor(), config, "addon/probe", "value")
        ).not.toThrow();
        expect(ADDON_RESOURCE_PARAM_KEYS[PROBE_CATEGORY]).toBeUndefined();
    });
});
