/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * #1218: a fresh deploy reuses a retained API Gateway account CloudWatch role of the same name
 * instead of failing with "already exists", and a stack that already exists keeps its own role.
 *
 * The upgrade-safety half pins what an existing stack sees: the role keeps the logical id, name and
 * properties it has shipped with, gains only a Condition that is true on every update, and the
 * account setting still resolves to that role. The fresh-deploy half runs the decision handler
 * (index.py) against a stubbed boto3 for every create, update and delete path.
 */

import * as fs from "fs";
import * as os from "os";
import * as path from "path";
import { spawnSync } from "child_process";
import {
    ALL_TEMPLATES,
    Resource,
    SynthResult,
    TemplateName,
    synthTemplate,
} from "../support/templateSynth";

// Three full-app synths plus two variants, ~20-30 s each.
jest.setTimeout(900_000);

/** The role's logical id on every shipped release. A different id replaces the role on upgrade. */
const SHIPPED_ROLE_LOGICAL_ID = "RestApiGatewayApiCloudWatchRole13BBC6CC";
/** The account resource's logical id on every shipped release. */
const SHIPPED_ACCOUNT_LOGICAL_ID = "RestApiGatewayApiAccountBFCA04D0";
const CONDITION_ID = "ApiCloudWatchRoleManagedByStack";

const synthed: Partial<Record<TemplateName, SynthResult>> = {};
const synth = (name: TemplateName): SynthResult => (synthed[name] ??= synthTemplate(name));

beforeAll(() => {
    ALL_TEMPLATES.forEach(synth);
});

function apiAccount(s: SynthResult): Resource {
    const accounts = s.ofType("AWS::ApiGateway::Account");
    expect(accounts.length).toBe(1);
    return accounts[0];
}

function apiRole(s: SynthResult): Resource {
    const account = apiAccount(s);
    const role = s.resources.find(
        (r) => r.stack === account.stack && r.logicalId === SHIPPED_ROLE_LOGICAL_ID
    );
    expect(role).toBeDefined();
    return role!;
}

function decision(s: SynthResult): Resource[] {
    return s.where("AWS::CloudFormation::CustomResource", (r) =>
        /^ApiCloudWatchRoleDecision/.test(r.logicalId)
    );
}

/** The parent-stack value a nested stack parameter is bound to. */
function parameterSource(s: SynthResult, nestedStack: string, parameter: string): any {
    const nested = s
        .ofType("AWS::CloudFormation::Stack")
        .filter((r) => r.properties.Parameters && parameter in r.properties.Parameters);
    expect(nested.length).toBe(1);
    expect(s.templates[nestedStack].Parameters[parameter]).toBeDefined();
    return nested[0].properties.Parameters[parameter];
}

describe("#1218 existing stack: the CloudWatch role is left as it is on upgrade", () => {
    test.each(ALL_TEMPLATES)(
        "%s: the role keeps its shipped logical id, name and properties",
        (name) => {
            const s = synth(name);
            const role = apiRole(s);
            expect(role.type).toBe("AWS::IAM::Role");
            expect(Object.keys(role.properties).sort()).toEqual([
                "AssumeRolePolicyDocument",
                "ManagedPolicyArns",
                "RoleName",
                "Tags",
            ]);
            expect(role.properties.AssumeRolePolicyDocument.Statement).toEqual([
                {
                    Action: "sts:AssumeRole",
                    Effect: "Allow",
                    Principal: { Service: "apigateway.amazonaws.com" },
                },
            ]);
            expect(SynthResult.flatten(role.properties.ManagedPolicyArns)).toContain(
                ":iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
            );
            // The IamRoleTransform formula with the cdk.json prefix (""), unchanged by this fix.
            expect(role.properties.RoleName).toMatch(
                new RegExp(`^[A-Za-z0-9]+CloudWatchRole[0-9A-F]{8}-${s.region}$`)
            );
            expect(role.raw.DeletionPolicy).toBe("Retain");
            expect(role.raw.UpdateReplacePolicy).toBe("Retain");
        }
    );

    test.each(ALL_TEMPLATES)(
        "%s: the role's only addition is a Condition on the stack's decision",
        (name) => {
            const s = synth(name);
            const role = apiRole(s);
            expect(role.raw.Condition).toBe(CONDITION_ID);
            const condition = s.templates[role.stack].Conditions[CONDITION_ID];
            const [ref, value] = condition["Fn::Equals"];
            expect(value).toBe("managed");
            // The parameter comes from the decision resource's Mode in the parent stack.
            expect(parameterSource(s, role.stack, ref.Ref)).toEqual({
                "Fn::GetAtt": [decision(s)[0].logicalId, "Mode"],
            });
        }
    );

    test.each(ALL_TEMPLATES)(
        "%s: the account setting points at the stack's role when it owns one",
        (name) => {
            const s = synth(name);
            const account = apiAccount(s);
            expect(account.logicalId).toBe(SHIPPED_ACCOUNT_LOGICAL_ID);
            const [cond, owned, reused] = account.properties.CloudWatchRoleArn["Fn::If"];
            expect(cond).toBe(CONDITION_ID);
            expect(owned).toEqual({ "Fn::GetAtt": [SHIPPED_ROLE_LOGICAL_ID, "Arn"] });
            expect(parameterSource(s, account.stack, reused.Ref)).toEqual({
                "Fn::GetAtt": [decision(s)[0].logicalId, "RoleArn"],
            });
            expect(account.raw.DeletionPolicy).toBe("Retain");
            expect(account.raw.UpdateReplacePolicy).toBe("Retain");
            expect(account.raw.Condition).toBeUndefined();
        }
    );
});

