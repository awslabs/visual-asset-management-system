/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Identifiers of the built-in system pipelines and workflows. The registration construct overrides
 * the bundle ids with these so a deployment keeps a known id, and every Lambda builder that launches
 * or filters on a system workflow imports them rather than restating the literal.
 */

export const SYSTEM_GENAI_METADATA_PIPELINE_ID = "system-genai-metadata";
export const SYSTEM_GENAI_METADATA_WORKFLOW_ID = "system-genai-metadata";
export const SYSTEM_GENAI_METADATA_TEMPLATE_ID = "system-genai-metadata-default";
/** Database the system workflows are registered under. */
export const SYSTEM_WORKFLOW_DATABASE_ID = "GLOBAL";
