/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The workflow execution status change rule (lib/lambdaBuilder/workflowStatusEvents.ts), in every
 * partition template and with a customer managed key.
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
    WORKFLOW_STATUS_CHANGE_RULE_ID,
    WORKFLOW_STATUS_EVENT_STATUSES,
} from "../../lib/lambdaBuilder/workflowStatusEvents";

jest.setTimeout(900_000);

const PARTITION_OF: Record<TemplateName, string> = {
    commercial: "aws",
    govcloud: "aws-us-gov",
    eusovereign: "aws-eusc",
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
        expect(prefix).toBe(
            `arn:${PARTITION_OF[cell.template]}:states:${synth.region}:123456789012:stateMachine:` +
                WORKFLOW_STATE_MACHINE_NAME_PREFIX
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

    test("adds only a resource policy scoped to the rule, no role and no IAM statement", () => {
        const permissions = Object.values(resources).filter(
            (r: any) =>
                r.Type === "AWS::Lambda::Permission" &&
                JSON.stringify(r.Properties.SourceArn ?? "").includes(ruleId)
        );
        expect(permissions).toHaveLength(1);
        const permission: any = permissions[0];
        expect(permission.Properties.Action).toBe("lambda:InvokeFunction");
        expect(permission.Properties.Principal).toBe("events.amazonaws.com");
        expect(permission.Properties.SourceArn).toEqual({ "Fn::GetAtt": [ruleId, "Arn"] });
        for (const r of Object.values(resources) as any[]) {
            if (r.Type === "AWS::IAM::Policy" || r.Type === "AWS::IAM::ManagedPolicy") {
                expect(JSON.stringify(r.Properties.PolicyDocument)).not.toContain(ruleId);
            }
        }
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
