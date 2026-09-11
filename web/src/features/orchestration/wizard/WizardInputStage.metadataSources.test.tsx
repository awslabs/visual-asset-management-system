/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Metadata sources at arity 'none'.
 *
 * A run with no input files has no assets or databases to derive metadata from, so the entities are
 * named. They are METADATA sources, not inputs: no file is selected, and they travel in their own
 * request fields — the backend rejects any input file at this arity.
 *
 * The selection is optional at every point. A pipeline that genuinely requires the metadata checks for
 * it itself and fails itself, so the wizard must never block on an empty selection.
 *
 * `MetadataSourceSelector` is NOT mocked: the pickers are the subject. Only the data hooks are stubbed.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WizardInputStage from "./WizardInputStage";
import type { ExecuteInputFile, MetadataSourceAsset, Workflow } from "../types";

jest.mock("../api/queries", () => ({
    useDatabases: jest.fn(),
    useAssetSearch: jest.fn(),
    useAssetFileSearch: jest.fn(),
    useFileVersions: jest.fn(),
}));

const queries = () => require("../api/queries");

const ASSETS: Record<string, { assetId: string; assetName: string }[]> = {
    db1: [
        { assetId: "asset-a", assetName: "Pump A" },
        { assetId: "asset-b", assetName: "Pump B" },
    ],
    db2: [{ assetId: "asset-c", assetName: "Valve C" }],
};

/** All four metadata types on, so both pickers are offered unless a test says otherwise. */
const ALL_METADATA = {
    assetMetadata: true,
    fileMetadata: true,
    fileAttributes: true,
    databaseMetadata: true,
};

const noneWorkflow = (systemConfig: Record<string, any> = {}): Workflow =>
    ({
        databaseId: "db1",
        workflowId: "wf-none",
        workflowName: "Results only",
        enabled: true,
        archived: false,
        specifiedPipelines: [{ pipelineId: "pipe1", pipelineDatabaseId: "db1" }],
        systemConfig: {
            inputFileArity: "none",
            metadataInputs: ALL_METADATA,
            outputTarget: { locationType: "none" },
            ...systemConfig,
        },
    } as Workflow);

/** The steps the stage resolves the effective metadata gate against. */
const stepWantingAll = [{ label: "Pipeline 1", systemConfig: { metadataInputs: ALL_METADATA } }];

interface RenderOptions {
    workflow?: Workflow;
    initialSources?: MetadataSourceAsset[];
    initialDatabaseId?: string;
    pipelineConstraints?: any[];
    inputFiles?: ExecuteInputFile[];
}

/** Renders the stage as a controlled form so add/remove/select reflect back like the real wizard. */
function renderStage(options: RenderOptions = {}) {
    const {
        workflow = noneWorkflow(),
        initialSources = [],
        initialDatabaseId,
        pipelineConstraints = stepWantingAll,
        inputFiles = [],
    } = options;

    const sourcesSeen: MetadataSourceAsset[][] = [];
    const databaseIdsSeen: (string | undefined)[] = [];

    const Harness: React.FC = () => {
        const [sources, setSources] = React.useState<MetadataSourceAsset[]>(initialSources);
        const [dbId, setDbId] = React.useState<string | undefined>(initialDatabaseId);
        return (
            <WizardInputStage
                workflow={workflow}
                databaseId="db1"
                inputFiles={inputFiles}
                metadataSourceAssets={sources}
                metadataSourceDatabaseId={dbId}
                onInputFilesChange={jest.fn()}
                onMetadataSourceAssetsChange={(next) => {
                    sourcesSeen.push(next);
                    setSources(next);
                }}
                onMetadataSourceDatabaseIdChange={(next) => {
                    databaseIdsSeen.push(next);
                    setDbId(next);
                }}
                onOutputAssetIdChange={jest.fn()}
                onOutputDatabaseIdChange={jest.fn()}
                onOutputPathPrefixChange={jest.fn()}
                pipelineConstraints={pipelineConstraints}
            />
        );
    };
    render(<Harness />);
    return {
        latestSources: () => sourcesSeen[sourcesSeen.length - 1],
        latestDatabaseId: () => databaseIdsSeen[databaseIdsSeen.length - 1],
    };
}

beforeEach(() => {
    jest.clearAllMocks();
    // GLOBAL is included deliberately: it is a real value of the databases hook (the shared
    // pipeline/workflow catalog), so the picker's exclusion of it has to be a filter, not an accident
    // of the fixture.
    queries().useDatabases.mockReturnValue({
        data: [{ databaseId: "db1" }, { databaseId: "db2" }, { databaseId: "GLOBAL" }],
    });
    queries().useAssetSearch.mockImplementation((_q: string, databaseId?: string) => ({
        data: {
            items: (ASSETS[databaseId || ""] || []).map((a) => ({ databaseId, ...a })),
            total: (ASSETS[databaseId || ""] || []).length,
        },
        isFetching: false,
    }));
    queries().useAssetFileSearch.mockReturnValue({
        data: { items: [], total: 0 },
        isFetching: false,
    });
    queries().useFileVersions.mockReturnValue({ data: [], isFetching: false });
});

