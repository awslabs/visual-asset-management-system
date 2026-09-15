/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * ARN and service-principal hygiene: the wildcards and literals that read as something they are not.
 *
 * Five findings, all in the same family — a policy or trust relationship whose text does not describe
 * what it actually grants:
 *
 * *   **S1-INFRA-040 / S1-INFRA-042** `IAMArn().stateMachineEvents` rendered the EventBridge service
 *     namespace as `event` (singular), which is not an AWS namespace, so the workflow role's
 *     `events:PutRule` statement could never match a rule ARN and granted nothing. Both the helper entry
 *     and the statement are removed rather than corrected: VAMS has no Step Functions `.sync`
 *     integration, so no managed rule exists to grant on, and correcting the namespace would have
 *     WIDENED an inert grant instead of narrowing a real one.
 * *   **S1-INFRA-112** `IAMArn().role` and `.policy` hardcoded `*` in the account field while every
 *     other entry in the same helper used the deployment's account. `iam:PassRole` only resolves a
 *     same-account role, so the effective scope never changed — but a reviewer cannot tell that from the
 *     rendered policy.
 * *   **S1-INFRA-115** The workflow Step Functions execution role trusted `lambda.amazonaws.com` and
 *     carried `AWSLambdaVPCAccessExecutionRole` and `states:CreateStateMachine`, none of which any code
 *     path uses.
 * *   **S1-INFRA-124 / S1-INFRA-127 / S1-INFRA-129** Nine sites hardcoded a service-principal DNS
 *     literal instead of the partition-aware helper.
 *
 * The principal assertions are source-level on purpose. In `aws`, `aws-us-gov` and `aws-eusc` the helper
 * renders exactly the string the literals hardcoded, so a synthesized template is IDENTICAL either way —
 * a template assertion would pass with the literals restored. The convention is the thing being pinned.
 */

import * as fs from "fs";
import * as path from "path";
import { SynthResult, synthTemplate } from "../support/templateSynth";
import * as Service from "../../lib/helper/service-helper";
import { IAMArn } from "../../lib/helper/service-helper";
import * as Config from "../../config/config";
import commercialTemplate from "../../config/config.template.commercial.json";

const INFRA_LIB = path.resolve(__dirname, "..", "../lib");
const ACCOUNT = "123456789012";

/** A minimal resolved config, so IAMArn can render. */
function mockConfig(): Config.Config {
    const config = JSON.parse(JSON.stringify(commercialTemplate)) as Config.Config;
    config.env.account = ACCOUNT;
    config.env.region = "us-east-1";
    config.env.partition = "aws";
    config.env.coreStackName = "vams-test-us-east-1";
    config.app.baseStackName = "vams-test";
    return config;
}

/** Every .ts file under infra/lib. */
function libFiles(dir: string = INFRA_LIB): string[] {
    return fs.readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
        const full = path.join(dir, entry.name);
        if (entry.isDirectory()) return libFiles(full);
        return entry.isFile() && entry.name.endsWith(".ts") ? [full] : [];
    });
}

const LIB_FILES = libFiles();

describe("service principals come from the partition-aware helper", () => {
    test("[control] infra/lib was actually walked", () => {
        // An empty file list makes every assertion below vacuous, and the walk is recursive over a
        // deep tree.
        expect(LIB_FILES.length).toBeGreaterThan(50);
    });

    test("[control] the helper form is in use", () => {
        // Without this, the ban below is satisfied by a tree that uses no principals at all.
        const usingHelper = LIB_FILES.filter((f) =>
            /Service\("[A-Z_0-9]+"\)\.Principal/.test(fs.readFileSync(f, "utf-8"))
        );
        expect(usingHelper.length).toBeGreaterThan(3);
    });

    test("no file hardcodes a service-principal DNS literal", () => {
        const offenders: string[] = [];
        for (const file of LIB_FILES) {
            const source = fs.readFileSync(file, "utf-8");
            const matches = source.match(
                /new iam\.ServicePrincipal\(\s*"[a-z0-9.-]+\.amazonaws\.com(?:\.cn)?"\s*\)/g
            );
            if (matches) {
                offenders.push(`${path.relative(INFRA_LIB, file)}: ${matches.join(", ")}`);
            }
        }
        // The literal happens to be correct in every partition VAMS ships a template for, which is why
        // this is a convention rule rather than a bug: it is wrong in aws-cn and the ISO partitions, and
        // the point of the helper is that a reader does not have to know which.
        expect(offenders).toEqual([]);
    });
});

