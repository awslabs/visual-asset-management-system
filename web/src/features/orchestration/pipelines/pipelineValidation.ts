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

// A blank number input carries no value, so an empty string or NaN resolves to "unset" (the backend
// treats an absent numeric field as unset).
const blankNumberToUndefined = (val: unknown) =>
    val === "" || val === null || (typeof val === "number" && Number.isNaN(val)) ? undefined : val;

// The two job-template dialects Deadline Cloud createJob accepts (models/pipelines.py
// DEADLINE_TEMPLATE_TYPES). Blank leaves the createJob task on its own default of YAML.
export const DEADLINE_TEMPLATE_TYPES = ["JSON", "YAML"];
// The template travels inline in the state-machine definition, which Step Functions caps at 1 MB
// (models/pipelines.py MAX_DEADLINE_TEMPLATE_LENGTH).
const MAX_DEADLINE_TEMPLATE_LENGTH = 256 * 1024;

// Deadline job priority and task counts. The createJob task state casts each to an integer, so a
// fractional value is not the value that would run, and a negative one is rejected outright.
const deadlineCountSchema = z.preprocess(
    blankNumberToUndefined,
    z.number().int("Must be a whole number").min(0, "Cannot be negative").optional()
);

// Mirrors of the backend resource patterns (common/validators.py: aws_partition_group and
// aws_dns_suffix_group), so a malformed value is reported inline beside its field instead of coming
// back as a save-time 400. Keep both constants in step with the backend — a form stricter than the API
// blocks a value the deployment would have accepted, and the operator has no way to tell why.
// Every partition the CDK layer can deploy into (infra/lib/helper/const.ts). `-eusc` is spelled out
// because the EU Sovereign Cloud partition does not fit the -iso family shape.
const AWS_PARTITION = "aws(?:-us-gov|-cn|-eusc|-iso(?:-[a-z])?)?";
// Partitions do not share one DNS suffix: commercial/GovCloud amazonaws.com, China amazonaws.com.cn,
// EU Sovereign amazonaws.eu, and the ISO partitions their own domains.
const AWS_DNS_SUFFIX =
    "(?:amazonaws\\.com(?:\\.cn)?|amazonaws\\.eu|c2s\\.ic\\.gov|sc2s\\.sgov\\.gov|cloud\\.adc-e\\.uk)";
const SQS_QUEUE_URL = new RegExp(
    `^https://(vpce-[a-z0-9\\-]+\\.)?sqs[\\-a-z]*\\.[a-z0-9\\-]+\\.(vpce\\.)?${AWS_DNS_SUFFIX}/[0-9]{12}/[a-zA-Z0-9_\\-\\.]+$`
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

// Format checks on the per-type resource fields. Each is declared optional here and its PRESENCE is
// required by the superRefine on the active executionType, mirroring the backend, which requires the
// field in its type branch and then runs the format validator on the supplied value
// (models/pipelines.py _validate_execution_config). Splitting the two means a blank required field
// reports "is required" rather than a format complaint, and every missing field of the active type is
// reported in one pass instead of one at a time.
const formatted = (pattern: RegExp, message: string) =>
    z
        .string()
        .optional()
        .refine((val) => !val || pattern.test(val), { message });

const sqsConfigSchema = z.object({
    queueUrl: formatted(
        SQS_QUEUE_URL,
        "Must be a valid SQS queue URL (e.g. https://sqs.us-east-1.amazonaws.com/123456789012/my-queue)"
    ),
});

const eventBridgeConfigSchema = z.object({
    busArn: formatted(
        EVENTBRIDGE_BUS_ARN,
        "Must be a valid event-bus ARN (e.g. arn:aws:events:us-east-1:123456789012:event-bus/my-bus)"
    ),
    source: formatted(
        EVENTBRIDGE_SOURCE,
        "Must be 1-256 characters of letters, digits, dots, hyphens or underscores, and cannot start with 'aws.'"
    ),
    detailType: formatted(EVENTBRIDGE_DETAIL_TYPE, "Must be 1-256 characters"),
});

const deadlineCloudConfigSchema = z.object({
    farmId: z.string().optional(),
    queueId: z.string().optional(),
    storageProfileId: z.string().optional(),
    priority: deadlineCountSchema,
    maxRetriesPerTask: deadlineCountSchema,
    maxFailedTasksCount: deadlineCountSchema,
    templateType: z
        .string()
        .optional()
        .refine((val) => !val || DEADLINE_TEMPLATE_TYPES.includes(val), {
            message: `Must be one of ${DEADLINE_TEMPLATE_TYPES.join(", ")}`,
        }),
    template: z
        .string()
        .max(
            MAX_DEADLINE_TEMPLATE_LENGTH,
            `Cannot exceed ${MAX_DEADLINE_TEMPLATE_LENGTH} characters`
        )
        .optional(),
});

// Each execution type's own sub-block key.
const EXECUTION_TYPE_CONFIG_KEYS: Record<ExecutionType, string> = {
    Lambda: "lambda",
    SQS: "sqs",
    EventBridge: "eventBridge",
    DeadlineCloud: "deadlineCloud",
};
const ALL_EXECUTION_CONFIG_KEYS = Object.values(EXECUTION_TYPE_CONFIG_KEYS);

/**
 * Drop the per-type sub-blocks that do not belong to `executionType`, mirroring the backend, which
 * switches on the type and reads only the matching block (models/pipelines.py
 * _validate_execution_config). Two shapes need this: every stored pipeline carries ALL FOUR blocks
 * because the record builder fills the unused ones with `{}`
 * (common/workflows/pipelineRecords.py build_pipeline_execution_config), and a form session that
 * visited another type retains that type's fields. Neither belongs to the pipeline being saved.
 */
export function pruneExecutionConfig<T extends Record<string, any> | undefined>(config: T): T {
    if (!config) return config;
    const keep = EXECUTION_TYPE_CONFIG_KEYS[config.executionType as ExecutionType];
    const pruned: Record<string, any> = { ...config };
    ALL_EXECUTION_CONFIG_KEYS.forEach((key) => {
        if (key !== keep) delete pruned[key];
    });
    return pruned as T;
}

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
            // The OpenJD job template is embedded verbatim in the state-machine definition, so the
            // ASL builder refuses to generate a task state without it
            // (common/workflows/stepfunctions_builder.py DeadlineCloudTaskBuilder). The pipeline row
            // would save and then break every workflow that referenced it.
            if (!data.deadlineCloud?.template) {
                ctx.addIssue({
                    code: z.ZodIssueCode.custom,
                    message: "DeadlineCloud job template is required",
                    path: ["deadlineCloud", "template"],
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
    const result = pipelineSchema.safeParse({
        ...values,
        executionConfig: pruneExecutionConfig(values.executionConfig),
    });
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
