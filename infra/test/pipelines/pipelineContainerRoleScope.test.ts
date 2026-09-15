/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A pipeline container's job role must not carry a managed policy that grants `iam:PassRole` broadly.
 *
 * A Batch JOB role differs from an execution role in one way that matters: its credentials are
 * reachable from inside the container, through the ECS task metadata endpoint. The Splat Toolbox
 * container runs third-party 3D-reconstruction code — cloned from a public repository at synthesis —
 * as root, in privileged mode, over user-uploaded video and archives. Anything on that role is
 * reachable by a code-execution defect anywhere in that toolchain.
 *
 * It carried `AmazonSageMakerFullAccess`, which grants `iam:PassRole` on `arn:aws:iam::*:role/*`
 * conditioned only on the target service being one of sagemaker / states / glue / events / robomaker /
 * bedrock. That is a privilege-escalation path from a data-processing container to any role in the
 * account those services can assume. It also trusted `sagemaker.amazonaws.com` to assume the role.
 *
 * Neither was used. The container is written to run under either AWS Batch or Amazon SageMaker, which
 * is where they came from, but VAMS creates only a Batch job definition for it and the pipeline makes
 * no SageMaker API call: the single "SageMaker resource config" reference reads the local file
 * `/opt/ml/input/config/resourceconfig.json`, and no `sagemaker` boto3 client exists anywhere under
 * `backendPipelines/3dRecon/splatToolbox/`.
 *
 * The assertions are written as a policy over EVERY role in the assembly rather than as a check on this
 * one construct, because the reasoning is not specific to Splat Toolbox — the next container pipeline
 * would reach for the same managed policy for the same reason.
 */

import { SynthResult, synthTemplate, TemplateName } from "../support/templateSynth";

/** Every shipped configuration, so a pipeline enabled in only one of them is still covered. */
const TEMPLATES: TemplateName[] = ["commercial", "govcloud", "eusovereign"];

/**
 * Splat Toolbox ships DISABLED in every template, so a synth of the shipped configuration emits none of
 * its roles — and every assertion below then passes without ever seeing the subject. That is not a
 * theoretical concern: the first version of this suite passed with `AmazonSageMakerFullAccess` restored,
 * which is what revealed it. The container pipelines are switched on here so the roles exist.
 *
 * `useCodeBuild` is set so the image comes from an ECR URI instead of a local Docker build, which the T1
 * harness cannot perform.
 */
function enableContainerPipelines(c: any) {
    // A container pipeline needs the global VPC: its Batch compute and its CodeBuild project both run in
    // private subnets, and CDK refuses a Project with a security group and no VPC. The commercial
    // template ships with the VPC off, which is why enabling only the pipeline failed there while the two
    // restricted templates (VPC on by default) synthesized fine.
    c.app.useGlobalVpc.enabled = true;

    const pipelines = c.app.pipelines;
    if (pipelines.useSplatToolbox) {
        pipelines.useSplatToolbox.enabled = true;
        pipelines.useSplatToolbox.useCodeBuild = true;
        pipelines.useSplatToolbox.autoRegisterWithVAMS = false;
    }
}

const synthWithContainerPipelines = (name: TemplateName): SynthResult =>
    synthTemplate(name, {
        mutate: enableContainerPipelines,
        mutateKey: "container-pipelines-enabled",
    });

/**
 * AWS managed policies that grant `iam:PassRole` over a role wildcard.
 *
 * Deliberately a short, named list rather than a pattern: the property that matters is not the policy's
 * name but what it grants, and that can only be known per policy. An entry here is a policy someone has
 * read the contents of.
 */
const PASSROLE_GRANTING_MANAGED_POLICIES = [
    "AmazonSageMakerFullAccess",
    "AWSGlueConsoleFullAccess",
    "AWSStepFunctionsFullAccess",
    "IAMFullAccess",
    "AdministratorAccess",
    "PowerUserAccess",
];

describe.each(TEMPLATES)("%s: pipeline container job roles", (templateName) => {
    let synth: SynthResult;

    beforeAll(() => {
        synth = synthWithContainerPipelines(templateName);
    });

    test("the Splat Toolbox container job role IS in this synth", () => {
        // The control that matters. Every "no role carries X" assertion below is satisfied by a synth
        // that does not contain the role at all, and that is exactly how the first version of this
        // suite passed with the defect restored.
        const roles = synth.ofType("AWS::IAM::Role");
        expect(roles.length).toBeGreaterThan(0);
        const splatJobRole = roles.filter((r) => /SplatToolboxContainerJobRole/i.test(r.logicalId));
        expect(splatJobRole.length).toBeGreaterThan(0);
    });

    test("no role carries a managed policy that grants iam:PassRole over a role wildcard", () => {
        const offenders: string[] = [];
        for (const role of synth.ofType("AWS::IAM::Role")) {
            const arns = (role.properties.ManagedPolicyArns ?? []).map((a: unknown) =>
                SynthResult.flatten(a)
            );
            for (const arn of arns) {
                for (const policy of PASSROLE_GRANTING_MANAGED_POLICIES) {
                    if (arn.includes(policy)) {
                        offenders.push(`${role.stack}/${role.logicalId}: ${policy}`);
                    }
                }
            }
        }
        expect(offenders).toEqual([]);
    });

    test("no role trusts sagemaker.amazonaws.com, since VAMS creates no SageMaker resource", () => {
        // The trust half. Left in place it lets anything that can call SageMaker on the account's behalf
        // assume a role built for a Batch container.
        const offenders: string[] = [];
        for (const role of synth.ofType("AWS::IAM::Role")) {
            const trust = JSON.stringify(role.properties.AssumeRolePolicyDocument ?? {});
            if (trust.includes("sagemaker.amazonaws.com")) {
                offenders.push(`${role.stack}/${role.logicalId}`);
            }
        }
        expect(offenders).toEqual([]);
    });

    test("no SageMaker resource is created, which is why neither is needed", () => {
        // The fact the removal rests on. If a SageMaker resource ever appears, the reasoning above stops
        // holding and this test says so rather than the removal quietly becoming wrong.
        const sagemakerTypes = synth.resources
            .map((r) => r.type)
            .filter((t) => t.startsWith("AWS::SageMaker::"));
        expect(sagemakerTypes).toEqual([]);
    });
});
