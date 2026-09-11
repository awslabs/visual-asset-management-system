/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The Deadline Cloud job-status EventBridge rule must deliver IN-FLIGHT status changes, not only
 * terminal ones.
 *
 * Why this is worth a test of its own: the callback lambda does two separate jobs from the same
 * event, and they need it at different times. It resolves the Step Functions task token only on a
 * TERMINAL status, but it REGISTERS the Deadline job as the pipeline execution's sub-process on
 * EVERY status it is handed — and registration is what makes the job abortable, because
 * `execution abort` cancels registered Deadline jobs through UpdateJob and can only cancel what was
 * registered.
 *
 * With the rule filtered to `taskRunStatus: [SUCCEEDED, FAILED, CANCELED, NOT_COMPATIBLE]`, a job
 * was registered exactly when there was nothing left to cancel. Aborting an in-flight execution
 * stopped the state machine and left the farm job running with a task token nobody would resolve —
 * observed live on a real farm (S29-DEPLOY-009): abort reported success, and the job stayed
 * `READY` with `targetTaskRunStatus: None`, i.e. no cancellation was ever requested.
 *
 * The lambda side was already correct — `test_non_terminal_status_registers_the_job_and_leaves_the_
 * token_open` (S2-BACKEND-045) asserts an in-flight status registers and leaves the token open. This
 * filter is what kept that fix unreachable in practice, which is the pattern the test exists to
 * prevent recurring: a fix that is real in the code and absent in the deployment.
 */

import { synthTemplate } from "../support/templateSynth";

// Deadline Cloud is commercial-only and off by default in every shipped template, so the rule does
// not exist unless the execution type is enabled.
const synth = () =>
    synthTemplate("commercial", {
        mutateKey: "deadlineCloudEnabled",
        mutate: (c: any) => {
            c.app.pipelines.deadlineCloudExecutionTypeEnabled = true;
        },
    });

const statusRules = () =>
    synth()
        .ofType("AWS::Events::Rule")
        .filter((r) => {
            const p = r.properties?.EventPattern ?? {};
            const detailType = p["detail-type"] ?? p.detailType ?? [];
            return (
                JSON.stringify(p.source ?? []).includes("aws.deadline") &&
                JSON.stringify(detailType).includes("Job Run Status Change")
            );
        });

