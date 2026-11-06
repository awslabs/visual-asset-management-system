/*
 * Copyright 2026 Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0
 */

/**
 * A CSV saved by Excel or Windows tools starts with a UTF-8 byte order mark (U+FEFF). Read through
 * the real react-papaparse readRemoteFile (not a mock), the first header must not carry it: a
 * header that renders as "id" but is keyed "\uFEFFid" breaks the column for every row.
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
                <span key={column.key} data-testid="column">
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
