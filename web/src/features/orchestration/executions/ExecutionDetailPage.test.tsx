/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import ExecutionDetailPage from "./ExecutionDetailPage";
import type { ExecutionDetail } from "../types";

// Mock Monaco editor
jest.mock("@monaco-editor/react", () => ({
    __esModule: true,
    default: () => null,
}));

// Mock the query hooks
jest.mock("../api/queries", () => ({
    useExecutionDetails: jest.fn(),
    useExecutionDetailMetadata: jest.fn(),
}));

/**
 * The paged-metadata hook's shape for a collection that was NOT escalated (the details response
 * carried it whole), which is what every test that is not about escalation expects.
 */
const IDLE_PAGED_METADATA = {
    data: undefined,
    isLoading: false,
    isError: false,
    error: null,
    hasNextPage: false,
    isFetchingNextPage: false,
    fetchNextPage: jest.fn(),
};

// Mock the API service for logs
jest.mock("../api/executions", () => ({
    getExecutionLogs: jest.fn(),
}));

// Mock the permissions hook
jest.mock("../permissions/useAllowedRoutes", () => ({
    useAllowedRoutes: jest.fn(),
}));

describe("ExecutionDetailPage", () => {
    let queryClient: QueryClient;

    beforeEach(() => {
        queryClient = new QueryClient({
            defaultOptions: {
                queries: { retry: false },
            },
        });
        jest.clearAllMocks();
        // Default: no collection escalated. Tests about escalation override this.
        const { useExecutionDetailMetadata } = require("../api/queries");
        useExecutionDetailMetadata.mockReturnValue(IDLE_PAGED_METADATA);
    });

    it("shows the recorded per-step settings and flags what the template overrode", async () => {
        // The point of the Settings tab: a finished run must explain which settings were ENFORCED, even
        // after the template or pipeline has since been edited or archived. effectiveSystemConfig is the
        // recorded merge; templateOverrides says which keys the template changed.
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutionDetails.mockReturnValue({
            data: {
                workflowExecutionId: "e-set",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                workflowSystemConfig: { inputFileArity: "one", concurrencyRestriction: "perAsset" },
                pipelines: [
                    {
                        pipelineId: "p1",
                        pipelineName: "Cosmos 3 Nano",
                        executionStatus: "SUCCEEDED",
                        templateId: "cosmos3-nano-text2video",
                        effectiveSystemConfig: { inputFileArity: "one", requireTemplate: true },
                        templateOverrides: { inputFileArity: "one" },
                    },
                ],
            },
            isLoading: false,
            error: null,
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e-set" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await userEvent.click(await screen.findByRole("tab", { name: /Settings/i }));

        // Workflow level is labelled CURRENT, because it is read live rather than snapshotted.
        expect(screen.getByText(/Workflow settings \(current\)/i)).toBeInTheDocument();
        expect(screen.getByText(/can differ if/i)).toBeInTheDocument();

        // Per-step card names the step and its template.
        expect(screen.getByText(/Step 1: Cosmos 3 Nano/)).toBeInTheDocument();
        expect(screen.getByText("cosmos3-nano-text2video")).toBeInTheDocument();

        // Settings are rendered with readable labels, not raw camelCase keys.
        expect(screen.getAllByText(/Input file count:/).length).toBeGreaterThan(0);
        // And the key the template changed is flagged. (Only the step grid receives overrides, but the
        // badge renders once per overridden key, so assert on the count rather than a single match.)
        expect(screen.getAllByText("overridden").length).toBeGreaterThan(0);
    });

    it("says so when a step recorded no settings, rather than implying empty settings", async () => {
        // Executions from before settings capture existed have no effectiveSystemConfig. Showing an empty
        // grid would read as "no restrictions", which is a materially different claim.
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutionDetails.mockReturnValue({
            data: {
                workflowExecutionId: "e-old",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                pipelines: [{ pipelineId: "p1", executionStatus: "SUCCEEDED" }],
            },
            isLoading: false,
            error: null,
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e-old" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await userEvent.click(await screen.findByRole("tab", { name: /Settings/i }));
        expect(screen.getByText(/No settings recorded for this step/i)).toBeInTheDocument();
        expect(screen.queryByText("overridden")).not.toBeInTheDocument();
    });

    it("exposes the tab strip with tablist/tab semantics and marks the active tab", async () => {
        // Styled after Cloudscape's Tabs (a bordered strip with the selected tab lifted onto the
        // container surface). The ARIA roles are what make it a tab strip rather than loose buttons,
        // so they are asserted here alongside the selected state.
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutionDetails.mockReturnValue({
            data: {
                workflowExecutionId: "e-tabs",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                pipelines: [{ pipelineId: "p1", executionStatus: "SUCCEEDED" }],
            },
            isLoading: false,
            error: null,
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e-tabs" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        await waitFor(() => expect(screen.getByRole("tablist")).toBeInTheDocument());
        expect(screen.getByRole("tab", { name: /Inputs/i })).toHaveAttribute(
            "aria-selected",
            "true"
        );
        const pipelines = screen.getByRole("tab", { name: /Pipelines/i });
        expect(pipelines).toHaveAttribute("aria-selected", "false");
        await userEvent.click(pipelines);
        expect(screen.getByRole("tab", { name: /Pipelines/i })).toHaveAttribute(
            "aria-selected",
            "true"
        );
    });

    it("renders execution detail with pipeline config body and template snapshot", async () => {
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        const mockDetail: ExecutionDetail = {
            workflowExecutionId: "e1",
            workflowId: "wf-1",
            workflowDatabaseId: "db-1",
            executionStatus: "SUCCEEDED",
            triggeredByUserId: "user-1",
            triggerType: "manual",
            executionStartDate: "2026-07-18T10:00:00Z",
            executionStopDate: "2026-07-18T10:30:00Z",
            pipelines: [
                {
                    pipelineId: "pipe-1",
                    name: "Test Pipeline",
                    executionStatus: "SUCCEEDED",
                    executionStartDate: "2026-07-18T10:05:00Z",
                    executionStopDate: "2026-07-18T10:25:00Z",
                    renderedConfig: '{"key": "value"}',
                    configFormat: "json",
                    templateId: "template-1",
                    templateTags: { tag1: "value1" },
                    customTemplateOverrideUsed: false,
                },
            ],
            inputFiles: [
                {
                    databaseId: "db-1",
                    assetId: "asset-1",
                    inputAssetFileKey: "file.txt",
                    versionId: "v1",
                },
            ],
            outputs: {
                files: [{ relativeFilePath: "output.txt", size: 1024 }],
                metadata: [],
                results: [],
            },
        };

        useExecutionDetails.mockReturnValue({
            data: mockDetail,
            isLoading: false,
            error: null,
        });

        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: jest.fn(() => true),
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Check header elements
        expect(screen.getByText("Succeeded")).toBeInTheDocument();
        // The execution id appears in both the breadcrumb and the header field.
        expect(screen.getAllByText(/e1/).length).toBeGreaterThan(0);

        // Navigate to Pipelines tab
        // The tab strip is role="tablist"/role="tab" (Cloudscape-style), not plain buttons.
        const pipelinesTab = screen.getByRole("tab", { name: /Pipelines/i });
        await userEvent.click(pipelinesTab);

        // Check pipeline section renders
        await waitFor(() => {
            expect(screen.getByText("Test Pipeline")).toBeInTheDocument();
        });

        // Check template snapshot is visible
        expect(screen.getByText(/template-1/)).toBeInTheDocument();

        // Config should show as <pre> by default, not Monaco
        const configPre = screen.getByText('{"key": "value"}');
        expect(configPre.tagName).toBe("PRE");

        // Check for "View in editor" button
        expect(screen.getByText("View in editor")).toBeInTheDocument();
    });

    it("expands Monaco editor when 'View in editor' is clicked", async () => {
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        const mockDetail: ExecutionDetail = {
            workflowExecutionId: "e1",
            workflowId: "wf-1",
            workflowDatabaseId: "db-1",
            executionStatus: "SUCCEEDED",
            triggeredByUserId: "user-1",
            triggerType: "manual",
            executionStartDate: "2026-07-18T10:00:00Z",
            executionStopDate: "2026-07-18T10:30:00Z",
            pipelines: [
                {
                    pipelineId: "pipe-1",
                    name: "Test Pipeline",
                    executionStatus: "SUCCEEDED",
                    executionStartDate: "2026-07-18T10:05:00Z",
                    executionStopDate: "2026-07-18T10:25:00Z",
                    renderedConfig: '{"key": "value"}',
                    configFormat: "json",
                    templateId: "template-1",
                    templateTags: { tag1: "value1" },
                    customTemplateOverrideUsed: false,
                },
            ],
            inputFiles: [],
            outputs: {},
        };

        useExecutionDetails.mockReturnValue({
            data: mockDetail,
            isLoading: false,
            error: null,
        });

        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: jest.fn(() => true),
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Navigate to Pipelines tab
        // The tab strip is role="tablist"/role="tab" (Cloudscape-style), not plain buttons.
        const pipelinesTab = screen.getByRole("tab", { name: /Pipelines/i });
        await userEvent.click(pipelinesTab);

        await waitFor(() => {
            expect(screen.getByText("Test Pipeline")).toBeInTheDocument();
        });

        // Click "View in editor"
        const viewButton = screen.getByText("View in editor");
        await userEvent.click(viewButton);

        // Button should disappear after click
        await waitFor(() => {
            expect(screen.queryByText("View in editor")).not.toBeInTheDocument();
        });
    });

    it("keeps the Logs tab reachable but explains it when permission is denied", async () => {
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        const mockDetail: ExecutionDetail = {
            workflowExecutionId: "e1",
            workflowId: "wf-1",
            workflowDatabaseId: "db-1",
            executionStatus: "SUCCEEDED",
            triggeredByUserId: "user-1",
            triggerType: "manual",
            executionStartDate: "2026-07-18T10:00:00Z",
            executionStopDate: "2026-07-18T10:30:00Z",
            pipelines: [],
            inputFiles: [],
            outputs: {},
        };

        useExecutionDetails.mockReturnValue({
            data: mockDetail,
            isLoading: false,
            error: null,
        });

        // Deny logs permission
        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: jest.fn((method, path) => {
                if (path === "/workflows/executions/{executionId}/logs") {
                    return false;
                }
                return true;
            }),
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // The tab is offered so the logs are discoverable; selecting it explains the denial
        // instead of presenting an empty viewer.
        const logsTab = screen.getByRole("tab", { name: "Logs" });
        expect(logsTab).toBeInTheDocument();
        await userEvent.click(logsTab);
        expect(
            screen.getByText("You do not have permission to view execution logs.")
        ).toBeInTheDocument();
    });

    it("shows Logs section when permission is granted", () => {
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");

        const mockDetail: ExecutionDetail = {
            workflowExecutionId: "e1",
            workflowId: "wf-1",
            workflowDatabaseId: "db-1",
            executionStatus: "SUCCEEDED",
            triggeredByUserId: "user-1",
            triggerType: "manual",
            executionStartDate: "2026-07-18T10:00:00Z",
            executionStopDate: "2026-07-18T10:30:00Z",
            pipelines: [],
            inputFiles: [],
            outputs: {},
        };

        useExecutionDetails.mockReturnValue({
            data: mockDetail,
            isLoading: false,
            error: null,
        });

        // Allow logs permission
        useAllowedRoutes.mockReturnValue({
            loading: false,
            can: jest.fn(() => true),
        });

        render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e1" />
                </MemoryRouter>
            </QueryClientProvider>
        );

        // Logs section should be present
        expect(screen.getByText("Logs")).toBeInTheDocument();
    });

    /** Render the detail page with a supplied detail payload and permissive permissions. */
    const renderDetail = (data: Partial<ExecutionDetail>) => {
        const { useExecutionDetails } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutionDetails.mockReturnValue({
            data: {
                workflowExecutionId: "e-t",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                ...data,
            },
            isLoading: false,
            error: null,
        });
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(() => true) });
        return render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e-t" />
                </MemoryRouter>
            </QueryClientProvider>
        );
    };

    it("marks a truncated input section where it is rendered, not only in the banner", async () => {
        // A user scrolled to the table must be able to tell the list is a subset; the page-level banner
        // is off-screen by then.
        renderDetail({
            inputFiles: [{ databaseId: "db-1", assetId: "a1", inputAssetFileKey: "/f.glb" }],
            inputMetadata: [{ assetId: "a1", filePath: "/f.glb", metadata: { k: "v" } }],
            truncatedCollections: ["inputFiles"],
        });

        expect(await screen.findByText(/these sections are a subset/i)).toBeInTheDocument();
        // Exactly one section carries the marker — the truncated one.
        expect(screen.getAllByText("Partial")).toHaveLength(1);
    });

    it("marks each metadata collection independently", async () => {
        renderDetail({
            inputMetadata: [{ assetId: "a1", filePath: "/f.glb", metadata: { k: "v" } }],
            inputDatabaseMetadata: [{ databaseId: "src-db", filePath: "/", metadata: { p: "x" } }],
            truncatedCollections: ["inputDatabaseMetadata"],
        });

        // The asset collection is complete, so only the database collection is marked.
        expect(await screen.findByText(/Input Database Metadata \(1\)/)).toBeInTheDocument();
        expect(screen.getAllByText("Partial")).toHaveLength(1);
        expect(
            screen.queryByText(/produced more asset and file metadata than this view returns/i)
        ).not.toBeInTheDocument();
    });

    it("marks no section and shows no banner when nothing was truncated", async () => {
        renderDetail({
            inputFiles: [{ databaseId: "db-1", assetId: "a1", inputAssetFileKey: "/f.glb" }],
            inputMetadata: [{ assetId: "a1", filePath: "/f.glb", metadata: { k: "v" } }],
            truncatedCollections: [],
        });

        expect(await screen.findByText(/Input Files \(1\)/)).toBeInTheDocument();
        expect(screen.queryByText("Partial")).not.toBeInTheDocument();
        expect(screen.queryByText(/these sections are a subset/i)).not.toBeInTheDocument();
    });

    it("tells a multi-pipeline run's repeated database rows apart by pipeline", async () => {
        // Database metadata belongs to every pipeline of a run, so a 3-step workflow records the same
        // row three times, differing only by the pipeline that read it. Without the Pipeline column the
        // three are indistinguishable and read as one row triplicated by mistake.
        renderDetail({
            inputDatabaseMetadata: [
                {
                    pipelineId: "p1",
                    databaseId: "src-db",
                    filePath: "/",
                    metadata: { region: "us" },
                },
                {
                    pipelineId: "p2",
                    databaseId: "src-db",
                    filePath: "/",
                    metadata: { region: "us" },
                },
                {
                    pipelineId: "p3",
                    databaseId: "src-db",
                    filePath: "/",
                    metadata: { region: "us" },
                },
            ],
        });

        expect(await screen.findByText(/Input Database Metadata \(3\)/)).toBeInTheDocument();
        expect(screen.getAllByRole("columnheader", { name: /Pipeline/i }).length).toBeGreaterThan(
            0
        );
        // One row per pipeline, each naming its own reader.
        expect(screen.getByText("p1")).toBeInTheDocument();
        expect(screen.getByText("p2")).toBeInTheDocument();
        expect(screen.getByText("p3")).toBeInTheDocument();
        expect(screen.getAllByText("src-db")).toHaveLength(3);
    });

    it("attributes a per-file metadata row to the pipeline that received the file", async () => {
        // A per-file row belongs only to the pipeline that received that file (each gets the subset
        // passing its own inputFileFilters), so the asset collection's rows differ by pipeline too.
        renderDetail({
            inputMetadata: [
                {
                    pipelineId: "p1",
                    assetId: "a1",
                    filePath: "/scan.laz",
                    metadata: { crs: "4326" },
                },
                {
                    pipelineId: "p2",
                    assetId: "a1",
                    filePath: "/photo.jpg",
                    metadata: { crs: "3857" },
                },
            ],
        });

        expect(await screen.findByText(/Input Asset and File Metadata \(2\)/)).toBeInTheDocument();
        expect(screen.getByText("/scan.laz")).toBeInTheDocument();
        expect(screen.getByText("/photo.jpg")).toBeInTheDocument();
        expect(screen.getByText("p1")).toBeInTheDocument();
        expect(screen.getByText("p2")).toBeInTheDocument();
    });

    it("renders a single-pipeline run's metadata unchanged", async () => {
        renderDetail({
            inputMetadata: [
                { pipelineId: "p1", assetId: "a1", filePath: "/f.glb", metadata: { k: "v" } },
            ],
            inputDatabaseMetadata: [
                { pipelineId: "p1", databaseId: "src-db", filePath: "/", metadata: { p: "x" } },
            ],
        });

        expect(await screen.findByText(/Input Asset and File Metadata \(1\)/)).toBeInTheDocument();
        expect(screen.getByText(/Input Database Metadata \(1\)/)).toBeInTheDocument();
        // The one pipeline appears in both tables; no row is duplicated.
        expect(screen.getAllByText("p1")).toHaveLength(2);
        expect(screen.getByText("/f.glb")).toBeInTheDocument();
        expect(screen.getByText("src-db")).toBeInTheDocument();
    });

    it("shows database metadata above asset and file metadata", async () => {
        // Widest entity first, matching the order the pipeline and workflow forms present the metadata
        // toggles in. The asset/file heading names both entities it covers, so neither block reads as
        // "the metadata" now that a second collection sits beside it.
        renderDetail({
            inputMetadata: [{ pipelineId: "p1", assetId: "a1", filePath: "/f.glb", metadata: {} }],
            inputDatabaseMetadata: [
                { pipelineId: "p1", databaseId: "src-db", filePath: "/", metadata: {} },
            ],
        });
        const database = await screen.findByText(/Input Database Metadata/);
        const assetAndFile = screen.getByText(/Input Asset and File Metadata/);
        const nodes = Array.from(document.body.querySelectorAll("*"));
        expect(nodes.indexOf(database)).toBeLessThan(nodes.indexOf(assetAndFile));
    });

    it("keeps a file whose row carries ONLY attributes visible", async () => {
        // fileMetadata and fileAttributes are gated independently, so this row shape is reachable:
        // attributes captured, no metadata. Reading only `metadata` dropped the row entirely — the
        // API reported the attributes and the table showed nothing, with no truncation flag to explain
        // it, which is worse than omitting a column.
        renderDetail({
            inputMetadata: [
                {
                    pipelineId: "p1",
                    assetId: "a1",
                    filePath: "/clips/in.mp4",
                    metadata: {},
                    attributes: { fps: "30" },
                },
            ],
        });

        expect(await screen.findByText(/Input Asset and File Metadata \(1\)/)).toBeInTheDocument();
        expect(screen.getByText("fps")).toBeInTheDocument();
        expect(screen.getByText("30")).toBeInTheDocument();
        // Labeled as an attribute, so it is not mistaken for metadata.
        expect(screen.getByText("Attribute")).toBeInTheDocument();
    });

    it("shows metadata and attributes as separate labeled rows for one file", async () => {
        renderDetail({
            inputMetadata: [
                {
                    pipelineId: "p1",
                    assetId: "a1",
                    filePath: "/clips/in.mp4",
                    metadata: { PROMPT: "make it snow" },
                    attributes: { fps: "30" },
                },
            ],
        });

        expect(await screen.findByText(/Input Asset and File Metadata \(2\)/)).toBeInTheDocument();
        expect(screen.getByText("PROMPT")).toBeInTheDocument();
        expect(screen.getByText("fps")).toBeInTheDocument();
        expect(screen.getByText("Metadata")).toBeInTheDocument();
        expect(screen.getByText("Attribute")).toBeInTheDocument();
    });

    it("renders a metadata row carrying no pipelineId without failing", async () => {
        // An execution recorded before per-pipeline attribution has no pipelineId on its rows; the cell
        // falls back to the same dash the output tables use rather than rendering blank or throwing.
        renderDetail({
            inputMetadata: [{ assetId: "a1", filePath: "/f.glb", metadata: { k: "v" } }],
            inputDatabaseMetadata: [{ databaseId: "src-db", filePath: "/", metadata: { p: "x" } }],
        });

        expect(await screen.findByText(/Input Asset and File Metadata \(1\)/)).toBeInTheDocument();
        expect(screen.getByText(/Input Database Metadata \(1\)/)).toBeInTheDocument();
        expect(screen.getAllByText("—")).toHaveLength(2);
    });

    it("attributes each output metadata row to the pipeline that wrote it", async () => {
        // Output metadata is recorded per pipeline execution, so a two-step workflow writing the same
        // key onto the same file produces two rows identical but for the producing pipeline. Without a
        // Pipeline column an operator diagnosing a wrong value sees a duplicated row and cannot say
        // which step wrote it, even though the response carries the answer.
        renderDetail({
            outputs: {
                metadata: [
                    {
                        targetFilePath: "/out.glb",
                        metadataKey: "previewGenerated",
                        metadataValue: "true",
                        pipelineId: "p1",
                    },
                    {
                        targetFilePath: "/out.glb",
                        metadataKey: "previewGenerated",
                        metadataValue: "true",
                        pipelineId: "p2",
                    },
                ],
            } as any,
        });

        await userEvent.click(await screen.findByRole("tab", { name: /Outputs/i }));
        expect(screen.getByText(/Output Metadata \(2\)/)).toBeInTheDocument();
        // The Pipeline column exists and tells the two otherwise identical rows apart.
        expect(screen.getByRole("columnheader", { name: /Pipeline/i })).toBeInTheDocument();
        expect(screen.getByText("p1")).toBeInTheDocument();
        expect(screen.getByText("p2")).toBeInTheDocument();
    });

    it("renders an output metadata row carrying no pipelineId as a dash", async () => {
        // A run recorded before per-pipeline attribution has no pipelineId; the cell falls back to the
        // same dash the sibling output-files table uses rather than rendering blank.
        renderDetail({
            outputs: {
                metadata: [{ targetFilePath: "/out.glb", metadataKey: "k", metadataValue: "v" }],
            } as any,
        });

        await userEvent.click(await screen.findByRole("tab", { name: /Outputs/i }));
        expect(screen.getByText(/Output Metadata \(1\)/)).toBeInTheDocument();
        expect(screen.getAllByText("—").length).toBeGreaterThan(0);
    });

    it("offers the config body's S3 location when the inline copy is truncated", async () => {
        renderDetail({
            pipelines: [
                {
                    pipelineId: "p1",
                    executionStatus: "SUCCEEDED",
                    renderedConfig: "{trimmed",
                    renderedConfigTruncated: true,
                    renderedConfigLocation: {
                        bucket: "run-bkt",
                        key: "executions/e-t/input/1/config.json",
                    },
                },
            ],
        });

        await userEvent.click(await screen.findByRole("tab", { name: /Pipelines/i }));
        expect(screen.getByText("Truncated")).toBeInTheDocument();
        expect(
            screen.getByText("s3://run-bkt/executions/e-t/input/1/config.json")
        ).toBeInTheDocument();
    });

    it("shows no config location when the inline body is complete", async () => {
        renderDetail({
            pipelines: [
                {
                    pipelineId: "p1",
                    executionStatus: "SUCCEEDED",
                    renderedConfig: '{"k": 1}',
                    renderedConfigTruncated: false,
                },
            ],
        });

        await userEvent.click(await screen.findByRole("tab", { name: /Pipelines/i }));
        expect(screen.getByText("Executed Configuration")).toBeInTheDocument();
        expect(screen.queryByText("Truncated")).not.toBeInTheDocument();
        expect(screen.queryByText(/Complete body in Amazon S3/i)).not.toBeInTheDocument();
    });

    // ------------------------------------------------------------------------
    // Escalation to the paged metadata route + visual paging
    // ------------------------------------------------------------------------

    /** A paged-metadata hook result carrying `pages`. */
    const pagedResult = (pages: any[][], over: Record<string, any> = {}) => ({
        data: { pages: pages.map((Items) => ({ Items, collection: "input" })) },
        isLoading: false,
        isError: false,
        error: null,
        hasNextPage: false,
        isFetchingNextPage: false,
        fetchNextPage: jest.fn(),
        ...over,
    });

    /** Render with the paged hook answering per collection, and an optional permission predicate. */
    const renderEscalated = (
        data: Partial<ExecutionDetail>,
        byCollection: Record<string, any>,
        canRoute: (method: string, path: string) => boolean = () => true
    ) => {
        const { useExecutionDetails, useExecutionDetailMetadata } = require("../api/queries");
        const { useAllowedRoutes } = require("../permissions/useAllowedRoutes");
        useExecutionDetails.mockReturnValue({
            data: {
                workflowExecutionId: "e-p",
                workflowId: "wf-1",
                workflowDatabaseId: "db-1",
                executionStatus: "SUCCEEDED",
                ...data,
            },
            isLoading: false,
            error: null,
        });
        useExecutionDetailMetadata.mockImplementation(
            (_executionId: string, collection: string) =>
                byCollection[collection] || IDLE_PAGED_METADATA
        );
        useAllowedRoutes.mockReturnValue({ loading: false, can: jest.fn(canRoute) });
        return render(
            <QueryClientProvider client={queryClient}>
                <MemoryRouter>
                    <ExecutionDetailPage executionId="e-p" />
                </MemoryRouter>
            </QueryClientProvider>
        );
    };

    /** Metadata rows in the paged route's shape, one map entry each. */
    const pagedMetadataRows = (count: number, offset = 0) =>
        Array.from({ length: count }, (_, i) => ({
            databaseId: "db-1",
            assetId: `a${i + offset}`,
            filePath: `/f${i + offset}.glb`,
            scope: "asset",
            pipelineId: "p1",
            metadata: { k: `v${i + offset}` },
        }));

    it("re-reads a truncated metadata collection from the paged route instead of showing the subset", async () => {
        // The details response caps each collection, so its rows are the FIRST slice of a large run's
        // metadata. When it says so, the section is read through the paged route, which walks every
        // step — the table then shows the collection rather than a slice of it.
        renderEscalated(
            {
                inputMetadata: [
                    { assetId: "inline-only", filePath: "/i.glb", metadata: { k: "v" } },
                ],
                truncatedCollections: ["inputMetadata"],
            },
            { input: pagedResult([pagedMetadataRows(3)]) }
        );

        expect(await screen.findByText(/Input Asset and File Metadata \(3\)/)).toBeInTheDocument();
        // The subset row the details response carried is replaced, not merged.
        expect(screen.queryByText("inline-only")).not.toBeInTheDocument();
        expect(screen.getByText("a0")).toBeInTheDocument();
        expect(screen.getByText("a2")).toBeInTheDocument();
    });

    it("drops the Partial marker once the paged walk reached its last page", async () => {
        // Requirement C: a fully retrieved collection is no longer partial and must not claim to be.
        renderEscalated(
            { inputMetadata: [], truncatedCollections: ["inputMetadata"] },
            { input: pagedResult([pagedMetadataRows(2)], { hasNextPage: false }) }
        );

        expect(await screen.findByText(/Input Asset and File Metadata \(2\)/)).toBeInTheDocument();
        expect(screen.queryByText("Partial")).not.toBeInTheDocument();
    });

    it("keeps the Partial marker while the paged read still has pages left", async () => {
        // The other half of Requirement C: a bounded read is still bounded, so the badge stays until the
        // walk finishes.
        renderEscalated(
            { inputMetadata: [], truncatedCollections: ["inputMetadata"] },
            { input: pagedResult([pagedMetadataRows(2)], { hasNextPage: true }) }
        );

        expect(await screen.findByText(/Input Asset and File Metadata \(2\)/)).toBeInTheDocument();
        expect(screen.getAllByText("Partial")).toHaveLength(1);
    });

    it("fetches the next server page from the section's own Load more control", async () => {
        const fetchNextPage = jest.fn();
        renderEscalated(
            { inputMetadata: [], truncatedCollections: ["inputMetadata"] },
            { input: pagedResult([pagedMetadataRows(2)], { hasNextPage: true, fetchNextPage }) }
        );

        await userEvent.click(await screen.findByRole("button", { name: /Load more rows/i }));
        expect(fetchNextPage).toHaveBeenCalled();
    });

    it("shows no Load more control once the collection is fully loaded", async () => {
        renderEscalated(
            { inputMetadata: [], truncatedCollections: ["inputMetadata"] },
            { input: pagedResult([pagedMetadataRows(2)]) }
        );

        expect(await screen.findByText(/Input Asset and File Metadata \(2\)/)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Load more rows/i })).not.toBeInTheDocument();
    });

    it("pages the loaded rows locally, one page at a time", async () => {
        // Requirement B: the loaded set is itself paged in the browser, so 60 retrieved rows are read a
        // page at a time rather than as one 60-row wall.
        renderEscalated(
            { inputMetadata: [], truncatedCollections: ["inputMetadata"] },
            { input: pagedResult([pagedMetadataRows(30), pagedMetadataRows(30, 30)]) }
        );

        expect(await screen.findByText(/Input Asset and File Metadata \(60\)/)).toBeInTheDocument();
        // 60 rows at 25 per page.
        expect(screen.getByText(/Page 1 of 3/)).toBeInTheDocument();
        expect(screen.getByText("a0")).toBeInTheDocument();
        expect(screen.queryByText("a30")).not.toBeInTheDocument();

        await userEvent.click(screen.getAllByRole("button", { name: "Next" })[0]);
        expect(screen.getByText(/Page 2 of 3/)).toBeInTheDocument();
        expect(screen.getByText("a30")).toBeInTheDocument();
    });

    it("accumulates both server pages into one table rather than replacing the first", async () => {
        renderEscalated(
            { inputMetadata: [], truncatedCollections: ["inputMetadata"] },
            { input: pagedResult([pagedMetadataRows(2), pagedMetadataRows(2, 2)]) }
        );

        expect(await screen.findByText(/Input Asset and File Metadata \(4\)/)).toBeInTheDocument();
        expect(screen.getByText("a0")).toBeInTheDocument();
        expect(screen.getByText("a3")).toBeInTheDocument();
    });

    it("escalates only the collection the server flagged", async () => {
        const { useExecutionDetailMetadata } = require("../api/queries");
        renderEscalated(
            {
                inputMetadata: [],
                inputDatabaseMetadata: [
                    { databaseId: "src-db", filePath: "/", metadata: { p: "x" } },
                ],
                truncatedCollections: ["inputMetadata"],
            },
            { input: pagedResult([pagedMetadataRows(1)]) }
        );

        await screen.findByText(/Input Asset and File Metadata \(1\)/);
        // The flagged asset/file collection is enabled; the complete database collection is not, so it
        // costs no request.
        expect(useExecutionDetailMetadata).toHaveBeenCalledWith("e-p", "input", true);
        expect(useExecutionDetailMetadata).toHaveBeenCalledWith("e-p", "inputDatabase", false);
    });

    it("escalates the output metadata collection on the Outputs tab", async () => {
        const { useExecutionDetailMetadata } = require("../api/queries");
        renderEscalated(
            {
                outputs: { metadata: [{ targetFilePath: "/o.glb", metadataKey: "k" }] },
                truncatedCollections: ["outputs.metadata"],
            },
            {
                output: pagedResult([
                    [{ targetFilePath: "/paged.glb", metadataKey: "pk", metadataValue: "pv" }],
                ]),
            }
        );

        await userEvent.click(await screen.findByRole("tab", { name: /Outputs/i }));
        expect(useExecutionDetailMetadata).toHaveBeenCalledWith("e-p", "output", true);
        // Output rows are already one key/value each, so they need no reshaping.
        expect(screen.getByText("/paged.glb")).toBeInTheDocument();
        expect(screen.getByText("pv")).toBeInTheDocument();
    });

    it("does not claim a run has no outputs while its output metadata is being paged", async () => {
        // The details response caps a collection to zero rows when the file collections consumed the
        // budget. The empty-state card would then assert "no asset outputs" over a run that has them.
        renderEscalated(
            { outputs: { metadata: [] }, truncatedCollections: ["outputs.metadata"] },
            {
                output: pagedResult([
                    [{ targetFilePath: "/paged.glb", metadataKey: "pk", metadataValue: "pv" }],
                ]),
            }
        );

        await userEvent.click(await screen.findByRole("tab", { name: /Outputs/i }));
        expect(screen.queryByText(/No asset outputs were recorded/i)).not.toBeInTheDocument();
        expect(screen.getByText("/paged.glb")).toBeInTheDocument();
    });

    it("keeps the subset on screen and states the failure when the paged read fails", async () => {
        // A read path, so the failure is inline: an empty table would be indistinguishable from a run
        // that recorded no metadata.
        renderEscalated(
            {
                inputMetadata: [{ assetId: "inline", filePath: "/i.glb", metadata: { k: "v" } }],
                truncatedCollections: ["inputMetadata"],
            },
            {
                input: {
                    ...IDLE_PAGED_METADATA,
                    isError: true,
                    error: new Error("Forbidden"),
                },
            }
        );

        expect(await screen.findByRole("alert")).toHaveTextContent(/Forbidden/);
        expect(screen.getByText("inline")).toBeInTheDocument();
        // Still partial: the complete set was never retrieved.
        expect(screen.getAllByText("Partial")).toHaveLength(1);
    });

    it("explains a truncated section instead of escalating when the route is not permitted", async () => {
        // Tier-1 for the paged route is separate from details, so a deployment can allow one and not the
        // other. The section says the complete set is out of reach rather than issuing a 403.
        const { useExecutionDetailMetadata } = require("../api/queries");
        renderEscalated(
            {
                inputMetadata: [{ assetId: "inline", filePath: "/i.glb", metadata: { k: "v" } }],
                truncatedCollections: ["inputMetadata"],
            },
            {},
            (_method, path) => path !== "/workflows/executions/{executionId}/details/metadata"
        );

        expect(
            await screen.findByText(/do not have permission to page the complete set/i)
        ).toBeInTheDocument();
        expect(useExecutionDetailMetadata).toHaveBeenCalledWith("e-p", "input", false);
        expect(screen.getAllByText("Partial")).toHaveLength(1);
    });

    it("says plainly that a truncated file collection has no complete set to load", async () => {
        // Files stay inline with no paged route, so the flag is the reader's only signal — and there is
        // nothing further to fetch.
        renderEscalated(
            {
                inputFiles: [{ databaseId: "db-1", assetId: "a1", inputAssetFileKey: "/f.glb" }],
                truncatedCollections: ["inputFiles"],
            },
            {}
        );

        expect(await screen.findByText(/not retrievable through this view/i)).toBeInTheDocument();
        expect(screen.queryByRole("button", { name: /Load more rows/i })).not.toBeInTheDocument();
    });

    it("does not call a metadata collection a subset in the banner once it is paged", async () => {
        // The banner named every flagged collection as a subset. An escalated one is not — it is read
        // separately, page by page — so the two are stated apart.
        renderEscalated(
            {
                inputFiles: [{ databaseId: "db-1", assetId: "a1", inputAssetFileKey: "/f.glb" }],
                inputMetadata: [],
                truncatedCollections: ["inputFiles", "inputMetadata"],
            },
            { input: pagedResult([pagedMetadataRows(1)]) }
        );

        const subset = await screen.findByText(/sections are a subset: inputFiles\./);
        expect(subset).toBeInTheDocument();
        expect(
            screen.getByText(/read separately, a page at a time: inputMetadata\./)
        ).toBeInTheDocument();
    });
});
