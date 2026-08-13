/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useReducer, useEffect, useCallback, useState, Suspense } from "react";
import { useNavigate } from "react-router-dom";
import {
    useAllPipelines,
    useWorkflow,
    useWorkflowMutations,
    useTemplates,
    usePrefetchPipelineTemplates,
} from "../api/queries";
import PipelineOrderList from "./PipelineOrderList";
import WorkflowSystemConfigFields from "./WorkflowSystemConfigFields";
import WorkflowValidationPanel from "./WorkflowValidationPanel";
import TriggersEditor from "./TriggersEditor";
import Breadcrumb from "../components/Breadcrumb";
import Stepper from "../components/Stepper";
import { btnPrimary, btnSecondary } from "../components/controlStyles";
import { validateWorkflow, allPipelineRefsSelected } from "./workflowValidation";
import type {
    Workflow,
    WorkflowCreateRequest,
    SpecifiedPipelineRef,
    InputFileArity,
    ConcurrencyRestriction,
    OutputLocationType,
    Template,
} from "../types";
import { useToast, toastErrorMessage } from "../components/ToastProvider";

const DagPreview = React.lazy(() => import("./DagPreview"));

interface WorkflowBuilderProps {
    mode: "create" | "edit";
    databaseId: string;
    workflowId?: string;
}

interface WorkflowFormState {
    workflowIdValue: string;
    workflowName: string;
    category: string;
    description: string;
    subDashboardUrl: string;
    enabled: boolean;
    inputFileArity: InputFileArity;
    assetScope: Record<string, boolean>;
    metadataInputs: Record<string, boolean>;
    allowFilters: string[];
    excludeFilters: string[];
    concurrencyRestriction: ConcurrencyRestriction;
    locationType: OutputLocationType;
    allowOverride: boolean;
    allowWorkflowTriggerChaining: boolean;
    defaultOutputPathPrefix: string;
    specifiedPipelines: SpecifiedPipelineRef[];
    templatesByPipeline: Record<string, Template[]>;
    validationErrors: string[];
    validationWarnings: string[];
    backendWarnings: string[];
    saving: boolean;
    saveError: string | null;
    /** Set by any action that changes an authored value, so Cancel can confirm before discarding. */
    dirty: boolean;
}

type WorkflowFormAction =
    // `authored` false for a value the form derives itself rather than one the user entered, so it
    // does not count toward dirtiness.
    | { type: "SET_FIELD"; field: keyof WorkflowFormState; value: any; authored?: boolean }
    | { type: "LOAD_WORKFLOW"; workflow: Workflow }
    | { type: "SET_TEMPLATES"; key: string; templates: Template[] }
    | { type: "SET_VALIDATION"; errors: string[]; warnings: string[] }
    | { type: "SET_SAVING"; saving: boolean }
    | { type: "SET_SAVE_ERROR"; error: string | null }
    | { type: "SET_BACKEND_WARNINGS"; warnings: string[] }
    | { type: "RESET" };

const initialState: WorkflowFormState = {
    workflowIdValue: "",
    workflowName: "",
    category: "",
    description: "",
    subDashboardUrl: "",
    enabled: true,
    inputFileArity: "one",
    assetScope: {},
    metadataInputs: {},
    allowFilters: [],
    excludeFilters: [],
    concurrencyRestriction: "none",
    locationType: "asset",
    allowOverride: false,
    allowWorkflowTriggerChaining: false,
    defaultOutputPathPrefix: "",
    specifiedPipelines: [],
    templatesByPipeline: {},
    validationErrors: [],
    validationWarnings: [],
    backendWarnings: [],
    saving: false,
    saveError: null,
    dirty: false,
};

