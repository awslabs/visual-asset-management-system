/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState } from "react";
import { useForm } from "react-hook-form";
import type { Pipeline, PipelineCreateRequest, ExecutionType } from "../types";
import { validatePipeline } from "./pipelineValidation";
import { useCreatePipeline, useUpdatePipeline } from "../api/queries";
import Dialog from "../components/Dialog";
import InfoTooltip from "../components/InfoTooltip";
import StringListInput from "../components/StringListInput";
import CollapsibleSection from "../components/CollapsibleSection";
import AssetSpanControl from "../components/AssetSpanControl";
import Breadcrumb from "../components/Breadcrumb";
import Stepper from "../components/Stepper";
import { btnPrimary, btnSecondary } from "../components/controlStyles";
import { appCache } from "../../../services/appCache";
import { useToast, toastErrorMessage } from "../components/ToastProvider";

// Registration options for an optional numeric input: a blank field carries no value, so it is
// submitted as undefined rather than NaN.
const optionalNumberField = {
    setValueAs: (v: string) => (v === "" || v === undefined || v === null ? undefined : Number(v)),
};

// Validation-error keys that have a dedicated message element next to their field. Any other key is
// surfaced in the form-level summary so a save is never refused without feedback.
const MAPPED_ERROR_KEYS = new Set([
    "_form",
    "pipelineName",
    "category",
    "description",
    "executionConfig.executionType",
    "executionConfig.lambda.resourceId",
    "executionConfig.sqs.queueUrl",
    "executionConfig.eventBridge.busArn",
    "executionConfig.eventBridge.source",
    "executionConfig.eventBridge.detailType",
    "executionConfig.deadlineCloud.farmId",
    "executionConfig.deadlineCloud.queueId",
    "executionConfig.waitForCallback",
    "executionConfig.taskTimeout",
    "executionConfig.taskHeartbeatTimeout",
]);

// Page-wizard steps, in order. Module scope so onSubmit can gate on the final step.
const PIPELINE_STEPS = [
    { id: "basic", label: "Basic" },
    { id: "execution", label: "Execution" },
    { id: "settings", label: "Settings" },
];
const LAST_PIPELINE_STEP = PIPELINE_STEPS[PIPELINE_STEPS.length - 1].id;

// Which wizard step owns each inline error message, so a page-mode save failure lands on the step
// showing the offending field.
const errorStep = (key: string) =>
    key.startsWith("executionConfig.") ? "execution" : key === "_form" ? null : "basic";

interface PipelineFormProps {
    mode: "create" | "edit";
    databaseId: string;
    initial?: Partial<Pipeline>;
    onDone: () => void;
    /** "dialog" (default, modal) or "page" (full-page wizard with breadcrumb + stepper). */
    variant?: "dialog" | "page";
}

