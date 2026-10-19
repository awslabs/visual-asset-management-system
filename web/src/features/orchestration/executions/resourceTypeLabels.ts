/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Display labels for the sub-process resource types VAMS knows how to stop. A type that is not listed
 * — an externally owned pipeline's own compute, say — is shown by its raw `resourceType`, so a new
 * type renders without a release rather than as an empty string.
 */
export const RESOURCE_TYPE_LABELS: Readonly<Record<string, string>> = {
    stepFunctionsExecution: "Step Functions execution",
    batchJob: "AWS Batch job",
    deadlineCloudJob: "Deadline Cloud job",
    sageMakerTrainingJob: "SageMaker training job",
    sageMakerPipelineExecution: "SageMaker pipeline execution",
};

/** The label for a resource type, or the type itself when it has none. */
export const resourceTypeLabel = (resourceType: string): string =>
    RESOURCE_TYPE_LABELS[resourceType] ?? resourceType;