describe("Deadline Cloud job-status EventBridge rule", () => {
    it("exists when the DeadlineCloud execution type is enabled", () => {
        // Positive control for every assertion below: a rule that was never emitted would satisfy
        // "carries no terminal-only filter" trivially.
        expect(statusRules().length).toBeGreaterThan(0);
    });

    it("does not filter job status changes to terminal statuses only", () => {
        for (const rule of statusRules()) {
            const detail = (rule.properties?.EventPattern ?? {}).detail ?? {};
            const statuses: string[] = detail.taskRunStatus ?? [];
            if (statuses.length === 0) continue; // no filter at all — in-flight statuses arrive
            // If a filter is ever reintroduced it must still admit an in-flight status, or
            // registration returns to happening only when the job is already over.
            const admitsInFlight = statuses.some((s) =>
                ["RUNNING", "READY", "ASSIGNED", "STARTING", "SCHEDULED", "PENDING"].includes(s)
            );
            expect(admitsInFlight).toBe(true);
        }
    });

    it("keeps a dead-letter queue on the rule target so a failed delivery is not silently dropped", () => {
        for (const rule of statusRules()) {
            const targets: any[] = rule.properties?.Targets ?? [];
            expect(targets.length).toBeGreaterThan(0);
            for (const t of targets) {
                expect(t.DeadLetterConfig?.Arn).toBeDefined();
            }
        }
    });

    /**
     * The abort path's `deadline:UpdateJob`/`ListJobs`/`GetJob` grant must follow the same flag as the
     * rest of the Deadline surface.
     *
     * With the execution type disabled, no pipeline can be registered as `DeadlineCloud`, so no
     * execution can hold a job to cancel — and an ungated grant would leave `executionService` holding
     * standing `UpdateJob` on every farm in the account for a code path that cannot run. Measured on
     * the deployed stack after the execution type was turned back off: the role still carried all
     * three actions on `farm/*`.
     *
     * Asserted as a pair. The disabled half alone is satisfied by a grant that was never emitted in
     * either state, which would mean abort silently cannot cancel anything.
     */
    describe("the abort path's Deadline grant", () => {
        // CDK spills an over-long inline policy into an AWS::IAM::ManagedPolicy, so scanning only
        // AWS::IAM::Policy reports a grant that IS present as missing.
        const deadlineActionsOnExecutionService = (enabled: boolean): string[] => {
            const s = enabled
                ? synth()
                : synthTemplate("commercial", {
                      mutateKey: "deadlineCloudDisabled",
                      mutate: (c: any) => {
                          c.app.pipelines.deadlineCloudExecutionTypeEnabled = false;
                      },
                  });
            const found: string[] = [];
            for (const type of ["AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"]) {
                for (const pol of s.ofType(type)) {
                    // The policy's own logical id names the role it defaults for.
                    if (!/executionService/i.test(pol.logicalId)) continue;
                    for (const stmt of pol.properties?.PolicyDocument?.Statement ?? []) {
                        const actions = ([] as string[]).concat(stmt.Action ?? []);
                        found.push(...actions.filter((a) => String(a).startsWith("deadline:")));
                    }
                }
            }
            return found;
        };

        it("is granted when the DeadlineCloud execution type is enabled", () => {
            const actions = deadlineActionsOnExecutionService(true);
            expect(actions).toEqual(
                expect.arrayContaining([
                    "deadline:UpdateJob",
                    "deadline:ListJobs",
                    "deadline:GetJob",
                ])
            );
        });

        it("is absent when the DeadlineCloud execution type is disabled", () => {
            expect(deadlineActionsOnExecutionService(false)).toEqual([]);
        });
    });

    /**
     * The error handler needs the same three Deadline actions abort does, on a path abort cannot
     * reach.
     *
     * `handleExecutionError` runs on every caught workflow failure and stamps the in-flight pipeline
     * rows terminal. A terminal row is no longer a candidate for the abort API, so a farm job left
     * running at that point has no in-product remedy at all — and the task is
     * `createJob.waitForTaskToken`, so Step Functions holds the token and not the job. Without the
     * grant the cancel is attempted and denied, which the handler records as "may still be running"
     * on the pipeline log row rather than actually stopping the job.
     *
     * `ListJobs` + `GetJob` are what reach a job that was never REGISTERED. Registration comes from
     * the job-status callback, which Deadline invokes on a status CHANGE, so a job sitting queued
     * with no worker assigned never produces one — exactly the job most worth cancelling, since it
     * has consumed nothing yet. Both paths share one implementation, so a grant on only one of them
     * makes the shared helper reachable from one caller and denied from the other.
     */
    describe("the error handler's Deadline grant", () => {
        // CDK spills an over-long inline policy into an AWS::IAM::ManagedPolicy, so scanning only
        // AWS::IAM::Policy reports a grant that IS present as missing.
        const deadlineActionsOnErrorHandler = (enabled: boolean): string[] => {
            const s = enabled
                ? synth()
                : synthTemplate("commercial", {
                      // Same key as the abort-path block above: identical mutation, so this is a
                      // cache hit rather than a second ~20 s synth.
                      mutateKey: "deadlineCloudDisabled",
                      mutate: (c: any) => {
                          c.app.pipelines.deadlineCloudExecutionTypeEnabled = false;
                      },
                  });
            const found: string[] = [];
            for (const type of ["AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"]) {
                for (const pol of s.ofType(type)) {
                    // The policy's own logical id names the role it defaults for.
                    if (!/handleExecutionError/i.test(pol.logicalId)) continue;
                    for (const stmt of pol.properties?.PolicyDocument?.Statement ?? []) {
                        const actions = ([] as string[]).concat(stmt.Action ?? []);
                        found.push(...actions.filter((a) => String(a).startsWith("deadline:")));
                    }
                }
            }
            return found;
        };

        it("is granted when the DeadlineCloud execution type is enabled", () => {
            expect(deadlineActionsOnErrorHandler(true)).toEqual(
                expect.arrayContaining([
                    "deadline:UpdateJob",
                    "deadline:ListJobs",
                    "deadline:GetJob",
                ])
            );
        });

        it("is absent when the DeadlineCloud execution type is disabled", () => {
            // Paired with the above: the disabled half alone is satisfied by a grant that was never
            // emitted in either state, which would mean the error handler can never cancel anything.
            expect(deadlineActionsOnErrorHandler(false)).toEqual([]);
        });

        /**
         * Discovery reads farmId/queueId from the pipeline DEFINITION, not from any execution row —
         * they are not on the pipeline execution and were never on it. Without this read the
         * handler resolves ("", "") and reports the job as possibly still running while holding
         * every Deadline action it needs, which looks like a Deadline problem and is a DynamoDB one.
         * Gated with the actions, so the disabled half pins that it is not standing access.
         */
        const readsPipelineDefinitions = (enabled: boolean): boolean => {
            const s = enabled
                ? synth()
                : synthTemplate("commercial", {
                      mutateKey: "deadlineCloudDisabled",
                      mutate: (c: any) => {
                          c.app.pipelines.deadlineCloudExecutionTypeEnabled = false;
                      },
                  });
            for (const type of ["AWS::IAM::Policy", "AWS::IAM::ManagedPolicy"]) {
                for (const pol of s.ofType(type)) {
                    if (!/handleExecutionError/i.test(pol.logicalId)) continue;
                    for (const stmt of pol.properties?.PolicyDocument?.Statement ?? []) {
                        const actions = ([] as string[]).concat(stmt.Action ?? []);
                        if (!actions.some((a) => String(a) === "dynamodb:GetItem")) continue;
                        const resources = JSON.stringify(stmt.Resource ?? "");
                        if (/PipelineStorageTableV2|pipelineStorageV2/i.test(resources))
                            return true;
                    }
                }
            }
            return false;
        };

        it("can read the pipeline definitions the farm and queue come from", () => {
            expect(readsPipelineDefinitions(true)).toBe(true);
        });

        it("does not read the pipeline definitions when the execution type is disabled", () => {
            expect(readsPipelineDefinitions(false)).toBe(false);
        });
    });

    it("still routes lifecycle failure states, which never reach a task-run status", () => {
        const lifecycle = synth()
            .ofType("AWS::Events::Rule")
            .filter((r) => {
                const p = r.properties?.EventPattern ?? {};
                const detailType = p["detail-type"] ?? p.detailType ?? [];
                return JSON.stringify(detailType).includes("Job Lifecycle Status Change");
            });
        expect(lifecycle.length).toBeGreaterThan(0);
        const statuses = JSON.stringify(lifecycle[0].properties?.EventPattern?.detail ?? {});
        expect(statuses).toContain("CREATE_FAILED");
        expect(statuses).toContain("UPLOAD_FAILED");
    });
});