const PipelineForm: React.FC<PipelineFormProps> = ({
    mode,
    databaseId,
    initial,
    onDone,
    variant = "dialog",
}) => {
    const toast = useToast();
    const [isOpen, setIsOpen] = useState(true);
    const createMutation = useCreatePipeline();
    const updateMutation = useUpdatePipeline();
    const [validationErrors, setValidationErrors] = useState<Record<string, string>>({});
    // Non-blocking warnings returned alongside a successful save (e.g. a require-template pipeline
    // in an auto-trigger with no default template). The save succeeded; the user acknowledges them
    // before the form closes.
    const [saveWarnings, setSaveWarnings] = useState<string[]>([]);

    const config = appCache.getItem("config");
    const featuresEnabled = config?.featuresEnabled || [];
    // GovCloud is not re-checked here: getConfig() already refuses to synthesize a stack that enables
    // Deadline Cloud in GovCloud or any non-'aws' partition, so the feature flag cannot be present in
    // such a deployment.
    const showDeadlineCloud = featuresEnabled.includes("DEADLINECLOUD_PIPELINES");

    const isDeadlineCloudDisabled =
        mode === "edit" &&
        initial?.executionConfig?.executionType === "DeadlineCloud" &&
        !showDeadlineCloud;

    // Input-file filters are add-to lists (each entry an ext/path/name/wildcard), not comma text.
    const [allowFilters, setAllowFilters] = React.useState<string[]>(
        initial?.systemConfig?.inputFileFilters?.allow || []
    );
    const [excludeFilters, setExcludeFilters] = React.useState<string[]>(
        initial?.systemConfig?.inputFileFilters?.exclude || []
    );
    // Wizard step (page variant only). Dialog variant shows all sections at once.
    const [wizardStep, setWizardStep] = React.useState<string>("basic");

    const {
        register,
        handleSubmit,
        watch,
        setValue,
        reset,
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
    const inputFileArity = watch("systemConfig.inputFileArity") || "one";

    React.useEffect(() => {
        if (executionType === "DeadlineCloud") {
            setValue("executionConfig.waitForCallback", "Enabled");
        }
    }, [executionType, setValue]);

    // Fields are seeded from `initial` at mount. A route change between two edit targets can swap
    // the prop without remounting the form, so every field is re-seeded when the pipeline identity
    // changes — otherwise the previous pipeline's values would be saved onto the new one.
    const seededPipelineId = React.useRef(initial?.pipelineId);
    React.useEffect(() => {
        const nextId = initial?.pipelineId;
        if (nextId === seededPipelineId.current) return;
        seededPipelineId.current = nextId;
        reset(initial);
        setAllowFilters(initial?.systemConfig?.inputFileFilters?.allow || []);
        setExcludeFilters(initial?.systemConfig?.inputFileFilters?.exclude || []);
        setValidationErrors({});
        setSaveWarnings([]);
    }, [initial, reset]);

    const onSubmit = async (data: Partial<Pipeline>) => {
        // A save that returned warnings leaves the form open on the acknowledge banner; the entity
        // already exists, so a further submit would create/update a second time.
        if (saveWarnings.length > 0) return;

        // In the page wizard only the final step saves. Any submit raised from an earlier step is
        // spurious — an Enter keypress in a text input, or a click that lands as the footer swaps
        // Next for Save — and would persist a half-configured pipeline, then navigate away before
        // the remaining steps were ever shown.
        if (variant === "page" && wizardStep !== LAST_PIPELINE_STEP) {
            return;
        }

        const validation = validatePipeline(data);
        if (!validation.ok) {
            const errors = (validation.errors as Record<string, string>) || {};
            setValidationErrors(errors);
            // Page mode saves from the last step, so move to the step holding the first message.
            const target = Object.keys(errors)
                .map(errorStep)
                .find((step) => !!step);
            if (variant === "page" && target) setWizardStep(target);
            return;
        }

        setValidationErrors({});

        const allow = allowFilters;
        const exclude = excludeFilters;

        // pipelineId is sent as null when the user did not supply one, so the backend auto-generates
        // it. An empty string is rejected (min_length=1), so never send "".
        const body: PipelineCreateRequest = {
            databaseId,
            pipelineId: data.pipelineId || null,
            pipelineName: data.pipelineName || "",
            category: data.category,
            description: data.description,
            enabled: data.enabled,
            executionConfig: data.executionConfig!,
            systemConfig: {
                ...data.systemConfig,
                inputFileFilters: { allow, exclude },
            },
        };

        try {
            const result =
                mode === "create"
                    ? await createMutation.mutateAsync(body)
                    : await updateMutation.mutateAsync({
                          databaseId,
                          pipelineId: initial?.pipelineId || "",
                          // The update route takes the id from the path; the body must not carry a
                          // null id (the update model ignores it, but keep the payload honest).
                          body: { ...body, pipelineId: initial?.pipelineId },
                      });
            // The save succeeded. If the backend returned non-blocking warnings, show them and let
            // the user acknowledge before closing; otherwise close immediately.
            const warnings = (result as any)?.warnings;
            if (Array.isArray(warnings) && warnings.length > 0) {
                setSaveWarnings(warnings);
                toast.warning(mode === "create" ? "Pipeline created" : "Pipeline saved", {
                    description: warnings[0],
                });
            } else {
                // The form closes here, so the toast is the only confirmation the save landed.
                toast.success(mode === "create" ? "Pipeline created" : "Pipeline saved", {
                    description: body.pipelineName || undefined,
                });
                setIsOpen(false);
                onDone();
            }
        } catch (err) {
            // Kept as the form-level summary (it sits with the fields) AND raised as a toast, so a
            // rejection is visible even when the summary is scrolled out of view.
            const message = toastErrorMessage(err, "Submission failed");
            setValidationErrors({ _form: message });
            toast.error(mode === "create" ? "Create failed" : "Save failed", {
                description: message,
            });
        }
    };

    const handleClose = () => {
        setIsOpen(false);
        onDone();
    };

    const isPage = variant === "page";

    // Errors on paths without their own inline message element, shown as a form-level summary.
    const unmappedErrors = Object.entries(validationErrors).filter(
        ([key]) => !MAPPED_ERROR_KEYS.has(key)
    );

    // Page variant is a stepper wizard (mirrors the workflow builder). Dialog variant shows every
    // section at once. Each section maps to a step; the last step (settings) shows Save.
    const stepIndex = PIPELINE_STEPS.findIndex((s) => s.id === wizardStep);
    // In dialog mode all sections render; in page mode only the active step's section renders.
    const showSection = (id: string) => !isPage || wizardStep === id;
    const isLastStep = stepIndex === PIPELINE_STEPS.length - 1;

    // Per-step validity gate for the page wizard: the Basic step needs a name; the Execution step
    // needs the execution-type-specific required fields. Blocks Next until the step is valid.
    const pipelineNameVal = watch("pipelineName");
    const stepValid = (() => {
        if (wizardStep === "basic") return !!(pipelineNameVal || "").trim();
        if (wizardStep === "execution") {
            if (executionType === "SQS") return !!watch("executionConfig.sqs.queueUrl");
            if (executionType === "EventBridge")
                return !!(
                    watch("executionConfig.eventBridge.busArn") &&
                    watch("executionConfig.eventBridge.source") &&
                    watch("executionConfig.eventBridge.detailType")
                );
            if (executionType === "DeadlineCloud")
                return !!(
                    watch("executionConfig.deadlineCloud.farmId") &&
                    watch("executionConfig.deadlineCloud.queueId")
                );
            return true;
        }
        return true;
    })();

    const footerButtons = (
        <>
            <button type="button" onClick={handleClose} className={btnSecondary}>
                Cancel
            </button>
            {/* Page mode: Back/Next between steps, Save on the last. Dialog mode: single Save. */}
            {isPage && stepIndex > 0 && (
                <button
                    type="button"
                    onClick={() => setWizardStep(PIPELINE_STEPS[stepIndex - 1].id)}
                    className={btnSecondary}
                >
                    Back
                </button>
            )}
            {/* The Next and submit buttons carry distinct keys so React mounts a NEW DOM node when the
                step changes instead of reusing one and only swapping its `type`. Advancing onto the
                last step re-renders this footer during the Next click; a reused element would already
                be `type="submit"` by the time the browser completed that click, submitting the form
                and saving a half-configured pipeline before the last step was ever shown. */}
            {isPage && !isLastStep ? (
                <button
                    key="wizard-next"
                    type="button"
                    onClick={() => setWizardStep(PIPELINE_STEPS[stepIndex + 1].id)}
                    disabled={!stepValid}
                    className={btnPrimary}
                >
                    Next
                </button>
            ) : (
                !isDeadlineCloudDisabled &&
                saveWarnings.length === 0 && (
                    <button
                        key="wizard-save"
                        type="submit"
                        form="pipeline-form"
                        disabled={isSubmitting}
                        className={btnPrimary}
                    >
                        {isSubmitting ? "Saving..." : mode === "create" ? "Create" : "Update"}
                    </button>
                )
            )}
        </>
    );

    const formBody = (
        <form id="pipeline-form" onSubmit={handleSubmit(onSubmit)} className="space-y-4">
            {isPage && <Stepper steps={PIPELINE_STEPS} current={wizardStep} />}
            {isDeadlineCloudDisabled && (
                <div className="p-3 bg-yellow-100 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-300 rounded">
                    <strong>Read-only:</strong> This DeadlineCloud pipeline cannot be edited because
                    the DeadlineCloud feature is disabled on this system. You may delete it or
                    remove it from workflows.
                </div>
            )}
            {validationErrors._form && (
                <div className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded whitespace-pre-line">
                    {validationErrors._form}
                </div>
            )}

            {unmappedErrors.length > 0 && (
                <div className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded">
                    <ul className="list-disc list-inside space-y-1">
                        {unmappedErrors.map(([key, message]) => (
                            <li key={key}>
                                {key}: {message}
                            </li>
                        ))}
                    </ul>
                </div>
            )}

            {saveWarnings.length > 0 && (
                <div className="p-3 bg-yellow-100 dark:bg-yellow-900/20 text-yellow-800 dark:text-yellow-300 rounded space-y-2">
                    <strong>Pipeline saved with warnings:</strong>
                    <ul className="list-disc list-inside space-y-1">
                        {saveWarnings.map((warning, idx) => (
                            <li key={idx}>{warning}</li>
                        ))}
                    </ul>
                    <button
                        type="button"
                        onClick={() => {
                            setSaveWarnings([]);
                            setIsOpen(false);
                            onDone();
                        }}
                        className={btnPrimary}
                    >
                        Acknowledge
                    </button>
                </div>
            )}

            {showSection("basic") && (
                <CollapsibleSection
                    title="Basic information"
                    description="Name, category, description, and status."
                >
                    {/* Pipeline ID is auto-generated by the backend on create (prevents collisions); it
                    is not a user-entered field on the web. Shown read-only when editing. The CLI
                    keeps it as an optional override for CDK auto-registration. */}
                    {mode === "edit" && initial?.pipelineId && (
                        <div>
                            <label htmlFor="pipelineId" className="block text-sm font-medium mb-1">
                                Pipeline ID
                            </label>
                            <input
                                id="pipelineId"
                                value={initial.pipelineId}
                                disabled
                                className="w-full px-3 py-2 border border-border-input rounded bg-surface-secondary text-text-primary opacity-50"
                            />
                        </div>
                    )}

                    <div>
                        <label htmlFor="pipelineName" className="block text-sm font-medium mb-1">
                            Pipeline Name *
                        </label>
                        <input
                            id="pipelineName"
                            {...register("pipelineName")}
                            disabled={isDeadlineCloudDisabled}
                            maxLength={256}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                            placeholder="Pipeline name"
                        />
                        {validationErrors.pipelineName && (
                            <p className="text-vams-error text-sm mt-1">
                                {validationErrors.pipelineName}
                            </p>
                        )}
                    </div>

                    <div>
                        <label htmlFor="category" className="block text-sm font-medium mb-1">
                            Category
                        </label>
                        <input
                            id="category"
                            {...register("category")}
                            disabled={isDeadlineCloudDisabled}
                            maxLength={256}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                            placeholder="e.g. 3D, GenAI"
                        />
                        {validationErrors.category && (
                            <p className="text-vams-error text-sm mt-1">
                                {validationErrors.category}
                            </p>
                        )}
                    </div>

                    <div>
                        <label htmlFor="description" className="block text-sm font-medium mb-1">
                            Description
                        </label>
                        <textarea
                            id="description"
                            {...register("description")}
                            disabled={isDeadlineCloudDisabled}
                            rows={3}
                            maxLength={1024}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                            placeholder="Pipeline description"
                        />
                        {validationErrors.description && (
                            <p className="text-vams-error text-sm mt-1">
                                {validationErrors.description}
                            </p>
                        )}
                    </div>

                    <div className="flex items-center gap-2">
                        <input
                            {...register("enabled")}
                            type="checkbox"
                            id="enabled"
                            disabled={isDeadlineCloudDisabled}
                        />
                        <label htmlFor="enabled" className="text-sm font-medium">
                            Enabled
                        </label>
                    </div>
                </CollapsibleSection>
            )}

            {showSection("execution") && (
                <CollapsibleSection
                    title="Execution configuration"
                    description="How the pipeline runs: execution type and its resource/callback settings."
                >
                    <div>
                        <label htmlFor="executionType" className="block text-sm font-medium mb-1">
                            Execution Type *
                        </label>
                        <select
                            id="executionType"
                            {...register("executionConfig.executionType")}
                            disabled={isDeadlineCloudDisabled}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                        >
                            <option value="Lambda">Lambda</option>
                            <option value="SQS">SQS</option>
                            <option value="EventBridge">EventBridge</option>
                            {(showDeadlineCloud ||
                                initial?.executionConfig?.executionType === "DeadlineCloud") && (
                                <option value="DeadlineCloud">DeadlineCloud</option>
                            )}
                        </select>
                        {validationErrors["executionConfig.executionType"] && (
                            <p className="text-vams-error text-sm mt-1">
                                {validationErrors["executionConfig.executionType"]}
                            </p>
                        )}
                    </div>

                    {executionType === "Lambda" && (
                        <div>
                            <label
                                htmlFor="lambdaResourceId"
                                className="block text-sm font-medium mb-1"
                            >
                                Lambda Resource ID
                            </label>
                            <input
                                id="lambdaResourceId"
                                {...register("executionConfig.lambda.resourceId")}
                                disabled={isDeadlineCloudDisabled}
                                className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                placeholder="Lambda function ARN or name"
                            />
                            {validationErrors["executionConfig.lambda.resourceId"] && (
                                <p className="text-vams-error text-sm mt-1">
                                    {validationErrors["executionConfig.lambda.resourceId"]}
                                </p>
                            )}
                            {!watch("executionConfig.lambda.resourceId") && (
                                <p className="text-text-secondary text-sm mt-1">
                                    Leave blank to auto-provision a new Lambda
                                </p>
                            )}
                        </div>
                    )}

                    {executionType === "SQS" && (
                        <div>
                            <label htmlFor="queueUrl" className="block text-sm font-medium mb-1">
                                Queue URL *
                            </label>
                            <input
                                id="queueUrl"
                                {...register("executionConfig.sqs.queueUrl")}
                                disabled={isDeadlineCloudDisabled}
                                className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                placeholder="https://sqs.region.amazonaws.com/account/queue"
                            />
                            {validationErrors["executionConfig.sqs.queueUrl"] && (
                                <p className="text-vams-error text-sm mt-1">
                                    {validationErrors["executionConfig.sqs.queueUrl"]}
                                </p>
                            )}
                        </div>
                    )}

                    {executionType === "EventBridge" && (
                        <>
                            <div>
                                <label htmlFor="busArn" className="block text-sm font-medium mb-1">
                                    Event Bus ARN *
                                </label>
                                <input
                                    id="busArn"
                                    {...register("executionConfig.eventBridge.busArn")}
                                    disabled={isDeadlineCloudDisabled}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                    placeholder="arn:aws:events:region:account:event-bus/name"
                                />
                                {validationErrors["executionConfig.eventBridge.busArn"] && (
                                    <p className="text-vams-error text-sm mt-1">
                                        {validationErrors["executionConfig.eventBridge.busArn"]}
                                    </p>
                                )}
                            </div>
                            <div>
                                <label htmlFor="source" className="block text-sm font-medium mb-1">
                                    Source *
                                </label>
                                <input
                                    id="source"
                                    {...register("executionConfig.eventBridge.source")}
                                    disabled={isDeadlineCloudDisabled}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                    placeholder="e.g. vams.pipeline"
                                />
                                {validationErrors["executionConfig.eventBridge.source"] && (
                                    <p className="text-vams-error text-sm mt-1">
                                        {validationErrors["executionConfig.eventBridge.source"]}
                                    </p>
                                )}
                            </div>
                            <div>
                                <label
                                    htmlFor="detailType"
                                    className="block text-sm font-medium mb-1"
                                >
                                    Detail Type *
                                </label>
                                <input
                                    id="detailType"
                                    {...register("executionConfig.eventBridge.detailType")}
                                    disabled={isDeadlineCloudDisabled}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                    placeholder="e.g. PipelineExecution"
                                />
                                {validationErrors["executionConfig.eventBridge.detailType"] && (
                                    <p className="text-vams-error text-sm mt-1">
                                        {validationErrors["executionConfig.eventBridge.detailType"]}
                                    </p>
                                )}
                            </div>
                        </>
                    )}

                    {executionType === "DeadlineCloud" && (
                        <>
                            <div>
                                <label htmlFor="farmId" className="block text-sm font-medium mb-1">
                                    Farm ID *
                                </label>
                                <input
                                    id="farmId"
                                    {...register("executionConfig.deadlineCloud.farmId")}
                                    disabled={isDeadlineCloudDisabled}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                    placeholder="farm-..."
                                />
                                {validationErrors["executionConfig.deadlineCloud.farmId"] && (
                                    <p className="text-vams-error text-sm mt-1">
                                        {validationErrors["executionConfig.deadlineCloud.farmId"]}
                                    </p>
                                )}
                            </div>
                            <div>
                                <label htmlFor="queueId" className="block text-sm font-medium mb-1">
                                    Queue ID *
                                </label>
                                <input
                                    id="queueId"
                                    {...register("executionConfig.deadlineCloud.queueId")}
                                    disabled={isDeadlineCloudDisabled}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                    placeholder="queue-..."
                                />
                                {validationErrors["executionConfig.deadlineCloud.queueId"] && (
                                    <p className="text-vams-error text-sm mt-1">
                                        {validationErrors["executionConfig.deadlineCloud.queueId"]}
                                    </p>
                                )}
                            </div>
                            <div>
                                <label className="block text-sm font-medium mb-1">
                                    Storage Profile ID
                                </label>
                                <input
                                    {...register("executionConfig.deadlineCloud.storageProfileId")}
                                    disabled={isDeadlineCloudDisabled}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium mb-1">Priority</label>
                                <input
                                    {...register(
                                        "executionConfig.deadlineCloud.priority",
                                        optionalNumberField
                                    )}
                                    type="number"
                                    disabled={isDeadlineCloudDisabled}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium mb-1">
                                    Max Retries Per Task
                                </label>
                                <input
                                    {...register(
                                        "executionConfig.deadlineCloud.maxRetriesPerTask",
                                        optionalNumberField
                                    )}
                                    type="number"
                                    disabled={isDeadlineCloudDisabled}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium mb-1">
                                    Max Failed Tasks Count
                                </label>
                                <input
                                    {...register(
                                        "executionConfig.deadlineCloud.maxFailedTasksCount",
                                        optionalNumberField
                                    )}
                                    type="number"
                                    disabled={isDeadlineCloudDisabled}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                />
                            </div>
                            <div>
                                <label className="block text-sm font-medium mb-1">
                                    Template Type
                                </label>
                                <input
                                    {...register("executionConfig.deadlineCloud.templateType")}
                                    disabled={isDeadlineCloudDisabled}
                                    className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                                />
                            </div>
                        </>
                    )}

                    <div>
                        <label htmlFor="waitForCallback" className="block text-sm font-medium mb-1">
                            Wait For Callback
                        </label>
                        <select
                            id="waitForCallback"
                            {...register("executionConfig.waitForCallback")}
                            disabled={executionType === "DeadlineCloud" || isDeadlineCloudDisabled}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                        >
                            <option value="Enabled">Enabled</option>
                            <option value="Disabled">Disabled</option>
                        </select>
                        {executionType === "DeadlineCloud" && (
                            <p className="text-text-secondary text-sm mt-1">
                                Locked to Enabled for DeadlineCloud
                            </p>
                        )}
                        {validationErrors["executionConfig.waitForCallback"] && (
                            <p className="text-vams-error text-sm mt-1">
                                {validationErrors["executionConfig.waitForCallback"]}
                            </p>
                        )}
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1">
                            Task Timeout (seconds)
                        </label>
                        <input
                            {...register("executionConfig.taskTimeout")}
                            disabled={isDeadlineCloudDisabled}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                            placeholder="1-604800 (max 1 week)"
                        />
                        {validationErrors["executionConfig.taskTimeout"] && (
                            <p className="text-vams-error text-sm mt-1">
                                {validationErrors["executionConfig.taskTimeout"]}
                            </p>
                        )}
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1">
                            Task Heartbeat Timeout (seconds)
                        </label>
                        <input
                            {...register("executionConfig.taskHeartbeatTimeout")}
                            disabled={isDeadlineCloudDisabled}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                            placeholder="1-604800 (max 1 week)"
                        />
                        {validationErrors["executionConfig.taskHeartbeatTimeout"] && (
                            <p className="text-vams-error text-sm mt-1">
                                {validationErrors["executionConfig.taskHeartbeatTimeout"]}
                            </p>
                        )}
                    </div>
                </CollapsibleSection>
            )}

            {showSection("settings") && (
                <CollapsibleSection
                    title="Execution settings"
                    description="Admin controls: input-file count, asset selection rules, metadata inputs, templates, and filters."
                >
                    <div>
                        <div className="flex items-center gap-1.5 text-sm font-medium mb-1">
                            Input file count
                            <InfoTooltip text="How many input files an execution of this pipeline takes: 'None' (no input file), 'One file', or 'Multiple files'." />
                        </div>
                        <select
                            value={inputFileArity}
                            onChange={(e) => {
                                const value = e.target.value;
                                // Results-only ('none') runs take no input files, so the input-file
                                // filters do not apply — clear them so a stale filter isn't persisted.
                                if (value === "none") {
                                    setAllowFilters([]);
                                    setExcludeFilters([]);
                                }
                                setValue("systemConfig.inputFileArity", value as any);
                            }}
                            disabled={isDeadlineCloudDisabled}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                        >
                            <option value="none">None</option>
                            <option value="one">One file</option>
                            <option value="multi">Multiple files</option>
                        </select>
                    </div>

                    <div>
                        <div className="flex items-center gap-1.5 text-sm font-medium mb-2">
                            Asset selection rules
                            <InfoTooltip text="Constrains which input-file selections an execution of this pipeline may make. Each rule is enforced at execute time." />
                        </div>
                        <AssetSpanControl
                            scope={watch("systemConfig.assetScope") || {}}
                            disabled={isDeadlineCloudDisabled}
                            onChange={(s) =>
                                setValue("systemConfig.assetScope", s as Record<string, boolean>)
                            }
                        />
                    </div>

                    {/* Input-file filters sit directly beneath the asset selection rules (both
                        constrain the input selection). Hidden for results-only ('none') pipelines,
                        which take no input files. */}
                    {inputFileArity !== "none" && (
                        <>
                            <div>
                                <div className="flex items-center gap-1.5 text-sm font-medium mb-1">
                                    Input file filters — allow
                                    <InfoTooltip text="Only files matching an allow entry are eligible as inputs (when any allow entry is set). Each entry may be an extension (*.glb), a file name, a path (/models/), or a wildcard." />
                                </div>
                                <StringListInput
                                    ariaLabel="Add allow filter"
                                    value={allowFilters}
                                    onChange={setAllowFilters}
                                    placeholder="e.g. *.glb  or  /models/  or  building.fbx"
                                />
                            </div>

                            <div>
                                <div className="flex items-center gap-1.5 text-sm font-medium mb-1">
                                    Input file filters — exclude
                                    <InfoTooltip text="Files matching an exclude entry are never eligible as inputs. Each entry may be an extension, file name, path, or wildcard. Exclude takes precedence over allow." />
                                </div>
                                <StringListInput
                                    ariaLabel="Add exclude filter"
                                    value={excludeFilters}
                                    onChange={setExcludeFilters}
                                    placeholder="e.g. *.tmp  or  /drafts/"
                                />
                            </div>
                        </>
                    )}

                    <div>
                        <div className="flex items-center gap-1.5 text-sm font-medium mb-2">
                            Metadata provided to the pipeline
                            <InfoTooltip text="Which metadata is gathered from the input assets/files and passed to the pipeline in the shared metadata envelope." />
                        </div>
                        <div className="space-y-2">
                            <label className="flex items-center gap-2">
                                <input
                                    {...register("systemConfig.metadataInputs.assetMetadata")}
                                    type="checkbox"
                                    disabled={isDeadlineCloudDisabled}
                                />
                                <span className="text-sm">Asset metadata</span>
                                <InfoTooltip text="Include each input asset's asset-level metadata." />
                            </label>
                            <label className="flex items-center gap-2">
                                <input
                                    {...register("systemConfig.metadataInputs.fileMetadata")}
                                    type="checkbox"
                                    disabled={isDeadlineCloudDisabled}
                                />
                                <span className="text-sm">File metadata</span>
                                <InfoTooltip text="Include per-file metadata for each input file." />
                            </label>
                            <label className="flex items-center gap-2">
                                <input
                                    {...register("systemConfig.metadataInputs.fileAttributes")}
                                    type="checkbox"
                                    disabled={isDeadlineCloudDisabled}
                                />
                                <span className="text-sm">File attributes</span>
                                <InfoTooltip text="Include per-file attributes (the string-typed file attribute fields)." />
                            </label>
                        </div>
                    </div>

                    <div className="flex items-center gap-2">
                        <input
                            {...register("systemConfig.requireTemplate")}
                            type="checkbox"
                            id="requireTemplate"
                            disabled={isDeadlineCloudDisabled}
                        />
                        <label htmlFor="requireTemplate" className="text-sm font-medium">
                            Require template
                        </label>
                        <InfoTooltip text="When on, every execution of this pipeline must select one of its configuration templates — an execution cannot run without a template." />
                    </div>

                    <div className="flex items-center gap-2">
                        <input
                            {...register("systemConfig.allowCustomTemplateOverride")}
                            type="checkbox"
                            id="allowCustom"
                            disabled={isDeadlineCloudDisabled}
                        />
                        <label htmlFor="allowCustom" className="text-sm font-medium">
                            Allow custom template override
                        </label>
                        <InfoTooltip text="When on, an execution may supply its own raw configuration body in place of a saved template (a one-off override), instead of choosing a predefined template." />
                    </div>

                    <div>
                        <label className="block text-sm font-medium mb-1">
                            Aux Preview Pipeline Suffix
                        </label>
                        <input
                            {...register("systemConfig.auxPreviewPipelineSuffix")}
                            disabled={isDeadlineCloudDisabled}
                            className="w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary disabled:opacity-50"
                            placeholder="e.g. -preview"
                        />
                    </div>
                </CollapsibleSection>
            )}
        </form>
    );

    // Page variant: full-page with breadcrumb + heading + footer buttons at the bottom.
    if (isPage) {
        return (
            <div className="orchestration-root orchestration-page space-y-6 bg-surface min-h-full">
                <div className="space-y-2">
                    <Breadcrumb
                        items={[
                            { label: "Pipelines", to: `/databases/${databaseId}/pipelines` },
                            {
                                label:
                                    mode === "create"
                                        ? "Create Pipeline"
                                        : initial?.pipelineName ||
                                          initial?.pipelineId ||
                                          "Edit Pipeline",
                            },
                        ]}
                    />
                    <h1 className="text-text-primary">
                        {mode === "create" ? "Create Pipeline" : "Edit Pipeline"}
                    </h1>
                </div>
                {formBody}
                <div className="flex justify-end gap-2">{footerButtons}</div>
            </div>
        );
    }

    // Dialog variant (default).
    return (
        <Dialog
            open={isOpen}
            onOpenChange={handleClose}
            title={mode === "create" ? "Create Pipeline" : "Edit Pipeline"}
            footer={footerButtons}
        >
            {formBody}
        </Dialog>
    );
};

export default PipelineForm;
