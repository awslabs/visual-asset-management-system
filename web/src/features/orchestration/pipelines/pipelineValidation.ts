/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { z } from "zod";
import type { Pipeline, ExecutionType } from "../types";

const executionTypeEnum = z.enum(["Lambda", "SQS", "EventBridge", "DeadlineCloud"]);

const timeoutSchema = z.string().refine(
    (val) => {
        const num = parseInt(val, 10);
        return !isNaN(num) && num.toString() === val && num >= 1 && num <= 604800;
    },
    { message: "Must be an integer between 1 and 604800" }
);

const lambdaConfigSchema = z.object({
    resourceId: z.string().optional(),
});

const sqsConfigSchema = z.object({
    queueUrl: z.string().min(1, "SQS queueUrl is required"),
});

const eventBridgeConfigSchema = z.object({
    busArn: z.string().min(1, "EventBridge busArn is required"),
    source: z.string().min(1, "EventBridge source is required"),
    detailType: z.string().min(1, "EventBridge detailType is required"),
});

const deadlineCloudConfigSchema = z.object({
    farmId: z.string().min(1, "DeadlineCloud farmId is required"),
    queueId: z.string().min(1, "DeadlineCloud queueId is required"),
    storageProfileId: z.string().optional(),
    priority: z.number().optional(),
    maxRetriesPerTask: z.number().optional(),
    maxFailedTasksCount: z.number().optional(),
    templateType: z.string().optional(),
});

const executionConfigSchema = z
    .object({
        executionType: executionTypeEnum,
        waitForCallback: z.enum(["Enabled", "Disabled"]).optional(),
        taskTimeout: timeoutSchema.optional(),
        taskHeartbeatTimeout: timeoutSchema.optional(),
        lambda: lambdaConfigSchema.optional(),
        sqs: sqsConfigSchema.optional(),
        eventBridge: eventBridgeConfigSchema.optional(),
        deadlineCloud: deadlineCloudConfigSchema.optional(),
    })
    .superRefine((data, ctx) => {
        // Type-specific validations
        if (data.executionType === "SQS") {
            if (!data.sqs?.queueUrl) {
                ctx.addIssue({
                    code: z.ZodIssueCode.custom,
                    message: "SQS queueUrl is required",
                    path: ["sqs", "queueUrl"],
                });
            }
        }

        if (data.executionType === "EventBridge") {
            if (!data.eventBridge?.busArn) {
                ctx.addIssue({
                    code: z.ZodIssueCode.custom,
                    message: "EventBridge busArn is required",
                    path: ["eventBridge", "busArn"],
                });
            }
            if (!data.eventBridge?.source) {
                ctx.addIssue({
                    code: z.ZodIssueCode.custom,
                    message: "EventBridge source is required",
                    path: ["eventBridge", "source"],
                });
            }
            if (!data.eventBridge?.detailType) {
                ctx.addIssue({
                    code: z.ZodIssueCode.custom,
                    message: "EventBridge detailType is required",
                    path: ["eventBridge", "detailType"],
                });
            }
        }

        if (data.executionType === "DeadlineCloud") {
            if (!data.deadlineCloud?.farmId) {
                ctx.addIssue({
                    code: z.ZodIssueCode.custom,
                    message: "DeadlineCloud farmId is required",
                    path: ["deadlineCloud", "farmId"],
                });
            }
            if (!data.deadlineCloud?.queueId) {
                ctx.addIssue({
                    code: z.ZodIssueCode.custom,
                    message: "DeadlineCloud queueId is required",
                    path: ["deadlineCloud", "queueId"],
                });
            }
            if (data.waitForCallback !== "Enabled") {
                ctx.addIssue({
                    code: z.ZodIssueCode.custom,
                    message: "DeadlineCloud requires waitForCallback to be Enabled",
                    path: ["waitForCallback"],
                });
            }
        }
    });

const pipelineSchema = z.object({
    pipelineId: z
        .string()
        .regex(/^[-_a-zA-Z0-9]{3,63}$/, "pipelineId must match pattern ^[-_a-zA-Z0-9]{3,63}$")
        .optional(),
    pipelineName: z.string().min(1, "pipelineName is required"),
    executionConfig: executionConfigSchema,
});

export interface ValidationResult {
    ok: boolean;
    errors?: Record<string, string> | string[];
}

export function validatePipeline(values: Partial<Pipeline>): ValidationResult {
    const result = pipelineSchema.safeParse(values);
    if (result.success) {
        return { ok: true };
    }

    const errors: Record<string, string> = {};
    result.error.issues.forEach((issue) => {
        const path = issue.path.join(".");
        errors[path] = issue.message;
    });

    return { ok: false, errors };
}
