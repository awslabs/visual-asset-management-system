/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useMemo, useState } from "react";
import { type ColumnDef } from "@tanstack/react-table";
import DataTable from "../../../features/orchestration/components/DataTable";
import Dialog from "../../../features/orchestration/components/Dialog";
import ContextMenu, {
    type ContextMenuItem,
} from "../../../features/orchestration/components/ContextMenu";
import RefreshButton from "../../../features/orchestration/components/RefreshButton";
import StatusBadge from "../../../features/orchestration/components/StatusBadge";
import {
    btnPrimary,
    btnSecondary,
    control,
} from "../../../features/orchestration/components/controlStyles";
import {
    useToast,
    toastErrorMessage,
} from "../../../features/orchestration/components/ToastProvider";
import { useAllowedRoutes } from "../../../features/orchestration/permissions/useAllowedRoutes";
import type { ExecutionStatus } from "../../../features/orchestration/types";
import {
    useContainerCatalog,
    useContainerJobMutations,
    useContainerJobs,
} from "../api/containerQueries";
import { unconfirmedNotice } from "../api/outcome";
import ConfirmActionDialog from "../admin/ConfirmActionDialog";
import { useInFlight } from "../components/useInFlight";
import {
    CONTAINERS_CATALOG_ROUTE,
    CONTAINERS_JOB_ROUTE,
    CONTAINERS_JOBS_ROUTE,
} from "./containerRoutes";
import type {
    EdhContainerBackend,
    EdhContainerCatalogItem,
    EdhContainerJob,
    EdhContainerScope,
    SubmitContainerJobRequest,
} from "../types";

export { CONTAINERS_CATALOG_ROUTE, CONTAINERS_JOB_ROUTE, CONTAINERS_JOBS_ROUTE };

/** The record field that names a catalog item, per kind. */
const CATALOG_NAME_FIELD: Record<EdhContainerCatalogItem["kind"], string> = {
    jobDefinition: "jobDefinitionName",
    jobQueue: "jobQueueName",
    eksCluster: "name",
};

/** The backend options: eks appears only when the deployment accepts it, and is experimental. */
export const BACKEND_LABELS: Record<EdhContainerBackend, string> = {
    batch: "AWS Batch",
    eks: "Amazon EKS (experimental)",
};

/**
 * The status pill for a job: AWS Batch's states and the words the service derives for an EKS job
 * mapped onto the execution badge's states. Queued covers everything before a container starts.
 */
export const JOB_STATUS_BADGE: Record<string, ExecutionStatus> = {
    SUBMITTED: "NEW",
    PENDING: "NEW",
    RUNNABLE: "NEW",
    STARTING: "RUNNING",
    RUNNING: "RUNNING",
    SUCCEEDED: "SUCCEEDED",
    FAILED: "FAILED",
};

/** A job that has finished; there is nothing left to kill. */
export const TERMINAL_JOB_STATUSES: ReadonlySet<string> = new Set(["SUCCEEDED", "FAILED"]);

export const isTerminalJobStatus = (status: string) => TERMINAL_JOB_STATUSES.has(status);

export function catalogItemName(item: EdhContainerCatalogItem): string {
    const value = item[CATALOG_NAME_FIELD[item.kind]];
    return typeof value === "string" ? value : "";
}

const text = (value: unknown, fallback = "—"): string =>
    typeof value === "string" && value !== "" ? value : fallback;

/** An integer at or above `min` from a number input, nothing when the field is blank, or the message to show. */
const parseOptionalInt = (
    label: string,
    raw: string,
    min: number
): { value?: number; error?: string } => {
    if (raw.trim() === "") return {};
    const value = Number(raw);
    if (!Number.isInteger(value) || value < min) {
        return { error: `${label} must be a whole number of at least ${min}.` };
    }
    return { value };
};

// The floors of the resource overrides: what the inputs declare and the request model accepts.
const VCPUS_MIN = 1;
const MEMORY_MIB_MIN = 512;
const GPU_MIN = 0;

