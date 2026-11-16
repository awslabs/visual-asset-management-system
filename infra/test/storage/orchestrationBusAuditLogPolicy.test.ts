/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The orchestration bus audit rule writes to its log group through the log group's OWN resource
 * policy, never through an account-level CloudWatch Logs resource policy (#1266).
 *
 * The CDK `CloudWatchLogGroup` event target creates one account-level policy per stack
 * (`Custom::CloudwatchLogResourcePolicy`). An account holds at most 10 of those per Region (hard
 * limit), so an account could hold at most 10 VAMS deployments in a Region and the 11th rolled back.
 * A log-group-scoped policy (`AWS::Logs::LogGroup` `ResourcePolicyDocument`) does not count against
 * that limit and lives and dies with the log group, so one stack's uninstall cannot remove another
 * stack's grant.
 *
 * Pinned here, in every shipped template and with a customer managed key:
 *   - no account-level Logs resource policy of either form is synthesized anywhere;
 *   - the audit log group carries a single statement for the EventBridge principal, limited to
 *     CreateLogStream/PutLogEvents on its own streams, to this account and to rules on this
 *     deployment's orchestration bus;
 *   - the audit rule targets that log group by ARN with no role (EventBridge rejects a role on a
 *     CloudWatch Logs target).
 */

import {
    Resource,
    SynthResult,
    synthTemplate,
    TemplateName,
    ACCOUNT,
} from "../support/templateSynth";

const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];
const AUDIT_LOG_GROUP_PREFIX = "/aws/vendedlogs/VAMSOrchestrationBusAudit-";

interface Cell {
    label: string;
    template: TemplateName;
    mutate?: (c: any) => void;
    mutateKey?: string;
}

const CELLS: Cell[] = [
    ...TEMPLATES.map((template) => ({ label: template, template })),
    ...TEMPLATES.map((template) => ({
        label: `${template} + CMK`,
        template,
        mutate: (c: any) => {
            c.app.useKmsCmkEncryption.enabled = true;
        },
        mutateKey: "cmk-on",
    })),
];

function auditLogGroup(synth: SynthResult): Resource {
    const groups = synth.where(
        "AWS::Logs::LogGroup",
        (r) =>
            typeof r.properties.LogGroupName === "string" &&
            r.properties.LogGroupName.startsWith(AUDIT_LOG_GROUP_PREFIX)
    );
    expect(groups).toHaveLength(1);
    return groups[0];
}

function orchestrationBusName(synth: SynthResult): string {
    const buses = synth.where(
        "AWS::Events::EventBus",
        (r) => typeof r.properties.Name === "string" && r.properties.Name.endsWith("-orchestration")
    );
    expect(buses).toHaveLength(1);
    return buses[0].properties.Name;
}

describe.each(CELLS)("$label: orchestration bus audit log permission", (cell) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthTemplate(
            cell.template,
            cell.mutate ? { mutate: cell.mutate, mutateKey: cell.mutateKey } : {}
        );
    });

    /** `arn:<partition>:<service>:<region>:<account>`, with the partition as the stack emits it. */
    function arnPrefix(service: string): string {
        return `arn:\${AWS::Partition}:${service}:${synth.region}:${ACCOUNT}`;
    }

    test("no account-level CloudWatch Logs resource policy grants EventBridge", () => {
        // Each account-level policy counts against the 10-per-Region account limit.
        expect(synth.countOfType("AWS::Logs::ResourcePolicy")).toBe(0);
        const accountLevel = synth.ofType("Custom::CloudwatchLogResourcePolicy");
        for (const policy of accountLevel) {
            expect(JSON.stringify(policy.raw)).not.toContain("events.amazonaws.com");
        }
        // The only remaining account-level policy is the one the CDK OpenSearch Domain construct
        // creates for a provisioned domain's slow/application logs (one per domain), tracked separately.
        const provisionedDomains = synth.countOfType("AWS::OpenSearchService::Domain");
        expect(accountLevel.length).toBe(provisionedDomains);
        for (const policy of accountLevel) {
            expect(JSON.stringify(policy.raw)).toContain("es.amazonaws.com");
        }
    });

    test("the audit log group grants EventBridge write access through its own resource policy", () => {
        const group = auditLogGroup(synth);
        const busName = orchestrationBusName(synth);
        const doc = group.properties.ResourcePolicyDocument;
        expect(doc).toBeDefined();
        expect(doc.Statement).toHaveLength(1);

        const [statement] = doc.Statement;
        expect(statement.Effect).toBe("Allow");
        expect(statement.Principal).toEqual({ Service: "events.amazonaws.com" });
        expect([...statement.Action].sort()).toEqual(["logs:CreateLogStream", "logs:PutLogEvents"]);

        // The log group's name is a literal, not a Ref: a Ref to itself inside its own property is a
        // circular dependency that CloudFormation rejects.
        expect(typeof group.properties.LogGroupName).toBe("string");
        expect(JSON.stringify(doc)).not.toContain(group.logicalId);

        const resource = SynthResult.flatten(statement.Resource);
        expect(resource).toBe(`${arnPrefix("logs")}:log-group:${group.properties.LogGroupName}:*`);

        expect(SynthResult.flatten(statement.Condition.StringEquals["aws:SourceAccount"])).toBe(
            ACCOUNT
        );
        expect(SynthResult.flatten(statement.Condition.ArnLike["aws:SourceArn"])).toBe(
            `${arnPrefix("events")}:rule/${busName}/*`
        );
    });

    test("the audit rule targets the log group by ARN, with no role", () => {
        const group = auditLogGroup(synth);
        const expectedArn = `${arnPrefix("logs")}:log-group:${group.properties.LogGroupName}`;
        const rules = synth.where("AWS::Events::Rule", (r) =>
            (r.properties.Targets ?? []).some(
                (t: any) => SynthResult.flatten(t.Arn) === expectedArn
            )
        );
        expect(rules).toHaveLength(1);
        const targets = rules[0].properties.Targets;
        expect(targets).toHaveLength(1);
        expect(targets[0].RoleArn).toBeUndefined();
        expect(rules[0].properties.EventPattern.source).toEqual([
            { prefix: expect.stringMatching(/\S/) },
        ]);
    });
});
