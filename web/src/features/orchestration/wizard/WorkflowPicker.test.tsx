/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import WorkflowPicker, {
    pipelineKeyFor,
    selectionErrorsFor,
    workflowCategories,
    workflowKey,
} from "./WorkflowPicker";
import type { Pipeline, Workflow } from "../types";

const CONVERT: Workflow = {
    databaseId: "db1",
    workflowId: "wf1",
    workflowName: "Convert",
    description: "Turns CAD into GLB.",
    enabled: true,
    archived: false,
    specifiedPipelines: [{ pipelineDatabaseId: "GLOBAL", pipelineId: "conv" }],
    systemConfig: { inputFileArity: "one", outputTarget: { locationType: "asset" } },
};

const THUMBS: Workflow = {
    ...CONVERT,
    workflowId: "wf2",
    workflowName: "Thumbnails",
    description: "Renders previews.",
    specifiedPipelines: [],
};

const PIPELINES: Record<string, Pipeline> = {
    "GLOBAL:conv": {
        databaseId: "GLOBAL",
        pipelineId: "conv",
        pipelineName: "Converter",
        executionConfig: { executionType: "Lambda" },
        systemConfig: { inputFileFilters: { allow: ["*.glb", "*.obj"] } },
    } as Pipeline,
};

const renderPicker = (over: Record<string, any> = {}) => {
    const onSelect = jest.fn();
    render(
        <WorkflowPicker
            workflows={[CONVERT, THUMBS]}
            pipelinesByKey={PIPELINES}
            selectedKey=""
            onSelect={onSelect}
            {...over}
        />
    );
    return onSelect;
};