function workflowFormReducer(
    state: WorkflowFormState,
    action: WorkflowFormAction
): WorkflowFormState {
    switch (action.type) {
        case "SET_FIELD":
            return {
                ...state,
                [action.field]: action.value,
                dirty: state.dirty || action.authored !== false,
            };
        case "LOAD_WORKFLOW": {
            const workflow = action.workflow;
            const sc = workflow.systemConfig || {};
            return {
                ...state,
                workflowIdValue: workflow.workflowId || "",
                workflowName: workflow.workflowName || "",
                category: workflow.category || "",
                description: workflow.description || "",
                subDashboardUrl: workflow.subDashboardUrl || "",
                enabled: workflow.enabled ?? true,
                specifiedPipelines: workflow.specifiedPipelines || [],
                inputFileArity: sc.inputFileArity || "one",
                assetScope: sc.assetScope || {},
                metadataInputs: sc.metadataInputs || {},
                allowFilters: sc.inputFileFilters?.allow || [],
                excludeFilters: sc.inputFileFilters?.exclude || [],
                concurrencyRestriction: sc.concurrencyRestriction || "none",
                locationType: sc.outputTarget?.locationType || "asset",
                allowOverride: sc.outputTarget?.allowOverride ?? false,
                allowWorkflowTriggerChaining: sc.allowWorkflowTriggerChaining ?? false,
                defaultOutputPathPrefix: sc.defaultOutputFileBaseExecutionPathExtension || "",
                dirty: false,
            };
        }
        case "SET_TEMPLATES":
            return {
                ...state,
                templatesByPipeline: {
                    ...state.templatesByPipeline,
                    [action.key]: action.templates,
                },
            };
        case "SET_VALIDATION":
            return {
                ...state,
                validationErrors: action.errors,
                validationWarnings: action.warnings,
            };
        case "SET_SAVING":
            return { ...state, saving: action.saving };
        case "SET_SAVE_ERROR":
            return { ...state, saveError: action.error };
        case "SET_BACKEND_WARNINGS":
            return { ...state, backendWarnings: action.warnings };
        case "RESET":
            return initialState;
        default:
            return state;
    }
}

const TemplatesFetcher: React.FC<{
    pipelineDatabaseId: string;
    pipelineId: string;
    onTemplatesLoaded: (key: string, templates: Template[]) => void;
}> = ({ pipelineDatabaseId, pipelineId, onTemplatesLoaded }) => {
    const { data: templates } = useTemplates(pipelineDatabaseId, pipelineId);
    const key = `${pipelineDatabaseId}:${pipelineId}`;

    useEffect(() => {
        if (templates) {
            onTemplatesLoaded(key, templates);
        }
    }, [templates, key, onTemplatesLoaded]);

    return null;
};

