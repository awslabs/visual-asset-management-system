/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { validatePipeline } from "./pipelineValidation";
import type { Pipeline } from "../types";

describe("pipelineValidation", () => {
    it("accepts a valid Lambda pipeline", () => {
        const r = validatePipeline({
            pipelineName: "test",
            executionConfig: { executionType: "Lambda" },
        } as any);
        expect(r.ok).toBe(true);
    });

    it("rejects missing pipelineName", () => {
        const r = validatePipeline({
            executionConfig: { executionType: "Lambda" },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("rejects invalid pipelineId pattern", () => {
        const r = validatePipeline({
            pipelineId: "a b!",
            pipelineName: "test",
            executionConfig: { executionType: "Lambda" },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("accepts valid pipelineId pattern", () => {
        const r = validatePipeline({
            pipelineId: "valid-pipeline_123",
            pipelineName: "test",
            executionConfig: { executionType: "Lambda" },
        } as any);
        expect(r.ok).toBe(true);
    });

    it("rejects taskTimeout over one week", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "Lambda", taskTimeout: "999999999" },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("rejects taskTimeout of zero", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "Lambda", taskTimeout: "0" },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("rejects negative taskTimeout", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "Lambda", taskTimeout: "-10" },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("rejects non-integer taskTimeout", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "Lambda", taskTimeout: "123.45" },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("accepts valid taskTimeout within range", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "Lambda", taskTimeout: "3600" },
        } as any);
        expect(r.ok).toBe(true);
    });

    it("accepts taskTimeout at max (604800)", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "Lambda", taskTimeout: "604800" },
        } as any);
        expect(r.ok).toBe(true);
    });

    it("accepts blank timeouts (the backend treats an empty value as no timeout)", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "Lambda",
                taskTimeout: "",
                taskHeartbeatTimeout: "",
            },
        } as any);
        expect(r.ok).toBe(true);
    });

    it("rejects taskHeartbeatTimeout over one week", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "Lambda", taskHeartbeatTimeout: "999999999" },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("SQS requires queueUrl", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "SQS" },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("accepts valid SQS with queueUrl", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "SQS",
                sqs: { queueUrl: "https://sqs.us-east-1.amazonaws.com/123456789012/queue" },
            },
        } as any);
        expect(r.ok).toBe(true);
    });

    it("EventBridge requires busArn", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "EventBridge",
                eventBridge: { source: "test", detailType: "test" },
            },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("EventBridge requires source", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "EventBridge",
                eventBridge: { busArn: "arn:aws:events:...", detailType: "test" },
            },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("EventBridge requires detailType", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "EventBridge",
                eventBridge: { busArn: "arn:aws:events:...", source: "test" },
            },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("accepts valid EventBridge with all fields", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "EventBridge",
                eventBridge: {
                    busArn: "arn:aws:events:us-east-1:123456789012:event-bus/my-bus",
                    source: "test",
                    detailType: "test",
                },
            },
        } as any);
        expect(r.ok).toBe(true);
    });

    it("rejects a malformed SQS queue URL", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "SQS", sqs: { queueUrl: "my queue" } },
        } as any);
        expect(r.ok).toBe(false);
        expect((r.errors as Record<string, string>)["executionConfig.sqs.queueUrl"]).toMatch(
            /valid SQS queue URL/
        );
    });

    it("rejects a malformed event-bus ARN", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "EventBridge",
                eventBridge: {
                    busArn: "arn:aws:events:...",
                    source: "test",
                    detailType: "test",
                },
            },
        } as any);
        expect(r.ok).toBe(false);
        expect((r.errors as Record<string, string>)["executionConfig.eventBridge.busArn"]).toMatch(
            /event-bus ARN/
        );
    });

    it("rejects an EventBridge source using the reserved aws. prefix", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "EventBridge",
                eventBridge: {
                    busArn: "arn:aws-us-gov:events:us-gov-west-1:123456789012:event-bus/my-bus",
                    source: "aws.events",
                    detailType: "test",
                },
            },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("rejects a malformed Lambda resourceId", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "Lambda",
                lambda: { resourceId: "foo bar!" },
            },
        } as any);
        expect(r.ok).toBe(false);
        expect((r.errors as Record<string, string>)["executionConfig.lambda.resourceId"]).toMatch(
            /function ARN or a valid function name/
        );
    });

    it("accepts a Lambda resourceId given as a function name or an ARN", () => {
        const byName = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "Lambda", lambda: { resourceId: "my-function" } },
        } as any);
        expect(byName.ok).toBe(true);

        const byArn = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "Lambda",
                lambda: {
                    resourceId: "arn:aws:lambda:us-east-1:123456789012:function:my-function",
                },
            },
        } as any);
        expect(byArn.ok).toBe(true);
    });

    it("rejects an over-long pipelineName, category and description", () => {
        const r = validatePipeline({
            pipelineName: "n".repeat(257),
            category: "c".repeat(257),
            description: "d".repeat(1025),
            executionConfig: { executionType: "Lambda" },
        } as any);
        expect(r.ok).toBe(false);
        const errors = r.errors as Record<string, string>;
        expect(errors.pipelineName).toBeDefined();
        expect(errors.category).toBeDefined();
        expect(errors.description).toBeDefined();
    });

    it("DeadlineCloud requires farmId+queueId and Enabled callback", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "DeadlineCloud", waitForCallback: "Disabled" },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("DeadlineCloud rejects without farmId", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "DeadlineCloud",
                waitForCallback: "Enabled",
                deadlineCloud: { queueId: "queue-123" },
            },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("DeadlineCloud rejects without queueId", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "DeadlineCloud",
                waitForCallback: "Enabled",
                deadlineCloud: { farmId: "farm-123" },
            },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("accepts valid DeadlineCloud with farmId+queueId, a template and Enabled callback", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "DeadlineCloud",
                waitForCallback: "Enabled",
                deadlineCloud: {
                    farmId: "farm-123",
                    queueId: "queue-123",
                    template: "specificationVersion: jobtemplate-2023-09",
                },
            },
        } as any);
        expect(r.ok).toBe(true);
    });

    it("accepts blank DeadlineCloud numeric settings", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "DeadlineCloud",
                waitForCallback: "Enabled",
                deadlineCloud: {
                    farmId: "farm-123",
                    queueId: "queue-123",
                    template: "specificationVersion: jobtemplate-2023-09",
                    // A blank number input yields NaN under valueAsNumber and "" without it.
                    priority: NaN,
                    maxRetriesPerTask: "",
                    maxFailedTasksCount: undefined,
                },
            },
        } as any);
        expect(r.ok).toBe(true);
    });

    it("rejects invalid executionType", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "InvalidType" as any },
        } as any);
        expect(r.ok).toBe(false);
    });

    // Every stored pipeline carries all four per-type sub-blocks, empty for the unused types
    // (common/workflows/pipelineRecords.py build_pipeline_execution_config), so the irrelevant ones
    // must not be judged against their required fields.
    describe("the stored shape every pipeline record carries", () => {
        const storedLambdaConfig = {
            executionType: "Lambda" as const,
            waitForCallback: "Disabled" as const,
            taskTimeout: "",
            taskHeartbeatTimeout: "",
            lambda: {},
            sqs: {},
            eventBridge: {},
            deadlineCloud: {},
        };

        it("accepts a stored Lambda pipeline whose unused sub-blocks are empty objects", () => {
            const r = validatePipeline({
                pipelineId: "stored-pipe",
                pipelineName: "Stored",
                executionConfig: storedLambdaConfig,
            } as any);
            expect(r.errors).toBeUndefined();
            expect(r.ok).toBe(true);
        });

        it("accepts a stored SQS pipeline whose other sub-blocks are empty objects", () => {
            const r = validatePipeline({
                pipelineName: "Stored SQS",
                executionConfig: {
                    ...storedLambdaConfig,
                    executionType: "SQS",
                    sqs: { queueUrl: "https://sqs.us-east-1.amazonaws.com/123456789012/queue" },
                },
            } as any);
            expect(r.ok).toBe(true);
        });

        it("accepts a stored DeadlineCloud pipeline whose other sub-blocks are empty objects", () => {
            const r = validatePipeline({
                pipelineName: "Stored DC",
                executionConfig: {
                    ...storedLambdaConfig,
                    executionType: "DeadlineCloud",
                    waitForCallback: "Enabled",
                    deadlineCloud: {
                        farmId: "farm-1",
                        queueId: "queue-1",
                        template: "specificationVersion: jobtemplate-2023-09",
                    },
                },
            } as any);
            expect(r.ok).toBe(true);
        });

        it("ignores a stale sub-block left by an abandoned execution type", () => {
            const r = validatePipeline({
                pipelineName: "Explored types",
                executionConfig: {
                    executionType: "Lambda",
                    // What react-hook-form retains after the user selected SQS, typed nothing, and
                    // switched back to Lambda (shouldUnregister defaults to false).
                    sqs: { queueUrl: "" },
                    eventBridge: { busArn: "", source: "", detailType: "" },
                },
            } as any);
            expect(r.errors).toBeUndefined();
            expect(r.ok).toBe(true);
        });

        it("still requires the ACTIVE type's fields", () => {
            const r = validatePipeline({
                pipelineName: "x",
                executionConfig: { ...storedLambdaConfig, executionType: "SQS" },
            } as any);
            expect(r.ok).toBe(false);
            expect((r.errors as Record<string, string>)["executionConfig.sqs.queueUrl"]).toBe(
                "SQS queueUrl is required"
            );
        });
    });

    // The ASL builder embeds the OpenJD template verbatim and refuses to generate a task state
    // without it (common/workflows/stepfunctions_builder.py DeadlineCloudTaskBuilder), so a pipeline
    // saved without one breaks every workflow that references it.
    describe("DeadlineCloud template and bounded settings", () => {
        const dcConfig = (deadlineCloud: Record<string, any>) => ({
            pipelineName: "x",
            executionConfig: {
                executionType: "DeadlineCloud" as const,
                waitForCallback: "Enabled" as const,
                deadlineCloud: { farmId: "farm-1", queueId: "queue-1", ...deadlineCloud },
            },
        });

        it("requires the job template", () => {
            const r = validatePipeline(dcConfig({}) as any);
            expect(r.ok).toBe(false);
            expect(
                (r.errors as Record<string, string>)["executionConfig.deadlineCloud.template"]
            ).toMatch(/template is required/);
        });

        it("rejects a lower-case templateType the backend refuses", () => {
            const r = validatePipeline(dcConfig({ template: "t:", templateType: "yaml" }) as any);
            expect(r.ok).toBe(false);
            expect(
                (r.errors as Record<string, string>)["executionConfig.deadlineCloud.templateType"]
            ).toMatch(/JSON, YAML/);
        });

        it("accepts the two templateType values the backend allows", () => {
            ["JSON", "YAML"].forEach((templateType) => {
                expect(validatePipeline(dcConfig({ template: "t:", templateType }) as any).ok).toBe(
                    true
                );
            });
        });

        it("rejects negative priority and retry counts", () => {
            const r = validatePipeline(
                dcConfig({
                    template: "t:",
                    priority: -5,
                    maxRetriesPerTask: -3,
                    maxFailedTasksCount: -1,
                }) as any
            );
            expect(r.ok).toBe(false);
            const errors = r.errors as Record<string, string>;
            expect(errors["executionConfig.deadlineCloud.priority"]).toMatch(/negative/);
            expect(errors["executionConfig.deadlineCloud.maxRetriesPerTask"]).toMatch(/negative/);
            expect(errors["executionConfig.deadlineCloud.maxFailedTasksCount"]).toMatch(/negative/);
        });

        it("rejects a fractional priority the createJob task would truncate", () => {
            const r = validatePipeline(dcConfig({ template: "t:", priority: 12.5 }) as any);
            expect(r.ok).toBe(false);
            expect(
                (r.errors as Record<string, string>)["executionConfig.deadlineCloud.priority"]
            ).toMatch(/whole number/);
        });

        it("accepts zero, the lowest valid priority", () => {
            const r = validatePipeline(dcConfig({ template: "t:", priority: 0 }) as any);
            expect(r.ok).toBe(true);
        });

        it("rejects a template beyond the state-machine definition budget", () => {
            const r = validatePipeline(dcConfig({ template: "y".repeat(256 * 1024 + 1) }) as any);
            expect(r.ok).toBe(false);
            expect(
                (r.errors as Record<string, string>)["executionConfig.deadlineCloud.template"]
            ).toMatch(/Cannot exceed/);
        });
    });
});
