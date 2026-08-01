/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import { z } from "zod";
import type { Pipeline, ExecutionType } from "../types";

const executionTypeEnum = z.enum(["Lambda", "SQS", "EventBridge", "DeadlineCloud"]);

// Task timeouts are optional. An omitted value and an empty string both mean "no timeout" (the
// backend skips the field for either), so both pass; any supplied value must be a whole number of
// seconds within one week.
const timeoutSchema = z
    .string()
    .optional()
    .refine(
        (val) => {
            if (val === undefined || val === "") return true;
            const num = parseInt(val, 10);
            return !isNaN(num) && num.toString() === val && num >= 1 && num <= 604800;
        },
        { message: "Must be an integer between 1 and 604800" }
    );

// Optional numeric settings. A blank number input carries no value, so an empty string or NaN
// resolves to "unset" (the backend treats an absent numeric field as unset).
const optionalNumberSchema = z.preprocess(
    (val) =>
        val === "" || val === null || (typeof val === "number" && Number.isNaN(val))
            ? undefined
            : val,
    z.number().optional()
);

// Mirrors of the backend resource patterns (common/validators.py), so a malformed value is reported
// inline beside its field instead of coming back as a save-time 400. Partition-aware.
const AWS_PARTITION = "aws(?:-us-gov|-cn|-iso(?:-[a-z])?)?";
const SQS_QUEUE_URL = new RegExp(
    `^https://(vpce-[a-z0-9\\-]+\\.)?sqs[\\-a-z]*\\.[a-z0-9\\-]+\\.(vpce\\.)?amazonaws\\.com(\\.cn)?/[0-9]{12}/[a-zA-Z0-9_\\-\\.]+$`
);
const EVENTBRIDGE_BUS_ARN = new RegExp(
    `^arn:(${AWS_PARTITION}):events:[a-z0-9\\-]+:[0-9]{12}:event-bus/[a-zA-Z0-9_\\-\\./]+$`
);
const EVENTBRIDGE_SOURCE = /^(?!aws\.)[a-zA-Z0-9\-._]{1,256}$/;
const EVENTBRIDGE_DETAIL_TYPE = /^[\s\S]{1,256}$/;
const AWS_ARN = new RegExp(
    `^arn:(${AWS_PARTITION}):[a-z0-9\\-]{1,63}:[a-z0-9\\-]*:[0-9]{0,12}:[a-zA-Z0-9\\-\\._:/]{1,1700}$`
);
const LAMBDA_FUNCTION_NAME = /^[a-zA-Z0-9\-_]{1,140}(:[a-zA-Z0-9\-_$]{1,128})?$/;

const lambdaConfigSchema = z.object({
    // Blank means "auto-provision a Lambda"; a supplied target is either an ARN or a function name.
    resourceId: z
        .string()
        .optional()
        .refine(
            (val) =>
                !val ||
                (val.startsWith("arn:") ? AWS_ARN.test(val) : LAMBDA_FUNCTION_NAME.test(val)),
            { message: "Must be a Lambda function ARN or a valid function name" }
        ),
});

const sqsConfigSchema = z.object({
    queueUrl: z
        .string()
        .min(1, "SQS queueUrl is required")
        .regex(
            SQS_QUEUE_URL,
            "Must be a valid SQS queue URL (e.g. https://sqs.us-east-1.amazonaws.com/123456789012/my-queue)"
        ),
});

const eventBridgeConfigSchema = z.object({
    busArn: z
        .string()
        .min(1, "EventBridge busArn is required")
        .regex(
            EVENTBRIDGE_BUS_ARN,
            "Must be a valid event-bus ARN (e.g. arn:aws:events:us-east-1:123456789012:event-bus/my-bus)"
        ),
    source: z
        .string()
        .min(1, "EventBridge source is required")
        .regex(
            EVENTBRIDGE_SOURCE,
            "Must be 1-256 characters of letters, digits, dots, hyphens or underscores, and cannot start with 'aws.'"
        ),
    detailType: z
        .string()
        .min(1, "EventBridge detailType is required")
        .regex(EVENTBRIDGE_DETAIL_TYPE, "Must be 1-256 characters"),
});

const deadlineCloudConfigSchema = z.object({
    farmId: z.string().min(1, "DeadlineCloud farmId is required"),
    queueId: z.string().min(1, "DeadlineCloud queueId is required"),
    storageProfileId: z.string().optional(),
    priority: optionalNumberSchema,
    maxRetriesPerTask: optionalNumberSchema,
    maxFailedTasksCount: optionalNumberSchema,
    templateType: z.string().optional(),
});

const executionConfigSchema = z
    .object({
        executionType: executionTypeEnum,
        waitForCallback: z.enum(["Enabled", "Disabled"]).optional(),
        taskTimeout: timeoutSchema,
        taskHeartbeatTimeout: timeoutSchema,
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
    pipelineName: z
        .string()
        .min(1, "pipelineName is required")
        .max(256, "pipelineName cannot exceed 256 characters"),
    category: z.string().max(256, "category cannot exceed 256 characters").nullish(),
    description: z.string().max(1024, "description cannot exceed 1024 characters").nullish(),
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
