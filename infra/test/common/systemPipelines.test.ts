/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The system pipeline ids are stated once, in infra/common/systemPipelines.ts, and every CDK
 * consumer imports them. This pins the literals (external references depend on them) and their
 * agreement with the shipped vamsSchema bundle: the bundle's pipeline.json and workflow.json carry
 * no id of their own (the registration construct supplies these constants as overrides), so the
 * bundle-side check is that both files exist and that the template file is named by the template id.
 */

import * as fs from "fs";
import * as path from "path";
import {
    SYSTEM_GENAI_METADATA_PIPELINE_ID,
    SYSTEM_GENAI_METADATA_TEMPLATE_ID,
    SYSTEM_GENAI_METADATA_WORKFLOW_ID,
    SYSTEM_WORKFLOW_DATABASE_ID,
} from "../../common/systemPipelines";

const BUNDLE = path.join(
    __dirname,
    "..",
    "..",
    "..",
    "backendPipelines",
    "system",
    "genAiMetadata",
    "vamsSchema"
);

describe("system pipeline id constants", () => {
    test("carry the ids the spec fixes", () => {
        expect(SYSTEM_GENAI_METADATA_PIPELINE_ID).toBe("system-genai-metadata");
        expect(SYSTEM_GENAI_METADATA_WORKFLOW_ID).toBe("system-genai-metadata");
        expect(SYSTEM_GENAI_METADATA_TEMPLATE_ID).toBe("system-genai-metadata-default");
        expect(SYSTEM_WORKFLOW_DATABASE_ID).toBe("GLOBAL");
    });

    test("match the shipped bundle", () => {
        const pipeline = JSON.parse(fs.readFileSync(path.join(BUNDLE, "pipeline.json"), "utf8"));
        const workflow = JSON.parse(fs.readFileSync(path.join(BUNDLE, "workflow.json"), "utf8"));
        // The bundle states no id; the registration passes the constants as idOverrides. A bundle
        // that started carrying one would have to agree with the constant.
        expect(pipeline.pipelineId ?? SYSTEM_GENAI_METADATA_PIPELINE_ID).toBe(
            SYSTEM_GENAI_METADATA_PIPELINE_ID
        );
        expect(workflow.workflowId ?? SYSTEM_GENAI_METADATA_WORKFLOW_ID).toBe(
            SYSTEM_GENAI_METADATA_WORKFLOW_ID
        );
        expect(pipeline.isSystem).toBe(true);
        expect(workflow.isSystem).toBe(true);
        const templates = fs.readdirSync(path.join(BUNDLE, "templates"));
        expect(templates).toContain(`${SYSTEM_GENAI_METADATA_TEMPLATE_ID}.json`);
        const template = JSON.parse(
            fs.readFileSync(
                path.join(BUNDLE, "templates", `${SYSTEM_GENAI_METADATA_TEMPLATE_ID}.json`),
                "utf8"
            )
        );
        expect(template.templateId).toBe(SYSTEM_GENAI_METADATA_TEMPLATE_ID);
    });
});
