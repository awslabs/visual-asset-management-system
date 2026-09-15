/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Multi-file arity: a compact LIST of the selected files plus picker rows for the ones still being
 * chosen.
 *
 * A `multi` workflow's whole point is combining several files, and the entries are independent by
 * design — each carries its own databaseId/assetId, so a selection can span assets and even
 * databases. That independence is easy to lose to a refactor that hoists the asset out of the entry
 * (which would silently restrict every run to one asset), so it is asserted here rather than left to
 * the single-file path's coverage.
 *
 * The list has to hold hundreds of entries (the file manager's Automation action presets them), so
 * a complete entry is one windowed row that opens no request of its own; a row is edited by opening
 * it, and a new one is picked through `InputFileSelector`, which is NOT mocked. Only the data hooks
 * are stubbed.
 */

import React from "react";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WizardInputStage from "./WizardInputStage";
import type { ExecuteInputFile, Workflow } from "../types";

jest.mock("../api/queries", () => ({
    useDatabases: jest.fn(),
    useAssetSearch: jest.fn(),
    useAssetFileSearch: jest.fn(),
    useAssetFilePages: jest.fn(),
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
function renderStage(
    initial: ExecuteInputFile[] = [],
    workflow = multiWorkflow(),
    extraProps: Record<string, any> = {}
) {
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
                {...extraProps}
            />
        );
    };
    render(<Harness />);
    return { seen, latest: () => seen[seen.length - 1] };
}

/** The picker rows (an Asset control each). */
const rows = () => screen.getAllByLabelText("Asset");
/** The compact rows of the selected-files list. */
const listRows = () => screen.queryAllByTestId("input-file-row");
const listRowTexts = () => listRows().map((r) => r.textContent || "");
const editButtons = () => screen.getAllByRole("button", { name: "Edit" });

const rowsFor = (count: number, assetId = "asset-a"): ExecuteInputFile[] =>
    Array.from({ length: count }, (_, i) => ({
        databaseId: "db1",
        assetId,
        relativeFileKey: `/f${i}.glb`,
    }));

/** A one-page file listing for the bulk picker. */
const listing = (keys: string[]) => {
    const fetchNextPage = jest.fn();
    queries().useAssetFilePages.mockReturnValue({
        data: {
            pages: [
                {
                    items: keys.map((k) => ({
                        fileName: k.slice(1),
                        key: k,
                        relativePath: k,
                        isFolder: false,
                    })),
                },
            ],
        },
        isLoading: false,
        isError: false,
        error: null,
        hasNextPage: false,
        isFetchingNextPage: false,
        fetchNextPage,
    });
    return fetchNextPage;
};

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
    listing([]);
});