const WorkflowBuilder: React.FC<WorkflowBuilderProps> = ({ mode, databaseId, workflowId }) => {
    const navigate = useNavigate();
    const toast = useToast();
    // Pipeline picker scope (mirrors the backend rule enforced in workflowService):
    //   - GLOBAL workflow  -> only GLOBAL pipelines
    //   - database workflow -> GLOBAL + that database's pipelines (the DB list endpoint returns only
    //     the database's own pipelines, so GLOBAL is fetched separately and merged).
    // Archived pipelines are included so an existing workflow's reference to one still resolves: the
    // card shows which pipeline it is (tagged and non-selectable) and validation reports the block.
    const isGlobalWorkflow = databaseId === "GLOBAL";
    const { data: dbPipelines = [], isSuccess: dbPipelinesLoaded } = useAllPipelines(
        databaseId,
        true
    );
    const { data: globalPipelines = [], isSuccess: globalPipelinesLoaded } = useAllPipelines(
        "GLOBAL",
        true,
        !isGlobalWorkflow
    );
    // Only once BOTH lists have arrived is a reference that resolves to nothing genuinely missing
    // rather than not fetched yet.
    const pipelinesLoaded = !!dbPipelinesLoaded && (isGlobalWorkflow || !!globalPipelinesLoaded);
    const pipelines = React.useMemo(() => {
        if (isGlobalWorkflow) return dbPipelines;
        const seen = new Set<string>();
        return [...dbPipelines, ...globalPipelines].filter((p: any) => {
            const key = `${p.databaseId}:${p.pipelineId}`;
            if (seen.has(key)) return false;
            seen.add(key);
            return true;
        });
    }, [isGlobalWorkflow, dbPipelines, globalPipelines]);
    const { data: workflow } = useWorkflow(databaseId, workflowId || "");
    const { createWorkflow, updateWorkflow } = useWorkflowMutations();

    const [state, dispatch] = useReducer(workflowFormReducer, initialState);
    const [wizardStep, setWizardStep] = useState<string>("basic");
    // Set once the save succeeded but the backend returned non-fatal warnings. The builder stays
    // mounted so the warning list is readable, and navigation waits for an explicit acknowledgement.
    const [savedWithWarnings, setSavedWithWarnings] = useState(false);

    const handleTemplatesLoaded = useCallback((key: string, templates: Template[]) => {
        dispatch({ type: "SET_TEMPLATES", key, templates });
    }, []);

    // Warm each referenced pipeline's template list before the Pipelines step is reached. The
    // TemplatesFetcher rows only mount on that step, so in edit mode — where the pipelines are already
    // chosen — arriving there previously meant waiting on a fetch per pipeline with nothing rendered.
    // Session-scoped: the entries are dropped when the builder unmounts.
    const templatePrefetchTargets = React.useMemo(
        () =>
            state.specifiedPipelines
                .filter((ref) => ref.pipelineId && ref.pipelineDatabaseId)
                .map((ref) => ({
                    databaseId: ref.pipelineDatabaseId as string,
                    pipelineId: ref.pipelineId,
                    defaultTemplateId: ref.defaultTemplateId || undefined,
                })),
        [state.specifiedPipelines]
    );
    usePrefetchPipelineTemplates(templatePrefetchTargets);

    // The route reuses one element for every /databases/:databaseId/workflows/:workflowId, so an
    // edit-to-edit navigation changes the props without remounting: clear the previous workflow's
    // values instead of showing them until the new query resolves.
    useEffect(() => {
        dispatch({ type: "RESET" });
    }, [databaseId, workflowId]);

    useEffect(() => {
        if (mode === "edit" && workflow) {
            dispatch({ type: "LOAD_WORKFLOW", workflow });
        }
    }, [mode, workflow]);

    // When output writes to an asset but there are no input files (arity 'none'), there is no input
    // asset to lock the output to — so an output asset must be selectable at execute time. Force
    // allowOverride on in that case (the backend enforces the same rule at save). Results-only
    // ('none') is NOT coupled to arity: it may take input files (e.g. metadata analysis).
    useEffect(() => {
        if (
            state.locationType === "asset" &&
            state.inputFileArity === "none" &&
            !state.allowOverride
        ) {
            dispatch({
                type: "SET_FIELD",
                field: "allowOverride",
                value: true,
                authored: false,
            });
        }
    }, [state.locationType, state.inputFileArity, state.allowOverride]);

    const assembleWorkflow = useCallback((): WorkflowCreateRequest => {
        return {
            databaseId,
            // Sent as null when the user did not supply one so the backend auto-generates it. An
            // empty string is rejected (min_length=1), so never send "".
            workflowId: state.workflowIdValue || null,
            workflowName: state.workflowName,
            category: state.category,
            description: state.description,
            subDashboardUrl: state.subDashboardUrl,
            enabled: state.enabled,
            specifiedPipelines: state.specifiedPipelines,
            systemConfig: {
                inputFileArity: state.inputFileArity,
                assetScope: state.assetScope,
                metadataInputs: state.metadataInputs,
                inputFileFilters: {
                    allow: state.allowFilters,
                    exclude: state.excludeFilters,
                },
                concurrencyRestriction: state.concurrencyRestriction,
                outputTarget: {
                    locationType: state.locationType,
                    allowOverride: state.allowOverride,
                },
                allowWorkflowTriggerChaining: state.allowWorkflowTriggerChaining,
                defaultOutputFileBaseExecutionPathExtension: state.defaultOutputPathPrefix,
            },
        };
    }, [
        databaseId,
        state.workflowIdValue,
        state.workflowName,
        state.category,
        state.description,
        state.subDashboardUrl,
        state.enabled,
        state.specifiedPipelines,
        state.inputFileArity,
        state.assetScope,
        state.metadataInputs,
        state.allowFilters,
        state.excludeFilters,
        state.concurrencyRestriction,
        state.locationType,
        state.allowOverride,
        state.allowWorkflowTriggerChaining,
        state.defaultOutputPathPrefix,
    ]);

    useEffect(() => {
        const assembled = assembleWorkflow();

        const pipelinesById = pipelines.reduce((acc: Record<string, any>, p: any) => {
            const key = `${p.databaseId}:${p.pipelineId}`;
            acc[key] = p;
            return acc;
        }, {});

        const result = validateWorkflow(assembled, pipelinesById, { pipelinesLoaded });
        dispatch({ type: "SET_VALIDATION", errors: result.errors, warnings: result.warnings });
    }, [assembleWorkflow, pipelines, pipelinesLoaded]);

    const handleSave = async () => {
        if (state.validationErrors.length > 0) return;

        dispatch({ type: "SET_SAVING", saving: true });
        dispatch({ type: "SET_SAVE_ERROR", error: null });
        try {
            const body = assembleWorkflow();

            const result =
                mode === "create"
                    ? await createWorkflow.mutateAsync(body)
                    : await updateWorkflow.mutateAsync({
                          databaseId,
                          // The route param is authoritative; reducer state lags a workflowId change.
                          workflowId: workflowId || state.workflowIdValue,
                          body: { ...body, workflowId: workflowId || state.workflowIdValue },
                      });

            const warnings: string[] = Array.isArray(result?.warnings) ? result.warnings : [];
            dispatch({ type: "SET_BACKEND_WARNINGS", warnings });
            if (warnings.length > 0) {
                // Keep the author on the form so the warnings are read before leaving.
                setSavedWithWarnings(true);
                toast.warning(mode === "create" ? "Workflow created" : "Workflow saved", {
                    description: warnings[0],
                });
            } else {
                // Navigating away removes the form, so the toast carries the confirmation.
                toast.success(mode === "create" ? "Workflow created" : "Workflow saved", {
                    description: state.workflowName || undefined,
                });
                navigate(`/databases/${databaseId}/workflows`);
            }
        } catch (err) {
            // Kept in the validation panel (with the other blocking messages) AND raised as a toast.
            const message = toastErrorMessage(err, "Failed to save workflow");
            dispatch({ type: "SET_SAVE_ERROR", error: message });
            toast.error(mode === "create" ? "Create failed" : "Save failed", {
                description: message,
            });
        } finally {
            dispatch({ type: "SET_SAVING", saving: false });
        }
    };

    const isSaveDisabled = state.validationErrors.length > 0 || state.saving;

    // Wizard steps. Each section is one step; Review is last. The optional Triggers step is only
    // shown when editing — triggers are set through a separate endpoint keyed by an existing
    // workflow, so a not-yet-created workflow has nothing to attach them to.
    const WIZARD_STEPS = [
        { id: "basic", label: "Basic information" },
        { id: "execution", label: "Execution settings" },
        { id: "pipelines", label: "Pipelines" },
        ...(mode === "edit" ? [{ id: "triggers", label: "Triggers (optional)" }] : []),
        { id: "review", label: "Review" },
    ];
    const stepIndex = WIZARD_STEPS.findIndex((s) => s.id === wizardStep);
    // Per-step validity gate: Basic needs a name; Pipelines needs at least one pipeline and every
    // card to name one. Other steps impose no blocking requirement (full cross-field validation
    // still gates Save on Review).
    const stepValid = (() => {
        if (wizardStep === "basic") return !!state.workflowName.trim();
        if (wizardStep === "pipelines")
            return (
                state.specifiedPipelines.length > 0 &&
                allPipelineRefsSelected(state.specifiedPipelines)
            );
        return true;
    })();
    const goNext = () =>
        setWizardStep(WIZARD_STEPS[Math.min(stepIndex + 1, WIZARD_STEPS.length - 1)].id);
    const goBack = () => setWizardStep(WIZARD_STEPS[Math.max(stepIndex - 1, 0)].id);

    // Cancel leaves the wizard, discarding everything entered across its steps. A completed
    // four-step definition is a lot to lose to one mis-click next to Back, so confirm while there is
    // anything to lose. After a warned save the entered values are already persisted.
    const handleCancel = () => {
        if (
            state.dirty &&
            !savedWithWarnings &&
            !confirm("Discard this workflow and leave without saving?")
        ) {
            return;
        }
        navigate(-1);
    };

    return (
        <div className="orchestration-root orchestration-page space-y-6 bg-surface min-h-full">
            <div className="space-y-2">
                <Breadcrumb
                    items={[
                        { label: "Workflows", to: `/databases/${databaseId}/workflows` },
                        {
                            label:
                                mode === "create"
                                    ? "Create Workflow"
                                    : state.workflowName ||
                                      state.workflowIdValue ||
                                      "Edit Workflow",
                        },
                    ]}
                />
                <h1 className="text-text-primary">
                    {mode === "create" ? "Create Workflow" : "Edit Workflow"}
                </h1>
            </div>

            <Stepper steps={WIZARD_STEPS} current={wizardStep} />

            {wizardStep === "basic" && (
                <div className="space-y-4">
                    {/* Workflow ID is auto-generated by the backend on create (prevents collisions);
                        it is not a user-entered field on the web. It is shown read-only when editing.
                        The CLI keeps it as an optional override for CDK auto-registration. */}
                    {mode === "edit" && (
                        <div>
                            <label
                                htmlFor="workflowId"
                                className="block text-sm font-medium mb-1 text-text-primary"
                            >
                                Workflow ID
                            </label>
                            <input
                                id="workflowId"
                                type="text"
                                value={state.workflowIdValue}
                                disabled
                                className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-secondary text-text-primary opacity-50"
                            />
                        </div>
                    )}
                    <div>
                        <label
                            htmlFor="workflowName"
                            className="block text-sm font-medium mb-1 text-text-primary"
                        >
                            Workflow Name
                        </label>
                        <input
                            id="workflowName"
                            type="text"
                            value={state.workflowName}
                            onChange={(e) =>
                                dispatch({
                                    type: "SET_FIELD",
                                    field: "workflowName",
                                    value: e.target.value,
                                })
                            }
                            className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                        />
                    </div>
                    <div>
                        <label
                            htmlFor="category"
                            className="block text-sm font-medium mb-1 text-text-primary"
                        >
                            Category (optional)
                        </label>
                        <input
                            id="category"
                            type="text"
                            value={state.category}
                            onChange={(e) =>
                                dispatch({
                                    type: "SET_FIELD",
                                    field: "category",
                                    value: e.target.value,
                                })
                            }
                            className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                        />
                    </div>
                    <div>
                        <label
                            htmlFor="description"
                            className="block text-sm font-medium mb-1 text-text-primary"
                        >
                            Description (optional)
                        </label>
                        <textarea
                            id="description"
                            value={state.description}
                            onChange={(e) =>
                                dispatch({
                                    type: "SET_FIELD",
                                    field: "description",
                                    value: e.target.value,
                                })
                            }
                            rows={3}
                            className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                        />
                    </div>
                    <div>
                        <label
                            htmlFor="subDashboardUrl"
                            className="block text-sm font-medium mb-1 text-text-primary"
                        >
                            Sub-Dashboard URL (optional)
                        </label>
                        <input
                            id="subDashboardUrl"
                            type="text"
                            value={state.subDashboardUrl}
                            onChange={(e) =>
                                dispatch({
                                    type: "SET_FIELD",
                                    field: "subDashboardUrl",
                                    value: e.target.value,
                                })
                            }
                            className="orch-outline w-full px-3 py-2 border border-border-input rounded bg-surface-input text-text-primary"
                        />
                    </div>
                    <div>
                        <label className="flex items-center gap-2">
                            <input
                                type="checkbox"
                                checked={state.enabled}
                                onChange={(e) =>
                                    dispatch({
                                        type: "SET_FIELD",
                                        field: "enabled",
                                        value: e.target.checked,
                                    })
                                }
                            />
                            <span className="text-sm font-medium text-text-primary">
                                {state.enabled ? "Enabled" : "Disabled"}
                            </span>
                        </label>
                    </div>
                </div>
            )}

            {wizardStep === "execution" && (
                <WorkflowSystemConfigFields
                    inputFileArity={state.inputFileArity}
                    assetScope={state.assetScope}
                    metadataInputs={state.metadataInputs}
                    allowFilters={state.allowFilters}
                    excludeFilters={state.excludeFilters}
                    concurrencyRestriction={state.concurrencyRestriction}
                    locationType={state.locationType}
                    allowOverride={state.allowOverride}
                    allowWorkflowTriggerChaining={state.allowWorkflowTriggerChaining}
                    defaultOutputPathPrefix={state.defaultOutputPathPrefix}
                    onInputFileArityChange={(value) =>
                        dispatch({ type: "SET_FIELD", field: "inputFileArity", value })
                    }
                    onAssetScopeChange={(value) =>
                        dispatch({ type: "SET_FIELD", field: "assetScope", value })
                    }
                    onMetadataInputsChange={(value) =>
                        dispatch({ type: "SET_FIELD", field: "metadataInputs", value })
                    }
                    onAllowFiltersChange={(value) =>
                        dispatch({ type: "SET_FIELD", field: "allowFilters", value })
                    }
                    onExcludeFiltersChange={(value) =>
                        dispatch({ type: "SET_FIELD", field: "excludeFilters", value })
                    }
                    onConcurrencyRestrictionChange={(value) =>
                        dispatch({ type: "SET_FIELD", field: "concurrencyRestriction", value })
                    }
                    onLocationTypeChange={(value) =>
                        dispatch({ type: "SET_FIELD", field: "locationType", value })
                    }
                    onAllowOverrideChange={(value) =>
                        dispatch({ type: "SET_FIELD", field: "allowOverride", value })
                    }
                    onAllowWorkflowTriggerChainingChange={(value) =>
                        dispatch({
                            type: "SET_FIELD",
                            field: "allowWorkflowTriggerChaining",
                            value,
                        })
                    }
                    onDefaultOutputPathPrefixChange={(value) =>
                        dispatch({ type: "SET_FIELD", field: "defaultOutputPathPrefix", value })
                    }
                />
            )}

            {wizardStep === "pipelines" && (
                <div className="space-y-4">
                    {state.specifiedPipelines.map((ref, idx) => {
                        if (!ref.pipelineId || !ref.pipelineDatabaseId) return null;
                        return (
                            <TemplatesFetcher
                                key={`${ref.pipelineDatabaseId}:${ref.pipelineId}-${idx}`}
                                pipelineDatabaseId={ref.pipelineDatabaseId}
                                pipelineId={ref.pipelineId}
                                onTemplatesLoaded={handleTemplatesLoaded}
                            />
                        );
                    })}
                    <PipelineOrderList
                        value={state.specifiedPipelines}
                        pipelineOptions={pipelines}
                        templatesByPipeline={state.templatesByPipeline}
                        onChange={(value) =>
                            dispatch({ type: "SET_FIELD", field: "specifiedPipelines", value })
                        }
                    />
                    {state.specifiedPipelines.length > 0 && (
                        <Suspense
                            fallback={
                                <div className="text-sm text-text-secondary">
                                    Loading preview...
                                </div>
                            }
                        >
                            <DagPreview refs={state.specifiedPipelines} />
                        </Suspense>
                    )}
                </div>
            )}

            {wizardStep === "triggers" && mode === "edit" && (
                <TriggersEditor
                    databaseId={databaseId}
                    workflowId={state.workflowIdValue}
                    pipelineRefs={state.specifiedPipelines}
                />
            )}

            {wizardStep === "review" && (
                <div className="space-y-4">
                    <div className="orch-outline bg-surface-container border border-border-default rounded-lg p-4 space-y-2">
                        <h2 className="text-base font-semibold text-text-primary">Review</h2>
                        <div className="text-sm text-text-primary grid grid-cols-1 md:grid-cols-2 gap-2">
                            <div>
                                <span className="text-text-secondary">Name:</span>{" "}
                                {state.workflowName || "—"}
                            </div>
                            <div>
                                <span className="text-text-secondary">Database:</span> {databaseId}
                            </div>
                            <div>
                                <span className="text-text-secondary">Input file count:</span>{" "}
                                {state.inputFileArity}
                            </div>
                            <div>
                                <span className="text-text-secondary">Output:</span>{" "}
                                {state.locationType === "none"
                                    ? "Results only"
                                    : "Asset" + (state.allowOverride ? " (override allowed)" : "")}
                            </div>
                            <div>
                                <span className="text-text-secondary">Pipelines:</span>{" "}
                                {state.specifiedPipelines.length}
                            </div>
                        </div>
                    </div>
                </div>
            )}

            {savedWithWarnings && (
                <div
                    role="status"
                    className="p-3 rounded bg-amber-100 dark:bg-amber-900/20 text-amber-800 dark:text-amber-300"
                >
                    The workflow was saved with warnings. Review them below, then choose Continue.
                </div>
            )}

            {/* Live validation is relevant on every step. */}
            <WorkflowValidationPanel
                validationErrors={state.validationErrors}
                validationWarnings={state.validationWarnings}
                backendWarnings={state.backendWarnings}
                saveError={state.saveError}
            />

            {/* Wizard navigation. Save is only on the final (Review) step. */}
            <div className="flex justify-between gap-2">
                <button onClick={handleCancel} className={btnSecondary}>
                    Cancel
                </button>
                <div className="flex gap-2">
                    {stepIndex > 0 && (
                        <button onClick={goBack} className={btnSecondary}>
                            Back
                        </button>
                    )}
                    {stepIndex < WIZARD_STEPS.length - 1 && (
                        <button onClick={goNext} disabled={!stepValid} className={btnPrimary}>
                            Next
                        </button>
                    )}
                    {stepIndex === WIZARD_STEPS.length - 1 && (
                        <>
                            {/* After a warned save, Save stays available when editing (a PUT is
                                idempotent) so further edits remain saveable, but is withdrawn on
                                the create path where a second submit would create a second
                                workflow. Continue only leaves the form. */}
                            {(!savedWithWarnings || mode === "edit") && (
                                <button
                                    onClick={handleSave}
                                    disabled={isSaveDisabled}
                                    className={btnPrimary}
                                >
                                    {state.saving ? "Saving..." : "Save"}
                                </button>
                            )}
                            {savedWithWarnings && (
                                <button
                                    onClick={() => navigate(`/databases/${databaseId}/workflows`)}
                                    className={mode === "edit" ? btnSecondary : btnPrimary}
                                >
                                    Continue
                                </button>
                            )}
                        </>
                    )}
                </div>
            </div>
        </div>
    );
};

export default WorkflowBuilder;