describe("#1218 decision resource", () => {
    test.each(ALL_TEMPLATES)(
        "%s: one decision in the core stack, naming the role it guards",
        (name) => {
            const s = synth(name);
            const decisions = decision(s);
            expect(decisions.length).toBe(1);
            const d = decisions[0];
            expect(d.logicalId).toBe("ApiCloudWatchRoleDecision");
            expect(d.stack).not.toBe(apiRole(s).stack);
            expect(d.properties.StackId).toEqual({ Ref: "AWS::StackId" });
            expect(d.properties.RoleName).toBe(apiRole(s).properties.RoleName);
            expect(Object.keys(d.properties).sort()).toEqual([
                "RoleName",
                "ServiceToken",
                "StackId",
            ]);
        }
    );

    test.each(ALL_TEMPLATES)(
        "%s: the handler can read only its own stack and that one role",
        (name) => {
            const s = synth(name);
            const roleName = apiRole(s).properties.RoleName;
            const policies = s.where("AWS::IAM::Policy", (r) =>
                /^ApiCloudWatchRoleDecisionProviderHandlerServiceRole/.test(r.logicalId)
            );
            expect(policies.length).toBe(1);
            const statements = policies[0].properties.PolicyDocument.Statement;
            expect(statements.map((st: any) => st.Action)).toEqual([
                "cloudformation:DescribeStacks",
                "iam:GetRole",
            ]);
            expect(statements[0].Resource).toEqual({ Ref: "AWS::StackId" });
            // Partition-aware, and the account is the deployment's own (the harness binds one).
            expect(SynthResult.flatten(statements[1].Resource)).toMatch(
                new RegExp(
                    `^arn:\\$\\{AWS::Partition\\}:iam::(\\d{12}|\\$\\{AWS::AccountId\\}):role/${roleName}$`
                )
            );
            statements.forEach((st: any) => expect(st.Effect).toBe("Allow"));
        }
    );
});

describe("#1218 role name prefix and no role renaming", () => {
    it("with an IamRoleNamePrefix, the decision names the prefixed role", () => {
        const s = synthTemplate("commercial", {
            context: {
                environments: {
                    common: { SolutionName: "AWSVisualAssetManagementSystem" },
                    aws: { PermissionBoundaryArn: "", IamRoleNamePrefix: "vamsx" },
                },
            },
        });
        const roleName = apiRole(s).properties.RoleName;
        expect(roleName.startsWith("vamsx")).toBe(true);
        expect(decision(s)[0].properties.RoleName).toBe(roleName);
    });

    it("without the aws environment block, roles keep generated names and nothing changes", () => {
        const s = synthTemplate("commercial", {
            context: {
                environments: { common: { SolutionName: "AWSVisualAssetManagementSystem" } },
            },
        });
        const role = apiRole(s);
        expect(role.properties.RoleName).toBeUndefined();
        expect(role.raw.Condition).toBeUndefined();
        expect(decision(s)).toEqual([]);
        expect(apiAccount(s).properties.CloudWatchRoleArn).toEqual({
            "Fn::GetAtt": [SHIPPED_ROLE_LOGICAL_ID, "Arn"],
        });
    });
});

/* -------------------------------------------------------------------------------------------------
 * The decision handler, run against a stubbed boto3
 * ---------------------------------------------------------------------------------------------- */

const HANDLER_DIR = path.join(
    __dirname,
    "../../lib/nestedStacks/apiLambda/constructs/apiCloudWatchRoleDecision"
);

const DRIVER = `
import json, sys, types

scenario = json.loads(sys.argv[1])
calls = []

class ClientError(Exception):
    def __init__(self, code):
        super().__init__(code)
        self.response = {"Error": {"Code": code}}

class BotoCoreError(Exception):
    pass

class Client:
    def __init__(self, service):
        self.service = service
    def describe_stacks(self, StackName):
        calls.append("describe_stacks")
        status = scenario.get("stackStatus")
        if status is None:
            raise ClientError("AccessDenied")
        return {"Stacks": [{"StackStatus": status}]}
    def get_role(self, RoleName):
        calls.append("get_role:" + RoleName)
        role = scenario.get("role", "absent")
        if role == "absent":
            raise ClientError("NoSuchEntity")
        if role == "denied":
            raise ClientError("AccessDenied")
        return {"Role": {"Arn": "arn:aws:iam::123456789012:role/" + RoleName}}

boto3 = types.ModuleType("boto3")
boto3.client = lambda service, **kwargs: Client(service)
botocore = types.ModuleType("botocore")
exceptions = types.ModuleType("botocore.exceptions")
exceptions.ClientError = ClientError
exceptions.BotoCoreError = BotoCoreError
botocore.exceptions = exceptions
sys.modules.update({"boto3": boto3, "botocore": botocore, "botocore.exceptions": exceptions})

sys.path.insert(0, scenario["handlerDir"])
import index
result = index.on_event(scenario["event"], None)
print(json.dumps({"result": result, "calls": calls}))
`;

