/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The two opt-in metadataInputs keys in the pipeline form, the template-overrides editor and the
 * workflow form: each is offered with its label and help, reads OFF when a stored map omits it (the
 * opposite default from the four metadata types), and saves under its own key.
 */

import React from "react";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PipelineForm from "./PipelineForm";
import TemplateOverridesEditor from "./TemplateOverridesEditor";
import WorkflowSystemConfigFields from "../workflows/WorkflowSystemConfigFields";
import { OPT_IN_METADATA_HELP } from "../wizard/resolveRestrictions";

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

const FILES_LABEL = "Per-file metadata for whole-asset and folder runs";
const SCHEMA_LABEL = "Metadata schema";

/** The checkbox of the row a metadata label belongs to. */
const toggleFor = (label: string) =>
    screen
        .getByText(label)
        .closest("label")
        ?.querySelector("input[type=checkbox]") as HTMLInputElement;

beforeEach(() => {
    jest.clearAllMocks();
});

describe("PipelineForm opt-in metadata inputs", () => {
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

    it("offers both toggles after the four metadata types", () => {
        render(<PipelineForm mode="create" databaseId="db1" onDone={jest.fn()} />, {
            wrapper: createWrapper(),
        });
        const at = (label: string) =>
            Array.prototype.indexOf.call(
                document.body.querySelectorAll("*"),
                screen.getByText(label)
            );
        expect(at("File attributes")).toBeLessThan(at(FILES_LABEL));
        expect(at(FILES_LABEL)).toBeLessThan(at(SCHEMA_LABEL));
    });

    it("shows both as off for a stored config that omits them, while the four types read on", () => {
        renderStored({});
        expect(toggleFor(FILES_LABEL)).not.toBeChecked();
        expect(toggleFor(SCHEMA_LABEL)).not.toBeChecked();
        expect(toggleFor("File metadata")).toBeChecked();
    });

    it("shows each as on only when the stored config turns it on", () => {
        renderStored({ assetFileMetadata: true });
        expect(toggleFor(FILES_LABEL)).toBeChecked();
        expect(toggleFor(SCHEMA_LABEL)).not.toBeChecked();
    });

    it("saves a toggled-on key under its own name", async () => {
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
        await user.click(toggleFor(SCHEMA_LABEL));
        fireEvent.submit(document.getElementById("pipeline-form")!);

        await waitFor(() => expect(mutateAsync).toHaveBeenCalled());
        const saved = mutateAsync.mock.calls[0][0].systemConfig.metadataInputs;
        expect(saved.metadataSchema).toBe(true);
        // The untouched opt-in key is not turned on (absent reads off).
        expect(saved.assetFileMetadata === true).toBe(false);
    });

    it("explains each toggle, including the file limit", () => {
        expect(OPT_IN_METADATA_HELP.assetFileMetadata).toContain("500 files per selection");
        expect(OPT_IN_METADATA_HELP.assetFileMetadata).toContain("Off by default");
        expect(OPT_IN_METADATA_HELP.metadataSchema).toContain("GLOBAL");
        expect(OPT_IN_METADATA_HELP.metadataSchema).toContain("Off by default");
    });
});

describe("TemplateOverridesEditor opt-in metadata inputs", () => {
    it("offers both keys once metadata inputs are overridden, off by default", () => {
        const onChange = jest.fn();
        render(
            <TemplateOverridesEditor
                value={{ metadataInputs: { assetMetadata: true } }}
                onChange={onChange}
            />
        );
        expect(toggleFor(FILES_LABEL)).not.toBeChecked();
        expect(toggleFor(SCHEMA_LABEL)).not.toBeChecked();
        fireEvent.click(toggleFor(FILES_LABEL));
        expect(onChange).toHaveBeenCalledWith({
            metadataInputs: { assetMetadata: true, assetFileMetadata: true },
        });
    });
});

describe("WorkflowSystemConfigFields opt-in metadata inputs", () => {
    it("offers both gates, off when the workflow omits them, and sets each by its key", () => {
        const onMetadataInputsChange = jest.fn();
        const props: any = {
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
            onInputFileArityChange: jest.fn(),
            onAssetScopeChange: jest.fn(),
            onMetadataInputsChange,
            onAllowFiltersChange: jest.fn(),
            onExcludeFiltersChange: jest.fn(),
            onConcurrencyRestrictionChange: jest.fn(),
            onLocationTypeChange: jest.fn(),
            onAllowOverrideChange: jest.fn(),
            onAllowWorkflowTriggerChainingChange: jest.fn(),
            onDefaultOutputPathPrefixChange: jest.fn(),
        };
        render(<WorkflowSystemConfigFields {...props} />);
        expect(toggleFor(FILES_LABEL)).not.toBeChecked();
        expect(toggleFor(SCHEMA_LABEL)).not.toBeChecked();
        fireEvent.click(toggleFor(SCHEMA_LABEL));
        expect(onMetadataInputsChange).toHaveBeenCalledWith({ metadataSchema: true });
    });
});