/** Job names as each backend accepts them, and the rule shown for a name outside one. */
const JOB_NAME_RULES: Record<EdhContainerBackend, { pattern: RegExp; message: string }> = {
    batch: {
        pattern: /^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$/,
        message:
            "Job name must be 1-128 letters, digits, hyphens or underscores, starting with a letter or digit.",
    },
    eks: {
        pattern: /^[a-z0-9]([-a-z0-9]{0,61}[a-z0-9])?$/,
        message:
            "Job name must be 1-63 lower-case letters, digits or hyphens, starting and ending with a letter or digit.",
    },
};

interface SubmitJobDialogProps {
    backend: EdhContainerBackend;
    definitions: string[];
    queues: string[];
    defaultQueue?: string;
    onClose: () => void;
    onSubmit: (body: SubmitContainerJobRequest) => Promise<void>;
    busy: boolean;
}

const SubmitJobDialog: React.FC<SubmitJobDialogProps> = ({
    backend,
    definitions,
    queues,
    defaultQueue,
    onClose,
    onSubmit,
    busy,
}) => {
    const [jobDefinition, setJobDefinition] = useState(definitions[0] ?? "");
    const [jobQueue, setJobQueue] = useState(defaultQueue ?? queues[0] ?? "");
    const [name, setName] = useState("");
    const [command, setCommand] = useState("");
    const [vcpus, setVcpus] = useState("");
    const [memory, setMemory] = useState("");
    const [gpu, setGpu] = useState("");
    const [error, setError] = useState<string | null>(null);

    const isEks = backend === "eks";
    const definitionLabel = isEks ? "Image URI" : "Job definition";
    const queueLabel = isEks ? "EKS cluster" : "Job queue";

    const handleSubmit = async () => {
        setError(null);
        if (!jobDefinition.trim() || !name.trim()) {
            setError(`${definitionLabel} and job name are required.`);
            return;
        }
        if (!jobQueue) {
            setError(`${queueLabel} is required.`);
            return;
        }
        if (!JOB_NAME_RULES[backend].pattern.test(name.trim())) {
            setError(JOB_NAME_RULES[backend].message);
            return;
        }
        const resources = {
            vcpus: parseOptionalInt("vCPUs", vcpus, VCPUS_MIN),
            memory: parseOptionalInt("Memory (MiB)", memory, MEMORY_MIB_MIN),
            gpu: parseOptionalInt("GPUs", gpu, GPU_MIN),
        };
        const resourceError = Object.values(resources).find((r) => r.error)?.error;
        if (resourceError) {
            setError(resourceError);
            return;
        }
        const words = command
            .trim()
            .split(/\s+/)
            .filter((w) => w !== "");
        const body: SubmitContainerJobRequest = {
            jobDefinition: jobDefinition.trim(),
            jobQueue,
            name: name.trim(),
            backend,
            ...(words.length > 0 ? { command: words } : {}),
            ...(resources.vcpus.value !== undefined ? { vcpus: resources.vcpus.value } : {}),
            ...(resources.memory.value !== undefined ? { memory: resources.memory.value } : {}),
            ...(resources.gpu.value !== undefined ? { gpu: resources.gpu.value } : {}),
        };
        try {
            await onSubmit(body);
        } catch (err) {
            setError(toastErrorMessage(err));
        }
    };

    const field = "flex flex-col gap-1 text-sm";

    return (
        <Dialog
            open={true}
            onOpenChange={(open) => !open && onClose()}
            title="Submit container job"
            footer={
                <>
                    <button className={btnSecondary} onClick={onClose} disabled={busy}>
                        Cancel
                    </button>
                    <button className={btnPrimary} onClick={handleSubmit} disabled={busy}>
                        Submit
                    </button>
                </>
            }
        >
            <div className="grid gap-3">
                <div className={field}>
                    <label htmlFor="edh-job-definition">{definitionLabel}</label>
                    {isEks ? (
                        <input
                            id="edh-job-definition"
                            className={control}
                            value={jobDefinition}
                            onChange={(e) => setJobDefinition(e.target.value)}
                            placeholder="123456789012.dkr.ecr.<region>.amazonaws.com/image:tag"
                        />
                    ) : (
                        <select
                            id="edh-job-definition"
                            className={control}
                            value={jobDefinition}
                            onChange={(e) => setJobDefinition(e.target.value)}
                        >
                            {definitions.map((d) => (
                                <option key={d} value={d}>
                                    {d}
                                </option>
                            ))}
                        </select>
                    )}
                </div>
                <div className={field}>
                    <label htmlFor="edh-job-queue">{queueLabel}</label>
                    <select
                        id="edh-job-queue"
                        className={control}
                        value={jobQueue}
                        onChange={(e) => setJobQueue(e.target.value)}
                    >
                        {queues.length === 0 && <option value="">None available</option>}
                        {queues.map((q) => (
                            <option key={q} value={q}>
                                {q}
                            </option>
                        ))}
                    </select>
                </div>
                <div className={field}>
                    <label htmlFor="edh-job-name">Job name</label>
                    <input
                        id="edh-job-name"
                        className={control}
                        value={name}
                        onChange={(e) => setName(e.target.value)}
                        placeholder={
                            isEks
                                ? "lower-case letters, digits, hyphens"
                                : "letters, digits, hyphens, underscores"
                        }
                    />
                </div>
                <div className={field}>
                    <label htmlFor="edh-job-command">Command</label>
                    <input
                        id="edh-job-command"
                        className={control}
                        value={command}
                        onChange={(e) => setCommand(e.target.value)}
                        placeholder="Optional; split on whitespace"
                    />
                </div>
                <div className="grid grid-cols-3 gap-3">
                    <div className={field}>
                        <label htmlFor="edh-job-vcpus">vCPUs</label>
                        <input
                            id="edh-job-vcpus"
                            className={control}
                            type="number"
                            min={VCPUS_MIN}
                            value={vcpus}
                            onChange={(e) => setVcpus(e.target.value)}
                        />
                    </div>
                    <div className={field}>
                        <label htmlFor="edh-job-memory">Memory (MiB)</label>
                        <input
                            id="edh-job-memory"
                            className={control}
                            type="number"
                            min={MEMORY_MIB_MIN}
                            step={512}
                            value={memory}
                            onChange={(e) => setMemory(e.target.value)}
                        />
                    </div>
                    <div className={field}>
                        <label htmlFor="edh-job-gpu">GPUs</label>
                        <input
                            id="edh-job-gpu"
                            className={control}
                            type="number"
                            min={GPU_MIN}
                            value={gpu}
                            onChange={(e) => setGpu(e.target.value)}
                        />
                    </div>
                </div>
                {error && (
                    <div
                        role="alert"
                        className="p-2 rounded bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 text-sm"
                    >
                        {error}
                    </div>
                )}
            </div>
        </Dialog>
    );
};

