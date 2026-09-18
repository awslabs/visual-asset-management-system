/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useReducer, useEffect, useCallback, useRef, useState, Suspense } from "react";
import { useNavigate, useLocation } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
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
import TriggerDraftsEditor from "./TriggerDraftsEditor";
import { withCurrentPipelineTemplates } from "./triggerDraft";
import type { PendingTrigger } from "./triggerDraft";
import { setTrigger } from "../api/workflows";
import { invalidateTriggerQueries, seedWorkflowCache } from "../api/triggerCache";
import { useAllowedRoutes } from "../permissions/useAllowedRoutes";
import type {
    Workflow,
    WorkflowCreateRequest,
    WorkflowTrigger,
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

/** The route template the allowed-routes API returns for the trigger endpoint. */
const TRIGGER_ROUTE = "/database/{databaseId}/workflows/{workflowId}/triggers/{triggerType}";

/** What the create flow hands the edit route once the workflow exists. */
interface BuilderHopState {
    step?: string;
    pendingTriggers?: PendingTrigger[];
    backendWarnings?: string[];
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
    /** Triggers drafted before the workflow exists; written after the create POST. */
    triggerDrafts: WorkflowTrigger[];
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
    | { type: "SET_TRIGGER_DRAFTS"; drafts: WorkflowTrigger[] }
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
    triggerDrafts: [],
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
        case "SET_TRIGGER_DRAFTS":
            return { ...state, triggerDrafts: action.drafts, dirty: true };
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
    // A system workflow (shipped by a vamsSchema bundle) accepts only `enabled` on update; every other
    // field is locked and the save sends nothing else, so the unsent body is not validated either.
    const isSystemWorkflow = mode === "edit" && !!workflow?.isSystem;
    const { createWorkflow, updateWorkflow } = useWorkflowMutations();
    const location = useLocation();
    const queryClient = useQueryClient();
    // Trigger writes go to their own endpoint with their own Tier-1 grant, so a role may create a
    // workflow yet be unable to set a trigger on it.
    const { loading: permissionsLoading, can } = useAllowedRoutes();
    const canSetTriggers = can("PUT", TRIGGER_ROUTE);

    const [state, dispatch] = useReducer(workflowFormReducer, initialState);
    const [wizardStep, setWizardStep] = useState<string>("basic");
    // Set once the save succeeded but the backend returned non-fatal warnings. The builder stays
    // mounted so the warning list is readable, and navigation waits for an explicit acknowledgement.
    const [savedWithWarnings, setSavedWithWarnings] = useState(false);
    // Drafts the create flow could not write, handed to this route for the live editor. The editor
    // hands the list back as drafts are written, so what remains here is what Cancel would discard.
    const [pendingTriggers, setPendingTriggers] = useState<PendingTrigger[]>([]);
    // Asks the editor to open the first pending draft; cleared once it has, so revisiting the step
    // lists the rest without reopening a form.
    const [openPendingTrigger, setOpenPendingTrigger] = useState(false);
    const handlePendingOpened = useCallback(() => setOpenPendingTrigger(false), []);
    // Whether this route was reached from the create flow's hand-off, whose history entry it replaced.
    const hopLandedRef = useRef(false);

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
        setPendingTriggers([]);
        setOpenPendingTrigger(false);
        hopLandedRef.current = false;
    }, [databaseId, workflowId]);

    // The create flow hands off to this route once the workflow exists, carrying the step to show
    // and anything it could not finish. That hand-off is a prop change on this same element, never
    // a mount, so the state is read here; it is then cleared from history so a reload or Back does
    // not replay it.
    useEffect(() => {
        const hop = location.state as BuilderHopState | null;
        if (!hop || typeof hop !== "object") return;
        if (typeof hop.step === "string") setWizardStep(hop.step);
        const pending = Array.isArray(hop.pendingTriggers) ? hop.pendingTriggers : [];
        setPendingTriggers(pending);
        setOpenPendingTrigger(pending.length > 0);
        hopLandedRef.current = true;
        dispatch({
            type: "SET_BACKEND_WARNINGS",
            warnings: Array.isArray(hop.backendWarnings) ? hop.backendWarnings : [],
        });
        setSavedWithWarnings(false);
        navigate(location.pathname, { replace: true, state: null });
        // Keyed on the history entry: the same state must not be consumed twice, and the values
        // read here all belong to that entry.
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [databaseId, workflowId, location.key]);

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

    // Create is two phases: the workflow POST, then one trigger PUT per draft against the returned
    // id (the trigger endpoint needs the stored row, and its sibling checks are order-dependent, so
    // the PUTs run one at a time). Every branch after the POST leaves create mode — a second Save
    // here would create a second workflow.
    const createWorkflowWithTriggers = async (body: WorkflowCreateRequest) => {
        const result = await createWorkflow.mutateAsync(body);
        const newId: string = typeof result?.workflowId === "string" ? result.workflowId : "";
        const warnings: string[] = Array.isArray(result?.warnings) ? result.warnings : [];
        const drafts = state.triggerDrafts;
        const listRoute = `/databases/${databaseId}/workflows`;
        const editRoute = `/databases/${databaseId}/workflows/${newId}`;
        const triggerCount = (n: number) => `${n} trigger${n === 1 ? "" : "s"}`;

        if (!newId) {
            // Without the id neither a trigger PUT nor the edit route can be addressed, so the
            // outcome is reported the way a create without drafts always has been.
            dispatch({ type: "SET_BACKEND_WARNINGS", warnings });
            if (drafts.length > 0) {
                toast.warning(`Workflow created; ${triggerCount(drafts.length)} not saved`, {
                    description: "The response did not include the workflow id.",
                });
            }
            if (warnings.length > 0) {
                setSavedWithWarnings(true);
                toast.warning("Workflow created", { description: warnings[0] });
                return;
            }
            toast.success("Workflow created", { description: state.workflowName || undefined });
            navigate(listRoute);
            return;
        }

        seedWorkflowCache(queryClient, databaseId, newId, result);

        const failed: PendingTrigger[] = [];
        for (const draft of drafts) {
            // Only pipelines in the saved body count: one may have been removed after drafting.
            const triggerBody = withCurrentPipelineTemplates(draft, body.specifiedPipelines);
            const [ok, data] = await setTrigger(databaseId, newId, draft.triggerType, triggerBody);
            if (ok) {
                invalidateTriggerQueries(queryClient, databaseId, newId);
            } else {
                failed.push({
                    draft: triggerBody,
                    error: typeof data === "string" ? data : "Failed to set trigger",
                });
            }
        }

        // The hop replaces the create entry in history: the workflow exists now, so Back from the
        // edit route must not land on a blank create form for it.
        if (failed.length > 0) {
            toast.warning(`Workflow created; ${triggerCount(failed.length)} not saved`, {
                description: failed[0].error,
            });
            navigate(editRoute, {
                replace: true,
                state: { step: "triggers", pendingTriggers: failed, backendWarnings: warnings },
            });
            return;
        }
        if (warnings.length > 0) {
            toast.warning("Workflow created", { description: warnings[0] });
            navigate(editRoute, {
                replace: true,
                state: { step: "review", backendWarnings: warnings },
            });
            return;
        }
        // Navigating away removes the form, so the toast carries the confirmation.
        toast.success("Workflow created", {
            description:
                drafts.length > 0
                    ? `${state.workflowName} · ${triggerCount(drafts.length)} saved`
                    : state.workflowName || undefined,
        });
        navigate(listRoute);
    };

    const handleSave = async () => {
        if (!isSystemWorkflow && state.validationErrors.length > 0) return;

        dispatch({ type: "SET_SAVING", saving: true });
        dispatch({ type: "SET_SAVE_ERROR", error: null });
        try {
            const targetWorkflowId = workflowId || state.workflowIdValue;

            if (isSystemWorkflow) {
                await updateWorkflow.mutateAsync({
                    databaseId,
                    workflowId: targetWorkflowId,
                    body: { enabled: state.enabled },
                });
                toast.success("Workflow saved", { description: state.workflowName || undefined });
                navigate(`/databases/${databaseId}/workflows`);
                return;
            }

            const body = assembleWorkflow();

            if (mode === "create") {
                await createWorkflowWithTriggers(body);
                return;
            }

            const result = await updateWorkflow.mutateAsync({
                databaseId,
                // The route param is authoritative; reducer state lags a workflowId change.
                workflowId: targetWorkflowId,
                body: { ...body, workflowId: targetWorkflowId },
            });

            const warnings: string[] = Array.isArray(result?.warnings) ? result.warnings : [];
            dispatch({ type: "SET_BACKEND_WARNINGS", warnings });
            if (warnings.length > 0) {
                // Keep the author on the form so the warnings are read before leaving.
                setSavedWithWarnings(true);
                toast.warning("Workflow saved", { description: warnings[0] });
            } else {
                // Navigating away removes the form, so the toast carries the confirmation.
                toast.success("Workflow saved", { description: state.workflowName || undefined });
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

    const isSaveDisabled = isSystemWorkflow
        ? state.saving
        : state.validationErrors.length > 0 || state.saving;

    // Wizard steps. Each section is one step; Review is last. Triggers are optional in both modes:
    // when editing they are written live; when creating they are drafted and written once the
    // workflow exists, since the trigger endpoint is keyed by a stored workflow.
    const WIZARD_STEPS = [
        { id: "basic", label: "Basic information" },
        { id: "execution", label: "Execution settings" },
        { id: "pipelines", label: "Pipelines" },
        { id: "triggers", label: "Triggers (optional)" },
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
    // definition is a lot to lose to one mis-click next to Back, so confirm while there is
    // anything to lose. After a warned save the entered values are already persisted. Trigger
    // drafts still waiting to be written are not part of the workflow body, so `dirty` does not
    // cover them: they get their own confirmation. A route reached from the create flow's hand-off
    // has no create form behind it to return to, so Cancel leaves to the list.
    const handleCancel = () => {
        const unsaved = pendingTriggers.length;
        if (unsaved > 0) {
            const message = `${unsaved} unsaved trigger${
                unsaved === 1 ? "" : "s"
            } will be discarded. Leave anyway?`;
            if (!confirm(message)) return;
        } else if (
            state.dirty &&
            !savedWithWarnings &&
            !confirm("Discard this workflow and leave without saving?")
        ) {
            return;
        }
        if (hopLandedRef.current) {
            navigate(`/databases/${databaseId}/workflows`);
        } else {
            navigate(-1);
        }
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

            {isSystemWorkflow && (
                <div className="p-3 bg-blue-100 dark:bg-blue-900/20 text-blue-800 dark:text-blue-300 rounded">
                    <strong>System workflow:</strong> shipped with the deployment and read-only
                    here. Only <em>Enabled</em> can be changed, and a redeploy re-asserts the
                    shipped values.
                </div>
            )}

            <Stepper steps={WIZARD_STEPS} current={wizardStep} />

            {wizardStep === "basic" && (
                <div className="space-y-4">
                    <fieldset
                        disabled={isSystemWorkflow}
                        className="m-0 p-0 border-0 min-w-0 space-y-4"
                    >
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
                    </fieldset>
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
                <fieldset
                    disabled={isSystemWorkflow}
                    className="m-0 p-0 border-0 min-w-0 space-y-4"
                >
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
                </fieldset>
            )}

            {wizardStep === "pipelines" && (
                <div className="space-y-4">
                    <fieldset
                        disabled={isSystemWorkflow}
                        className="m-0 p-0 border-0 min-w-0 space-y-4"
                    >
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
                    </fieldset>
                </div>
            )}

            {wizardStep === "triggers" && mode === "edit" && (
                <TriggersEditor
                    databaseId={databaseId}
                    workflowId={workflowId || ""}
                    pipelineRefs={state.specifiedPipelines}
                    systemLocked={isSystemWorkflow}
                    pendingTriggers={pendingTriggers}
                    onPendingTriggersChange={setPendingTriggers}
                    openFirstPending={openPendingTrigger}
                    onPendingOpened={handlePendingOpened}
                />
            )}

            {wizardStep === "triggers" &&
                mode === "create" &&
                (permissionsLoading ? (
                    <div
                        data-testid="triggers-permission-skeleton"
                        aria-busy="true"
                        className="orch-outline border border-border-default rounded p-6 bg-surface-container space-y-3"
                    >
                        <h2 className="text-xl font-semibold text-text-primary">Triggers</h2>
                        <div className="h-4 w-1/2 rounded bg-surface-secondary animate-pulse" />
                        <div className="h-4 w-1/3 rounded bg-surface-secondary animate-pulse" />
                    </div>
                ) : canSetTriggers ? (
                    <TriggerDraftsEditor
                        drafts={state.triggerDrafts}
                        onChange={(drafts) => dispatch({ type: "SET_TRIGGER_DRAFTS", drafts })}
                        pipelineRefs={state.specifiedPipelines}
                    />
                ) : (
                    <div className="orch-outline border border-border-default rounded p-6 bg-surface-container space-y-2">
                        <h2 className="text-xl font-semibold text-text-primary">Triggers</h2>
                        <p className="text-sm text-text-secondary">
                            Your role can create workflows but cannot set triggers; a workflow
                            administrator can add them later.
                        </p>
                    </div>
                ))}

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
                                <span className="text-text-secondary">Database:</span>{" "}
                                {databaseId === "GLOBAL" ? "🌐 GLOBAL" : databaseId}
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
                            {mode === "create" && (
                                <div>
                                    <span className="text-text-secondary">Triggers:</span>{" "}
                                    {state.triggerDrafts.length}
                                </div>
                            )}
                        </div>
                        {mode === "create" && state.triggerDrafts.length > 0 && (
                            <ul
                                aria-label="Trigger drafts"
                                className="text-sm text-text-primary list-disc list-inside"
                            >
                                {state.triggerDrafts.map((draft) => {
                                    const allow = draft.inputFileFilters?.allow || [];
                                    const exclude = draft.inputFileFilters?.exclude || [];
                                    // Counted from what Save sends: a template chosen for a
                                    // pipeline since removed from the workflow is not in the body.
                                    const templates = Object.values(
                                        withCurrentPipelineTemplates(
                                            draft,
                                            state.specifiedPipelines
                                        ).defaultTemplateIds || {}
                                    ).filter(Boolean).length;
                                    return (
                                        <li key={draft.triggerType}>
                                            {draft.triggerType} —{" "}
                                            {draft.enabled ? "enabled" : "disabled"}, {allow.length}{" "}
                                            allow / {exclude.length} exclude, {templates} default
                                            template{templates === 1 ? "" : "s"}
                                        </li>
                                    );
                                })}
                            </ul>
                        )}
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
