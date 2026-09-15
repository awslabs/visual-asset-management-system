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
import { render, screen, waitFor } from "@testing-library/react";
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
    default: ({ columns }: any) => <div>{`grid:${columns.length}`}</div>,
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
});