describe("IAMArn renders no unnecessary wildcards", () => {
    const helperSource = fs.readFileSync(
        path.resolve(__dirname, "..", "../lib/helper/service-helper.ts"),
        "utf-8"
    );

    test("[control] the IAMArn helper is the file being read", () => {
        expect(helperSource).toContain("export function IAMArn(");
    });

    test("the role and policy ARNs use the deployment account, not a wildcard", () => {
        // Asserted on the RENDERED ARN, not the source text. A first draft matched the source for
        // `:iam::${config.env.account}:role/` and passed in isolation, then failed in the full run
        // because Prettier had split the interpolation across lines — the value was correct and the
        // assertion was reading formatting. The rendered string is also what ends up in the policy.
        Service.SetConfig(mockConfig());
        const rendered = IAMArn("some-role-name");
        expect(rendered.role).toBe(`arn:aws:iam::${ACCOUNT}:role/some-role-name`);
        expect(rendered.policy).toBe(`arn:aws:iam::${ACCOUNT}:policy/some-role-name`);
        // The shape under test: an empty region field followed by a wildcard account.
        expect(rendered.role).not.toMatch(/:iam::\*:/);
        expect(rendered.policy).not.toMatch(/:iam::\*:/);
    });

    test("the broken stateMachineEvents entry is gone, and nothing references it", () => {
        // Removed rather than corrected: see the file header. A grant that matched nothing is safe to
        // delete and unsafe to fix.
        expect(helperSource).not.toMatch(/stateMachineEvents:\s*`arn:/);
        // Comment lines are stripped before matching: both this fix and the helper explain the removal
        // in prose that names the property, and a substring search over the raw text reports those
        // explanations as live consumers.
        const consumers = LIB_FILES.filter((f) => {
            const code = fs
                .readFileSync(f, "utf-8")
                .split("\n")
                .filter((line) => !line.trim().startsWith("//") && !line.trim().startsWith("*"))
                .join("\n");
            return /IAMArn\([^)]*\)\.stateMachineEvents/.test(code);
        }).map((f) => path.relative(INFRA_LIB, f));
        expect(consumers).toEqual([]);
    });

    test("no ARN in the helper uses the singular 'event' service namespace", () => {
        // The defect generalized: `events` is the namespace, and `event` matches nothing.
        expect(helperSource).not.toMatch(/:event:\$\{/);
    });
});

describe("the workflow state machine execution role", () => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate("commercial");
    });

    /** The workflow execution role, found by its description rather than by logical id. */
    function workflowRole() {
        const roles = synth
            .ofType("AWS::IAM::Role")
            .filter((r) => String((r.properties as any).Description) === "VAMS Workflow IAM Role.");
        expect(roles).toHaveLength(1);
        return roles[0];
    }

    test("[control] the role is present in the synthesized template", () => {
        expect(workflowRole()).toBeDefined();
    });

    test("is trusted only by Step Functions", () => {
        // A spare trust relationship is a standing way into the role. No Lambda assumes this one — the
        // auto-provisioned pipeline Lambda gets its own from createRoleToAttachToLambdaPipelines.
        const document = JSON.stringify(
            (workflowRole().properties as any).AssumeRolePolicyDocument
        );
        expect(document).toContain("states.");
        expect(document).not.toContain("lambda.amazonaws.com");
    });

    test("carries no Lambda VPC-access managed policy", () => {
        const managed = JSON.stringify((workflowRole().properties as any).ManagedPolicyArns ?? []);
        expect(managed).not.toContain("AWSLambdaVPCAccessExecutionRole");
    });

    test("cannot create state machines and manages no EventBridge rules", () => {
        // Creation belongs to the workflowService Lambda, which holds the grant plus the iam:PassRole
        // that goes with it.
        const policies = JSON.stringify((workflowRole().properties as any).Policies ?? []);
        expect(policies).not.toContain("states:CreateStateMachine");
        expect(policies).not.toContain("events:PutRule");
        expect(policies).not.toContain("events:DescribeRule");
    });

    test("still holds the grants the state machine genuinely needs", () => {
        // The positive control for the four removals above. Without it, a change that emptied the role
        // entirely would satisfy every assertion here while breaking every workflow.
        //
        // Note what is NOT asserted: any `states:` action. The role has none, and correctly so — the
        // workflow ASL invokes Lambdas and sends to SQS/EventBridge, and the pipeline sub-state-machines
        // are started by a Lambda under its own role. An earlier draft of this test required a `states:`
        // action and failed, which is the useful half: the role's remaining surface is logs, Lambda
        // invoke, SQS send, EventBridge put, S3 and the X-Ray/CloudWatch defaults.
        const policies = JSON.stringify((workflowRole().properties as any).Policies ?? []);
        expect(policies).toContain("lambda:InvokeFunction");
        expect(policies).toContain("logs:CreateLogDelivery");
        expect(policies).toContain("sqs:SendMessage");
        expect(policies).toContain("events:PutEvents");
    });
});

describe("sns:ListTopics is not granted on a topic-scoped statement", () => {
    test("no lambda builder grants sns:ListTopics", () => {
        // ListTopics takes no resource constraint, so granting it alongside a topic ARN granted nothing:
        // the resource could never match. Removing it is therefore not a permission reduction. Verified
        // before removal that no backend handler calls list_topics, so nothing depended on it — had one
        // done so, it was already failing.
        const offenders = LIB_FILES.filter((f) =>
            fs.readFileSync(f, "utf-8").includes("sns:ListTopics")
        ).map((f) => path.relative(INFRA_LIB, f));
        expect(offenders).toEqual([]);
    });

    test("[control] the SNS grants that remain are still there", () => {
        // Without this the ban above is satisfied by a builder that grants no SNS action at all, which
        // would break per-asset topic creation.
        const assetFunctions = fs.readFileSync(
            path.resolve(__dirname, "..", "../lib/lambdaBuilder/assetFunctions.ts"),
            "utf-8"
        );
        expect(assetFunctions).toContain("sns:CreateTopic");
        expect(assetFunctions).toContain("sns:DeleteTopic");
        // The pinned per-asset topic ARN the remaining grants are scoped to.
        expect(assetFunctions).toContain("assetTopicWildcardArn");
    });
});
