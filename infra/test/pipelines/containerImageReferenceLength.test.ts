/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * No pipeline can name a container image AWS Batch will refuse.
 *
 * Batch and Amazon ECS cap a container image reference at 255 characters over the whole
 * `<account>.dkr.ecr.<region>.amazonaws.com/<repository>:<tag>` string. `lib/helper/containerImageTag.ts`
 * already truncates the tag for this reason, and its own header states the gap this file closes:
 * "Neither CDK synth nor a unit test sees this: the template is valid CloudFormation and the length is
 * only known once the repository URI resolves, so it surfaces as a deploy rollback."
 *
 * MEASURED, which is why this exists rather than being a precaution. Enabling
 * `useConversionCoordinateTransform.useCodeBuild` produced an auto-generated repository URI of 237
 * characters; with the 32-character content tag the reference was 270 and AWS Batch rejected every job
 * at submit with `Container.image should be 255 characters or less`. The failure is close to invisible:
 * the job never starts, so there is no container log, no exit code and no image-pull error — the
 * pipeline's state machine sits in its `waitForTaskToken` task and the workflow waits out its timeout.
 * A deploy, a synth and the whole existing suite were all green.
 *
 * The check cannot be "measure the URI", because an auto-generated name is a CloudFormation token at
 * synth and its length is unknowable here. So the rule is the one property that IS decidable at synth:
 * a repository whose auto-generated name would be too long must carry an EXPLICIT short name. The
 * reference deployment's measurements give the budget:
 *
 *     auto-named repositories measured   194 - 207 chars  -> reference 227 - 240, inside the cap
 *     coordinateTransform auto-named     237 chars        -> reference 270, REJECTED
 *
 * so an auto-named repository is only safe while its construct path stays as shallow as the others'.
 */

import { synthTemplate, SynthResult, TemplateName } from "../support/templateSynth";

/** Longest registry host this can produce: 12-digit account + the longest ECR regional hostname. */
const REGISTRY_HOST_BUDGET = "123456789012.dkr.ecr.ap-southeast-4.amazonaws.com/".length;
/** `:` plus the truncated content tag, from lib/helper/containerImageTag.ts. */
const TAG_BUDGET = 1 + 32;
const MAX_IMAGE_REFERENCE_LENGTH = 255;

/**
 * Pipelines whose ECR repository MUST carry an explicit name because the auto-generated one is known to
 * exceed the cap. Keyed by a fragment of the construct id, with the measured length that put it here.
 */
const MUST_BE_EXPLICITLY_NAMED: Record<string, string> = {
    EcrRepoCoordTransform:
        "auto-generated URI measured 237 chars on the reference deployment; 237 + 33 = 270 > 255, and " +
        "AWS Batch rejected every job at submit",
};

function ecrRepositories(s: SynthResult) {
    return s.ofType("AWS::ECR::Repository");
}