function runHandler(scenario: Record<string, unknown>): { result: any; calls: string[] } {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), "cwrole-"));
    try {
        const driver = path.join(dir, "driver.py");
        fs.writeFileSync(driver, DRIVER);
        const run = spawnSync(
            "python3",
            [driver, JSON.stringify({ ...scenario, handlerDir: HANDLER_DIR })],
            { encoding: "utf8", env: { ...process.env, PYTHONDONTWRITEBYTECODE: "1" } }
        );
        if (run.status !== 0) throw new Error(run.stderr || run.stdout);
        return JSON.parse(run.stdout.trim().split("\n").pop()!);
    } finally {
        fs.rmSync(dir, { recursive: true, force: true });
    }
}

const ROLE = "vamscoreexampleCloudWatchRoleD38AF193-us-east-1";
const ARN = `arn:aws:iam::123456789012:role/${ROLE}`;
const create = {
    RequestType: "Create",
    ResourceProperties: { StackId: "stack-id", RoleName: ROLE },
};

describe("#1218 decision handler", () => {
    it("an upgrade of an existing stack keeps the stack-owned role, even though the role exists", () => {
        const { result, calls } = runHandler({
            event: create,
            stackStatus: "UPDATE_IN_PROGRESS",
            role: "exists",
        });
        expect(result).toEqual({
            PhysicalResourceId: "managed",
            Data: { Mode: "managed", RoleArn: "" },
        });
        expect(calls).toEqual(["describe_stacks"]);
    });

    it("a fresh deploy that finds a retained role of the same name reuses it", () => {
        const { result, calls } = runHandler({
            event: create,
            stackStatus: "CREATE_IN_PROGRESS",
            role: "exists",
        });
        expect(result).toEqual({
            PhysicalResourceId: `reuse:${ARN}`,
            Data: { Mode: "reuse", RoleArn: ARN },
        });
        expect(calls).toEqual(["describe_stacks", `get_role:${ROLE}`]);
    });

    it("a fresh deploy with no such role creates it", () => {
        const { result } = runHandler({ event: create, stackStatus: "CREATE_IN_PROGRESS" });
        expect(result.Data).toEqual({ Mode: "managed", RoleArn: "" });
    });

    it("an unreadable stack status falls back to the stack-owned role", () => {
        const { result, calls } = runHandler({ event: create, stackStatus: null, role: "exists" });
        expect(result.Data.Mode).toBe("managed");
        expect(calls).toEqual(["describe_stacks"]);
    });

    it("an unreadable role on a fresh deploy falls back to creating it", () => {
        const { result } = runHandler({
            event: create,
            stackStatus: "CREATE_IN_PROGRESS",
            role: "denied",
        });
        expect(result.Data.Mode).toBe("managed");
    });

    it("an update keeps a stack-owned role stack-owned without any lookup", () => {
        const { result, calls } = runHandler({
            event: {
                RequestType: "Update",
                PhysicalResourceId: "managed",
                ResourceProperties: { RoleName: ROLE },
                OldResourceProperties: { RoleName: ROLE },
            },
            stackStatus: "UPDATE_IN_PROGRESS",
            role: "exists",
        });
        expect(result.Data.Mode).toBe("managed");
        expect(calls).toEqual([]);
    });

    it("an update keeps a reused role without any lookup", () => {
        const { result, calls } = runHandler({
            event: {
                RequestType: "Update",
                PhysicalResourceId: `reuse:${ARN}`,
                ResourceProperties: { RoleName: ROLE },
                OldResourceProperties: { RoleName: ROLE },
            },
            role: "absent",
        });
        expect(result).toEqual({
            PhysicalResourceId: `reuse:${ARN}`,
            Data: { Mode: "reuse", RoleArn: ARN },
        });
        expect(calls).toEqual([]);
    });

    it("an update that renames a reused role checks the new name again", () => {
        const { result, calls } = runHandler({
            event: {
                RequestType: "Update",
                PhysicalResourceId: `reuse:${ARN}`,
                ResourceProperties: { RoleName: "renamed-us-east-1" },
                OldResourceProperties: { RoleName: ROLE },
            },
            role: "absent",
        });
        expect(result.Data.Mode).toBe("managed");
        expect(calls).toEqual(["get_role:renamed-us-east-1"]);
    });

    it("a delete changes nothing", () => {
        const { result, calls } = runHandler({
            event: {
                RequestType: "Delete",
                PhysicalResourceId: `reuse:${ARN}`,
                ResourceProperties: { RoleName: ROLE },
            },
        });
        expect(result).toEqual({ PhysicalResourceId: `reuse:${ARN}` });
        expect(calls).toEqual([]);
    });
});
