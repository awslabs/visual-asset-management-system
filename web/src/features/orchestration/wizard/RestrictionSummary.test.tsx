/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import RestrictionSummary from "./RestrictionSummary";
import { resolveRestrictions } from "./resolveRestrictions";

const wf = (over: any = {}) => ({
    inputFileArity: "one",
    metadataInputs: {},
    inputFileFilters: {},
    outputTarget: { locationType: "asset" },
    ...over,
});

describe("RestrictionSummary", () => {
    it("shows the accepted patterns, so the user learns them before picking files", () => {
        render(
            <RestrictionSummary
                restrictions={resolveRestrictions(
                    wf({ inputFileFilters: { allow: ["*.glb", "*.e57"] } }),
                    []
                )}
            />
        );
        expect(screen.getByText("*.glb")).toBeInTheDocument();
        expect(screen.getByText("*.e57")).toBeInTheDocument();
        expect(screen.getByText(/Exactly one input file/)).toBeInTheDocument();
    });

    it("says any file type when nothing restricts the selection", () => {
        render(<RestrictionSummary restrictions={resolveRestrictions(wf(), [])} />);
        expect(screen.getByText("Any file type")).toBeInTheDocument();
    });

    it("names where a restriction came from", () => {
        const { rerender } = render(
            <RestrictionSummary
                restrictions={resolveRestrictions(
                    wf({ inputFileFilters: { allow: ["*.glb"] } }),
                    []
                )}
            />
        );
        expect(screen.getByText(/from the workflow/)).toBeInTheDocument();

        rerender(
            <RestrictionSummary
                restrictions={resolveRestrictions(wf(), [
                    { systemConfig: { inputFileFilters: { allow: ["*.obj"] } } },
                ])}
            />
        );
        expect(screen.getByText(/from the pipelines/)).toBeInTheDocument();
    });

    it("shows excluded patterns separately from accepted ones", () => {
        render(
            <RestrictionSummary
                restrictions={resolveRestrictions(
                    wf({ inputFileFilters: { allow: ["*.glb"], exclude: ["*.previewFile.*"] } }),
                    []
                )}
            />
        );
        expect(screen.getByText("Excluded:")).toBeInTheDocument();
        expect(screen.getByText("*.previewFile.*")).toBeInTheDocument();
    });

    it("hides the file-type rows for a run that takes no input files", () => {
        // Showing accepted types for a results-only run would imply a file selection is coming.
        render(
            <RestrictionSummary
                restrictions={resolveRestrictions(wf({ inputFileArity: "none" }), [])}
            />
        );
        expect(screen.getByText(/No input files/)).toBeInTheDocument();
        expect(screen.queryByText("Accepted file types:")).not.toBeInTheDocument();
    });

    it("warns when a step needs metadata the workflow does not provide", () => {
        render(
            <RestrictionSummary
                restrictions={resolveRestrictions(
                    wf({ metadataInputs: { assetMetadata: false } }),
                    [{ systemConfig: { metadataInputs: { assetMetadata: true } } }]
                )}
            />
        );
        expect(screen.getByText(/runs without it/)).toBeInTheDocument();
    });

    it("says a results-only run writes nothing to an asset", () => {
        render(
            <RestrictionSummary
                restrictions={resolveRestrictions(
                    wf({ outputTarget: { locationType: "none" } }),
                    []
                )}
            />
        );
        expect(screen.getByText(/Results only/)).toBeInTheDocument();
    });

    it("flags that a template may narrow the values further", () => {
        render(
            <RestrictionSummary
                restrictions={resolveRestrictions(wf(), [
                    { systemConfig: {}, templateKnown: false },
                ])}
            />
        );
        expect(screen.getByText(/can narrow these further/)).toBeInTheDocument();
    });

    it("renders a single line in compact mode, for the workflow picker", () => {
        render(
            <RestrictionSummary
                compact
                restrictions={resolveRestrictions(
                    wf({ inputFileFilters: { allow: ["*.glb"] } }),
                    []
                )}
            />
        );
        expect(screen.getByText(/1 file type · 1 file · writes to an asset/)).toBeInTheDocument();
        // Not dumped INLINE — the picker stays scannable — but reachable, because a count alone does
        // not tell the user which files to go and find.
        expect(screen.queryByText("*.glb")).not.toBeInTheDocument();
        expect(
            screen.getByRole("button", { name: /Which files this workflow accepts/i })
        ).toBeInTheDocument();
    });

    it("notes in compact mode when a template could still narrow things", () => {
        render(
            <RestrictionSummary
                compact
                restrictions={resolveRestrictions(wf(), [
                    { systemConfig: {}, templateKnown: false },
                ])}
            />
        );
        expect(screen.getByText(/may narrow once a template is chosen/)).toBeInTheDocument();
    });
});
