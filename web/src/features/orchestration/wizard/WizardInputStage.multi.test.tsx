/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Multi-file arity: an editable LIST of input files, each row independently scoped.
 *
 * A `multi` workflow's whole point is combining several files, and the rows are independent by
 * design — each carries its own databaseId/assetId, so a selection can span assets and even
 * databases. That independence is easy to lose to a refactor that hoists the asset picker out of the
 * row (which would silently restrict every run to one asset), so it is asserted here rather than left
 * to the single-file path's coverage.
 *
 * `InputFileSelector` is NOT mocked: the rows and their per-file version selectors are the subject.
 * Only the data hooks are stubbed.
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WizardInputStage from "./WizardInputStage";
import type { ExecuteInputFile, Workflow } from "../types";

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

const FILES: Record<string, string[]> = {
    "asset-a": ["/a-one.glb", "/a-two.glb"],
    "asset-b": ["/b-one.glb"],
    "asset-c": ["/c-one.glb"],
};

const multiWorkflow = (systemConfig: Record<string, any> = {}): Workflow =>
    ({
        databaseId: "db1",
        workflowId: "wf-multi",
        workflowName: "Multi",
        enabled: true,
        archived: false,
        specifiedPipelines: [{ pipelineId: "pipe1", pipelineDatabaseId: "db1" }],
        systemConfig: { inputFileArity: "multi", ...systemConfig },
    } as Workflow);

/** Renders the stage as a controlled list so add/remove/edit reflect back like the real wizard. */
function renderStage(initial: ExecuteInputFile[] = [], workflow = multiWorkflow()) {
    const seen: ExecuteInputFile[][] = [];
    const Harness: React.FC = () => {
        const [files, setFiles] = React.useState<ExecuteInputFile[]>(initial);
        return (
            <WizardInputStage
                workflow={workflow}
                databaseId="db1"
                inputFiles={files}
                onInputFilesChange={(next) => {
                    seen.push(next);
                    setFiles(next);
                }}
                onOutputAssetIdChange={jest.fn()}
                onOutputDatabaseIdChange={jest.fn()}
                onOutputPathPrefixChange={jest.fn()}
            />
        );
    };
    render(<Harness />);
    return { seen, latest: () => seen[seen.length - 1] };
}

const rows = () => screen.getAllByLabelText("Asset");

beforeEach(() => {
    jest.clearAllMocks();
    queries().useDatabases.mockReturnValue({
        data: [{ databaseId: "db1" }, { databaseId: "db2" }],
    });
    // Asset/file hooks answer per (databaseId, assetId) so different rows genuinely see different
    // data — a shared stub would hide a row-independence regression.
    queries().useAssetSearch.mockImplementation((_q: string, databaseId?: string) => ({
        data: {
            items: (ASSETS[databaseId || ""] || []).map((a) => ({ databaseId, ...a })),
            total: (ASSETS[databaseId || ""] || []).length,
        },
        isFetching: false,
    }));
    queries().useAssetFileSearch.mockImplementation(
        (_q: string, databaseId?: string, assetId?: string) => ({
            data: {
                items: (FILES[assetId || ""] || []).map((p) => ({
                    fileName: p.slice(1),
                    key: p,
                    relativePath: p,
                    isFolder: false,
                })),
                total: (FILES[assetId || ""] || []).length,
            },
            isFetching: false,
        })
    );
    queries().useFileVersions.mockImplementation(
        (_db?: string, _asset?: string, relativeFileKey?: string) => ({
            data: relativeFileKey
                ? [
                      {
                          versionId: `${relativeFileKey}-v2`,
                          relativeKey: relativeFileKey,
                          isLatest: true,
                      },
                      {
                          versionId: `${relativeFileKey}-v1`,
                          relativeKey: relativeFileKey,
                          isLatest: false,
                      },
                  ]
                : [],
            isFetching: false,
        })
    );
});

