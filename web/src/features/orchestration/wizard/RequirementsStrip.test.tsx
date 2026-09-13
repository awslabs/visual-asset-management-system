/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import RequirementsStrip, { MAX_INLINE_PATTERNS } from "./RequirementsStrip";
import type { ResolvedRestrictions } from "./resolveRestrictions";

const restrictions = (over: Partial<ResolvedRestrictions> = {}): ResolvedRestrictions => ({
    allow: ["*.glb", "*.obj"],
    exclude: [],
    source: "workflow",
    metadataInputs: [],
    metadataInputKeys: [],
    metadataGatedOff: [],
    arity: "one",
    outputType: "asset",
    templatesResolved: true,
    wholeAssetAllowed: false,
    folderAllowed: false,
    ...over,
});

describe("RequirementsStrip", () => {
    it("states arity, accepted types, output and metadata as chips", () => {
        render(
            <RequirementsStrip
                restrictions={restrictions({ metadataInputs: ["Asset metadata", "File metadata"] })}
            />
        );
        expect(screen.getByText("1 input file")).toBeInTheDocument();
        expect(screen.getByText("*.glb")).toBeInTheDocument();
        expect(screen.getByText("*.obj")).toBeInTheDocument();
        expect(screen.getByText("Writes to an asset")).toBeInTheDocument();
        expect(screen.getByText("Metadata: Asset metadata, File metadata")).toBeInTheDocument();
    });

    it("says any file type when nothing restricts the selection, and results only for no output", () => {
        render(
            <RequirementsStrip
                restrictions={restrictions({ allow: [], arity: "multi", outputType: "none" })}
            />
        );
        expect(screen.getByText("Any file type")).toBeInTheDocument();
        expect(screen.getByText("1 or more input files")).toBeInTheDocument();
        expect(screen.getByText("Results only")).toBeInTheDocument();
    });

    it("hides the file-type chips for a workflow that takes no files", () => {
        render(
            <RequirementsStrip restrictions={restrictions({ arity: "none", allow: ["*.glb"] })} />
        );
        expect(screen.getByText("No input files")).toBeInTheDocument();
        expect(screen.queryByText("*.glb")).not.toBeInTheDocument();
    });

    it("shows excluded patterns as their own chips", () => {
        render(<RequirementsStrip restrictions={restrictions({ exclude: ["*.tmp"] })} />);
        expect(screen.getByText("Excludes *.tmp")).toBeInTheDocument();
    });

    it("caps the inline patterns and lists the rest in a popover", async () => {
        const allow = Array.from({ length: MAX_INLINE_PATTERNS + 3 }, (_, i) => `*.t${i}`);
        render(<RequirementsStrip restrictions={restrictions({ allow })} />);
        expect(screen.getByText(`*.t${MAX_INLINE_PATTERNS - 1}`)).toBeInTheDocument();
        expect(screen.queryByText(`*.t${MAX_INLINE_PATTERNS}`)).not.toBeInTheDocument();
        const more = screen.getByRole("button", { name: "3 more accepted file types" });
        expect(more).toHaveTextContent("+3");
        await userEvent.click(more);
        expect(await screen.findByText("Accepted file types")).toBeInTheDocument();
        expect(screen.getAllByText(`*.t${MAX_INLINE_PATTERNS + 2}`).length).toBeGreaterThan(0);
    });

    it("carries the template caveat only while a template is still unchosen", () => {
        const { unmount } = render(
            <RequirementsStrip restrictions={restrictions({ templatesResolved: false })} />
        );
        expect(screen.getByText(/may narrow once a template is chosen/)).toBeInTheDocument();
        unmount();
        // Control: the wording is conditional.
        render(<RequirementsStrip restrictions={restrictions({ templatesResolved: true })} />);
        expect(screen.queryByText(/may narrow once a template is chosen/)).not.toBeInTheDocument();
    });
});
