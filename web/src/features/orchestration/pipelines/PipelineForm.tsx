/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useForm } from "react-hook-form";
import type { Pipeline, ExecutionType } from "../types";
import { validatePipeline } from "./pipelineValidation";
import { useCreatePipeline, useUpdatePipeline } from "../api/queries";
import Dialog from "../components/Dialog";
import { appCache } from "../../../services/appCache";

interface PipelineFormProps {
    mode: "create" | "edit";
    databaseId: string;
    initial?: Partial<Pipeline>;
    onDone: () => void;
}

const PipelineForm: React.FC<PipelineFormProps> = ({ mode, databaseId, initial, onDone }) => {
    const [isOpen, setIsOpen] = useState(true);
    const createMutation = useCreatePipeline();
    const updateMutation = useUpdatePipeline();
    const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});

    const config = appCache.getItem("config");
    const featuresEnabled = config?.featuresEnabled || [];
    const showDeadlineCloud =
        featuresEnabled.includes("DEADLINECLOUD_PIPELINES") && !featuresEnabled.includes("GOVCLOUD");

    const {
        register,
        handleSubmit,
        watch,
        setValue,
        formState: { isSubmitting },
    } = useForm<Partial<Pipeline>>({
        defaultValues: initial || {
            pipelineName: "",
            category: "",
            description: "",
            enabled: true,
            executionConfig: {
                executionType: "Lambda",
                waitForCallback: "Disabled",
            },
            systemConfig: {
                inputFileArity: "one",
                assetScope: {},
                metadataInputs: {},
                requireTemplate: false,
                allowCustomTemplateOverride: false,
                inputFileFilters: { allow: [], exclude: [] },
            },
        },
    });

    const executionType = watch("executionConfig.executionType");
    const waitForCallback = watch("executionConfig.waitForCallback");

    React.useEffect(() => {
        if (executionType === "DeadlineCloud") {
            setValue("executionConfig.waitForCallback", "Enabled");
        }
    }, [executionType, setValue]);

    const onSubmit = async (data: Partial<Pipeline>) => {
        const validation = validatePipeline(data);
        if (!validation.ok) {
            setValidationErrors((validation.errors as Record<string, string>) || {});
            return;
        }

        setValidationErrors({});

        const body: Pipeline = {
            databaseId,
            pipelineId: data.pipelineId || "",
            pipelineName: data.pipelineName || "",
            category: data.category,
            description: data.description,
            enabled: data.enabled,
            executionConfig: data.executionConfig!,
            systemConfig: data.systemConfig,
        };

        try {
            if (mode === "create") {
                await createMutation.mutateAsync(body);
            } else {
                await updateMutation.mutateAsync({
                    databaseId,
                    pipelineId: initial?.pipelineId || "",
                    body,
                });
            }
            setIsOpen(false);
            onDone();
        } catch (err: any) {
            console.log("Form submission error:", err);
            setValidationErrors({ _form: err.message || "Submission failed" });
        }
    };

    const handleClose = () => {
        setIsOpen(false);
        onDone();
    };

    return (
        <Dialog
            open={isOpen}
            onOpenChange={handleClose}
            title={mode === "create" ? "Create Pipeline" : "Edit Pipeline"}
            footer={
                <>
                    <button
                        type="button"
                        onClick={handleClose}
                        className="px-4 py-2 bg-gray-200 dark:bg-gray-700 text-gray-900 dark:text-gray-100 rounded hover:bg-gray-300 dark:hover:bg-gray-600"
                    >
                        Cancel
                    </button>
                    <button
                        type="submit"
                        form="pipeline-form"
                        disabled={isSubmitting}
                        className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:opacity-50"
                    >
                        {isSubmitting ? "Saving..." : mode === "create" ? "Create" : "Update"}
                    </button>
                </>
            }
        >
            <form id="pipeline-form" onSubmit={handleSubmit(onSubmit)} className="space-y-4">
                {validationErrors._form && (
                    <div className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded">
                        {validationErrors._form}
                    </div>
                )}

                {mode === "create" && (
                    <div>
                        <label htmlFor="pipelineId" className="block text-sm font-medium mb-1">Pipeline ID (optional)</label>
                        <input
                            id="pipelineId"
                            {...register("pipelineId")}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            placeholder="Auto-generated if left blank"
                        />
                        {validationErrors.pipelineId && (
                            <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                                {validationErrors.pipelineId}
                            </p>
                        )}
                    </div>
                )}

                <div>
                    <label htmlFor="pipelineName" className="block text-sm font-medium mb-1">Pipeline Name *</label>
                    <input
                        id="pipelineName"
                        {...register("pipelineName")}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        placeholder="Pipeline name"
                    />
                    {validationErrors.pipelineName && (
                        <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                            {validationErrors.pipelineName}
                        </p>
                    )}
                </div>

                <div>
                    <label htmlFor="category" className="block text-sm font-medium mb-1">Category</label>
                    <input
                        id="category"
                        {...register("category")}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        placeholder="e.g. 3D, GenAI"
                    />
                </div>

                <div>
                    <label htmlFor="description" className="block text-sm font-medium mb-1">Description</label>
                    <textarea
                        id="description"
                        {...register("description")}
                        rows={3}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        placeholder="Pipeline description"
                    />
                </div>

                <div className="flex items-center gap-2">
                    <input {...register("enabled")} type="checkbox" id="enabled" />
                    <label htmlFor="enabled" className="text-sm font-medium">
                        Enabled
                    </label>
                </div>

                <hr className="border-gray-300 dark:border-gray-600" />
                <h3 className="text-lg font-semibold">Execution Configuration</h3>

                <div>
                    <label htmlFor="executionType" className="block text-sm font-medium mb-1">Execution Type *</label>
                    <select
                        id="executionType"
                        {...register("executionConfig.executionType")}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                    >
                        <option value="Lambda">Lambda</option>
                        <option value="SQS">SQS</option>
                        <option value="EventBridge">EventBridge</option>
                        {showDeadlineCloud && <option value="DeadlineCloud">DeadlineCloud</option>}
                    </select>
                    {validationErrors["executionConfig.executionType"] && (
                        <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                            {validationErrors["executionConfig.executionType"]}
                        </p>
                    )}
                </div>

                {executionType === "Lambda" && (
                    <div>
                        <label htmlFor="lambdaResourceId" className="block text-sm font-medium mb-1">Lambda Resource ID</label>
                        <input
                            id="lambdaResourceId"
                            {...register("executionConfig.lambda.resourceId")}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            placeholder="Lambda function ARN or name"
                        />
                        {!watch("executionConfig.lambda.resourceId") && (
                            <p className="text-gray-600 dark:text-gray-400 text-sm mt-1">
                                Leave blank to auto-provision a new Lambda
                            </p>
                        )}
                    </div>
                )}

                {executionType === "SQS" && (
                    <div>
                        <label htmlFor="queueUrl" className="block text-sm font-medium mb-1">Queue URL *</label>
                        <input
                            id="queueUrl"
                            {...register("executionConfig.sqs.queueUrl")}
                            className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            placeholder="https://sqs.region.amazonaws.com/account/queue"
                        />
                        {validationErrors["executionConfig.sqs.queueUrl"] && (
                            <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                                {validationErrors["executionConfig.sqs.queueUrl"]}
                            </p>
                        )}
                    </div>
                )}

                {executionType === "EventBridge" && (
                    <>
                        <div>
                            <label htmlFor="busArn" className="block text-sm font-medium mb-1">Event Bus ARN *</label>
                            <input
                                id="busArn"
                                {...register("executionConfig.eventBridge.busArn")}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                placeholder="arn:aws:events:region:account:event-bus/name"
                            />
                            {validationErrors["executionConfig.eventBridge.busArn"] && (
                                <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                                    {validationErrors["executionConfig.eventBridge.busArn"]}
                                </p>
                            )}
                        </div>
                        <div>
                            <label htmlFor="source" className="block text-sm font-medium mb-1">Source *</label>
                            <input
                                id="source"
                                {...register("executionConfig.eventBridge.source")}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                placeholder="e.g. vams.pipeline"
                            />
                            {validationErrors["executionConfig.eventBridge.source"] && (
                                <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                                    {validationErrors["executionConfig.eventBridge.source"]}
                                </p>
                            )}
                        </div>
                        <div>
                            <label htmlFor="detailType" className="block text-sm font-medium mb-1">Detail Type *</label>
                            <input
                                id="detailType"
                                {...register("executionConfig.eventBridge.detailType")}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                placeholder="e.g. PipelineExecution"
                            />
                            {validationErrors["executionConfig.eventBridge.detailType"] && (
                                <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                                    {validationErrors["executionConfig.eventBridge.detailType"]}
                                </p>
                            )}
                        </div>
                    </>
                )}

                {executionType === "DeadlineCloud" && (
                    <>
                        <div>
                            <label htmlFor="farmId" className="block text-sm font-medium mb-1">Farm ID *</label>
                            <input
                                id="farmId"
                                {...register("executionConfig.deadlineCloud.farmId")}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                placeholder="farm-..."
                            />
                            {validationErrors["executionConfig.deadlineCloud.farmId"] && (
                                <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                                    {validationErrors["executionConfig.deadlineCloud.farmId"]}
                                </p>
                            )}
                        </div>
                        <div>
                            <label htmlFor="queueId" className="block text-sm font-medium mb-1">Queue ID *</label>
                            <input
                                id="queueId"
                                {...register("executionConfig.deadlineCloud.queueId")}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                                placeholder="queue-..."
                            />
                            {validationErrors["executionConfig.deadlineCloud.queueId"] && (
                                <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                                    {validationErrors["executionConfig.deadlineCloud.queueId"]}
                                </p>
                            )}
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">Storage Profile ID</label>
                            <input
                                {...register("executionConfig.deadlineCloud.storageProfileId")}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">Priority</label>
                            <input
                                {...register("executionConfig.deadlineCloud.priority", { valueAsNumber: true })}
                                type="number"
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">Max Retries Per Task</label>
                            <input
                                {...register("executionConfig.deadlineCloud.maxRetriesPerTask", {
                                    valueAsNumber: true,
                                })}
                                type="number"
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">Max Failed Tasks Count</label>
                            <input
                                {...register("executionConfig.deadlineCloud.maxFailedTasksCount", {
                                    valueAsNumber: true,
                                })}
                                type="number"
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium mb-1">Template Type</label>
                            <input
                                {...register("executionConfig.deadlineCloud.templateType")}
                                className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                            />
                        </div>
                    </>
                )}

                <div>
                    <label htmlFor="waitForCallback" className="block text-sm font-medium mb-1">Wait For Callback</label>
                    <select
                        id="waitForCallback"
                        {...register("executionConfig.waitForCallback")}
                        disabled={executionType === "DeadlineCloud"}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100 disabled:opacity-50"
                    >
                        <option value="Enabled">Enabled</option>
                        <option value="Disabled">Disabled</option>
                    </select>
                    {executionType === "DeadlineCloud" && (
                        <p className="text-gray-600 dark:text-gray-400 text-sm mt-1">
                            Locked to Enabled for DeadlineCloud
                        </p>
                    )}
                    {validationErrors["executionConfig.waitForCallback"] && (
                        <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                            {validationErrors["executionConfig.waitForCallback"]}
                        </p>
                    )}
                </div>

                <div>
                    <label className="block text-sm font-medium mb-1">Task Timeout (seconds)</label>
                    <input
                        {...register("executionConfig.taskTimeout")}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        placeholder="1-604800 (max 1 week)"
                    />
                    {validationErrors["executionConfig.taskTimeout"] && (
                        <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                            {validationErrors["executionConfig.taskTimeout"]}
                        </p>
                    )}
                </div>

                <div>
                    <label className="block text-sm font-medium mb-1">Task Heartbeat Timeout (seconds)</label>
                    <input
                        {...register("executionConfig.taskHeartbeatTimeout")}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        placeholder="1-604800 (max 1 week)"
                    />
                    {validationErrors["executionConfig.taskHeartbeatTimeout"] && (
                        <p className="text-red-600 dark:text-red-400 text-sm mt-1">
                            {validationErrors["executionConfig.taskHeartbeatTimeout"]}
                        </p>
                    )}
                </div>

                <hr className="border-gray-300 dark:border-gray-600" />
                <h3 className="text-lg font-semibold">System Configuration</h3>

                <div>
                    <label className="block text-sm font-medium mb-1">Input File Arity</label>
                    <select
                        {...register("systemConfig.inputFileArity")}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                    >
                        <option value="none">None</option>
                        <option value="one">One</option>
                        <option value="multi">Multi</option>
                    </select>
                </div>

                <div>
                    <label className="block text-sm font-medium mb-2">Asset Scope</label>
                    <div className="space-y-2">
                        <label className="flex items-center gap-2">
                            <input {...register("systemConfig.assetScope.metadata")} type="checkbox" />
                            <span className="text-sm">Metadata</span>
                        </label>
                        <label className="flex items-center gap-2">
                            <input {...register("systemConfig.assetScope.visualization")} type="checkbox" />
                            <span className="text-sm">Visualization</span>
                        </label>
                        <label className="flex items-center gap-2">
                            <input {...register("systemConfig.assetScope.preview")} type="checkbox" />
                            <span className="text-sm">Preview</span>
                        </label>
                    </div>
                </div>

                <div>
                    <label className="block text-sm font-medium mb-2">Metadata Inputs</label>
                    <div className="space-y-2">
                        <label className="flex items-center gap-2">
                            <input {...register("systemConfig.metadataInputs.core")} type="checkbox" />
                            <span className="text-sm">Core</span>
                        </label>
                        <label className="flex items-center gap-2">
                            <input {...register("systemConfig.metadataInputs.custom")} type="checkbox" />
                            <span className="text-sm">Custom</span>
                        </label>
                    </div>
                </div>

                <div className="flex items-center gap-2">
                    <input {...register("systemConfig.requireTemplate")} type="checkbox" id="requireTemplate" />
                    <label htmlFor="requireTemplate" className="text-sm font-medium">
                        Require Template
                    </label>
                </div>

                <div className="flex items-center gap-2">
                    <input
                        {...register("systemConfig.allowCustomTemplateOverride")}
                        type="checkbox"
                        id="allowCustom"
                    />
                    <label htmlFor="allowCustom" className="text-sm font-medium">
                        Allow Custom Template Override
                    </label>
                </div>

                <div>
                    <label className="block text-sm font-medium mb-1">Aux Preview Pipeline Suffix</label>
                    <input
                        {...register("systemConfig.auxPreviewPipelineSuffix")}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        placeholder="e.g. -preview"
                    />
                </div>

                <div>
                    <label className="block text-sm font-medium mb-1">Input File Filters (Allow)</label>
                    <input
                        {...register("systemConfig.inputFileFilters.allow")}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        placeholder="Comma-separated extensions: .jpg, .png"
                    />
                </div>

                <div>
                    <label className="block text-sm font-medium mb-1">Input File Filters (Exclude)</label>
                    <input
                        {...register("systemConfig.inputFileFilters.exclude")}
                        className="w-full px-3 py-2 border border-gray-300 dark:border-gray-600 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-gray-100"
                        placeholder="Comma-separated extensions: .tmp, .log"
                    />
                </div>
            </form>
        </Dialog>
    );
};

export default PipelineForm;
