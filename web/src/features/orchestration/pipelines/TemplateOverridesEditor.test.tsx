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
function renderEditor(initial: Record<string, any> = {}) {
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
