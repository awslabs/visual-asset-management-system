/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The state machine role's `batch:SubmitJob` grant on `job-definition/*` stays suppressed whether or
 * not the deployment account resolves (#477).
 *
 * `BatchSubmitJob` grants SubmitJob on `arn:<partition>:batch:<region>:<account>:job-definition/*`.
 * With the shipped templates' `env.account: null` and no AWS credentials (a CI job, a local check),
 * the account is the `AWS::AccountId` pseudo parameter and cdk-nag reports the resource as
 * `...:batch:us-east-1:<AWS::AccountId>:job-definition/*`. An `appliesTo` literal that interpolates
 * `Stack.of(this).account` carries an unresolved token instead, matches nothing, and the synth fails
 * on AwsSolutions-IAM5. The suppression is therefore a regex over the resource shape.
 *
 * Runs the AwsSolutions pack against the 3D preview thumbnail construct, the pipeline the defect was
 * found on; the other constructs fixed under #477 take the same regex. Both account forms are covered,
 * because the regex must keep matching the concrete account the literal matched.
 */

import * as cdk from "aws-cdk-lib";
import { Annotations, Match, Template } from "aws-cdk-lib/assertions";
import { AwsSolutionsChecks } from "cdk-nag";
import { Preview3dThumbnailConstruct } from "../../lib/nestedStacks/pipelines/preview/3dThumbnail/constructs/preview3dThumbnail-construct";
import { makePipelineHarness } from "../support/pipelineConstructHarness";

/** A cdk-nag finding id for SubmitJob on every job definition, in either account form. */
const JOB_DEFINITION_FINDING = String.raw`Resource::arn:<AWS::Partition>:batch:us-east-1:(<AWS::AccountId>|123456789012):job-definition/\*`;

interface NagResult {
    /** Unsuppressed AwsSolutions-IAM5 findings on job-definition/*. */
    errors: string[];
    /** The same finding reported as raised and suppressed (cdk-nag `logIgnores`). */
    suppressed: string[];
    /** The state machine role policy's SubmitJob resources, as emitted. */
    submitJobResources: unknown[];
}

/** The part of an emitted IAM policy statement this suite reads. */
interface PolicyStatement {
    Action: string | string[];
    Resource: unknown;
}

function synthWithNag(id: string, unresolvedAccount: boolean): NagResult {
    const h = makePipelineHarness(
        id,
        (c) => {
            c.app.pipelines.usePreview3dThumbnail.enabled = true;
            c.app.pipelines.usePreview3dThumbnail.autoRegisterWithVAMS = false;
        },
        { unresolvedAccount }
    );
    const nested = new Preview3dThumbnailConstruct(h.stack, "Preview3dThumbnailPipeline", {
        config: h.config,
        storageResources: h.storage,
        vpc: h.vpc,
        pipelineSubnets: h.subnets,
        pipelineSecurityGroups: h.securityGroups,
        lambdaCommonBaseLayer: h.lambdaCommonBaseLayer,
        importGlobalPipelineWorkflowV2FunctionName: "importGlobalPipelineWorkflow",
    });
    cdk.Aspects.of(h.stack).add(new AwsSolutionsChecks({ logIgnores: true, reports: false }));

    // A nested stack is not an artifact of its own, so its constructs' annotations are read from the
    // enclosing stack.
    const annotations = Annotations.fromStack(h.stack);
    const messages = (found: ReturnType<typeof annotations.findError>): string[] =>
        found.map((m) => String(m.entry.data));
    const errors = messages(
        annotations.findError(
            "*",
            Match.stringLikeRegexp(String.raw`^AwsSolutions-IAM5\[${JOB_DEFINITION_FINDING}\]`)
        )
    );
    const suppressed = messages(
        annotations.findInfo(
            "*",
            Match.stringLikeRegexp(
                String.raw`^CdkNagSuppression\[${JOB_DEFINITION_FINDING}\]: AwsSolutions-IAM5 was triggered but suppressed`
            )
        )
    );

    const submitJobResources = Object.values(
        Template.fromStack(nested).findResources("AWS::IAM::Policy")
    ).flatMap((policy) =>
        (policy.Properties.PolicyDocument.Statement as PolicyStatement[])
            .filter((s) => [s.Action].flat().includes("batch:SubmitJob"))
            .flatMap((s) => [s.Resource].flat())
    );

    return { errors, suppressed, submitJobResources };
}

describe.each([
    ["unresolved", true],
    ["concrete", false],
])("3D preview thumbnail job-definition IAM5 suppression, %s account", (label, unresolved) => {
    let result: NagResult;

    beforeAll(() => {
        result = synthWithNag(`JobDefinitionNag-${label}`, unresolved);
    });

    test("[control] the state machine role is granted SubmitJob on job-definition/*", () => {
        // Without the grant there is no finding to suppress, and the assertions below hold vacuously.
        const rendered = JSON.stringify(result.submitJobResources);
        expect(rendered).toContain(":job-definition/*");
        expect(rendered).toContain(unresolved ? '"Ref":"AWS::AccountId"' : ":123456789012:");
    });

    test("[control] cdk-nag raises the finding and reports it suppressed", () => {
        expect(result.suppressed.length).toBeGreaterThan(0);
    });

    test("no AwsSolutions-IAM5 job-definition finding is left unsuppressed", () => {
        expect(result.errors).toEqual([]);
    });
});
