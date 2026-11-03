/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `npm run check.quotas` counts the Region's CloudTrail trails before a deploy that creates one.
 * AWS CloudTrail allows five trails per Region; a fresh deploy into a Region that already has five
 * fails on the stack's trail after most of the core stack has been created.
 */

import {
    checkCloudTrailTrails,
    CLOUDTRAIL_TRAILS_PER_REGION,
    ExecFn,
} from "../../scripts/assertDeployQuotas";

const REGION = "us-east-1";
const CONFIG = { name: "vams", app: { baseStackName: "demo", addStackCloudTrailLogs: true } };
const CORE_STACK = "vams-core-demo-us-east-1";

const trails = (n: number, homeRegion = REGION) =>
    Array.from({ length: n }, (_v, i) => ({ Name: `trail-${i}`, HomeRegion: homeRegion }));

interface Fake {
    trailList?: object[];
    describeFails?: boolean;
    /** Physical ids of the core stack's trails; undefined means the stack does not exist. */
    stackTrails?: string[];
    stackFails?: boolean;
}

function fakeExec(fake: Fake): { exec: ExecFn; calls: string[][] } {
    const calls: string[][] = [];
    const exec: ExecFn = (_file, args) => {
        calls.push(args);
        if (args[0] === "cloudtrail") {
            if (fake.describeFails) throw new Error("AccessDeniedException");
            return JSON.stringify({ trailList: fake.trailList ?? [] });
        }
        if (args[0] === "cloudformation") {
            expect(args).toContain(CORE_STACK);
            if (fake.stackFails) throw Object.assign(new Error("x"), { stderr: "AccessDenied" });
            if (fake.stackTrails === undefined) {
                throw Object.assign(new Error("x"), {
                    stderr: `An error occurred (ValidationError): Stack with id ${CORE_STACK} does not exist`,
                });
            }
            return JSON.stringify(fake.stackTrails);
        }
        throw new Error(`unexpected command ${args.join(" ")}`);
    };
    return { exec, calls };
}

describe("checkCloudTrailTrails", () => {
    test("the limit is five trails per Region", () => {
        expect(CLOUDTRAIL_TRAILS_PER_REGION).toBe(5);
    });

    test("five foreign trails and no existing stack fails with the remedy", () => {
        const { exec } = fakeExec({ trailList: trails(5) });
        const r = checkCloudTrailTrails(CONFIG, REGION, exec, {});
        expect(r.status).toBe("FAIL");
        expect(r.message).toContain("app.addStackCloudTrailLogs");
        expect(r.message).toContain("5");
    });

    test("four trails pass without looking up the stack", () => {
        const { exec, calls } = fakeExec({ trailList: trails(4) });
        expect(checkCloudTrailTrails(CONFIG, REGION, exec, {}).status).toBe("OK");
        expect(calls.some((a) => a[0] === "cloudformation")).toBe(false);
    });

    test("five trails including the stack's own trail pass (an upgrade)", () => {
        const { exec } = fakeExec({ trailList: trails(5), stackTrails: ["vams-core-demo-trail"] });
        expect(checkCloudTrailTrails(CONFIG, REGION, exec, {}).status).toBe("OK");
    });

    test("trails homed in another Region do not count", () => {
        const { exec } = fakeExec({ trailList: [...trails(4), ...trails(3, "eu-west-1")] });
        expect(checkCloudTrailTrails(CONFIG, REGION, exec, {}).status).toBe("OK");
    });

    test("shadow trails are excluded from the listing", () => {
        const { exec, calls } = fakeExec({ trailList: trails(1) });
        checkCloudTrailTrails(CONFIG, REGION, exec, {});
        expect(calls[0]).toContain("--no-include-shadow-trails");
    });

    test("addStackCloudTrailLogs false skips without calling AWS", () => {
        const { exec, calls } = fakeExec({ trailList: trails(5) });
        const config = { ...CONFIG, app: { ...CONFIG.app, addStackCloudTrailLogs: false } };
        expect(checkCloudTrailTrails(config, REGION, exec, {}).status).toBe("SKIP");
        expect(calls).toEqual([]);
    });

    test("addStackCloudTrailLogs absent defaults to creating a trail", () => {
        const { exec } = fakeExec({ trailList: trails(5) });
        const config = { name: "vams", app: { baseStackName: "demo" } };
        expect(checkCloudTrailTrails(config, REGION, exec, {}).status).toBe("FAIL");
    });

    test("an unreadable trail list is UNKNOWN, not a pass", () => {
        const { exec } = fakeExec({ describeFails: true });
        const r = checkCloudTrailTrails(CONFIG, REGION, exec, {});
        expect(r.status).toBe("UNKNOWN");
        expect(r.message).toContain("cloudtrail:DescribeTrails");
    });

    test("five trails with an unreadable stack is UNKNOWN", () => {
        const { exec } = fakeExec({ trailList: trails(5), stackFails: true });
        expect(checkCloudTrailTrails(CONFIG, REGION, exec, {}).status).toBe("UNKNOWN");
    });

    test("STACK_NAME supplies the base stack name when the file has none", () => {
        const { exec } = fakeExec({ trailList: trails(5), stackTrails: ["t"] });
        const config = { name: "vams", app: {} };
        expect(checkCloudTrailTrails(config, REGION, exec, { STACK_NAME: "demo" }).status).toBe(
            "OK"
        );
    });
});
