/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * CSV files as real tools write them, read through the real react-papaparse readRemoteFile (not a
 * mock): a UTF-8 byte order mark before the first header (#1182), rows wider than the header
 * (#1183), a trailing newline (#1184) and a repeated header name (#1185).
 */

import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import ColumnarViewerComponent from "./ColumnarViewerComponent";

const mockDownloadAsset = jest.fn();
jest.mock("../../../services/APIService", () => ({
    downloadAsset: (...args: any[]) => mockDownloadAsset(...args),
}));

jest.mock("react-data-grid", () => ({
    __esModule: true,
    default: ({ columns, rows }: any) => (
        <div data-testid="data-grid">
            {columns.map((column: any) => (
                <span key={column.key} data-testid="column" data-name={column.name}>
                    {JSON.stringify(column.key)}
                </span>
            ))}
            <span>{`rows:${rows.length}`}</span>
            <span data-testid="first-row">{JSON.stringify(rows[0])}</span>
        </div>
    ),
}));

jest.mock("fcs", () => ({ __esModule: true, default: jest.fn() }));
jest.mock("arraybuffer-to-buffer", () => ({ __esModule: true, default: (b: any) => b }));

// viewableExtensions reaches the registry, which uses Vite's import.meta.glob.
jest.mock("../../core/PluginRegistry", () => ({
    PluginRegistry: {
        getInstance: () => ({ getCompatibleViewers: () => [], isInitialized: () => true }),
    },
}));

/** Serves `body` to papaparse's download streamer, which reads the file with XMLHttpRequest. */
function serveText(body: string) {
    (window as any).XMLHttpRequest = class {
        readyState = 0;
        status = 0;
        responseText = "";
        onload: any = null;
        onerror: any = null;
        withCredentials = false;
        open() {}
        setRequestHeader() {}
        getResponseHeader() {
            return null;
        }
        send() {
            setTimeout(() => {
                this.readyState = 4;
                this.status = 200;
                this.responseText = body;
                this.onload?.();
            }, 0);
        }
    };
}

describe("ColumnarViewerComponent reading a CSV through react-papaparse", () => {
    const originalXhr = (window as any).XMLHttpRequest;

    beforeEach(() => {
        mockDownloadAsset.mockResolvedValue([true, "https://example.test/data.csv?sig=1"]);
    });

    afterEach(() => {
        (window as any).XMLHttpRequest = originalXhr;
    });

    it("drops the UTF-8 byte order mark from the first header", async () => {
        serveText("\uFEFFid,name\n1,alpha\n2,beta");
        render(
            <ColumnarViewerComponent
                {...({ assetId: "a1", databaseId: "d1" } as any)}
                assetKey="/data.csv"
            />
        );

        await waitFor(() => expect(screen.getByTestId("data-grid")).toBeInTheDocument());
        const keys = screen.getAllByTestId("column").map((node) => JSON.parse(node.textContent!));
        expect(keys).toEqual(["id", "name"]);
        expect(screen.getByText("rows:2")).toBeInTheDocument();
        expect(JSON.parse(screen.getByTestId("first-row").textContent!)).toEqual({
            id: "1",
            name: "alpha",
        });
    });

    it("shows a row with more cells than the header under generated column names", async () => {
        // Real exports carry these (NOAA's co2-mm-mlo.csv has 6 headers over 7-cell rows). The
        // extra cell used to index past the header list and throw inside papaparse's callback,
        // leaving the loading spinner up for good.
        serveText("a,b\n1,2,3\n4,5");
        render(
            <ColumnarViewerComponent
                {...({ assetId: "a1", databaseId: "d1" } as any)}
                assetKey="/data.csv"
            />
        );

        await waitFor(() => expect(screen.getByTestId("data-grid")).toBeInTheDocument());
        const keys = screen.getAllByTestId("column").map((node) => JSON.parse(node.textContent!));
        expect(keys).toEqual(["a", "b", "Column 3"]);
        expect(screen.getByText("rows:2")).toBeInTheDocument();
        expect(JSON.parse(screen.getByTestId("first-row").textContent!)).toEqual({
            a: "1",
            b: "2",
            "Column 3": "3",
        });
    });

    it("does not add an empty row for a trailing newline", async () => {
        serveText("id,name\n1,alpha\n2,beta\n");
        render(
            <ColumnarViewerComponent
                {...({ assetId: "a1", databaseId: "d1" } as any)}
                assetKey="/data.csv"
            />
        );

        await waitFor(() => expect(screen.getByTestId("data-grid")).toBeInTheDocument());
        expect(screen.getByText("rows:2")).toBeInTheDocument();
    });

    it("shows every column of a repeated header name, under distinct keys and labels", async () => {
        serveText("id,value,value,value\n1,a,b,c\n");
        render(
            <ColumnarViewerComponent
                {...({ assetId: "a1", databaseId: "d1" } as any)}
                assetKey="/data.csv"
            />
        );

        await waitFor(() => expect(screen.getByTestId("data-grid")).toBeInTheDocument());
        const columns = screen.getAllByTestId("column");
        const keys = columns.map((node) => JSON.parse(node.textContent!));
        expect(keys).toEqual(["id", "value", "value (2)", "value (3)"]);
        expect(columns.map((node) => node.getAttribute("data-name"))).toEqual(keys);
        expect(JSON.parse(screen.getByTestId("first-row").textContent!)).toEqual({
            id: "1",
            value: "a",
            "value (2)": "b",
            "value (3)": "c",
        });
    });

    it("keeps a generated column name from colliding with a header", async () => {
        serveText("a,Column 3\n1,2,3\n");
        render(
            <ColumnarViewerComponent
                {...({ assetId: "a1", databaseId: "d1" } as any)}
                assetKey="/data.csv"
            />
        );

        await waitFor(() => expect(screen.getByTestId("data-grid")).toBeInTheDocument());
        const keys = screen.getAllByTestId("column").map((node) => JSON.parse(node.textContent!));
        expect(keys).toEqual(["a", "Column 3", "Column 3 (2)"]);
        expect(JSON.parse(screen.getByTestId("first-row").textContent!)).toEqual({
            a: "1",
            "Column 3": "2",
            "Column 3 (2)": "3",
        });
    });

    it("[control] reads the same file without a byte order mark identically", async () => {
        serveText("id,name\n1,alpha\n2,beta");
        render(
            <ColumnarViewerComponent
                {...({ assetId: "a1", databaseId: "d1" } as any)}
                assetKey="/data.csv"
            />
        );

        await waitFor(() => expect(screen.getByTestId("data-grid")).toBeInTheDocument());
        const keys = screen.getAllByTestId("column").map((node) => JSON.parse(node.textContent!));
        expect(keys).toEqual(["id", "name"]);
    });
});