describe("WizardInputStage multi-file arity", () => {
    it("starts with an editable list and an Add control rather than a fixed single row", async () => {
        renderStage();
        expect(screen.getByText(/No input files added yet/)).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Add Input File" })).toBeInTheDocument();
        expect(screen.getByRole("button", { name: "Add files…" })).toBeInTheDocument();
    });

    it("adds a picker row per click, so several files can be selected", async () => {
        const { latest } = renderStage();
        const add = screen.getByRole("button", { name: "Add Input File" });
        await userEvent.click(add);
        await waitFor(() => expect(rows()).toHaveLength(1));
        await userEvent.click(screen.getByRole("button", { name: "Add Input File" }));
        await waitFor(() => expect(rows()).toHaveLength(2));
        expect(latest()).toHaveLength(2);
        // A new row asks for an explicit file; the whole asset is one option of its picker.
        expect(latest()[1].relativeFileKey).toBe("");
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

    it("lists each complete entry with its own database and asset, so a selection can span assets", () => {
        // The load-bearing assertion for "multiple files over same asset or multiple assets": each
        // entry holds its own databaseId/assetId, and the list says so per row rather than hoisting
        // one asset over the selection.
        renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db2", assetId: "asset-c", relativeFileKey: "/c-one.glb" },
        ]);
        expect(screen.getByTestId("input-file-count")).toHaveTextContent("2 files across 2 assets");
        const texts = listRowTexts();
        expect(texts).toHaveLength(2);
        expect(texts[0]).toContain("db1 / asset-a");
        expect(texts[0]).toContain("/a-one.glb");
        expect(texts[1]).toContain("db2 / asset-c");
        expect(texts[1]).toContain("/c-one.glb");
        // Complete entries are rows, not pickers.
        expect(screen.queryAllByLabelText("File")).toHaveLength(0);
    });

    it("edits only the row that was opened", async () => {
        const { latest } = renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
        ]);
        await userEvent.click(editButtons()[1]);
        // The opened row is the only picker; the other stays a list row.
        expect(screen.getAllByLabelText("File")).toHaveLength(1);
        expect(listRows()).toHaveLength(1);
        await userEvent.click(screen.getByLabelText("File"));
        await userEvent.click(await screen.findByRole("option", { name: "/a-two.glb" }));
        expect(latest().map((f) => f.relativeFileKey)).toEqual(["/a-one.glb", "/a-two.glb"]);
    });

    it("lets a new row select a second file of the SAME asset", async () => {
        const { latest } = renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "" },
        ]);
        await userEvent.click(screen.getByLabelText("File"));
        await userEvent.click(await screen.findByRole("option", { name: "/a-two.glb" }));
        const files = latest();
        expect(files.every((f) => f.assetId === "asset-a")).toBe(true);
        expect(files.map((f) => f.relativeFileKey)).toEqual(["/a-one.glb", "/a-two.glb"]);
    });

    it("keeps a row open once its file is picked, offers its version defaulting to Latest, and folds it on Done", async () => {
        const { latest } = renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "" },
        ]);
        await userEvent.click(screen.getByLabelText("File"));
        await userEvent.click(await screen.findByRole("option", { name: "/a-one.glb" }));
        // Still a picker row: the version can be pinned before the row folds into the list.
        const version = screen.getByLabelText("File version") as HTMLSelectElement;
        expect(version.value).toBe("");
        await userEvent.selectOptions(version, "/a-one.glb-v1");
        expect(latest()[0].versionId).toBe("/a-one.glb-v1");

        await userEvent.click(screen.getByRole("button", { name: "Done" }));
        expect(screen.queryByLabelText("File")).not.toBeInTheDocument();
        expect(listRowTexts()[0]).toContain("/a-one.glb");
        expect(listRowTexts()[0]).toContain("v /a-one.g");
    });

    it("scopes an opened row's version list to that row's own file and opens none for listed rows", async () => {
        // A list row opens no request; the row opened for editing loads ITS file's history, not the
        // asset's — the bug that made every row in an asset show the same (asset-scoped) options.
        renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-two.glb" },
        ]);
        expect(queries().useFileVersions).not.toHaveBeenCalled();
        await userEvent.click(editButtons()[1]);
        expect(queries().useFileVersions).toHaveBeenCalledWith("db1", "asset-a", "/a-two.glb");
        expect(queries().useFileVersions).not.toHaveBeenCalledWith("db1", "asset-a", "/a-one.glb");
        const options = Array.from(
            screen.getByLabelText("File version").querySelectorAll("option")
        ).map((o) => o.getAttribute("value"));
        expect(options).toContain("/a-two.glb-v2");
        expect(options).not.toContain("/a-one.glb-v2");
    });

    it("pins a version on one row without touching the other", async () => {
        const { latest } = renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-two.glb" },
        ]);
        await userEvent.click(editButtons()[1]);
        await userEvent.selectOptions(screen.getByLabelText("File version"), "/a-two.glb-v1");
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

/**
 * A row's databaseId must be an ASSET database the picker can display.
 *
 * The wizard's own `databaseId` is the SCOPE it was opened in, which on the workflows page and the
 * global executions board is "GLOBAL" — the shared pipeline/workflow catalog, not an asset database.
 * Seeding it into a row left the Database select blank (no option matches it) while the Asset picker
 * was enabled and full of assets, so the user picked one and launched against a database that does not
 * exist. All shipped workflows are GLOBAL, so this is the primary discovery path.
 */
