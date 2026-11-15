/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The three workflow functions that write an execution's terminal status publish its
 * workflow.execution.completed event, so each role can call events:PutEvents on the orchestration
 * bus, in every shipped config template. The compliance callback waits for that event to record a
 * pipeline rule's outcome and release the run's locks; a role without the grant leaves the rule
 * pending.
 *
 * The end-state handler's role is a named exception to the IAM inline policy budget
 * (iamInlinePolicyBudget.test.ts), so its PutEvents sits in a customer managed policy the role names
 * in ManagedPolicyArns and adds nothing to the inline total.
 */

import { PROCESS_OUTPUT_EVENTS_POLICY_ID } from "../../lib/lambdaBuilder/workflowFunctions";
import { ALL_TEMPLATES, SynthResult, synthTemplate } from "../support/templateSynth";
import { inlinePolicies, managedPolicies } from "../support/iamPolicySize";

jest.setTimeout(900_000);

/** The publishers: the execution service (abort and the lazy reconcile), the end-state handler and
 * the error handler. */
const PUBLISHERS = ["executionService", "processWorkflowExecutionOutput", "handleExecutionError"];

function roleOf(synth: SynthResult, construct: string): { roleId: string; stack: string } {
    const roles = synth
        .ofType("AWS::IAM::Role")
        .filter((r) => new RegExp(`^${construct}ServiceRole[0-9A-F]{8}$`).test(r.logicalId));
    expect(roles).toHaveLength(1);
    return { roleId: roles[0].logicalId, stack: roles[0].stack };
}

/** Whether a statement in `docs` allows events:PutEvents on the orchestration bus. */
function putsToBus(docs: [string, any][]): boolean {
    return docs.some(([, doc]) =>
        ((doc?.Statement ?? []) as any[]).some(
            (st) =>
                st.Effect === "Allow" &&
                ([] as string[]).concat(st.Action ?? []).includes("events:PutEvents") &&
                JSON.stringify(st.Resource ?? "").includes("OrchestrationBus")
        )
    );
}

describe.each(ALL_TEMPLATES)("workflow completion event grants (%s template)", (name) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(name);
    });

    test.each(PUBLISHERS)("%s can publish to the orchestration bus", (construct) => {
        const { roleId, stack } = roleOf(synth, construct);
        const docs = [
            ...inlinePolicies(synth, roleId, stack),
            ...managedPolicies(synth, roleId, stack),
        ];
        expect(putsToBus(docs)).toBe(true);
    });

    test("the end-state handler's PutEvents is in its own managed policy, not inline", () => {
        const { roleId, stack } = roleOf(synth, "processWorkflowExecutionOutput");
        expect(putsToBus(inlinePolicies(synth, roleId, stack))).toBe(false);
        const events = managedPolicies(synth, roleId, stack).filter(([id]) =>
            id.includes(PROCESS_OUTPUT_EVENTS_POLICY_ID)
        );
        expect(events).toHaveLength(1);
        expect(putsToBus(events)).toBe(true);
    });
});
