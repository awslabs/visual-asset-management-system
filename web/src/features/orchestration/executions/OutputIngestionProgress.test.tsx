/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import OutputIngestionProgress, {
    formatIngestionBytes,
    IngestedBadge,
    INGESTING_OUTPUTS_PHASE,
} from "./OutputIngestionProgress";
import type { OutputIngestion } from "../types";

const GiB = 1024 * 1024 * 1024;

const progress = (over: Partial<OutputIngestion> = {}): OutputIngestion => ({
    filesTotal: 3,
    filesDone: 1,
    bytesTotal: 3 * GiB,
    bytesDone: 1 * GiB,
    files: [
        {
            relativeKey: "/out/a.e57",
            status: "SUCCEEDED",
            bytesTotal: GiB,
            partsTotal: 4,
            partsDone: 4,
            error: "",
        },
        {
            relativeKey: "/out/b.e57",
            status: "RUNNING",
            bytesTotal: 2 * GiB,
            partsTotal: 8,
            partsDone: 2,
            error: "",
        },
        {
            relativeKey: "/out/c.e57",
            status: "QUEUED",
            bytesTotal: 0,
            partsTotal: 0,
            partsDone: 0,
            error: "",
        },
    ],
    filesTruncated: false,
    ...over,
});

describe("OutputIngestionProgress", () => {
    it("shows the file counts, a progress bar and the bytes copied", () => {
        render(
            <OutputIngestionProgress
                executionPhase={INGESTING_OUTPUTS_PHASE}
                outputIngestion={progress()}
            />
        );
        expect(screen.getByText("Ingesting outputs (1 of 3)")).toBeInTheDocument();
        const bar = screen.getByRole("progressbar", { name: "Ingesting outputs (1 of 3)" });
        expect(bar).toHaveAttribute("aria-valuenow", "33");
        expect(screen.getByText(/1 GiB of 3 GiB copied into the asset/)).toBeInTheDocument();
    });

    it("lists each file with a text status and its part progress", () => {
        render(
            <OutputIngestionProgress
                executionPhase={INGESTING_OUTPUTS_PHASE}
                outputIngestion={progress()}
            />
        );
        const table = screen.getByRole("table", { name: "Output files being copied" });
        const rows = within(table).getAllByRole("row").slice(1);
        expect(rows).toHaveLength(3);
        expect(rows[0]).toHaveTextContent("/out/a.e57");
        expect(rows[0]).toHaveTextContent("Landed");
        expect(rows[0]).toHaveTextContent("100%");
        expect(rows[1]).toHaveTextContent("Copying");
        expect(rows[1]).toHaveTextContent("25%");
        expect(
            within(rows[1]).getByRole("progressbar", { name: "/out/b.e57 25% copied" })
        ).toBeInTheDocument();
        // A file whose copy is not planned yet has no part count.
        expect(rows[2]).toHaveTextContent("Queued");
        expect(rows[2]).toHaveTextContent("Not started");
    });

    it("maps a failed and a cancelled copy to text and shows the reason", () => {
        render(
            <OutputIngestionProgress
                executionPhase={INGESTING_OUTPUTS_PHASE}
                outputIngestion={progress({
                    files: [
                        {
                            relativeKey: "/out/x.las",
                            status: "FAILED",
                            bytesTotal: GiB,
                            partsTotal: 4,
                            partsDone: 1,
                            error: "The copy could not be completed",
                        },
                        {
                            relativeKey: "/out/y.las",
                            status: "CANCELLED",
                            bytesTotal: GiB,
                            partsTotal: 4,
                            partsDone: 0,
                            error: "Cancelled",
                        },
                    ],
                })}
            />
        );
        expect(screen.getByText("Failed")).toBeInTheDocument();
        expect(screen.getByText("The copy could not be completed")).toBeInTheDocument();
        expect(screen.getByText("Cancelled", { selector: "span" })).toBeInTheDocument();
    });

    it("says when the file list is a subset", () => {
        render(
            <OutputIngestionProgress
                executionPhase={INGESTING_OUTPUTS_PHASE}
                outputIngestion={progress({ filesTotal: 250, filesTruncated: true })}
            />
        );
        expect(
            screen.getByText("Showing the first 3 files; the counts above cover every file.")
        ).toBeInTheDocument();
    });

    it("tolerates a response without a files array", () => {
        const { files, filesTruncated, ...counters } = progress();
        void files;
        void filesTruncated;
        render(
            <OutputIngestionProgress
                executionPhase={INGESTING_OUTPUTS_PHASE}
                outputIngestion={counters as OutputIngestion}
            />
        );
        expect(screen.getByText("Ingesting outputs (1 of 3)")).toBeInTheDocument();
        expect(screen.queryByRole("table")).not.toBeInTheDocument();
    });

    it("renders nothing without the phase or without the progress", () => {
        const { container: noPhase } = render(
            <OutputIngestionProgress outputIngestion={progress()} />
        );
        expect(noPhase).toBeEmptyDOMElement();
        const { container: noProgress } = render(
            <OutputIngestionProgress executionPhase={INGESTING_OUTPUTS_PHASE} />
        );
        expect(noProgress).toBeEmptyDOMElement();
    });

    it("shows only the summary when compact", () => {
        render(
            <OutputIngestionProgress
                executionPhase={INGESTING_OUTPUTS_PHASE}
                outputIngestion={progress({ filesTruncated: true })}
                compact
            />
        );
        expect(screen.getByText("Ingesting outputs (1 of 3)")).toBeInTheDocument();
        expect(screen.queryByRole("table")).not.toBeInTheDocument();
        expect(screen.queryByText(/Showing the first/)).not.toBeInTheDocument();
    });
});

describe("IngestedBadge", () => {
    it("marks only a row the copy engine landed", () => {
        const { rerender } = render(<IngestedBadge ingestionStatus="SUCCEEDED" />);
        expect(screen.getByText("Ingested by copy engine")).toBeInTheDocument();
        rerender(<IngestedBadge ingestionStatus="" />);
        expect(screen.queryByText("Ingested by copy engine")).not.toBeInTheDocument();
        rerender(<IngestedBadge />);
        expect(screen.queryByText("Ingested by copy engine")).not.toBeInTheDocument();
    });
});

describe("formatIngestionBytes", () => {
    it("formats binary sizes", () => {
        expect(formatIngestionBytes(0)).toBe("0 B");
        expect(formatIngestionBytes(512)).toBe("512 B");
        expect(formatIngestionBytes(1536 * 1024)).toBe("1.5 MiB");
        expect(formatIngestionBytes(5 * GiB)).toBe("5 GiB");
        expect(formatIngestionBytes(1024 * GiB)).toBe("1 TiB");
        // TiB is the largest unit, so a larger size stays in TiB.
        expect(formatIngestionBytes(1024 * 1024 * GiB)).toBe("1024 TiB");
    });
});
