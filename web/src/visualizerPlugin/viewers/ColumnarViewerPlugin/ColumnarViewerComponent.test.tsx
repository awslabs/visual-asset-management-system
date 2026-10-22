/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * The render guard `!loaded || columns.length === 0` shows a spinner, so anything
 * that parses to zero columns — a binary format with no parser, an empty file —
 * used to leave the spinner as the terminal state, indistinguishable from a slow
 * network. Format dispatch also has to be anchored: a substring test sends
 * "report.fcs.csv" to the FCS parser and "DATA.FCS" to the CSV one.
 */

import React from "react";
import { render, screen, waitFor, act } from "@testing-library/react";
import ColumnarViewerComponent from "./ColumnarViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

const mockReadRemoteFile = jest.fn();
jest.mock("react-papaparse", () => ({
    readRemoteFile: (...args: any[]) => mockReadRemoteFile(...args),
}));

jest.mock("react-data-grid", () => ({
    __esModule: true,
    default: ({ columns, rows }: any) => (
        <div>
            <span>{`grid:${columns.length}`}</span>
            <span>{`rows:${rows.length}`}</span>
        </div>
    ),
}));

jest.mock("fcs", () => ({
    __esModule: true,
    default: jest.fn().mockImplementation(() => ({})),
}));

jest.mock("arraybuffer-to-buffer", () => ({
    __esModule: true,
    default: (buffer: any) => buffer,
}));

// viewableExtensions reaches the registry, which uses Vite's import.meta.glob.
jest.mock("../../core/PluginRegistry", () => ({
    PluginRegistry: {
        getInstance: () => ({ getCompatibleViewers: () => [], isInitialized: () => true }),
    },
}));

const props = (assetKey: string) => ({ assetId: "a1", databaseId: "d1", assetKey } as any);

describe("ColumnarViewerComponent", () => {
    let originalXhr: any;
    const xhrs: any[] = [];

    beforeEach(() => {
        jest.clearAllMocks();
        xhrs.length = 0;
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/data?sig=1"]);
        originalXhr = (window as any).XMLHttpRequest;
        // The FCS path drives XMLHttpRequest directly; a stub keeps the suite off
        // the network while still recording that this branch was the one taken.
        (window as any).XMLHttpRequest = class {
            onload: any = null;
            onerror: any = null;
            responseType = "";
            response: any = null;
            constructor() {
                xhrs.push(this);
            }
            open() {}
            send() {}
        };
    });

    afterEach(() => {
        (window as any).XMLHttpRequest = originalXhr;
    });

    it("renders the grid for a parsable CSV", async () => {
        mockReadRemoteFile.mockImplementation((_url: string, options: any) =>
            options.complete({
                data: [
                    ["a", "b"],
                    ["1", "2"],
                ],
            })
        );

        render(<ColumnarViewerComponent {...props("tables/data.csv")} />);

        await waitFor(() => expect(screen.getByText("grid:2")).toBeInTheDocument());
        expect(screen.queryByText(/Loading data/)).toBeNull();
    });

    it("reports a file that parses to no columns instead of spinning forever", async () => {
        // Positive control is the test above: the same harness renders a grid when
        // there are columns, so this failure comes from the empty parse.
        mockReadRemoteFile.mockImplementation((_url: string, options: any) =>
            options.complete({ data: [] })
        );

        render(<ColumnarViewerComponent {...props("tables/empty.csv")} />);

        await waitFor(() =>
            expect(screen.getByText(/contains no readable columns/i)).toBeInTheDocument()
        );
        expect(screen.queryByText(/Loading data/)).toBeNull();
    });

    it("reports a declared-but-unparsable format instead of feeding it to the CSV parser", async () => {
        render(<ColumnarViewerComponent {...props("tables/matrix.rds")} />);

        await waitFor(() =>
            expect(screen.getByText(/\.rds is not supported/i)).toBeInTheDocument()
        );
        expect(mockReadRemoteFile).not.toHaveBeenCalled();
        // The message sits on a theme panel over the striped viewer background.
        const message = screen.getByText(/\.rds is not supported/i);
        expect(message.style.background).toBe("var(--vams-bg-primary)");
        expect(message.style.color).toBe("var(--vams-color-error)");
    });

    it("dispatches on the real extension, not a substring of the name", async () => {
        mockReadRemoteFile.mockImplementation((_url: string, options: any) =>
            options.complete({ data: [["a"], ["1"]] })
        );

        render(<ColumnarViewerComponent {...props("reports/report.fcs.csv")} />);

        // A .csv file whose name contains ".fcs" belongs to the CSV parser.
        await waitFor(() => expect(mockReadRemoteFile).toHaveBeenCalledTimes(1));
        expect(xhrs).toHaveLength(0);
    });

    it("treats an upper-case .FCS as FCS, not as delimited text", async () => {
        render(<ColumnarViewerComponent {...props("reports/DATA.FCS")} />);

        // The FCS reader opens its own XMLHttpRequest, so one recorded request is
        // positive evidence of the branch taken.
        await waitFor(() => expect(xhrs).toHaveLength(1));
        expect(mockReadRemoteFile).not.toHaveBeenCalled();
    });

    describe("FCS events", () => {
        const FCS = jest.requireMock("fcs").default as jest.Mock;

        /** An FCS parse of `events` events with `params` values each. */
        const fcsData = (events: number, params: number, total?: number) => {
            const text: Record<string, string> = {};
            for (let p = 1; p <= params; p++) text[`$P${p}N`] = `P${p}`;
            if (total !== undefined) text.$TOT = String(total);
            return {
                dataAsStrings: Array.from(
                    { length: events },
                    (_, e) => `[${Array.from({ length: params }, (_, p) => e * 10 + p).join(",")}]`
                ),
                text,
                meta: {},
            };
        };

        const loadFcs = async (parse: () => any) => {
            FCS.mockImplementation(parse);
            render(<ColumnarViewerComponent {...props("cells/sample.fcs")} />);
            await waitFor(() => expect(xhrs).toHaveLength(1));
            act(() => {
                xhrs[0].response = new ArrayBuffer(8);
                xhrs[0].onload();
            });
        };

        it("builds one row per event and one column per parameter", async () => {
            await loadFcs(() => fcsData(20, 3, 20));
            await waitFor(() => expect(screen.getByText("rows:20")).toBeInTheDocument());
            expect(screen.getByText("grid:3")).toBeInTheDocument();
            expect(screen.queryByTestId("fcs-event-count")).toBeNull();
        });

        it("reads a file with fewer events than parameters", async () => {
            await loadFcs(() => fcsData(2, 5));
            await waitFor(() => expect(screen.getByText("rows:2")).toBeInTheDocument());
            expect(screen.getByText("grid:5")).toBeInTheDocument();
        });

        it("says how many events were read when the file holds more", async () => {
            await loadFcs(() => fcsData(20, 3, 5000));
            await waitFor(() =>
                expect(screen.getByTestId("fcs-event-count")).toHaveTextContent(
                    "Showing 20 of 5,000 events"
                )
            );
        });

        it("reports a parse that throws instead of spinning", async () => {
            jest.spyOn(console, "error").mockImplementation(() => undefined);
            await loadFcs(() => {
                throw new Error("bad header");
            });
            await waitFor(() =>
                expect(screen.getByText(/Unable to read this file as FCS data/)).toBeInTheDocument()
            );
        });
    });
});
