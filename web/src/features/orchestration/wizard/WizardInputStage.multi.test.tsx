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
});

/**
 * A large selection does not open a version request per row on mount.
 *
 * The file manager's Automation action can carry hundreds of files into this step; each row's version
 * list is its own `fileInfo?includeVersions=true` call, which saturates the browser's connection pool
 * and delays first paint for lists nobody opens.
 */
describe("WizardInputStage version-request fan-out", () => {
    const rowsFor = (count: number): ExecuteInputFile[] =>
        Array.from({ length: count }, (_, i) => ({
            databaseId: "db1",
            assetId: "asset-a",
            relativeFileKey: `/f${i}.glb`,
        }));

    /** The distinct rows whose version query is actually enabled (a databaseId was passed). */
    const enabledVersionRows = () => [
        ...new Set(
            queries()
                .useFileVersions.mock.calls.filter((c: any[]) => !!c[0])
                .map((c: any[]) => c[2])
        ),
    ];

    it("keeps the requests eager for a small selection", () => {
        renderStage(rowsFor(3));
        expect(enabledVersionRows()).toHaveLength(3);
    });

    it("opens none of them up front for a large selection", () => {
        renderStage(rowsFor(40));
        expect(enabledVersionRows()).toEqual([]);
        // The selectors themselves stay present — deferral is about the request, not the control.
        expect(screen.getAllByLabelText("File version")).toHaveLength(40);
    });

    it("loads one row's history when its selector is reached", async () => {
        renderStage(rowsFor(40));
        await userEvent.click(screen.getAllByLabelText("File version")[7]);
        await waitFor(() => expect(enabledVersionRows()).toEqual(["/f7.glb"]));
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
