/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";

/**
 * Collapsible help panel listing the SYSTEM template tags the renderer resolves automatically in a
 * template config body (written as `{{tagName}}`), grouped by category. Mirrors the backend catalog
 * in common/workflows/templateTags.py. Shown on the template config step and the execute wizard so
 * authors/operators can see which placeholders are available without leaving the form.
 *
 * These are distinct from a template's own tag schema (the per-template fields a person fills in):
 * system tags are always available and are never supplied by the caller.
 *
 * The two groups labelled "JSON value" render an object, array, or number and are the whole value, so
 * they take no quotes in a json config body; every other tag renders escaped text that fills a JSON
 * string and belongs inside that string's quotes. A json body is checked against these two shapes at
 * save, so the wrong quoting is a 400 rather than a malformed config at run time.
 */

interface TagEntry {
    tag: string;
    desc: string;
}

interface TagGroup {
    title: string;
    tags: TagEntry[];
}

// Grouped to match templateTags.py sections A–K. Descriptions are concise author-facing hints.
const SYSTEM_TAG_GROUPS: TagGroup[] = [
    {
        title: "Execution & workflow identity",
        tags: [
            { tag: "executionId", desc: "This workflow execution's id." },
            { tag: "workflowId", desc: "The workflow's id." },
            { tag: "workflowDatabaseId", desc: "The workflow's database id." },
            { tag: "triggerType", desc: "How the run started (Manual / fileUpload)." },
            { tag: "executingUserName", desc: "User id that launched the run." },
        ],
    },
    {
        title: "Pipeline-task identity",
        tags: [
            { tag: "pipelineExecutionId", desc: "This pipeline task's execution id." },
            { tag: "pipelineId", desc: "The pipeline's id." },
            { tag: "pipelineName", desc: "The pipeline's name." },
            { tag: "pipelineDatabaseId", desc: "The pipeline's database id." },
            { tag: "jobName", desc: "The per-pipeline job name in the workflow." },
        ],
    },
    {
        title: "Timestamps",
        tags: [
            { tag: "jobStartTimestamp", desc: "Job start (ISO-8601)." },
            { tag: "jobStartTimestampUnix", desc: "Job start (Unix seconds)." },
            { tag: "jobStartDate", desc: "Job start date (YYYY-MM-DD)." },
            { tag: "executionStartTimestamp", desc: "Workflow execution start (ISO-8601)." },
        ],
    },
    {
        title: "First input file",
        tags: [
            { tag: "firstAssetFileDatabaseId", desc: "Database id of the first input file." },
            { tag: "firstAssetFileAssetId", desc: "Asset id of the first input file." },
            { tag: "firstAssetFileAssetBucket", desc: "S3 bucket of the first input file." },
            {
                tag: "firstAssetFileAssetRootS3Key",
                desc: "Asset root S3 key of the first input file.",
            },
            {
                tag: "firstAssetFileRelativePath",
                desc: "Asset-relative path of the first input file.",
            },
            { tag: "firstAssetFileKey", desc: "Full S3 key of the first input file." },
            { tag: "firstAssetFileVersionId", desc: "Version id of the first input file." },
            {
                tag: "firstAssetFileAuxPreviewPrefix",
                desc: "Auxiliary preview prefix for the first input file.",
            },
            { tag: "firstAssetFileS3Uri", desc: "s3:// URI of the first input file." },
            {
                tag: "firstAssetFileAuxPreviewS3Uri",
                desc: "s3:// URI of the first input file's aux preview.",
            },
            { tag: "firstAssetFileFileName", desc: "File name of the first input file." },
            { tag: "firstAssetFileFileNameNoExt", desc: "File name without extension." },
            { tag: "firstAssetFileFileExtension", desc: "File extension of the first input file." },
        ],
    },
    {
        title: "Input-file collections (JSON value — no quotes)",
        tags: [
            { tag: "assetFileKeyArray", desc: "All input-file full S3 keys." },
            { tag: "assetFileRelativePathArray", desc: "All input-file asset-relative paths." },
            { tag: "assetFileS3UriArray", desc: "All input-file s3:// URIs." },
            { tag: "assetFileVersionIdArray", desc: "All input-file version ids." },
            { tag: "assetFileObjectArray", desc: "All input files as objects." },
            { tag: "assetFileAssetIdArray", desc: "All input-file asset ids." },
            { tag: "assetFileUniqueAssetIdArray", desc: "Unique input-file asset ids." },
            { tag: "assetFileDatabaseIdArray", desc: "All input-file database ids." },
            { tag: "assetFileUniqueDatabaseIdArray", desc: "Unique input-file database ids." },
            { tag: "assetFileCount", desc: "Number of input files." },
        ],
    },
    {
        title: "Output locations",
        tags: [
            { tag: "outputBucket", desc: "Output S3 bucket." },
            { tag: "outputFilesPrefix", desc: "Output files S3 prefix." },
            { tag: "outputFilesS3Uri", desc: "Output files s3:// URI." },
            { tag: "outputPreviewsPrefix", desc: "Output previews S3 prefix." },
            { tag: "outputPreviewsS3Uri", desc: "Output previews s3:// URI." },
            { tag: "outputMetadataPrefix", desc: "Output metadata S3 prefix." },
            { tag: "outputMetadataS3Uri", desc: "Output metadata s3:// URI." },
            { tag: "outputResultsPrefix", desc: "Output results S3 prefix." },
            { tag: "outputResultsS3Uri", desc: "Output results s3:// URI." },
            { tag: "outputTargetAssetId", desc: "Output target asset id." },
            { tag: "outputTargetDatabaseId", desc: "Output target database id." },
            { tag: "outputTargetLocationType", desc: "Output target type (asset / none)." },
            { tag: "outputTargetAssetRootS3Key", desc: "Output target asset root S3 key." },
            {
                tag: "outputFileBaseExecutionPathExtension",
                desc: "The output base-path prefix this run writes under ('/' = asset root).",
            },
        ],
    },
    {
        title: "Auxiliary locations",
        tags: [
            { tag: "auxBucket", desc: "Auxiliary (temp/working) S3 bucket." },
            { tag: "auxTempPrefix", desc: "Auxiliary temp prefix." },
            { tag: "auxTempS3Uri", desc: "Auxiliary temp s3:// URI." },
            { tag: "auxPreviewPipelineSuffix", desc: "Auxiliary preview pipeline suffix." },
        ],
    },
    {
        title: "Metadata / configuration locations",
        tags: [
            {
                tag: "inputMetadataS3Location",
                desc: "S3 location of the run's input metadata file.",
            },
            {
                tag: "inputConfigurationS3Location",
                desc: "S3 location of the run's input configuration file.",
            },
        ],
    },
    {
        title: "System / orchestration",
        tags: [
            { tag: "orchestrationBusArn", desc: "EventBridge orchestration bus ARN." },
            { tag: "orchestrationEventPrefix", desc: "Orchestration event source prefix." },
        ],
    },
    {
        title: "Metadata content (JSON value — no quotes)",
        tags: [
            { tag: "inputMetadataObject", desc: "The full input metadata payload." },
            { tag: "assetMetadataObject", desc: "Asset metadata." },
            { tag: "fileMetadataObject", desc: "File metadata." },
            { tag: "fileAttributesObject", desc: "File attributes." },
            { tag: "assetDataObject", desc: "Asset data." },
            { tag: "databaseMetadataObject", desc: "Database metadata." },
        ],
    },
    {
        title: "AWS Deadline Cloud",
        tags: [
            { tag: "deadlineFarmId", desc: "Deadline Cloud farm id (empty until configured)." },
            { tag: "deadlineQueueId", desc: "Deadline Cloud queue id (empty until configured)." },
            {
                tag: "deadlineStorageProfileId",
                desc: "Deadline Cloud storage profile id (empty until configured).",
            },
        ],
    },
];

