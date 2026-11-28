/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The workflow execution status change rule (`buildWorkflowStatusChangeRule` in lib/lambdaBuilder/workflowFunctions.ts),
 * in every partition template and with a customer managed key.
 *
 * The rule is what reconciles a run that ended outside VAMS without a read, so it has four contracts:
 * it matches only terminal status changes of this account and Region's workflow state machines (no
 * account-wide or all-state-machine match), it invokes the execution service and nothing else, it adds
 * no role and no IAM statement (only a resource policy on the function, scoped to the rule), and its
 * dead-letter queue is encrypted with the deployment key when one is configured. The handler side of
 * the contract (event shapes, foreign executions, duplicates, races) is pinned in
 * backend/tests/handlers/workflows/test_system_pipeline_hooks.py.
 */

import * as fs from "fs";
import * as path from "path";
import {
    SynthResult,
    TemplateName,
    buildTemplateConfig,
    synthTemplate,
} from "../support/templateSynth";
import { nestedTemplateKeyFor } from "../support/iamPolicySize";
import {
    WORKFLOW_STATE_MACHINE_NAME_PREFIX,
    WORKFLOW_STATUS_CHANGE_DLQ_ID,
    WORKFLOW_STATUS_CHANGE_PERMISSION_ID,
    WORKFLOW_STATUS_CHANGE_RULE_ID,
    WORKFLOW_STATUS_EVENT_STATUSES,
} from "../../lib/lambdaBuilder/workflowFunctions";

jest.setTimeout(900_000);

const PARTITION_OF: Record<TemplateName, string> = {
    commercial: "aws",
    govcloud: "aws-us-gov",
    eusovereign: "aws-eusc",
};

const REGION_OF: Record<TemplateName, string> = {
    commercial: "us-east-1",
    govcloud: "us-gov-west-1",
    eusovereign: "eusc-de-east-1",
};

const CELLS: { name: string; template: TemplateName; cmk: boolean }[] = [
    { name: "commercial", template: "commercial", cmk: false },
    { name: "commercial-cmk", template: "commercial", cmk: true },
    { name: "govcloud", template: "govcloud", cmk: false },
    { name: "eusovereign", template: "eusovereign", cmk: false },
];

const synthCell = (cell: { name: string; template: TemplateName; cmk: boolean }): SynthResult =>
    cell.cmk
        ? synthTemplate(cell.template, {
              mutate: (c) => {
                  c.app.useKmsCmkEncryption.enabled = true;
              },
              mutateKey: "commercial-cmk",
          })
        : synthTemplate(cell.template);

function byPrefix(resources: Record<string, any>, type: string, prefix: string) {
    return Object.entries(resources).filter(
        ([id, r]) => r.Type === type && new RegExp(`^${prefix}[0-9A-F]{8}$`).test(id)
    );
}