describe("WizardInputStage metadata sources at arity none", () => {
    it("offers a database picker and an asset picker but NO file picker", async () => {
        renderStage();
        expect(await screen.findByLabelText("Metadata source database")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Add Metadata Source Asset" }));
        expect(await screen.findByLabelText("Metadata source asset")).toBeInTheDocument();
        // A metadata source is an entity, never a file — so no file or version control appears, and
        // neither do the input-file pickers (this arity carries no input files at all).
        expect(screen.queryByLabelText("File")).not.toBeInTheDocument();
        expect(screen.queryByLabelText("File version")).not.toBeInTheDocument();
        expect(screen.queryByRole("button", { name: "Add Input File" })).not.toBeInTheDocument();
    });

    it("states that the selection is optional and only for metadata input", () => {
        renderStage();
        const notice = screen.getByRole("status");
        expect(notice).toHaveTextContent(/optional and only for metadata input/i);
        expect(notice).toHaveTextContent(/database and asset\(s\)/i);
    });

    it("names only the database in the notice when asset metadata is off", () => {
        const workflow = noneWorkflow({
            metadataInputs: { ...ALL_METADATA, assetMetadata: false },
        });
        renderStage({
            workflow,
            pipelineConstraints: [
                {
                    label: "Pipeline 1",
                    systemConfig: { metadataInputs: { ...ALL_METADATA, assetMetadata: false } },
                },
            ],
        });
        expect(screen.getByRole("status")).toHaveTextContent(
            /The database you select here is optional and only for metadata input/i
        );
        expect(screen.queryByLabelText("Metadata source asset")).not.toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Add Metadata Source Asset" })
        ).not.toBeInTheDocument();
    });

    it("names only the asset(s) in the notice when database metadata is off", () => {
        const workflow = noneWorkflow({
            metadataInputs: { ...ALL_METADATA, databaseMetadata: false },
        });
        renderStage({
            workflow,
            pipelineConstraints: [
                {
                    label: "Pipeline 1",
                    systemConfig: { metadataInputs: { ...ALL_METADATA, databaseMetadata: false } },
                },
            ],
        });
        expect(screen.getByRole("status")).toHaveTextContent(/The asset\(s\) you select here/i);
        expect(screen.queryByLabelText("Metadata source database")).not.toBeInTheDocument();
    });

    it("hides the whole section when neither asset nor database metadata is provided", () => {
        const off = { ...ALL_METADATA, assetMetadata: false, databaseMetadata: false };
        renderStage({
            workflow: noneWorkflow({ metadataInputs: off }),
            pipelineConstraints: [{ label: "Pipeline 1", systemConfig: { metadataInputs: off } }],
        });
        expect(screen.queryByText("Metadata Sources")).not.toBeInTheDocument();
        expect(screen.queryByLabelText("Metadata source database")).not.toBeInTheDocument();
    });

    it("does NOT offer GLOBAL in the metadata-source database picker", () => {
        // databaseMetadata reads ONE concrete database's own metadata; GLOBAL is the
        // unscoped/all-databases keyword and not an asset database, so the backend rejects it. Offering
        // it would produce a guaranteed 400.
        renderStage();
        const picker = screen.getByLabelText("Metadata source database") as HTMLSelectElement;
        const values = Array.from(picker.querySelectorAll("option")).map((o) => o.value);
        expect(values).toContain("db1");
        expect(values).toContain("db2");
        expect(values).not.toContain("GLOBAL");
    });

    it("offers exactly one database, not a multi-select", async () => {
        const { latestDatabaseId } = renderStage();
        const picker = screen.getByLabelText("Metadata source database") as HTMLSelectElement;
        expect(picker.multiple).toBe(false);
        await userEvent.selectOptions(picker, "db2");
        expect(latestDatabaseId()).toBe("db2");
        await userEvent.selectOptions(picker, "db1");
        expect(latestDatabaseId()).toBe("db1");
    });

    it("clears the database selection back to none", async () => {
        const { latestDatabaseId } = renderStage({ initialDatabaseId: "db1" });
        await userEvent.selectOptions(screen.getByLabelText("Metadata source database"), "");
        expect(latestDatabaseId()).toBeUndefined();
    });
});