describe.each(["commercial", "govcloud", "eusovereign"] as TemplateName[])(
    "%s: container image reference length",
    (templateName) => {
        let synth: SynthResult;

        beforeAll(() => {
            // The CodeBuild path is what creates these repositories, and it is off by default for
            // coordinateTransform — the very reason this defect shipped unnoticed. Turn it on for every
            // pipeline that has the flag, so the repositories actually exist in the assembly.
            synth = synthTemplate(templateName, {
                mutateKey: "all-codebuild-on",
                mutate: (c: any) => {
                    // A CodeBuild pipeline's project runs in the pipeline VPC, so the construct throws
                    // «CannotConfigureSecurityGroupAllow» without one. The commercial template ships
                    // useGlobalVpc off (the restricted templates require it on), so enabling CodeBuild
                    // alone breaks the synth before any repository is created — enable the VPC too, which
                    // is what a deployment actually using CodeBuild has.
                    c.app.useGlobalVpc = { ...(c.app.useGlobalVpc ?? {}), enabled: true };
                    const pipelines = c.app?.pipelines ?? {};
                    for (const key of Object.keys(pipelines)) {
                        const entry = pipelines[key];
                        if (entry && typeof entry === "object" && "useCodeBuild" in entry) {
                            entry.enabled = true;
                            entry.useCodeBuild = true;
                        }
                    }
                    // Cosmos and GR00T require an accepted EULA / token field to be non-empty before
                    // their constructs build; supply synth-only values.
                    if (pipelines.useNvidiaCosmos)
                        pipelines.useNvidiaCosmos.huggingFaceToken = "synth-only";
                    if (pipelines.useNvidiaCosmos3)
                        pipelines.useNvidiaCosmos3.huggingFaceToken = "synth-only";
                    if (pipelines.useNvidiaGr00t)
                        pipelines.useNvidiaGr00t.huggingFaceToken = "synth-only";
                    if (pipelines.useIsaacLabTraining)
                        pipelines.useIsaacLabTraining.acceptNvidiaEula = true;
                },
            });
        });

        it("emits ECR repositories at all", () => {
            // The control. Every assertion below is satisfied by an assembly that emitted none — and
            // that is the likely failure here, because these repositories only exist when useCodeBuild
            // is on, which the shipped templates do not set.
            expect(ecrRepositories(synth).length).toBeGreaterThan(0);
        });

        it("every repository known to exceed the cap carries an explicit name", () => {
            const offenders: string[] = [];
            for (const [fragment, why] of Object.entries(MUST_BE_EXPLICITLY_NAMED)) {
                const matches = ecrRepositories(synth).filter((r) =>
                    r.logicalId.includes(fragment)
                );
                if (matches.length === 0) {
                    offenders.push(
                        `${fragment}: no such repository in this assembly — the exemption is stale, or ` +
                            `the pipeline stopped being built`
                    );
                    continue;
                }
                for (const repo of matches) {
                    if (!repo.properties.RepositoryName) {
                        offenders.push(
                            `${repo.stack}/${repo.logicalId} has no RepositoryName. ${why}. An ` +
                                `auto-generated name is derived from the nested-stack path and cannot be ` +
                                `shortened; give it an explicit name.`
                        );
                    }
                }
            }
            expect(offenders).toEqual([]);
        });

        it("every EXPLICIT repository name leaves room for the host and the tag", () => {
            const named = ecrRepositories(synth).filter((r) => r.properties.RepositoryName);
            // Control: an assembly with no explicitly named repository would pass this vacuously, and
            // the rule above requires at least one.
            expect(named.length).toBeGreaterThan(0);

            const offenders: string[] = [];
            for (const repo of named) {
                const name = SynthResult.flatten(repo.properties.RepositoryName);
                const reference = REGISTRY_HOST_BUDGET + name.length + TAG_BUDGET;
                if (reference > MAX_IMAGE_REFERENCE_LENGTH) {
                    offenders.push(
                        `${repo.logicalId}: name ${name.length} chars -> reference ~${reference} > ` +
                            `${MAX_IMAGE_REFERENCE_LENGTH} (${name})`
                    );
                }
            }
            expect(offenders).toEqual([]);
        });

        it("an explicit repository name is a legal ECR name", () => {
            // A name Batch would accept for length is still refused by ECR if it is uppercase or
            // carries an illegal character, and that failure arrives at deploy rather than at synth.
            const offenders: string[] = [];
            for (const repo of ecrRepositories(synth).filter((r) => r.properties.RepositoryName)) {
                const name = SynthResult.flatten(repo.properties.RepositoryName);
                if (!/^[a-z0-9][a-z0-9._/-]{1,255}$/.test(name)) {
                    offenders.push(`${repo.logicalId}: ${name} is not a legal ECR repository name`);
                }
            }
            expect(offenders).toEqual([]);
        });

        /**
         * An explicitly named repository must make its own rename re-fire the image build.
         *
         * `RepositoryName` is a REPLACEMENT property, so a rename destroys the old repository with every
         * image in it and creates an empty one. The build that should repopulate it runs from a custom
         * resource, and AWS CloudFormation invokes a custom resource's Update handler only when one of its
         * PROPERTIES changes — a rename changes neither the project name nor the source hash. MEASURED:
         * after the rename the new repository held 0 images while the Batch job definition referenced
         * `f0462fcacd8a838acf19eb56badf08a2`, the deploy exited 0, and the image had to be built by hand.
         *
         * An auto-named repository has no rename to trigger this, which is why the rule applies only to
         * the explicitly named ones.
         */
        it("an explicitly named repository's build trigger depends on that repository", () => {
            const named = ecrRepositories(synth).filter((r) => r.properties.RepositoryName);
            expect(named.length).toBeGreaterThan(0);

            const offenders: string[] = [];
            for (const repo of named) {
                // The trigger lives in the same nested template as its repository.
                const triggers = synth
                    .ofType("AWS::CloudFormation::CustomResource")
                    .concat(synth.ofType("Custom::AWS"))
                    .filter((c) => c.stack === repo.stack);
                const buildTriggers = triggers.filter((c) => "ProjectName" in (c.properties ?? {}));

                if (buildTriggers.length === 0) {
                    offenders.push(
                        `${repo.logicalId}: no build-trigger custom resource found in ${repo.stack} — ` +
                            `either the trigger moved or this test no longer identifies it, and in ` +
                            `either case the rule below is not being checked`
                    );
                    continue;
                }
                for (const trigger of buildTriggers) {
                    const props = Object.keys(trigger.properties ?? {});
                    // Any property carrying the repository is sufficient; the URI is what the construct
                    // passes. Without one, a rename is a silent no-op.
                    const dependsOnRepo = props.some((p) => /repositor/i.test(p));
                    if (!dependsOnRepo) {
                        offenders.push(
                            `${trigger.logicalId} triggers the build for the explicitly named ` +
                                `${SynthResult.flatten(
                                    repo.properties.RepositoryName
                                )} but carries no ` +
                                `repository-derived property (has: ${props.join(
                                    ", "
                                )}). Renaming the ` +
                                `repository replaces it EMPTY and this trigger will not re-fire.`
                        );
                    }
                }
            }
            expect(offenders).toEqual([]);
        });
    }
);