/**
 * Shared, plain-text instruction for the config body's `{{tagName}}` placeholders. Single source so
 * the Config Body tooltip and the SystemTagHelp panel stay in sync — update the wording here only.
 */
export const CONFIG_BODY_SYSTEM_TAG_INSTRUCTIONS =
    "The configuration body delivered to the pipeline. Write {{tagName}} placeholders: this " +
    "template's own tag fields (filled in at launch) and the system tags listed below (resolved " +
    "automatically per pipeline task when the config body renders). Expand “System template " +
    "tags” beneath the editor for the full list.";

interface SystemTagHelpProps {
    /** Start expanded (default collapsed). */
    defaultOpen?: boolean;
}

const SystemTagHelp: React.FC<SystemTagHelpProps> = ({ defaultOpen = false }) => {
    const [open, setOpen] = useState(defaultOpen);

    return (
        <div className="orch-outline rounded-lg border border-border-default bg-surface-secondary">
            <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                aria-expanded={open}
                className="w-full flex items-center justify-between px-3 py-2 text-sm font-medium text-text-primary"
            >
                <span>System template tags ({"{{tagName}}"}) available in the config body</span>
                <span aria-hidden="true">{open ? "▾" : "▸"}</span>
            </button>
            {open && (
                <div className="px-3 pb-3 space-y-3">
                    <p className="text-xs text-text-secondary">
                        {CONFIG_BODY_SYSTEM_TAG_INSTRUCTIONS} Fields like{" "}
                        <code>{"{{outputFileBaseExecutionPathExtension}}"}</code> expose the run's
                        output base path.
                    </p>
                    <p className="text-xs text-text-secondary">
                        In a <strong>json</strong> config body, a placeholder that renders a JSON
                        value is the whole value and takes no quotes — the two “JSON value” groups
                        below, as in <code>{'"files": {{assetFileKeyArray}}'}</code>, and equally
                        this template's own tags declared <strong>integer</strong>,{" "}
                        <strong>number</strong>, <strong>boolean</strong>, or{" "}
                        <strong>string-list</strong>, as in <code>{'"steps": {{STEPS}}'}</code>. A
                        tag that renders text goes inside the string it fills — a{" "}
                        <strong>string</strong> or <strong>enum</strong> tag and every remaining
                        system tag, as in <code>{'"prompt": "{{PROMPT}}"'}</code>. Saving rejects
                        the reverse of either: quoting a typed tag would deliver <code>"150"</code>{" "}
                        where the pipeline expects <code>150</code>.
                    </p>
                    {SYSTEM_TAG_GROUPS.map((group) => (
                        <div key={group.title}>
                            <div className="text-xs font-semibold text-text-primary mb-1">
                                {group.title}
                            </div>
                            <ul className="space-y-0.5">
                                {group.tags.map((t) => (
                                    <li key={t.tag} className="text-xs text-text-secondary">
                                        <code className="text-text-primary">{`{{${t.tag}}}`}</code>{" "}
                                        — {t.desc}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    ))}
                </div>
            )}
        </div>
    );
};

export default SystemTagHelp;
