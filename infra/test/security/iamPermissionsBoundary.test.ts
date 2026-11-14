/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * `environments.aws.PermissionBoundaryArn` (infra/cdk.json) sets an IAM permissions boundary on every
 * IAM role the app creates (#431).
 *
 * - T1: with a boundary configured, every `AWS::IAM::Role` in the root and every nested template of
 *   each shipped config template carries exactly that boundary. With none configured, no role carries
 *   a `PermissionsBoundary` property at all (no `AWS::NoValue` placeholder either), so a boundary
 *   applied by another mechanism is not removed.
 * - Role names do not change with the boundary setting: a changed `RoleName` replaces the role.
 * - The roles CDK adds while preparing a cross-Region reference (the export reader and writer
 *   providers, created after aspects have run) are bounded too.
 * - `getConfig()` rejects a malformed or other-partition boundary ARN.
 */

import * as cdk from "aws-cdk-lib";
import * as ssm from "aws-cdk-lib/aws-ssm";
import cdkJson from "../../cdk.json";
import commercialTemplate from "../../config/config.template.commercial.json";
import * as Config from "../../config/config";
import { IamRoleTransform } from "../../lib/aspects/iam-role-transform.aspect";
import { ALL_TEMPLATES, SynthResult, TemplateName, synthTemplate } from "../support/templateSynth";
import { newTestApp } from "../support/testApp";

// Six full-app synths, ~20-30 s each.
jest.setTimeout(900_000);

const PARTITION: Record<TemplateName, string> = {
    commercial: "aws",
    govcloud: "aws-us-gov",
    eusovereign: "aws-eusc",
};

const boundaryArn = (name: TemplateName) =>
    `arn:${PARTITION[name]}:iam::123456789012:policy/vams-test-boundary`;

/** cdk.json's `environments` with only the boundary ARN replaced. */
function environmentsWithBoundary(arn: string): Record<string, unknown> {
    const environments = JSON.parse(JSON.stringify((cdkJson as any).context.environments));
    environments.aws.PermissionBoundaryArn = arn;
    return { environments };
}

const unbounded: Partial<Record<TemplateName, SynthResult>> = {};
const bounded: Partial<Record<TemplateName, SynthResult>> = {};
const synthUnbounded = (name: TemplateName) => (unbounded[name] ??= synthTemplate(name));
const synthBounded = (name: TemplateName) =>
    (bounded[name] ??= synthTemplate(name, {
        context: environmentsWithBoundary(boundaryArn(name)),
    }));

const roles = (s: SynthResult) => s.ofType("AWS::IAM::Role");

describe.each(ALL_TEMPLATES)("%s template", (name) => {
    test("control: the synth emits IAM roles in the root and in nested stacks", () => {
        const all = roles(synthUnbounded(name));
        const stacks = new Set(all.map((r) => r.stack));
        expect(all.length).toBeGreaterThan(50);
        expect(Array.from(stacks).some((s) => s.endsWith(".nested"))).toBe(true);
        expect(Array.from(stacks).some((s) => !s.endsWith(".nested"))).toBe(true);
    });

    test("with no boundary configured, no role carries a PermissionsBoundary property", () => {
        const s = synthUnbounded(name);
        expect(
            roles(s)
                .filter((r) => "PermissionsBoundary" in r.properties)
                .map((r) => `${r.stack}/${r.logicalId}`)
        ).toEqual([]);
    });

    // TEMPORARY-TEST: pins removal of the dead role-transform Conditions/Parameters (#431)
    test("no template declares the unused role-transform Conditions or Parameters", () => {
        const s = synthUnbounded(name);
        for (const tpl of Object.values<any>(s.templates)) {
            expect(Object.keys(tpl.Conditions ?? {})).not.toEqual(
                expect.arrayContaining(["conditionPrefix"])
            );
            expect(Object.keys(tpl.Conditions ?? {})).not.toEqual(
                expect.arrayContaining(["conditionBoundary"])
            );
            expect(Object.keys(tpl.Parameters ?? {})).not.toEqual(
                expect.arrayContaining(["rolePrefix"])
            );
            expect(Object.keys(tpl.Parameters ?? {})).not.toEqual(
                expect.arrayContaining(["permissionBoundaryArn"])
            );
        }
    });

    test("with a boundary configured, every role in every template carries exactly that boundary", () => {
        const s = synthBounded(name);
        expect(roles(s).length).toBe(roles(synthUnbounded(name)).length);
        expect(
            roles(s)
                .filter((r) => r.properties.PermissionsBoundary !== boundaryArn(name))
                .map(
                    (r) =>
                        `${r.stack}/${r.logicalId}: ${JSON.stringify(
                            r.properties.PermissionsBoundary
                        )}`
                )
        ).toEqual([]);
    });

    test("the boundary setting does not change any role's name", () => {
        const names = (s: SynthResult) =>
            Object.fromEntries(
                roles(s).map((r) => [`${r.stack}/${r.logicalId}`, r.properties.RoleName])
            );
        const before = names(synthUnbounded(name));
        // Every core-stack role keeps the deterministic name the role transform has always set.
        expect(Object.values(before).every((n) => typeof n === "string" && n.length > 0)).toBe(
            true
        );
        expect(names(synthBounded(name))).toEqual(before);
    });
});

describe("roles CDK adds during prepare (cross-Region references)", () => {
    const ARN = "arn:aws:iam::123456789012:policy/vams-test-boundary";

    /** A producer in us-east-1 and a consumer in us-west-2: CDK adds an export writer and reader. */
    function crossRegionApp(withBoundary: boolean): Record<string, any> {
        const app = newTestApp();
        const producer = new cdk.Stack(app, "Producer", {
            env: { account: "123456789012", region: "us-east-1" },
            crossRegionReferences: true,
        });
        const param = new ssm.StringParameter(producer, "Value", { stringValue: "x" });
        const consumer = new cdk.Stack(app, "Consumer", {
            env: { account: "123456789012", region: "us-west-2" },
            crossRegionReferences: true,
        });
        new cdk.CfnOutput(consumer, "Out", { value: param.parameterName });
        if (withBoundary) IamRoleTransform.applyPermissionsBoundary(app, ARN);
        const asm = app.synth();
        const out: Record<string, any> = {};
        for (const stack of asm.stacks) {
            for (const [id, r] of Object.entries<any>(stack.template.Resources ?? {})) {
                if (r.Type === "AWS::IAM::Role") out[`${stack.stackName}/${id}`] = r.Properties;
            }
        }
        return out;
    }

    test("control: without a boundary the export writer and reader roles exist and are unbounded", () => {
        const found = crossRegionApp(false);
        const ids = Object.keys(found);
        expect(ids.some((id) => id.startsWith("Producer/") && /ExportWriter/.test(id))).toBe(true);
        expect(ids.some((id) => id.startsWith("Consumer/") && /ExportReader/.test(id))).toBe(true);
        expect(Object.values(found).every((p) => p.PermissionsBoundary === undefined)).toBe(true);
    });

    test("with a boundary, the export writer and reader roles carry it", () => {
        const found = crossRegionApp(true);
        expect(Object.keys(found).length).toBeGreaterThanOrEqual(2);
        expect(
            Object.entries(found)
                .filter(([, p]) => p.PermissionsBoundary !== ARN)
                .map(([id]) => id)
        ).toEqual([]);
    });
});

describe("PermissionBoundaryArn validation", () => {
    test.each([
        ["", "aws"],
        [undefined, "aws"],
        ["arn:aws:iam::123456789012:policy/MyBoundary", "aws"],
        ["arn:aws:iam::123456789012:policy/team/boundaries/MyBoundary", "aws"],
        ["arn:aws:iam::aws:policy/PowerUserAccess", "aws"],
        ["arn:aws-us-gov:iam::123456789012:policy/MyBoundary", "aws-us-gov"],
        ["arn:aws-eusc:iam::123456789012:policy/MyBoundary", "aws-eusc"],
    ])("accepts %p in %s", (value, partition) => {
        expect(() => Config.validatePermissionBoundaryArn(value, partition)).not.toThrow();
    });

    test.each([
        ["MyBoundary"],
        ["arn:aws:iam::123456789012:role/MyBoundary"],
        ["arn:aws:iam:::policy/MyBoundary"],
        ["arn:aws:iam::123456789012:policy/"],
        [" arn:aws:iam::123456789012:policy/MyBoundary"],
        [42],
    ])("rejects malformed %p", (value) => {
        expect(() => Config.validatePermissionBoundaryArn(value, "aws")).toThrow(
            /PermissionBoundaryArn .*must be empty or an IAM managed policy ARN/
        );
    });

    test.each([
        ["arn:aws:iam::123456789012:policy/MyBoundary", "aws-us-gov"],
        ["arn:aws:iam::123456789012:policy/MyBoundary", "aws-eusc"],
        ["arn:aws-us-gov:iam::123456789012:policy/MyBoundary", "aws"],
    ])("rejects %p in the %s partition", (value, partition) => {
        expect(() => Config.validatePermissionBoundaryArn(value, partition)).toThrow(
            new RegExp(`targets the '${partition}' partition`)
        );
    });

    /** getConfig() over the commercial template, with `environments` set in the app context. */
    function getConfigWithBoundary(arn: string): Error | undefined {
        const cfg = JSON.parse(JSON.stringify(commercialTemplate)) as any;
        cfg.env.region = "us-east-1";
        cfg.env.account = "123456789012";
        cfg.app.baseStackName = "pb431";
        const nodeFs = jest.requireActual<typeof import("fs")>("fs");
        const realRead = nodeFs.readFileSync;
        const readSpy = jest
            .spyOn(nodeFs, "readFileSync")
            .mockImplementation((p: any, ...rest: any[]) =>
                typeof p === "string" && p.endsWith("config.json")
                    ? JSON.stringify(cfg)
                    : (realRead as any)(p, ...rest)
            );
        const warnSpy = jest.spyOn(console, "warn").mockImplementation(() => undefined);
        try {
            Config.getConfig(newTestApp({ context: environmentsWithBoundary(arn) }));
            return undefined;
        } catch (e) {
            return e as Error;
        } finally {
            warnSpy.mockRestore();
            readSpy.mockRestore();
        }
    }

    test("getConfig() accepts a boundary ARN in the deployment's partition", () => {
        expect(
            getConfigWithBoundary("arn:aws:iam::123456789012:policy/MyBoundary")
        ).toBeUndefined();
    });

    test("getConfig() rejects a malformed boundary ARN", () => {
        expect(getConfigWithBoundary("MyBoundary")?.message).toMatch(/PermissionBoundaryArn/);
    });

    test("getConfig() rejects a boundary ARN from another partition", () => {
        expect(
            getConfigWithBoundary("arn:aws-us-gov:iam::123456789012:policy/MyBoundary")?.message
        ).toMatch(/targets the 'aws' partition/);
    });
});
