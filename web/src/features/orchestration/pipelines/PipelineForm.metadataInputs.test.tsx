/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The pipeline systemConfig's metadataInputs toggles.
 *
 * The record builders default every key ON, and the API stores systemConfig wholesale, so a stored map
 * may omit any of the four while still carrying it. Each control is therefore rendered from a watched
 * value resolved through metadataEnabled rather than a bare registration, which would show a pipeline
 * whose map omits a key as opted out of it and then save that opt-out.
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PipelineForm from "./PipelineForm";

jest.mock("../api/queries", () => ({
    useCreatePipeline: jest.fn(() => ({ mutateAsync: jest.fn() })),
    useUpdatePipeline: jest.fn(() => ({ mutateAsync: jest.fn() })),
}));

jest.mock("../../../services/appCache", () => ({
    appCache: { getItem: jest.fn(() => ({ featuresEnabled: [] })) },
}));

const createWrapper = () => {
    const queryClient = new QueryClient({
        defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
    });
    const Wrapper = ({ children }: { children: React.ReactNode }) => (
        <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    );
    Wrapper.displayName = "TestQueryClientWrapper";
    return Wrapper;
};

/** The checkbox next to a metadata label. */
const toggleFor = (label: string) =>
    screen
        .getByText(label)
        .parentElement?.querySelector("input[type=checkbox]") as HTMLInputElement;

beforeEach(() => {
    jest.clearAllMocks();
});

describe("PipelineForm metadata inputs", () => {
    it("offers all four metadata toggles", () => {
        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });
        expect(screen.getByText("Asset metadata")).toBeInTheDocument();
        expect(screen.getByText("File metadata")).toBeInTheDocument();
        expect(screen.getByText("File attributes")).toBeInTheDocument();
        expect(screen.getByText("Database metadata")).toBeInTheDocument();
    });

    it("orders the toggles widest entity first", () => {
        // database -> asset -> file, the containment the rows describe.
        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });
        const at = (label: string) =>
            Array.prototype.indexOf.call(
                document.body.querySelectorAll("*"),
                screen.getByText(label)
            );
        expect(at("Database metadata")).toBeLessThan(at("Asset metadata"));
        expect(at("Asset metadata")).toBeLessThan(at("File metadata"));
        expect(at("File metadata")).toBeLessThan(at("File attributes"));
    });

    /** Render the edit form over a stored (possibly partial) metadataInputs map. */
    const renderStored = (metadataInputs: Record<string, boolean>) =>
        render(
            <PipelineForm
                mode="edit"
                databaseId="db1"
                initial={{
                    pipelineId: "p1",
                    pipelineName: "Stored",
                    databaseId: "db1",
                    executionConfig: { executionType: "Lambda" },
                    systemConfig: { inputFileArity: "one", metadataInputs },
                }}
                onDone={jest.fn()}
            />,
            { wrapper: createWrapper() }
        );

    const ALL_LABELS = ["Asset metadata", "File metadata", "File attributes", "Database metadata"];

    it("shows every toggle as on for a stored config that omits all of them", () => {
        // The empty map is not an opt-out of everything: the execute path collects all four, so the
        // form must not present the pipeline as having declined them.
        renderStored({});
        for (const label of ALL_LABELS) {
            expect(toggleFor(label)).toBeChecked();
        }
    });

    it.each(ALL_LABELS)("shows %s as off only when the stored config turns it off", (label) => {
        const key = {
            "Asset metadata": "assetMetadata",
            "File metadata": "fileMetadata",
            "File attributes": "fileAttributes",
            "Database metadata": "databaseMetadata",
        }[label] as string;
        renderStored({ [key]: false });
        expect(toggleFor(label)).not.toBeChecked();
        // Naming one key does not turn the others off.
        for (const other of ALL_LABELS.filter((l) => l !== label)) {
            expect(toggleFor(other)).toBeChecked();
        }
    });

    it("saves the toggled-off value under the databaseMetadata key", async () => {
        const user = userEvent.setup();
        const mutateAsync = jest.fn().mockResolvedValue({ pipeline: { pipelineId: "p1" } });
        const { useCreatePipeline } = require("../api/queries");
        (useCreatePipeline as jest.Mock).mockReturnValue({ mutateAsync });

        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });

        await user.type(screen.getByLabelText(/Pipeline Name/), "New Pipe");
        const timeouts = screen.getAllByPlaceholderText(/1-604800/);
        await user.type(timeouts[0], "3600");
        await user.type(timeouts[1], "60");
        await user.click(toggleFor("Database metadata"));
        fireEvent.submit(document.getElementById("pipeline-form")!);

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        // The create mutation takes the body directly (the update one wraps it with the path ids).
        expect(mutateAsync.mock.calls[0][0].systemConfig.metadataInputs.databaseMetadata).toBe(
            false
        );
    });
});