/** The container-jobs panel of the Workstations page; the page supplies the heading and the tab. */
const SimulationsTab: React.FC = () => {
    const toast = useToast();
    const { loading: permissionsLoading, can } = useAllowedRoutes();
    const [backend, setBackend] = useState<EdhContainerBackend>("batch");
    const [cluster, setCluster] = useState("");
    const canList = can("GET", CONTAINERS_JOBS_ROUTE);
    const catalog = useContainerCatalog(can("GET", CONTAINERS_CATALOG_ROUTE));
    // An eks listing is per cluster, so nothing is asked for until one is chosen.
    const scope: EdhContainerScope | undefined =
        backend === "eks" && cluster ? { cluster } : undefined;
    const listEnabled = canList && (backend === "batch" || !!scope);
    const jobsQuery = useContainerJobs(backend, listEnabled, scope);
    const { submitJob, killJob } = useContainerJobMutations();
    const { claim, release } = useInFlight();
    const [submitOpen, setSubmitOpen] = useState(false);
    const [killTarget, setKillTarget] = useState<EdhContainerJob | null>(null);
    const [actionError, setActionError] = useState<string | null>(null);

    const canSubmit = can("POST", CONTAINERS_JOBS_ROUTE);
    const canKill = can("DELETE", CONTAINERS_JOB_ROUTE);
    const eksEnabled = catalog.data?.eksEnabled ?? false;

    const { definitions, queues, clusters } = useMemo(() => {
        const items: EdhContainerCatalogItem[] = catalog.data?.Items ?? [];
        // A job definition is listed once per revision; the submit names it once.
        const names = (kind: EdhContainerCatalogItem["kind"]) =>
            Array.from(
                new Set(
                    items
                        .filter((i) => i.kind === kind)
                        .map(catalogItemName)
                        .filter((n) => n !== "")
                )
            );
        const clusterNames = names("eksCluster");
        return backend === "eks"
            ? { definitions: [], queues: clusterNames, clusters: clusterNames }
            : {
                  definitions: names("jobDefinition"),
                  queues: names("jobQueue"),
                  clusters: clusterNames,
              };
    }, [catalog.data, backend]);

    const rows = jobsQuery.data ?? [];

    const changeBackend = (next: EdhContainerBackend) => {
        setBackend(next);
        setCluster("");
    };

    const handleKill = async (job: EdhContainerJob) => {
        const key = `${backend}/${job.jobId}`;
        if (!claim(key)) return;
        setActionError(null);
        try {
            await killJob.mutateAsync({ jobId: job.jobId, backend, ...(scope ? { scope } : {}) });
            setKillTarget(null);
            toast.success("Job kill requested", { description: job.jobName });
        } catch (err) {
            setKillTarget(null);
            const unconfirmed = unconfirmedNotice("Kill", err);
            if (unconfirmed) {
                toast.warning(unconfirmed.title, {
                    description: `${job.jobName}: ${unconfirmed.message}`,
                });
                return;
            }
            toast.error("Kill failed", {
                description: `${job.jobName}: ${toastErrorMessage(err)}`,
            });
            setActionError(`Failed to kill ${job.jobName}: ${toastErrorMessage(err)}`);
        } finally {
            release(key);
        }
    };

    // Stable across a poll: a fresh `cell` function would remount the cell and close an open row
    // menu. The action cell reaches only `setKillTarget`, which never changes identity.
    const columns = useMemo<ColumnDef<EdhContainerJob>[]>(
        () => [
            {
                id: "name",
                header: "Name",
                cell: ({ row }) => (
                    <span className="font-semibold text-text-primary">
                        {text(row.original.jobName)}
                    </span>
                ),
            },
            {
                id: "jobId",
                header: "Job id",
                cell: ({ row }) => (
                    <span className="font-mono text-xs">{text(row.original.jobId)}</span>
                ),
            },
            {
                id: "definition",
                header: "Definition",
                cell: ({ row }) => <span>{text(row.original.edhJobDefinition)}</span>,
            },
            {
                id: "status",
                header: "Status",
                cell: ({ row }) => (
                    // An unmapped status renders as a neutral badge carrying the raw value.
                    <span title={row.original.status || undefined}>
                        <StatusBadge
                            status={
                                JOB_STATUS_BADGE[row.original.status] ??
                                (row.original.status as ExecutionStatus)
                            }
                        />
                    </span>
                ),
            },
            ...(canKill
                ? ([
                      {
                          id: "actions",
                          header: "Actions",
                          cell: ({ row }) => {
                              const job = row.original;
                              // Kill is destructive and the only action; a finished job, or a
                              // listing without an id to address, offers it disabled.
                              const items: ContextMenuItem[] = [
                                  {
                                      label: "Kill",
                                      danger: true,
                                      onSelect: () => setKillTarget(job),
                                      disabled: job.jobId === "" || isTerminalJobStatus(job.status),
                                  },
                              ];
                              return (
                                  <ContextMenu
                                      items={items}
                                      trigger={
                                          <button
                                              aria-label={`Actions for ${text(job.jobName)}`}
                                              className="bg-transparent border-0 px-2 py-1 rounded text-lg leading-none text-text-secondary hover:text-gray-700 hover:bg-gray-100 dark:hover:text-gray-300 dark:hover:bg-gray-700 cursor-pointer"
                                          >
                                              ⋮
                                          </button>
                                      }
                                  />
                              );
                          },
                      },
                  ] as ColumnDef<EdhContainerJob>[])
                : []),
        ],
        [canKill]
    );

    // Gate the panel's contents, not the tab: a caller who may not list jobs is told so rather than
    // shown a live-looking, empty list.
    if (!permissionsLoading && !canList) {
        return (
            <div
                role="status"
                className="p-4 rounded bg-surface-container border border-border-default text-text-primary"
            >
                You do not have access to container jobs.
            </div>
        );
    }

    const loading = (listEnabled && jobsQuery.isLoading) || permissionsLoading;

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between gap-2 flex-wrap">
                <div className="flex items-center gap-3 flex-wrap">
                    <div className="flex items-center gap-2 text-sm">
                        <label htmlFor="edh-container-backend">Backend</label>
                        <select
                            id="edh-container-backend"
                            className={control}
                            value={backend}
                            onChange={(e) => changeBackend(e.target.value as EdhContainerBackend)}
                        >
                            <option value="batch">{BACKEND_LABELS.batch}</option>
                            {eksEnabled && <option value="eks">{BACKEND_LABELS.eks}</option>}
                        </select>
                    </div>
                    {backend === "eks" && (
                        <div className="flex items-center gap-2 text-sm">
                            <label htmlFor="edh-container-cluster">Cluster</label>
                            <select
                                id="edh-container-cluster"
                                className={control}
                                value={cluster}
                                onChange={(e) => setCluster(e.target.value)}
                            >
                                <option value="">Choose a cluster</option>
                                {clusters.map((c) => (
                                    <option key={c} value={c}>
                                        {c}
                                    </option>
                                ))}
                            </select>
                        </div>
                    )}
                    <RefreshButton
                        onClick={() => jobsQuery.refetch()}
                        busy={jobsQuery.isFetching}
                    />
                    <span className="text-sm text-text-secondary">
                        Your jobs refresh every 15 seconds.
                    </span>
                </div>
                {canSubmit && (
                    <button onClick={() => setSubmitOpen(true)} className={btnPrimary}>
                        Submit Job
                    </button>
                )}
            </div>

            {actionError && (
                <div
                    role="alert"
                    className="p-3 bg-red-100 dark:bg-red-900/20 text-red-700 dark:text-red-400 rounded flex items-start justify-between gap-3"
                >
                    <span>{actionError}</span>
                    <button
                        onClick={() => setActionError(null)}
                        aria-label="Dismiss action error"
                        className="bg-transparent border-0 text-lg leading-none cursor-pointer"
                    >
                        ×
                    </button>
                </div>
            )}

            {backend === "eks" && !scope ? (
                <div role="status" className="p-8 text-center text-text-secondary">
                    Choose a cluster to list its jobs.
                </div>
            ) : loading ? (
                <div role="status" className="p-8 text-center text-text-secondary">
                    Loading container jobs...
                </div>
            ) : jobsQuery.error ? (
                <div role="alert" className="p-4 text-vams-error">
                    Error loading container jobs: {toastErrorMessage(jobsQuery.error)}
                </div>
            ) : rows.length === 0 ? (
                <div className="p-8 text-center text-text-secondary">No container jobs found.</div>
            ) : (
                <DataTable
                    columns={columns}
                    rows={rows}
                    ariaLabel="Container jobs"
                    // The list re-reads on a cadence, so rows are keyed by their own identity.
                    getRowId={(row, index) => row.jobId || String(index)}
                    filtering={false}
                />
            )}

            {submitOpen && (
                <SubmitJobDialog
                    backend={backend}
                    definitions={definitions}
                    queues={queues}
                    defaultQueue={backend === "eks" ? cluster || undefined : undefined}
                    busy={submitJob.isPending}
                    onClose={() => setSubmitOpen(false)}
                    onSubmit={async (body) => {
                        if (!claim("submit")) return;
                        try {
                            await submitJob.mutateAsync(body);
                        } catch (err) {
                            const unconfirmed = unconfirmedNotice("Submission", err);
                            // Any other failure stays in the dialog, beside the fields to correct.
                            if (!unconfirmed) throw err;
                            // The job may be running already, so the dialog closes rather than
                            // invite a second submit.
                            setSubmitOpen(false);
                            toast.warning(unconfirmed.title, {
                                description: `${body.name}: ${unconfirmed.message}`,
                            });
                            return;
                        } finally {
                            release("submit");
                        }
                        setSubmitOpen(false);
                        toast.success("Job submitted", { description: body.name });
                    }}
                />
            )}

            {killTarget && (
                <ConfirmActionDialog
                    open
                    title="Kill container job"
                    confirmLabel="Kill"
                    busy={killJob.isPending}
                    onCancel={() => setKillTarget(null)}
                    onConfirm={() => handleKill(killTarget)}
                    message={
                        <p>
                            Kill <strong>{text(killTarget.jobName)}</strong>? The job stops and
                            cannot be resumed.
                        </p>
                    }
                />
            )}
        </div>
    );
};

export default SimulationsTab;
