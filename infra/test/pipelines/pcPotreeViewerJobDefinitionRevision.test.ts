/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Potree viewer pipeline submits both of its Batch jobs by the revision-less job definition ARN (#1033).
 *
 * An E57/PLY run is two Batch jobs in one state machine execution: PDAL, then Potree once PDAL finished.
 * The job definitions are replaced by every deploy that changes a container image (a new revision is
 * registered and the old one deregistered). With the revisioned ARN (`Ref` of the job definition) baked
 * into the state machine, a run that started before such a deploy submitted its Potree job against the
 * deregistered revision and failed. Submitting by `job-definition/<name>` makes Batch run the latest
 * ACTIVE revision, so the run finishes on the new definition.
 *
 * Asserted over the synthesized ASL, the IAM policy the state machine role carries, and the job
 * definitions themselves, in each shipped template.
 */

import { synthTemplate, SynthResult, TemplateName, Resource } from "../support/templateSynth";

const SUBMIT_STATES = ["PdalConverterBatchJob", "PotreeConverterBatchJob"];

function potreeStateMachine(s: SynthResult): { sm: Resource; asl: any } {
    const machines = s
        .ofType("AWS::StepFunctions::StateMachine")
        .filter((sm) => sm.logicalId.includes("PcPotreeViewerProcessingStateMachine"));
    expect(machines).toHaveLength(1);
    const asl = JSON.parse(SynthResult.flatten(machines[0].properties.DefinitionString));
    return { sm: machines[0], asl };
}

function potreeJobDefinitionNames(s: SynthResult, stack: string): string[] {
    return s
        .ofType("AWS::Batch::JobDefinition")
        .filter((jd) => jd.stack === stack)
        .map((jd) => String(jd.properties.JobDefinitionName))
        .sort();
}

describe.each(["commercial", "govcloud", "eusovereign"] as TemplateName[])(
    "%s: Potree viewer job definition references",
    (templateName) => {
        let synth: SynthResult;
        let sm: Resource;
        let asl: any;

        beforeAll(() => {
            synth = synthTemplate(templateName, {
                mutateKey: "potree-viewer-on",
                mutate: (c: any) => {
                    c.app.useGlobalVpc = { ...(c.app.useGlobalVpc ?? {}), enabled: true };
                    c.app.pipelines.usePreviewPcPotreeViewer.enabled = true;
                },
            });
            ({ sm, asl } = potreeStateMachine(synth));
        });

        test("[control] the PDAL and Potree submit states and both job definitions are emitted", () => {
            for (const state of SUBMIT_STATES) {
                expect(asl.States[state]?.Resource).toMatch(/:states:::batch:submitJob/);
            }
            const names = potreeJobDefinitionNames(synth, sm.stack);
            expect(names).toHaveLength(2);
            expect(names[0]).toMatch(/^PcPotreeViewerJob_PDAL/);
            expect(names[1]).toMatch(/^PcPotreeViewerJob_Potree/);
        });

        test("each submit names its job definition without a revision", () => {
            const names = potreeJobDefinitionNames(synth, sm.stack);
            const submitted = SUBMIT_STATES.map(
                (state) => asl.States[state].Parameters.JobDefinition
            );
            for (const arn of submitted) {
                expect(typeof arn).toBe("string");
                expect(arn).toMatch(/^arn:[^:]+:batch:[^:]+:[^:]+:job-definition\/[^:/]+$/);
            }
            expect(submitted.map((arn: string) => arn.split("/").pop()).sort()).toEqual(names);
        });

        test("the state machine definition takes no reference to a job definition resource", () => {
            // A `Ref` of AWS::Batch::JobDefinition resolves to the revisioned ARN, the #1033 failure.
            const jobDefinitionIds = new Set(
                synth
                    .ofType("AWS::Batch::JobDefinition")
                    .filter((jd) => jd.stack === sm.stack)
                    .map((jd) => jd.logicalId)
            );
            const refs =
                JSON.stringify(sm.properties.DefinitionString).match(/"Ref":"[^"]+"/g) ?? [];
            const toJobDefinitions = refs.filter((r) => jobDefinitionIds.has(r.slice(7, -1)));
            expect(toJobDefinitions).toEqual([]);
        });

        test("the state machine role may submit by the revision-less ARN", () => {
            const roleRef = sm.properties.RoleArn["Fn::GetAtt"][0];
            const policies = synth
                .ofType("AWS::IAM::Policy")
                .filter(
                    (p) =>
                        p.stack === sm.stack &&
                        (p.properties.Roles ?? []).some((r: any) => r.Ref === roleRef)
                );
            const submitResources = policies
                .flatMap((p) => p.properties.PolicyDocument.Statement)
                .filter((st: any) => [].concat(st.Action).includes("batch:SubmitJob" as never))
                .flatMap((st: any) => [].concat(st.Resource))
                .map((r: any) => SynthResult.flatten(r));
            const definitionPatterns = submitResources.filter((r: string) =>
                /:job-definition\//.test(r)
            );
            expect(definitionPatterns.length).toBeGreaterThan(0);
            // Unchanged scope: the grant BatchSubmitJob already emits is job-definition/* and nothing wider.
            for (const r of definitionPatterns) {
                expect(r).toMatch(/:job-definition\/\*$/);
                expect(r).not.toMatch(/:batch:\*|:\*:job-definition/);
            }
        });
    }
);
