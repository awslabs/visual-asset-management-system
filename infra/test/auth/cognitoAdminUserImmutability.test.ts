/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Amazon Cognito admin user is keyed on its username, so `app.adminUserId` and
 * `app.adminEmailAddress` are immutable after the first deployment. This suite pins the two guards that
 * make that survivable.
 *
 * What happens without them was observed live on 2026-08-26 against `vams-core-prod5-us-west-2`: the
 * local configuration carried `adminUserId: "scheurik"` while the deployed resource owned
 * `Username: "scheurik@amazon.com"`. Same logical id, so CloudFormation treated it as a replacement —
 * and because a separately-created `scheurik` already existed in the pool, the create failed with
 * "User with name scheurik already exists in UserPool", the AuthBuilder nested stack failed, and the
 * whole core stack rolled back 17 nested stacks after roughly fifteen minutes. Had the username NOT
 * existed, the deployment would instead have succeeded while deleting the previous administrator.
 *
 * Two guards, and neither can prevent the replacement — only make it non-destructive and visible:
 *
 *   1. `RemovalPolicy.RETAIN` on the user, so a replacement orphans the previous identity instead of
 *      deleting it. Asserted as `UpdateReplacePolicy` AND `DeletionPolicy` on the emitted resource,
 *      because the destructive case is the *replace*, and CloudFormation reads that from
 *      `UpdateReplacePolicy` rather than from `DeletionPolicy`.
 *   2. A `getConfig()` warning when the two fields differ, plus rejection of a username Amazon Cognito
 *      itself would refuse. The rejection is deliberately limited to what Cognito rejects, so it can
 *      never refuse a value that is already deployed and working — a validation that broke an existing
 *      customer's deployment would be worse than the defect.
 *
 * The retain policy is asserted against the real synthesized auth stack rather than a hand-built one,
 * so it cannot pass on a construct that the app does not actually use.
 */

import * as fs from "fs";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";
import { newTestApp } from "../support/testApp";
import { synthTemplate } from "../support/templateSynth";

const realReadFileSync = jest.requireActual("fs").readFileSync;

jest.mock("fs", () => {
    const actual = jest.requireActual("fs");
    return { ...actual, readFileSync: jest.fn(actual.readFileSync) };
});

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

// See configPartitionValidation.test.ts: jest.resetModules() must NOT be used here — it would create a
// second fs mock instance and make every "does not throw" assertion pass against the real config.json.
const withAdmin = (adminUserId: unknown, adminEmailAddress?: unknown) => {
    const config = JSON.parse(JSON.stringify(commercialTemplate));
    config.env.region = "us-west-2";
    config.env.account = "123456789012";
    config.app.baseStackName = "vamstest";
    config.app.adminUserId = adminUserId;
    config.app.adminEmailAddress = adminEmailAddress ?? adminUserId;
    serveConfig(config);
    return () => Config.getConfig(newTestApp());
};

describe("Cognito admin user immutability guards", () => {
    let warnSpy: jest.SpyInstance;

    beforeEach(() => {
        warnSpy = jest.spyOn(console, "warn").mockImplementation(() => undefined);
    });

    afterEach(() => {
        warnSpy.mockRestore();
        // Restored to DELEGATING, not cleared. mockReset() would leave readFileSync returning undefined
        // for every path, which breaks the synthTemplate() harness in any test that runs afterwards —
        // and it would break it by making the synth read nothing rather than by failing outright.
        (fs.readFileSync as unknown as jest.Mock).mockImplementation(realReadFileSync);
    });

    const adminWarnings = () =>
        warnSpy.mock.calls.map((c) => String(c[0])).filter((m) => m.includes("adminUserId"));

    describe("the user survives a replacement", () => {
        test("the admin user carries UpdateReplacePolicy and DeletionPolicy Retain", () => {
            // UpdateReplacePolicy is the load-bearing one: the destructive path is a replacement
            // triggered by a username change, not a stack deletion.
            const synth = synthTemplate("commercial");
            const users = synth.ofType("AWS::Cognito::UserPoolUser");
            expect(users.length).toBeGreaterThan(0);
            for (const user of users) {
                expect(user.raw.UpdateReplacePolicy).toBe("Retain");
                expect(user.raw.DeletionPolicy).toBe("Retain");
            }
        });

        test("the retain policy did not change the resource's properties", () => {
            // A removal policy is a resource ATTRIBUTE. If adding it had altered a property, existing
            // deployments would see a replacement on upgrade — the very outcome being guarded against.
            const synth = synthTemplate("commercial");
            const users = synth.ofType("AWS::Cognito::UserPoolUser");
            for (const user of users) {
                expect(Object.keys(user.properties)).toEqual(
                    expect.arrayContaining(["Username", "UserPoolId"])
                );
                expect(user.properties).not.toHaveProperty("UpdateReplacePolicy");
            }
        });
    });

    describe("getConfig() rejects only what Cognito rejects", () => {
        test("a username matching the email is accepted with no warning", () => {
            // The template default shape, and the positive control: without it, every "warns" case
            // below would be satisfied by a build that always warns.
            const run = withAdmin("admin@example.com", "admin@example.com");
            expect(run).not.toThrow();
            run();
            expect(adminWarnings()).toEqual([]);
        });

        test("a plain username differing from the email warns about immutability", () => {
            const run = withAdmin("administrator", "admin@example.com");
            expect(run).not.toThrow();
            run();
            expect(adminWarnings().join(" ")).toMatch(/immutable after the first deployment/);
        });

        test("a username containing whitespace is rejected", () => {
            // Cognito refuses this, so rejecting it cannot break a working deployment.
            expect(withAdmin("admin user")).toThrow(/contains whitespace/);
        });

        test("a username longer than Cognito's limit is rejected and names the limit", () => {
            const tooLong = "a".repeat(Config.COGNITO_USERNAME_MAX_LENGTH + 1);
            expect(withAdmin(tooLong)).toThrow(
                new RegExp(`Amazon Cognito allows at most ${Config.COGNITO_USERNAME_MAX_LENGTH}`)
            );
        });

        test("a username exactly at the limit is accepted", () => {
            // The boundary, so the check cannot drift into rejecting a legal value.
            const atLimit = "a".repeat(Config.COGNITO_USERNAME_MAX_LENGTH);
            expect(withAdmin(atLimit, atLimit)).not.toThrow();
        });

        test("an unusual but Cognito-legal username is accepted", () => {
            // Backwards compatibility: punctuation and non-ASCII letters are inside Cognito's username
            // character class, so a deployment already using one must keep deploying.
            expect(withAdmin("admin.o'brien+vams", "admin@example.com")).not.toThrow();
        });
    });
});