describe("WizardInputStage multi-file arity", () => {
    it("starts with an editable list and an Add control rather than a fixed single row", async () => {
        renderStage();
        expect(screen.getByText(/No input files added yet/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Add Input File" })).toBeInTheDocument();
    });

    it("adds a row per click, so several files can be selected", async () => {
        const { latest } = renderStage();
        const add = screen.getByRole("button", { name: "Add Input File" });
        await userEvent.click(add);
        await waitFor(() => expect(rows()).toHaveLength(1));
        await userEvent.click(screen.getByRole("button", { name: "Add Input File" }));
        await waitFor(() => expect(rows()).toHaveLength(2));
        expect(latest()).toHaveLength(2);
    });

    it("removes the clicked row and keeps the others", async () => {
        // Index-targeted removal: removing the middle row must not drop the last one.
        const { latest } = renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db1", assetId: "asset-b", relativeFileKey: "/b-one.glb" },
            { databaseId: "db2", assetId: "asset-c", relativeFileKey: "/c-one.glb" },
        ]);
        await userEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]);
        expect(latest().map((f) => f.assetId)).toEqual(["asset-a", "asset-c"]);
    });

    it("gives every row its own database and asset picker, so a selection can span assets", async () => {
        // The load-bearing assertion for "multiple files over same asset or multiple assets": each row
        // holds its own databaseId/assetId. Hoisting either out of the row would break this.
        renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db2", assetId: "asset-c", relativeFileKey: "/c-one.glb" },
        ]);
        expect(screen.getAllByLabelText("Database")).toHaveLength(2);
        expect(rows()).toHaveLength(2);
        // Row 2's file picker offers db2/asset-c's files, not row 1's.
        await userEvent.click(screen.getAllByLabelText("File")[1]);
        expect(await screen.findByRole("option", { name: "/c-one.glb" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: "/a-one.glb" })).not.toBeInTheDocument();
    });

    it("edits only the row that changed", async () => {
        const { latest } = renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
        ]);
        await userEvent.click(screen.getAllByLabelText("File")[1]);
        await userEvent.click(await screen.findByRole("option", { name: "/a-two.glb" }));
        expect(latest().map((f) => f.relativeFileKey)).toEqual(["/a-one.glb", "/a-two.glb"]);
    });

    it("lets two rows select two different files of the SAME asset", async () => {
        const { latest } = renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "" },
        ]);
        await userEvent.click(screen.getAllByLabelText("File")[1]);
        await userEvent.click(await screen.findByRole("option", { name: "/a-two.glb" }));
        const files = latest();
        expect(files.every((f) => f.assetId === "asset-a")).toBe(true);
        expect(files.map((f) => f.relativeFileKey)).toEqual(["/a-one.glb", "/a-two.glb"]);
    });

    it("offers a per-row file version selector, defaulting to Latest", () => {
        renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db2", assetId: "asset-c", relativeFileKey: "/c-one.glb" },
        ]);
        const selectors = screen.getAllByLabelText("File version") as HTMLSelectElement[];
        expect(selectors).toHaveLength(2);
        // Unset = read whatever is current at launch.
        expect(selectors.map((s) => s.value)).toEqual(["", ""]);
    });

    it("scopes each row's version list to that row's own file", () => {
        // Two rows over DIFFERENT files must not share one version list — the bug that made every row
        // in an asset show the same (asset-scoped) options.
        renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-two.glb" },
        ]);
        expect(queries().useFileVersions).toHaveBeenCalledWith("db1", "asset-a", "/a-one.glb");
        expect(queries().useFileVersions).toHaveBeenCalledWith("db1", "asset-a", "/a-two.glb");
        const optionSets = (screen.getAllByLabelText("File version") as HTMLSelectElement[]).map(
            (s) => Array.from(s.querySelectorAll("option")).map((o) => o.getAttribute("value"))
        );
        expect(optionSets[0]).toContain("/a-one.glb-v2");
        expect(optionSets[0]).not.toContain("/a-two.glb-v2");
        expect(optionSets[1]).toContain("/a-two.glb-v2");
    });

    it("pins a version on one row without touching the other", async () => {
        const { latest } = renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-two.glb" },
        ]);
        await userEvent.selectOptions(screen.getAllByLabelText("File version")[1], "/a-two.glb-v1");
        expect(latest()[0].versionId).toBeUndefined();
        expect(latest()[1].versionId).toBe("/a-two.glb-v1");
    });

    it("hides files the workflow's filters reject in every row", async () => {
        renderStage(
            [{ databaseId: "db1", assetId: "asset-a", relativeFileKey: "" }],
            multiWorkflow({ inputFileFilters: { allow: ["*a-one*"] } })
        );
        await userEvent.click(screen.getByLabelText("File"));
        expect(await screen.findByRole("option", { name: "/a-one.glb" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: "/a-two.glb" })).not.toBeInTheDocument();
    });
});
