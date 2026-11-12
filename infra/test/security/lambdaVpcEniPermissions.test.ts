/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * While `app.useGlobalVpc.enabled`, every Lambda execution role keeps `AWSLambdaVPCAccessExecutionRole`,
 * whether its function is in the VPC or not (#1347). CDK attaches that policy only while a function's
 * `vpc` is set, so before the fix turning `useForAllLambdas` off detached it from each role before
 * Lambda deleted the function's network interfaces, and the interfaces were orphaned.
 *
 * - Unit: `LambdaVpcEniPermissionsAspect` on a small stack: the policy on in-VPC and out-of-VPC roles
 *   once each, the function-code Deny only outside the VPC, imported roles untouched, role names and
 *   permissions boundaries unchanged.
 * - T1: between `useForAllLambdas` on and off (VPC on), no role loses the policy, and every function that
 *   is in the VPC with it on keeps the policy on its role with it off; outside the VPC the Deny is there.
 *   With no VPC nothing is added. The GovCloud and EU Sovereign templates (VPC on by default) get the
 *   same, with a partition-aware policy ARN. CDK Nag reports nothing for the new grants.
 */

import * as cdk from "aws-cdk-lib";
import * as ec2 from "aws-cdk-lib/aws-ec2";
import * as iam from "aws-cdk-lib/aws-iam";
import * as lambda from "aws-cdk-lib/aws-lambda";
import { Template } from "aws-cdk-lib/assertions";
import { IamRoleTransform } from "../../lib/aspects/iam-role-transform.aspect";
import {
    LAMBDA_ENI_CHANGE_ACTIONS,
    LambdaVpcEniPermissionsAspect,
} from "../../lib/aspects/lambda-vpc-eni-permissions.aspect";
import {
    Resource,
    SynthResult,
    TemplateName,
    policiesAttachedTo,
    resolveReference,
    synthTemplate,
} from "../support/templateSynth";
import { newTestApp } from "../support/testApp";

// Up to six full-app synths.
jest.setTimeout(900_000);

const VPC_ACCESS = "iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole";
const DENY_SID = "DenyEniChangesByFunctionCode";

const vpcAccessCount = (role: Resource): number =>
    ((role.properties.ManagedPolicyArns ?? []) as any[]).filter((arn) =>
        SynthResult.flatten(arn).endsWith(VPC_ACCESS)
    ).length;

function statementsOfRole(s: SynthResult, role: Resource): any[] {
    const out = policiesAttachedTo(s, role.logicalId, role.stack).flatMap(
        (p) => (p.properties.PolicyDocument?.Statement ?? []) as any[]
    );
    for (const p of role.properties.Policies ?? [])
        out.push(...(p.PolicyDocument?.Statement ?? []));
    // Customer managed policies the role names in its own ManagedPolicyArns.
    for (const arn of (role.properties.ManagedPolicyArns ?? []) as any[]) {
        const policy = arn?.Ref
            ? s.resources.find((r) => r.stack === role.stack && r.logicalId === arn.Ref)
            : undefined;
        if (policy?.type === "AWS::IAM::ManagedPolicy") {
            out.push(...((policy.properties.PolicyDocument?.Statement ?? []) as any[]));
        }
    }
    return out;
}

const hasCodeDeny = (s: SynthResult, role: Resource) =>
    statementsOfRole(s, role).some((st) => st.Sid === DENY_SID);

const key = (r: Resource) => `${r.stack}/${r.logicalId}`;

/** Each Lambda function with the role it runs as, following cross-stack references. */
function functionRoles(s: SynthResult): { fn: Resource; role: Resource }[] {
    return s.ofType("AWS::Lambda::Function").map((fn) => {
        const role = resolveReference(s, fn.stack, fn.properties.Role);
        if (!role || role.type !== "AWS::IAM::Role") {
            throw new Error(`no role resolved for ${key(fn)}`);
        }
        return { fn, role };
    });
}

const inVpc = (fn: Resource) => (fn.properties.VpcConfig?.SubnetIds ?? []).length > 0;

/**
 * CDK builds these two handlers as raw `AWS::Lambda::Function` resources, not with `lambda.Function`, so the
 * aspect does not see them; neither is ever placed in a VPC.
 */
const CDK_LOW_LEVEL_HANDLERS =
    /^(CustomS3AutoDeleteObjectsCustomResourceProviderHandler|BucketNotificationsHandler)/;

/** Functions whose role lacks the policy, other than CDK's two low-level handlers. */
const withoutPolicy = (s: SynthResult) =>
    functionRoles(s)
        .filter((p) => !CDK_LOW_LEVEL_HANDLERS.test(p.fn.logicalId) && vpcAccessCount(p.role) !== 1)
        .map((p) => key(p.fn));

describe("LambdaVpcEniPermissionsAspect (unit)", () => {
    function build(opts: { aspect: boolean; roleTransform?: boolean }) {
        const app = newTestApp();
        const stack = new cdk.Stack(app, "S", {
            env: { account: "123456789012", region: "us-east-1" },
        });
        const vpc = new ec2.Vpc(stack, "Vpc", { maxAzs: 2, natGateways: 0 });
        const props = {
            runtime: lambda.Runtime.PYTHON_3_12,
            handler: "index.handler",
            code: lambda.Code.fromInline("def handler(e, c):\n    return None\n"),
        };
        new lambda.Function(stack, "InVpc", { ...props, vpc });
        new lambda.Function(stack, "OutOfVpc", props);
        const shared = new iam.Role(stack, "SharedRole", {
            assumedBy: new iam.ServicePrincipal("lambda.amazonaws.com"),
        });
        new lambda.Function(stack, "SharedInVpc", { ...props, vpc, role: shared });
        new lambda.Function(stack, "SharedOut", { ...props, role: shared });
        new lambda.Function(stack, "Imported", {
            ...props,
            role: iam.Role.fromRoleArn(
                stack,
                "ImportedRole",
                "arn:aws:iam::123456789012:role/external"
            ),
        });
        if (opts.roleTransform) {
            cdk.Aspects.of(stack).add(
                new IamRoleTransform("pfx", "arn:aws:iam::123456789012:policy/boundary")
            );
        }
        if (opts.aspect) {
            cdk.Aspects.of(stack).add(new LambdaVpcEniPermissionsAspect(), {
                priority: cdk.AspectPriority.MUTATING,
            });
        }
        return Template.fromStack(stack).toJSON().Resources as Record<string, any>;
    }

    const roleOf = (res: Record<string, any>, fnPrefix: string) => {
        const fn = Object.entries(res).find(
            ([id, r]) => id.startsWith(fnPrefix) && r.Type === "AWS::Lambda::Function"
        )![1];
        return fn.Properties.Role["Fn::GetAtt"][0] as string;
    };
    const vpcAccessOn = (res: Record<string, any>, roleId: string) =>
        (res[roleId].Properties.ManagedPolicyArns ?? []).filter((a: any) =>
            SynthResult.flatten(a).endsWith(VPC_ACCESS)
        ).length;
    const denyOn = (res: Record<string, any>, roleId: string) =>
        ((res[roleId].Properties.ManagedPolicyArns ?? []) as any[])
            .filter((a) => a?.Ref && res[a.Ref]?.Type === "AWS::IAM::ManagedPolicy")
            .flatMap((a) => res[a.Ref].Properties.PolicyDocument.Statement)
            .filter((st: any) => st.Sid === DENY_SID);

    test("without the aspect, CDK gives the policy only to the in-VPC function's role (the bug)", () => {
        const res = build({ aspect: false });
        expect(vpcAccessOn(res, roleOf(res, "InVpc"))).toBe(1);
        expect(vpcAccessOn(res, roleOf(res, "OutOfVpc"))).toBe(0);
    });

    test("every role carries the policy exactly once; only the out-of-VPC roles get the code Deny", () => {
        const res = build({ aspect: true });
        for (const fn of ["InVpc", "OutOfVpc", "SharedInVpc"]) {
            expect(vpcAccessOn(res, roleOf(res, fn))).toBe(1);
        }
        expect(denyOn(res, roleOf(res, "InVpc"))).toEqual([]);
        expect(denyOn(res, roleOf(res, "OutOfVpc"))).toEqual([
            {
                Sid: DENY_SID,
                Effect: "Deny",
                Action: [...LAMBDA_ENI_CHANGE_ACTIONS].sort(),
                Resource: "*",
                Condition: { Null: { "lambda:SourceFunctionArn": "false" } },
            },
        ]);
        // A role shared with a function outside the VPC carries one Deny, whatever the visit order.
        expect(denyOn(res, roleOf(res, "SharedInVpc"))).toHaveLength(1);
        // One Deny policy for the whole stack, not one per role.
        expect(Object.values(res).filter((r) => r.Type === "AWS::IAM::ManagedPolicy")).toHaveLength(
            1
        );
    });

    test("the Deny adds nothing to a role's inline policies", () => {
        const without = build({ aspect: false });
        const withIt = build({ aspect: true });
        const inline = (res: Record<string, any>) =>
            JSON.stringify(
                Object.entries(res)
                    .filter(([, r]) => r.Type === "AWS::IAM::Policy")
                    .map(([id, r]) => [id, r.Properties.PolicyDocument])
            );
        expect(inline(withIt)).toBe(inline(without));
    });

    test("the Deny leaves the read actions alone, which VAMS function code calls", () => {
        expect(LAMBDA_ENI_CHANGE_ACTIONS).not.toContain("ec2:DescribeNetworkInterfaces");
        expect(LAMBDA_ENI_CHANGE_ACTIONS).not.toContain("ec2:DescribeSubnets");
    });

    test("an imported role gets no policy attachment and no Deny", () => {
        const res = build({ aspect: true });
        const policies = Object.values(res).filter((r) => r.Type === "AWS::IAM::Policy");
        expect(
            policies.filter((p) => JSON.stringify(p.Properties.Roles ?? []).includes("external"))
        ).toEqual([]);
    });

    test("role names and permissions boundaries are the same with and without the aspect", () => {
        const pick = (res: Record<string, any>) =>
            Object.fromEntries(
                Object.entries(res)
                    .filter(([, r]) => r.Type === "AWS::IAM::Role")
                    .map(([id, r]) => [
                        id,
                        { name: r.Properties.RoleName, boundary: r.Properties.PermissionsBoundary },
                    ])
            );
        const without = pick(build({ aspect: false, roleTransform: true }));
        const withIt = pick(build({ aspect: true, roleTransform: true }));
        expect(Object.keys(without).length).toBeGreaterThan(2);
        expect(withIt).toEqual(without);
        for (const v of Object.values(withIt)) {
            expect(v.name).toMatch(/^pfx/);
            expect(v.boundary).toBe("arn:aws:iam::123456789012:policy/boundary");
        }
    });
});

const vpcCell = (useForAllLambdas: boolean) =>
    synthTemplate("commercial", {
        mutate: (c: any) => {
            c.app.useGlobalVpc.enabled = true;
            c.app.useGlobalVpc.useForAllLambdas = useForAllLambdas;
        },
        mutateKey: `eni1347-vpc-forAll-${useForAllLambdas}`,
    });

describe("commercial template, VPC on: useForAllLambdas true -> false (T1)", () => {
    test("no role loses AWSLambdaVPCAccessExecutionRole when useForAllLambdas turns off", () => {
        const on = vpcCell(true);
        const off = vpcCell(false);
        const offRoles = new Map(off.ofType("AWS::IAM::Role").map((r) => [key(r), r]));
        const withPolicy = on.ofType("AWS::IAM::Role").filter((r) => vpcAccessCount(r) > 0);
        expect(withPolicy.length).toBeGreaterThan(50);
        expect(
            withPolicy
                .filter((r) => !offRoles.has(key(r)) || vpcAccessCount(offRoles.get(key(r))!) !== 1)
                .map(key)
        ).toEqual([]);
    });

    test("every function that is in the VPC with useForAllLambdas on keeps the policy with it off", () => {
        const on = vpcCell(true);
        const off = vpcCell(false);
        const offByFn = new Map(functionRoles(off).map((p) => [key(p.fn), p]));
        const moved = functionRoles(on).filter((p) => inVpc(p.fn));
        expect(moved.length).toBeGreaterThan(50);
        const offPairs = moved.map((p) => offByFn.get(key(p.fn))!);
        expect(offPairs.filter((p) => !p).length).toBe(0);
        // Control: most of them leave the VPC with useForAllLambdas off.
        expect(offPairs.filter((p) => !inVpc(p.fn)).length).toBeGreaterThan(40);
        expect(offPairs.filter((p) => vpcAccessCount(p.role) !== 1).map((p) => key(p.fn))).toEqual(
            []
        );
    });

    test.each([false, true])(
        "useForAllLambdas %s: the code Deny is on the roles of functions outside the VPC only",
        (forAll) => {
            const s = vpcCell(forAll);
            const pairs = functionRoles(s);
            const outside = pairs.filter((p) => !inVpc(p.fn));
            const missing = outside
                .filter((p) => vpcAccessCount(p.role) > 0 && !hasCodeDeny(s, p.role))
                .map((p) => key(p.fn));
            expect(missing).toEqual([]);
            const insideRoles = new Set(pairs.filter((p) => inVpc(p.fn)).map((p) => key(p.role)));
            const outsideRoles = new Set(outside.map((p) => key(p.role)));
            const deniedInside = Array.from(insideRoles).filter(
                (k) =>
                    !outsideRoles.has(k) &&
                    hasCodeDeny(s, s.ofType("AWS::IAM::Role").find((r) => key(r) === k)!)
            );
            expect(deniedInside).toEqual([]);
        }
    );

    test.each([false, true])(
        "useForAllLambdas %s: every function's role carries the policy exactly once",
        (forAll) => {
            const s = vpcCell(forAll);
            expect(functionRoles(s).length).toBeGreaterThan(50);
            expect(withoutPolicy(s)).toEqual([]);
        }
    );

    test("no role names AWSLambdaVPCAccessExecutionRole twice", () => {
        for (const s of [vpcCell(true), vpcCell(false)]) {
            expect(
                s
                    .ofType("AWS::IAM::Role")
                    .filter((r) => vpcAccessCount(r) > 1)
                    .map(key)
            ).toEqual([]);
        }
    });

    test("CDK Nag reports no finding on the new grants", () => {
        const s = synthTemplate("commercial", {
            mutate: (c: any) => {
                c.app.useGlobalVpc.enabled = true;
                c.app.useGlobalVpc.useForAllLambdas = false;
            },
            mutateKey: "eni1347-vpc-forAll-false",
            nag: true,
        });
        expect(
            s.errors.filter((e) =>
                /AWSLambdaVPCAccessExecutionRole|lambda:SourceFunctionArn|ec2:/.test(e.message)
            )
        ).toEqual([]);
    });
});

describe("commercial template, no VPC (T1)", () => {
    test("nothing is added: no code Deny, and only the roles VAMS gives the policy explicitly carry it", () => {
        const s = synthTemplate("commercial");
        const pairs = functionRoles(s);
        expect(pairs.length).toBeGreaterThan(50);
        expect(s.grep(DENY_SID)).toEqual([]);
        // lambdaPipelineFunctions and the auth-defaults custom resource attach it in their own code.
        expect(
            s
                .ofType("AWS::IAM::Role")
                .filter((r) => vpcAccessCount(r) > 0)
                .map((r) => r.logicalId.replace(/[0-9A-F]{8}$/, ""))
                .sort()
        ).toEqual(["AuthDefaultCustomResourceRole", "lambdaPipelineRole"]);
    });
});

describe.each<TemplateName>(["govcloud", "eusovereign"])(
    "%s template (VPC on by default, T1)",
    (name) => {
        test("every function's role carries the policy, through a partition-aware ARN", () => {
            const s = synthTemplate(name);
            expect(functionRoles(s).length).toBeGreaterThan(50);
            expect(withoutPolicy(s)).toEqual([]);
            for (const r of s.ofType("AWS::IAM::Role")) {
                for (const arn of (r.properties.ManagedPolicyArns ?? []) as any[]) {
                    const flat = SynthResult.flatten(arn);
                    if (flat.endsWith(VPC_ACCESS))
                        expect(flat.startsWith("arn:${AWS::Partition}:")).toBe(true);
                }
            }
        });

        test("the roles of functions outside the VPC carry the code Deny", () => {
            const s = synthTemplate(name);
            const outside = functionRoles(s).filter(
                (p) => !inVpc(p.fn) && !CDK_LOW_LEVEL_HANDLERS.test(p.fn.logicalId)
            );
            expect(outside.length).toBeGreaterThan(50);
            expect(outside.filter((p) => !hasCodeDeny(s, p.role)).map((p) => key(p.fn))).toEqual(
                []
            );
        });
    }
);
