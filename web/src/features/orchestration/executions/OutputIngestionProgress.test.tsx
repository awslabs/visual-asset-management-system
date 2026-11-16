/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

import React from "react";
import { render, screen, within } from "@testing-library/react";
import OutputIngestionProgress, {
    copiedIngestionBytes,
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
        // The bar follows the bytes copied: 1 GiB landed plus a quarter of the 2 GiB file still
        // copying, of 3 GiB.
        expect(bar).toHaveAttribute("aria-valuenow", "50");
        expect(screen.getByText(/1\.5 GiB of 3 GiB copied into the asset/)).toBeInTheDocument();
    });

    it("moves the summary bar while one large file is copying (#1123 D4)", () => {
        // The live smoke: the bar stayed empty (0 of 1 files done) next to "432 GiB of 1 TiB
        // copied" and the file row at 42 %.
        render(
            <OutputIngestionProgress
                executionPhase={INGESTING_OUTPUTS_PHASE}
                outputIngestion={progress({
                    filesTotal: 1,
                    filesDone: 0,
                    bytesTotal: 1024 * GiB,
                    bytesDone: 0,
                    files: [
                        {
                            relativeKey: "/big0.bin",
                            status: "RUNNING",
                            bytesTotal: 1024 * GiB,
                            partsTotal: 4096,
                            partsDone: 1728,
                            error: "",
                        },
                    ],
                })}
            />
        );
        const bar = screen.getByRole("progressbar", { name: "Ingesting outputs (0 of 1)" });
        expect(bar).toHaveAttribute("aria-valuenow", "42");
        expect(screen.getByText(/432 GiB of 1 TiB copied into the asset/)).toBeInTheDocument();
        expect(
            screen.getByRole("progressbar", { name: "/big0.bin 42% copied" })
        ).toBeInTheDocument();
    });

    it("falls back to the files done when no byte total is reported", () => {
        render(
            <OutputIngestionProgress
                executionPhase={INGESTING_OUTPUTS_PHASE}
                outputIngestion={progress({
                    filesTotal: 4,
                    filesDone: 1,
                    bytesTotal: 0,
                    bytesDone: 0,
                })}
            />
        );
        const bar = screen.getByRole("progressbar", { name: "Ingesting outputs (1 of 4)" });
        expect(bar).toHaveAttribute("aria-valuenow", "25");
    });

    it("counts a single large file that is still copying (#1123 D3)", () => {
        // The live smoke: one 40 GiB file at 80 % read "0 B of 40 GiB copied".
        render(
            <OutputIngestionProgress
                executionPhase={INGESTING_OUTPUTS_PHASE}
                outputIngestion={progress({
                    filesTotal: 1,
                    filesDone: 0,
                    bytesTotal: 40 * GiB,
                    bytesDone: 0,
                    files: [
                        {
                            relativeKey: "/out/big0.bin",
                            status: "RUNNING",
                            bytesTotal: 40 * GiB,
                            partsTotal: 160,
                            partsDone: 128,
                            error: "",
                        },
                    ],
                })}
            />
        );
        expect(screen.getByText(/32 GiB of 40 GiB copied into the asset/)).toBeInTheDocument();
        expect(screen.queryByText(/^0 B of/)).not.toBeInTheDocument();
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

describe("copiedIngestionBytes", () => {
    it("adds only the copying files' share to the landed bytes, capped at the total", () => {
        expect(copiedIngestionBytes(progress())).toBe(1.5 * GiB);
        // A failed or cancelled file adds nothing; a landed one is already in bytesDone.
        expect(
            copiedIngestionBytes(
                progress({
                    bytesDone: 0,
                    files: [
                        {
                            relativeKey: "/f",
                            status: "FAILED",
                            bytesTotal: GiB,
                            partsTotal: 4,
                            partsDone: 2,
                            error: "x",
                        },
                    ],
                })
            )
        ).toBe(0);
        expect(
            copiedIngestionBytes(
                progress({
                    bytesTotal: 2 * GiB,
                    bytesDone: 2 * GiB,
                    files: [
                        {
                            relativeKey: "/r",
                            status: "RUNNING",
                            bytesTotal: GiB,
                            partsTotal: 4,
                            partsDone: 9,
                            error: "",
                        },
                    ],
                })
            )
        ).toBe(2 * GiB);
        const { files, ...noFiles } = progress();
        void files;
        expect(copiedIngestionBytes(noFiles as OutputIngestion)).toBe(GiB);
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
