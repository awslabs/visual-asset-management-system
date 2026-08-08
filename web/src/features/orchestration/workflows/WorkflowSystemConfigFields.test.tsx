/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * Field ORDER is the contract here, not just presence.
 *
 * The metadata toggles decide what a run RECEIVES, so they belong with the other input settings.
 * They were rendered after the output-destination section, which read as though metadata were an
 * output concern. The pipeline editor already groups them with the inputs; this keeps the workflow
 * editor consistent with it.
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import WorkflowSystemConfigFields from "./WorkflowSystemConfigFields";

const noop = () => undefined;

const baseProps = {
    inputFileArity: "one" as const,
    assetScope: {},
    metadataInputs: {},
    allowFilters: [] as string[],
    excludeFilters: [] as string[],
    concurrencyRestriction: "none" as const,
    locationType: "asset" as const,
    allowOverride: false,
    allowWorkflowTriggerChaining: false,
    defaultOutputPathPrefix: "",
    onInputFileArityChange: noop,
    onAssetScopeChange: noop,
    onMetadataInputsChange: noop,
    onAllowFiltersChange: noop,
    onExcludeFiltersChange: noop,
    onConcurrencyRestrictionChange: noop,
    onLocationTypeChange: noop,
    onAllowOverrideChange: noop,
    onAllowWorkflowTriggerChainingChange: noop,
    onDefaultOutputPathPrefixChange: noop,
};

/** Document position of the first node whose text matches, for ordering assertions. */
function positionOf(text: string | RegExp): number {
    const el = screen.getByText(text);
    const all = Array.from(document.querySelectorAll("*"));
    return all.indexOf(el as Element);
}

describe("WorkflowSystemConfigFields ordering", () => {
    it("renders the metadata toggles ABOVE the output destination", () => {
        render(<WorkflowSystemConfigFields {...(baseProps as any)} />);
        expect(positionOf("Metadata provided to pipelines")).toBeLessThan(
            positionOf("Output destination")
        );
    });

    it("still renders every metadata toggle", () => {
        // Moving the block must not drop any of its controls.
        render(<WorkflowSystemConfigFields {...(baseProps as any)} />);
        expect(screen.getByText("Asset metadata")).toBeInTheDocument();
        expect(screen.getByText("File metadata")).toBeInTheDocument();
        expect(screen.getByText("File attributes")).toBeInTheDocument();
        expect(screen.getByText("Database metadata")).toBeInTheDocument();
    });

    it("orders the metadata toggles widest entity first", () => {
        // database -> asset -> file, the containment the rows describe. All four sit inside the
        // metadata block, above the output destination — appending one after it would read as though
        // that metadata were an output concern.
        render(<WorkflowSystemConfigFields {...(baseProps as any)} />);
        expect(positionOf("Database metadata")).toBeLessThan(positionOf("Asset metadata"));
        expect(positionOf("Asset metadata")).toBeLessThan(positionOf("File metadata"));
        expect(positionOf("File metadata")).toBeLessThan(positionOf("File attributes"));
        expect(positionOf("File attributes")).toBeLessThan(positionOf("Output destination"));
    });

    it("keeps the output destination and concurrency sections present and in order", () => {
        render(<WorkflowSystemConfigFields {...(baseProps as any)} />);
        expect(positionOf("Output destination")).toBeLessThan(
            positionOf("Concurrency restriction")
        );
    });

    describe("metadata toggle state", () => {
        const LABELS = ["Asset metadata", "File metadata", "File attributes", "Database metadata"];
        const KEY_OF: Record<string, string> = {
            "Asset metadata": "assetMetadata",
            "File metadata": "fileMetadata",
            "File attributes": "fileAttributes",
            "Database metadata": "databaseMetadata",
        };

        /** The checkbox next to a metadata label. */
        const toggleFor = (label: string) =>
            screen
                .getByText(label)
                .parentElement?.querySelector("input[type=checkbox]") as HTMLInputElement;

        it("shows every toggle as on when the stored map omits all of them", () => {
            // The record builders default every key ON, so an empty map is not an opt-out of
            // everything. Rendering it as such would show the workflow as providing no metadata while
            // its runs collect all four, and saving the form would persist that opt-out.
            render(<WorkflowSystemConfigFields {...(baseProps as any)} />);
            for (const label of LABELS) {
                expect(toggleFor(label)).toBeChecked();
            }
        });

        it.each(LABELS)("shows %s as off only when the stored map turns it off", (label) => {
            render(
                <WorkflowSystemConfigFields
                    {...(baseProps as any)}
                    metadataInputs={{ [KEY_OF[label]]: false }}
                />
            );
            expect(toggleFor(label)).not.toBeChecked();
            for (const other of LABELS.filter((l) => l !== label)) {
                expect(toggleFor(other)).toBeChecked();
            }
        });
    });
});