describe.each(CELLS)("workflow status change rule ($name)", (cell) => {
    let synth: SynthResult;
    let resources: Record<string, any>;
    let rule: any;
    let ruleId: string;

    beforeAll(() => {
        synth = synthCell(cell);
        resources = synth.templates[nestedTemplateKeyFor(synth, "ApiBuilder2")].Resources;
        const rules = byPrefix(resources, "AWS::Events::Rule", WORKFLOW_STATUS_CHANGE_RULE_ID);
        expect(rules).toHaveLength(1);
        [ruleId, rule] = rules[0];
    });

    test("matches only terminal status changes of this account and Region's workflow state machines", () => {
        const pattern = rule.Properties.EventPattern;
        expect(pattern.source).toEqual(["aws.states"]);
        expect(pattern["detail-type"]).toEqual(["Step Functions Execution Status Change"]);
        expect([...pattern.detail.status].sort()).toEqual(
            [...WORKFLOW_STATUS_EVENT_STATUSES].sort()
        );
        expect(pattern.detail.status).not.toContain("RUNNING");
        // The default bus (no EventBusName), where Step Functions publishes.
        expect(rule.Properties.EventBusName).toBeUndefined();
        const arnMatch = pattern.detail.stateMachineArn;
        expect(arnMatch).toHaveLength(1);
        const prefix = SynthResult.flatten(arnMatch[0].prefix);
        // The Region is pinned per template, not read back from the synth, so a GovCloud cell that
        // silently synthesized in a commercial Region (and partition) fails here.
        expect(synth.region).toBe(REGION_OF[cell.template]);
        expect(prefix).toBe(
            `arn:${PARTITION_OF[cell.template]}:states:${REGION_OF[cell.template]}:123456789012:` +
                `stateMachine:${WORKFLOW_STATE_MACHINE_NAME_PREFIX}`
        );
        expect(prefix).not.toContain("*");
    });

    test("invokes the execution service function only, with a dead-letter queue", () => {
        const targets = rule.Properties.Targets;
        expect(targets).toHaveLength(1);
        const fnId = targets[0].Arn["Fn::GetAtt"][0];
        expect(fnId).toMatch(/^executionService[0-9A-F]{8}$/);
        expect(targets[0].RetryPolicy.MaximumRetryAttempts).toBe(3);
        const dlqId = targets[0].DeadLetterConfig.Arn["Fn::GetAtt"][0];
        expect(dlqId).toMatch(new RegExp(`^${WORKFLOW_STATUS_CHANGE_DLQ_ID}[0-9A-F]{8}$`));
    });

    /** The rule's ARN, built from its fixed name in this cell's partition, Region and account. */
    const ruleArnOf = (): string =>
        `arn:${PARTITION_OF[cell.template]}:events:${REGION_OF[cell.template]}:123456789012:` +
        `rule/${rule.Properties.Name}`;

    test("has a fixed name, unique per deployment and within EventBridge's limit", () => {
        const name = rule.Properties.Name;
        expect(typeof name).toBe("string");
        expect(name.length).toBeLessThanOrEqual(64);
        expect(name).toMatch(/^[A-Za-z0-9-]+$/);
        // Derived from the top-level stack name (unique per account and Region) with a hash suffix.
        expect(name.startsWith(`vams-t1-${cell.template}`)).toBe(true);
        expect(name).toMatch(/[0-9A-F]{8}$/);
    });

    test("adds only a resource policy scoped to the rule, no role and no IAM statement", () => {
        const permissions = Object.values(resources).filter(
            (r: any) =>
                r.Type === "AWS::Lambda::Permission" &&
                r.Properties.Principal === "events.amazonaws.com" &&
                SynthResult.flatten(r.Properties.SourceArn ?? "") === ruleArnOf()
        );
        expect(permissions).toHaveLength(1);
        const permission: any = permissions[0];
        expect(permission.Properties.Action).toBe("lambda:InvokeFunction");
        expect(permission.Properties.FunctionName["Fn::GetAtt"][0]).toMatch(
            /^executionService[0-9A-F]{8}$/
        );
        for (const r of Object.values(resources) as any[]) {
            if (r.Type === "AWS::IAM::Policy" || r.Type === "AWS::IAM::ManagedPolicy") {
                const doc = JSON.stringify(r.Properties.PolicyDocument);
                expect(doc).not.toContain(ruleId);
                expect(doc).not.toContain(rule.Properties.Name);
            }
        }
    });

    // In-dev D2 (#1135): the rule was live about a minute before the function's resource policy and
    // the dead-letter queue's policy on a first deploy, so other deployments' events failed delivery
    // into this deployment's dead-letter queue. CloudFormation now creates both policies first.
    test("is created only after the function's resource policy and the queue policy", () => {
        // A top-level L1 in the nested stack keeps its construct id as its logical id.
        const permissionId = WORKFLOW_STATUS_CHANGE_PERMISSION_ID;
        expect(resources[permissionId]?.Type).toBe("AWS::Lambda::Permission");
        const policies = Object.entries(resources).filter(
            ([, r]: [string, any]) =>
                r.Type === "AWS::SQS::QueuePolicy" &&
                r.Properties.Queues.some(
                    (q: any) =>
                        q.Ref &&
                        new RegExp(`^${WORKFLOW_STATUS_CHANGE_DLQ_ID}[0-9A-F]{8}$`).test(q.Ref)
                )
        );
        expect(policies).toHaveLength(1);
        const [queuePolicyId, queuePolicy]: [string, any] = policies[0] as any;
        expect(rule.DependsOn).toEqual(expect.arrayContaining([permissionId, queuePolicyId]));

        // Neither policy references the rule, which would make the dependency a cycle.
        expect(JSON.stringify(resources[permissionId])).not.toContain(ruleId);
        expect(JSON.stringify(queuePolicy)).not.toContain(ruleId);
        const sendStatements = queuePolicy.Properties.PolicyDocument.Statement.filter(
            (s: any) => s.Action === "sqs:SendMessage"
        );
        expect(sendStatements).toHaveLength(1);
        expect(sendStatements[0].Principal).toEqual({ Service: "events.amazonaws.com" });
        expect(SynthResult.flatten(sendStatements[0].Condition.ArnEquals["aws:SourceArn"])).toBe(
            ruleArnOf()
        );
    });

    test("the dead-letter queue is encrypted with the deployment key when one is configured", () => {
        const queues = byPrefix(resources, "AWS::SQS::Queue", WORKFLOW_STATUS_CHANGE_DLQ_ID);
        expect(queues).toHaveLength(1);
        const props = queues[0][1].Properties;
        // The GovCloud and EU Sovereign templates ship with the customer managed key enabled.
        const keyConfigured =
            cell.cmk || buildTemplateConfig(cell.template).app.useKmsCmkEncryption.enabled;
        if (keyConfigured) {
            expect(props.KmsMasterKeyId).toBeDefined();
            expect(props.SqsManagedSseEnabled).toBeUndefined();
        } else {
            expect(props.KmsMasterKeyId).toBeUndefined();
            expect(props.SqsManagedSseEnabled).toBe(true);
        }
    });
});

describe("handler parity", () => {
    const handler = fs.readFileSync(
        path.join(
            __dirname,
            "..",
            "..",
            "..",
            "backend",
            "backend",
            "handlers",
            "workflows",
            "executionService.py"
        ),
        "utf-8"
    );
    const asl = fs.readFileSync(
        path.join(
            __dirname,
            "..",
            "..",
            "..",
            "backend",
            "backend",
            "common",
            "workflows",
            "workflowAsl.py"
        ),
        "utf-8"
    );

    test("the handler recognizes the event the rule matches", () => {
        expect(handler).toContain('SFN_STATUS_CHANGE_SOURCE = "aws.states"');
        expect(handler).toContain(
            'SFN_STATUS_CHANGE_DETAIL_TYPE = "Step Functions Execution Status Change"'
        );
    });

    test("the prefix is the one the backend names workflow state machines with", () => {
        expect(asl).toContain(`return "${WORKFLOW_STATE_MACHINE_NAME_PREFIX}" + name + suffix`);
    });
});