describe("WorkflowPicker", () => {
    it("lists every workflow as a listbox row with its database and requirements", () => {
        renderPicker();
        const rows = screen.getAllByRole("option");
        expect(rows).toHaveLength(2);
        rows.forEach((row) => expect(row.parentElement).toBe(screen.getByRole("listbox")));
        expect(rows[0]).toHaveTextContent("Convert");
        expect(rows[0]).toHaveTextContent("db1");
        expect(rows[0]).toHaveTextContent("Turns CAD into GLB.");
        // The compact summary, resolved through the referenced pipeline.
        expect(rows[0]).toHaveTextContent("2 file types · 1 file · writes to an asset");
        // Compact: the patterns themselves stay in the hover, not on the row.
        expect(screen.queryByText("*.glb")).not.toBeInTheDocument();
    });

    it("filters by name, id, database and description as you type", async () => {
        renderPicker();
        const search = screen.getByLabelText("Workflow");
        expect(search).toHaveAttribute("placeholder", "Type to search…");
        await userEvent.type(search, "thumb");
        expect(screen.getAllByRole("option")).toHaveLength(1);
        expect(screen.getByRole("option", { name: /Thumbnails/ })).toBeInTheDocument();
        await userEvent.clear(search);
        await userEvent.type(search, "previews");
        expect(screen.getAllByRole("option")).toHaveLength(1);
        await userEvent.clear(search);
        await userEvent.type(search, "zzz");
        expect(screen.queryAllByRole("option")).toHaveLength(0);
        expect(screen.getByText("No workflows match “zzz”.")).toBeInTheDocument();
    });

    it("reports the clicked row's key and marks the selected row", async () => {
        const onSelect = renderPicker();
        await userEvent.click(screen.getByRole("option", { name: /Thumbnails/ }));
        expect(onSelect).toHaveBeenCalledWith("db1:wf2");
        renderPicker({ selectedKey: "db1:wf2" });
        const selected = screen.getAllByRole("option", { name: /Thumbnails/ });
        expect(selected[selected.length - 1]).toHaveAttribute("aria-selected", "true");
    });

    it("selects with the keyboard too", async () => {
        const onSelect = renderPicker();
        const row = screen.getByRole("option", { name: /Convert/ });
        row.focus();
        await userEvent.keyboard("{Enter}");
        expect(onSelect).toHaveBeenCalledWith("db1:wf1");
    });

    it("walks the rows with the arrow keys from a single tab stop", async () => {
        renderPicker();
        const [first, second] = screen.getAllByRole("option");
        // One row is in the tab order; the rest are reached with the arrow keys.
        expect(first).toHaveAttribute("tabindex", "0");
        expect(second).toHaveAttribute("tabindex", "-1");

        await userEvent.click(screen.getByLabelText("Workflow"));
        await userEvent.tab();
        expect(first).toHaveFocus();
        await userEvent.keyboard("{ArrowDown}");
        expect(second).toHaveFocus();
        // The ends are clamped rather than wrapped.
        await userEvent.keyboard("{ArrowDown}");
        expect(second).toHaveFocus();
        await userEvent.keyboard("{Home}");
        expect(first).toHaveFocus();
        await userEvent.keyboard("{End}");
        expect(second).toHaveFocus();
        await userEvent.keyboard("{ArrowUp}");
        expect(first).toHaveFocus();
        // Tab leaves the list instead of visiting every row.
        await userEvent.tab();
        expect(screen.getByRole("listbox").contains(document.activeElement)).toBe(false);
    });

    it("gives the tab stop to the selected row, or to the first row once the selection is filtered out", async () => {
        renderPicker({ selectedKey: "db1:wf2" });
        expect(screen.getByRole("option", { name: /Thumbnails/ })).toHaveAttribute("tabindex", "0");
        expect(screen.getByRole("option", { name: /Convert/ })).toHaveAttribute("tabindex", "-1");
        await userEvent.type(screen.getByLabelText("Workflow"), "conv");
        const rows = screen.getAllByRole("option");
        expect(rows).toHaveLength(1);
        expect(rows[0]).toHaveAttribute("tabindex", "0");
    });

    it("keeps every control outside the option rows and one detail icon beside the selection", () => {
        renderPicker({ selectedKey: "db1:wf1" });
        screen
            .getAllByRole("option")
            .forEach((row) => expect(row.querySelector("button, a, input, [tabindex]")).toBeNull());
        // The pattern detail stays reachable — from ONE focusable icon, outside the list.
        const icons = screen.getAllByRole("button", { name: /Which files this workflow accepts/i });
        expect(icons).toHaveLength(1);
        expect(screen.getByRole("listbox").contains(icons[0])).toBe(false);
        // Each row still carries the detail as its hover text, and never as visible content.
        expect(screen.getByRole("option", { name: /Convert/ })).toHaveAttribute(
            "title",
            expect.stringContaining("*.glb")
        );
        expect(screen.queryByText("*.glb")).not.toBeInTheDocument();
    });

    it("says what it will run on and badges each row's compatibility", () => {
        renderPicker({
            presetInputFiles: [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/notes.txt" }],
        });
        expect(screen.getByText(/Running on 1 selection:/)).toBeInTheDocument();
        expect(screen.getByRole("option", { name: /Convert/ })).toHaveTextContent("Not compatible");
        // Thumbnails references no pipeline, so nothing narrows the .txt away.
        expect(screen.getByRole("option", { name: /Thumbnails/ })).toHaveTextContent("Compatible");
        // No row is selected yet, so nothing is an alert.
        expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    });

    it("explains an incompatible selected row in an alert", () => {
        renderPicker({
            selectedKey: "db1:wf1",
            presetInputFiles: [{ databaseId: "db1", assetId: "a1", relativeFileKey: "/notes.txt" }],
        });
        const alert = screen.getByRole("alert");
        expect(alert).toHaveTextContent("This workflow cannot run on the current selection:");
        expect(alert.querySelectorAll("li").length).toBeGreaterThan(0);
    });

    it("truncates a long selection line", () => {
        renderPicker({
            presetInputFiles: ["/a.glb", "/b.glb", "/c.glb", "/d.glb"].map((k) => ({
                databaseId: "db1",
                assetId: "a1",
                relativeFileKey: k,
            })),
        });
        expect(screen.getByText(/Running on 4 selections/)).toBeInTheDocument();
        expect(screen.getByText(/\+1 more/)).toBeInTheDocument();
    });

    it("shows an empty state when there is nothing to choose", () => {
        renderPicker({ workflows: [] });
        expect(screen.getByText("No workflows available.")).toBeInTheDocument();
    });
});

describe("WorkflowPicker category chips", () => {
    const pipeline = (id: string, category?: string, databaseId = "GLOBAL"): Pipeline =>
        ({
            databaseId,
            pipelineId: id,
            pipelineName: id,
            category,
            executionConfig: { executionType: "Lambda" },
        } as Pipeline);

    const CATALOG: Record<string, Pipeline> = {
        "GLOBAL:conv": pipeline("conv", "Conversion"),
        "GLOBAL:conv2": pipeline("conv2", "Conversion"),
        "GLOBAL:prev": pipeline("prev", "Preview"),
        "GLOBAL:gen": pipeline("gen", "GenAI"),
        "GLOBAL:wfm": pipeline("wfm", "WFM"),
        "GLOBAL:vla": pipeline("vla", "VLA Training"),
        "GLOBAL:blank": pipeline("blank", "  "),
        "GLOBAL:none": pipeline("none"),
        "db1:local": pipeline("local", "3D Reconstruction", "db1"),
    };

    const withSteps = (workflowId: string, ids: string[], databaseId = "db1"): Workflow => ({
        ...CONVERT,
        databaseId,
        workflowId,
        workflowName: workflowId,
        description: undefined,
        specifiedPipelines: ids.map((pipelineId) => ({
            pipelineDatabaseId: "GLOBAL",
            pipelineId,
        })),
    });

    const chipsOf = (row: HTMLElement) =>
        within(row)
            .queryAllByTestId("workflow-category")
            .map((chip) => chip.textContent);

    it("shows each pipeline's category as a chip after the database chip", () => {
        renderPicker({
            workflows: [withSteps("Chain", ["conv", "prev"])],
            pipelinesByKey: CATALOG,
        });
        const row = screen.getByRole("option", { name: /Chain/ });
        expect(chipsOf(row)).toEqual(["Conversion", "Preview"]);
        const dbChip = within(row).getByText("db1");
        const [first] = within(row).getAllByTestId("workflow-category");
        expect(
            dbChip.compareDocumentPosition(first) & Node.DOCUMENT_POSITION_FOLLOWING
        ).toBeTruthy();
        // The same chip style as the database badge.
        expect(first.className).toContain("rounded-full");
        expect(first.className).toContain("bg-surface-secondary");
        expect(first.className).toContain("text-[11px]");
        expect(screen.queryByTestId("workflow-category-overflow")).not.toBeInTheDocument();
    });

    it("deduplicates repeated categories and skips blank, missing and unknown pipelines", () => {
        renderPicker({
            workflows: [withSteps("Dupes", ["conv", "conv2", "blank", "none", "missing", "prev"])],
            pipelinesByKey: CATALOG,
        });
        expect(chipsOf(screen.getByRole("option", { name: /Dupes/ }))).toEqual([
            "Conversion",
            "Preview",
        ]);
    });

    it("caps the visible chips at three and folds the rest into a +N chip", () => {
        renderPicker({
            workflows: [withSteps("Long", ["conv", "prev", "gen", "wfm", "vla"])],
            pipelinesByKey: CATALOG,
        });
        const row = screen.getByRole("option", { name: /Long/ });
        expect(chipsOf(row)).toEqual(["Conversion", "Preview", "GenAI"]);
        const overflow = within(row).getByTestId("workflow-category-overflow");
        expect(overflow).toHaveTextContent("+2");
        expect(overflow).toHaveAttribute("title", "WFM, VLA Training");
        expect(within(row).queryByText("WFM")).not.toBeInTheDocument();
        // Still no control inside the option row.
        expect(row.querySelector("button, a, input, [tabindex]")).toBeNull();
    });

    it("matches the search against category text, case-insensitively, including folded ones", async () => {
        renderPicker({
            workflows: [
                withSteps("Chain", ["conv", "prev"]),
                withSteps("Long", ["conv", "prev", "gen", "wfm", "vla"]),
                THUMBS,
            ],
            pipelinesByKey: CATALOG,
        });
        const search = screen.getByLabelText("Workflow");
        await userEvent.type(search, "genai");
        expect(screen.getAllByRole("option")).toHaveLength(1);
        expect(screen.getByRole("option", { name: /Long/ })).toBeInTheDocument();
        await userEvent.clear(search);
        await userEvent.type(search, "vla train");
        expect(screen.getAllByRole("option")).toHaveLength(1);
        await userEvent.clear(search);
        // "CONVERSION" appears in no name or description, so only the category can match it.
        await userEvent.type(search, "CONVERSION");
        expect(screen.getAllByRole("option")).toHaveLength(2);
        await userEvent.clear(search);
        await userEvent.type(search, "thumb");
        expect(screen.getAllByRole("option")).toHaveLength(1);
    });

    it("keys a step by its own database, falling back to the workflow's", () => {
        const wf = { ...CONVERT, databaseId: "db1" };
        expect(pipelineKeyFor(wf, { pipelineDatabaseId: "GLOBAL", pipelineId: "conv" })).toBe(
            "GLOBAL:conv"
        );
        expect(pipelineKeyFor(wf, { pipelineId: "local" })).toBe("db1:local");
        expect(
            workflowCategories(
                { ...wf, specifiedPipelines: [{ pipelineId: "local" }, { pipelineId: "conv" }] },
                CATALOG
            )
        ).toEqual(["3D Reconstruction"]);
    });
});

describe("selectionErrorsFor", () => {
    it("is empty without a preset selection and names the pipeline otherwise", () => {
        expect(selectionErrorsFor(CONVERT, PIPELINES)).toEqual([]);
        const errors = selectionErrorsFor(CONVERT, PIPELINES, [
            { databaseId: "db1", assetId: "a1", relativeFileKey: "/notes.txt" },
        ]);
        expect(errors.some((e) => e.includes('Pipeline "conv"'))).toBe(true);
    });

    it("keys a workflow by database and id", () => {
        expect(workflowKey(CONVERT)).toBe("db1:wf1");
    });
});
