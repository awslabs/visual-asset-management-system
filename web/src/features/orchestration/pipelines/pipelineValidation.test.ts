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

    it("accepts valid DeadlineCloud with farmId+queueId and Enabled callback", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "DeadlineCloud",
                waitForCallback: "Enabled",
                deadlineCloud: { farmId: "farm-123", queueId: "queue-123" },
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
});
