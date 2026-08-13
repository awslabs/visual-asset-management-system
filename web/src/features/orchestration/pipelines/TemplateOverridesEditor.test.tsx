/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A template's metadataInputs override must cover every key the pipeline systemConfig has, otherwise
 * toggling the override on silently drops the missing one: the override REPLACES the pipeline's map
 * per key, so a seed that omits a key writes an override the backend then reads through its own
 * defaults, which is not what the editor showed.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import TemplateOverridesEditor from "./TemplateOverridesEditor";

/** Renders the editor as a controlled form so a toggle reflects back like the real template form. */
function renderEditor(
    initial: Record<string, any> = {},
    inherited: {
        inheritedAssetScope?: Record<string, any>;
        inheritedArity?: any;
        inheritedFilters?: { allow?: string[]; exclude?: string[] };
    } = {}
) {
    const seen: Record<string, any>[] = [];
    const Harness: React.FC = () => {
        const [value, setValue] = React.useState(initial);
        return (
            <TemplateOverridesEditor
                value={value}
                onChange={(next) => {
                    seen.push(next);
                    setValue(next);
                }}
                {...inherited}
            />
        );
    };
    render(<Harness />);
    return { latest: () => seen[seen.length - 1] };
}

const metaToggle = (label: string) =>
    screen
        .getByText(label)
        .closest("label")
        ?.querySelector("input[type=checkbox]") as HTMLInputElement;

describe("TemplateOverridesEditor metadata inputs", () => {
    it("seeds all four metadata keys when the override is switched on", async () => {
        const { latest } = renderEditor();
        await userEvent.click(screen.getByRole("checkbox", { name: /Override metadata inputs/i }));
        expect(latest().metadataInputs).toEqual({
            assetMetadata: true,
            fileMetadata: true,
            fileAttributes: true,
            databaseMetadata: true,
        });
    });

    it("offers a row for every metadata key", async () => {
        renderEditor({ metadataInputs: { assetMetadata: true } });
        expect(screen.getByText("Asset metadata")).toBeInTheDocument();
        expect(screen.getByText("File metadata")).toBeInTheDocument();
        expect(screen.getByText("File attributes")).toBeInTheDocument();
        expect(screen.getByText("Database metadata")).toBeInTheDocument();
    });

    it("orders the rows widest entity first", () => {
        // database -> asset -> file, the containment the rows describe.
        renderEditor({ metadataInputs: { assetMetadata: true } });
        const at = (label: string) =>
            Array.prototype.indexOf.call(
                document.body.querySelectorAll("*"),
                screen.getByText(label)
            );
        expect(at("Database metadata")).toBeLessThan(at("Asset metadata"));
        expect(at("Asset metadata")).toBeLessThan(at("File metadata"));
        expect(at("File metadata")).toBeLessThan(at("File attributes"));
    });

    it("shows database metadata as on for an override map that omits the key", () => {
        // The record builders default it ON, so an override saved before the key existed keeps
        // providing database metadata; rendering it unchecked would misreport the stored value.
        renderEditor({ metadataInputs: { assetMetadata: false } });
        expect(metaToggle("Database metadata")).toBeChecked();
    });

    it.each(["Asset metadata", "File metadata", "File attributes", "Database metadata"])(
        "shows %s as on for an override map that omits it",
        (label) => {
            // The same rule for all four, not databaseMetadata alone: an omitted key carries its
            // builder default (ON). Binding the raw value would render an omission as opted-out and
            // then persist that opt-out the next time any row in the block is touched.
            renderEditor({ metadataInputs: {} });
            expect(metaToggle(label)).toBeChecked();
        }
    );

    it("keeps the other three on when one key is explicitly off", () => {
        renderEditor({ metadataInputs: { fileMetadata: false } });
        expect(metaToggle("File metadata")).not.toBeChecked();
        expect(metaToggle("Asset metadata")).toBeChecked();
        expect(metaToggle("File attributes")).toBeChecked();
        expect(metaToggle("Database metadata")).toBeChecked();
    });

    it("writes the databaseMetadata key when it is toggled off", async () => {
        const { latest } = renderEditor({ metadataInputs: { assetMetadata: true } });
        await userEvent.click(metaToggle("Database metadata"));
        expect(latest().metadataInputs).toEqual({
            assetMetadata: true,
            databaseMetadata: false,
        });
    });

    it("removes the whole key when the override is switched off", async () => {
        const { latest } = renderEditor({ metadataInputs: { databaseMetadata: false } });
        await userEvent.click(screen.getByRole("checkbox", { name: /Override metadata inputs/i }));
        expect(latest()).not.toHaveProperty("metadataInputs");
    });
});

/**
 * An override REPLACES the pipeline's value for its key, so a seed that does not start from the
 * pipeline's own setting silently rewrites it: empty filter lists read as allow-all
 * (executionValidation.is_open_allow_list), and a hardcoded arity narrows a multi-file pipeline.
 */
describe("TemplateOverridesEditor inherited seeds", () => {
    it("seeds the input-file filters from the pipeline's own filters", async () => {
        const { latest } = renderEditor(
            {},
            { inheritedFilters: { allow: ["*.glb", "*.gltf"], exclude: ["*.previewFile.*"] } }
        );
        await userEvent.click(
            screen.getByRole("checkbox", { name: /Override input file filters/i })
        );
        expect(latest().inputFileFilters).toEqual({
            allow: ["*.glb", "*.gltf"],
            exclude: ["*.previewFile.*"],
        });
    });

    it("does not alias the pipeline's filter arrays into the override", async () => {
        const inheritedFilters = { allow: ["*.glb"], exclude: [] };
        const { latest } = renderEditor({}, { inheritedFilters });
        await userEvent.click(
            screen.getByRole("checkbox", { name: /Override input file filters/i })
        );
        await userEvent.type(screen.getByLabelText("Override allow filter"), "*.usd");
        await userEvent.click(screen.getAllByRole("button", { name: "Add" })[0]);
        expect(latest().inputFileFilters.allow).toEqual(["*.glb", "*.usd"]);
        // The pipeline's own list must be untouched by editing the template's copy.
        expect(inheritedFilters.allow).toEqual(["*.glb"]);
    });

    it("seeds empty filter lists when the pipeline declares none", async () => {
        const { latest } = renderEditor();
        await userEvent.click(
            screen.getByRole("checkbox", { name: /Override input file filters/i })
        );
        expect(latest().inputFileFilters).toEqual({ allow: [], exclude: [] });
    });

    it("seeds the input file count from the pipeline's arity", async () => {
        const { latest } = renderEditor({}, { inheritedArity: "multi" });
        await userEvent.click(screen.getByRole("checkbox", { name: /Override input file count/i }));
        expect(latest().inputFileArity).toBe("multi");
        expect(
            (
                screen.getByRole("combobox", {
                    name: "Override input file count",
                }) as HTMLSelectElement
            ).value
        ).toBe("multi");
    });

    it("seeds arity 'none' rather than defaulting it to one file", async () => {
        const { latest } = renderEditor({}, { inheritedArity: "none" });
        await userEvent.click(screen.getByRole("checkbox", { name: /Override input file count/i }));
        expect(latest().inputFileArity).toBe("none");
    });

    it("defaults the arity seed to one file when the pipeline declares none", async () => {
        // Matches the backend's own read of an absent inputFileArity.
        const { latest } = renderEditor();
        await userEvent.click(screen.getByRole("checkbox", { name: /Override input file count/i }));
        expect(latest().inputFileArity).toBe("one");
    });
});
