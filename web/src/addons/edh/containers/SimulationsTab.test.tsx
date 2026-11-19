/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import SimulationsTab, {
    BACKEND_LABELS,
    CONTAINERS_CATALOG_ROUTE,
    CONTAINERS_JOBS_ROUTE,
    CONTAINERS_JOB_ROUTE,
    JOB_STATUS_BADGE,
    catalogItemName,
    isTerminalJobStatus,
} from "./SimulationsTab";
import * as containerQueries from "../api/containerQueries";
import * as useAllowedRoutesModule from "../../../features/orchestration/permissions/useAllowedRoutes";
import { EdhRequestError } from "../api/outcome";
import type { EdhContainerJob } from "../types";
import { expectHeldOpen } from "../components/heldDialogTestSupport";

jest.mock("../api/containerQueries", () => ({
    useContainerCatalog: jest.fn(),
    useContainerJobs: jest.fn(),
    useContainerJobMutations: jest.fn(),
}));

jest.mock("../../../features/orchestration/permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

const mockToast = { success: jest.fn(), warning: jest.fn(), info: jest.fn(), error: jest.fn() };
jest.mock("../../../features/orchestration/components/ToastProvider", () => ({
    ...jest.requireActual("../../../features/orchestration/components/ToastProvider"),
    useToast: () => mockToast,
}));

const UNCONFIRMED =
    "EDH did not confirm the request in time; it may still be in progress. Refresh before trying again.";
const unknown502 = () =>
    new EdhRequestError(UNCONFIRMED, 502, { message: UNCONFIRMED, outcomeUnknown: true });
// A request that stays in flight for the rest of the test.
const neverAnswers = () => new Promise<never>(() => undefined);

// Both clicks land before a re-render, as a double-click's do.
const doubleClick = (element: HTMLElement) => {
    fireEvent.click(element);
    fireEvent.click(element);
};

// The add-on's answer to a submit EDH accepted without a readable reply.
const JOB_SUBMITTED =
    "EDH accepted the request but returned an unreadable answer. Check the job list before retrying.";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

const catalog = (eksEnabled: boolean, withQueues = true) => ({
    data: {
        Items: [
            { kind: "jobDefinition", jobDefinitionName: "sim-train", revision: 1 },
            { kind: "jobDefinition", jobDefinitionName: "sim-train", revision: 2 },
            { kind: "jobDefinition", jobDefinitionName: "sim-infer", revision: 1 },
            ...(withQueues ? [{ kind: "jobQueue", jobQueueName: "gpu-queue" }] : []),
            ...(eksEnabled ? [{ kind: "eksCluster", name: "edh-eks-a" }] : []),
        ],
        eksEnabled,
    },
    isLoading: false,
    error: null,
});

const runningJob: EdhContainerJob = {
    backend: "batch",
    jobId: "j-1",
    jobName: "render-1",
    status: "RUNNING",
    edhJobDefinition: "sim-train",
    jobHandle: "batch:j-1",
};

const finishedJob: EdhContainerJob = {
    ...runningJob,
    jobId: "j-2",
    jobName: "render-2",
    status: "SUCCEEDED",
};

const failedJob: EdhContainerJob = {
    ...runningJob,
    jobId: "j-3",
    jobName: "render-3",
    status: "FAILED",
};

const queuedJob: EdhContainerJob = {
    ...runningJob,
    jobId: "j-4",
    jobName: "render-4",
    status: "RUNNABLE",
};

const jobs = (rows: EdhContainerJob[]) => ({
    data: rows,
    isLoading: false,
    error: null,
    refetch: jest.fn(),
    isFetching: false,
});

const mutations = () => ({
    submitJob: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
    killJob: { mutateAsync: jest.fn().mockResolvedValue({}), isPending: false },
});

const allow = (can: (method: string, path: string) => boolean, loading = false) =>
    (useAllowedRoutesModule.useAllowedRoutes as jest.Mock).mockReturnValue({ loading, can });

// A fresh element per render: React bails out of re-rendering an element it is handed twice.
const tab = () => (
    <QueryClientProvider client={queryClient}>
        <SimulationsTab />
    </QueryClientProvider>
);

const renderTab = () => render(tab());

/**
 * Holds `held`'s next request in flight, reading as pending, until the returned function answers
 * it.
 */
const holdRequest = (held: { mutateAsync: jest.Mock; isPending: boolean }) => {
    let answer = () => undefined as void;
    held.mutateAsync = jest.fn(() => {
        held.isPending = true;
        return new Promise((resolve) => {
            answer = () => {
                held.isPending = false;
                resolve({});
            };
        });
    });
    return () => act(async () => answer());
};

/**
 * Presses Enter on the dialog's `label` button and checks the busy window: the button reads
 * `busyLabel` and keeps focus (aria-disabled, not disabled), and a second Enter, Escape and Cancel
 * leave the request and the dialog as they are.
 */
const pressAndHold = async (
    user: ReturnType<typeof userEvent.setup>,
    dialog: HTMLElement,
    label: string,
    busyLabel: string
) => {
    within(dialog).getByRole("button", { name: label }).focus();
    await user.keyboard("{Enter}");
    const busy = within(dialog).getByRole("button", { name: busyLabel });
    expect(busy).toHaveAttribute("aria-disabled", "true");
    expect(busy).not.toBeDisabled();
    expect(busy).toHaveFocus();
    await user.keyboard("{Enter}");
    await user.keyboard("{Escape}");
    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(screen.getByRole("dialog")).toBe(dialog);
};

async function openActionsFor(user: ReturnType<typeof userEvent.setup>, name: string) {
    // The kebab menu is a Radix dropdown — it opens on keyboard activation in jsdom.
    screen.getByLabelText(`Actions for ${name}`).focus();
    await user.keyboard("{Enter}");
    return screen.findAllByRole("menuitem");
}

describe("SimulationsTab", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (containerQueries.useContainerCatalog as jest.Mock).mockReturnValue(catalog(false));
        (containerQueries.useContainerJobs as jest.Mock).mockReturnValue(jobs([runningJob]));
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(mutations());
        allow(() => true);
    });

    it("exposes the route templates addonApiRoutes.py declares, verbatim, for can()", () => {
        expect(CONTAINERS_CATALOG_ROUTE).toBe("/addon/edh/containers/catalog");
        expect(CONTAINERS_JOBS_ROUTE).toBe("/addon/edh/containers/jobs");
        expect(CONTAINERS_JOB_ROUTE).toBe("/addon/edh/containers/jobs/{jobId}");
    });

    it("reads a catalog record's name by its kind", () => {
        expect(catalogItemName({ kind: "jobDefinition", jobDefinitionName: "sim-train" })).toBe(
            "sim-train"
        );
        expect(catalogItemName({ kind: "jobQueue", jobQueueName: "gpu-queue" })).toBe("gpu-queue");
        expect(catalogItemName({ kind: "eksCluster", name: "edh-eks-a" })).toBe("edh-eks-a");
        expect(catalogItemName({ kind: "jobQueue" })).toBe("");
    });

    it("maps the Batch and EKS job states onto the execution badge and knows which are final", () => {
        expect(JOB_STATUS_BADGE).toEqual({
            SUBMITTED: "NEW",
            PENDING: "NEW",
            RUNNABLE: "NEW",
            STARTING: "RUNNING",
            RUNNING: "RUNNING",
            SUCCEEDED: "SUCCEEDED",
            FAILED: "FAILED",
        });
        expect(isTerminalJobStatus("SUCCEEDED")).toBe(true);
        expect(isTerminalJobStatus("FAILED")).toBe(true);
        expect(isTerminalJobStatus("RUNNING")).toBe(false);
        expect(isTerminalJobStatus("PENDING")).toBe(false);
    });

    it("lists the caller's jobs with their id, status pill and definition, and no heading of its own", () => {
        renderTab();
        expect(screen.queryByRole("heading")).not.toBeInTheDocument();
        const table = screen.getByRole("table", { name: "Container jobs" });
        expect(within(table).getByText("render-1")).toBeInTheDocument();
        expect(within(table).getByText("j-1")).toBeInTheDocument();
        expect(within(table).getByText("Running")).toBeInTheDocument();
        expect(within(table).queryByText("RUNNING")).not.toBeInTheDocument();
        expect(within(table).getByText("sim-train")).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Submit Job" })).toBeInTheDocument();
        expect(containerQueries.useContainerJobs).toHaveBeenCalledWith("batch", true, undefined);
    });

    it("renders each job state as the matching pill, with the raw state as its title", () => {
        (containerQueries.useContainerJobs as jest.Mock).mockReturnValue(
            jobs([runningJob, finishedJob, failedJob, queuedJob])
        );
        renderTab();
        const table = screen.getByRole("table", { name: "Container jobs" });
        expect(within(table).getByText("Running")).toBeInTheDocument();
        expect(within(table).getByText("Succeeded")).toBeInTheDocument();
        expect(within(table).getByText("Failed")).toBeInTheDocument();
        expect(within(table).getByText("Queued")).toBeInTheDocument();
        expect(within(table).getByTitle("RUNNABLE")).toBeInTheDocument();
    });

    it("shows the empty state, not an empty table, when there are no jobs", () => {
        (containerQueries.useContainerJobs as jest.Mock).mockReturnValue(jobs([]));
        renderTab();
        expect(screen.getByText("No container jobs found.")).toBeInTheDocument();
        expect(screen.queryByRole("table")).not.toBeInTheDocument();
    });

    it("names the backends by product, offers EKS as experimental only when enabled, and lists eks jobs per cluster", async () => {
        const user = userEvent.setup();
        const { unmount } = renderTab();
        expect(screen.getByRole("option", { name: "AWS Batch" })).toBeInTheDocument();
        expect(
            screen.queryByRole("option", { name: "Amazon EKS (experimental)" })
        ).not.toBeInTheDocument();
        expect(BACKEND_LABELS).toEqual({ batch: "AWS Batch", eks: "Amazon EKS (experimental)" });
        unmount();

        (containerQueries.useContainerCatalog as jest.Mock).mockReturnValue(catalog(true));
        renderTab();
        await user.selectOptions(screen.getByLabelText("Backend"), "eks");
        expect(screen.getByText("Choose a cluster to list its jobs.")).toBeInTheDocument();
        expect(containerQueries.useContainerJobs).toHaveBeenLastCalledWith("eks", false, undefined);

        await user.selectOptions(screen.getByLabelText("Cluster"), "edh-eks-a");
        expect(containerQueries.useContainerJobs).toHaveBeenLastCalledWith("eks", true, {
            cluster: "edh-eks-a",
        });
    });

    it("submits an eks job as an image URI on the chosen cluster", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        (containerQueries.useContainerCatalog as jest.Mock).mockReturnValue(catalog(true));
        renderTab();
        await user.selectOptions(screen.getByLabelText("Backend"), "eks");
        await user.selectOptions(screen.getByLabelText("Cluster"), "edh-eks-a");

        await user.click(screen.getByRole("button", { name: "Submit Job" }));
        const dialog = screen.getByRole("dialog");
        // The definition is a free-text image URI, and the cluster stands in for the queue.
        const uri = within(dialog).getByLabelText("Image URI");
        expect(uri.tagName).toBe("INPUT");
        expect(uri).toHaveAttribute("placeholder", expect.not.stringContaining("us-east-1"));
        expect(uri).toHaveAttribute("placeholder", expect.stringContaining("<region>"));
        expect(within(dialog).queryByLabelText("Job definition")).not.toBeInTheDocument();
        expect(within(dialog).getByLabelText("EKS cluster")).toHaveValue("edh-eks-a");
        await user.type(uri, "123456789012.dkr.ecr.eu-central-1.amazonaws.com/sim:1");
        await user.type(within(dialog).getByLabelText("Job name"), "render-eks");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));

        await waitFor(() =>
            expect(m.submitJob.mutateAsync).toHaveBeenCalledWith({
                jobDefinition: "123456789012.dkr.ecr.eu-central-1.amazonaws.com/sim:1",
                jobQueue: "edh-eks-a",
                name: "render-eks",
                backend: "eks",
            })
        );
    });

    it("kills an eks job with its cluster scope", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        (containerQueries.useContainerCatalog as jest.Mock).mockReturnValue(catalog(true));
        (containerQueries.useContainerJobs as jest.Mock).mockReturnValue(
            jobs([
                {
                    ...runningJob,
                    backend: "eks",
                    jobId: "render-eks",
                    jobName: "render-eks",
                    jobHandle: "eks:edh-eks-a/default/render-eks",
                    cluster: "edh-eks-a",
                },
            ])
        );
        renderTab();
        await user.selectOptions(screen.getByLabelText("Backend"), "eks");
        await user.selectOptions(screen.getByLabelText("Cluster"), "edh-eks-a");

        const items = await openActionsFor(user, "render-eks");
        await user.click(items[0]);
        await user.click(screen.getByRole("button", { name: "Kill" }));
        await waitFor(() =>
            expect(m.killJob.mutateAsync).toHaveBeenCalledWith({
                jobId: "render-eks",
                backend: "eks",
                scope: { cluster: "edh-eks-a" },
            })
        );

        // Back on Batch the cluster is forgotten, so the listing carries no scope.
        await user.selectOptions(screen.getByLabelText("Backend"), "batch");
        expect(containerQueries.useContainerJobs).toHaveBeenLastCalledWith(
            "batch",
            true,
            undefined
        );
    });

    it("hides Submit Job when the caller may not POST the jobs route", () => {
        allow((method, path) => !(method === "POST" && path === CONTAINERS_JOBS_ROUTE));
        renderTab();
        expect(screen.queryByRole("button", { name: "Submit Job" })).not.toBeInTheDocument();
        expect(screen.getByText("render-1")).toBeInTheDocument();
    });

    it("tells a caller who may not list jobs so, instead of a live-looking empty list", () => {
        allow((method, path) => !(method === "GET" && path === CONTAINERS_JOBS_ROUTE));
        renderTab();
        expect(screen.getByRole("status")).toHaveTextContent(
            "You do not have access to container jobs."
        );
        expect(screen.queryByText("No container jobs found.")).not.toBeInTheDocument();
        expect(screen.queryByLabelText("Backend")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Refresh" })).not.toBeInTheDocument();
        expect(screen.queryByText(/refresh every 15 seconds/)).not.toBeInTheDocument();
        expect(containerQueries.useContainerJobs).toHaveBeenCalledWith("batch", false, undefined);
    });

    it("shows the loading state, not the no-access panel, while permissions are still loading", () => {
        allow(() => false, true);
        renderTab();
        expect(screen.getByRole("status")).toHaveTextContent("Loading container jobs...");
    });

    it("submits the chosen definition, the first queue, name, command and resources as the request body", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        renderTab();

        await user.click(screen.getByRole("button", { name: "Submit Job" }));
        const dialog = screen.getByRole("dialog");
        // Revisions of one definition collapse to one option.
        expect(within(dialog).getAllByRole("option", { name: "sim-train" })).toHaveLength(1);
        await user.selectOptions(within(dialog).getByLabelText("Job definition"), "sim-infer");
        await user.type(within(dialog).getByLabelText("Job name"), "render-2");
        await user.type(within(dialog).getByLabelText("Command"), "python main.py --fast");
        await user.type(within(dialog).getByLabelText("vCPUs"), "4");
        await user.type(within(dialog).getByLabelText("Memory (MiB)"), "16384");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));

        await waitFor(() =>
            expect(m.submitJob.mutateAsync).toHaveBeenCalledWith({
                jobDefinition: "sim-infer",
                jobQueue: "gpu-queue",
                name: "render-2",
                backend: "batch",
                command: ["python", "main.py", "--fast"],
                vcpus: 4,
                memory: 16384,
            })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("refuses a resource override below its floor before it reaches the service", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        renderTab();

        await user.click(screen.getByRole("button", { name: "Submit Job" }));
        const dialog = screen.getByRole("dialog");
        await user.type(within(dialog).getByLabelText("Job name"), "render-2");
        await user.type(within(dialog).getByLabelText("vCPUs"), "0");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));
        expect(within(dialog).getByRole("alert")).toHaveTextContent(
            "vCPUs must be a whole number of at least 1."
        );
        expect(m.submitJob.mutateAsync).not.toHaveBeenCalled();

        await user.clear(within(dialog).getByLabelText("vCPUs"));
        await user.type(within(dialog).getByLabelText("Memory (MiB)"), "256");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));
        expect(within(dialog).getByRole("alert")).toHaveTextContent(
            "Memory (MiB) must be a whole number of at least 512."
        );
        expect(m.submitJob.mutateAsync).not.toHaveBeenCalled();

        // A GPU count of 0 is a valid override and goes through.
        await user.clear(within(dialog).getByLabelText("Memory (MiB)"));
        await user.type(within(dialog).getByLabelText("GPUs"), "0");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));
        await waitFor(() =>
            expect(m.submitJob.mutateAsync).toHaveBeenCalledWith(
                expect.objectContaining({ name: "render-2", gpu: 0 })
            )
        );
        expect(m.submitJob.mutateAsync.mock.calls[0][0]).not.toHaveProperty("vcpus");
    });

    it("refuses to submit without a job queue", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        (containerQueries.useContainerCatalog as jest.Mock).mockReturnValue(catalog(false, false));
        renderTab();

        await user.click(screen.getByRole("button", { name: "Submit Job" }));
        const dialog = screen.getByRole("dialog");
        await user.type(within(dialog).getByLabelText("Job name"), "render-2");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));

        expect(within(dialog).getByRole("alert")).toHaveTextContent("Job queue is required.");
        expect(m.submitJob.mutateAsync).not.toHaveBeenCalled();
    });

    it("shows the batch job-name rule before a name Batch would refuse reaches the service", async () => {
        // The rule is checked on Submit, on the name as a whole, so each name is pasted rather than
        // typed key by key and user-event waits between nothing: a busy host then cannot push the
        // loop past the test timeout.
        const user = userEvent.setup({ delay: null });
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        renderTab();

        await user.click(screen.getByRole("button", { name: "Submit Job" }));
        const dialog = screen.getByRole("dialog");
        const nameInput = within(dialog).getByLabelText("Job name");
        for (const refused of ["-render", "_render", "render.1", "r".repeat(129)]) {
            await user.clear(nameInput);
            await user.paste(refused);
            expect(nameInput).toHaveValue(refused);
            await user.click(within(dialog).getByRole("button", { name: "Submit" }));
            expect(within(dialog).getByRole("alert")).toHaveTextContent(
                "Job name must be 1-128 letters, digits, hyphens or underscores, starting with a letter or digit."
            );
        }
        expect(m.submitJob.mutateAsync).not.toHaveBeenCalled();

        await user.clear(nameInput);
        await user.paste("R0_-render");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));
        await waitFor(() =>
            expect(m.submitJob.mutateAsync).toHaveBeenCalledWith(
                expect.objectContaining({ name: "R0_-render", backend: "batch" })
            )
        );
    });

    it("shows the eks job-name rule before a name that is not a lower-case DNS label reaches the service", async () => {
        // As in the batch case: the rule is checked on Submit, so the values are pasted.
        const user = userEvent.setup({ delay: null });
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        (containerQueries.useContainerCatalog as jest.Mock).mockReturnValue(catalog(true));
        renderTab();
        await user.selectOptions(screen.getByLabelText("Backend"), "eks");
        await user.selectOptions(screen.getByLabelText("Cluster"), "edh-eks-a");

        await user.click(screen.getByRole("button", { name: "Submit Job" }));
        const dialog = screen.getByRole("dialog");
        await user.click(within(dialog).getByLabelText("Image URI"));
        await user.paste("123456789012.dkr.ecr.eu-central-1.amazonaws.com/sim:1");
        const nameInput = within(dialog).getByLabelText("Job name");
        for (const refused of ["Render-EKS", "render_eks", "-render", "render-", "r".repeat(64)]) {
            await user.clear(nameInput);
            await user.paste(refused);
            expect(nameInput).toHaveValue(refused);
            await user.click(within(dialog).getByRole("button", { name: "Submit" }));
            expect(within(dialog).getByRole("alert")).toHaveTextContent(
                "Job name must be 1-63 lower-case letters, digits or hyphens, starting and ending with a letter or digit."
            );
        }
        expect(m.submitJob.mutateAsync).not.toHaveBeenCalled();

        await user.clear(nameInput);
        await user.paste("r".repeat(63));
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));
        await waitFor(() =>
            expect(m.submitJob.mutateAsync).toHaveBeenCalledWith(
                expect.objectContaining({ name: "r".repeat(63), backend: "eks" })
            )
        );
    });

    it("keeps the dialog open and shows the message when a submit is refused", async () => {
        const user = userEvent.setup();
        const m = mutations();
        m.submitJob.mutateAsync = jest.fn().mockRejectedValue(new Error("Forbidden"));
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        renderTab();

        await user.click(screen.getByRole("button", { name: "Submit Job" }));
        const dialog = screen.getByRole("dialog");
        await user.type(within(dialog).getByLabelText("Job name"), "render-2");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));

        await waitFor(() =>
            expect(within(dialog).getByRole("alert")).toHaveTextContent("Forbidden")
        );
        expect(screen.getByRole("dialog")).toBeInTheDocument();
    });

    it("offers Kill from the row menu as a destructive item, confirmed by a red button, and hides the menu without DELETE", async () => {
        const user = userEvent.setup();
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        const { unmount } = renderTab();

        // No per-row button: the action lives behind the kebab, like every other list.
        expect(screen.queryByRole("button", { name: "Kill render-1" })).not.toBeInTheDocument();
        const items = await openActionsFor(user, "render-1");
        expect(items.map((item) => item.textContent)).toEqual(["Kill"]);
        const kill = screen.getByRole("menuitem", { name: "Kill" });
        expect(kill).not.toHaveAttribute("aria-disabled", "true");
        expect(kill).toHaveClass("text-red-600");
        await user.click(kill);

        const dialog = screen.getByRole("dialog", { name: "Kill container job" });
        expect(dialog).toHaveTextContent(/Kill render-1\?/);
        expect(m.killJob.mutateAsync).not.toHaveBeenCalled();
        const confirm = within(dialog).getByRole("button", { name: "Kill" });
        expect(confirm).toHaveClass("bg-red-600");
        await user.click(confirm);
        await waitFor(() =>
            expect(m.killJob.mutateAsync).toHaveBeenCalledWith({ jobId: "j-1", backend: "batch" })
        );
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        unmount();

        allow((method, path) => !(method === "DELETE" && path === CONTAINERS_JOB_ROUTE));
        renderTab();
        expect(screen.queryByLabelText("Actions for render-1")).not.toBeInTheDocument();
        expect(screen.queryByRole("columnheader", { name: "Actions" })).not.toBeInTheDocument();
    });

    it("disables Kill for a finished job and for a listing without an id", async () => {
        const user = userEvent.setup();
        (containerQueries.useContainerJobs as jest.Mock).mockReturnValue(
            jobs([finishedJob, failedJob, { ...runningJob, jobId: "", jobName: "render-0" }])
        );
        renderTab();

        await openActionsFor(user, "render-2");
        expect(screen.getByRole("menuitem", { name: "Kill" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        await user.keyboard("{Escape}");
        await openActionsFor(user, "render-3");
        expect(screen.getByRole("menuitem", { name: "Kill" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
        await user.keyboard("{Escape}");
        await openActionsFor(user, "render-0");
        expect(screen.getByRole("menuitem", { name: "Kill" })).toHaveAttribute(
            "aria-disabled",
            "true"
        );
    });

    it("reports a refused kill as a banner and a closed dialog", async () => {
        const user = userEvent.setup();
        const m = mutations();
        m.killJob.mutateAsync = jest.fn().mockRejectedValue(new Error("Forbidden"));
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        renderTab();

        const items = await openActionsFor(user, "render-1");
        await user.click(items[0]);
        await user.click(screen.getByRole("button", { name: "Kill" }));

        await waitFor(() =>
            expect(screen.getByRole("alert")).toHaveTextContent(
                "Failed to kill render-1: Forbidden"
            )
        );
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    });

    it("closes the dialog and warns when EDH did not confirm a submit, so the job is not sent twice", async () => {
        const user = userEvent.setup();
        const m = mutations();
        m.submitJob.mutateAsync = jest.fn().mockRejectedValue(unknown502());
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        renderTab();

        await user.click(screen.getByRole("button", { name: "Submit Job" }));
        const dialog = screen.getByRole("dialog");
        await user.type(within(dialog).getByLabelText("Job name"), "render-2");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));

        await waitFor(() =>
            expect(mockToast.warning).toHaveBeenCalledWith("Submission not confirmed", {
                description: `render-2: ${UNCONFIRMED}`,
            })
        );
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(mockToast.success).not.toHaveBeenCalled();
    });

    it("sends one submit for a double-click on Submit", async () => {
        const user = userEvent.setup();
        const m = mutations();
        m.submitJob.mutateAsync = jest.fn(neverAnswers);
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        renderTab();

        await user.click(screen.getByRole("button", { name: "Submit Job" }));
        const dialog = screen.getByRole("dialog");
        await user.type(within(dialog).getByLabelText("Job name"), "render-2");
        doubleClick(within(dialog).getByRole("button", { name: "Submit" }));
        await waitFor(() => expect(m.submitJob.mutateAsync).toHaveBeenCalled());
        expect(m.submitJob.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
    });

    it("sends one kill for a double-click on the confirmation", async () => {
        const user = userEvent.setup();
        const m = mutations();
        m.killJob.mutateAsync = jest.fn(neverAnswers);
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        renderTab();

        const items = await openActionsFor(user, "render-1");
        await user.click(items[0]);
        doubleClick(screen.getByRole("button", { name: "Kill" }));
        await waitFor(() =>
            expect(m.killJob.mutateAsync).toHaveBeenCalledWith({ jobId: "j-1", backend: "batch" })
        );
        expect(m.killJob.mutateAsync.mock.calls.length).toBeLessThanOrEqual(1);
    });

    it("treats a submit EDH accepted without a readable answer as not confirmed", async () => {
        const user = userEvent.setup();
        const m = mutations();
        const accepted = new EdhRequestError(JOB_SUBMITTED, 502, {
            message: JOB_SUBMITTED,
            outcomeUnknown: true,
        });
        m.submitJob.mutateAsync = jest.fn().mockRejectedValue(accepted);
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        renderTab();

        await user.click(screen.getByRole("button", { name: "Submit Job" }));
        const dialog = screen.getByRole("dialog");
        await user.type(within(dialog).getByLabelText("Job name"), "render-2");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));

        await waitFor(() =>
            expect(mockToast.warning).toHaveBeenCalledWith("Submission not confirmed", {
                description: `render-2: ${JOB_SUBMITTED}`,
            })
        );
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(mockToast.error).not.toHaveBeenCalled();
    });

    it("warns 'Kill not confirmed' with no banner when EDH did not confirm a kill", async () => {
        const user = userEvent.setup();
        const m = mutations();
        m.killJob.mutateAsync = jest.fn().mockRejectedValue(unknown502());
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        renderTab();

        const items = await openActionsFor(user, "render-1");
        await user.click(items[0]);
        await user.click(screen.getByRole("button", { name: "Kill" }));

        await waitFor(() =>
            expect(mockToast.warning).toHaveBeenCalledWith("Kill not confirmed", {
                description: `render-1: ${UNCONFIRMED}`,
            })
        );
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
        expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
        expect(mockToast.error).not.toHaveBeenCalled();
    });

    it("keeps an open row menu open across a poll tick", async () => {
        const user = userEvent.setup();
        const { rerender } = renderTab();

        await openActionsFor(user, "render-1");
        expect(screen.getByRole("menu")).toBeInTheDocument();

        (containerQueries.useContainerJobs as jest.Mock).mockReturnValue({
            ...jobs([{ ...runningJob }]),
            isFetching: true,
        });
        rerender(tab());
        expect(screen.getByRole("menu")).toBeInTheDocument();

        (containerQueries.useContainerJobs as jest.Mock).mockReturnValue(jobs([{ ...runningJob }]));
        rerender(tab());
        expect(screen.getByRole("menu")).toBeInTheDocument();
        expect(screen.getByRole("menuitem", { name: "Kill" })).toBeInTheDocument();
    });
});

describe("SimulationsTab row actions button", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (containerQueries.useContainerCatalog as jest.Mock).mockReturnValue(catalog(false));
        (containerQueries.useContainerJobs as jest.Mock).mockReturnValue(jobs([runningJob]));
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(mutations());
        allow(() => true);
    });

    it("draws the button as an icon and opens the job's menu from it", async () => {
        const user = userEvent.setup();
        renderTab();

        const button = screen.getByRole("button", { name: "Actions for render-1" });
        // An SVG icon, not a text glyph that renders blank without a font carrying it.
        expect(button.querySelector('svg[aria-hidden="true"]')).not.toBeNull();
        expect(button.textContent).toBe("");
        expect((await openActionsFor(user, "render-1")).length).toBeGreaterThan(0);
    });
});