describe("WizardInputStage row database seeding", () => {
    const globalDatabases = () =>
        queries().useDatabases.mockReturnValue({
            data: [{ databaseId: "db1" }, { databaseId: "db2" }, { databaseId: "GLOBAL" }],
        });

    it("seeds an added row with no database when the wizard scope is GLOBAL", async () => {
        globalDatabases();
        const seen: ExecuteInputFile[][] = [];
        const Harness: React.FC = () => {
            const [files, setFiles] = React.useState<ExecuteInputFile[]>([]);
            return (
                <WizardInputStage
                    workflow={multiWorkflow()}
                    databaseId="GLOBAL"
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
        await userEvent.click(screen.getByRole("button", { name: "Add Input File" }));
        expect(seen[seen.length - 1][0].databaseId).toBe("");
        // The blank value is what the Database select can display, and it keeps the Asset picker
        // disabled until a real database is chosen.
        await waitFor(() =>
            expect((screen.getByLabelText("Database") as HTMLSelectElement).value).toBe("")
        );
        expect(screen.getByLabelText("Asset")).toBeDisabled();
    });

    it("still seeds a real database scope into an added row", async () => {
        const { latest } = renderStage();
        await userEvent.click(screen.getByRole("button", { name: "Add Input File" }));
        expect(latest()[0].databaseId).toBe("db1");
    });

    it("prefers the preset asset's database over the GLOBAL scope", async () => {
        globalDatabases();
        const seen: ExecuteInputFile[][] = [];
        const Harness: React.FC = () => {
            const [files, setFiles] = React.useState<ExecuteInputFile[]>([]);
            return (
                <WizardInputStage
                    workflow={multiWorkflow()}
                    databaseId="GLOBAL"
                    presetAsset={{ databaseId: "db2", assetId: "asset-c" }}
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
        await userEvent.click(screen.getByRole("button", { name: "Add Input File" }));
        expect(seen[seen.length - 1][0]).toEqual(
            expect.objectContaining({ databaseId: "db2", assetId: "asset-c" })
        );
    });

    it("seeds no database into the arity-one fallback row under a GLOBAL scope", () => {
        globalDatabases();
        render(
            <WizardInputStage
                workflow={
                    {
                        ...multiWorkflow(),
                        systemConfig: { inputFileArity: "one" },
                    } as Workflow
                }
                databaseId="GLOBAL"
                inputFiles={[]}
                onInputFilesChange={jest.fn()}
                onOutputAssetIdChange={jest.fn()}
                onOutputDatabaseIdChange={jest.fn()}
                onOutputPathPrefixChange={jest.fn()}
            />
        );
        expect((screen.getByLabelText("Database") as HTMLSelectElement).value).toBe("");
        expect(screen.getByLabelText("Asset")).toBeDisabled();
    });
});

/**
 * The file pickers filter on the RESOLVED restrictions, not the workflow's alone.
 *
 * A workflow that restricts nothing but whose pipelines do would otherwise offer files the chain
 * rejects: the picker offered `/notes.txt`, the validation panel below it then said the pipeline's
 * filters exclude every selected input, and the picker's own "N files hidden" note never appeared
 * because it had hidden nothing.
 */
describe("WizardInputStage resolved input-file filters", () => {
    const stepAllowing = (allow: string[]) => [
        { label: "Pipeline 1", systemConfig: { inputFileFilters: { allow } } },
    ];

    it("hides a file the PIPELINE's filters reject even when the workflow restricts nothing", async () => {
        queries().useAssetFileSearch.mockReturnValue({
            data: {
                items: ["/a-one.glb", "/notes.txt"].map((p) => ({
                    fileName: p.slice(1),
                    key: p,
                    relativePath: p,
                    isFolder: false,
                })),
                total: 2,
            },
            isFetching: false,
        });
        renderStage(
            [{ databaseId: "db1", assetId: "asset-a", relativeFileKey: "" }],
            multiWorkflow(),
            {
                pipelineConstraints: stepAllowing(["*.glb"]),
            }
        );
        await userEvent.click(screen.getByLabelText("File"));
        expect(await screen.findByRole("option", { name: "/a-one.glb" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: "/notes.txt" })).not.toBeInTheDocument();
    });

    it("hides a file a TEMPLATE's overrides reject", async () => {
        renderStage(
            [{ databaseId: "db1", assetId: "asset-a", relativeFileKey: "" }],
            multiWorkflow(),
            {
                pipelineConstraints: [
                    {
                        label: "Pipeline 1",
                        systemConfig: { inputFileFilters: { allow: [] } },
                        templateOverrides: { inputFileFilters: { allow: ["*a-one*"] } },
                    },
                ],
            }
        );
        await userEvent.click(screen.getByLabelText("File"));
        expect(await screen.findByRole("option", { name: "/a-one.glb" })).toBeInTheDocument();
        expect(screen.queryByRole("option", { name: "/a-two.glb" })).not.toBeInTheDocument();
    });

    it("badges each listed entry against the same resolved filters", () => {
        renderStage(
            [
                { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/a-one.glb" },
                { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/notes.txt" },
            ],
            multiWorkflow(),
            { pipelineConstraints: stepAllowing(["*.glb"]) }
        );
        const texts = listRowTexts();
        expect(texts[0]).toContain("Compatible");
        expect(texts[0]).not.toContain("Not compatible");
        expect(texts[1]).toContain("Not compatible");
        expect(screen.getByTestId("input-file-count")).toHaveTextContent("1 file not compatible");
    });
});

/**
 * folderAllowed is a gate the stage resolves and must pass on.
 *
 * The backend accepts a trailing-slash key wherever the resolved scope grants it (`_scope_errors`), and
 * the authoring UI offers folderAllowed as an independent checkbox — so a workflow with
 * `{folderAllowed: true, wholeAssetAllowed: false}` is a legitimate configuration whose only reachable
 * path was the file manager's Automation action.
 */
describe("WizardInputStage folder selections", () => {
    const scoped = (assetScope: Record<string, boolean>) => multiWorkflow({ assetScope });

    it("offers each folder the asset's files sit in", async () => {
        queries().useAssetFileSearch.mockReturnValue({
            data: {
                items: ["/models/pump.glb", "/textures/skin.png"].map((p) => ({
                    fileName: p.split("/").pop(),
                    key: p,
                    relativePath: p,
                    isFolder: false,
                })),
                total: 2,
            },
            isFetching: false,
        });
        renderStage(
            [{ databaseId: "db1", assetId: "asset-a", relativeFileKey: "" }],
            scoped({ folderAllowed: true })
        );
        await userEvent.click(screen.getByLabelText("File"));
        const list = await screen.findByRole("listbox");
        const labels = Array.from(list.querySelectorAll('[role="option"]')).map(
            (o) => o.textContent || ""
        );
        expect(labels.some((l) => l.startsWith("/models/") && !l.includes("pump"))).toBe(true);
        expect(labels.some((l) => l.startsWith("/textures/") && !l.includes("skin"))).toBe(true);
    });

    it("offers no folder when the resolved scope does not allow one", async () => {
        queries().useAssetFileSearch.mockReturnValue({
            data: {
                items: [
                    {
                        fileName: "pump.glb",
                        key: "/models/pump.glb",
                        relativePath: "/models/pump.glb",
                        isFolder: false,
                    },
                ],
                total: 1,
            },
            isFetching: false,
        });
        renderStage(
            [{ databaseId: "db1", assetId: "asset-a", relativeFileKey: "" }],
            multiWorkflow()
        );
        await userEvent.click(screen.getByLabelText("File"));
        const list = await screen.findByRole("listbox");
        const labels = Array.from(list.querySelectorAll('[role="option"]')).map(
            (o) => o.textContent || ""
        );
        expect(labels).toEqual(["/models/pump.glb"]);
    });

    it("lists a whole-asset and a folder entry as rows, badged against the resolved scope", () => {
        renderStage(
            [
                { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/" },
                { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/docs/" },
            ],
            scoped({ folderAllowed: true, wholeAssetAllowed: false })
        );
        const texts = listRowTexts();
        expect(texts[0]).toContain("Whole asset (all files)");
        expect(texts[0]).toContain("Not compatible");
        expect(texts[1]).toContain("/docs/ (folder)");
        expect(texts[1]).not.toContain("Not compatible");
    });
});

/**
 * A large selection opens no version request at all.
 *
 * The file manager's Automation action can carry hundreds of files into this step; each row's version
 * list is its own `fileInfo?includeVersions=true` call, which saturates the browser's connection pool
 * and delays first paint for lists nobody opens. A listed row therefore carries no hook; the history
 * is fetched for the one row opened for editing, and only when its selector is reached.
 */
describe("WizardInputStage version-request fan-out", () => {
    /** The distinct rows whose version query is actually enabled (a databaseId was passed). */
    const enabledVersionRows = () => [
        ...new Set(
            queries()
                .useFileVersions.mock.calls.filter((c: any[]) => !!c[0])
                .map((c: any[]) => c[2])
        ),
    ];

    it("opens none for listed rows, small selection or large", () => {
        renderStage(rowsFor(3));
        expect(enabledVersionRows()).toEqual([]);
        expect(queries().useFileVersions).not.toHaveBeenCalled();
    });

    it("opens none up front for a large selection, and mounts a window of it", () => {
        renderStage(rowsFor(40));
        expect(enabledVersionRows()).toEqual([]);
        expect(screen.getByTestId("input-file-count")).toHaveTextContent("40 files across 1 asset");
        // Windowed: far fewer rows are in the DOM than the selection holds.
        expect(listRows().length).toBeLessThan(40);
        expect(listRows().length).toBeGreaterThan(0);
    });

    it("loads one row's history when it is opened for editing and its selector is reached", async () => {
        renderStage(rowsFor(40));
        await userEvent.click(editButtons()[7]);
        // A large selection defers the request past the row's mount, to the control itself.
        expect(enabledVersionRows()).toEqual([]);
        await userEvent.click(screen.getByLabelText("File version"));
        await waitFor(() => expect(enabledVersionRows()).toEqual(["/f7.glb"]));
    });

    it("keeps the request eager for a small selection once a row is opened", async () => {
        renderStage(rowsFor(3));
        await userEvent.click(editButtons()[0]);
        expect(enabledVersionRows()).toEqual(["/f0.glb"]);
    });
});

/**
 * The selected-files list at the scale the file manager can preset: hundreds of entries in one
 * windowed list, with the count, the cap, a filter and a guarded Clear all.
 */
describe("WizardInputStage selected files at scale", () => {
    it("mounts a window of rows for 500 preset files and states the count", () => {
        renderStage(rowsFor(500));
        expect(screen.getByTestId("input-file-count")).toHaveTextContent(
            "500 files across 1 asset"
        );
        expect(screen.getByTestId("input-file-count")).toHaveTextContent("max 1000");
        expect(listRows().length).toBeLessThan(100);
        // No picker rows: the preset selection arrives complete.
        expect(screen.queryAllByLabelText("File")).toHaveLength(0);
        expect(screen.queryByText(/No input files added yet/)).not.toBeInTheDocument();
    });

    it("says when the selection is over the execution cap", () => {
        renderStage(rowsFor(1001));
        expect(screen.getByTestId("input-file-count")).toHaveTextContent("over the 1000 limit");
    });

    it("counts across assets", () => {
        renderStage([...rowsFor(300, "asset-a"), ...rowsFor(200, "asset-b")]);
        expect(screen.getByTestId("input-file-count")).toHaveTextContent(
            "500 files across 2 assets"
        );
    });

    it("narrows the visible rows with the filter", async () => {
        renderStage(rowsFor(500));
        await userEvent.type(screen.getByLabelText("Filter selected files"), "/f42");
        // /f42.glb and /f420.glb … /f429.glb.
        expect(screen.getByText("Showing 11 of 500")).toBeInTheDocument();
        expect(listRows()).toHaveLength(11);
        expect(listRowTexts().every((t) => t.includes("/f42"))).toBe(true);
    });

    it("removes one row from the list", async () => {
        const { latest } = renderStage(rowsFor(3));
        await userEvent.click(screen.getAllByRole("button", { name: "Remove" })[1]);
        expect(latest().map((f) => f.relativeFileKey)).toEqual(["/f0.glb", "/f2.glb"]);
    });

    it("clears everything only after confirmation", async () => {
        const { seen, latest } = renderStage(rowsFor(5));
        await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
        expect(screen.getByText("Remove all 5?")).toBeInTheDocument();
        await userEvent.click(screen.getByRole("button", { name: "Keep" }));
        expect(seen).toHaveLength(0);
        expect(listRows()).toHaveLength(5);

        await userEvent.click(screen.getByRole("button", { name: "Clear all" }));
        await userEvent.click(screen.getByRole("button", { name: "Yes, clear" }));
        expect(latest()).toEqual([]);
        expect(screen.getByText(/No input files added yet/)).toBeInTheDocument();
    });
});

/**
 * "Add files…" opens the bulk picker; what it adds lands in the list once, deduplicated against the
 * selection. The picker's own behaviour is covered in BulkFilePicker.test.tsx.
 */
describe("WizardInputStage bulk picker", () => {
    const openPicker = async () => {
        await userEvent.click(screen.getByRole("button", { name: "Add files…" }));
        const dialog = await screen.findByRole("dialog", { name: "Add input files" });
        // The stage's database seeds the picker; the asset is chosen here.
        expect((within(dialog).getByLabelText("Database") as HTMLSelectElement).value).toBe("db1");
        await userEvent.click(within(dialog).getByRole("button", { name: "Asset" }));
        await userEvent.click(await screen.findByRole("option", { name: /Pump A/ }));
        return dialog;
    };

    it("appends the checked files once and reports what was added", async () => {
        listing(["/p1.glb", "/p2.glb", "/p3.glb"]);
        const { latest } = renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/p1.glb" },
        ]);
        const dialog = await openPicker();
        // The entry already in the selection is marked and not offered again.
        expect(within(dialog).getByLabelText("/p1.glb")).toBeDisabled();
        expect(within(dialog).getByText("Already selected")).toBeInTheDocument();
        await userEvent.click(within(dialog).getByRole("button", { name: "Select all shown" }));
        expect(within(dialog).getByText("2 files selected")).toBeInTheDocument();
        await userEvent.click(within(dialog).getByRole("button", { name: "Add 2 files" }));

        expect(latest().map((f) => f.relativeFileKey)).toEqual(["/p1.glb", "/p2.glb", "/p3.glb"]);
        expect(screen.getByText("Added 2 files.")).toBeInTheDocument();
        expect(screen.queryByRole("dialog", { name: "Add input files" })).not.toBeInTheDocument();
        expect(screen.getByTestId("input-file-count")).toHaveTextContent("3 files across 1 asset");
    });

    it("adds pasted keys for the chosen asset, skipping ones already selected", async () => {
        const { latest } = renderStage([
            { databaseId: "db1", assetId: "asset-a", relativeFileKey: "/p1.glb" },
        ]);
        const dialog = await openPicker();
        await userEvent.click(within(dialog).getByRole("tab", { name: "Paste keys" }));
        await userEvent.type(
            within(dialog).getByLabelText("Relative file keys"),
            "scans/x.glb{enter}/p1.glb{enter}/y.txt"
        );
        expect(within(dialog).getByText(/3 keys · 1 already selected/)).toBeInTheDocument();
        await userEvent.click(within(dialog).getByRole("button", { name: "Add 2 keys" }));
        expect(latest().map((f) => f.relativeFileKey)).toEqual([
            "/p1.glb",
            "/scans/x.glb",
            "/y.txt",
        ]);
        expect(latest()[1]).toEqual(
            expect.objectContaining({ databaseId: "db1", assetId: "asset-a" })
        );
    });
});

/**
 * The output path prefix's explanation belongs behind an info icon.
 *
 * It is four sentences of reference material for one optional field; inline it dominated the Output
 * section of the step. The examples matter as much as the prose — the date and execution id are the
 * common way to separate one run's output from another's, so both must be shown.
 */
describe("WizardInputStage output path prefix help", () => {
    // Output controls only render when the workflow writes to an asset and allows an override.
    const outputWorkflow = () =>
        multiWorkflow({
            outputTarget: { locationType: "asset", allowOverride: true },
        });

    it("offers the explanation from an info icon, not as a paragraph", async () => {
        renderStage([], outputWorkflow());
        expect(await screen.findByLabelText("Output path prefix help")).toBeInTheDocument();
    });

    it("keeps the field itself present and labelled", async () => {
        renderStage([], outputWorkflow());
        expect(await screen.findByLabelText("Output path prefix")).toBeInTheDocument();
    });

    it("does not print the long explanation inline", async () => {
        // The regression this guards: reverting to a paragraph under the input.
        renderStage([], outputWorkflow());
        await screen.findByLabelText("Output path prefix");
        expect(
            screen.queryByText(/Inserted immediately before each output file/i)
        ).not.toBeInTheDocument();
    });

    it("shows the date and execution-id examples when opened", async () => {
        renderStage([], outputWorkflow());
        await userEvent.hover(await screen.findByLabelText("Output path prefix help"));

        // getAllBy: Radix renders the tooltip content plus a visually-hidden a11y copy, so each
        // example legitimately appears more than once.
        //
        // Asserted as the STANDALONE date example (`/{{jobStartDate}}/`), not merely the tag appearing
        // somewhere: it also occurs inside the combined `/{{jobStartDate}}/{{executionId}}/` form, so
        // a loose match still passed when the standalone example was removed.
        await waitFor(() =>
            expect(screen.getAllByText("/{{jobStartDate}}/").length).toBeGreaterThan(0)
        );
        expect(screen.getAllByText("/{{executionId}}/").length).toBeGreaterThan(0);
        expect(screen.getAllByText("/{{jobStartDate}}/{{executionId}}/").length).toBeGreaterThan(0);
    });

    it("still explains the trailing-slash behaviour", async () => {
        // The subtlest part of the field: without a trailing / the prefix joins onto the FILE NAME.
        renderStage([], outputWorkflow());
        await userEvent.hover(await screen.findByLabelText("Output path prefix help"));
        await waitFor(() =>
            expect(screen.getAllByText(/joins onto the file name/i).length).toBeGreaterThan(0)
        );
    });
});

/**
 * The requirements — including the "a template can still narrow these" caveat — are stated once, in
 * the strip above the step (RequirementsStrip.test.tsx pins the wording; ExecuteWizard.test.tsx pins
 * that `templateKnown` reaches it). The step itself must not repeat them, or the dialog says the same
 * thing twice one line apart.
 */
describe("WizardInputStage template caveat", () => {
    const constraints = (templateKnown?: boolean) => [
        {
            label: 'Pipeline "pipe1"',
            systemConfig: { inputFileArity: "multi", requireTemplate: true } as any,
            templateOverrides: undefined,
            templateKnown,
        },
    ];

    it("no longer renders the caveat on the step itself; the strip above it owns the wording", () => {
        // The one shape that DID render it here before: a required template still unchosen.
        renderStage([], multiWorkflow(), { pipelineConstraints: constraints(false) });

        expect(screen.queryByText(/template can narrow these further/i)).not.toBeInTheDocument();
        expect(screen.queryByText(/may narrow once a template is chosen/)).not.toBeInTheDocument();
        expect(screen.queryByText("What this workflow accepts")).not.toBeInTheDocument();
    });
});