describe("WizardInputStage metadata source asset multiplicity", () => {
    const crossAsset = (crossAssetAllowed: boolean) =>
        noneWorkflow({
            assetScope: { crossAssetAllowed, singleAssetOnly: !crossAssetAllowed },
        });

    it("allows several source assets when the workflow allows cross-asset input", async () => {
        const { latestSources } = renderStage({ workflow: crossAsset(true) });
        const add = () => screen.getByRole("button", { name: "Add Metadata Source Asset" });
        await userEvent.click(add());
        await waitFor(() =>
            expect(screen.getAllByLabelText("Metadata source asset")).toHaveLength(1)
        );
        await userEvent.click(add());
        await waitFor(() =>
            expect(screen.getAllByLabelText("Metadata source asset")).toHaveLength(2)
        );
        expect(latestSources()).toHaveLength(2);
    });

    it("allows only ONE source asset when the workflow does not", async () => {
        renderStage({ workflow: crossAsset(false) });
        await userEvent.click(screen.getByRole("button", { name: "Add Metadata Source Asset" }));
        await waitFor(() =>
            expect(screen.getAllByLabelText("Metadata source asset")).toHaveLength(1)
        );
        // The add control is withdrawn once a row exists, so a second source cannot be named.
        expect(
            screen.queryByRole("button", { name: "Add Metadata Source Asset" })
        ).not.toBeInTheDocument();
    });

    it("re-offers the add control after the single row is removed", async () => {
        renderStage({
            workflow: crossAsset(false),
            initialSources: [{ databaseId: "db1", assetId: "asset-a" }],
        });
        expect(
            screen.queryByRole("button", { name: "Add Metadata Source Asset" })
        ).not.toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Remove Metadata Source" }));
        expect(
            await screen.findByRole("button", { name: "Add Metadata Source Asset" })
        ).toBeInTheDocument();
    });

    it("gives every source row its own database and asset picker, so sources can span databases", async () => {
        renderStage({
            workflow: crossAsset(true),
            initialSources: [
                { databaseId: "db1", assetId: "asset-a" },
                { databaseId: "db2", assetId: "asset-c" },
            ],
        });
        // One database picker per row, scoping that row's asset search independently.
        expect(screen.getAllByLabelText("Metadata source asset database")).toHaveLength(2);
        // Row 2's asset picker offers db2's assets, not row 1's. The option's accessible name is its
        // label plus the detail line (the assetId), so it is matched by substring.
        await userEvent.click(screen.getAllByLabelText("Metadata source asset")[1]);
        expect(await screen.findByRole("option", { name: /Valve C/ })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: /Pump A/ })).not.toBeInTheDocument();
    });

    it("removes the clicked source row and keeps the others", async () => {
        const { latestSources } = renderStage({
            workflow: crossAsset(true),
            initialSources: [
                { databaseId: "db1", assetId: "asset-a" },
                { databaseId: "db1", assetId: "asset-b" },
                { databaseId: "db2", assetId: "asset-c" },
            ],
        });
        await userEvent.click(screen.getAllByRole("button", { name: "Remove Metadata Source" })[1]);
        expect(latestSources().map((s) => s.assetId)).toEqual(["asset-a", "asset-c"]);
    });

    it("resets a row's asset when its database changes", async () => {
        const { latestSources } = renderStage({
            workflow: crossAsset(true),
            initialSources: [{ databaseId: "db1", assetId: "asset-a" }],
        });
        await userEvent.selectOptions(
            screen.getByLabelText("Metadata source asset database"),
            "db2"
        );
        expect(latestSources()[0]).toEqual({ databaseId: "db2", assetId: "" });
    });
});

describe("WizardInputStage input-file arities are unaffected", () => {
    const withArity = (inputFileArity: string) =>
        ({
            databaseId: "db1",
            workflowId: "wf",
            workflowName: "WF",
            enabled: true,
            archived: false,
            specifiedPipelines: [{ pipelineId: "pipe1", pipelineDatabaseId: "db1" }],
            systemConfig: { inputFileArity, metadataInputs: ALL_METADATA },
        } as Workflow);

    it("still renders the single file picker at arity one, with no metadata-source section", () => {
        renderStage({ workflow: withArity("one") });
        expect(screen.getByLabelText("File")).toBeInTheDocument();
        expect(screen.queryByText("Metadata Sources")).not.toBeInTheDocument();
        expect(screen.queryByLabelText("Metadata source database")).not.toBeInTheDocument();
    });

    it("still renders the add-file control at arity multi, with no metadata-source section", () => {
        renderStage({ workflow: withArity("multi") });
        expect(screen.getByRole("button", { name: "Add Input File" })).toBeInTheDocument();
        expect(screen.queryByText("Metadata Sources")).not.toBeInTheDocument();
        expect(
            screen.queryByRole("button", { name: "Add Metadata Source Asset" })
        ).not.toBeInTheDocument();
    });
});
