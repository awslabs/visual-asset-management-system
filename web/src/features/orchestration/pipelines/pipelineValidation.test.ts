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
            executionConfig: { executionType: "SQS", sqs: { queueUrl: "https://sqs.us-east-1.amazonaws.com/123/queue" } },
        } as any);
        expect(r.ok).toBe(true);
    });

    it("EventBridge requires busArn", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "EventBridge", eventBridge: { source: "test", detailType: "test" } },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("EventBridge requires source", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "EventBridge", eventBridge: { busArn: "arn:aws:events:...", detailType: "test" } },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("EventBridge requires detailType", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: { executionType: "EventBridge", eventBridge: { busArn: "arn:aws:events:...", source: "test" } },
        } as any);
        expect(r.ok).toBe(false);
    });

    it("accepts valid EventBridge with all fields", () => {
        const r = validatePipeline({
            pipelineName: "x",
            executionConfig: {
                executionType: "EventBridge",
                eventBridge: { busArn: "arn:aws:events:...", source: "test", detailType: "test" }
            },
        } as any);
        expect(r.ok).toBe(true);
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
                deadlineCloud: { queueId: "queue-123" }
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
                deadlineCloud: { farmId: "farm-123" }
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
                deadlineCloud: { farmId: "farm-123", queueId: "queue-123" }
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
