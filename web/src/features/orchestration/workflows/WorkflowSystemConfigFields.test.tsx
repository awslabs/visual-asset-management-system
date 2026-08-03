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

    it("still renders all three metadata toggles", () => {
        // Moving the block must not drop any of its controls.
        render(<WorkflowSystemConfigFields {...(baseProps as any)} />);
        expect(screen.getByText("Asset metadata")).toBeInTheDocument();
        expect(screen.getByText("File metadata")).toBeInTheDocument();
        expect(screen.getByText("File attributes")).toBeInTheDocument();
    });

    it("keeps the output destination and concurrency sections present and in order", () => {
        render(<WorkflowSystemConfigFields {...(baseProps as any)} />);
        expect(positionOf("Output destination")).toBeLessThan(
            positionOf("Concurrency restriction")
        );
    });
});