describe("focus when the Kill confirmation closes", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (containerQueries.useContainerCatalog as jest.Mock).mockReturnValue(catalog(false));
        (containerQueries.useContainerJobs as jest.Mock).mockReturnValue(jobs([runningJob]));
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(mutations());
        allow(() => true);
    });

    // Hidden included: the table is hidden from the accessibility tree while a dialog is open.
    const trigger = () =>
        screen.getByRole("button", { name: "Actions for render-1", hidden: true });

    it.each(["Escape", "Cancel", "Kill"])(
        "returns to the job's actions after %s, the job keeping its row",
        async (how) => {
            const user = userEvent.setup();
            renderTab();
            await openActionsFor(user, "render-1");
            await user.click(screen.getByRole("menuitem", { name: "Kill" }));
            const dialog = await screen.findByRole("dialog", { name: "Kill container job" });
            if (how === "Escape") {
                await user.keyboard("{Escape}");
            } else {
                await user.click(within(dialog).getByRole("button", { name: how }));
            }
            await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
            await waitFor(() => expect(trigger()).toHaveFocus());
        }
    );

    it("keeps focus on Killing... and the confirmation open while the kill is in flight", async () => {
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        const answer = holdRequest(m.killJob);
        const user = userEvent.setup({ delay: null });
        renderTab();
        await openActionsFor(user, "render-1");
        await user.click(screen.getByRole("menuitem", { name: "Kill" }));
        const dialog = await screen.findByRole("dialog", { name: "Kill container job" });

        await pressAndHold(user, dialog, "Kill", "Killing...");
        expect(m.killJob.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() => expect(trigger()).toHaveFocus());
    });
});

describe("focus when the Submit dialog closes", () => {
    beforeEach(() => {
        jest.clearAllMocks();
        (containerQueries.useContainerCatalog as jest.Mock).mockReturnValue(catalog(false));
        (containerQueries.useContainerJobs as jest.Mock).mockReturnValue(jobs([runningJob]));
        allow(() => true);
    });

    // Hidden included: the tab is hidden from the accessibility tree while a dialog is open.
    const button = (name: string) => screen.getByRole("button", { name, hidden: true });

    const openSubmit = async (user: ReturnType<typeof userEvent.setup>) => {
        button("Submit Job").focus();
        await user.keyboard("{Enter}");
        return screen.findByRole("dialog", { name: "Submit container job" });
    };

    const submitNamed = async (user: ReturnType<typeof userEvent.setup>, dialog: HTMLElement) => {
        await user.click(within(dialog).getByLabelText("Job name"));
        await user.paste("render-2");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    };

    it.each(["Escape", "Cancel"])("returns to Submit Job after %s", async (how) => {
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(mutations());
        const user = userEvent.setup({ delay: null });
        renderTab();
        const dialog = await openSubmit(user);
        if (how === "Escape") {
            await user.keyboard("{Escape}");
        } else {
            await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        }
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() => expect(button("Submit Job")).toHaveFocus());
    });

    it("goes to the refresh button once the job is submitted", async () => {
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        const user = userEvent.setup({ delay: null });
        renderTab();
        await submitNamed(user, await openSubmit(user));
        expect(m.submitJob.mutateAsync).toHaveBeenCalledTimes(1);
        await waitFor(() => expect(button("Refresh")).toHaveFocus());
    });

    it("goes to the refresh button when EDH did not confirm the submit", async () => {
        const m = mutations();
        m.submitJob.mutateAsync = jest.fn().mockRejectedValue(unknown502());
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        const user = userEvent.setup({ delay: null });
        renderTab();
        await submitNamed(user, await openSubmit(user));
        expect(mockToast.warning).toHaveBeenCalledWith("Submission not confirmed", {
            description: `render-2: ${UNCONFIRMED}`,
        });
        await waitFor(() => expect(button("Refresh")).toHaveFocus());
    });

    it("keeps focus on Submitting... and the dialog open while the submit is in flight", async () => {
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        const answer = holdRequest(m.submitJob);
        const user = userEvent.setup({ delay: null });
        renderTab();
        const dialog = await openSubmit(user);
        await user.click(within(dialog).getByLabelText("Job name"));
        await user.paste("render-2");

        await pressAndHold(user, dialog, "Submit", "Submitting...");
        expect(m.submitJob.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() => expect(button("Refresh")).toHaveFocus());
    });

    it("holds the dialog while the submit is in flight: the close button reads unavailable and the backdrop keeps focus", async () => {
        const m = mutations();
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        const answer = holdRequest(m.submitJob);
        const user = userEvent.setup({ delay: null });
        renderTab();
        const dialog = await openSubmit(user);
        await user.click(within(dialog).getByLabelText("Job name"));
        await user.paste("render-2");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));

        await expectHeldOpen(user, dialog);
        expect(m.submitJob.mutateAsync).toHaveBeenCalledTimes(1);

        await answer();
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    });

    it("returns to Submit Job when a submit is refused and the dialog is then cancelled", async () => {
        const m = mutations();
        m.submitJob.mutateAsync = jest.fn().mockRejectedValue(new Error("EDH refused the job"));
        (containerQueries.useContainerJobMutations as jest.Mock).mockReturnValue(m);
        const user = userEvent.setup({ delay: null });
        renderTab();
        const dialog = await openSubmit(user);
        await user.click(within(dialog).getByLabelText("Job name"));
        await user.paste("render-2");
        await user.click(within(dialog).getByRole("button", { name: "Submit" }));
        expect(await within(dialog).findByText("EDH refused the job")).toBeInTheDocument();
        await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
        await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
        await waitFor(() => expect(button("Submit Job")).toHaveFocus());
    });
});
